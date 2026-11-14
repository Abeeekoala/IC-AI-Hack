"""`vaxforge report`: assertions on the rendered HTML (states, "not run" dashes, escaping, the fixture label, the sources)."""
from __future__ import annotations

import hashlib
import html
import re
from pathlib import Path

import pytest

from vaxforge import cli, report
from vaxforge.artifacts import RunStore

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
BAD = "<script>alert(1)</script>"
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


def test_other_stage_contents_are_not_rendered_so_no_assay_value_can_appear(run):
    run.write_artifact("s8", {"protocol": {"predicted_elisa_od450": 1.987}, "falsifier": "no titre rise"}, inputs=[])
    page = report.render(run.run_dir)
    assert "1.987" not in page and "no titre rise" not in page
    assert "Payload fields: <code>falsifier, protocol</code>" in section(page, "protocol")


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
    assert report.main([str(run.run_dir)]) == 0 and (tmp_path / "report.html").is_file()           # default --out
    missing = tmp_path / "nope"
    assert cli.main(["report", str(missing)]) == 1 and not missing.exists()                          # RunStore would have created it
    assert "is not a folder" in capsys.readouterr().err
    assert report.main([str(run.run_dir), "--out", str(tmp_path / "no_such_dir" / "r.html")]) == 1
