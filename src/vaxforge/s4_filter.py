"""S4 - Safety and conservancy filters.

Input : ``runs/<run_id>/s3_rank.json``
Output: ``runs/<run_id>/s4_filter.json``

Four gates (README S4): human-proteome homology (+ IEDB human MHC-ligand re-check), allergenicity
(AlgPred 2.0), toxicity (ToxinPred2) and conservancy (BLASTp against other strains).

Rules enforced here:
  * Nothing is dropped silently. Every rejection keeps its full evidence and is listed under
    ``rejected`` with ``recoverable: true`` (README counter-caution: over-aggressive subtractive
    filtering can discard subdominant but protective epitopes). ``s4.rescue`` forces a kept
    decision and records that it was a rescue.
  * A gate that cannot run is ``unavailable`` - never ``pass``. The artifact sets
    ``incomplete_screening: true`` so downstream stages / the report can show it.
"""
from __future__ import annotations

import csv
import hashlib
import io
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence

from .prediction import artifacts as art
from .prediction.blast import BlastRunner, best_hits, shared_exact_kmers
from .prediction.cli import stage_main
from .prediction.config import coerce_config
from .prediction.errors import ContractError, ToolFailed, ToolUnavailable
from .prediction.fasta import write_fasta
from .prediction.refdata import RefData, load_peptide_set
from .prediction.tools import resolve_executable, run_cmd, tool_record

STAGE = "s4_filter"
VERSION = 1
GATES = ("human_homology", "allergenicity", "toxicity", "conservancy")


@dataclass
class GateResult:
    status: str                 # pass | flag | reject | unavailable
    reason: str
    metrics: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {"status": self.status, "reason": self.reason, "metrics": self.metrics}


class Gate:
    name = ""

    def thresholds(self) -> dict:
        return {}

    def tool_info(self) -> dict:
        raise NotImplementedError

    def evaluate(self, cands: list[dict]) -> dict[str, GateResult]:
        raise NotImplementedError


def db_prefix(db_dir: Path, fasta: Path, label: str) -> Path:
    """BLAST DB prefix keyed on the FASTA identity so a changed/other FASTA never reuses a stale database."""
    fasta = Path(fasta).resolve()
    if not fasta.exists():
        raise ToolUnavailable(f"reference FASTA not found: {fasta}")
    st = fasta.stat()
    tag = hashlib.sha1(f"{fasta}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:8]
    return Path(db_dir) / f"{label}_{tag}"


def _seqs(cands: Sequence[Mapping]) -> list[tuple[str, str]]:
    return [(c["protein_id"], c["sequence"]) for c in cands]


# ------------------------------------------------------------------------ human homology
class HumanHomologyGate(Gate):
    name = "human_homology"

    def __init__(self, blast: BlastRunner, human_fasta: Path, db_dir: Path, cfg: Mapping,
                 ligands: set[str] | None):
        self.blast, self.human_fasta, self.db_dir, self.cfg = blast, Path(human_fasta), Path(db_dir), cfg
        k = int(cfg["shared_peptide_length"])
        self.k = k
        self.ligand_kmers: set[str] | None = None
        if ligands:
            self.ligand_kmers = {p[i:i + k] for p in ligands if len(p) >= k for i in range(len(p) - k + 1)}

    def thresholds(self) -> dict:
        c = self.cfg
        return {"min_identity": c["min_identity"], "min_coverage": c["min_coverage"], "evalue": c["evalue"],
                "shared_peptide_length": self.k, "ligand_recheck": self.ligand_kmers is not None}

    def tool_info(self) -> dict:
        return tool_record("BLAST+ blastp vs human proteome", version=self.blast.version(), backend=self.blast.mode,
                           fidelity="reference", human_proteome=str(self.human_fasta),
                           ligand_recheck="IEDB human MHC ligands" if self.ligand_kmers is not None else "unavailable")

    def evaluate(self, cands: list[dict]) -> dict[str, GateResult]:
        db = self.blast.makeblastdb(self.human_fasta, db_prefix(self.db_dir, self.human_fasta, "human"))
        with tempfile.TemporaryDirectory(prefix="vf_s4_hh_") as td:
            q = write_fasta(Path(td) / "q.fa", _seqs(cands))
            hsps = self.blast.blastp(q, db, evalue=self.cfg["evalue"], max_target_seqs=5)
        by_q: dict[str, list] = {}
        for h in hsps:
            by_q.setdefault(h.qseqid, []).append(h)
        out: dict[str, GateResult] = {}
        for c in cands:
            pid = c["protein_id"]
            hs = by_q.get(pid, [])
            if not hs:
                out[pid] = GateResult("pass", "no human homolog above the E-value cutoff", {"best_hit": None})
                continue
            best = best_hits(hs)[pid]
            homologous = best.identity >= self.cfg["min_identity"] and best.coverage >= self.cfg["min_coverage"]
            shared = shared_exact_kmers(hs, self.k)
            overlap = sorted(k for k in shared if self.ligand_kmers is not None and k in self.ligand_kmers)
            metrics = {"best_hit": best.brief(), "homologous": homologous, "n_shared_peptides": len(shared),
                       "shared_peptide_examples": sorted(shared)[:5],
                       "known_human_mhc_ligand_overlap": overlap[:10], "n_ligand_overlap": len(overlap),
                       "ligand_recheck": "done" if self.ligand_kmers is not None else "unavailable"}
            if self.ligand_kmers is not None:
                if overlap:
                    out[pid] = GateResult("reject", f"{len(overlap)} shared {self.k}-mer(s) are known human MHC ligands "
                                          "(self-peptide / autoimmunity risk)", metrics)
                elif homologous or shared:
                    out[pid] = GateResult("flag", "human homolog or shared peptides, but none is a known human MHC ligand", metrics)
                else:
                    out[pid] = GateResult("pass", "weak human similarity only", metrics)
            else:
                if homologous:
                    out[pid] = GateResult("reject", f"human homolog (identity {best.identity:.2f}, coverage {best.coverage:.2f}); "
                                          "IEDB ligand re-check unavailable so this may be over-aggressive - recoverable", metrics)
                else:
                    out[pid] = GateResult("flag" if shared else "pass",
                                          "shared peptides with human proteome; ligand re-check unavailable" if shared
                                          else "weak human similarity only", metrics)
        return out


# ----------------------------------------------------------------- CLI predictors (ML)
def _pick_score_column(headers: Sequence[str]) -> int:
    """Index of the score column. Verified real headers (2026-11-14):
    ToxinPred2 / AlgPred2 ``-m 1``: ``ID,Sequence,ML_Score,Prediction``;
    AlgPred2 ``-m 2``: ``Subject,ML Score,MERCI Score,BLAST Score,Hybrid Score,Prediction`` (hybrid can exceed 1)."""
    low = [h.strip().lstrip("#").strip().lower().replace("_", " ") for h in headers]
    for want in ("hybrid score", "hybrid", "ml score", "score"):
        for i, h in enumerate(low):
            if h == want:
                return i
    for i, h in enumerate(low):
        if "score" in h and "blast" not in h and "merci" not in h:
            return i
    raise ToolFailed(f"cannot find a score column in {list(headers)}")


def parse_predictor_csv(text: str) -> dict[str, float]:
    """AlgPred2 / ToxinPred2 CSV -> ``{sequence_id: score}`` (header-driven; first column is the id).

    The header row is the first row that has a cell mentioning 'score'; banner lines before it
    ("Please cite ...", blank lines) are skipped.
    """
    rows = [r for r in csv.reader(io.StringIO(text.strip())) if r]
    hdr_i = next((i for i, r in enumerate(rows) if any("score" in c.lower() for c in r)), None)
    if hdr_i is None:
        if len(rows) < 2:
            return {}
        raise ToolFailed(f"cannot find a score column in {rows[0]}")
    col = _pick_score_column(rows[hdr_i])
    out: dict[str, float] = {}
    for r in rows[hdr_i + 1:]:
        if len(r) > col and r[0].strip():
            try:
                out[r[0].strip().lstrip(">")] = float(r[col])
            except ValueError:
                continue
    return out


class CliScoreGate(Gate):
    """Allergenicity (AlgPred 2.0) / toxicity (ToxinPred2): reject when score >= threshold."""

    def __init__(self, name: str, tool_name: str, argv: Sequence[str], threshold: float, model: int, timeout: float = 3600):
        self.name, self.tool_name, self.argv, self.threshold, self.model, self.timeout = name, tool_name, list(argv), threshold, model, timeout

    def thresholds(self) -> dict:
        return {"score_threshold": self.threshold, "model": self.model}

    def tool_info(self) -> dict:
        return tool_record(self.tool_name, version=None, backend="cli", fidelity="reference", model=self.model,
                           command=" ".join(self.argv))

    def evaluate(self, cands: list[dict]) -> dict[str, GateResult]:
        with tempfile.TemporaryDirectory(prefix=f"vf_s4_{self.name}_") as td:
            fa = write_fasta(Path(td) / "in.fa", _seqs(cands))
            outp = Path(td) / "out.csv"
            run_cmd(self.argv + ["-i", str(fa), "-o", str(outp), "-m", str(self.model), "-t", str(self.threshold), "-d", "2"],
                    timeout=self.timeout, what=self.tool_name)
            if not outp.exists():
                raise ToolFailed(f"{self.tool_name} wrote no output file")
            scores = parse_predictor_csv(outp.read_text())
        res: dict[str, GateResult] = {}
        for c in cands:
            s = scores.get(c["protein_id"])
            if s is None:
                res[c["protein_id"]] = GateResult("unavailable", f"{self.tool_name} returned no score for this sequence")
            elif s >= self.threshold:
                res[c["protein_id"]] = GateResult("reject", f"{self.tool_name} score {s:.3f} >= {self.threshold}", {"score": s})
            else:
                res[c["protein_id"]] = GateResult("pass", f"{self.tool_name} score {s:.3f} < {self.threshold}", {"score": s})
        return res


# ------------------------------------------------------------------------- conservancy
class ConservancyGate(Gate):
    name = "conservancy"

    def __init__(self, blast: BlastRunner, strains: list[tuple[str, Path]], db_dir: Path, *, identity: float, cfg: Mapping):
        self.blast, self.strains, self.db_dir, self.identity, self.cfg = blast, strains, Path(db_dir), identity, cfg

    def thresholds(self) -> dict:
        return {"min_identity": self.identity, "min_coverage": self.cfg["min_coverage"],
                "min_fraction_strains": self.cfg["min_fraction_strains"], "evalue": self.cfg["evalue"],
                "on_fail": self.cfg.get("on_fail", "reject"), "strains": [s for s, _ in self.strains]}

    def tool_info(self) -> dict:
        return tool_record("BLAST+ blastp vs second-strain proteome(s)", version=self.blast.version(),
                           backend=self.blast.mode, fidelity="reference", strains=[s for s, _ in self.strains])

    def evaluate(self, cands: list[dict]) -> dict[str, GateResult]:
        if not self.strains:
            raise ToolUnavailable("no comparison strain proteome available")
        per_strain: dict[str, dict[str, Any]] = {c["protein_id"]: {} for c in cands}
        with tempfile.TemporaryDirectory(prefix="vf_s4_cons_") as td:
            q = write_fasta(Path(td) / "q.fa", _seqs(cands))
            for label, fasta in self.strains:
                db = self.blast.makeblastdb(fasta, db_prefix(self.db_dir, Path(fasta), f"strain_{Path(fasta).stem}"))
                hits = best_hits(self.blast.blastp(q, db, evalue=self.cfg["evalue"], max_target_seqs=3))
                for c in cands:
                    h = hits.get(c["protein_id"])
                    per_strain[c["protein_id"]][label] = (
                        {"conserved": False, "best_hit": None} if h is None else
                        {"conserved": h.identity >= self.identity and h.coverage >= self.cfg["min_coverage"],
                         "best_hit": h.brief()})
        res: dict[str, GateResult] = {}
        for c in cands:
            d = per_strain[c["protein_id"]]
            frac = sum(1 for v in d.values() if v["conserved"]) / len(d)
            ok = frac >= self.cfg["min_fraction_strains"]
            res[c["protein_id"]] = GateResult(
                "pass" if ok else ("reject" if self.cfg.get("on_fail", "reject") == "reject" else "flag"),
                f"conserved in {frac:.0%} of {len(d)} comparison strain(s) (identity>={self.identity}, "
                f"coverage>={self.cfg['min_coverage']})", {"fraction_strains_conserved": frac, "strains": d})
        return res


# --------------------------------------------------------------------------------- run
def _s1_files(run_dir: Path, cache_dir: str | Path) -> dict:
    """Proteomes Stream A's S1 already fetched (``cache_file`` refs), so S4 does not download a second copy."""
    p = art.find_artifact(run_dir, "s1_acquire")
    if p is None:
        return {}
    pl = art.read_artifact(p).get("payload", {})

    def path_of(entry: Mapping | None) -> Path | None:
        ref = (entry or {}).get("cache_file") or (entry or {}).get("file")
        if not isinstance(ref, Mapping) or "path" not in ref:
            return None
        f = (Path(cache_dir) if ref.get("root") == "cache" else run_dir) / ref["path"]
        return f if f.exists() else None
    comp = pl.get("comparison_proteome") or {}
    return {"human": path_of((pl.get("reference_dbs") or {}).get("human_proteome")),
            "comparison": path_of(comp), "comparison_label": comp.get("name"), "comparison_upid": comp.get("upid")}


def build_gates(cfg: Mapping, context: Mapping, refdata: RefData, run_dir: Path, warnings: list[str],
                s1: Mapping | None = None) -> dict[str, Gate | str]:
    """Return ``{gate: Gate | 'disabled' | 'unavailable: reason'}``."""
    s4, tools, thr = cfg["s4"], cfg["tools"], cfg["thresholds"]
    gates: dict[str, Gate | str] = {}
    blast = BlastRunner(tools["blast_mode"], docker=tools["docker"], docker_image=tools["blast_docker_image"],
                        threads=int(tools["blast_threads"]))
    cache = Path(cfg["refdata"]["cache_dir"])

    # human homology
    hh = s4["human_homology"]
    if not hh["enabled"]:
        gates["human_homology"] = "disabled"
    elif not blast.available():
        gates["human_homology"] = "unavailable: BLAST+ not available"
    else:
        try:
            human = refdata.human_proteome(cfg["refdata"]["human_proteome"] or (s1 or {}).get("human"),
                                           reviewed_only=cfg["refdata"]["human_reviewed_only"])
            ligands = None
            if hh["ligand_recheck"] and cfg["refdata"]["iedb_human_ligands"]:
                ligands = load_peptide_set(cfg["refdata"]["iedb_human_ligands"])
            elif hh["ligand_recheck"]:
                warnings.append("human_homology: refdata.iedb_human_ligands not set; IEDB MHC-ligand re-check skipped "
                                "(rejections are by homology only and may be over-aggressive)")
            gates["human_homology"] = HumanHomologyGate(blast, human, cache / "blastdb", hh, ligands)
        except (ToolUnavailable, ToolFailed, OSError) as e:
            gates["human_homology"] = f"unavailable: {e}"

    # allergenicity / toxicity
    for key, tool_key, default_cmd, nice in (("allergenicity", "algpred2", "algpred2", "AlgPred 2.0"),
                                            ("toxicity", "toxinpred2", "toxinpred2", "ToxinPred2")):
        c = s4[key]
        if not c["enabled"]:
            gates[key] = "disabled"
            continue
        argv = resolve_executable(tools[tool_key], default_cmd)
        gates[key] = (CliScoreGate(key, nice, argv, float(thr[key]), int(c["model"])) if argv
                      else f"unavailable: '{tools[tool_key] or default_cmd}' not found (pip install {default_cmd})")

    # conservancy
    cc = s4["conservancy"]
    if not cc["enabled"]:
        gates["conservancy"] = "disabled"
    elif not blast.available():
        gates["conservancy"] = "unavailable: BLAST+ not available"
    else:
        strains: list[tuple[str, Path]] = []
        try:
            for p in cfg["refdata"]["conservancy_proteomes"]:
                strains.append((Path(p).stem, Path(p)))
            if not strains and (s1 or {}).get("comparison"):                     # Stream A's S1 already chose and fetched one
                strains.append((f"{(s1 or {}).get('comparison_label') or 'comparison proteome'} (from S1)", s1["comparison"]))
            if not strains:
                taxid = context.get("taxid")
                species = context.get("s2_meta", {}).get("species_taxid") or taxid
                if species is None:
                    raise ToolUnavailable("no taxid in S3 context; set refdata.conservancy_proteomes")
                excl = tuple(x for x in [context.get("s2_meta", {}).get("proteome_id"), context.get("s2_meta", {}).get("upid")] if x)
                strains = refdata.strain_proteomes(int(species), exclude_upids=excl, exclude_organism_ids=(taxid,) if taxid else (),
                                                   n=cc["n_strains"])
        except (ToolUnavailable, ToolFailed, OSError) as e:
            gates["conservancy"] = f"unavailable: {e}"
        else:
            gates["conservancy"] = (ConservancyGate(blast, strains, cache / "blastdb", identity=float(thr["conservancy_identity"]), cfg=cc)
                                    if strains else "unavailable: no other-strain proteome found")
    return gates


def run(target: Any, cfg: Any = None, *, s3_path: str | Path | None = None,
        gates: Mapping[str, Gate] | None = None, refdata: RefData | None = None, force: bool = False,
        **_ignored) -> Path:
    """``target`` is a run directory or Stream A's ``RunStore`` (``run(store, config)``)."""
    cfg = coerce_config(cfg)
    store, run_dir = art.split_target(target)
    s3p = Path(s3_path) if s3_path else art.find_artifact(run_dir, "s3_rank")
    if s3p is None:
        raise ContractError(f"no s3_rank.json in {run_dir}")
    s3 = art.read_artifact(s3p)
    candidates = s3["payload"]["candidates"]
    context = s3["payload"]["context"]
    inputs = [art.input_ref(s3p, s3)]
    s4 = cfg["s4"]
    params = {"s4": s4, "thresholds": cfg["thresholds"], "tools": {k: cfg["tools"][k] for k in ("blast_mode", "blast_docker_image")},
              "refdata": {k: cfg["refdata"][k] for k in ("human_proteome", "conservancy_proteomes", "iedb_human_ligands",
                                                         "human_reviewed_only")}}
    if gates is None and refdata is None and not force:
        if store is not None:
            if art.try_reuse_store(store, STAGE, params):
                return Path(store.artifact_path("s4"))
        else:
            reused = art.try_reuse(run_dir, STAGE, inputs, params)
            if reused:
                return reused

    warnings: list[str] = []                                        # S4's own warnings only
    refdata = refdata or RefData(cfg["refdata"]["cache_dir"], offline=cfg["refdata"]["offline"])
    built = dict(build_gates(cfg, context, refdata, run_dir, warnings, _s1_files(run_dir, cfg["refdata"]["cache_dir"]))) if gates is None else {g: gates.get(g, "disabled") for g in GATES}

    per_gate: dict[str, dict[str, GateResult]] = {}
    gate_report: dict[str, dict] = {}
    tools: list[dict] = []
    for name in GATES:
        g = built.get(name, "disabled")
        if isinstance(g, str):
            status = "disabled" if g == "disabled" else "unavailable"
            gate_report[name] = {"status": status, "reason": g if status == "unavailable" else "disabled in config"}
            if status == "unavailable":
                warnings.append(f"gate '{name}' did not run: {g}")
            continue
        try:
            per_gate[name] = g.evaluate(candidates)
            gate_report[name] = {"status": "ran", "thresholds": g.thresholds()}
            tools.append(g.tool_info())
        except (ToolUnavailable, ToolFailed) as e:
            gate_report[name] = {"status": "unavailable", "reason": str(e), "thresholds": g.thresholds()}
            warnings.append(f"gate '{name}' did not run: {e}")

    unavailable = [n for n, r in gate_report.items() if r["status"] == "unavailable"]
    if unavailable and s4["on_unavailable"] == "fail":
        raise ToolUnavailable(f"S4 gates unavailable ({', '.join(unavailable)}) and s4.on_unavailable=fail")
    for n in unavailable:
        warnings.append(f"INCOMPLETE SCREENING: '{n}' was not applied; its candidates were carried forward unscreened for that gate")

    rescue = set(s4["rescue"])
    survivors, rejected = [], []
    funnel = [{"gate": "input", "n": len(candidates)}]
    alive = {c["protein_id"] for c in candidates}
    for name in GATES:
        if name in per_gate:
            counts = {"pass": 0, "flag": 0, "reject": 0, "unavailable": 0}
            for c in candidates:
                counts[per_gate[name][c["protein_id"]].status] += 1
            newly = {c["protein_id"] for c in candidates if per_gate[name][c["protein_id"]].status == "reject"} & alive
            alive -= {p for p in newly if p not in rescue}
            funnel.append({"gate": name, **{f"n_{k}": v for k, v in counts.items()}, "remaining_after_gate": len(alive)})
    for c in candidates:
        pid = c["protein_id"]
        results = {n: per_gate[n][pid].as_dict() for n in per_gate}
        rejected_by = [n for n in per_gate if per_gate[n][pid].status == "reject"]
        entry = {k: v for k, v in c.items()}
        entry["s3"] = {k: c.get(k) for k in ("rank", "tie_group", "composite_score", "protegenicity", "passes_thresholds", "terms", "evidence")}
        for k in ("rank", "tie_group", "composite_score", "protegenicity", "protegenicity_raw", "passes_thresholds", "terms", "evidence",
                  "selected", "selected_reason"):
            entry.pop(k, None)
        entry["gates"] = results
        entry["gates_unavailable"] = unavailable + [n for n in per_gate if per_gate[n][pid].status == "unavailable"]
        entry["flags"] = [n for n in per_gate if per_gate[n][pid].status == "flag"]
        if rejected_by and pid not in rescue:
            entry.update({"rejected_by": rejected_by, "recoverable": True})
            rejected.append(entry)
        else:
            if rejected_by:
                entry["rescued_despite"] = rejected_by
            survivors.append(entry)

    payload = {
        "context": context,
        "gates": gate_report,
        "incomplete_screening": bool(unavailable),
        "unavailable_gates": unavailable,
        "degraded": [f"S4 gate '{n}' did not run (candidates are not screened by it, not passed by it)" for n in unavailable],
        "funnel": funnel,
        "counts": {"n_input": len(candidates), "n_survivors": len(survivors), "n_rejected": len(rejected),
                   "n_rescued": sum(1 for s in survivors if "rescued_despite" in s)},
        "candidates": survivors,
        "rejected": rejected,
        "recovery": ("Every rejected entry keeps its full gate evidence. To recover one, add its protein_id to config "
                     "s4.rescue and re-run S4; it is then kept with 'rescued_despite' recorded."),
        "upstream": {"s3_ranker": s3["payload"]["ranker"], "s3_leakage_audit": s3["payload"]["leakage_audit"]},
        "refdata_provenance": refdata.provenance,
    }
    env = art.build_envelope(stage=STAGE, version=VERSION, run_id=run_dir.name, inputs=inputs, params=params, tools=tools,
                             warnings=warnings, payload=payload)
    return art.write_stage(target, env)


def main(argv=None) -> int:
    def extra(ap):
        ap.add_argument("--s3-path", dest="s3_path", type=Path, default=None)
    return stage_main(STAGE, run, argv, extra)


if __name__ == "__main__":
    raise SystemExit(main())
