"""Stream C stages on the artifact contract: S8, S9, S9+ and S10 `run(store, config)` over a COPY of fixtures/nmb_fixture.

The fixture has only S0-S2, so candidates are the S2 STAND-IN: proteins a person names (`streamc.proteins`).
Stream B's S3-S7 route is in test_streamc_upstream.py. Nothing here modifies the committed fixture; every test works on a copy under `tmp_path`.

Statuses (see docs/streamc.md): a stage with nothing to work on writes artifact `ok` + `payload.status == "not_run"` + `payload.reasons`
(never `needs_user_input`: `vaxforge run` stops on any artifact status but `ok`); `failed` stays for real defects.

Real Opentrons simulations (S9 `ok`, S9+) are `needs_ot` tests: one protein, about 20 s per S9 run. Everything else
switches the simulation off (`streamc.simulate_runs: false`), which is the UNVERIFIED -> `ok` + `not_run` path.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaxforge import cli, s8_protocol, s9_robot, s9plus_animate, s10_animal
from vaxforge.artifacts import REQUIRED_PAYLOAD_KEYS, ContractError, RunStore, iter_file_refs
from vaxforge.config import Config, ConfigError, load_config
from vaxforge.fasta import read_fasta
from vaxforge.s8_protocol import Candidate, Protocol, build_protocol, to_json, validate_protocol
from vaxforge.s9_robot import RULES
from vaxforge.s9_sessions import PLACEHOLDER_BANNER, RobotParams
from vaxforge.s9_validate import find_ot_python, static_lint
from vaxforge.s10_animal import ArmRole, StudyDesign, outstanding_human_inputs, validate_design
from vaxforge.streamc_adapter import (
    STAND_IN_WARNING,
    ProteinSelectionError,
    _candidates_from_upstream,
    load_inputs_from_store,
    match_proteins,
    payload_keys_applied,
)
from vaxforge.streamc_models import RANK_SOURCE_USER_SELECTION

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
needs_ot = pytest.mark.skipif(find_ot_python() is None, reason="no Opentrons 7.5.0 interpreter (set VAXFORGE_OT_PYTHON)")

FHBP, NADA, NHBA = "sp|Q9JXV4|FHBP_NEIMB", "sp|Q9JXK7|NADA1_NEIMB", "sp|Q7DD37|NHBA_NEIMB"
TITLE = "# Animal immunogenicity study proposal - Neisseria meningitidis"
S12_NOT_EVIDENCED = "S12 biosecurity gate NOT EVIDENCED"


def copy_run(dest: Path) -> RunStore:
    """A private copy of the committed S0-S2 fixture (the fixture itself is never written to)."""
    shutil.copytree(FIXTURE, dest)
    return RunStore(dest, cache_dir=dest.parent / "cache")


def cfg(**streamc) -> Config:
    return Config(dict(streamc=streamc))


def vaxforge(*args):
    """`python -m vaxforge ...` in a subprocess: (returncode, stdout, stderr)."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    p = subprocess.run([sys.executable, "-m", "vaxforge", *map(str, args)], capture_output=True, text=True, cwd=ROOT, env=env, timeout=300)
    return p.returncode, p.stdout, p.stderr


@pytest.fixture
def store(tmp_path):
    return copy_run(tmp_path / "run")


@pytest.fixture(scope="module")
def nosim(tmp_path_factory):
    """S8, S9 (simulation switched off) and S10 over three named proteins, run once and shared."""
    st = copy_run(tmp_path_factory.mktemp("nosim") / "run")
    c = cfg(proteins=["fhbp", "NADA", "q7dd37"], top_k=3, simulate_runs=False)
    return SimpleNamespace(store=st, cfg=c, s8=s8_protocol.run(st, c), s9=s9_robot.run(st, c), s10=s10_animal.run(st, c))


@pytest.fixture(scope="module")
def simulated(tmp_path_factory):
    """S8, S9 (real Opentrons simulation), S9+ and S10 for one protein, run once and shared by the needs_ot tests."""
    st = copy_run(tmp_path_factory.mktemp("sim") / "run")
    c = cfg(proteins=["fhbp"], top_k=1)
    s8a, s9a = s8_protocol.run(st, c), s9_robot.run(st, c)
    return SimpleNamespace(store=st, cfg=c, s8=s8a, s9=s9a, s9plus=s9plus_animate.run(st, c), s10=s10_animal.run(st, c))


# =========================================================================== the adapter reads a RunStore


def test_the_pathogen_comes_from_s0(store):
    p = load_inputs_from_store(store, cfg(proteins=["fhbp"])).pathogen
    taxon = store.read_artifact("s0").payload["taxon"]
    assert (p.name, p.taxid) == (taxon["scientific_name"], taxon["taxid"]) == ("Neisseria meningitidis", 487)
    # no artifact says either of these, so they stay unknown (None) instead of being inferred
    assert p.susceptible_small_animal_model is None and p.correlate_of_protection is None


@pytest.mark.parametrize(
    "name",
    ["fhbp", "FHBP", "fhbP", "FHBP_NEIMB", "fhbp_neimb", "Q9JXV4", "q9jxv4", "sp|Q9JXV4|FHBP_NEIMB", "SP|q9jxv4|fhbp_neimb", " fhbp "],
)
def test_a_protein_can_be_named_by_gene_entry_name_accession_or_protein_id_in_any_case(store, name):
    (c,) = load_inputs_from_store(store, cfg(proteins=[name])).candidates
    assert c.candidate_id == FHBP


def test_candidates_are_built_from_s2_and_the_proteome_in_the_order_given(store):
    inputs = load_inputs_from_store(store, cfg(proteins=["nada", "nhba", "fhbp"]))
    assert [c.candidate_id for c in inputs.candidates] == [NADA, NHBA, FHBP]
    assert [c.rank for c in inputs.candidates] == [1, 2, 3]  # the order given
    assert {c.rank_source for c in inputs.candidates} == {RANK_SOURCE_USER_SELECTION}
    assert inputs.rank_source == RANK_SOURCE_USER_SELECTION and inputs.stages == ["s0", "s2"]
    fasta = {r.id: r.seq for r in read_fasta(FIXTURE / "data" / "proteome.faa")}
    s2 = {p["protein_id"]: p for p in store.read_artifact("s2").payload["proteins"]}
    for c in inputs.candidates:
        assert c.sequence == fasta[c.candidate_id] and c.length_aa == len(c.sequence) == s2[c.candidate_id]["length"]
        assert c.localisation == s2[c.candidate_id]["localisation"]["label"]
        # everything only S3-S7 could fill stays empty
        assert c.protegenicity is None and c.epitopes == [] and c.pc90 is None and c.coverage_by_population == {}
        assert c.structure_confidence is None and c.structure_validators == [] and c.evidence_refs == []
    assert "Factor H binding protein" in inputs.candidates[2].protein_name  # readable, not the bare FASTA id
    assert inputs.warnings[0] == STAND_IN_WARNING
    assert inputs.selection["requested"] == ["nada", "nhba", "fhbp"] and inputs.selection["used"] == [NADA, NHBA, FHBP]


def test_the_stand_in_warning_has_the_agreed_text():
    assert STAND_IN_WARNING == ("S3-S7 not available: candidates are an explicit user selection from S2, "
                                "rank is the order given, not a prediction")


def test_a_chosen_protein_that_s2_does_not_call_accessible_gets_a_warning_and_keeps_its_label(store):
    rows = store.read_artifact("s2").payload["proteins"]
    odd = next(p for p in rows if p["localisation"]["accessibility"] != "accessible")
    inputs = load_inputs_from_store(store, cfg(proteins=[odd["protein_id"]]))
    (w,) = [x for x in inputs.warnings if odd["protein_id"] in x]
    assert "no S3-S7 filter has screened it" in w and odd["localisation"]["label"] in w
    assert inputs.candidates[0].localisation == odd["localisation"]["label"]  # "Unknown" stays a value, never dropped


def test_an_unknown_name_is_an_error_that_lists_close_matches(store):
    with pytest.raises(ProteinSelectionError) as exc:
        load_inputs_from_store(store, cfg(proteins=["fhbq"]))
    e = exc.value
    assert e.reason == "unknown_protein" and "'fhbq'" in str(e) and "FHBP_NEIMB" in str(e) and "Q9JXV4" in str(e)
    assert [s["protein_id"] for s in e.suggestions] == [FHBP] and e.stages == ["s0", "s2"]
    with pytest.raises(ProteinSelectionError, match="No close match"):
        load_inputs_from_store(store, cfg(proteins=["qqqqqqqq"]))


ROWS = [
    dict(protein_id="sp|A1|X_ONE", accession="A1", entry_name="X_ONE", gene="geneA", description="one"),
    dict(protein_id="sp|B2|X_TWO", accession="B2", entry_name="X_TWO", gene="GENEA", description="two"),
    dict(protein_id="sp|C3|X_THREE", accession="C3", entry_name="X_THREE", gene="geneC", description="three"),
]


def test_an_ambiguous_name_is_refused_not_guessed():
    with pytest.raises(ProteinSelectionError, match="ambiguous") as exc:
        match_proteins(ROWS, ["genea"])
    assert exc.value.reason == "ambiguous_protein" and len(exc.value.suggestions) == 2
    assert match_proteins(ROWS, ["A1", "x_three"]) == [0, 2]  # unambiguous names still resolve


def test_naming_a_protein_twice_is_refused():
    with pytest.raises(ProteinSelectionError, match="already selected") as exc:
        match_proteins(ROWS, ["a1", "sp|A1|X_ONE"])
    assert exc.value.reason == "duplicate_protein"


def test_top_k_is_applied_after_every_name_is_checked_and_the_cut_is_reported(store):
    four = cfg(proteins=["fhbp", "nada", "nhba", "porA"])
    cut = load_inputs_from_store(store, four, top_k=2)
    assert [c.candidate_id for c in cut.candidates] == [FHBP, NADA]
    assert any("only the first 2 are used" in w and "OMPA_NEIMB" in w for w in cut.warnings)
    assert len(load_inputs_from_store(store, four).candidates) == 3  # streamc.top_k defaults to 3
    assert len(load_inputs_from_store(store, cfg(proteins=["fhbp", "nada"], top_k=1)).candidates) == 1  # ... or comes from the config
    with pytest.raises(ProteinSelectionError):  # a typo beyond the cut is still caught
        load_inputs_from_store(store, cfg(proteins=["fhbp", "nada", "bogus"]), top_k=1)


def test_proteins_can_be_passed_as_an_argument_or_as_one_comma_separated_string(store):
    assert load_inputs_from_store(store, None, proteins=["fhbp"]).candidates[0].candidate_id == FHBP
    two = load_inputs_from_store(store, cfg(proteins="nada, fhbp")).candidates
    assert [c.candidate_id for c in two] == [NADA, FHBP]
    assert load_inputs_from_store(store, cfg(proteins=["nada"]), proteins=["fhbp"]).candidates[0].candidate_id == FHBP  # argument wins


def test_the_upstream_hook_returns_none_when_no_s3_s7_artifact_exists(store):
    assert _candidates_from_upstream(store) is None  # the S2 stand-in applies only then (test_streamc_upstream.py: what happens when one exists)


def test_missing_upstream_artifacts_raise_contract_errors(tmp_path):
    st = copy_run(tmp_path / "run")
    (st.run_dir / "s2_annotate.json").unlink()
    with pytest.raises(ContractError, match="s2 artifact not found"):
        load_inputs_from_store(st, cfg(proteins=["fhbp"]))
    (st.run_dir / "s0_taxonomy.json").unlink()
    with pytest.raises(ContractError, match="s0 artifact not found"):
        s8_protocol.run(st, cfg(proteins=["fhbp"]))


# =========================================================================== nothing to work on -> ok + payload.status "not_run"


@pytest.mark.parametrize("stage", [s8_protocol, s10_animal], ids=["s8", "s10"])
def test_no_selection_is_ok_with_payload_status_not_run_and_s2_suggestions_and_never_picks_for_you(store, stage):
    art = stage.run(store, cfg())
    assert art.status == "ok" and art.payload["status"] == "not_run"  # `ok`: vaxforge run stops on any other artifact status
    assert art.payload["selection"] == dict(source="none", reason="no_selection")
    assert art.payload["pathogen"] == dict(scientific_name="Neisseria meningitidis", taxid=487)
    reasons = art.payload["reasons"]
    assert len(reasons) == 1 and reasons[0].startswith("No candidates selected") and "streamc.proteins" in reasons[0] and "s3_rank" in reasons[0]
    assert art.warnings == [f"{stage.STAGE.upper()} not run: {reasons[0]}"]  # the same text, first and only warning
    sugg = art.payload["suggestions"]
    assert 1 <= len(sugg) <= 5
    for s in sugg:  # strong/moderate virulence evidence AND accessible: the rule for what is offered
        assert s["virulence_evidence"] in ("strong", "moderate") and s["accessibility"] == "accessible" and s["gene"] in reasons[0]
    if stage is s8_protocol:
        assert art.payload["candidates"] == []
    else:
        assert art.payload["design"] is None and art.payload["study_file"] is None and art.payload["candidates_included"] == []
        assert art.payload["outstanding_human_inputs"] is None  # not computed: None, never a made-up 0
    assert "needs_user_input" not in json.dumps(art.data)
    assert store.read_artifact(art.stage).require_ok().digest == art.digest  # nothing downstream is told "upstream failed"
    assert art.inputs == dict(s0=store.digest_of("s0"), s2=store.digest_of("s2"))
    assert store.verify() == []


def test_an_unknown_protein_name_in_a_stage_is_not_run_with_close_matches_not_a_crash(store):
    art = s8_protocol.run(store, cfg(proteins=["fhbp", "nadaa"]))
    assert art.status == "ok" and art.payload["status"] == "not_run" and art.payload["selection"]["reason"] == "unknown_protein"
    assert "NADA1_NEIMB" in art.payload["reasons"][0] and art.warnings[0].startswith("S8 not run: Unknown protein 'nadaa'")
    assert [s["protein_id"] for s in art.payload["suggestions"]] == [NADA]
    assert art.inputs == dict(s0=store.digest_of("s0"), s2=store.digest_of("s2"))
    assert store.verify() == []


def test_s9_and_s9plus_follow_an_s8_that_did_not_run_instead_of_failing(store):
    s8a = s8_protocol.run(store, cfg())  # nothing named: S8 is ok + not_run
    s9a = s9_robot.run(store, cfg())
    assert s9a.status == "ok" and s9a.payload["status"] == "not_run" and s9a.inputs == dict(s8=s8a.digest)
    assert s9a.payload["reasons"][0].startswith("s8 did not run (No candidates selected. S3-S7 (ranking") and "payload.reasons of s8_protocol.json" in s9a.payload["reasons"][0]
    assert len(s9a.payload["reasons"][0]) < 260  # S8's full message (with the S2 suggestions) is not repeated by every later stage
    assert s9a.warnings[0] == "S9 not run: " + s9a.payload["reasons"][0] and PLACEHOLDER_BANNER in s9a.warnings
    assert s9a.payload["candidates"] == [] and s9a.payload["summary"] == dict(n_candidates=0, n_sessions=0, outcomes={}, all_pass=False)
    assert s9a.producer["tools"] == dict(opentrons="not_run")
    plus = s9plus_animate.run(store, cfg())
    assert plus.status == "ok" and plus.payload["status"] == "not_run" and plus.inputs == dict(s9=s9a.digest)
    assert plus.payload["reasons"][0].startswith("s9 did not run (s8 did not run (No candidates selected.") and len(plus.payload["reasons"][0]) < 260
    assert plus.warnings[0] == "S9+ not run: " + plus.payload["reasons"][0] and plus.payload["sessions"] == []
    assert store.verify() == []


# =========================================================================== S8


def test_s8_writes_one_protocol_per_candidate_on_the_contract(store):
    art = s8_protocol.run(store, cfg(proteins=["fhbp", "nada"], top_k=3))
    assert art.status == "ok" and art.data["schema"] == "vaxforge/s8_protocol/v1" and art.data["stage"] == "s8"
    assert art.producer["module"] == "vaxforge.s8_protocol"
    assert art.inputs == dict(s0=store.digest_of("s0"), s2=store.digest_of("s2"))  # what it read, not DEFAULT_INPUTS (s4-s7)
    pl = art.payload
    assert pl["pathogen"] == dict(scientific_name="Neisseria meningitidis", taxid=487)
    assert pl["selection"]["source"] == "s2_stand_in"
    assert [(c["candidate_id"], c["rank"], c["rank_source"]) for c in pl["candidates"]] == [
        (FHBP, 1, RANK_SOURCE_USER_SELECTION), (NADA, 2, RANK_SOURCE_USER_SELECTION)]
    fasta = {r.id: r.seq for r in read_fasta(FIXTURE / "data" / "proteome.faa")}
    for c in pl["candidates"]:
        proto = Protocol.model_validate(c["protocol"])
        assert proto.candidate_id == c["candidate_id"] and validate_protocol(proto) == [] and proto.falsifier
        assert c["sequence_sha256"] == hashlib.sha256(fasta[c["candidate_id"]].encode()).hexdigest()
        assert c["length_aa"] == len(fasta[c["candidate_id"]]) and c["localisation"] == "OuterMembrane"
    first = load_inputs_from_store(store, cfg(proteins=["fhbp"])).candidates[0]
    # The stage adds only OPTIONAL fields to the template (cost, per-step quantities and narratives; tests/wetlab/test_s8_*.py): without them the
    # protocol is exactly build_protocol's - steps, controls, gates, falsifier and all.
    staged, template = pl["candidates"][0]["protocol"], build_protocol(first).model_dump(mode="json")
    for body in (staged, template):
        for key in ("cost", "cost_gbp_range", "estimate_basis"):
            body.pop(key)
        for step in body["steps"]:
            for key in ("quantities", "narrative", "narrative_source"):
                step.pop(key)
    assert staged == template
    assert art.warnings == [STAND_IN_WARNING]
    assert art.params == dict(proteins=["fhbp", "nada"], top_k=3, readouts=["elisa", "rt_qpcr"], endotoxin_limit_eu_per_ug=None,
                              robot_params=RobotParams().model_dump(mode="json"), prices={}, study={}, s8_backend="template", s8_model=None)  # what the added fields come from
    assert store.verify() == [] and store.read_artifact("s8").digest == art.digest


def test_s8_shortlists_the_top_three_by_default(store):
    art = s8_protocol.run(store, cfg(proteins=["fhbp", "nada", "nhba", "porA", "tbp1"]))
    assert [c["candidate_id"] for c in art.payload["candidates"]] == [FHBP, NADA, NHBA]
    assert any("only the first 3 are used" in w for w in art.warnings)


def test_s8_settings_come_from_config(store):
    art = s8_protocol.run(store, cfg(proteins=["fhbp"], readouts="elisa,facs", endotoxin_limit_eu_per_ug=0.5))
    steps = art.payload["candidates"][0]["protocol"]["steps"]
    assert [s["kind"] for s in steps if s["kind"] in ("elisa", "rt_qpcr", "facs")] == ["elisa", "facs"]
    assert "<= 0.5 EU/ug" in next(s for s in steps if s["kind"] == "lal_assay")["gate"]
    assert art.params["readouts"] == ["elisa", "facs"] and art.params["endotoxin_limit_eu_per_ug"] == 0.5


def test_s8_does_not_invent_an_endotoxin_limit(store):
    steps = s8_protocol.run(store, cfg(proteins=["fhbp"])).payload["candidates"][0]["protocol"]["steps"]
    assert "NOT YET SET" in next(s for s in steps if s["kind"] == "lal_assay")["gate"]


@pytest.mark.parametrize(
    "bad",
    [dict(top_k=0), dict(top_k="three"), dict(readouts=["western"]), dict(endotoxin_limit_eu_per_ug="lots")],
    ids=["top_k_zero", "top_k_text", "readout", "limit"],
)
def test_bad_s8_settings_are_config_errors(store, bad):
    with pytest.raises(ConfigError):
        s8_protocol.run(store, cfg(proteins=["fhbp"], **bad))


def test_the_streamc_section_of_config_yaml_holds_exactly_the_code_defaults(tmp_path):
    """config.yaml's `streamc:` section only documents the keys; every value must equal what the code does without it."""
    section = load_config(ROOT / "config.yaml").get("streamc")
    assert section == dict(proteins=None, top_k=3, readouts=["elisa", "rt_qpcr"], endotoxin_limit_eu_per_ug=None, backend="template", model=None,
                           simulate_runs=True, s12_status="pending", robot_params={}, ot_python=None, study={}, prices={}, s8_backend="template", s8_model=None)
    with_section, without = copy_run(tmp_path / "a"), copy_run(tmp_path / "b")
    named = dict(proteins=["fhbp", "nada"])
    for module in (s8_protocol, s9_robot, s10_animal):  # S9 with the simulator switched off in BOTH (this test is not about simulating)
        extra = dict(simulate_runs=False) if module is s9_robot else {}
        a = module.run(with_section, Config(dict(streamc={**section, **named, **extra})))
        b = module.run(without, Config(dict(streamc={**named, **extra})))
        assert a.digest == b.digest, module.__name__


def test_our_structural_check_is_applied_only_while_a_stage_writes(store, tmp_path):
    """REQUIRED_PAYLOAD_KEYS is process-global: stream A's own tests write toy s8/s9 payloads, so none of ours may stay registered."""
    before = dict(REQUIRED_PAYLOAD_KEYS)
    for stage in (s8_protocol, s9_robot, s10_animal):
        stage.run(store, cfg(proteins=["fhbp"], top_k=1, simulate_runs=False))
    s9plus_animate_keys = s9plus_animate.PAYLOAD_KEYS
    assert dict(REQUIRED_PAYLOAD_KEYS) == before
    toy = RunStore(tmp_path / "toy")
    toy.write_artifact("s8", dict(protocol=[]), inputs=[])  # A's toy payload is accepted when no stream C stage is writing
    with payload_keys_applied("s8", s8_protocol.PAYLOAD_KEYS):  # ... and refused while the check is applied
        with pytest.raises(ContractError, match="s8 payload is missing required keys"):
            toy.write_artifact("s8", dict(protocol=[]), inputs=[])
    assert dict(REQUIRED_PAYLOAD_KEYS) == before and s9plus_animate_keys == ("sessions", "summary")


def test_s8_handles_every_protein_of_the_fixture(store):
    ids = [p["protein_id"] for p in store.read_artifact("s2").payload["proteins"]]
    art = s8_protocol.run(store, cfg(proteins=ids, top_k=len(ids)))
    assert art.status == "ok" and [c["candidate_id"] for c in art.payload["candidates"]] == ids and len(ids) == 40
    assert [c["rank"] for c in art.payload["candidates"]] == list(range(1, 41))


# =========================================================================== S9 without a simulator


def test_s9_without_a_simulator_is_ok_and_not_run_with_the_code_generated_but_not_simulated(nosim):
    art = nosim.s9
    assert art.status == "ok" and art.payload["status"] == "not_run"  # `ok`: a plain vaxforge run must not stop here
    assert art.payload["reasons"] == [art.warnings[0].removeprefix("S9 not run: ")]  # the same text, first
    assert art.warnings[0].startswith("S9 not run: code generated, not simulated: simulation is switched off")
    assert "VAXFORGE_OT_PYTHON" in art.warnings[0] and "streamc.simulate_runs" in art.warnings[0] and "UNVERIFIED is not a pass" in art.warnings[0]
    assert any(PLACEHOLDER_BANNER == w for w in art.warnings) and any(S12_NOT_EVIDENCED in w for w in art.warnings)
    assert art.producer["tools"] == dict(opentrons="not_run")  # never a version that was not run
    assert art.payload["simulator"] == dict(simulated=False, opentrons_version=None)
    sessions = [s for c in art.payload["candidates"] for b in c["backends"] for s in b["sessions"]]  # the per-session summary and sidecars are kept
    assert {s["outcome"] for s in sessions} == {"UNVERIFIED"} and art.payload["summary"]["all_pass"] is False
    assert art.payload["summary"]["outcomes"] == dict(UNVERIFIED=len(sessions)) and all(s["files"]["code"] for s in sessions)
    assert nosim.store.verify() == []


def test_s9plus_after_an_s9_that_was_not_simulated_is_not_run_not_a_contract_error(tmp_path):
    st = copy_run(tmp_path / "run")
    c = cfg(proteins=["fhbp"], top_k=1, simulate_runs=False)
    s8_protocol.run(st, c)
    s9a = s9_robot.run(st, c)
    plus = s9plus_animate.run(st, c)
    assert s9a.payload["status"] == "not_run"
    assert plus.status == "ok" and plus.payload["status"] == "not_run" and plus.inputs == dict(s9=s9a.digest)
    assert plus.payload["reasons"][0].startswith("s9 did not run (code generated, not simulated: simulation is switched off")  # S9's own reason, passed on
    assert plus.payload["sessions"] == [] and plus.payload["summary"] == dict(n_sessions=0, n_animated=0, n_skipped=0)
    assert plus.warnings[0] == "S9+ not run: " + plus.payload["reasons"][0] and st.verify() == []
    assert not (st.run_dir / "data" / "s9plus").exists()  # nothing was animated, nothing was written


def test_s9plus_without_an_s9_artifact_is_not_run(store):
    plus = s9plus_animate.run(store, cfg())
    assert plus.status == "ok" and plus.payload["status"] == "not_run" and plus.inputs == {}
    assert plus.payload["reasons"][0].startswith("no s9 artifact in this run") and plus.warnings[0] == "S9+ not run: " + plus.payload["reasons"][0]
    assert store.verify() == []


def test_s9_without_a_simulator_but_with_simulation_on_says_none_was_found(store, monkeypatch):
    monkeypatch.setattr("vaxforge.s9_validate.find_ot_python", lambda: None)  # grade_source looks it up here ...
    monkeypatch.setattr("vaxforge.s9_robot.find_ot_python", lambda: None)  # ... and compile_protocol here
    c = cfg(proteins=["fhbp"], top_k=1)
    s8_protocol.run(store, c)
    art = s9_robot.run(store, c)
    assert art.status == "ok" and art.payload["status"] == "not_run"
    assert art.warnings[0].startswith("S9 not run: code generated, not simulated: no Opentrons simulator was found")
    assert "VAXFORGE_OT_PYTHON" in art.warnings[0] and "streamc.simulate_runs" not in art.warnings[0]


def test_s9_gives_each_candidate_its_own_folder_and_a_file_ref_for_every_sidecar(nosim):
    cands = nosim.s9.payload["candidates"]
    assert [c["candidate_id"] for c in cands] == [FHBP, NADA, NHBA]
    assert [c["dir_name"] for c in cands] == ["sp_q9jxv4_fhbp_neimb", "sp_q9jxk7_nada1_neimb", "sp_q7dd37_nhba_neimb"]
    suffix = dict(code=".py", setup=".setup.md", plan=".plan.json", grade=".grade.json")
    for c in cands:
        (b,) = c["backends"]
        assert b["label"] == "template" and b["dir_name"] == "template" and b["sessions"]
        assert c["report"]["path"] == f"data/s9/{c['dir_name']}/robot_report.json"
        assert c["workflow"]["path"] == f"data/s9/{c['dir_name']}/workflow.md" and c["coverage"]
        for s in b["sessions"]:
            assert s["files"]["sim"] is None  # nothing was simulated, so there is no simulation file
            for kind, ext in suffix.items():
                assert s["files"][kind]["path"] == f"data/s9/{c['dir_name']}/template/{s['session_id']}{ext}"
            assert s["code_sha256"] and s["plan_sha256"] and s["grade"]["verdict"] == "UNVERIFIED"


def test_every_file_reference_resolves_and_its_hash_matches(nosim):
    st = nosim.store
    for art, expected in ((nosim.s9, None), (nosim.s10, 1)):
        refs = list(iter_file_refs(art.payload))
        assert refs and (expected is None or len(refs) == expected)
        for ref in refs:
            assert ref["root"] == "run" and not Path(ref["path"]).is_absolute() and ".." not in Path(ref["path"]).parts
            data = st.resolve(ref).read_bytes()
            assert st.verify_ref(ref) is None
            assert hashlib.sha256(data).hexdigest() == ref["sha256"] and len(data) == ref["bytes"]
    sessions = sum(len(b["sessions"]) for c in nosim.s9.payload["candidates"] for b in c["backends"])
    assert len(list(iter_file_refs(nosim.s9.payload))) == 3 * 3 + 4 * sessions  # 3 per candidate + code/setup/plan/grade per session


def test_the_code_hash_in_the_payload_is_the_hash_of_the_written_file(nosim):
    for c in nosim.s9.payload["candidates"]:
        for s in c["backends"][0]["sessions"]:
            assert s["code_sha256"] == s["files"]["code"]["sha256"]


def test_the_chain_is_valid_and_each_stage_records_the_digests_it_read(nosim):
    st = nosim.store
    assert st.verify() == [] and st.stages_present() == ["s0", "s1", "s2", "s8", "s9", "s10"]
    reads_s0_s2 = dict(s0=st.digest_of("s0"), s2=st.digest_of("s2"))
    assert nosim.s8.inputs == reads_s0_s2 and nosim.s10.inputs == reads_s0_s2
    assert nosim.s9.inputs == dict(s8=nosim.s8.digest)
    for art, module in ((nosim.s8, "s8_protocol"), (nosim.s9, "s9_robot"), (nosim.s10, "s10_animal")):
        assert art.data["schema"] == f"vaxforge/{module}/v1" and art.producer["module"] == f"vaxforge.{module}"
        assert art.data["digest"] == st.read_artifact(art.stage).digest  # read_artifact re-hashes the file
    m = st.read_manifest()["stages"]
    assert (m["s8"]["status"], m["s9"]["status"], m["s10"]["status"]) == ("ok", "ok", "ok")  # s9: ok + payload.status not_run (no simulator)


def test_vaxforge_verify_passes_on_the_run_with_our_artifacts(nosim):
    code, out, err = vaxforge("verify", nosim.store.run_dir)
    assert code == 0, out + err
    assert "OK - digests, upstream chain and referenced files are consistent" in out
    assert all(s in out for s in ("s8", "s9", "s10"))


def test_rerunning_s8_with_other_proteins_makes_s9_stale(tmp_path):
    st = copy_run(tmp_path / "run")
    c = cfg(proteins=["fhbp"], simulate_runs=False)
    s8_protocol.run(st, c)
    s9_robot.run(st, c)
    assert st.verify() == []
    s8_protocol.run(st, cfg(proteins=["nada"], simulate_runs=False))
    assert any("s9: STALE" in p for p in st.verify())


def test_a_rerun_gives_the_same_digests_when_nothing_changed(nosim):
    for module, art in ((s8_protocol, nosim.s8), (s9_robot, nosim.s9), (s10_animal, nosim.s10)):
        assert module.run(nosim.store, nosim.cfg).digest == art.digest, module.__name__


def test_what_is_hashed_holds_no_local_path_and_no_timestamp(nosim):
    here = {str(nosim.store.run_dir), nosim.store.run_dir.as_posix(), str(nosim.store.run_dir.parent), nosim.store.run_dir.parent.as_posix()}
    for art in (nosim.s8, nosim.s9, nosim.s10):
        text = json.dumps([art.payload, art.params, art.warnings, art.producer], ensure_ascii=False)
        assert not any(h in text for h in here), art.stage
        assert "Users" not in text and not re.search(r"(?<![A-Za-z])[A-Za-z]:[\\/]", text), art.stage  # no C:\ or C:/ (https:// is fine)
        assert not re.search(r"\d{4}-\d\d-\d\dT\d\d:\d\d", text), art.stage


def test_a_rerun_of_s9_does_not_leave_old_files_behind(tmp_path):
    st = copy_run(tmp_path / "run")
    c = cfg(proteins=["fhbp"], top_k=1, simulate_runs=False)
    s8_protocol.run(st, c)
    first = s9_robot.run(st, c)
    stray = st.run_dir / "data" / "s9" / "sp_q9jxv4_fhbp_neimb" / "template" / "stale.txt"
    other = st.run_dir / "data" / "s9" / "no_longer_a_candidate"
    stray.write_text("old", encoding="utf-8")
    other.mkdir()
    (other / "x.txt").write_text("old", encoding="utf-8")
    again = s9_robot.run(st, c)
    assert not stray.exists() and not other.exists() and again.digest == first.digest
    assert (st.run_dir / "data" / "proteome.faa").is_file()  # only this stage's own folder is cleared


def test_s9_without_an_s8_artifact_is_not_run_and_a_failed_s8_is_still_an_error(store):
    art = s9_robot.run(store, cfg())
    assert art.status == "ok" and art.payload["status"] == "not_run" and art.inputs == {}
    assert art.payload["reasons"][0].startswith("no s8 artifact in this run") and art.warnings[0] == "S9 not run: " + art.payload["reasons"][0]
    assert store.verify() == []
    store.write_artifact("s8", dict(pathogen={}, selection={}, candidates=[]), status="failed", warnings=["protocol rule broken: x"], inputs=[])
    with pytest.raises(ContractError, match=r"s8 artifact has status 'failed': protocol rule broken: x"):  # a real defect upstream is not "nothing to do"
        s9_robot.run(store, cfg())


def test_a_failing_session_makes_s9_failed_with_one_warning_per_session(tmp_path):
    st = copy_run(tmp_path / "run")
    base = dict(proteins=["fhbp"], top_k=1)
    s8_protocol.run(st, cfg(**base))
    art = s9_robot.run(st, cfg(**base, ot_python=str(tmp_path / "no" / "such" / "python.exe")))  # an interpreter that does not exist
    assert art.status == "failed"
    sessions = [s for c in art.payload["candidates"] for b in c["backends"] for s in b["sessions"]]
    assert {s["outcome"] for s in sessions} == {"FAIL"} and {s["grade"]["sim_error_kind"] for s in sessions} == {"SimulatorUnavailable"}
    per_session = [w for w in art.warnings if w.startswith(f"{FHBP} / template / ")]
    assert len(per_session) == len(sessions) and all("FAIL - " in w and "SimulatorUnavailable" in w for w in per_session)
    assert art.warnings[0] == per_session[0]  # first, because require_ok() quotes it
    assert PLACEHOLDER_BANNER in art.warnings and any(S12_NOT_EVIDENCED in w for w in art.warnings)
    assert art.producer["tools"] == dict(opentrons="not_run") and art.payload["summary"]["all_pass"] is False
    with pytest.raises(ContractError, match="status 'failed'"):
        s9plus_animate.run(st, cfg())
    assert st.verify() == []


def test_s9_settings_are_checked(store):
    s8_protocol.run(store, cfg(proteins=["fhbp"], top_k=1))
    for bad in (dict(backend="gpt"), dict(s12_status="maybe"), dict(robot_params=dict(replicates=9))):
        with pytest.raises(ConfigError):
            s9_robot.run(store, cfg(simulate_runs=False, **bad))


def test_s9_code_survives_every_awkward_protein_name(store):
    """Real UniProt descriptions contain brackets, slashes and commas; the generated code must still lint clean."""
    c = cfg(proteins=["trmD", "potD-1", "NMB1420"], simulate_runs=False)
    s8_protocol.run(store, c)
    art = s9_robot.run(store, c)
    assert "tRNA (guanine-N(1)-)-methyltransferase" in art.payload["candidates"][0]["protein_name"]
    for cand in art.payload["candidates"]:
        for s in cand["backends"][0]["sessions"]:
            assert static_lint(store.resolve(s["files"]["code"]).read_text(encoding="utf-8")) == []
            assert s["grade"]["lint"] == [] and s["outcome"] == "UNVERIFIED"


# =========================================================================== S9 / S9+ with the real simulator


@needs_ot
def test_s9_all_pass_is_ok_and_records_the_simulator_version(simulated):
    art, st = simulated.s9, simulated.store
    assert art.status == "ok" and art.producer["tools"] == dict(opentrons="7.5.0")
    assert art.payload["simulator"] == dict(simulated=True, opentrons_version="7.5.0")
    (c,) = art.payload["candidates"]
    (b,) = c["backends"]
    n = len(b["sessions"])
    assert b["all_pass"] is True and b["tally"] == dict(PASS=n) and n >= 10
    for s in b["sessions"]:
        g = s["grade"]
        assert s["outcome"] == "PASS" and g["verdict"] == "PASS" and g["sim_ok"] is True and g["n_rule_violations"] == 0
        assert set(g["rule_counts"]) == set(RULES) and not any(g["rule_counts"].values())
        assert not [f for f in g["findings"] if f["severity"] == "error"] and g["lint"] == []
        assert s["files"]["sim"] is not None and st.verify_ref(s["files"]["sim"]) is None
        assert json.loads(st.resolve(s["files"]["sim"]).read_text(encoding="utf-8"))["ok"] is True
    assert art.payload["summary"] == dict(n_candidates=1, n_sessions=n, outcomes=dict(PASS=n), all_pass=True)
    assert art.warnings[0] == PLACEHOLDER_BANNER and S12_NOT_EVIDENCED in art.warnings[1] and len(art.warnings) == 2
    assert st.verify() == []


@needs_ot
def test_s9plus_writes_a_timeline_and_a_player_per_session(simulated):
    art, st = simulated.s9plus, simulated.store
    assert art.status == "ok" and art.inputs == dict(s9=simulated.s9.digest) and "status" not in art.payload  # a normal result: no payload.status
    n = simulated.s9.payload["summary"]["n_sessions"]
    assert art.payload["summary"] == dict(n_sessions=n, n_animated=n, n_skipped=0) and len(art.payload["sessions"]) == n
    for s in art.payload["sessions"]:
        assert s["ok"] is True and s["s9_outcome"] == "PASS" and s["backend"] == "template"
        folder = f"data/s9plus/sp_q9jxv4_fhbp_neimb/template/{s['session_id']}/"
        assert s["timeline"]["path"] == folder + "timeline.json" and s["html"]["path"] == folder + "protocol_animation.html"
        timeline = json.loads(st.resolve(s["timeline"]).read_text(encoding="utf-8"))
        assert timeline["schema"] == "vaxforge.timeline/1" and timeline["ok"] is True and timeline["frames"]
        html = st.resolve(s["html"]).read_text(encoding="utf-8")
        assert html.startswith("<!doctype html>") and "Content-Security-Policy" in html and "fetch(" not in html
    assert art.warnings == [s9plus_animate.TIMES_NOTE]
    assert len(list(iter_file_refs(art.payload))) == 2 * n and st.verify() == []


@needs_ot
def test_a_rerun_of_every_stage_gives_the_same_digest_with_the_real_simulator(simulated):
    for module, art in ((s8_protocol, simulated.s8), (s9_robot, simulated.s9), (s9plus_animate, simulated.s9plus), (s10_animal, simulated.s10)):
        assert module.run(simulated.store, simulated.cfg).digest == art.digest, module.__name__
    assert simulated.store.verify() == []


@needs_ot
def test_vaxforge_verify_passes_on_the_simulated_run(simulated):
    code, out, err = vaxforge("verify", simulated.store.run_dir)
    assert code == 0, out + err
    assert all(s in out for s in ("s8", "s9", "s9plus", "s10"))


# =========================================================================== S10


def test_s10_artifact_holds_the_design_and_a_file_ref_to_the_markdown(nosim):
    art, st = nosim.s10, nosim.store
    assert art.status == "ok" and art.data["schema"] == "vaxforge/s10_animal/v1"
    design = StudyDesign.model_validate(art.payload["design"])
    assert validate_design(design) == []
    assert art.payload["candidates_included"] == [FHBP, NADA, NHBA] == design.candidates_included
    assert art.payload["outstanding_human_inputs"] == len(outstanding_human_inputs(design)) > 0
    ref = art.payload["study_file"]
    assert ref["path"] == "data/s10/animal_study.md" and st.verify_ref(ref) is None
    md = st.resolve(ref).read_text(encoding="utf-8")
    assert md.startswith(TITLE) and "DESIGN ONLY" in md
    assert design.challenge_model.status == "unknown_check" and design.correlate.correlate is None  # never inferred
    assert design.pathogen == "Neisseria meningitidis" and design.pathogen_taxid == 487
    assert list(art.producer["tools"]) == ["scipy"]
    assert art.params["top_k"] == 3 and art.params["study_config"]["power"] == 0.8


def test_s10_rows_for_the_stand_in_candidates_are_not_testable_where_data_is_absent(nosim):
    pl = nosim.s10.payload
    rows = pl["design"]["falsification_map"]
    assert len(rows) == 3 * 6 and {r["upstream_stage"] for r in rows} == {"S3", "S5_T_cell", "S5_B_cell", "S6", "S7", "functional"}
    for r in rows:
        assert r["status"] == "not_testable_no_upstream_prediction", (r["candidate_id"], r["upstream_stage"])
        assert not r["upstream_prediction"] and not r["upstream_data"] and not r["falsified_if"] and not r["peptide_pools"]
    assert pl["falsification_rows"] == dict(total=18, by_status=dict(not_testable_no_upstream_prediction=18))
    assert any("not_testable_no_upstream_prediction" in w for w in nosim.s10.warnings)
    assert STAND_IN_WARNING in nosim.s10.warnings
    s3 = next(r for r in rows if r["upstream_stage"] == "S3")
    assert "No S3 prediction" in s3["note"] and "not a ranking" in s3["note"]


def test_s10_does_not_call_a_users_order_an_s3_rank(nosim):
    labels = [a["label"] for a in nosim.s10.payload["design"]["arms"] if a["role"] == ArmRole.CANDIDATE.value]
    assert labels and not any(re.search(r"S3 rank \d", label) for label in labels)  # "no S3 rank" is fine; "S3 rank 1" is a claim
    assert any(f"{FHBP}, user-selected #1, no S3 rank" in label for label in labels)
    assert "S3 ranks" not in json.dumps(nosim.s10.payload["design"])


def test_an_invalid_study_setting_makes_s10_failed_with_the_violation_first(store):
    art = s10_animal.run(store, cfg(proteins=["fhbp"], study=dict(species="mouse_wild_type", goal="hla_epitope_validation")))
    assert art.status == "failed"
    assert art.warnings[0].startswith("design validation: goal hla_epitope_validation requires HLA-transgenic mice")
    assert art.payload["design"] and art.payload["study_file"]["path"] == "data/s10/animal_study.md"  # still shown, so the problem can be read
    with pytest.raises(ContractError, match="status 'failed'"):
        store.read_artifact("s10").require_ok()


def test_study_settings_come_from_config_and_bad_ones_are_config_errors(store):
    art = s10_animal.run(store, cfg(proteins=["fhbp"], study=dict(
        power=0.9, species="mouse_hla_transgenic", goal="hla_epitope_validation", hla_transgenic_lines=["A2.DR1"])))
    design = art.payload["design"]
    assert art.status == "ok" and design["config"]["power"] == 0.9 and design["species_selection"]["species"] == "mouse_hla_transgenic"
    assert art.params["study_config"]["power"] == 0.9
    with pytest.raises(ConfigError, match="streamc.study"):
        s10_animal.run(store, cfg(proteins=["fhbp"], study=dict(power=80)))
    with pytest.raises(ConfigError, match="mapping"):
        s10_animal.run(store, cfg(proteins=["fhbp"], study="mouse"))


def test_s10_reads_no_s8_or_s9_output(store):
    art = s10_animal.run(store, cfg(proteins=["fhbp"]))  # no s8 / s9 artifact exists in this run
    assert art.status == "ok" and sorted(art.inputs) == ["s0", "s2"] and not store.has("s8") and not store.has("s9")
    assert store.verify() == []


def test_a_rerun_of_s10_replaces_its_folder(store):
    c = cfg(proteins=["fhbp"])
    first = s10_animal.run(store, c)
    stray = store.run_dir / "data" / "s10" / "stale.md"
    stray.write_text("old", encoding="utf-8")
    assert s10_animal.run(store, c).digest == first.digest and not stray.exists()


# =========================================================================== `vaxforge run` and the registered commands


def test_vaxforge_run_with_no_proteins_named_exits_zero_and_every_stage_says_not_run(tmp_path):
    """The case that used to stop `vaxforge run` at s8 (exit 1): a run with S0-S2 only and nothing named must not fail."""
    run = copy_run(tmp_path / "run").run_dir
    plain = tmp_path / "plain.yaml"
    plain.write_text("pipeline:\n  top_k: 20\n", encoding="utf-8")  # no streamc section; --top-k 20 must not reach stream C
    code, out, err = vaxforge("run", "--pathogen", "Neisseria meningitidis", "--out", run, "--config", plain,
                              "--stages", "s8,s9,s9plus,s10", "--top-k", "20")
    assert "Traceback" not in err and "Traceback" not in out
    assert code == 0, out + err
    for stage in ("s8", "s9", "s9plus", "s10"):
        assert re.search(rf"^{stage}\s+ok\b", out, re.M), (stage, out)
    assert "(verify: ok)" in out
    st = RunStore(run)
    assert st.stages_present() == ["s0", "s1", "s2", "s8", "s9", "s9plus", "s10"]
    for stage in ("s8", "s9", "s9plus", "s10"):
        art = st.read_artifact(stage)
        assert art.status == "ok" and art.payload["status"] == "not_run" and art.payload["reasons"], stage
    assert st.verify() == []


def test_vaxforge_run_with_proteins_in_the_config_writes_the_stages_and_verifies(tmp_path):
    run = copy_run(tmp_path / "run").run_dir
    conf = tmp_path / "streamc.yaml"
    conf.write_text("streamc:\n  proteins: [fhbp, nada]\n  top_k: 2\n", encoding="utf-8")
    code, out, err = vaxforge("run", "--pathogen", "Neisseria meningitidis", "--out", run, "--config", conf, "--stages", "s8,s10")
    assert code == 0, out + err
    assert re.search(r"^s8\s+ok", out, re.M) and re.search(r"^s10\s+ok", out, re.M) and "(verify: ok)" in out
    st = RunStore(run)
    assert [c["candidate_id"] for c in st.read_artifact("s8").payload["candidates"]] == [FHBP, NADA]
    assert vaxforge("verify", run)[0] == 0


@pytest.mark.parametrize("command", ["compile-protocol", "animal-study"])
def test_our_subcommands_are_registered_in_vaxforge_and_do_not_clash(command):
    code, out, err = vaxforge(command, "--help")
    assert code == 0 and "usage: vaxforge " + command in out, out + err
    assert "--config" in out  # the shared common flags are inherited


def test_the_readme_commands_parse():
    parser = cli.build_parser()
    a = parser.parse_args(["compile-protocol", "runs/nmb/protocol.json", "--target", "opentrons-ot2"])
    assert a.target == "opentrons-ot2" and a.source == "runs/nmb/protocol.json"
    b = parser.parse_args(["animal-study", "runs/nmb", "--species", "hla-transgenic", "--arms", "4", "--power", "0.8"])
    assert (b.run_dir, b.species, b.arms, b.power) == ("runs/nmb", "hla-transgenic", "4", 0.8)
    with pytest.raises(SystemExit):
        parser.parse_args(["compile-protocol", "p.json", "--target", "flex"])


def test_animal_study_on_a_run_folder_runs_the_s10_stage(tmp_path, capsys):
    run = copy_run(tmp_path / "run").run_dir
    code = cli.main(["animal-study", str(run), "--proteins", "fhbp,nada", "--species", "hla-transgenic", "--arms", "4", "--power", "0.8"])
    out = capsys.readouterr()
    assert code == 0, out.out + out.err
    st = RunStore(run)
    art = st.read_artifact("s10")
    cfg_ = art.payload["design"]["config"]
    assert art.status == "ok" and cfg_["power"] == 0.8 and cfg_["species"] == "mouse_hla_transgenic"
    assert art.payload["candidates_included"] == [FHBP, NADA] and "data/s10/animal_study.md" in out.out and st.verify() == []


def test_animal_study_on_a_run_folder_without_proteins_exits_two_and_says_what_to_do(tmp_path, capsys):
    run = copy_run(tmp_path / "run").run_dir
    assert cli.main(["animal-study", str(run)]) == 2  # the explicit command keeps the "nothing to do" exit code 2 ...
    out = capsys.readouterr()
    assert "No candidates selected" in out.err and "streamc.proteins" in out.err
    assert out.out.startswith("s10: ok, payload.status not_run")
    art = RunStore(run).read_artifact("s10")
    assert art.status == "ok" and art.payload["status"] == "not_run"  # ... while the artifact is `ok`, so vaxforge run goes on


def test_compile_protocol_on_a_run_folder_runs_the_s9_stage(tmp_path, capsys):
    st = copy_run(tmp_path / "run")
    s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1))
    assert cli.main(["compile-protocol", str(st.run_dir), "--target", "opentrons-ot2", "--no-simulate"]) == 2  # not simulated -> "nothing to do" 2
    assert "VAXFORGE_OT_PYTHON" in capsys.readouterr().err
    art = st.read_artifact("s9")
    assert art.status == "ok" and art.payload["status"] == "not_run"
    assert cli.main(["compile-protocol", str(st.run_dir / "s8_protocol.json"), "--no-simulate"]) == 2  # the artifact path works too
    assert st.verify() == []


def test_compile_protocol_on_a_bare_protocol_json_still_needs_out(tmp_path, capsys):
    proto = tmp_path / "protocol.json"
    proto.write_text(to_json(build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1))), encoding="utf-8")
    assert cli.main(["compile-protocol", str(proto), "--no-simulate"]) == 2
    assert "--out is required" in capsys.readouterr().err
    assert cli.main(["compile-protocol", str(proto), "--out", str(tmp_path / "o"), "--no-simulate"]) == 1  # UNVERIFIED is not a pass
    assert (tmp_path / "o" / "robot_report.json").is_file()
