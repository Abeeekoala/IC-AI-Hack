"""S3 - Antigenicity ranking.

Input : ``runs/<run_id>/s2_annotate.json`` (proteome + virulence/localisation annotations)
Output: ``runs/<run_id>/s3_rank.json``  (full ranking with the evidence chain + the candidate set for S4)

Design decisions taken from the README / flow diagram:
  * Vaxign-ML (Docker) is the baseline ranker. VaxiJen is never used (AUC 0.56 / MCC 0.08).
  * Two protegenicity thresholds (default 0.90 and 0.85) are ALWAYS reported.
  * No Vaxign-ML AUROC/AUPRC is stated anywhere (README: could not be verified).
  * ``Unknown`` localisation stays an explicit value; it is never collapsed into "not surface".
  * Leakage is auditable: the artifact says whether the model could have seen the target
    pathogen in training (``leakage_audit``) so the validation harness can assert on it.
  * Composite weights are configurable so the validation loop can re-weight S3 (README V -> S3).

Facts verified from the Vaxign-ML-docker source: the ``protegenicity`` column is a *percentile
(0-100)* of the model score against the training-score distribution - not a probability - so it
is divided by 100 to compare against the 0.90 / 0.85 thresholds. The container swallows its own
exceptions and exits 0, so a missing ``<stem>.result.tsv`` is the only failure signal.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Protocol

from .prediction import artifacts as art
from .prediction.cli import stage_main
from .prediction.config import coerce_config
from .prediction.errors import ConfigError, ContractError, ToolFailed, ToolUnavailable
from .prediction.fasta import sanitise, write_fasta
from .prediction.schema import Protein, load_s2_annotations
from .prediction.scoring import composite, groups_split_by_cut, tie_groups, tie_key_round, tie_summary
from .prediction.tools import docker_available, docker_wrap, run_cmd, tool_record

STAGE = "s3_rank"
VERSION = 1

_GRAM_NEG = {"proteobacteria", "pseudomonadota", "bacteroidetes", "bacteroidota", "spirochaetes",
             "spirochaetota", "chlamydiae", "chlamydiota", "fusobacteria", "fusobacteriota",
             "cyanobacteria", "cyanobacteriota", "gammaproteobacteria", "betaproteobacteria"}
_GRAM_POS = {"firmicutes", "bacillota", "actinobacteria", "actinomycetota"}
_AMBIGUOUS = {"mollicutes", "mycoplasmatota", "mycobacteriaceae", "mycobacterium", "tenericutes"}


# ----------------------------------------------------------------------------- backends
@dataclass
class ProtegenicityResult:
    scores: dict[str, float]                 # protein_id -> protegenicity in [0, 1]
    raw: dict[str, float]                    # tool-native value (Vaxign-ML: percentile 0-100)
    tool: dict
    leakage: dict
    warnings: list[str] = field(default_factory=list)


class Backend(Protocol):
    name: str

    def score(self, proteins: list[Protein], organism_type: str, workdir: Path) -> ProtegenicityResult: ...


def _lineage_names(meta: Mapping) -> list[str]:
    names: list[str] = []

    def collect(node: Any) -> None:
        if isinstance(node, str):
            names.extend(x.strip().lower() for x in node.replace(";", ",").split(",") if x.strip())
        elif isinstance(node, Mapping):
            for k in ("scientific_name", "name", "ScientificName"):
                if k in node:
                    collect(node[k])
        elif isinstance(node, (list, tuple)):
            for x in node:
                collect(x)

    for key in ("lineage", "Lineage", "taxonomy_lineage"):
        if key in meta:
            collect(meta[key])
    tax = meta.get("taxonomy")
    if isinstance(tax, Mapping):
        for key in ("lineage", "Lineage"):
            if key in tax:
                collect(tax[key])
    return names


def infer_organism_type(spec: str, meta: Mapping) -> str:
    """'gram-' | 'gram+' | 'virus'. ``spec`` may force a value; otherwise infer from S0's lineage."""
    spec = (spec or "auto").lower()
    if spec in ("gram-", "gram+", "virus"):
        return spec
    if spec != "auto":
        raise ConfigError(f"s3.organism_type={spec!r}: use auto | gram- | gram+ | virus")
    explicit = str(meta.get("organism_type", "")).lower()
    if explicit in ("gram-", "gram+", "virus"):
        return explicit
    names = set(_lineage_names(meta))
    if "viruses" in names or "virus" in names:
        return "virus"
    if names & _AMBIGUOUS:
        raise ConfigError("lineage is ambiguous for Gram staining (e.g. Mycobacterium/Mollicutes); "
                          "set s3.organism_type explicitly")
    neg, pos = bool(names & _GRAM_NEG), bool(names & _GRAM_POS)
    if neg and not pos:
        return "gram-"
    if pos and not neg:
        return "gram+"
    raise ConfigError("cannot infer Gram stain / organism type from the S2 metadata; set s3.organism_type "
                      "to 'gram-', 'gram+' or 'virus' (Vaxign-ML needs it)")


def _docker_digest(image: str, docker: str) -> str | None:
    try:
        r = run_cmd([docker, "image", "inspect", "--format", "{{index .RepoDigests 0}}", image], check=False, timeout=30)
        return r.stdout.strip() or None
    except Exception:                                      # noqa: BLE001 - provenance is best-effort
        return None


def parse_vaxign_result(text: str) -> tuple[dict[str, float], dict[str, float]]:
    """``<stem>.result.tsv`` -> ``(scores_0_1, raw_percentiles)``. Header: sample, prediction, protegenicity."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines or lines[0].split("\t")[:3] != ["sample", "prediction", "protegenicity"]:
        raise ToolFailed(f"unexpected Vaxign-ML result header: {lines[0][:80] if lines else '<empty>'!r}")
    raw: dict[str, float] = {}
    for ln in lines[1:]:
        c = ln.split("\t")
        if len(c) < 3:
            raise ToolFailed(f"malformed Vaxign-ML row: {ln[:80]!r}")
        raw[c[0]] = float(c[2])
    bad = [k for k, v in raw.items() if not 0.0 <= v <= 100.0 + 1e-6]
    if bad:
        raise ToolFailed(f"protegenicity outside the expected 0-100 percentile range for {bad[:3]}")
    return {k: v / 100.0 for k, v in raw.items()}, raw


class VaxignMLBackend:
    """Runs the ``e4ong1031/vaxign-ml`` image exactly as the project's own ``VaxignML.sh`` does."""

    name = "vaxign_ml"

    def __init__(self, *, image: str, docker: str = "docker", use_sudo: bool = False, processes: int | None = None,
                 saved_model_dir: str | None = None, timeout: float = 4 * 3600, extra_docker_args=(),
                 cache_dir: str | Path = "cache"):
        self.image, self.docker, self.sudo, self.processes = image, docker, use_sudo, processes
        self.model_dir = Path(saved_model_dir).resolve() if saved_model_dir else None
        self.timeout, self.extra = timeout, list(extra_docker_args)
        self.cache = Path(cache_dir) / "vaxign_ml"

    def available(self) -> bool:
        return docker_available(self.docker)

    def _cache_key(self, fasta_text: str, organism: str) -> str:
        h = hashlib.sha256()
        for part in (fasta_text, organism, self.image, str(self.model_dir or "stock")):
            h.update(part.encode())
            h.update(b"\0")
        if self.model_dir and self.model_dir.exists():
            for f in sorted(self.model_dir.glob("*.sav")) + sorted(self.model_dir.glob("*.scores")):
                h.update(f.name.encode())
                h.update(hashlib.sha256(f.read_bytes()).digest())
        return h.hexdigest()[:24]

    def score(self, proteins: list[Protein], organism_type: str, workdir: Path) -> ProtegenicityResult:
        warns: list[str] = []
        entries, replaced = [], 0
        for p in proteins:
            s = sanitise(p.sequence)
            replaced += s.n_replaced
            entries.append((p.protein_id, s.sequence))
        if replaced:
            warns.append(f"{replaced} non-standard residues replaced position-preservingly before Vaxign-ML")
        fasta_text = "".join(f">{i}\n{s}\n" for i, s in entries)
        key = self._cache_key(fasta_text, organism_type)
        cached = self.cache / key / "input.result.tsv"
        digest = None
        if cached.exists():
            result_text = cached.read_text()
            cache_hit = True
            digest_file = cached.with_name("image_digest.txt")                  # keep provenance on cache hits too
            digest = digest_file.read_text().strip() if digest_file.exists() else None
        else:
            if not self.available():
                raise ToolUnavailable("Docker daemon not reachable; Vaxign-ML needs Docker (README section 5)")
            cache_hit = False
            work = Path(tempfile.mkdtemp(prefix="vf_vaxign_", dir=workdir if workdir.exists() else None)).resolve()
            try:
                fasta = write_fasta(work / "input.faa", entries)
                out = work / "out"
                (out / "_FEATURE" / "PSORTB").mkdir(parents=True)
                argv = ["python3.6", "VaxignML.py", "-i", str(fasta), "-o", str(out), "-t", organism_type]
                mounts = [fasta.parent, out, out / "_FEATURE" / "PSORTB"]
                if self.model_dir:
                    argv += ["-s", str(self.model_dir)]
                    mounts.append(self.model_dir)
                if self.processes is not None:
                    argv += ["-p", str(self.processes)]
                cmd = docker_wrap(self.image, argv, mounts, docker=self.docker, sudo=self.sudo, extra=self.extra)
                # replicate VaxignML.sh: the PSORTB feature dir is mounted at /tmp/results inside the container
                cmd = _retarget_psortb_mount(cmd, out / "_FEATURE" / "PSORTB")
                res = run_cmd(cmd, timeout=self.timeout, what="Vaxign-ML docker", check=False)
                produced = out / "input.result.tsv"
                if res.returncode != 0 or not produced.exists():
                    # VaxignML.py catches every exception and still exits 0: absence of the file is the failure.
                    tail = (res.stdout.strip() or res.stderr.strip())[-1200:]
                    raise ToolFailed(f"Vaxign-ML produced no result file (exit {res.returncode}). Container output tail: {tail}")
                result_text = produced.read_text()
                self.cache.joinpath(key).mkdir(parents=True, exist_ok=True)
                cached.write_text(result_text)
                digest = _docker_digest(self.image, self.docker)
                if digest:
                    cached.with_name("image_digest.txt").write_text(digest + "\n")
            finally:
                shutil.rmtree(work, ignore_errors=True)
        scores, raw = parse_vaxign_result(result_text)
        missing = [p.protein_id for p in proteins if p.protein_id not in scores]
        if missing:
            warns.append(f"Vaxign-ML returned no score for {len(missing)} proteins (e.g. {missing[:3]}); they are ranked last")
        stock = self.model_dir is None
        holdout = _read_sidecar(self.model_dir / "holdout.json") if self.model_dir else None
        leak = {
            "model": "vaxign-ml pretrained (trained on all of Protegen)" if stock else f"custom model dir {self.model_dir}",
            "holdout_enforced": False if stock else None,
            "declared_holdout_taxids": (holdout or {}).get("holdout_taxids"),
            "note": ("The stock Vaxign-ML model was trained on all of Protegen. If the target pathogen has Protegen entries "
                     "(true for N. meningitidis / Bexsero), do NOT present rediscovery of known antigens as validation."
                     if stock else
                     "Custom model: holdout is only as good as the training set that produced it; the validation harness "
                     "must verify it (see validation/leakage_check.py)."),
        }
        tool = tool_record("Vaxign-ML", version=digest or f"docker:{self.image}", backend="docker", fidelity="reference",
                           organism_type=organism_type, cache_hit=cache_hit,
                           score_semantics="protegenicity percentile (0-100) divided by 100")
        return ProtegenicityResult(scores, raw, tool, leak, warns)


def _retarget_psortb_mount(cmd: list[str], psortb_dir: Path) -> list[str]:
    """docker_wrap mounts every path at itself; VaxignML.sh mounts the PSORTB dir at /tmp/results instead."""
    same = f"{psortb_dir.resolve()}:{psortb_dir.resolve()}"
    return [f"{psortb_dir.resolve()}:/tmp/results" if tok == same else tok for tok in cmd]


def _read_sidecar(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


class ScoresFileBackend:
    """Precomputed protegenicity, e.g. from a LOPOV-retrained Vaxign-ML run by the validation harness.

    TSV (``protein_id<TAB>score``) or JSON (``{id: score}``). Scores are taken as already in [0, 1]
    unless ``scale`` is 100. An optional sidecar ``<file>.meta.json`` may declare
    ``{"holdout_taxids": [...], "model": "...", "scale": 100}``.
    """

    name = "scores_file"

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def score(self, proteins: list[Protein], organism_type: str, workdir: Path) -> ProtegenicityResult:
        if not self.path.exists():
            raise ToolUnavailable(f"scores file not found: {self.path}")
        meta = _read_sidecar(self.path.with_name(self.path.name + ".meta.json")) or {}
        scale = float(meta.get("scale", 1.0))
        text = self.path.read_text()
        raw: dict[str, float] = {}
        if self.path.suffix == ".json":
            raw = {str(k): float(v) for k, v in json.loads(text).items()}
        else:
            for ln in text.splitlines():
                c = ln.split("\t")
                if len(c) >= 2 and c[0] != "protein_id":
                    raw[c[0]] = float(c[1])
        scores = {k: v / scale for k, v in raw.items()}
        bad = [k for k, v in scores.items() if not 0.0 <= v <= 1.0 + 1e-9]
        if bad:
            raise ContractError(f"{self.path.name}: scores outside [0, 1] for {bad[:3]} (set 'scale': 100 in the sidecar?)")
        declared = meta.get("holdout_taxids")
        leak = {"model": meta.get("model", f"scores file {self.path.name}"),
                # the sidecar may declare ``"holdout_enforced": false`` (e.g. a stock Vaxign-ML model trained on all of
                # Protegen), which switches on the LEAKAGE RISK warning; otherwise it stays unknown (None), never True
                "holdout_enforced": False if meta.get("holdout_enforced") is False else None,
                "declared_holdout_taxids": declared,
                "note": "Scores supplied externally; whether the model held out the target pathogen is declared, not verified here."}
        tool = tool_record("precomputed-protegenicity", version=meta.get("version"), backend="scores_file",
                           fidelity="reference" if meta.get("model", "").lower().startswith("vaxign") else "alternative")
        return ProtegenicityResult(scores, raw, tool, leak, [])


# ------------------------------------------------------------------------------- scoring
def localisation_term(p: Protein, prior: Mapping[str, float]) -> float:
    return float(prior.get(p.localisation.label, prior.get("Unknown", 0.5)))


def virulence_term(p: Protein, cfg: Mapping) -> float:
    v = p.virulence
    if v is None or v.identity is None:
        return 0.0
    cov = 1.0 if v.coverage is None else v.coverage
    ok = (v.identity >= cfg["min_identity"] and cov >= cfg["min_coverage"] and
          (v.evalue is None or v.evalue <= cfg["max_evalue"]))
    return float(v.identity * cov) if ok else 0.0


def _load_weights(spec: Any) -> dict[str, float]:
    if isinstance(spec, (str, Path)):
        return {k: float(v) for k, v in json.loads(Path(spec).read_text()).items()}
    return {k: float(v) for k, v in dict(spec).items()}


def _make_backend(cfg: Mapping, name: str) -> Backend | None:
    s3 = cfg["s3"]
    if name == "scores_file":
        if not s3["scores_file"]:
            raise ConfigError("s3.backend=scores_file needs s3.scores_file")
        return ScoresFileBackend(s3["scores_file"])
    if name == "vaxign_ml":
        v = s3["vaxign_ml"]
        return VaxignMLBackend(image=v["docker_image"], docker=cfg["tools"]["docker"], use_sudo=v["use_sudo"],
                               processes=v["processes"], saved_model_dir=v["saved_model_dir"], timeout=v["timeout_s"],
                               extra_docker_args=v["extra_docker_args"], cache_dir=cfg["refdata"]["cache_dir"])
    if name == "subtractive_filter":
        return None
    raise ConfigError(f"unknown s3.backend {name!r}")


# --------------------------------------------------------------------------------- run
def run(target: Any, cfg: Any = None, *, s2_path: str | Path | None = None,
        backend: Backend | None = None, force: bool = False, **_ignored) -> Path:
    """``target`` is a run directory or Stream A's ``RunStore`` (``run(store, config)``, as ``vaxforge run`` calls it)."""
    cfg = coerce_config(cfg)
    store, run_dir = art.split_target(target)
    s3 = cfg["s3"]
    s2p = Path(s2_path) if s2_path else art.find_artifact(run_dir, "s2_annotate", ("s2_annotations", "s2"))
    if s2p is None:
        raise ContractError(f"no S2 artifact in {run_dir} (looked for s2_annotate.json); pass --s2-path")
    s2 = load_s2_annotations(s2p, cache_dir=cfg["refdata"]["cache_dir"])
    inputs = [art.input_ref(s2p)]
    weights = _load_weights(s3["weights"])
    fallback_weights = _load_weights(s3["fallback_weights"])
    thresholds = sorted({float(t) for t in cfg["thresholds"]["protegenicity"]}, reverse=True)
    params = {"backend": s3["backend"], "organism_type": s3["organism_type"], "weights": weights,
              "fallback_weights": fallback_weights, "thresholds": thresholds,
              "localisation_prior": s3["localisation_prior"], "virulence": s3["virulence"],
              "min_candidates": s3["min_candidates"], "max_candidates": s3["max_candidates"],
              "holdout": s3["holdout"], "model_dir": s3["vaxign_ml"]["saved_model_dir"],
              "scores_file": str(s3["scores_file"]) if s3["scores_file"] else None,
              "docker_image": s3["vaxign_ml"]["docker_image"], "seed": cfg["reproducibility"]["seed"]}
    if backend is None and not force:
        reused = (Path(store.artifact_path("s3")) if store is not None and art.try_reuse_store(store, STAGE, params)
                  else art.try_reuse(run_dir, STAGE, inputs, params) if store is None else None)
        if reused:
            old = art.read_artifact(reused)["payload"]["ranker"]["backend"]
            # a fallback produced while Docker was down must not shadow a later real Vaxign-ML run
            if not (s3["backend"] == "auto" and old == "subtractive_filter"):
                return reused

    warnings: list[str] = []
    backend_name = s3["backend"]
    result: ProtegenicityResult | None = None
    organism_type = None
    if backend is None:
        order = ["vaxign_ml", "subtractive_filter"] if backend_name == "auto" else [backend_name]
        for cand in order:
            b = _make_backend(cfg, cand)
            if b is None:
                warnings.append("RULE-BASED FALLBACK: Vaxign-ML was not run; ranking uses S2 localisation + VFDB evidence only "
                                "and has NO protegenicity score. It is a transparent heuristic, not Vaxign-ML.")
                backend_name = "subtractive_filter"
                break
            try:
                organism_type = infer_organism_type(s3["organism_type"], s2.meta) if cand == "vaxign_ml" else "n/a"
                result = b.score(s2.proteins, organism_type, run_dir)
                backend_name = cand
                break
            except ToolUnavailable as e:
                if backend_name != "auto":
                    raise
                warnings.append(f"Vaxign-ML unavailable ({e}); fell back to the rule-based ranker")
    else:
        # injected backend (tests / validation harness); only Vaxign-ML itself needs the Gram stain
        organism_type = infer_organism_type(s3["organism_type"], s2.meta) if backend.name == "vaxign_ml" else "n/a"
        result = backend.score(s2.proteins, organism_type, run_dir)
        backend_name = backend.name

    use_weights = weights if result is not None else fallback_weights
    prior = s3["localisation_prior"]
    target_taxid = next((p.taxid for p in s2.proteins if p.taxid), s2.meta.get("taxid"))
    rows = []
    unrecognised = 0
    for p in s2.proteins:
        unrecognised += 0 if p.localisation.recognised else 1
        prot = result.scores.get(p.protein_id) if result else None
        terms = {"protegenicity": prot, "localisation": localisation_term(p, prior), "virulence": virulence_term(p, s3["virulence"])}
        rows.append({"p": p, "terms": terms, "composite": composite(terms, use_weights), "prot": prot})
    if unrecognised:
        warnings.append(f"{unrecognised} proteins had unrecognised localisation labels; treated as 'Unknown'")
    lsc = s2.meta.get("localisation_status_counts") or {}
    if lsc.get("not_run"):
        warnings.append(f"S2 localisation was NOT RUN for {lsc['not_run']} proteins (no PSORTb): they are 'Unknown' with a neutral prior, not 'not surface'")
    if lsc.get("fixture_stand_in"):
        warnings.append(f"localisation for {lsc['fixture_stand_in']} proteins is a fixture stand-in from UniProt text, not PSORTb")
    rows.sort(key=lambda r: (r["composite"] is None, -(r["composite"] or 0.0), -(r["prot"] or 0.0),
                             -r["terms"]["localisation"], r["p"].protein_id))
    # `rank` stays the ordinal position (stable, readers rely on it). Candidates the evidence cannot tell apart
    # (equal composite, protegenicity and localisation prior) are ordered by protein_id only: mark them as a tie.
    tgroups = tie_groups([(tie_key_round(r["composite"]), tie_key_round(r["prot"]), tie_key_round(r["terms"]["localisation"]))
                          for r in rows])

    lenient = min(thresholds)
    selected_ids: dict[str, str] = {}
    if result is not None:
        for r in rows:
            if r["prot"] is not None and r["prot"] >= lenient:
                selected_ids[r["p"].protein_id] = f"protegenicity>={lenient:.2f}"
    # With a real protegenicity score the threshold defines the set and padding only prevents starving S4;
    # the rule-based fallback has no threshold, so it hands S4 the full candidate budget.
    pad_to = min(s3["min_candidates"] if result is not None else s3["max_candidates"], len(rows))
    for r in rows:
        if len(selected_ids) >= pad_to:
            break
        if r["p"].protein_id not in selected_ids and r["composite"] is not None:
            selected_ids[r["p"].protein_id] = "rank_padding" if result is not None else "rule_based_top_rank"
    ordered = [r["p"].protein_id for r in rows if r["p"].protein_id in selected_ids]
    truncated = max(0, len(ordered) - s3["max_candidates"])
    if truncated:
        ordered = ordered[: s3["max_candidates"]]
        warnings.append(f"{truncated} candidates above the threshold were cut by s3.max_candidates={s3['max_candidates']}")
    keep = set(ordered)
    ties = {**tie_summary(tgroups), "basis": "equal composite_score, protegenicity and localisation prior (rounded to 9 decimals); "
                                            "inside a tie the order is by protein_id only, not by evidence"}
    if ties["n_groups"]:
        lg = ties["largest_group"]
        warnings.append(f"{ties['n_tied_candidates']} candidates are tied with another one on composite score, protegenicity and "
                        f"localisation prior (largest tie: {lg['size']} at rank {lg['competition_rank']}); inside a tie the order is "
                        f"by protein_id only, not by evidence (see tie_group)")
    for g in groups_split_by_cut(tgroups, [r["p"].protein_id in keep for r in rows]):
        warnings.append(f"the candidate cut falls inside a tie: {g['kept']} of {g['size']} candidates tied at rank "
                        f"{g['competition_rank']} were kept, chosen by protein_id order and not by evidence")

    def entry(rank: int, r: dict, with_seq: bool) -> dict:
        p: Protein = r["p"]
        e = {
            "protein_id": p.protein_id, "name": p.name, "gene": p.gene, "length": p.length, "rank": rank,
            "tie_group": tgroups[rank - 1],
            "composite_score": r["composite"], "protegenicity": r["prot"],
            "protegenicity_raw": None if result is None else result.raw.get(p.protein_id),
            "passes_thresholds": {f"{t:.2f}": (None if r["prot"] is None else r["prot"] >= t) for t in thresholds},
            "terms": r["terms"],
            "evidence": {
                "localisation": {"label": p.localisation.label, "raw": p.localisation.raw, "score": p.localisation.score,
                                 "source": p.localisation.source, "status": p.localisation.status, "prior": r["terms"]["localisation"],
                                 "is_unknown": p.localisation.label == "Unknown"},
                "virulence": None if p.virulence is None else {
                    "hit": p.virulence.hit_id, "identity": p.virulence.identity, "coverage": p.virulence.coverage,
                    "evalue": p.virulence.evalue, "term": r["terms"]["virulence"]},
            },
            "selected": p.protein_id in keep,
            "selected_reason": selected_ids.get(p.protein_id) if p.protein_id in keep else None,
        }
        if with_seq:
            e["sequence"] = p.sequence
            e["taxid"] = p.taxid
            e["accession"] = p.accession
        return e

    ranking_entries = [entry(i + 1, r, with_seq=False) for i, r in enumerate(rows)]
    by_id = {r["p"].protein_id: (i + 1, r) for i, r in enumerate(rows)}
    candidates = [entry(by_id[pid][0], by_id[pid][1], with_seq=True) for pid in ordered]

    n_scored = sum(1 for r in rows if r["prot"] is not None)
    summary = {f"{t:.2f}": {"n_pass": sum(1 for r in rows if r["prot"] is not None and r["prot"] >= t),
                            "fraction_of_proteome": (sum(1 for r in rows if r["prot"] is not None and r["prot"] >= t) / len(rows)),
                            "available": result is not None} for t in thresholds}
    if result is None:
        leak = {"model": "none (rule-based, nothing trained)", "holdout_enforced": True,
                "note": "No trained model is involved, so there is no training-set leakage."}
        tools = [tool_record("subtractive-filter", version="vaxforge-s3-rule/1", backend="rule_based", fidelity="fallback")]
        semantics = "composite of S2 localisation prior and VFDB evidence; NOT a protegenicity score"
    else:
        leak, tools, semantics = result.leakage, [result.tool], "protegenicity in [0,1] = Vaxign-ML percentile/100 (see tool record)"
        warnings.extend(result.warnings)
        if leak.get("holdout_enforced") is False:
            warnings.append("LEAKAGE RISK: pretrained Vaxign-ML was trained on all Protegen entries; the target pathogen's "
                            "known antigens are in its training set. Do not present their rediscovery as validation.")
    leak = {**leak, "target_taxid": target_taxid, "requested_holdout": s3["holdout"]}

    payload = {
        "context": {"taxid": target_taxid,
                    # only Vaxign-ML needs the Gram stain, but record what the upstream stage knows whatever backend ran
                    "organism_type": organism_type if organism_type not in (None, "n/a") else s2.meta.get("organism_type", "n/a"),
                    "s2_meta": _jsonable_meta(s2.meta),
                    "n_proteins": len(rows)},
        "ranker": {"backend": backend_name, "score_semantics": semantics, "weights_used": use_weights,
                   "thresholds": thresholds,
                   "note": "Default weights reproduce the pure Vaxign-ML ordering; other terms are recorded as evidence only."},
        "leakage_audit": leak,
        "counts": {"n_proteins": len(rows), "n_scored": n_scored, "n_unscored": len(rows) - n_scored,
                   "n_selected": len(ordered), "threshold_summary": summary,
                   "fold_enrichment": None,
                   "fold_enrichment_note": "Needs ground-truth positives; computed by the validation harness, not here."},
        "degraded": ([] if result is not None else
                      ["S3 used the rule-based fallback ranker, not Vaxign-ML or a scores file: no protegenicity, "
                       "a composite of localisation and virulence evidence only"]),
        "ties": ties,
        "candidates": candidates,
        "ranking": ranking_entries,
    }
    env = art.build_envelope(stage=STAGE, version=VERSION, run_id=run_dir.name, inputs=inputs, params=params,
                             tools=tools, warnings=warnings, payload=payload)
    return art.write_stage(target, env)


def _jsonable_meta(meta: Mapping) -> dict:
    try:
        json.dumps(meta)
        return dict(meta)
    except TypeError:
        return {k: str(v) for k, v in meta.items()}


def main(argv=None) -> int:
    def extra(ap):
        ap.add_argument("--s2-path", dest="s2_path", type=Path, default=None, help="explicit S2 artifact path")
    return stage_main(STAGE, run, argv, extra)


if __name__ == "__main__":
    raise SystemExit(main())
