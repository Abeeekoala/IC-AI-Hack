"""`vaxforge report`: assertions on the rendered HTML (states, "not run" dashes, escaping, the fixture label, the sources)."""
from __future__ import annotations

import hashlib
import html
import json
import re
from pathlib import Path

import pytest

from vaxforge import cli, report, s9_5_finetune
from vaxforge.artifacts import RunStore
from vaxforge.config import Config

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
EVIDENCE = ROOT / "docs" / "examples" / "s9_5_adaption_evidence.real_4b.json"        # a real Adaption run (id 82c660bc-...)
TRAIN_MANIFEST = ROOT / "docs" / "examples" / "s9_5_manifest.real_4b.trimmed.json"
BAD = "<script>alert(1)</script>"
NR = '<span class="nr">&mdash; not run</span>'
SECTION_IDS = ["launcher", "overview", "candidates", "detail", "validation", "protocol", "robot", "animal", "prereg", "warnings", "limits"]


def section(page: str, sid: str) -> str:
    return re.search(rf'<section id="{sid}">.*?</section>', page, re.S).group(0)


def chips(page: str) -> dict[str, str]:
    """Pipeline-overview card title -> the status chip text."""
    return dict(re.findall(r'<li class="card"><h3>([^<]*)</h3><span class="chip chip-[a-z-]+">([^<]*)</span>', page))


def rows(block: str) -> list[list[str]]:
    return [re.findall(r"<td>(.*?)</td>", r, re.S) for r in re.findall(r"<tr>(.*?)</tr>", block, re.S) if "<td>" in r]


def row(block: str, label: str) -> list[str]:
    return next(r for r in rows(block) if r[0] == label)


# ---- a made-up run: S0-S7 written through RunStore, with hostile strings in several places ---------------------------
def build_run(tmp_path: Path, name: str = "synthetic") -> RunStore:
    st = RunStore(tmp_path / name, run_id=name)
    st.write_artifact("s0", {"query": BAD, "resolution": {"status": "resolved", "method": "exact", "message": "Resolved it"},
                             "taxon": {"scientific_name": "Testus bacterium", "taxid": 1}, "organism": {"domain": "bacteria", "gram_stain": "negative"},
                             "candidates": []}, tools={"ncbi-eutils": "taxonomy"}, warnings=["s0 says it's ok & fine"])
    st.write_artifact("s1", {"taxid": 1, "proteome": {"name": "Test proteome", "upid": "UP1", "n_sequences": 3, "release": "2026_01"},
                             "comparison_proteome": {}, "reference_dbs": {}, "blast_dbs": {}, "resource_status": {}},
                      tools={"blast": "2.17.0+"}, volatile={"cache_hits": {"proteome": True, "vfdb": True}})
    prot = lambda pid, vir, loc: {"protein_id": pid, "virulence": vir, "localisation": loc}  # noqa: E731
    st.write_artifact("s2", {"proteome": {}, "thresholds": {}, "tools": {}, "summary": {}, "columns": [], "proteins": [
        prot("P1", {"status": "hit", "evidence": "strong", "best_hit": {"vfdb_id": "VFG1", "gene": "fhbp", "identity_pct": 62.5, "query_coverage_pct": 88.0, "evalue": 1e-30}},
             {"status": "predicted", "label": "OuterMembrane", "accessibility": "accessible"}),
        prot("P2", {"status": "no_hit"}, {"status": "predicted", "label": "Extracellular", "accessibility": "accessible"}),
        prot("P3", {"status": "not_applicable"}, {"status": "not_run", "label": "Unknown", "accessibility": "unknown"})]},
        tools={"blastp": "2.17.0+", "psortb": "not_run"})
    cand = lambda pid, name, rank, prot_: {"protein_id": pid, "name": name, "rank": rank, "composite_score": prot_ or 0.5,  # noqa: E731
                                           "protegenicity": prot_, "passes_thresholds": {t: None if prot_ is None else prot_ >= float(t) for t in ("0.90", "0.85")}}
    s3c = [cand("P1", "Factor H binding protein", 1, 0.93), cand("P2", BAD, 2, 0.88), cand("P3", "Unscored protein", 3, None)]
    st.write_artifact("s3", {"ranker": {"backend": "vaxign_ml", "score_semantics": "protegenicity = percentile / 100"},
                             "leakage_audit": {"model": "Vaxign-ML pretrained", "holdout_enforced": False, "declared_holdout_taxids": [], "note": "trained on all Protegen"},
                             "counts": {"fold_enrichment": None, "fold_enrichment_note": "Needs ground-truth positives"},
                             "candidates": s3c, "ranking": s3c},
                      tools={"Vaxign-ML": "1.0"}, warnings=["LEAKAGE RISK: pretrained model saw the target's antigens", BAD])
    gates = lambda: {"allergenicity": {"status": "pass", "reason": "AlgPred2 score 0.10 < 0.45"},  # noqa: E731
                     "toxicity": {"status": "unavailable", "reason": "ToxinPred2 is not installed"}}
    st.write_artifact("s4", {"gates": {}, "incomplete_screening": True, "unavailable_gates": ["toxicity"],
                             "candidates": [{"protein_id": "P1", "name": "Factor H binding protein", "gates": gates(), "flags": ["conservancy"]},
                                            {"protein_id": "P2", "name": BAD, "gates": {"allergenicity": {"status": "pass", "reason": BAD}}},
                                            {"protein_id": "P3", "name": "Unscored protein", "gates": {}}],
                             "rejected": [{"protein_id": "P9", "name": "Housekeeping", "rejected_by": ["human_homology"], "recoverable": True}]},
                      tools={"blastp": "2.17.0+", "AlgPred": "2.0"}, warnings=["INCOMPLETE SCREENING: toxicity gate unavailable"])
    st.write_artifact("s5", {"candidates": [
        {"protein_id": "P1", "tcell": {"score": 0.4, "class_i": {"n_epitopes": 7, "n_promiscuous": 2}, "class_ii": {"n_epitopes": 3}},
         "bcell": {"linear_score": 0.3, "conformational": {"status": "ran"}}},
        {"protein_id": "P2", "tcell": {"score": 0.2, "class_i": {"n_epitopes": 0, "n_promiscuous": 0}, "class_ii": {"n_epitopes": 1}}, "bcell": {}},
        {"protein_id": "P3", "tcell": {"score": None}, "bcell": {}}]}, tools={"MHCflurry": "2.1 " + BAD})
    st.write_artifact("s6", {"candidates": [
        {"protein_id": "P1", "coverage": {"score": 0.91, "score_population": "World", "mode_used": "combined", "min_population": "Central America"}},
        {"protein_id": "P2", "coverage": {"score": 0.55, "score_population": "World", "mode_used": "combined", "min_population": "Europe"}},
        {"protein_id": "P3", "coverage": {"score": 0.0, "score_population": "World", "mode_used": "combined", "min_population": "World"}}]},
        tools={"popcover-own": "1"})
    st.write_artifact("s7", {"top_k": 2, "candidates": [
        {"protein_id": "P1", "name": "Factor H binding protein", "rank": 1, "in_top_k": True, "rank_basis": "structure_informed",
         "scores": {"final": 0.8123}, "structure": {"status": "ran", "mean_plddt": 61.5, "validator_agreement": {"value": 0.7}}},
        {"protein_id": "P2", "name": BAD, "rank": 2, "in_top_k": True, "rank_basis": "structure_informed",
         "scores": {"final": 0.5}, "structure": {"status": "ran", "mean_plddt": 55.0, "validator_agreement": {"value": None}}},
        {"protein_id": "P3", "name": "Unscored protein", "rank": 3, "in_top_k": False, "rank_basis": "pre_structure",
         "scores": {"final": 0.25}, "structure": {"status": "not_selected", "mean_plddt": None}}]}, tools={"Boltz-2": "2.2.1"})
    return st


# ---- S8, S9, S9+, S10, S12 payloads: the real chain's shapes (/docs/streamc.md), trimmed, with a hostile string in the names ----
def s8_payload() -> dict:
    steps = [{"step_id": f"s0{i}", "automation": a} for i, a in enumerate(("manual", "full", "partial", "partial"), 1)]
    protocol = lambda falsifier: {  # noqa: E731
        "falsifier": falsifier, "steps": steps, "cost_gbp_range": None, "config": {"endotoxin_limit_eu_per_ug": None, "readouts": ["elisa", "rt_qpcr"]},
        "does_not_measure": ["adaptive immunity", "antibody titre", "population HLA diversity (THP-1 is a single-genotype cell line)"],
        "estimate_basis": "Durations are coarse engineering estimates, not measured; cost is unsourced."}
    return {"selection": {"source": "s7_final_ranking"}, "order_fasta": {"path": "data/s8/order.fasta", "records": 2}, "order_fasta_missing": [],
            "cost": {"status": "not_estimated", "total": None, "priced_subtotal": None, "note": "No item is priced, so there is no estimate."},
            "candidates": [{"candidate_id": "P1", "protein_name": "Factor H binding protein", "rank": 1, "rank_source": "s7_final_order",
                            "protocol": protocol("falsified if the response does not exceed the endotoxin-matched LPS control")},
                           {"candidate_id": "P2", "protein_name": BAD, "rank": 2, "rank_source": "s7_final_order",
                            "protocol": protocol("falsified if " + BAD)}]}


def s9_payload() -> dict:
    session = lambda sid, outcome, **kw: {"session_id": sid, "outcome": outcome, **kw}  # noqa: E731
    return {"parameters_status": "PLACEHOLDER PARAMETERS: volumes are coarse defaults", "biosecurity": "S12 biosecurity gate NOT EVIDENCED to S9.",
            "simulator": {"simulated": True, "opentrons_version": "7.5.0"},
            "summary": {"n_candidates": 1, "n_sessions": 4, "outcomes": {"FAIL": 1, "PASS": 2, "UNVERIFIED": 1}, "all_pass": False},
            "candidates": [{"candidate_id": "P1", "protein_name": "Factor H binding protein", "backends": [{"label": "template", "model_id": None, "sessions": [
                session("s02_assembly", "PASS"), session("s03_transformation", "PASS"),
                session("s05_elisa", "FAIL", grade={"sim_error": "labware not found " + BAD}), session("s06_qpcr", "UNVERIFIED")]}]}]}


def s10_payload() -> dict:
    arm = lambda aid, role, dose, primary: {"arm_id": aid, "role": role, "dose_ug": dose, "n_per_group": 13, "primary_comparison": primary}  # noqa: E731
    return {"candidates_included": ["P1", "P2"], "outstanding_human_inputs": 17,
            "falsification_rows": {"total": 3, "by_status": {"not_testable_no_upstream_prediction": 2, "testable": 1}},
            "design": {"species_selection": {"species": "mouse_wild_type", "strain": "BALB/c " + BAD, "strain_status": "addressed"},
                       "adjuvant": {"name": "alum"}, "route": {"route": "IM"}, "challenge_model": {"status": "unknown_check"},
                       "positive_control": {"status": "check"}, "primary_endpoint": {"name": "Serum IgG endpoint titre (log10)"},
                       "arms": [arm("A01", "candidate_antigen_plus_adjuvant", 20.0, True), arm("A02", "adjuvant_only", None, False)],
                       "power_analysis": {"n_per_group_allocated": 13, "total_animals": 26, "effect_size_d": 1.4, "effect_size_status": "ASSUMPTION - replace with pilot data",
                                          "alpha_familywise": 0.05, "multiplicity_method": "holm", "target_power": 0.8, "achieved_power": 0.8312323946125562},
                       "falsification_map": [
                           {"candidate_id": "P1", "upstream_stage": "S3", "animal_readout": "IgG ELISA titre", "falsified_if": "no titre rise over adjuvant-only", "status": "testable"},
                           {"candidate_id": "P2", "upstream_stage": "S5_T_cell", "animal_readout": "IFN-gamma ELISpot", "falsified_if": None,
                            "rule_if_testable": "Response to whole antigen but NOT to the predicted pools", "status": "not_testable_no_upstream_prediction"}]}}


def s12_payload(state="blocked", reasons=None, review=None) -> dict:
    check = lambda result: {"result": result, "detail": ""}  # noqa: E731
    return {"state": state, "reasons": reasons or [], "review_required": bool(review), "review": review or [],
            "order": {"status": "supplied", "source": "s8", "n_sequences": 1}, "provider_record": {"status": "not_supplied"},
            "sequences": [{"id": "P1", "length": 392, "provider_verdict": "not_screened", "local_checks": {
                "selected_candidate": check("pass"), "sequence_sanity": check("pass"), "virulence_flag": check("review"), "toxin_flag": check("not_evaluated")}}],
            "limits": ["The local checks are not a synthesis-screening result and never substitute for one."]}


FLAGS = ["P1: virulence_flag: P1: S2 records it as virulence-associated (evidence strong); flagged for human review, not blocked",
         "P2: virulence_flag: " + BAD]


def add_wetlab(st: RunStore, **extra) -> RunStore:
    for stage, payload in {"s8": s8_payload(), "s9": s9_payload(), "s9plus": {"summary": {"n_sessions": 4, "n_animated": 3, "n_skipped": 1}, "sessions": []},
                           "s10": s10_payload(), "s12": s12_payload("blocked", ["P1: toxin_flag: S4 recorded no toxicity result"], FLAGS), **extra}.items():
        st.write_artifact(stage, payload, inputs=[], tools={"opentrons": "7.5.0"} if stage == "s9" else {})
    return st


@pytest.fixture
def run(tmp_path):
    return build_run(tmp_path)


@pytest.fixture
def page(run):
    return report.render(run.run_dir, now="2026-11-14T12:00:00Z")


# ---- the committed fixture --------------------------------------------------------------------------------------------
def snapshot(d: Path) -> dict:
    return {str(p.relative_to(d)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(d.rglob("*")) if p.is_file()}


def test_fixture_is_labelled_on_every_section_and_its_stages_are_fixture_or_not_run():
    before = snapshot(FIXTURE)
    page = report.render(FIXTURE, now="2026-11-14T12:00:00Z")
    assert snapshot(FIXTURE) == before, "the report must not write into the run"
    assert re.search(r'<header class="top">.*Fixture, not a result.*</header>', page, re.S)
    for sid in SECTION_IDS:
        assert "Fixture, not a result" in section(page, sid), sid
    states = chips(page)
    assert len(states) == 15
    assert [states[t] for t in states if t[:3] in ("S0 ", "S1 ", "S2 ")] == ["fixture"] * 3
    assert {v for t, v in states.items() if t[:3] not in ("S0 ", "S1 ", "S2 ")} == {"not run"}
    # S3-S7 never ran here: the ranked list says so instead of showing an empty table or zeros
    assert "&mdash; not run" in section(page, "candidates") and "<table" not in section(page, "candidates")
    assert "FIXTURE: localisation values are stand-ins" in section(page, "warnings")


def test_a_run_that_is_not_a_fixture_carries_no_fixture_label(page):
    assert "Fixture, not a result" not in page


# ---- statuses ------------------------------------------------------------------------------------------------------------
def test_each_of_the_eight_states_is_reachable(tmp_path):
    st = RunStore(tmp_path / "states", run_id="states")
    w = lambda stage, payload, **kw: st.write_artifact(stage, payload, inputs=[], **kw)  # noqa: E731
    w("s0", {"query": "q"}, status="needs_user_input", warnings=["ambiguous: pick one"])
    w("s3", {}, status="failed", warnings=["Vaxign-ML crashed"])
    w("s4", {"status": "blocked_on_data"})
    w("s9", {"status": "refused"})
    w("s9_5", {"fine_tune": {"status": "not_run", "reasons": ["127 rows, 1000 needed"]}})
    w("s10", {"x": 1})
    s1 = {"taxid": 1, "proteome": {}, "comparison_proteome": {}, "reference_dbs": {}, "blast_dbs": {}, "resource_status": {}}
    w("s1", s1, volatile={"cache_hits": {"proteome": True}})
    w("s11", {"x": 1}, volatile={"cache_hits": {"proteome": True, "vfdb": False}})
    page = report.render(st.run_dir)
    got = {t.split(" ")[0]: s for t, s in chips(page).items()}
    assert got["S0"] == "needs user input" and got["S3"] == "failed" and got["S4"] == "blocked" and got["S9"] == "refused"
    assert got["S9.5"] == "not run" and got["S10"] == "done" and got["S1"] == "cached" and got["S11"] == "done"
    assert got["S2"] == "not run" and got["S12"] == "not run"
    assert "127 rows, 1000 needed" in section(page, "overview")          # why it was not run, from the artifact
    assert set(chips(page).values()) <= {"done", "not run", "needs user input", "failed", "blocked", "refused", "cached", "fixture"}


def test_an_artifact_that_fails_its_digest_is_failed_and_does_not_hide_the_rest(run):
    path = run.artifact_path("s3")
    path.write_text(path.read_text(encoding="utf-8").replace("0.93", "0.99"), encoding="utf-8")
    page = report.render(run.run_dir)
    assert chips(page)["S3 · Antigenicity ranking"] == "failed"
    assert "digest mismatch" in section(page, "overview")
    assert "Warnings of these stages could not be read: s3" in section(page, "warnings")
    assert "0.99" not in page and "Passes protegenicity" not in page          # nothing from the edited file is shown
    assert chips(page)["S2 · Virulence and localisation"] == "done"


# ---- candidates and their evidence --------------------------------------------------------------------------------------
def test_candidates_are_ranked_from_s7_with_source_stage_and_tool_version(page):
    cand = section(page, "candidates")
    assert "Final score" in cand
    ranked = rows(cand.split("Rejected by S4")[0])
    assert [r[0] for r in ranked] == ["1", "2", "3"]
    assert "s7 · Boltz-2 2.2.1" in ranked[0][3] and "0.8123" in ranked[0][2]
    assert "outside the top-k" in ranked[2][4] and "S4 flag: conservancy" in ranked[0][4]
    rejected = rows(cand.split("Rejected by S4")[1])
    assert "human_homology" in rejected[0][1] and "s4 · " in rejected[0][3]


def test_every_evidence_row_names_its_source_stage_and_tool_version(page):
    det = section(page, "detail")
    evidence = [r for r in rows(det) if len(r) == 3]
    assert len(evidence) > 40
    for label, value, source in evidence:
        assert re.match(r"s\d+ · \S", source), (label, source)
    p1 = det.split('id="c2"')[0]
    assert row(p1, "Protegenicity")[1:] == ["0.93", "s3 · Vaxign-ML 1.0"]
    assert row(p1, "Virulence (BLAST vs VFDB)")[2] == "s2 · blastp 2.17.0+, psortb not_run"
    assert "identity 62.5%" in row(p1, "Virulence (BLAST vs VFDB)")[1] and "E-value 1e-30" in row(p1, "Virulence (BLAST vs VFDB)")[1]
    assert row(p1, "Final score (weights are unvalidated priors)")[1:] == ["0.8123", "s7 · Boltz-2 2.2.1"]
    assert row(p1, "Class I epitopes")[1] == "7"


def test_a_value_that_was_not_computed_is_a_dash_with_words_never_zero_or_empty(page):
    p3 = section(page, "detail").split('id="c3"')[1]
    nr = '<span class="nr">&mdash; not run</span>'
    na = '<span class="nr">&mdash; not applicable</span>'
    assert row(p3, "Protegenicity")[1] == nr
    assert row(p3, "Passes protegenicity threshold 0.90")[1] == nr
    assert row(p3, "Localisation")[1] == nr                                        # S2 localisation status not_run
    assert row(p3, "Virulence (BLAST vs VFDB)")[1] == na                           # S2 says not applicable
    assert row(p3, "T-cell score (heuristic, unvalidated)")[1] == nr
    assert row(p3, "S4 gates")[1] == nr                                            # no gate result recorded
    assert row(p3, "Mean pLDDT (confidence of the model, not immunogenicity)")[1] == nr
    # a computed zero is a zero, not a dash
    assert row(p3, "Population coverage score (World, combined)")[1] == "0"
    assert row(section(page, "detail").split('id="c2"')[1], "Class I epitopes")[1] == "0"
    # a gate that could not run is "not run" with its reason, never "pass"
    p1 = section(page, "detail").split('id="c2"')[0]
    assert row(p1, "S4 gate toxicity")[1] == f"{nr} ToxinPred2 is not installed"
    assert row(p1, "S4 gate allergenicity")[1] == "pass: AlgPred2 score 0.10 &lt; 0.45"
    assert row(p1, "Validator agreement")[1] == "0.7"
    assert row(section(page, "detail").split('id="c2"')[1].split('id="c3"')[0], "Validator agreement")[1] == nr
    # validation: no ground truth was run, so fold enrichment is a dash, and the holdout is stated as not enforced
    val = section(page, "validation")
    assert row(val, "Fold enrichment")[1] == nr and row(val, "Holdout enforced")[1] == "no"


def test_stages_that_were_not_run_show_in_the_evidence_not_a_default(tmp_path):
    st = RunStore(tmp_path / "only_s3", run_id="only_s3")
    st.write_artifact("s3", {"candidates": [{"protein_id": "P1", "name": "A", "rank": 1, "composite_score": 0.5, "protegenicity": None}]}, inputs=[])
    page = report.render(st.run_dir)
    assert "Composite score" in section(page, "candidates")
    d = section(page, "detail")
    for label in ("S4 safety and conservancy gates", "S5 epitope prediction", "S6 population coverage", "S7 structure and final ranking"):
        assert row(d, label)[1] == '<span class="nr">&mdash; not run</span>'
    assert row(d, "Protegenicity")[1] == '<span class="nr">&mdash; not run</span>'


# ---- escaping ---------------------------------------------------------------------------------------------------------------
def test_artifact_strings_are_escaped_and_the_page_has_no_script_or_external_reference(page):
    assert BAD not in page
    assert html.escape(BAD) in page
    assert "<script" not in page.lower() and "<img" not in page.lower()
    assert not re.findall(r'(?:src|href)="(?!#)', page), "no external or relative links: the file must work offline"
    assert "https://" not in page and "http://" not in page and "@import" not in page


def test_hostile_run_id_and_attribute_breakouts_are_escaped(tmp_path):
    hostile = 'x"><img src=x onerror=alert(1)>'
    st = RunStore(tmp_path / "plain", run_id=hostile)
    st.write_artifact("s3", {"candidates": [{"protein_id": hostile, "name": hostile, "rank": 1}]}, inputs=[], warnings=[hostile])
    page = report.render(st.run_dir)
    assert "<img" not in page and "onerror=alert(1)>" not in page
    assert html.escape(hostile) in page


# ---- warnings, pre-registration, limits, footer --------------------------------------------------------------------------
def test_every_warning_from_every_artifact_appears_verbatim(page, run):
    block = section(page, "warnings")
    expected = [w for s in run.stages_present() for w in run.read_artifact(s).warnings]
    assert len(expected) == 4
    for w in expected:
        assert f"<li>{html.escape(w)}</li>" in block
    assert re.search(r"S3 · Antigenicity ranking.*LEAKAGE RISK", block, re.S)


def test_prereg_and_gate_read_whatever_artifact_exists(run):
    absent = section(report.render(run.run_dir), "prereg")
    assert absent.count('chip chip-not-run">not run') == 2                                               # S11 and S12 both "not run"
    run.write_artifact("s11", {"frozen": {"registered_at": "2026-11-14T10:00:00Z", "config_digest": "sha256:cfg",
                                          "upstream": {"s3": {"status": "ok", "digest": "sha256:abc", "tools": {"Vaxign-ML": "1.0"}},
                                                       "s9": {"status": "not_run"}}},
                              "frozen_digest": "sha256:frozen123", "note": "registered_at is this machine's clock " + BAD},
                       inputs=[], tools={})
    run.write_artifact("s12", {"status": "refused", "reason": "sequence matched a screening list"}, inputs=[], warnings=["gate says no"])
    page = report.render(run.run_dir)
    block = section(page, "prereg")
    assert chips(page)["S11 · Pre-registration"] == "done" and chips(page)["S12 · Biosecurity gate"] == "refused"
    assert row(block, "Frozen digest")[1] == "sha256:frozen123" and row(block, "Frozen digest")[2].startswith("s11 · ")
    assert row(block, html.escape("Registered at (this machine's clock)"))[1] == "2026-11-14T10:00:00Z"
    assert ["s3", "ok", "sha256:abc", "Vaxign-ML 1.0"] in rows(block)
    assert any(r[0] == "s9" and r[1] == "not_run" and "not run" in r[2] for r in rows(block))      # a stage not run when frozen stays not run
    assert html.escape(BAD) in block and BAD not in page
    assert "sequence matched a screening list" in block and "gate says no" in section(page, "warnings")


def test_a_field_the_report_does_not_name_is_never_shown_so_no_assay_value_can_appear(run):
    payload = s8_payload()
    payload["candidates"][0]["protocol"]["predicted_elisa_od450"] = 1.987
    payload["predicted_titre"] = 4321
    run.write_artifact("s8", payload, inputs=[])
    page = report.render(run.run_dir)
    assert "1.987" not in page and "4321" not in page and "predicted_elisa_od450" not in page
    assert "falsified if the response does not exceed" in section(page, "protocol")       # the named fields are shown


def test_limits_card_is_the_readme_text_and_matches_the_readme():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def table(start, end):
        body = readme[readme.index(start):readme.index(end, readme.index(start))]
        return [tuple(c.strip().replace("*", "") for c in ln.strip().strip("|").split(" | "))
                for ln in body.splitlines() if ln.startswith("|") and not ln.startswith(("|---", "| Claim", "| Not implemented"))]

    assert table("## 4. Known limitations", "## 5. Install") == list(report.CEILINGS)
    assert table("## 7. What is deliberately absent", "Listing what you refused") == list(report.ABSENT)
    block = section(report.render(FIXTURE), "limits")
    for claim, ceiling in report.CEILINGS + report.ABSENT:
        assert html.escape(claim) in block and html.escape(ceiling) in block


def test_footer_has_run_id_manifest_and_artifact_digests_and_generation_time(page, run):
    foot = page.split("<footer>")[1]
    assert row(foot, "Run id")[1] == "synthetic" and row(foot, "Generated (UTC)")[1] == "2026-11-14T12:00:00Z"
    assert row(foot, "manifest.json sha256")[1] == hashlib.sha256(run.manifest_path.read_bytes()).hexdigest()
    for s in run.stages_present():
        assert row(foot, f"Artifact digest {s}")[1] == run.digest_of(s)
    assert row(foot, "Config digest (manifest)")[1] == '<span class="nr">&mdash; not run</span>'
    assert "no problems found" in foot


def test_footer_reports_a_stale_chain(run):
    run.write_artifact("s2", {"proteome": {}, "thresholds": {}, "tools": {}, "summary": {}, "columns": [], "proteins": []})   # s3 was built on the old s2
    assert "STALE" in report.render(run.run_dir).split("<footer>")[1]


# ---- shape of the page; robustness; CLI --------------------------------------------------------------------------------
def test_page_is_one_self_contained_responsive_accessible_file(page):
    assert page.startswith("<!doctype html>") and '<html lang="en">' in page and 'name="viewport"' in page
    assert "prefers-color-scheme:dark" in page and "@media print" in page and "overflow-x:auto" in page
    assert [m for m in re.findall(r'<section id="(\w+)"', page)] == SECTION_IDS
    assert [m for m in re.findall(r'<li><a href="#(\w+)">', page)] == SECTION_IDS
    assert page.count("Honest ceiling</h3>") == 10 and "<h2>Source</h2>" in page
    for ref in re.findall(r'href="#(\w+)"', page):
        assert f'id="{ref}"' in page, ref
    assert page.index('id="overview"') < page.index('id="candidates"') < page.index('id="warnings"') < page.index('id="limits"') < page.index("<footer>")


def test_empty_and_malformed_runs_render_instead_of_crashing(tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    page = report.render(empty)
    assert set(chips(page).values()) == {"not run"} and "no artifacts in this run" in section(page, "warnings")
    odd = RunStore(tmp_path / "odd", run_id="odd")
    odd.write_artifact("s3", {"candidates": ["x", 3, None], "ranker": "not a dict", "leakage_audit": [1]}, inputs=[])
    odd.write_artifact("s11", {"frozen": "nope", "frozen_digest": None}, inputs=[])
    page = report.render(odd.run_dir)
    assert "no stage from S3 to S7 recorded a candidate list" in section(page, "candidates")
    assert chips(page)["S3 · Antigenicity ranking"] == "done"


def test_cli_writes_the_file_through_the_real_parser(tmp_path, monkeypatch, run, capsys):
    monkeypatch.setattr(cli, "EXTENSION_MODULES", cli.EXTENSION_MODULES + ["report"])
    out = tmp_path / "out" / "r.html"
    out.parent.mkdir()
    assert cli.main(["report", str(run.run_dir), "--out", str(out)]) == 0
    assert out.read_text(encoding="utf-8") == report.render(run.run_dir, now=re.search(r"Generated \(UTC\)</td><td>([^<]*)", out.read_text(encoding="utf-8")).group(1))
    monkeypatch.chdir(tmp_path)
    assert report.main([str(run.run_dir)]) == 0 and (run.run_dir / "report.html").is_file()        # default --out
    assert not (tmp_path / "report.html").exists(), "the default must not write into the current folder"
    missing = tmp_path / "nope"
    assert cli.main(["report", str(missing)]) == 1 and not missing.exists()                          # RunStore would have created it
    assert "is not a folder" in capsys.readouterr().err
    assert report.main([str(run.run_dir), "--out", str(tmp_path / "no_such_dir" / "r.html")]) == 1


# ---- S8, S9, S9+, S9.5, S10 and S12: what each section shows --------------------------------------------------------------
def part(block: str, start: str, end: str | None = None) -> str:
    text = block.split(start)[1]
    return text.split(end)[0] if end else text


def attach(st: RunStore, tmp_path: Path, **finetune):
    """S9.5 through its own stage, as `vaxforge run --stages s9_5` writes it (finetune.evidence / finetune.manifest are paths)."""
    cfg = Config({"finetune": {"base_model_id": None, "model_id": None, "eval_tasks": 11, "seed": 1, **finetune}}, root=tmp_path)
    return s9_5_finetune.run(st, cfg)


def test_s8_shows_each_falsification_condition_what_it_does_not_measure_and_cost_as_not_estimated(run):
    add_wetlab(run)
    block = section(report.render(run.run_dir), "protocol")
    p1 = block.split("<article")[1]
    assert row(p1, "Falsification condition")[1:] == ["falsified if the response does not exceed the endotoxin-matched LPS control", "s8 · tool version not recorded"]
    assert "<li>antibody titre</li>" in row(p1, "What this does NOT measure")[1]
    assert "<li>population HLA diversity (THP-1 is a single-genotype cell line)</li>" in row(p1, "What this does NOT measure")[1]
    assert row(p1, "Steps and who does them")[1] == "4 steps: HUMAN (manual) 1, ROBOT (full) 1, ROBOT-PARTIAL (partial) 2"
    assert row(p1, "Readouts")[1] == "<ul><li>elisa</li><li>rt_qpcr</li></ul>"
    assert row(p1, "Endotoxin limit (EU/ug)")[1] == '<span class="nr">&mdash; not set</span>'
    assert row(p1, "Cost range (GBP)")[1] == '<span class="nr">&mdash; not estimated</span>'
    assert row(block, "Cost (all shortlisted protocols)")[1] == '<span class="nr">&mdash; not estimated</span>'
    assert "No item is priced, so there is no estimate." in block
    assert "2 record(s) in <code>data/s8/order.fasta</code>" in row(block, "Order list for gene synthesis")[1]
    assert row(block, "Candidates with a protocol")[1] == "2" and row(block, "Candidates chosen from")[1] == "s7_final_ranking"


def test_s8_cost_is_a_total_only_when_every_item_is_priced(run):
    def cost_cell(cost):
        payload = s8_payload()
        payload["cost"] = cost
        run.write_artifact("s8", payload, inputs=[])
        return row(section(report.render(run.run_dir), "protocol"), "Cost (all shortlisted protocols)")[1]

    assert cost_cell({"status": "estimated", "total": {"currency": "GBP", "amount": 123.45}}) == "estimated total GBP 123.45 (only the listed items are counted)"
    partial = cost_cell({"status": "partial", "total": None, "priced_subtotal": {"currency": "GBP", "amount": 10.0}})
    assert "partial, no total" in partial and "priced so far GBP 10" in partial and "estimated total" not in partial
    assert cost_cell(None) == NR


def test_s9_shows_verdict_counts_per_candidate_and_each_session_that_did_not_pass(run):
    add_wetlab(run)
    s9 = part(section(report.render(run.run_dir), "robot"), "S9 · Robot code", "S9+ · Protocol animation")
    assert row(s9, "Simulator")[1] == "Opentrons 7.5.0, simulated" and row(s9, "Simulator")[2] == "s9 · opentrons 7.5.0"
    assert row(s9, "Outcomes of all sessions")[1] == "FAIL 1, PASS 2, UNVERIFIED 1"
    assert "PLACEHOLDER PARAMETERS" in row(s9, "Parameters")[1] and "NOT EVIDENCED" in row(s9, "Biosecurity")[1]
    per_backend = next(r for r in rows(s9) if len(r) == 8)
    assert per_backend[1:7] == ["template", "4", "2", "1", "1", "none"] and "Factor H binding protein" in per_backend[0]
    not_passed = [r for r in rows(s9) if len(r) == 5]
    assert ["P1", "template", "s05_elisa", "FAIL", "labware not found " + html.escape(BAD)] in not_passed
    assert ["P1", "template", "s06_qpcr", "UNVERIFIED", '<span class="nr">&mdash; not recorded</span>'] in not_passed
    assert len(not_passed) == 2, "a PASS session is counted, not listed"


def test_an_s9_that_did_not_run_never_shows_a_pass(run):
    empty = {"status": "not_run", "reasons": ["s8 did not run"], "parameters_status": "PLACEHOLDER", "biosecurity": "S12 note",
             "simulator": {"simulated": False, "opentrons_version": None}, "candidates": [],
             "summary": {"n_candidates": 0, "n_sessions": 0, "outcomes": {}, "all_pass": False}}
    run.write_artifact("s9", empty, inputs=[], tools={"opentrons": "not_run"})
    page = report.render(run.run_dir)
    s9 = part(section(page, "robot"), "S9 · Robot code", "S9+ · Protocol animation")
    assert chips(page)["S9 · Robot code"] == "not run" and "s8 did not run" in s9
    assert "PASS" not in s9 and "no robot code in this run" in s9
    assert row(s9, "Simulator")[1] == '<span class="nr">&mdash; not simulated</span>'
    assert row(s9, "Sessions (one robot-code file each)")[1] == NR and row(s9, "Outcomes of all sessions")[1] == NR   # the stage's 0 is not a count
    # code generated but not simulated: the sessions are UNVERIFIED, and there is still no PASS to show
    unverified = {**empty, "summary": {"n_candidates": 1, "n_sessions": 2, "outcomes": {"UNVERIFIED": 2}, "all_pass": False}, "candidates": [
        {"candidate_id": "P1", "protein_name": "A", "backends": [{"label": "template", "sessions": [
            {"session_id": "s02", "outcome": "UNVERIFIED"}, {"session_id": "s03", "outcome": "UNVERIFIED"}]}]}]}
    run.write_artifact("s9", unverified, inputs=[], tools={"opentrons": "not_run"})
    s9 = part(section(report.render(run.run_dir), "robot"), "S9 · Robot code", "S9+ · Protocol animation")
    cells = next(r for r in rows(s9) if len(r) == 8)
    assert cells[2:7] == ["2", NR, "0", "2", "none"] and "PASS 2" not in s9


def test_s9plus_says_how_many_animations_exist_and_embeds_none(run):
    add_wetlab(run)
    page = report.render(run.run_dir)
    a = part(section(page, "robot"), "S9+ · Protocol animation", "S9.5 · Fine-tuned")
    assert [row(a, label)[1] for label in ("Animations written", "Sessions considered", "Sessions skipped")] == ["3", "4", "1"]
    assert "this report does not embed them" in a and "<iframe" not in page and "<object" not in page and "<embed" not in page
    run.write_artifact("s9plus", {"status": "not_run", "reasons": ["s9 not run"], "sessions": [], "summary": {"n_sessions": 0, "n_animated": 0, "n_skipped": 0}}, inputs=[])
    a = part(section(report.render(run.run_dir), "robot"), "S9+ · Protocol animation", "S9.5 · Fine-tuned")
    assert [row(a, label)[1] for label in ("Animations written", "Sessions considered", "Sessions skipped")] == [NR] * 3
    assert "separate HTML files" not in a


def test_s9_5_not_run_shows_its_status_and_no_win_rate_even_if_a_stray_evidence_file_exists(run, tmp_path):
    for folder in ("s9_5", "data/s9_5"):                                  # files the artifact does not reference are not read
        (run.run_dir / folder).mkdir(parents=True)
        (run.run_dir / folder / "adaption_evidence.json").write_text(json.dumps({"run_id": "x", "status": "succeeded", "best_win_rate": 0.99}), encoding="utf-8")
    attach(run, tmp_path)
    page = report.render(run.run_dir)
    block = part(section(page, "robot"), "S9.5 · Fine-tuned")
    assert chips(page)["S9.5 · Fine-tuned protocol code"] == "not run"
    assert row(block, "Fine-tune status")[1] == '<span class="nr">&mdash; not run</span>'
    assert row(block, "Base model id")[1] == NR and "this stage does not generate yet" in block
    assert "best_win_rate" not in block and "0.99" not in page and "Adaption run" not in block


def test_adaption_evidence_is_labelled_as_adaptions_own_number_next_to_its_caveat(run, tmp_path):
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    attach(run, tmp_path, evidence=str(EVIDENCE), manifest=str(TRAIN_MANIFEST))
    page = report.render(run.run_dir)
    block = part(section(page, "robot"), "S9.5 · Fine-tuned")
    assert chips(page)["S9.5 · Fine-tuned protocol code"] == "done"
    best = row(block, html.escape("best_win_rate, Adaption's own number"))
    assert best[1].startswith("0.7832<br>") and html.escape(evidence["caveat"]) in best[1]          # the file's own caveat, beside the number
    assert "not evidence of protocol quality" in best[1]
    assert row(block, "Base model (pinned)")[1] == "Qwen/Qwen3.5-4B"
    assert row(block, "Run status (as Adaption reports it)")[1] == "succeeded"
    assert row(block, "Training method")[1] == "instruction" and row(block, "Training type")[1] == "lora"
    assert row(block, "Iterations completed (of the cap)")[1] == "1 of 2"
    assert row(block, "Checkpoint sha256")[1] == evidence["checkpoint_sha256"]
    assert "file data/s9_5/adaption_evidence.json · sha256 " in row(block, "Base model (pinned)")[2]
    assert row(block, "Held-out win rate")[1] == NR                       # this project's own evaluation was not attached
    assert row(block, "Rows included in the training file")[1] == "1000" and row(block, "Candidate rows considered")[1] == "3121"
    assert row(block, "Synthetic rows among those included")[1] == "571 of 1000 (57.1%), computed here from the two counts"
    assert row(block, "Meets the row minimum")[1] == "yes" and "file data/s9_5/manifest.json" in row(block, "Rows included in the training file")[2]
    assert "Adaption's own numbers about its own training run" in block


def test_evidence_without_a_manifest_or_a_caveat_is_still_labelled_and_hostile_text_is_escaped(run, tmp_path):
    evidence = json.loads(EVIDENCE.read_text(encoding="utf-8"))
    evidence.pop("caveat")
    evidence["trained_model_name"] = BAD
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(evidence), encoding="utf-8")
    attach(run, tmp_path, evidence=str(path))
    page = report.render(run.run_dir)
    block = part(section(page, "robot"), "S9.5 · Fine-tuned")
    assert "The artifact carries no caveat" in row(block, html.escape("best_win_rate, Adaption's own number"))[1]
    assert "no training manifest is attached" in block and "Synthetic rows" not in block
    assert BAD not in page and html.escape(BAD) in block


def test_s10_shows_arms_group_size_power_and_the_falsification_rows_with_their_states(run):
    add_wetlab(run)
    page = report.render(run.run_dir)
    block = section(page, "animal")
    assert row(block, "Arms")[1] == "2" and row(block, "Animals per group (allocated)")[1] == "13" and row(block, "Total animals")[1] == "26"
    assert row(block, "Assumed effect size d")[1] == "1.4 (ASSUMPTION - replace with pilot data)"     # stated, with its status
    assert row(block, "Family-wise alpha, multiplicity method")[1] == "0.05, holm" and row(block, "Target power, achieved power")[1] == "0.8, 0.8312"
    assert row(block, "Falsification rows and their states")[1] == "3 rows: not_testable_no_upstream_prediction 2, testable 1"
    assert row(block, "Challenge model")[1] == "unknown_check" and row(block, "Human inputs still outstanding")[1] == "17"
    assert row(block, "Primary endpoint")[2].startswith("s10 · ")
    arms = [r for r in rows(block) if len(r) == 6]
    assert arms[0][:5] == ["A01", "candidate_antigen_plus_adjuvant", "20", "13", "yes"]
    assert arms[1][:5] == ["A02", "adjuvant_only", '<span class="nr">&mdash; not applicable</span>', "13", "no"]        # a control has no dose
    testable = [r for r in rows(block) if len(r) == 5]
    assert testable == [["P1", "S3", "IgG ELISA titre", "no titre rise over adjuvant-only", "testable"]]
    assert "Response to whole antigen" not in block, "the rule of a row that cannot be tested is not shown as its falsification condition"


def test_s12_review_flags_are_a_review_list_not_an_error(run):
    add_wetlab(run)
    page = report.render(run.run_dir)
    s12 = part(section(page, "prereg"), "S12 · Biosecurity gate")
    assert chips(page)["S12 · Biosecurity gate"] == "blocked" and row(s12, "Gate state")[1] == "blocked"
    review = re.search(r'<div class="review">.*?</div>', s12, re.S).group(0)
    assert "for review" in review and "nothing is blocked by these" in review and "2 candidate flag(s)" in review
    assert html.escape(FLAGS[0]) in review and html.escape(FLAGS[1]) in review
    assert not re.search(r"chip-(failed|refused|blocked)", review) and "error" not in review.lower()
    reasons = row(s12, "Reasons")[1]
    assert "toxin_flag" in reasons and "virulence_flag" not in reasons         # only what blocks is a reason; a review flag is not
    assert "source: the order list S8 wrote" in row(s12, "Order list")[1] and row(s12, "Provider screening record")[1] == "not_supplied"
    seq = next(r for r in rows(s12) if len(r) == 7)
    assert seq == ["P1", "392", "not_screened", "pass", "pass", "review", "not_evaluated"]
    assert "never substitute for one" in s12
    # review flags alone change nothing: a cleared gate with a flag is still "done", and the list is still shown
    run.write_artifact("s12", s12_payload("cleared", ["all 1 ordered sequences passed"], FLAGS[:1]), inputs=[])
    page = report.render(run.run_dir)
    assert chips(page)["S12 · Biosecurity gate"] == "done" and html.escape(FLAGS[0]) in part(section(page, "prereg"), "S12 · Biosecurity gate")
    run.write_artifact("s12", s12_payload("not_screened", ["no order list was supplied"], FLAGS[:1]), inputs=[])
    assert chips(report.render(run.run_dir))["S12 · Biosecurity gate"] == "not run"


def test_s12_without_flags_has_no_review_box(run):
    run.write_artifact("s12", s12_payload("cleared", ["all passed"], []), inputs=[])
    assert 'class="review"' not in report.render(run.run_dir)


def test_stages_that_did_not_run_show_dashes_in_the_new_sections_never_zeros(run, tmp_path):
    run.write_artifact("s8", {"status": "not_run", "reasons": ["no candidates"], "candidates": [], "order_fasta": None, "order_fasta_missing": [],
                              "cost": None, "narratives": None}, inputs=[])
    run.write_artifact("s10", {"status": "not_run", "reasons": ["no candidates"], "candidates_included": [], "design": None, "study_file": None,
                               "outstanding_human_inputs": None, "falsification_rows": None}, inputs=[])
    run.write_artifact("s9plus", {"status": "not_run", "reasons": ["s9 not run"], "sessions": [], "summary": {"n_sessions": 0, "n_animated": 0, "n_skipped": 0}}, inputs=[])
    run.write_artifact("s12", {"state": "not_screened", "reasons": ["no order list was supplied"], "review_required": False, "review": [],
                               "order": {"status": "not_supplied"}, "provider_record": {"status": "not_supplied"}, "sequences": [], "limits": []}, inputs=[])
    attach(run, tmp_path)
    page = report.render(run.run_dir)
    states = {t.split(" ")[0]: s for t, s in chips(page).items()}
    assert [states[k] for k in ("S8", "S9+", "S9.5", "S10", "S12")] == ["not run"] * 5
    assert "no protocol: this run has no S8 candidates" in section(page, "protocol") and "<table" not in section(page, "protocol")
    assert "no study design in this run" in section(page, "animal") and "<table" not in section(page, "animal")
    assert 'class="review"' not in page and "Ordered sequences" not in page
    s12 = part(section(page, "prereg"), "S12 · Biosecurity gate")
    assert row(s12, "Gate state")[1] == "not_screened" and row(s12, "Order list")[1] == "not_supplied"
    a = part(section(page, "robot"), "S9+ · Protocol animation", "S9.5 · Fine-tuned")
    assert [row(a, label)[1] for label in ("Animations written", "Sessions considered", "Sessions skipped")] == [NR] * 3


def test_hostile_strings_in_the_new_sections_are_neutralised_and_the_page_stays_self_contained(run, tmp_path):
    add_wetlab(run)
    attach(run, tmp_path, evidence=str(EVIDENCE), manifest=str(TRAIN_MANIFEST))
    page = report.render(run.run_dir)
    assert BAD not in page and page.count(html.escape(BAD)) >= 6          # S8 name and falsifier, S9 sim error, S10 strain, S12 review flag
    assert "<script" not in page.lower() and "<iframe" not in page.lower() and "<img" not in page.lower()
    assert not re.findall(r'(?:src|href)="(?!#)', page) and "https://" not in page and "http://" not in page
    assert html.escape(BAD) in section(page, "protocol") and html.escape(BAD) in section(page, "animal")


def test_the_default_output_is_report_html_inside_the_run_folder(run, tmp_path, monkeypatch, capsys):
    cwd = tmp_path / "elsewhere"
    cwd.mkdir()
    monkeypatch.chdir(cwd)
    assert report.main([str(run.run_dir)]) == 0
    out = run.run_dir / "report.html"
    assert out.is_file() and f"wrote {out}" in capsys.readouterr().out and list(cwd.iterdir()) == []
    when = re.search(r"Generated \(UTC\)</td><td>([^<]*)", out.read_text(encoding="utf-8")).group(1)
    assert out.read_text(encoding="utf-8") == report.render(run.run_dir, now=when)           # a report.html in the folder changes nothing
    assert run.verify() == [] and report.main([str(run.run_dir), "--out", str(cwd / "x.html")]) == 0 and (cwd / "x.html").is_file()
