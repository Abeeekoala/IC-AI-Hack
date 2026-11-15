"""Stream C reads stream B's S3-S7 artifacts (docs/CONTRACT_S3_S7.md): the field mapping, the statuses, the S10 rows, the connection to A's CLI.

SYNTHETIC ARTIFACTS. B's real predictors (Vaxign-ML, NetMHCpan, BepiPred, DiscoTope, Boltz-2, ...) are not installed here, so the S3-S7
artifacts below are written by `write_synthetic_chain` through `RunStore.write_artifact`, with payloads that follow B's contract section
by section (shapes derived from B's code and tests, never by importing B's modules). Every number is deliberately fake (0.111, 0.222,
11.1 ...): nothing in them is a prediction, and the peptides are just stretches of the real fixture sequences so that positions stay
consistent. The one test that runs B's REAL stages (`test_real_degraded_...`) uses what B's code actually writes on a machine without
any of its tools, and is skipped where a tool is installed.

Everything works on a COPY of `fixtures/nmb_fixture` under `tmp_path`; tests that simulate are `needs_ot` (about 25 s each).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from b_tools_guard import needs_toolless_machine

from vaxforge import s8_protocol, s9_robot, s9plus_animate, s10_animal
from vaxforge.artifacts import ContractError, RunStore
from vaxforge.config import Config
from vaxforge.fasta import read_fasta
from vaxforge.s9_llm import LLMBackend, TemplateBackend
from vaxforge.s9_validate import find_ot_python
from vaxforge.s10_animal import ArmRole
from vaxforge.streamc_adapter import (
    STAND_IN_WARNING,
    NoCandidatesError,
    ProteinSelectionError,
    UpstreamNotUsable,
    _candidates_from_upstream,
    load_inputs_from_store,
)
from vaxforge.streamc_models import RANK_SOURCE_S7_FINAL, RANK_SOURCE_USER_SELECTION

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
needs_ot = pytest.mark.skipif(find_ot_python() is None, reason="no Opentrons 7.5.0 interpreter (set VAXFORGE_OT_PYTHON)")
# `needs_toolless_machine` (b_tools_guard.py, shared with test_s8_order_fasta.py) skips only when a stream B tool is USABLE here: a `docker`
# executable whose daemon is stopped does not count.

FHBP, NADA, NHBA, PORA = "sp|Q9JXV4|FHBP_NEIMB", "sp|Q9JXK7|NADA1_NEIMB", "sp|Q7DD37|NHBA_NEIMB", "sp|P0DH58|OMPA_NEIMB"
SEQ = {r.id: r.seq for r in read_fasta(FIXTURE / "data" / "proteome.faa")}
HLA_STUDY = dict(species="mouse_hla_transgenic", goal="hla_epitope_validation", hla_transgenic_lines=["A2.DR1"])  # T-cell / S6 rows become testable


def cfg(**streamc) -> Config:
    return Config(dict(streamc=streamc))


def copy_run(dest: Path) -> RunStore:
    shutil.copytree(FIXTURE, dest)
    return RunStore(dest, cache_dir=dest.parent / "cache")


def vaxforge(*args):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    p = subprocess.run([sys.executable, "-m", "vaxforge", *map(str, args)], capture_output=True, text=True, cwd=ROOT, env=env, timeout=600)
    return p.returncode, p.stdout, p.stderr


# =========================================================================== the synthetic S3-S7 chain (B's contract, sections 3-7)


def pep(pid: str, start: int, end: int) -> str:
    """1-based inclusive stretch of the real fixture sequence (positions and peptides stay consistent)."""
    return SEQ[pid][start - 1:end]


# per candidate: T-cell epitopes (class, start, end, alleles), linear B-cell regions (start, end, mean_score), conformational segments,
# S6 coverage by population (fractions), S7 structure
T_CELL = {
    FHBP: [("I", 10, 18, ["HLA-A*02:01", "HLA-A*24:02"]), ("I", 40, 48, ["HLA-B*07:02"]), ("II", 60, 74, ["HLA-DRB1*01:01", "HLA-DQA1*05:01/DQB1*02:01"])],
    NADA: [("I", 5, 13, ["HLA-A*02:01"]), ("I", 30, 38, ["HLA-A*03:01"])],
}
B_LINEAR = {FHBP: [(20, 34, 0.222), (100, 110, 0.111)]}  # NADA and NHBA: BepiPred ran and found no region
SEGMENTS = {FHBP: [[15, 20], [50, 51]]}
COVERAGE = {FHBP: {"World": 0.111, "Europe": 0.222, "East Asia": 0.333}, NADA: {"World": 0.444, "Europe": 0.555}}
PROTEGENICITY = {FHBP: 0.111, NADA: 0.222, NHBA: 0.333, PORA: 0.444}  # fake; S3 ranks them FHBP, NADA, NHBA, PORA (this order)
S3_ORDER = [FHBP, NADA, NHBA, PORA]
GATES = ("human_homology", "allergenicity", "toxicity", "conservancy")
SYNTHETIC_TOOLS = {"synthetic-b-fixture": "tests/wetlab/test_streamc_upstream.py"}


def _row(store: RunStore, pid: str) -> dict:
    return next(p for p in store.read_artifact("s2").payload["proteins"] if p["protein_id"] == pid)


def set_unknown_localisation(store: RunStore, pid: str) -> None:
    """Rewrite S2 so that `pid` has localisation Unknown (the real fixture has 21 such proteins; this picks the candidate)."""
    a = store.read_artifact("s2")
    payload = json.loads(json.dumps(a.payload))
    row = next(p for p in payload["proteins"] if p["protein_id"] == pid)
    row["localisation"].update(label="Unknown", accessibility="unknown")
    store.write_artifact("s2", payload, params=a.params, inputs=list(a.inputs), tools=a.producer["tools"], status="ok", warnings=a.warnings)


def write_synthetic_chain(store: RunStore, *, final=(NADA, FHBP, NHBA), backend="vaxign_ml", epitopes=True, coverage=True, structure="ran",
                          s4_incomplete=False, s7_status="ok", s7_warnings=(), seq_matches=True, ghost=None) -> SimpleNamespace:
    """S3 -> S7 written the way B's stages write through RunStore (status `ok`, DEFAULT_INPUTS chain, tools as name -> version).

    `structure`: "ran" (FHBP and NADA folded, NHBA failed), "degraded" (no Boltz-2: S7 degraded to sequence-only).
    `seq_matches`: False makes FHBP's structure say its sequence differs from the input. `ghost`: a protein id added to `final_candidates` only.
    The S7 final order is `final`; the S3 order is S3_ORDER, so a mixed-up rank would show. S7's `terms_raw.s3` is S3's COMPOSITE score
    (0.999 ...) and differs from the protegenicity on purpose, as in B's code."""
    fake = backend == "vaxign_ml"
    ranker = {"backend": backend, "weights_used": {"protegenicity": 1.0, "localisation": 0.0, "virulence": 0.0} if fake else {"localisation": 0.6, "virulence": 0.4},
              "score_semantics": ("protegenicity in [0,1] = Vaxign-ML percentile/100 (see tool record)" if fake
                                  else "composite of S2 localisation prior and VFDB evidence; NOT a protegenicity score"),
              "thresholds": [0.9, 0.85], "note": "SYNTHETIC: values are fake"}
    leak = ({"model": "vaxign-ml pretrained (trained on all of Protegen)", "holdout_enforced": False, "declared_holdout_taxids": None}
            if fake else {"model": "none (rule-based, nothing trained)", "holdout_enforced": True})
    leak = {**leak, "note": "SYNTHETIC", "target_taxid": 487, "requested_holdout": {"taxids": [], "note": None}}
    ctx = {"taxid": 487, "organism_type": "gram-", "s2_meta": {"synthetic": True}, "n_proteins": 40}

    # ---- S3 (section 3)
    s3_c = []
    for rank, pid in enumerate(S3_ORDER, start=1):
        row = _row(store, pid)
        prot = PROTEGENICITY[pid] if fake else None
        s3_c.append({"protein_id": pid, "name": row["description"], "gene": row["gene"], "length": len(SEQ[pid]), "rank": rank,
                     "composite_score": 0.999 - 0.1 * rank, "protegenicity": prot, "protegenicity_raw": None if prot is None else prot * 100,
                     "passes_thresholds": {"0.90": None if prot is None else prot >= 0.9, "0.85": None if prot is None else prot >= 0.85},
                     "terms": {"protegenicity": prot, "localisation": 1.0, "virulence": 0.0},
                     "evidence": {"localisation": {"label": row["localisation"]["label"], "is_unknown": row["localisation"]["label"] == "Unknown"}, "virulence": None},
                     "selected": True, "selected_reason": "synthetic", "sequence": SEQ[pid], "taxid": 487, "accession": row["accession"]})
    s3 = store.write_artifact("s3", {
        "context": ctx, "ranker": ranker, "leakage_audit": leak,
        "counts": {"n_proteins": 40, "n_scored": len(s3_c) if fake else 0, "n_unscored": 40 - (len(s3_c) if fake else 0), "n_selected": len(s3_c),
                   "threshold_summary": {}, "fold_enrichment": None, "fold_enrichment_note": "SYNTHETIC"},
        "candidates": s3_c, "ranking": [{k: v for k, v in c.items() if k not in ("sequence", "taxid", "accession")} for c in s3_c]},
        params={}, tools=SYNTHETIC_TOOLS, status="ok", warnings=["SYNTHETIC S3 artifact: every value is fake"])

    # ---- S4 (section 4)
    unavailable = list(GATES) if s4_incomplete else []
    s4_c = []
    for c in s3_c:
        e = {k: v for k, v in c.items() if k not in ("rank", "composite_score", "protegenicity", "protegenicity_raw", "passes_thresholds", "terms", "evidence",
                                                       "selected", "selected_reason")}
        e["s3"] = {k: c.get(k) for k in ("rank", "composite_score", "protegenicity", "passes_thresholds", "terms", "evidence")}
        e.update(gates={} if s4_incomplete else {g: {"status": "pass", "reason": "synthetic", "metrics": {}} for g in GATES},
                 gates_unavailable=list(unavailable), flags=[])
        s4_c.append(e)
    s4 = store.write_artifact("s4", {
        "context": ctx, "gates": {g: ({"status": "unavailable", "reason": "SYNTHETIC: tool not installed"} if s4_incomplete else {"status": "ran", "thresholds": {}}) for g in GATES},
        "incomplete_screening": s4_incomplete, "unavailable_gates": unavailable, "funnel": [{"gate": "input", "n": len(s4_c)}],
        "counts": {"n_input": len(s4_c), "n_survivors": len(s4_c), "n_rejected": 0, "n_rescued": 0}, "candidates": s4_c, "rejected": [],
        "recovery": "SYNTHETIC", "upstream": {"s3_ranker": ranker, "s3_leakage_audit": leak}, "refdata_provenance": {}},
        params={}, tools=SYNTHETIC_TOOLS, status="ok", warnings=[f"INCOMPLETE SCREENING: '{g}' was not applied" for g in unavailable])

    # ---- S5 (section 5): sidecar per candidate, selected epitopes, linear regions, conformational status
    status5 = {name: ({"status": "ran", "tool": "synthetic", "fidelity": "alternative"} if epitopes else {"status": "unavailable", "reason": "SYNTHETIC: no predictor installed"})
               for name in ("class_i", "class_ii", "bcell_linear", "conformational")}
    s5_c, t_set, b_set = [], [], []
    for c in s4_c:
        pid = c["protein_id"]
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", pid)
        side = store.data_path("s5", f"{safe}_tcell.tsv")
        side.write_text("SYNTHETIC placeholder, not an S5 epitope table\n", encoding="utf-8")
        mine = [(cls, a, b, al) for cls, a, b, al in T_CELL.get(pid, [])] if epitopes else []
        ids = {cls: [] for cls in ("I", "II")}
        for cls, a, b, al in mine:
            eid = f"{pid}|{cls}|{pep(pid, a, b)[:9]}"
            ids[cls].append(eid)
            t_set.append({"epitope_id": eid, "protein_id": pid, "class": cls, "peptide": pep(pid, a, b), "core": pep(pid, a, b)[:9], "start": a, "end": b,
                          "alleles": al, "n_alleles": len(al), "best_rank": 0.123})
        lin = ({"n_residues": len(SEQ[pid]), "n_chunks": 1, "chunked": False, "threshold": 0.1512, "top20pct_mean_score": 0.123,
                "n_regions": len(B_LINEAR.get(pid, [])),
                "regions": [{"start": a, "end": b, "peptide": pep(pid, a, b), "mean_score": m, "max_score": m + 0.1} for a, b, m in B_LINEAR.get(pid, [])]}
               if epitopes else {"status": "unavailable"})
        b_set += [{"protein_id": pid, **r} for r in lin.get("regions", [])]
        s5_c.append({
            "protein_id": pid, "accession": c["accession"], "name": c["name"], "gene": c["gene"], "length": c["length"], "sequence": c["sequence"], "taxid": 487,
            "upstream": {"s3": c["s3"], "s4": {"gates": c["gates"], "flags": [], "gates_unavailable": c["gates_unavailable"], "rescued_despite": None}},
            "tcell": {"score": 0.123 if epitopes else None, "score_basis": "class_i_only" if epitopes else "unavailable", "all_epitopes_file": store.run_ref(side),
                      "class_i": {"n_epitopes": len(ids["I"]), "n_promiscuous": 0, "score": 0.1, "top": [], "selected": ids["I"]},
                      "class_ii": {"n_epitopes": len(ids["II"]), "n_promiscuous": 0, "score": None, "top": [], "selected": ids["II"]}},
            "bcell": {"linear": lin, "linear_score": 0.123 if epitopes else None, "conformational": {"status": "unavailable"}}})
    up5 = {"s4_incomplete_screening": s4_incomplete, "s4_unavailable_gates": unavailable, "s3_ranker": ranker, "s3_leakage_audit": leak}
    eset = {"t_cell": t_set, "b_cell_linear": b_set, "note": "SYNTHETIC"}
    s5 = store.write_artifact("s5", {
        "context": ctx, "analysis_status": status5, "skipped_candidates": [], "upstream": up5, "candidates": s5_c, "epitope_set": eset, "limits": ["SYNTHETIC"]},
        params={}, tools=SYNTHETIC_TOOLS, status="ok",
        warnings=[] if epitopes else ["class I T-cell prediction did not run: SYNTHETIC: no predictor installed"])

    # ---- S6 (section 6)
    why6 = "SYNTHETIC: S5 produced no selected T-cell epitopes" if not (epitopes and coverage) else None
    analysis6 = ({"status": "ran", "table": {"path": "<synthetic>"}, "modes": ["class_i", "class_ii", "combined"], "populations": ["World", "Europe", "East Asia"],
                  "score_population": "World", "independence_assumption": "loci treated as independent"}
                 if why6 is None else {"status": "unavailable", "reason": why6})
    xcheck = store.data_path("s6", "iedb_epitope_allele.tsv")
    xcheck.write_text("SYNTHETIC placeholder\n", encoding="utf-8")
    set_level: dict = {"n_epitopes": len(t_set)}
    if why6 is None:
        set_level.update(by_population={}, fairness={"available": False, "reason": "SYNTHETIC"},
                         iedb_crosscheck_file={"file": store.run_ref(xcheck), "use": "SYNTHETIC"})
    s6_c = []
    for c in s5_c:
        pid = c["protein_id"]
        slim = {k: v for k, v in c.items()}
        if why6 is not None or pid not in COVERAGE:
            slim["coverage"] = {"score": None, "reason": why6 or "no selected T-cell epitopes for this candidate", "n_selected_epitopes": 0}
        else:
            brief = {"population": "World", "mode": "combined", "coverage": COVERAGE[pid]["World"], "average_hits": 1.11, "pc90": 1.11, "pc90_integer": 1,
                     "min_locus_frequency_mass": 0.9, "n_epitopes": 3, "n_epitopes_covered_by_table": 3, "unmapped_alleles": [], "approximations": [], "sample_size": None}
            slim["coverage"] = {"score": COVERAGE[pid]["World"], "score_population": "World", "mode_used": "combined", "n_selected_epitopes": 3,
                                "by_mode_at_score_population": {"class_i": {**brief, "mode": "class_i", "pc90": 7.77}, "class_ii": {**brief, "mode": "class_ii", "pc90": 8.88},
                                                                "combined": brief},
                                "by_population": dict(COVERAGE[pid]), "min_population": min(COVERAGE[pid], key=COVERAGE[pid].get)}
        s6_c.append(slim)
    s6 = store.write_artifact("s6", {
        "context": ctx, "analysis": analysis6, "set_level": set_level, "candidates": s6_c, "epitope_set": eset, "analysis_status_s5": status5, "upstream": up5,
        "limits": ["SYNTHETIC"]}, params={}, tools=SYNTHETIC_TOOLS, status="ok",
        warnings=[] if why6 is None else [f"population coverage did not run: {why6}"])

    # ---- S7 (section 7): final order, scores, structure
    degraded = structure == "degraded"
    ranked = [*final, *[p for p in S3_ORDER if p not in final]]
    by6 = {c["protein_id"]: c for c in s6_c}
    reason = "SYNTHETIC: boltz not found on PATH"
    validators_unavailable = {n: {"status": "unavailable", "reason": "SYNTHETIC: not installed"} for n in ("esmfold", "af2-no-msa")}
    s7_c = []
    for i, pid in enumerate(ranked):
        c = dict(by6[pid])
        in_top = pid in final
        if not in_top:
            st = {"status": "not_selected", "reason": f"outside the top {len(final)}"}
        elif degraded:
            st = {"status": "unavailable", "reason": reason}
        elif pid == NHBA:
            st = {"status": "failed", "reason": "SYNTHETIC: Boltz-2 crashed"}
        else:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", pid)
            model = store.data_path("s7", safe, "boltz2.pdb")
            model.write_text("REMARK SYNTHETIC placeholder, not a structure\n", encoding="utf-8")
            both = pid == FHBP
            vals = {"esmfold": {"status": "ran", "tool": "esmfold", "version": None, "model_file": store.run_ref(model), "lddt_ca_vs_boltz": 0.111,
                                "tm_score_vs_boltz": 0.222, "ca_rmsd_vs_boltz": 3.33, "mean_plddt": 44.4},
                    "af2-no-msa": ({"status": "ran", "tool": "colabfold", "version": None, "model_file": store.run_ref(model), "lddt_ca_vs_boltz": 0.555,
                                    "tm_score_vs_boltz": 0.666, "ca_rmsd_vs_boltz": 7.77, "mean_plddt": 55.5} if both
                                   else {"status": "unavailable", "reason": "SYNTHETIC: colabfold_batch not found"})}
            conf = ({"status": "ran", "tool": "DiscoTope-3.0", "n_residues": len(SEQ[pid]), "n_epitope_residues": 7, "epitope_fraction": 0.011, "mean_score": 0.123,
                     "epitope_segments": SEGMENTS[pid], "threshold_source": "tool_boolean", "mean_plddt_input": 11.1, "plddt_floor": 70.0,
                     "n_epitope_residues_below_floor": 7, "fraction_epitope_residues_below_floor": 1.0} if pid in SEGMENTS else {"status": "unavailable"})
            st = {"status": "ran", "engine": "Boltz-2", "model_file": store.run_ref(model),
                  "reported_confidence": {"confidence_score": 0.111, "ptm": 0.222, "complex_plddt": 0.333}, "mean_plddt": 11.1 if both else 22.2,
                  "frac_very_high_gt90": 0.0, "frac_confident_70_90": 0.0, "frac_low_50_70": 0.0, "frac_very_low_le50": 1.0, "scale_detected": "0-100",
                  "sequence_matches_input": seq_matches or not both, "n_residues_modelled": len(SEQ[pid]), "validators": vals,
                  "validator_agreement": {"metric": "mean lDDT-C-alpha of each refolder vs the Boltz-2 model", "value": 0.333 if both else 0.111,
                                          "n_validators": 2 if both else 1, "spread": 0.444 if both else None, "note": None if both else "single validator only"},
                  "structure_term": 0.111, "structure_term_basis": "SYNTHETIC", "conformational_epitopes": conf}
        c.update(rank=i + 1, in_top_k=in_top, rank_basis="pre_structure" if degraded or not in_top else "structure_informed",
                 scores={"pre_structure": 0.5, "final": 0.5 if in_top else None,
                         "terms_raw": {"s3": 0.999 - 0.1 * (S3_ORDER.index(pid) + 1), "tcell": 0.123, "bcell": 0.123, "coverage": 0.222, "structure": 0.111},
                         "terms_percentile": {}, "terms_used": ["s3"], "terms_missing": [], "weights": {"s3": 0.4}, "percentile_basis": "SYNTHETIC"},
                 structure=st)
        s7_c.append(c)
    s7_analysis = {"folding": {"status": "unavailable", "reason": reason} if degraded else {"status": "ran", "tool": "Boltz-2", "version": "SYNTHETIC", "msa_mode": "empty",
                                                                                          "accelerator": "cpu", "seed_controlled": False},
                   "validators": validators_unavailable if degraded else {n: {"status": "ready"} for n in validators_unavailable},
                   "degraded_to_sequence_only": degraded, "affinity": {"status": "not_implemented", "reason": "SYNTHETIC"},
                   "docking": {"status": "not_implemented", "reason": "SYNTHETIC"}, "validator_policy": "SYNTHETIC"}
    s7_payload = {
        "context": ctx, "top_k": len(final), "selection": {"basis": "SYNTHETIC", "pre_structure_weights": {}, "top_k_ids": list(final)}, "analysis": s7_analysis,
        "final_candidates": [*final, *([ghost] if ghost else [])], "candidates": s7_c, "epitope_set": eset, "set_level_coverage": set_level,
        "upstream": {"s6_analysis": analysis6, "s5_status": status5, **up5}, "limits": ["SYNTHETIC"]}
    s7 = store.write_artifact("s7", s7_payload, params={}, tools=SYNTHETIC_TOOLS, status=s7_status,
                              warnings=list(s7_warnings) or ([f"DEGRADED: Boltz-2 did not run ({reason})"] if degraded else []))
    return SimpleNamespace(s3=s3, s4=s4, s5=s5, s6=s6, s7=s7, payload=s7_payload)


@pytest.fixture
def run(tmp_path):
    """A COPY of the S0-S2 fixture with the full synthetic S3-S7 chain on it (S7 order: NadA, fHbp, NHBA)."""
    store = copy_run(tmp_path / "run")
    return SimpleNamespace(store=store, b=write_synthetic_chain(store))


def fresh(tmp_path, name="run2", **kw) -> SimpleNamespace:
    store = copy_run(tmp_path / name)
    return SimpleNamespace(store=store, b=write_synthetic_chain(store, **kw))


def by_id(inputs) -> dict:
    return {c.candidate_id: c for c in inputs.candidates}


def row(art, cid, stage):
    return next(r for r in art.payload["design"]["falsification_map"] if r["candidate_id"] == cid and r["upstream_stage"] == stage)


# =========================================================================== the synthetic chain is a valid run


def test_the_synthetic_chain_is_a_valid_run_with_s3_to_s7_present(run):
    st = run.store
    assert st.stages_present() == ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7"] and st.verify() == []
    assert all(a.status == "ok" for a in (run.b.s3, run.b.s4, run.b.s5, run.b.s6, run.b.s7))  # B writes `ok` whatever ran: the payload holds the truth
    assert run.b.s7.inputs == dict(s4=st.digest_of("s4"))  # B's S7 records s4 (A's DEFAULT_INPUTS), not s6 which it reads: reported to stream B


# =========================================================================== the mapping, field by field


def test_rank_is_the_position_in_final_candidates_not_the_s3_rank_and_the_join_to_s2_is_by_protein_id(run):
    inputs = load_inputs_from_store(run.store, cfg())
    assert [c.candidate_id for c in inputs.candidates] == [NADA, FHBP, NHBA]  # S3's own order is fHbp, NadA, NHBA, PorA
    assert [c.rank for c in inputs.candidates] == [1, 2, 3]
    assert {c.rank_source for c in inputs.candidates} == {RANK_SOURCE_S7_FINAL} == {inputs.rank_source}
    assert PORA not in by_id(inputs)  # in S7's candidates list, but not in final_candidates
    assert inputs.stages == ["s0", "s2", "s7"] and inputs.sources["pathogen"] == "s0.payload.taxon"
    for c in inputs.candidates:
        r = next(p for p in run.store.read_artifact("s2").payload["proteins"] if p["protein_id"] == c.candidate_id)
        assert c.sequence == SEQ[c.candidate_id] and c.length_aa == len(c.sequence) == r["length"]
        assert c.localisation == r["localisation"]["label"]
    assert "Factor H binding protein" in by_id(inputs)[FHBP].protein_name  # readable, as on the stand-in route
    assert inputs.selection == dict(source="s7_final_ranking", stages=["s2", "s7"], n_final_candidates=3, s7_top_k=3, degraded_to_sequence_only=False,
                                    rank_source=RANK_SOURCE_S7_FINAL, used=[NADA, FHBP, NHBA], top_k=3)


def test_localisation_unknown_stays_a_value_and_comes_from_s2(tmp_path):
    store = copy_run(tmp_path / "run")
    set_unknown_localisation(store, FHBP)
    write_synthetic_chain(store)
    c = by_id(load_inputs_from_store(store, cfg()))[FHBP]
    assert c.localisation == "Unknown"  # not dropped, not "not surface"
    assert by_id(load_inputs_from_store(store, cfg()))[NADA].localisation == "OuterMembrane"
    assert any(f"{FHBP}: S2 localisation is Unknown (accessibility unknown)" in w for w in load_inputs_from_store(store, cfg()).warnings)


def test_protegenicity_is_the_s3_protegenicity_not_the_composite_score_that_terms_raw_s3_holds(run):
    cands = by_id(load_inputs_from_store(run.store, cfg()))
    raw = {r["protein_id"]: r["scores"]["terms_raw"]["s3"] for r in run.b.payload["candidates"]}
    for pid in (NADA, FHBP, NHBA):
        assert cands[pid].protegenicity == PROTEGENICITY[pid] != raw[pid]  # B's section 8 points at terms_raw.s3: in B's code that is `composite_score`
    assert "Vaxign-ML percentile/100" in cands[FHBP].units["protegenicity"] and "vaxign_ml" in cands[FHBP].units["protegenicity"]


def test_t_cell_epitopes_are_grouped_by_protein_with_kind_alleles_and_positions(run):
    cands = by_id(load_inputs_from_store(run.store, cfg()))
    f = [e for e in cands[FHBP].epitopes if e.kind.startswith("T_")]
    assert [(e.kind, e.start, e.end, e.peptide) for e in f] == [
        ("T_class_I", 10, 18, pep(FHBP, 10, 18)), ("T_class_I", 40, 48, pep(FHBP, 40, 48)), ("T_class_II", 60, 74, pep(FHBP, 60, 74))]
    assert f[0].hla == ["HLA-A*02:01", "HLA-A*24:02"] and f[2].hla == ["HLA-DRB1*01:01", "HLA-DQA1*05:01/DQB1*02:01"]
    assert all(e.score is None for e in f)  # B gives a %Rank (lower is better); one `score` field cannot hold it beside BepiPred's
    n = [e for e in cands[NADA].epitopes if e.kind.startswith("T_")]
    assert [(e.kind, e.start, e.end) for e in n] == [("T_class_I", 5, 13), ("T_class_I", 30, 38)] and n[1].hla == ["HLA-A*03:01"]
    assert cands[NHBA].epitopes == []  # nothing selected for it upstream: nothing invented


def test_linear_b_cell_regions_become_b_linear_with_their_positions_and_mean_score(run):
    cands = by_id(load_inputs_from_store(run.store, cfg()))
    lin = [e for e in cands[FHBP].epitopes if e.kind == "B_linear"]
    assert [(e.start, e.end, e.peptide, e.score) for e in lin] == [(20, 34, pep(FHBP, 20, 34), 0.222), (100, 110, pep(FHBP, 100, 110), 0.111)]
    assert not [e for e in cands[NADA].epitopes + cands[NHBA].epitopes if e.kind == "B_linear"]  # BepiPred ran and called no region


def test_conformational_epitopes_come_from_structure_only_when_it_ran_and_the_sequence_matches(run, tmp_path):
    f = by_id(load_inputs_from_store(run.store, cfg()))[FHBP]
    conf = [e for e in f.epitopes if e.kind == "B_conformational"]
    assert [(e.start, e.end, e.peptide, e.score) for e in conf] == [(15, 20, pep(FHBP, 15, 20), None), (50, 51, pep(FHBP, 50, 51), None)]
    assert any("DiscoTope-3.0 residue numbers are 1-based sequence positions" in w for w in load_inputs_from_store(run.store, cfg()).warnings)
    assert not [e for e in by_id(load_inputs_from_store(run.store, cfg()))[NADA].epitopes if e.kind == "B_conformational"]  # status "unavailable"
    odd = fresh(tmp_path, seq_matches=False)  # the structure's sequence differs from the input: residue numbers cannot be mapped
    inputs = load_inputs_from_store(odd.store, cfg())
    assert not [e for e in by_id(inputs)[FHBP].epitopes if e.kind == "B_conformational"]
    assert any(f"{FHBP}: conformational epitopes were not carried" in w for w in inputs.warnings)


def test_coverage_is_carried_as_b_wrote_it_fractions_and_the_combined_pc90_with_the_unit_stated(run):
    cands = by_id(load_inputs_from_store(run.store, cfg()))
    f, n, h = cands[FHBP], cands[NADA], cands[NHBA]
    assert f.coverage_by_population == {"World": 0.111, "Europe": 0.222, "East Asia": 0.333}  # fractions in [0, 1], NOT converted
    assert n.coverage_by_population == {"World": 0.444, "Europe": 0.555}
    assert f.pc90 == n.pc90 == 1.11  # `combined` (mode_used), not class_i (7.77) or class_ii (8.88)
    assert "fraction of the population in [0, 1]" in f.units["coverage_by_population"]
    assert "IEDB PC90 for World, combined mode" in f.units["pc90"] and "not a fraction or a percent" in f.units["pc90"]
    assert h.coverage_by_population == {} and h.pc90 is None and "pc90" not in h.units  # S6 found no selected epitope for it


def test_structure_fields_exist_only_when_the_structure_ran_and_name_only_the_validators_that_ran(run):
    cands = by_id(load_inputs_from_store(run.store, cfg()))
    assert (cands[FHBP].structure_confidence, cands[FHBP].structure_validators) == (11.1, ["af2-no-msa", "esmfold"])  # sorted: RunStore sorts JSON keys
    assert (cands[NADA].structure_confidence, cands[NADA].structure_validators) == (22.2, ["esmfold"])  # af2-no-msa was unavailable: not named
    assert (cands[NHBA].structure_confidence, cands[NHBA].structure_validators) == (None, [])  # structure failed
    assert "mean pLDDT of the Boltz-2 model, 0-100" in cands[FHBP].units["structure_confidence"] and "structure_confidence" not in cands[NHBA].units
    assert any(f"{NHBA}: S7 structure status is 'failed' (SYNTHETIC: Boltz-2 crashed)" in w for w in load_inputs_from_store(run.store, cfg()).warnings)


def test_evidence_refs_are_the_digest_of_the_b_artifact_the_candidate_came_from(run):
    for c in load_inputs_from_store(run.store, cfg()).candidates:
        assert c.evidence_refs == [f"s7:{run.b.s7.digest}"]


def test_a_sequence_only_s7_leaves_every_structure_field_empty_but_keeps_what_s5_and_s6_gave(tmp_path):
    inputs = load_inputs_from_store(fresh(tmp_path, structure="degraded").store, cfg())
    for c in inputs.candidates:
        assert c.structure_confidence is None and c.structure_validators == [] and "structure_confidence" not in c.units
        assert not [e for e in c.epitopes if e.kind == "B_conformational"]
    assert by_id(inputs)[FHBP].pc90 == 1.11 and [e for e in by_id(inputs)[FHBP].epitopes if e.kind == "T_class_I"]
    assert inputs.selection["degraded_to_sequence_only"] is True
    assert sum(w.startswith("S7 DEGRADED to sequence-only ranking (SYNTHETIC: boltz not found on PATH)") for w in inputs.warnings) == 1  # once, not per candidate


def test_payload_level_states_that_b_writes_under_status_ok_become_warnings(tmp_path):
    r = fresh(tmp_path, backend="subtractive_filter", s4_incomplete=True, epitopes=False, coverage=False)
    assert r.b.s4.status == r.b.s5.status == r.b.s6.status == r.b.s7.status == "ok"  # nothing ran, and every artifact says ok
    w = " | ".join(load_inputs_from_store(r.store, cfg()).warnings)
    assert "S3 ran its rule-based fallback" in w and "S4 screening was INCOMPLETE: gate(s) human_homology, allergenicity, toxicity, conservancy did not run" in w
    assert "S5 analyses that did not run: " in w and "class_i (unavailable: SYNTHETIC: no predictor installed)" in w and "conformational (unavailable: SYNTHETIC" in w
    assert "S6 population coverage did not run (SYNTHETIC" in w


def test_a_leaky_pretrained_s3_is_flagged_and_a_clean_chain_is_not_flagged_for_s4(run):
    w = load_inputs_from_store(run.store, cfg()).warnings
    assert any("S3 LEAKAGE RISK" in x for x in w) and not any("INCOMPLETE" in x or "DEGRADED" in x or "rule-based fallback" in x for x in w)


def test_top_k_cuts_the_s7_order_and_streamc_proteins_does_not_override_it(run):
    inputs = load_inputs_from_store(run.store, cfg(top_k=2, proteins=["porA"]))
    assert [c.candidate_id for c in inputs.candidates] == [NADA, FHBP]
    assert any(f"only the first 2 are used (not used: {NHBA})" in w for w in inputs.warnings)
    assert any("streamc.proteins (porA) is ignored: S3-S7 artifacts exist" in w for w in inputs.warnings)
    assert STAND_IN_WARNING not in inputs.warnings and inputs.selection["used"] == [NADA, FHBP]


# =========================================================================== S3-S7 that cannot be used: no silent fall-back to the stand-in


@pytest.mark.parametrize("status", ["failed", "needs_user_input"])
def test_an_s7_that_is_not_ok_is_not_replaced_by_the_stand_in_and_carries_b_s_reason(tmp_path, status):
    r = fresh(tmp_path, s7_status=status, s7_warnings=["synthetic S7 failure: boltz crashed"])
    with pytest.raises(UpstreamNotUsable) as exc:
        _candidates_from_upstream(r.store)
    assert exc.value.reason == "upstream_not_ok" and f"s7 artifact has status {status!r}: synthetic S7 failure: boltz crashed" in str(exc.value)
    with pytest.raises(UpstreamNotUsable):  # naming proteins does not bring the stand-in back while S3-S7 exist
        load_inputs_from_store(r.store, cfg(proteins=["fhbp"]))
    for stage in (s8_protocol, s10_animal):
        art = stage.run(r.store, cfg(proteins=["fhbp"]))
        assert art.status == "ok" and art.payload["status"] == "not_run" and art.payload["selection"] == dict(source="none", reason="upstream_not_ok")
        assert art.payload["reasons"][0].startswith(f"s7 artifact has status {status!r}: synthetic S7 failure: boltz crashed")
        assert art.warnings == [f"{stage.STAGE.upper()} not run: {art.payload['reasons'][0]}"] and STAND_IN_WARNING not in art.warnings
        assert art.inputs == dict(s0=r.store.digest_of("s0"), s7=r.store.digest_of("s7"))  # what was read: the S7 that said no
    assert r.store.verify() == []


def test_s3_to_s7_that_stop_before_s7_are_not_replaced_by_the_stand_in(run):
    run.store.artifact_path("s7").unlink()
    with pytest.raises(UpstreamNotUsable, match=r"only partly present \(s3, s4, s5, s6\) and s7 \(the final ranking\) is missing") as exc:
        load_inputs_from_store(run.store, cfg(proteins=["fhbp"]))
    assert exc.value.reason == "upstream_incomplete" and exc.value.stages == ["s0"]
    art = s8_protocol.run(run.store, cfg(proteins=["fhbp"]))
    assert art.status == "ok" and art.payload["status"] == "not_run" and "python -m vaxforge.s7_structure" in art.payload["reasons"][0]
    assert art.inputs == dict(s0=run.store.digest_of("s0"))


def test_an_s7_in_bs_standalone_envelope_is_named_as_such(run):
    run.store.artifact_path("s7").write_text(json.dumps(dict(
        schema="vaxforge.s7_structure/1", stage="s7_structure", artifact_id="sha256:0", run_id="x", inputs=[], params={}, tools=[], warnings=[], payload={})), encoding="utf-8")
    with pytest.raises(UpstreamNotUsable, match="stream B's standalone envelope") as exc:
        load_inputs_from_store(run.store, cfg())
    assert exc.value.reason == "upstream_wrong_envelope" and "manifest.json" in str(exc.value)


def test_an_s7_without_final_candidates_says_so(tmp_path):
    r = fresh(tmp_path, final=())
    with pytest.raises(UpstreamNotUsable, match=r"s7 final_candidates is empty \(4 candidate\(s\)") as exc:
        load_inputs_from_store(r.store, cfg())
    assert exc.value.reason == "upstream_empty" and "s4_filter.json payload.rejected" in str(exc.value)


def test_a_final_candidate_that_s2_does_not_have_is_a_clear_contract_error(run):
    a = run.store.read_artifact("s2")
    payload = json.loads(json.dumps(a.payload))
    payload["proteins"] = [p for p in payload["proteins"] if p["protein_id"] != NADA]
    run.store.write_artifact("s2", payload, params=a.params, inputs=list(a.inputs), tools=a.producer["tools"], status="ok", warnings=a.warnings)
    with pytest.raises(ContractError, match=rf"s7 lists '{re.escape(NADA)}' in final_candidates but s2_annotate.json has no protein with that protein_id") as exc:
        load_inputs_from_store(run.store, cfg())
    assert "different S2 artifact" in str(exc.value) and "Re-run S3-S7" in str(exc.value)
    with pytest.raises(ContractError):  # a defect, not "nothing to do": the stage raises instead of writing a not_run artifact
        s8_protocol.run(run.store, cfg())
    assert not run.store.has("s8")


def test_a_final_candidate_that_s7_does_not_list_in_its_candidates_is_a_contract_error(tmp_path):
    r = fresh(tmp_path, ghost="sp|X00000|GHOST_NEIMB")
    with pytest.raises(ContractError, match=re.escape("lists 'sp|X00000|GHOST_NEIMB' in final_candidates but not in its candidates list")):
        load_inputs_from_store(r.store, cfg())


def test_the_stand_in_is_still_used_when_no_s3_to_s7_artifact_exists(tmp_path):
    store = copy_run(tmp_path / "run")
    assert _candidates_from_upstream(store) is None
    inputs = load_inputs_from_store(store, cfg(proteins=["fhbp"]))
    assert inputs.selection["source"] == "s2_stand_in" and inputs.rank_source == RANK_SOURCE_USER_SELECTION and inputs.warnings[0] == STAND_IN_WARNING
    assert inputs.stages == ["s0", "s2"]
    with pytest.raises(ProteinSelectionError, match="No candidates selected") as exc:
        load_inputs_from_store(store, cfg())
    assert isinstance(exc.value, NoCandidatesError) and "s3_rank" in str(exc.value)


# =========================================================================== the stages on the S7 order


def test_s8_and_s10_record_s7_in_their_inputs_and_say_where_the_candidates_came_from(run):
    c = cfg(top_k=3, study=HLA_STUDY)
    a8, a10 = s8_protocol.run(run.store, c), s10_animal.run(run.store, c)
    want = dict(s0=run.store.digest_of("s0"), s2=run.store.digest_of("s2"), s7=run.b.s7.digest)
    assert a8.status == a10.status == "ok" and "status" not in a8.payload and "status" not in a10.payload  # a normal result
    assert a8.inputs == a10.inputs == want
    assert [(e["candidate_id"], e["rank"], e["rank_source"]) for e in a8.payload["candidates"]] == [
        (NADA, 1, RANK_SOURCE_S7_FINAL), (FHBP, 2, RANK_SOURCE_S7_FINAL), (NHBA, 3, RANK_SOURCE_S7_FINAL)]
    assert a8.payload["selection"] == a10.payload["selection"] and a8.payload["selection"]["source"] == "s7_final_ranking"
    assert a10.payload["candidates_included"] == [NADA, FHBP, NHBA]
    assert "S3 LEAKAGE RISK" in " ".join(a8.warnings) and STAND_IN_WARNING not in a8.warnings + a10.warnings
    assert run.store.verify() == []
    assert s8_protocol.run(run.store, c).digest == a8.digest and s10_animal.run(run.store, c).digest == a10.digest  # re-run: same digests


# ---- the declared inputs are exactly the artifacts that were read
#
# An audit saw S8 and S10 declare [s0, s2, s7] and asked for s5 and s6 as well, because the epitopes and the coverage come from them. They come
# from S7: its payload re-exports `epitope_set` and every candidate's `coverage` (B's contract, section 7), and the adapter never opens an
# S3-S6 artifact (it only asks `store.has` which of them exist, to choose between S7 and the S2 stand-in). So s0, s2 and s7 are the truth, and
# declaring s5 or s6 would claim a read that does not happen. S7's own inputs are stream B's to declare; once S7 lists s5 and s6, `verify`
# flags S7 when they change, and S8 and S10 once S7 has been re-run with other results (their recorded S7 digest then differs). Chasing that
# chain is `verify`'s job, not ours. These tests keep the declaration honest.


@pytest.fixture
def reads(monkeypatch):
    """The stage ids passed to `RunStore.read_artifact` while a stage computes, that is before its own `write_artifact`.

    The write is left out on purpose: it reads every DECLARED input once more to take its digest, so counting it would make "declared ==
    read" true by construction.
    """
    seen: list[str] = []
    writing: list[bool] = []
    real_read, real_write = RunStore.read_artifact, RunStore.write_artifact

    def spy_read(self, stage, verify=True):
        if not writing:
            seen.append(stage)
        return real_read(self, stage, verify)

    def spy_write(self, *args, **kwargs):
        writing.append(True)
        try:
            return real_write(self, *args, **kwargs)
        finally:
            writing.pop()

    monkeypatch.setattr(RunStore, "read_artifact", spy_read)
    monkeypatch.setattr(RunStore, "write_artifact", spy_write)
    return seen


def _route(tmp_path, name: str):
    """(store, config, the artifacts that route reads) for each way `load_inputs_from_store` can end."""
    if name == "stand_in":  # no S3-S7 artifact: the proteins a person names, from S2
        return copy_run(tmp_path / "run"), cfg(proteins=["fhbp"], top_k=1), {"s0", "s2"}
    r = fresh(tmp_path, s7_status="failed" if name == "s7_not_ok" else "ok", s7_warnings=["synthetic S7 failure"] if name == "s7_not_ok" else ())
    if name == "s7_missing":  # S3-S6 exist, S7 does not: nothing is read but S0
        r.store.artifact_path("s7").unlink()
    return r.store, cfg(top_k=3, study=HLA_STUDY), {"s7": {"s0", "s2", "s7"}, "s7_not_ok": {"s0", "s7"}, "s7_missing": {"s0"}}[name]


@pytest.mark.parametrize("stage", [s8_protocol, s10_animal], ids=["s8", "s10"])
@pytest.mark.parametrize("route", ["s7", "s7_not_ok", "s7_missing", "stand_in"])
def test_s8_and_s10_declare_exactly_the_artifacts_they_read_and_never_open_s3_to_s6(tmp_path, reads, route, stage):
    store, conf, expected = _route(tmp_path, route)
    reads.clear()  # building the chain read artifacts too; only what the stage itself reads counts
    art = stage.run(store, conf)
    assert set(reads) == expected == set(art.inputs)
    assert not set(reads) & {"s3", "s4", "s5", "s6"}
    assert art.inputs == {s: store.digest_of(s) for s in expected}  # and each declared digest is that of the artifact read


def _edit(store: RunStore, stage: str) -> None:
    """Write `stage` again with the same params, inputs, tools and status but one more payload field: an upstream re-run that gave other results."""
    a = store.read_artifact(stage)
    payload = json.loads(json.dumps(a.payload))
    payload["edited_by_the_test"] = True
    store.write_artifact(stage, payload, params=a.params, inputs=list(a.inputs), tools=a.producer["tools"], status=a.status, warnings=a.warnings)


@pytest.mark.parametrize("upstream", ["s0", "s2", "s7"])
def test_verify_flags_s8_and_s10_when_an_artifact_they_read_changes(run, upstream):
    c = cfg(top_k=3, study=HLA_STUDY)
    s8_protocol.run(run.store, c)
    s10_animal.run(run.store, c)
    assert run.store.verify() == []
    _edit(run.store, upstream)
    problems = run.store.verify()
    for stage in ("s8", "s10"):
        assert any(p.startswith(f"{stage}: STALE - built from {upstream} ") for p in problems), (stage, problems)


def test_s10_rows_are_testable_where_b_supplied_data_and_not_testable_where_it_did_not(run):
    art = s10_animal.run(run.store, cfg(top_k=3, study=HLA_STUDY))
    status = lambda cid: {s: row(art, cid, s)["status"] for s in ("S3", "S5_T_cell", "S5_B_cell", "S6", "S7", "functional")}  # noqa: E731
    nt, t = "not_testable_no_upstream_prediction", "testable"
    assert status(FHBP) == dict(S3=t, S5_T_cell=t, S5_B_cell=t, S6=t, S7=t, functional=nt)  # no correlate of protection is known: never invented
    assert status(NADA) == dict(S3=t, S5_T_cell=t, S5_B_cell=nt, S6=t, S7=t, functional=nt)  # BepiPred called no region for it
    assert status(NHBA) == dict(S3=t, S5_T_cell=nt, S5_B_cell=nt, S6=nt, S7=nt, functional=nt)  # protegenicity only
    assert art.payload["falsification_rows"]["by_status"] == {nt: 8, t: 10}
    s3 = row(art, FHBP, "S3")
    assert s3["upstream_data"]["protegenicity"] == 0.111 and s3["upstream_data"]["s7_final_rank"] == 2 and "rank" not in s3["upstream_data"]
    assert "protegenicity 0.111" in s3["upstream_prediction"] and "S7's final order" in s3["upstream_prediction"] and "position 2" in s3["upstream_prediction"]
    assert "S3 ranks" not in json.dumps(art.payload["design"])  # the S7 position is never reported as an S3 rank
    assert "Vaxign-ML percentile/100" in s3["upstream_data"]["units"]["protegenicity"]
    s6 = row(art, FHBP, "S6")
    assert s6["upstream_data"]["pc90"] == 1.11 and s6["upstream_data"]["coverage_by_population"]["Europe"] == 0.222
    assert set(s6["upstream_data"]["units"]) == {"pc90", "coverage_by_population"} and "not a fraction or a percent" in s6["note"]
    s7 = row(art, FHBP, "S7")
    assert s7["upstream_data"] == dict(structure_confidence=11.1, structure_validators=["af2-no-msa", "esmfold"],
                                       units=dict(structure_confidence=s7["upstream_data"]["units"]["structure_confidence"]))
    assert "S7 structure model (confidence 11.1; validators: af2-no-msa, esmfold)" in s7["upstream_prediction"] and "0-100" in s7["note"]
    assert "S7 is top-k only, or its structure step did not run" in row(art, NHBA, "S7")["note"]
    pools = {p["epitope_class"] for r_ in art.payload["design"]["falsification_map"] if r_["candidate_id"] == FHBP for p in r_["peptide_pools"]}
    assert pools == {"T_class_I", "T_class_II", "B_linear", "B_conformational"}
    labels = [a["label"] for a in art.payload["design"]["arms"] if a["role"] == ArmRole.CANDIDATE.value]
    assert any(f"{NADA}, S7 final rank 1)" in x for x in labels) and not any("S3 rank" in x for x in labels)
    assert art.payload["design"]["falsification_map"] and "validation error" not in " ".join(art.warnings)


def test_the_s3_row_is_not_testable_when_s3_ran_its_rule_based_fallback_and_says_why(tmp_path):
    r = fresh(tmp_path, backend="subtractive_filter")
    art = s10_animal.run(r.store, cfg(top_k=3, study=HLA_STUDY))
    for cid in (NADA, FHBP, NHBA):
        s3 = row(art, cid, "S3")
        assert s3["status"] == "not_testable_no_upstream_prediction" and s3["upstream_prediction"] is None and s3["upstream_data"] == {}
        assert "rule-based fallback" in s3["note"] and "not an antigenicity prediction" in s3["note"]
    assert row(art, FHBP, "S5_T_cell")["status"] == "testable"  # the other rows still use what B supplied
    assert any("S3 ran its rule-based fallback" in w for w in art.warnings)


def test_a_sequence_only_s7_makes_the_s7_row_not_testable_and_the_run_still_verifies(tmp_path):
    r = fresh(tmp_path, structure="degraded")
    art = s10_animal.run(r.store, cfg(top_k=3, study=HLA_STUDY))
    assert [row(art, cid, "S7")["status"] for cid in (NADA, FHBP, NHBA)] == ["not_testable_no_upstream_prediction"] * 3
    assert row(art, FHBP, "S5_T_cell")["status"] == "testable" and row(art, FHBP, "S3")["status"] == "testable"
    assert r.store.verify() == [] and "S7 DEGRADED" in " ".join(art.warnings)


# =========================================================================== S9: refusals are a status, not a failure


class RefusingClient:
    """Stands in for a code model that declines every request."""

    def __init__(self):
        self.messages = self

    def create(self, **_kw):
        return SimpleNamespace(content=[SimpleNamespace(type="text", text="I can't help with that.")], stop_reason="end_turn")


def test_s9_with_every_session_refused_is_ok_with_payload_status_refused_and_no_code(tmp_path, monkeypatch):
    monkeypatch.setattr(s9_robot, "backends_from_choice", lambda choice, model=None: [LLMBackend(model_id="scripted", client=RefusingClient())])
    r = fresh(tmp_path)
    c = cfg(top_k=1, simulate_runs=False)
    s8_protocol.run(r.store, c)
    art = s9_robot.run(r.store, c)
    sessions = [s for cand in art.payload["candidates"] for b in cand["backends"] for s in b["sessions"]]
    n = len(sessions)
    assert art.status == "ok" and art.payload["status"] == "refused"
    assert {s["outcome"] for s in sessions} == {"REFUSED"} and art.payload["summary"]["outcomes"] == dict(REFUSED=n) and n >= 10
    assert all(s["files"]["code"] is None and s["files"]["sim"] is None and s["refusal"]["kind"] == "text_pattern" for s in sessions)  # no code exists
    assert art.payload["summary"]["all_pass"] is False and art.payload["reasons"][0].startswith(f"{n} of {n} session(s) were REFUSED by the code model")
    assert art.warnings[0] == "S9 refused: " + "; ".join(art.payload["reasons"]) and len([w for w in art.warnings if ": REFUSED - " in w]) == n
    assert r.store.verify() == []
    plus = s9plus_animate.run(r.store, c)  # nothing to animate: not a failure
    assert plus.status == "ok" and plus.payload["status"] == "not_run" and plus.inputs == dict(s9=art.digest)
    assert plus.payload["reasons"] == [f"every S9 session was skipped, so there is no simulation to animate ({n} x REFUSED by the code model: no code exists)"]
    assert plus.warnings[0] == "S9+ not run: " + plus.payload["reasons"][0] and r.store.verify() == []
    code, out, err = vaxforge("verify", r.store.run_dir)
    assert code == 0, out + err


@needs_ot
def test_s9_with_a_refusing_model_beside_the_template_is_refused_and_s9plus_animates_only_what_has_a_simulation(tmp_path, monkeypatch):
    monkeypatch.setattr(s9_robot, "backends_from_choice",
                        lambda choice, model=None: [TemplateBackend(), LLMBackend(model_id="scripted", client=RefusingClient())])
    r = fresh(tmp_path)
    c = cfg(top_k=1)
    s8_protocol.run(r.store, c)
    art = s9_robot.run(r.store, c)
    outcomes = art.payload["summary"]["outcomes"]
    assert art.status == "ok" and art.payload["status"] == "refused" and set(outcomes) == {"PASS", "REFUSED"} and outcomes["PASS"] == outcomes["REFUSED"]
    plus = s9plus_animate.run(r.store, c)
    n = outcomes["PASS"]
    assert plus.status == "ok" and "status" not in plus.payload  # sessions with a simulation were animated: a normal result
    assert plus.payload["summary"] == dict(n_sessions=2 * n, n_animated=n, n_skipped=n)
    assert {s["skipped"] for s in plus.payload["sessions"] if not s["ok"]} == {"REFUSED by the code model: no code exists"}
    assert r.store.verify() == []


# =========================================================================== the connection to A's CLI, with S3-S7 present


@needs_ot
def test_vaxforge_run_and_verify_with_s3_to_s7_present_and_our_four_stages(tmp_path):
    r = fresh(tmp_path)
    conf = tmp_path / "streamc.yaml"
    conf.write_text("streamc:\n  top_k: 1\n  study: {species: mouse_hla_transgenic, goal: hla_epitope_validation, hla_transgenic_lines: [A2.DR1]}\n", encoding="utf-8")
    code, out, err = vaxforge("run", "--pathogen", "Neisseria meningitidis", "--out", r.store.run_dir, "--config", conf, "--stages", "s8,s9,s9plus,s10")
    assert code == 0, out + err
    assert all(re.search(rf"^{s}\s+ok\b", out, re.M) for s in ("s8", "s9", "s9plus", "s10")) and "(verify: ok)" in out
    st = RunStore(r.store.run_dir)
    assert st.stages_present() == ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7", "s8", "s9", "s9plus", "s10"]
    a8, a9, a10 = st.read_artifact("s8"), st.read_artifact("s9"), st.read_artifact("s10")
    assert a8.inputs == a10.inputs == dict(s0=st.digest_of("s0"), s2=st.digest_of("s2"), s7=st.digest_of("s7")) and a9.inputs == dict(s8=a8.digest)
    assert [e["candidate_id"] for e in a8.payload["candidates"]] == [NADA] and "status" not in a9.payload and a9.payload["summary"]["all_pass"] is True
    code, out, err = vaxforge("verify", st.run_dir)
    assert code == 0 and "stages s0, s1, s2, s3, s4, s5, s6, s7, s8, s9, s9plus, s10" in out, out + err


# =========================================================================== B's REAL stages, on a machine without any of B's tools


def b_stage(run_dir: Path, module: str, cache: Path, *extra: str) -> None:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    args = [sys.executable, "-m", f"vaxforge.{module}", "--run-dir", str(run_dir), "--set", "refdata.offline=true",
            "--set", f"refdata.cache_dir={json.dumps(cache.as_posix())}", *extra]  # B's documented per-stage CLI (docs/STREAM_B.md, "Run")
    p = subprocess.run(args, capture_output=True, text=True, cwd=ROOT, env=env, timeout=300)
    assert p.returncode == 0, f"{module}: {p.stdout}{p.stderr}"


@needs_toolless_machine
def test_real_degraded_s3_to_s7_from_b_s_own_cli_are_read_stated_and_never_faked(tmp_path):
    """No tool, no network (refdata.offline): S3 falls back to its rule-based ranker, S4 gates are unavailable, S5 has no predictor, S6 has no
    frequency table, S7 degrades to sequence-only - and B writes artifact status `ok` for every one of them."""
    store = copy_run(tmp_path / "run")
    cache = tmp_path / "cache"
    for module, extra in (("s3_rank", ()), ("s4_filter", ()), ("s5_epitopes", ()), ("s6_coverage", ()), ("s7_structure", ("--set", "s7.top_k=4"))):
        b_stage(store.run_dir, module, cache, *extra)
    store = RunStore(store.run_dir, cache_dir=cache)
    assert store.stages_present() == ["s0", "s1", "s2", "s3", "s4", "s5", "s6", "s7"] and store.verify() == []
    s7 = store.read_artifact("s7")
    assert all(store.read_artifact(s).status == "ok" for s in ("s3", "s4", "s5", "s6", "s7"))
    assert s7.payload["analysis"]["degraded_to_sequence_only"] is True and s7.payload["upstream"]["s4_incomplete_screening"] is True
    inputs = load_inputs_from_store(store, cfg(top_k=4))
    assert [c.candidate_id for c in inputs.candidates] == s7.payload["final_candidates"][:4] and len(inputs.candidates) == 4
    assert [c.rank for c in inputs.candidates] == [1, 2, 3, 4] and inputs.rank_source == RANK_SOURCE_S7_FINAL
    for c in inputs.candidates:  # nothing ran, so nothing is invented
        assert c.protegenicity is None and c.epitopes == [] and c.pc90 is None and c.coverage_by_population == {}
        assert c.structure_confidence is None and c.structure_validators == [] and c.localisation and c.evidence_refs == [f"s7:{s7.digest}"]
    w = " | ".join(inputs.warnings)
    for needle in ("S3 ran its rule-based fallback", "S4 screening was INCOMPLETE", "S5 analyses that did not run", "S6 population coverage did not run", "S7 DEGRADED"):
        assert needle in w
    a8, a10 = s8_protocol.run(store, cfg(top_k=4)), s10_animal.run(store, cfg(top_k=4))
    assert a8.status == a10.status == "ok" and "status" not in a8.payload and a8.inputs == dict(s0=store.digest_of("s0"), s2=store.digest_of("s2"), s7=s7.digest)
    for cid in (c.candidate_id for c in inputs.candidates):  # every row that depends on a predictor B could not run is not testable
        assert {row(a10, cid, s)["status"] for s in ("S3", "S5_T_cell", "S5_B_cell", "S6", "S7")} == {"not_testable_no_upstream_prediction"}
    assert store.verify() == []
    code, out, err = vaxforge("verify", store.run_dir)
    assert code == 0 and "OK - digests, upstream chain and referenced files are consistent" in out, out + err
