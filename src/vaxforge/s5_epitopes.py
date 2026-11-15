"""S5 - Epitope prediction (T-cell, linear B-cell, conformational B-cell).

Input : ``runs/<run_id>/s4_filter.json``
Output: ``runs/<run_id>/s5_epitopes.json`` (+ sidecars under ``runs/<run_id>/s5/``)

README rules implemented:
  * T-cell: NetMHCpan (class I) and NetMHCIIpan (class II); class II is **down-weighted**
    (``epitopes.mhc_class_ii_weight``, default 0.3) in the combined T-cell score.
  * B-cell: BepiPred-**3.0** only (config validation rejects anything else). Proteins longer
    than 1000 aa are chunked with overlap and merged (ESM-2 training-crop length), and any truncation
    is *detected* (fewer rows than residues) rather than trusted.
  * Conformational: DiscoTope-3.0 on AlphaFold DB models (README: AUC 0.783 on AF models vs 0.795
    on experimental structures), checked for sequence identity with the candidate first.
  * Tool unavailable => that sub-analysis is recorded ``unavailable``; no heuristic stand-in is
    invented for a named predictor. The only stand-in is MHCflurry for class I, labelled
    ``fidelity: alternative``.
Predictions are hypotheses: presentable != recognised != protective.
"""
from __future__ import annotations

import csv
import re
from collections import defaultdict
from math import comb
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from .prediction import artifacts as art
from .prediction.bepipred import BepiPredRunner, call_linear_epitopes, top_fraction_mean
from .prediction.cli import stage_main
from .prediction.config import coerce_config
from .prediction.discotope import DiscoTopeRunner, summarise as discotope_summary
from .prediction.errors import ContractError, ToolFailed, ToolUnavailable
from .prediction.fasta import sanitise
from .prediction.hla import resolve_panel
from .prediction.netmhc import Binding, MHCflurryRunner, NetMHCRunner
from .prediction.refdata import RefData
from .prediction.structure_utils import parse_structure_ca
from .prediction.tools import resolve_executable, tool_record

STAGE = "s5_epitopes"
VERSION = 1
_UNIPROT_ACC = re.compile(r"^(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})$")


# ----------------------------------------------------------------------- T-cell helpers
def collapse_bindings(bindings: Sequence[Binding], *, strong: float, weak: float, mhc_class: str) -> list[dict]:
    """Group binders (``rank <= weak``) into epitope records.

    Grouping key is the 9-mer binding core (NetMHCpan / NetMHCIIpan report it), so different
    peptide lengths over the same core are ONE epitope. Backends that report no core (MHCflurry)
    set ``Binding.group`` (= start position) instead; counting every length separately would
    inflate epitope counts ~4x (seen on real data) and hits in S6.
    """
    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for b in bindings:
        if b.rank > weak:
            continue
        g = groups.setdefault((b.protein_id, b.group if b.group is not None else b.core), {"best": b, "alleles": {}})
        if b.rank < g["best"].rank:
            g["best"] = b
        cur = g["alleles"].get(b.allele)
        if cur is None or b.rank < cur:
            g["alleles"][b.allele] = b.rank
    out = []
    for (pid, key), g in groups.items():
        best: Binding = g["best"]
        core = best.core
        alleles = dict(sorted(g["alleles"].items(), key=lambda kv: (kv[1], kv[0])))
        off = best.peptide.find(core) if core in best.peptide else 0
        grouped_by_start = best.group is not None
        out.append({
            "epitope_id": f"{pid}|{mhc_class}|{best.start}:{core}" if grouped_by_start else f"{pid}|{mhc_class}|{core}",
            "protein_id": pid, "class": mhc_class, "core": core,
            "peptide": best.peptide, "start": best.start, "end": best.end,
            "core_start": best.start + off, "core_end": best.start + off + len(core) - 1,
            "alleles": list(alleles), "n_alleles": len(alleles),
            "n_strong_alleles": sum(1 for r in alleles.values() if r < strong),
            "best_rank": min(alleles.values()), "allele_ranks": {a: round(r, 4) for a, r in alleles.items()},
        })
    out.sort(key=lambda e: (-e["n_alleles"], e["best_rank"], e["start"]))
    return out


def select_epitopes(epitopes: Sequence[dict], *, max_n: int, min_alleles: int) -> list[dict]:
    pool = [e for e in epitopes if e["n_alleles"] >= min_alleles]
    pool.sort(key=lambda e: (-e["n_alleles"], e["best_rank"], e["start"]))
    chosen: list[dict] = []
    for e in pool:                                       # avoid near-duplicate cores (>=7 residues overlapping)
        if all(min(e["core_end"], c["core_end"]) - max(e["core_start"], c["core_start"]) + 1 < 7 for c in chosen):
            chosen.append(e)
        if len(chosen) >= max_n:
            break
    return chosen


def tcell_score(n_promiscuous: int, length: int, half_density: float) -> float:
    """``d / (d + half_density)`` with ``d`` = promiscuous epitopes per 100 aa.

    Strictly monotone and bounded in [0, 1): it never saturates, so candidates keep their order
    (a capped score gave every real candidate 1.0). Heuristic summary, NOT a validated predictor.
    """
    if length <= 0:
        return 0.0
    d = n_promiscuous / length * 100.0
    return d / (d + half_density)


def expected_random_per_100aa(n_alleles: int, p_strong: float, n_lengths: int, k: int) -> float:
    """Expected promiscuous (>= k strong alleles) epitopes per 100 positions if binding were random.

    %Rank is calibrated against random natural peptides, so a strong binder occurs with probability
    ``p_strong`` per allele per window by construction. Per start position an allele has ``n_lengths``
    chances; alleles are treated as independent (they are not - similar alleles share motifs - so this
    is a rough null, shown only so readers can see how far a candidate sits above chance).
    """
    q = 1.0 - (1.0 - p_strong) ** n_lengths
    tail = sum(comb(n_alleles, i) * q ** i * (1 - q) ** (n_alleles - i) for i in range(k, n_alleles + 1))
    return 100.0 * tail


# ----------------------------------------------------------------------- backend resolution
def resolve_class_i(cfg: Mapping) -> tuple[Any | None, str]:
    pref = cfg["s5"]["class_i"]["backend"]
    argv = resolve_executable(cfg["tools"]["netmhcpan"], "netMHCpan")
    if pref in ("auto", "netmhcpan") and argv:
        return NetMHCRunner("netmhcpan", argv), ""
    if pref in ("auto", "mhcflurry") and MHCflurryRunner.installed():
        return MHCflurryRunner(), ""
    return None, f"no class I predictor available (backend={pref}; netMHCpan not found, mhcflurry not installed)"


def resolve_class_ii(cfg: Mapping) -> tuple[Any | None, str]:
    argv = resolve_executable(cfg["tools"]["netmhciipan"], "netMHCIIpan")
    return (NetMHCRunner("netmhciipan", argv), "") if argv else (None, "netMHCIIpan not found (DTU licence; see docs/TOOLS_SETUP.md)")


def resolve_bcell(cfg: Mapping) -> tuple[Any | None, str]:
    argv = resolve_executable(cfg["tools"]["bepipred3"])
    if not argv:
        return None, "BepiPred-3.0 not configured (tools.bepipred3); BepiPred-2.0 is never used as a substitute"
    b = cfg["s5"]["bcell"]
    return BepiPredRunner(argv, pred=b["pred"], threshold=b["threshold"], esm_dir=b["esm_dir"]), ""


def resolve_discotope(cfg: Mapping) -> tuple[Any | None, str]:
    argv = resolve_executable(cfg["tools"]["discotope3"])
    return (DiscoTopeRunner(argv), "") if argv else (None, "DiscoTope-3.0 not configured (tools.discotope3)")


# --------------------------------------------------------------------------------- run
def run(target: Any, cfg: Any = None, *, s4_path: str | Path | None = None,
        class_i: Any | None = None, class_ii: Any | None = None, bcell: Any | None = None,
        discotope: Any | None = None, structure_lookup: Callable[[str], Path | None] | None = None,
        refdata: RefData | None = None, force: bool = False, **_ignored) -> Path:
    """``target`` is a run directory or Stream A's ``RunStore`` (``run(store, config)``)."""
    cfg = coerce_config(cfg)
    store, run_dir = art.split_target(target)
    s4p = Path(s4_path) if s4_path else art.find_artifact(run_dir, "s4_filter")
    if s4p is None:
        raise ContractError(f"no s4_filter.json in {run_dir}")
    s4 = art.read_artifact(s4p)
    cands = s4["payload"]["candidates"]
    inputs = [art.input_ref(s4p, s4)]
    s5 = cfg["s5"]
    cls_w = float(cfg["epitopes"]["mhc_class_ii_weight"])
    params = {"s5": s5, "mhc_class_ii_weight": cls_w, "bcell_tool": cfg["epitopes"]["bcell_tool"]}
    injected = any(x is not None for x in (class_i, class_ii, bcell, discotope, structure_lookup, refdata))
    if not injected and not force:
        if store is not None:
            if art.try_reuse_store(store, STAGE, params):
                return Path(store.artifact_path("s5"))
        else:
            reused = art.try_reuse(run_dir, STAGE, inputs, params)
            if reused:
                return reused

    warnings: list[str] = []
    tools: list[dict] = []
    sidecar_dir = run_dir / "data" / "s5"
    sidecar_dir.mkdir(parents=True, exist_ok=True)

    # ---- sanitise sequences for the tools (position-preserving) -------------------------
    seqs: dict[str, str] = {}
    skipped: list[dict] = []
    for c in cands:
        s = sanitise(c["sequence"])
        if s.fraction_replaced > s5["sanitize"]["max_ambiguous_fraction"]:
            skipped.append({"protein_id": c["protein_id"], "reason": f"{s.fraction_replaced:.1%} non-standard residues"})
            continue
        if s.n_replaced:
            warnings.append(f"{c['protein_id']}: {s.n_replaced} non-standard residues replaced for tool input")
        seqs[c["protein_id"]] = s.sequence
    if skipped:
        warnings.append(f"{len(skipped)} candidates skipped by S5 (too many non-standard residues)")

    # ---- T-cell class I ---------------------------------------------------------------
    ci_cfg, cii_cfg = s5["class_i"], s5["class_ii"]
    status = {"class_i": {"status": "unavailable"}, "class_ii": {"status": "unavailable"},
              "bcell_linear": {"status": "unavailable"}, "conformational": {"status": "unavailable"}}
    runner, why = (class_i, "") if class_i is not None else resolve_class_i(cfg)
    ci_epitopes: dict[str, list[dict]] = {pid: [] for pid in seqs}
    if runner is None:
        status["class_i"]["reason"] = why
        warnings.append(f"class I T-cell prediction did not run: {why}")
    else:
        panel = resolve_panel(ci_cfg["panel"], "I")
        try:
            b = runner.predict(seqs, panel, ci_cfg["lengths"], alleles_per_call=ci_cfg["alleles_per_call"], max_rank=ci_cfg["weak_rank"])
            by_pid: dict[str, list[Binding]] = defaultdict(list)
            for x in b:
                by_pid[x.protein_id].append(x)
            for pid in seqs:
                ci_epitopes[pid] = collapse_bindings(by_pid.get(pid, []), strong=ci_cfg["strong_rank"], weak=ci_cfg["weak_rank"], mhc_class="I")
            status["class_i"] = {"status": "ran", "tool": runner.name, "fidelity": runner.fidelity, "panel_size": len(panel),
                                 "panel": panel, "lengths": ci_cfg["lengths"], "thresholds": {"strong_rank": ci_cfg["strong_rank"], "weak_rank": ci_cfg["weak_rank"]}}
            unsupported = getattr(runner, "unsupported_alleles", [])
            if unsupported:
                status["class_i"]["unsupported_alleles"] = unsupported
                warnings.append(f"{runner.name} does not support {len(unsupported)} panel alleles: {unsupported}")
            if runner.fidelity != "reference":
                warnings.append(f"class I predicted with {runner.name} (open-source ALTERNATIVE), not NetMHCpan; do not label it NetMHCpan")
            tools.append(tool_record(runner.name, version=None, backend="local", fidelity=runner.fidelity, role="class I"))
        except (ToolUnavailable, ToolFailed) as e:
            status["class_i"]["reason"] = str(e)
            warnings.append(f"class I T-cell prediction failed: {e}")

    # ---- T-cell class II --------------------------------------------------------------
    cii_epitopes: dict[str, list[dict]] = {pid: [] for pid in seqs}
    if not cii_cfg["enabled"]:
        status["class_ii"] = {"status": "disabled"}
    else:
        runner2, why2 = (class_ii, "") if class_ii is not None else resolve_class_ii(cfg)
        if runner2 is None:
            status["class_ii"]["reason"] = why2
            warnings.append(f"class II T-cell prediction did not run: {why2} (T-cell score falls back to class I only)")
        else:
            panel2 = resolve_panel(cii_cfg["panel"], "II")
            try:
                b2 = runner2.predict(seqs, panel2, [cii_cfg["length"]], alleles_per_call=cii_cfg["alleles_per_call"], max_rank=cii_cfg["weak_rank"])
                by_pid2: dict[str, list[Binding]] = defaultdict(list)
                for x in b2:
                    by_pid2[x.protein_id].append(x)
                for pid in seqs:
                    cii_epitopes[pid] = collapse_bindings(by_pid2.get(pid, []), strong=cii_cfg["strong_rank"], weak=cii_cfg["weak_rank"], mhc_class="II")
                status["class_ii"] = {"status": "ran", "tool": runner2.name, "fidelity": runner2.fidelity, "panel_size": len(panel2),
                                      "panel": panel2, "length": cii_cfg["length"], "weight": cls_w,
                                      "thresholds": {"strong_rank": cii_cfg["strong_rank"], "weak_rank": cii_cfg["weak_rank"]},
                                      "note": "class II accuracy lags class I substantially; down-weighted"}
                tools.append(tool_record(runner2.name, version=None, backend="local", fidelity=runner2.fidelity, role="class II"))
            except (ToolUnavailable, ToolFailed) as e:
                status["class_ii"]["reason"] = str(e)
                warnings.append(f"class II T-cell prediction failed: {e}")

    # ---- linear B-cell (BepiPred-3.0) --------------------------------------------------
    bc = s5["bcell"]
    linear: dict[str, dict] = {}
    bp, why3 = (bcell, "") if bcell is not None else resolve_bcell(cfg)
    if bp is None:
        status["bcell_linear"]["reason"] = why3
        warnings.append(f"linear B-cell prediction did not run: {why3}")
    else:
        try:
            res = bp.predict(seqs, chunk_size=bc["chunk_size"], chunk_overlap=bc["chunk_overlap"])
            for pid, rs in res.items():
                regions = call_linear_epitopes(seqs[pid], rs.scores, threshold=bc["threshold"], min_len=bc["min_epitope_len"], merge_gap=bc["merge_gap"])
                side = sidecar_dir / f"{_safe(pid)}_bepipred.tsv"
                side.write_text("pos\tresidue\tscore\tlinear_score\n" + "".join(
                    f"{i + 1}\t{seqs[pid][i]}\t{rs.scores[i]:.5f}\t{rs.linear_scores[i]:.5f}\n" for i in range(len(seqs[pid]))))
                linear[pid] = {
                    "n_residues": len(rs.scores), "n_chunks": rs.n_chunks, "chunked": rs.n_chunks > 1,
                    "threshold": bc["threshold"], "top20pct_mean_score": round(top_fraction_mean(rs.scores, 0.2), 5),
                    "n_regions": len(regions),
                    "regions": [{"start": r.start, "end": r.end, "peptide": r.peptide, "mean_score": round(r.mean_score, 5),
                                 "max_score": round(r.max_score, 5)} for r in sorted(regions, key=lambda r: -r.mean_score)[:20]],
                    "per_residue_file": art.run_file_ref(run_dir, side)}
            status["bcell_linear"] = {"status": "ran", "tool": "BepiPred-3.0", "fidelity": "reference",
                                      "chunk_size": bc["chunk_size"], "chunk_overlap": bc["chunk_overlap"],
                                      "n_proteins_chunked": sum(1 for v in linear.values() if v["chunked"]),
                                      "note": "BepiPred-3.0 predicts antibody-contact residues; regions are hypotheses for peptide mapping"}
            tools.append(tool_record("BepiPred-3.0", version=None, backend="local", fidelity="reference", role="linear B-cell",
                                     licence="academic, non-commercial (DTU)"))
        except (ToolUnavailable, ToolFailed) as e:
            status["bcell_linear"]["reason"] = str(e)
            warnings.append(f"linear B-cell prediction failed: {e}")

    # ---- conformational B-cell (DiscoTope-3.0 on AlphaFold DB models) --------------------
    conf: dict[str, dict] = {}
    cf = s5["conformational"]
    if not cf["enabled"]:
        status["conformational"] = {"status": "disabled"}
    else:
        dt, why4 = (discotope, "") if discotope is not None else resolve_discotope(cfg)
        if dt is None:
            status["conformational"]["reason"] = why4
            warnings.append(f"conformational B-cell prediction did not run: {why4}")
        else:
            rd = refdata or RefData(cfg["refdata"]["cache_dir"], offline=cfg["refdata"]["offline"])
            lookup = structure_lookup or rd.afdb_structure
            by_id = cands_by_id(cands)
            n_ok = 0
            for pid in seqs:
                acc = by_id[pid].get("accession") or pid          # Stream A's protein_id may be 'sp|ACC|ENTRY'; use the accession
                if not _UNIPROT_ACC.match(acc) and structure_lookup is None:
                    conf[pid] = {"status": "skipped", "reason": "no UniProt accession (protein_id is not one); no AlphaFold DB lookup"}
                    continue
                try:
                    pdb = lookup(acc)
                except (ToolUnavailable, ToolFailed, OSError) as e:
                    conf[pid] = {"status": "skipped", "reason": f"structure lookup failed: {e}"}
                    continue
                if pdb is None:
                    conf[pid] = {"status": "skipped", "reason": "no AlphaFold DB model for this accession"}
                    continue
                try:
                    ca = parse_structure_ca(pdb)
                except ToolUnavailable as e:
                    conf[pid] = {"status": "skipped", "reason": str(e)}
                    continue
                if ca.sequence != by_id[pid]["sequence"]:
                    conf[pid] = {"status": "skipped", "reason": "AlphaFold DB sequence differs from the candidate sequence "
                                 "(residue numbering would not match)"}
                    continue
                try:
                    resid = dt.predict(pdb, struc_type=cf["struc_type"])
                except (ToolUnavailable, ToolFailed) as e:
                    conf[pid] = {"status": "failed", "reason": str(e)}
                    continue
                summ = discotope_summary(resid, cf["score_threshold"])
                summ.pop("epitope_residues", None)
                conf[pid] = {"status": "ran", "structure": {"source": "AlphaFold DB", "file": Path(pdb).name,
                                                           "mean_plddt": summ.get("mean_plddt_input")}, **summ}
                n_ok += 1
            status["conformational"] = {"status": "ran" if n_ok else "unavailable", "tool": "DiscoTope-3.0", "fidelity": "reference",
                                        "n_structures_scored": n_ok, "n_candidates": len(seqs),
                                        "note": "AlphaFold DB models; README: AUC 0.783 on AF models vs 0.795 on experimental structures"}
            if n_ok:
                tools.append(tool_record("DiscoTope-3.0", version=None, backend="local", fidelity="reference", role="conformational B-cell",
                                         licence="Creative Commons (see tool LICENSE)"))
            else:
                warnings.append("conformational B-cell prediction produced no scored structures")

    # ---- assemble per-candidate records + the epitope set handed to S6 --------------------
    min_prom = int(s5["min_strong_alleles_promiscuous"])
    sel_cfg = s5["select"]
    t_sel_all: list[dict] = []
    b_sel_all: list[dict] = []
    out_cands = []
    for c in cands:
        pid = c["protein_id"]
        if pid not in seqs:
            continue
        L = len(seqs[pid])
        e1, e2 = ci_epitopes[pid], cii_epitopes[pid]
        ran1, ran2 = status["class_i"]["status"] == "ran", status["class_ii"]["status"] == "ran"
        side = sidecar_dir / f"{_safe(pid)}_tcell.tsv"
        if ran1 or ran2:                                   # no header-only file for a prediction that never ran
            _write_tcell_sidecar(side, e1 + e2)
        prom1, prom2 = sum(1 for e in e1 if e["n_strong_alleles"] >= min_prom), sum(1 for e in e2 if e["n_strong_alleles"] >= min_prom)
        s1 = tcell_score(prom1, L, s5["half_score_density"]) if status["class_i"]["status"] == "ran" else None
        s2 = tcell_score(prom2, L, s5["half_score_density"]) if status["class_ii"]["status"] == "ran" else None
        null1 = (expected_random_per_100aa(len(status["class_i"]["panel"]), ci_cfg["strong_rank"] / 100.0, len(ci_cfg["lengths"]), min_prom)
                 if status["class_i"]["status"] == "ran" else None)
        null2 = (expected_random_per_100aa(len(status["class_ii"]["panel"]), cii_cfg["strong_rank"] / 100.0, 1, min_prom)
                 if status["class_ii"]["status"] == "ran" else None)
        dens1, dens2 = prom1 / L * 100.0, prom2 / L * 100.0
        if s1 is not None and s2 is not None:
            combined, basis = (1.0 * s1 + cls_w * s2) / (1.0 + cls_w), "class_i + class_ii(weighted)"
        elif s1 is not None:
            combined, basis = s1, "class_i_only"
        else:
            combined, basis = None, "unavailable"
        sel1 = select_epitopes(e1, max_n=sel_cfg["max_class_i"], min_alleles=sel_cfg["min_alleles"])
        sel2 = select_epitopes(e2, max_n=sel_cfg["max_class_ii"], min_alleles=sel_cfg["min_alleles"])
        t_sel_all += sel1 + sel2
        lin = linear.get(pid)
        if lin:
            b_sel_all += [{"protein_id": pid, **r} for r in lin["regions"][:5]]
        out_cands.append({
            "protein_id": pid, "accession": c.get("accession"), "name": c.get("name", ""), "gene": c.get("gene", ""),
            "length": c.get("length", L), "sequence": c["sequence"], "taxid": c.get("taxid"),
            "upstream": {"s3": c.get("s3"), "s4": {"gates": c.get("gates"), "flags": c.get("flags"),
                                                    "gates_unavailable": c.get("gates_unavailable"),
                                                    "rescued_despite": c.get("rescued_despite")}},
            "tcell": {
                "score": combined, "score_basis": basis,
                # an analysis that did not run reports null (contract rule 4), never 0 or []: 'no binders' and 'not computed' differ
                "class_i": {"n_epitopes": len(e1) if ran1 else None, "n_promiscuous": prom1 if ran1 else None,
                            "promiscuous_per_100aa": round(dens1, 3) if ran1 else None, "score": s1,
                            "random_expectation_per_100aa": None if null1 is None else round(null1, 3),
                            "enrichment_vs_random": None if not null1 else round(dens1 / null1, 2),
                            "top": e1[:50] if ran1 else None, "selected": [e["epitope_id"] for e in sel1] if ran1 else None},
                "class_ii": {"n_epitopes": len(e2) if ran2 else None, "n_promiscuous": prom2 if ran2 else None,
                             "promiscuous_per_100aa": round(dens2, 3) if ran2 else None, "score": s2,
                             "random_expectation_per_100aa": None if null2 is None else round(null2, 3),
                             "enrichment_vs_random": None if not null2 else round(dens2 / null2, 2),
                             "top": e2[:50] if ran2 else None, "selected": [e["epitope_id"] for e in sel2] if ran2 else None},
                "all_epitopes_file": art.run_file_ref(run_dir, side) if (ran1 or ran2) else None,
                "score_note": "d/(d+half_score_density), d = epitopes strongly bound by >= min_strong_alleles_promiscuous panel alleles per 100 aa; unvalidated heuristic - compare with random_expectation_per_100aa and re-tune with the validation harness"},
            "bcell": {"linear": lin or {"status": "unavailable"},
                      "linear_score": None if not lin else lin["top20pct_mean_score"],
                      "conformational": conf.get(pid, {"status": "unavailable"})},
        })

    degraded = [f"S5 {label} did not run" + (f": {status[k]['reason']}" if status[k].get("reason") else "")
                for k, label in (("class_i", "class I T-cell prediction"), ("class_ii", "class II T-cell prediction"),
                                 ("bcell_linear", "linear B-cell prediction"), ("conformational", "conformational B-cell prediction"))
                if status[k].get("status") not in ("ran", "disabled")]
    payload = {
        "context": s4["payload"]["context"],
        "analysis_status": status,
        "degraded": degraded,
        "skipped_candidates": skipped,
        "upstream": {"s4_incomplete_screening": s4["payload"]["incomplete_screening"],
                     "s4_unavailable_gates": s4["payload"]["unavailable_gates"],
                     "s3_ranker": s4["payload"]["upstream"]["s3_ranker"],
                     "s3_leakage_audit": s4["payload"]["upstream"]["s3_leakage_audit"]},
        "candidates": out_cands,
        "epitope_set": {
            "t_cell": [{k: e[k] for k in ("epitope_id", "protein_id", "class", "peptide", "core", "start", "end", "alleles",
                                           "n_alleles", "best_rank")} for e in t_sel_all],
            "b_cell_linear": b_sel_all,
            "note": "t_cell entries are the per-candidate selected epitopes (S6 input and S10 ELISpot peptide pools)",
        },
        "limits": ["presentable != recognised != sufficient response != protection",
                   "linear B-cell epitope prediction ceiling is roughly AUC 0.70-0.84 (README section 2, S5)"],
    }
    env = art.build_envelope(stage=STAGE, version=VERSION, run_id=run_dir.name, inputs=inputs, params=params, tools=tools,
                             warnings=warnings, payload=payload)
    return art.write_stage(target, env)


# ------------------------------------------------------------------------------ small utils
def cands_by_id(cands: Sequence[Mapping]) -> dict[str, Mapping]:
    return {c["protein_id"]: c for c in cands}


def _safe(pid: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", pid)


def _write_tcell_sidecar(path: Path, epitopes: Sequence[Mapping]) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, delimiter="\t")
        w.writerow(["epitope_id", "class", "core", "peptide", "start", "end", "n_alleles", "n_strong_alleles", "best_rank", "alleles"])
        for e in epitopes:
            w.writerow([e["epitope_id"], e["class"], e["core"], e["peptide"], e["start"], e["end"], e["n_alleles"],
                        e["n_strong_alleles"], f"{e['best_rank']:.4f}", ",".join(e["alleles"])])


def main(argv=None) -> int:
    def extra(ap):
        ap.add_argument("--s4-path", dest="s4_path", type=Path, default=None)
    return stage_main(STAGE, run, argv, extra)


if __name__ == "__main__":
    raise SystemExit(main())
