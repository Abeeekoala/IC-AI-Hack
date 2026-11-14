"""S7 - Structure for the top-k candidates, validator agreement, and the final coarse ranking.

Input : ``runs/<run_id>/s6_coverage.json``
Output: ``runs/<run_id>/s7_structure.json`` (+ structures under ``runs/<run_id>/s7/<protein_id>/``)

What this stage does:
  1. Pre-selects the top-k candidates from S3/S5/S6 evidence (structure is the expensive step).
  2. Folds them with **Boltz-2** (MIT). AlphaFold 3 is never used (non-commercial weights).
  3. Re-folds with a second and third independent predictor (ESMFold, AF2-no-MSA) and reports the
     agreement (lDDT-C-alpha, fixed-alignment TM-score, RMSD) and the *spread* between validators
     - README: "always report which validator you used, and ideally report two".
  4. Runs DiscoTope-3.0 on the Boltz-2 model and flags predicted epitope residues that sit in
     low-pLDDT regions (the S10 "folded vs denatured" falsification hinges on this).
  5. Produces the final coarse ranking handed to S8-S11.

What it deliberately does NOT do: ClusPro-TLR docking (README section 7); any affinity value
(Boltz-2's affinity head only supports protein - small-molecule binding); any immunogenicity
claim from structure confidence. pLDDT / validator agreement measure the *quality of the
structural model*, not the vaccine potential of the antigen.
Without a GPU / Boltz install the stage degrades to sequence-only ranking (README section 5).
"""
from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any, Mapping

from .prediction import artifacts as art
from .prediction.cli import stage_main
from .prediction.config import coerce_config
from .prediction.discotope import DiscoTopeRunner, summarise as discotope_summary
from .prediction.errors import ContractError, ToolFailed, ToolUnavailable
from .prediction.folders import BoltzRunner, ColabFoldSingleSeqRunner, ESMFoldRunner, rewrite_bfactors
from .prediction.scoring import composite, percentile_ranks
from .prediction.structure_utils import lddt_ca, parse_structure_ca, plddt_summary, rmsd_after_superposition, tm_score
from .prediction.tools import resolve_executable, tool_record

STAGE = "s7_structure"
VERSION = 1

LIMITS = [
    "pLDDT and validator agreement describe the quality of the structural MODEL, not the antigen's immunogenicity or protective potential",
    "the same sequence can score differently under different refolders (MotifBench: 28.05 with ESMFold vs 22.5 with AF2-no-MSA) - always read the validator spread",
    "no docking score, TLR docking or affinity is produced: ClusPro blind docking is 13.7% top-1 / 45.1% top-10 and a docking score is neither affinity nor immunogenicity; Boltz-2's affinity head supports protein-small-molecule binding only",
    "final_score weights are unvalidated priors intended to be re-tuned by the retrospective validation harness",
    "structure-informed ranking exists only for the top-k; the remaining candidates keep their pre-structure order below them",
]


# ------------------------------------------------------------------------ resolution
def resolve_boltz(cfg: Mapping) -> tuple[BoltzRunner | None, str]:
    argv = resolve_executable(cfg["tools"]["boltz"], "boltz")
    if not argv:
        return None, "boltz not found on PATH (pip install boltz; MIT licence)"
    b = cfg["s7"]["boltz"]
    return BoltzRunner(argv, accelerator=b["accelerator"], msa=b["msa"], recycling_steps=b["recycling_steps"],
                       sampling_steps=b["sampling_steps"], diffusion_samples=b["diffusion_samples"],
                       use_potentials=b["use_potentials"], timeout=b["timeout_s"], extra_args=b["extra_args"],
                       cache_dir=cfg["refdata"]["cache_dir"]), ""


def resolve_refolders(cfg: Mapping) -> dict[str, tuple[Any | None, str]]:
    out: dict[str, tuple[Any | None, str]] = {}
    for name in cfg["structure"]["refolder"]:
        if name == "esmfold":
            e = cfg["s7"]["esmfold"]
            if e["mode"] == "api" and cfg["refdata"]["offline"]:
                out[name] = (None, "offline mode and esmfold mode 'api'")
            elif e["mode"] == "local" and importlib.util.find_spec("transformers") is None:
                out[name] = (None, "ESMFold not available locally (needs transformers + torch + ~8 GB of weights); set s7.esmfold.mode: api "
                                   "to use the public ESM Atlas web service instead (it sends the sequence to a third-party server)")
            else:
                out[name] = (ESMFoldRunner(mode=e["mode"], api_url=e["api_url"], max_residues_api=e["max_residues_api"]), "")
        elif name == "af2-no-msa":
            argv = resolve_executable(cfg["tools"]["colabfold_batch"], "colabfold_batch")
            out[name] = (ColabFoldSingleSeqRunner(argv), "") if argv else (None, "colabfold_batch not found (LocalColabFold)")
    return out


def resolve_discotope(cfg: Mapping) -> DiscoTopeRunner | None:
    argv = resolve_executable(cfg["tools"]["discotope3"])
    return DiscoTopeRunner(argv) if argv else None


# ----------------------------------------------------------------------- scoring
def raw_terms(c: Mapping) -> dict[str, float | None]:
    s3 = (c.get("upstream") or {}).get("s3") or {}
    return {
        "s3": s3.get("composite_score"),
        "tcell": (c.get("tcell") or {}).get("score"),
        "bcell": (c.get("bcell") or {}).get("linear_score"),
        "coverage": (c.get("coverage") or {}).get("score"),
    }


def score_group(group: Mapping[str, Mapping[str, float | None]], weights: Mapping[str, float]) -> dict[str, dict]:
    """Percentile-rank each term within ``group`` then take the weighted composite.

    Percentiles make terms with very different native scales (protegenicity ~0-1, BepiPred
    probabilities ~0.1-0.4, coverage ~0-1) commensurable; raw values are kept alongside.
    """
    terms = sorted({t for v in group.values() for t in v})
    pct: dict[str, dict[str, float]] = {}
    for t in terms:
        vals = {pid: v[t] for pid, v in group.items() if v.get(t) is not None}
        pct[t] = percentile_ranks(vals)
    out = {}
    for pid, v in group.items():
        tp = {t: pct[t].get(pid) for t in terms}
        used = [t for t in terms if tp[t] is not None and weights.get(t, 0) > 0]
        out[pid] = {"score": composite(tp, weights), "terms_percentile": tp, "terms_raw": dict(v),
                    "terms_used": used, "terms_missing": [t for t in terms if weights.get(t, 0) > 0 and tp[t] is None]}
    return out


# --------------------------------------------------------------------------- run
def run(target: Any, cfg: Any = None, *, s6_path: str | Path | None = None,
        folder: Any | None = None, refolders: Mapping[str, Any] | None = None, discotope: Any | None = None,
        force: bool = False, **_ignored) -> Path:
    """``target`` is a run directory or Stream A's ``RunStore`` (``run(store, config)``)."""
    cfg = coerce_config(cfg)
    store, run_dir = art.split_target(target)
    s6p = Path(s6_path) if s6_path else art.find_artifact(run_dir, "s6_coverage")
    if s6p is None:
        raise ContractError(f"no s6_coverage.json in {run_dir}")
    s6 = art.read_artifact(s6p)
    cands = s6["payload"]["candidates"]
    inputs = [art.input_ref(s6p, s6)]
    s7 = cfg["s7"]
    top_k = int(s7["top_k"] or cfg["run"]["top_k"])
    weights = {k: float(v) for k, v in s7["final_weights"].items()}
    params = {"s7": s7, "structure": cfg["structure"], "top_k": top_k, "weights": weights}
    injected = any(x is not None for x in (folder, refolders, discotope))
    if not injected and not force:
        if store is not None:
            if art.try_reuse_store(store, STAGE, params):
                return Path(store.artifact_path("s7"))
        else:
            reused = art.try_reuse(run_dir, STAGE, inputs, params)
            if reused:
                return reused

    warnings: list[str] = []
    tools: list[dict] = []

    # ---- 1. pre-structure score and top-k -------------------------------------------------
    pre_w = {k: v for k, v in weights.items() if k != "structure"}
    pre = score_group({c["protein_id"]: raw_terms(c) for c in cands}, pre_w)
    order = sorted(cands, key=lambda c: (pre[c["protein_id"]]["score"] is None, -(pre[c["protein_id"]]["score"] or 0.0),
                                         ((c.get("upstream") or {}).get("s3") or {}).get("rank") or 10 ** 9, c["protein_id"]))
    top = order[: min(top_k, len(order))]
    top_ids = [c["protein_id"] for c in top]
    if len(cands) > top_k:
        warnings.append(f"only the top {top_k} of {len(cands)} candidates are folded and structure-ranked")
    if not any(pre[p]["terms_used"] for p in pre):
        warnings.append("no usable S3/S5/S6 terms for pre-selection; ordering falls back to S3 rank only")

    # ---- 2. Boltz-2 --------------------------------------------------------------------------
    fold_status: dict[str, Any]
    if folder is None:
        folder, why = resolve_boltz(cfg)
    else:
        why = ""
    if folder is None:
        fold_status = {"status": "unavailable", "reason": why}
        warnings.append(f"DEGRADED: Boltz-2 did not run ({why}); ranking is sequence-only (README section 5)")
    else:
        fold_status = {"status": "ran", "tool": folder.name, "version": getattr(folder, "version", None),
                       "msa_mode": getattr(folder, "msa", None), "accelerator": getattr(folder, "accelerator", None),
                       "seed_controlled": False, "seed_note": "Boltz CLI exposes no documented seed option; outputs are cached by content instead"}
        tools.append(tool_record(folder.name, version=getattr(folder, "version", None), backend="local", fidelity=folder.fidelity,
                                 sends_sequence_off_machine=bool(getattr(folder, "sends_sequence_off_machine", False)),
                                 off_machine_server="ColabFold MMseqs2 MSA server" if getattr(folder, "sends_sequence_off_machine", False) else None))
        if getattr(folder, "sends_sequence_off_machine", False):
            warnings.append("Boltz-2 used --use_msa_server: candidate sequences were sent to the ColabFold MSA server "
                            "(set s7.boltz.msa=empty to stay offline at an accuracy cost)")

    if refolders is None:
        ref_resolved = resolve_refolders(cfg)
    else:
        ref_resolved = {n: (r, "") for n, r in refolders.items()}
    ref_status = {n: ({"status": "ready"} if r is not None else {"status": "unavailable", "reason": why_})
                  for n, (r, why_) in ref_resolved.items()}
    if folder is not None:
        for n, (r, why_) in ref_resolved.items():
            if r is None:
                warnings.append(f"refolder '{n}' unavailable: {why_}")
        if sum(1 for r, _ in ref_resolved.values() if r is not None) < 2:
            warnings.append("fewer than two independent refolders available: validator agreement rests on a single validator "
                            "(README: ideally report two)")

    dt = discotope if discotope is not None else (resolve_discotope(cfg) if (folder is not None and s7["conformational_on_boltz"]) else None)

    structures: dict[str, dict] = {}
    used_external: set[str] = set()
    for c in top:
        pid, seq = c["protein_id"], c["sequence"]
        if folder is None:
            structures[pid] = {"status": "unavailable", "reason": fold_status["reason"]}
            continue
        if len(seq) > int(s7["boltz"]["max_residues"]):
            structures[pid] = {"status": "skipped", "reason": f"{len(seq)} residues > s7.boltz.max_residues={s7['boltz']['max_residues']}"}
            continue
        sdir = run_dir / "data" / "s7" / _safe(pid)
        try:
            fr = folder.fold(pid, seq, sdir)
        except (ToolUnavailable, ToolFailed) as e:
            structures[pid] = {"status": "failed", "reason": str(e)}
            continue
        try:
            ca = parse_structure_ca(fr.pdb)
            ps = plddt_summary(ca.bfactors)
        except ToolUnavailable as e:
            structures[pid] = {"status": "failed", "reason": str(e)}
            continue
        conf = (fr.meta or {}).get("confidence", {})
        entry: dict[str, Any] = {
            "status": "ran", "engine": folder.name,
            "model_file": art.run_file_ref(run_dir, fr.pdb),
            "reported_confidence": {k: conf.get(k) for k in ("confidence_score", "ptm", "complex_plddt")} if conf else None,
            **{k: round(v, 4) if isinstance(v, float) else v for k, v in ps.items()},
            "sequence_matches_input": ca.sequence == seq, "n_residues_modelled": len(ca.coords),
        }
        if getattr(folder, "sends_sequence_off_machine", False):
            used_external.add("ColabFold MSA server")
        # ---- 3. independent refolders -------------------------------------------------------
        validators: dict[str, dict] = {}
        for rname, (runner, why_) in ref_resolved.items():
            if runner is None:
                validators[rname] = {"status": "unavailable", "reason": why_}
                continue
            try:
                rr = runner.fold(pid, seq, sdir)
            except (ToolUnavailable, ToolFailed) as e:
                validators[rname] = {"status": "failed", "reason": str(e)}
                continue
            if getattr(runner, "sends_sequence_off_machine", False):
                used_external.add("ESM Atlas API")
            rc = parse_structure_ca(rr.pdb)
            if rc.sequence != ca.sequence or len(rc.coords) != len(ca.coords):
                validators[rname] = {"status": "skipped", "reason": "refolded sequence/length differs from the Boltz-2 model; no 1:1 comparison"}
                continue
            validators[rname] = {
                "status": "ran", "tool": runner.name, "version": getattr(runner, "version", None),
                "model_file": art.run_file_ref(run_dir, rr.pdb),
                "lddt_ca_vs_boltz": round(lddt_ca(rc.coords, ca.coords), 4),
                "tm_score_vs_boltz": round(tm_score(rc.coords, ca.coords), 4),
                "ca_rmsd_vs_boltz": round(rmsd_after_superposition(rc.coords, ca.coords), 3),
                "mean_plddt": round(plddt_summary(rc.bfactors)["mean_plddt"], 2),
            }
        ran = {k: v for k, v in validators.items() if v["status"] == "ran"}
        agreement = (sum(v["lddt_ca_vs_boltz"] for v in ran.values()) / len(ran)) if ran else None
        spread = (max(v["lddt_ca_vs_boltz"] for v in ran.values()) - min(v["lddt_ca_vs_boltz"] for v in ran.values())) if len(ran) >= 2 else None
        entry["validators"] = validators
        entry["validator_agreement"] = {
            "metric": "mean lDDT-C-alpha of each refolder vs the Boltz-2 model", "value": None if agreement is None else round(agreement, 4),
            "n_validators": len(ran), "spread": None if spread is None else round(spread, 4),
            "note": ("single validator only" if len(ran) == 1 else None if len(ran) >= 2 else "no validator ran")}
        plddt_unit = entry["mean_plddt"] / 100.0
        entry["structure_term"] = round(0.5 * plddt_unit + 0.5 * agreement, 4) if agreement is not None else round(plddt_unit, 4)
        entry["structure_term_basis"] = "0.5*mean_pLDDT/100 + 0.5*validator_agreement" if agreement is not None else "mean_pLDDT/100 (no validator agreement)"
        # ---- 4. DiscoTope-3.0 on the Boltz-2 model -----------------------------------------
        if dt is not None and s7["conformational_on_boltz"]:
            try:
                scale = 100.0 if ps["scale_detected"].startswith("0-1") else 1.0
                use = rewrite_bfactors(fr.pdb, sdir / "boltz2_af_bfactor.pdb", scale) if scale != 1.0 else fr.pdb
                resid = dt.predict(use, struc_type="alphafold")
                thr = cfg["s5"]["conformational"]["score_threshold"]
                summ = discotope_summary(resid, thr)
                epi_ids = set(summ.pop("epitope_residues"))
                pl = {rid: b * (100.0 if scale != 1.0 else 1.0) for rid, b in zip(ca.resids, ca.bfactors)}
                floor = float(s7["plddt_floor_for_epitope_confidence"])
                low = [r for r in epi_ids if pl.get(r, 100.0) < floor]
                entry["conformational_epitopes"] = {"status": "ran", "tool": "DiscoTope-3.0", **summ,
                                                    "plddt_floor": floor, "n_epitope_residues_below_floor": len(low),
                                                    "fraction_epitope_residues_below_floor": (len(low) / len(epi_ids)) if epi_ids else None}
            except (ToolUnavailable, ToolFailed) as e:
                entry["conformational_epitopes"] = {"status": "failed", "reason": str(e)}
        else:
            entry["conformational_epitopes"] = {"status": "unavailable" if dt is None else "disabled"}
        structures[pid] = entry

    if used_external:
        warnings.append("candidate sequences were sent to third-party servers: " + ", ".join(sorted(used_external)))
    for n, (runner, _) in ref_resolved.items():
        if runner is not None and any(isinstance(v, dict) and v.get("validators", {}).get(n, {}).get("status") == "ran" for v in structures.values()):
            tools.append(tool_record(runner.name, version=getattr(runner, "version", None), backend="local" if not getattr(runner, "sends_sequence_off_machine", False) else "api",
                                     fidelity=runner.fidelity, role="refolder (validator)",
                                     sends_sequence_off_machine=bool(getattr(runner, "sends_sequence_off_machine", False))))
    if dt is not None and any(v.get("conformational_epitopes", {}).get("status") == "ran" for v in structures.values() if isinstance(v, dict)):
        tools.append(tool_record("DiscoTope-3.0", version=None, backend="local", fidelity="reference", role="conformational epitopes on Boltz-2 model"))

    # ---- 5. final coarse ranking -----------------------------------------------------------
    group = {}
    for c in top:
        t = raw_terms(c)
        st = structures.get(c["protein_id"], {})
        t["structure"] = st.get("structure_term") if st.get("status") == "ran" else None
        group[c["protein_id"]] = t
    final = score_group(group, weights)
    top_sorted = sorted(top, key=lambda c: (final[c["protein_id"]]["score"] is None, -(final[c["protein_id"]]["score"] or 0.0),
                                            -(pre[c["protein_id"]]["score"] or 0.0), c["protein_id"]))
    rest = [c for c in order if c["protein_id"] not in set(top_ids)]
    ranked = top_sorted + rest

    out_cands = []
    for i, c in enumerate(ranked):
        pid = c["protein_id"]
        in_top = pid in set(top_ids)
        rec = {k: v for k, v in c.items()}
        rec.update({
            "rank": i + 1, "in_top_k": in_top, "rank_basis": "structure_informed" if in_top and folder is not None else "pre_structure",
            "scores": {
                "pre_structure": pre[pid]["score"],
                "final": final[pid]["score"] if in_top else None,
                "terms_raw": final[pid]["terms_raw"] if in_top else pre[pid]["terms_raw"],
                "terms_percentile": final[pid]["terms_percentile"] if in_top else pre[pid]["terms_percentile"],
                "terms_used": final[pid]["terms_used"] if in_top else pre[pid]["terms_used"],
                "terms_missing": final[pid]["terms_missing"] if in_top else pre[pid]["terms_missing"],
                "weights": weights if in_top else pre_w,
                "percentile_basis": f"within the {len(top)} top-k candidates" if in_top else f"within all {len(cands)} candidates",
            },
            "structure": structures.get(pid, {"status": "not_selected", "reason": f"outside the top {top_k}"}),
        })
        out_cands.append(rec)

    payload = {
        "context": s6["payload"]["context"],
        "top_k": top_k,
        "selection": {"basis": "pre-structure composite of S3, S5 T-cell, S5 B-cell, S6 coverage terms (percentile-ranked)",
                      "pre_structure_weights": pre_w, "top_k_ids": top_ids},
        "analysis": {
            "folding": fold_status,
            "validators": ref_status,
            "degraded_to_sequence_only": folder is None,
            "affinity": {"status": "not_implemented",
                         "reason": "Boltz-2's affinity head supports protein-small-molecule binding only; no validated affinity target exists for an antigen monomer"},
            "docking": {"status": "not_implemented", "reason": "ClusPro-TLR docking is deliberately absent (README sections 4 and 7)"},
            "validator_policy": "report every validator used; spread across validators is part of the result",
        },
        "final_candidates": [c["protein_id"] for c in top_sorted],
        "candidates": out_cands,
        "epitope_set": s6["payload"]["epitope_set"],
        "set_level_coverage": s6["payload"]["set_level"],
        "upstream": {"s6_analysis": s6["payload"]["analysis"], "s5_status": s6["payload"]["analysis_status_s5"],
                     **s6["payload"]["upstream"]},
        "limits": LIMITS,
    }
    env = art.build_envelope(stage=STAGE, version=VERSION, run_id=run_dir.name, inputs=inputs, params=params, tools=tools,
                             warnings=warnings, payload=payload)
    return art.write_stage(target, env)


def _safe(pid: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9._-]", "_", pid)


def main(argv=None) -> int:
    def extra(ap):
        ap.add_argument("--s6-path", dest="s6_path", type=Path, default=None)
    return stage_main(STAGE, run, argv, extra)


if __name__ == "__main__":
    raise SystemExit(main())
