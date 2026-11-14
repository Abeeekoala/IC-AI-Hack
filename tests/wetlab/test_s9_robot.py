"""S9 end to end: S8 protocol -> robot sessions -> simulate -> grade -> report.

Runs real Opentrons 7.5.0 simulations (skipped when no interpreter is available).
"""

import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaxforge.s8_protocol import Candidate, ProtocolConfig, StepKind, build_protocol
from vaxforge.s9_llm import LLMBackend, TemplateBackend
from vaxforge.s9_plan import CATALOG
from vaxforge.s9_robot import ProtocolInvalid, RobotReport, _cli, compile_protocol
from vaxforge.s9_sessions import HUMAN_ONLY, RobotParams, plan_sessions
from vaxforge.s9_validate import find_ot_python, static_lint

needs_ot = pytest.mark.skipif(find_ot_python() is None, reason="no Opentrons 7.5.0 interpreter (set VAXFORGE_OT_PYTHON)")

CAND = Candidate(candidate_id="c1", protein_name="fHbp", rank=1)
PROTO = build_protocol(CAND, ProtocolConfig(readouts=[StepKind.ELISA, StepKind.RT_QPCR, StepKind.FACS]))


@pytest.fixture(scope="module")
def compiled(tmp_path_factory):
    out = tmp_path_factory.mktemp("s9")
    return out, compile_protocol(PROTO, out, animate=True)


@needs_ot
def test_every_session_passes_every_check(compiled):
    out, rep = compiled
    (run,) = rep.runs
    assert run.label == "template" and run.all_pass, run.tally
    assert len(run.sessions) == len(plan_sessions(PROTO)) == 13
    for s in run.sessions:
        g = s.grade
        assert g.sim_ok and g.n_rule_violations == 0 and not g.lint
        assert not [f for f in g.findings if f.severity == "error"], (s.session_id, g.findings)
    assert rep.simulated and rep.opentrons_version == "7.5.0"


@needs_ot
def test_every_file_is_written_and_consistent(compiled):
    out, rep = compiled
    for s in rep.runs[0].sessions:
        for f in (s.code_file, s.setup_file, s.plan_file, s.sim_file, s.grade_file):
            assert f and not Path(f).is_absolute() and (out / f).exists(), (s.session_id, f)
        code = (out / s.code_file).read_text(encoding="utf-8")
        assert static_lint(code) == []
        assert f"plan-sha256: {s.plan_sha256}" in code
        assert "PLACEHOLDER PARAMETERS" in code and "S12 biosecurity gate NOT EVIDENCED" in code
        assert "PLACEHOLDER" in (out / s.setup_file).read_text(encoding="utf-8")
    assert (out / "robot_report.json").exists() and (out / "robot_report.md").exists() and (out / "workflow.md").exists()
    again = RobotReport.model_validate_json((out / "robot_report.json").read_text(encoding="utf-8"))
    assert again.candidate_id == "c1" and len(again.runs[0].sessions) == 13


@needs_ot
def test_every_robot_step_is_annotated_with_its_owner(compiled):
    out, rep = compiled
    for s in rep.runs[0].sessions:
        code = (out / s.code_file).read_text(encoding="utf-8")
        for sid in s.covers:
            step = next(x for x in PROTO.steps if x.step_id == sid)
            owner = {"full": "ROBOT", "partial": "ROBOT-PARTIAL", "manual": "HUMAN"}[step.automation.value]
            assert re.search(rf"ctx\.comment\(\"\[{sid}\] .* \| {owner} \| ", code), (s.session_id, owner)
        assert re.search(r'ctx\.pause\("HUMAN \[s\d\d\]: ', code)


@needs_ot
def test_coverage_matches_the_automatability_matrix(compiled):
    _out, rep = compiled
    cov = {c.step_id: c for c in rep.coverage}
    assert len(cov) == len(PROTO.steps)
    for s in PROTO.steps:
        c = cov[s.step_id]
        if s.kind in HUMAN_ONLY:
            assert c.owner == "HUMAN" and c.sessions == []
        else:
            assert c.sessions, s.kind
        if s.kind in (StepKind.ELISA,):
            assert len(c.sessions) == 2
    assert cov["s01"].owner == "HUMAN" and cov["s10"].owner == "HUMAN" and cov["s11"].owner == "ROBOT"
    assert all(c.human_remainder for c in rep.coverage)


@needs_ot
def test_animations_are_produced_from_the_simulations(compiled):
    out, rep = compiled
    for s in rep.runs[0].sessions:
        assert s.animation_error is None, (s.session_id, s.animation_error)
        assert s.animation_file and (out / s.animation_file).exists()


@needs_ot
def test_catalog_capacities_match_the_simulators_definitions(compiled):
    out, rep = compiled
    seen = set()
    for s in rep.runs[0].sessions:
        sim = json.loads((out / s.sim_file).read_text(encoding="utf-8"))
        for lw in sim["deck"]["labware"]:
            info = CATALOG[lw["load_name"]]
            wells = lw["wells"]
            assert len(wells) == info.rows * info.cols, lw["load_name"]
            assert wells["A1"]["max_volume_ul"] == info.well_max_ul, lw["load_name"]
            seen.add(lw["load_name"])
    assert seen == set(CATALOG)  # every catalogue entry is exercised by some session


@needs_ot
def test_output_is_reproducible(compiled, tmp_path):
    _out, rep = compiled
    rep2 = compile_protocol(PROTO, tmp_path, animate=False, simulate_runs=False)
    h1 = {s.session_id: s.code_sha256 for s in rep.runs[0].sessions}
    h2 = {s.session_id: s.code_sha256 for s in rep2.runs[0].sessions}
    assert h1 == h2


def test_without_a_simulator_the_outcome_is_unverified_not_pass(tmp_path):
    rep = compile_protocol(PROTO, tmp_path, simulate_runs=False, animate=False)
    assert {s.outcome for s in rep.runs[0].sessions} == {"UNVERIFIED"} and not rep.simulated
    assert not rep.runs[0].all_pass


def test_an_invalid_s8_protocol_is_refused(tmp_path):
    bad = PROTO.model_copy(deep=True)
    bad.falsifier = " "
    with pytest.raises(ProtocolInvalid, match="falsification"):
        compile_protocol(bad, tmp_path, simulate_runs=False)
    assert not (tmp_path / "robot").exists()


def test_s12_status_is_stated_in_the_report(tmp_path):
    rep = compile_protocol(PROTO, tmp_path, simulate_runs=False, animate=False, s12_status="cleared")
    assert "CLEARED" in rep.biosecurity
    assert "NOT EVIDENCED" in compile_protocol(PROTO, tmp_path / "b", simulate_runs=False, animate=False).biosecurity


def test_custom_parameters_flow_into_the_code(tmp_path):
    rep = compile_protocol(PROTO, tmp_path, params=RobotParams(stim_ul=15.0), simulate_runs=False, animate=False)
    code = (tmp_path / next(s for s in rep.runs[0].sessions if s.session_id == "s11_antigen_stimulation").code_file).read_text(encoding="utf-8")
    assert "p20.transfer(15," in code


# --------------------------------------------------------------------------- LLM path through the same validation


SOME = ["s02_plasmid_assembly", "s03_transformation", "s04_colony_pcr", "s05_expression_induction", "s06_lysis", "s08_endotoxin_removal", "s11_antigen_stimulation"]


def plans_by_id():
    return {p.session_id: p for p in plan_sessions(PROTO)}


def session_of(prompt):
    return re.match(r"Session: (\S+) - ", prompt).group(1)


def msg(text, stop_reason="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)


class ScriptedClient:
    """Stands in for a model: answers each session with `answer(session_id, template_code)`."""

    def __init__(self, answer):
        self.messages = self
        self.answer = answer
        self.templates = {}

    def create(self, **kw):
        sid = session_of(kw["messages"][0]["content"])
        if sid not in self.templates:
            from vaxforge.s9_plan import render_python

            plan = plans_by_id()[sid]
            info = {s.step_id: (s.title, {"full": "ROBOT", "partial": "ROBOT-PARTIAL", "manual": "HUMAN"}[s.automation.value], s.automation_note)
                    for s in PROTO.steps}
            self.templates[sid] = render_python(plan, step_info={k: info[k] for k in plan.covers})
        return self.answer(sid, self.templates[sid])


@needs_ot
def test_a_model_that_follows_the_spec_gets_the_same_verdicts_as_the_template(tmp_path):
    client = ScriptedClient(lambda sid, code: msg("```python\n" + code + "\n```"))
    rep = compile_protocol(PROTO, tmp_path, backends=[TemplateBackend(), LLMBackend(model_id="scripted", client=client)], animate=False, sessions=SOME)
    t, l = rep.runs
    assert t.all_pass and l.all_pass and l.model_id == "scripted"
    assert (tmp_path / "robot" / "template").is_dir() and (tmp_path / "robot" / "llm_scripted").is_dir()


@needs_ot
def test_a_model_that_changes_a_volume_is_caught_against_the_plan(tmp_path):
    def sloppy(sid, code):
        if sid == "s11_antigen_stimulation":
            code = code.replace("p20.transfer(20,", "p20.transfer(10,", 1)
        return msg("```python\n" + code + "\n```")

    rep = compile_protocol(PROTO, tmp_path, backends=[LLMBackend(model_id="sloppy", client=ScriptedClient(sloppy))], animate=False, sessions=SOME)
    by = {s.session_id: s for s in rep.runs[0].sessions}
    bad = by["s11_antigen_stimulation"]
    assert bad.outcome == "FAIL"
    assert any(f.rule == "plan_volume" for f in bad.grade.findings)
    assert by["s02_plasmid_assembly"].outcome == "PASS"  # one bad session does not hide the good ones


@needs_ot
def test_a_model_that_drops_a_human_handoff_is_caught(tmp_path):
    def dropper(sid, code):
        return msg("```python\n" + re.sub(r"\n    ctx\.pause\(.*\)", "", code) + "\n```")

    rep = compile_protocol(PROTO, tmp_path, backends=[LLMBackend(model_id="dropper", client=ScriptedClient(dropper))], animate=False, sessions=SOME)
    assert all(s.outcome == "FAIL" for s in rep.runs[0].sessions)
    assert any(f.rule == "plan_handoffs" for s in rep.runs[0].sessions for f in s.grade.findings)


@needs_ot
def test_refusals_and_errors_are_reported_per_session_and_do_not_stop_the_run(tmp_path):
    def mixed(sid, code):
        if sid.startswith("s02"):
            return msg("", "refusal")
        if sid.startswith("s03"):
            return msg("I can't help with that.")
        if sid.startswith("s04"):
            return msg("Let me think about it.")
        if sid.startswith("s05"):
            return msg("```python\nimport os\nos.system('x')\n```")
        return msg("```python\n" + code + "\n```")

    rep = compile_protocol(PROTO, tmp_path, backends=[LLMBackend(model_id="mixed", client=ScriptedClient(mixed))], animate=False, sessions=SOME)
    by = {s.session_id: s for s in rep.runs[0].sessions}
    assert by["s02_plasmid_assembly"].outcome == "REFUSED" and by["s02_plasmid_assembly"].refusal_kind == "api_stop_reason"
    assert by["s03_transformation"].outcome == "REFUSED" and by["s03_transformation"].refusal_kind == "text_pattern"
    assert by["s04_colony_pcr"].outcome == "ERROR" and by["s04_colony_pcr"].code_file is None
    assert by["s05_expression_induction"].outcome == "FAIL" and by["s05_expression_induction"].grade.lint  # never executed
    assert by["s06_lysis"].outcome == "PASS"
    assert rep.runs[0].tally == {"REFUSED": 2, "ERROR": 1, "FAIL": 1, "PASS": len(SOME) - 4}
    assert (tmp_path / "robot_report.md").read_text(encoding="utf-8").count("REFUSED") >= 2


@needs_ot
def test_cli_compile_and_grade(tmp_path, capsys):
    pj = tmp_path / "protocol.json"
    pj.write_text(PROTO.model_dump_json(), encoding="utf-8")
    assert _cli(["compile", str(pj), "--out", str(tmp_path / "out")]) == 0
    assert "template" in capsys.readouterr().out
    code = next((tmp_path / "out" / "robot" / "template").glob("s11_*.py"))
    assert _cli(["grade", str(code)]) == 0
    assert _cli(["compile", str(pj), "--out", str(tmp_path / "x"), "--no-simulate"]) == 1  # UNVERIFIED is not a pass


@needs_ot
def test_session_filter_regenerates_only_what_was_asked(tmp_path):
    rep = compile_protocol(PROTO, tmp_path, sessions=["s06_lysis"], animate=False)
    assert [s.session_id for s in rep.runs[0].sessions] == ["s06_lysis"] and rep.runs[0].all_pass
    assert not (tmp_path / "robot" / "template" / "s02_plasmid_assembly.py").exists()
    with pytest.raises(ValueError, match="unknown session id"):
        compile_protocol(PROTO, tmp_path / "y", sessions=["nope"], animate=False)


@needs_ot
def test_reports_contain_no_absolute_local_paths(compiled):
    out, rep = compiled
    blob = (out / "robot_report.json").read_text(encoding="utf-8")
    for needle in (str(out), out.as_posix(), "Users", "AppData"):
        assert needle not in blob, needle
    for s in rep.runs[0].sessions:
        sim = (out / s.sim_file).read_text(encoding="utf-8")
        assert "Users" not in sim and "AppData" not in sim and str(out) not in sim and out.as_posix() not in sim
