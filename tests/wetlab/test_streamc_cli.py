"""Stream C command line (`streamc_cli`): the output tree, exit codes, README-compatible flags, bad input.

`run` is exercised twice over the demo fixture: once for real (`needs_ot`: every S9 session is simulated in
Opentrons 7.5.0, about 25 s, skipped when no simulator is installed), and once with `--no-simulate`, which must exit 1
because UNVERIFIED is not a pass. Everything else is offline and fast. No test here calls a model: the template
backend is the default and `_backends` only constructs the LLM backend.
"""

import contextlib
import io
import json
import re
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaxforge.s8_protocol import Candidate, StepKind, build_protocol, to_json
from vaxforge.s9_validate import find_ot_python
from vaxforge.streamc_adapter import ASSUMPTIONS, STAND_IN_WARNING, RunInputs
from vaxforge.streamc_cli import _backends, main, run_stream_c
from vaxforge.streamc_models import PathogenContext, candidate_dir_names

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "nmb_demo"
needs_ot = pytest.mark.skipif(find_ot_python() is None, reason="no Opentrons 7.5.0 interpreter (set VAXFORGE_OT_PYTHON)")

BS = chr(92)  # a backslash, built here so that no string literal below needs escaping
DRIVE_PATH = re.compile("[A-Za-z]:[" + BS + BS + "/]")  # C:\ or C:/
USERS_PATH = re.compile("Users[" + BS + BS + "/]")
TITLE = "# Animal immunogenicity study proposal - Neisseria meningitidis serogroup B"


def cli(*argv):
    """Run `main` in-process: (exit code, stdout, stderr)."""
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue(), err.getvalue()


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def read_text(path):
    return Path(path).read_text(encoding="utf-8")


def robot_dir(out, cid="fixture_fhbp"):
    return Path(out) / "candidates" / cid / "robot" / "template"


@pytest.fixture(scope="module")
def simulated(tmp_path_factory):
    """One real `run` over the demo fixture (every session simulated, about 25 s), shared by the tests below."""
    out = tmp_path_factory.mktemp("streamc_sim") / "out"
    code, stdout, stderr = cli("run", FIXTURE, "--out", out, "--top-k", 2, "--no-animation")
    return SimpleNamespace(out=out, code=code, stdout=stdout, stderr=stderr)


@pytest.fixture(scope="module")
def unsimulated(tmp_path_factory):
    """The same `run` with --no-simulate: the code is generated and statically checked, nothing is simulated."""
    out = tmp_path_factory.mktemp("streamc_nosim") / "out"
    code, stdout, stderr = cli("run", FIXTURE, "--out", out, "--top-k", 2, "--no-simulate", "--no-animation")
    return SimpleNamespace(out=out, code=code, stdout=stdout, stderr=stderr)


def assert_summary_paths_are_relative(out):
    text = read_text(out / "stream_c_summary.json")
    assert "Users" not in text and not DRIVE_PATH.search(text)
    s = json.loads(text)
    paths = [s["study_file"]] + [c[k] for c in s["candidates"] for k in ("protocol_file", "robot_report")]
    assert len(paths) == 1 + 2 * len(s["candidates"])
    for p in paths:
        assert not Path(p).is_absolute() and ".." not in Path(p).parts and BS not in p, p
        assert (out / p).is_file(), p  # relative to the output directory, and really there
    md = read_text(out / "stream_c_summary.md")
    assert "Users" not in md and not DRIVE_PATH.search(md)


# --------------------------------------------------------------------------- run, simulated


@needs_ot
def test_simulated_run_passes_and_exits_zero(simulated):
    assert simulated.code == 0 and simulated.stderr == ""
    s = read_json(simulated.out / "stream_c_summary.json")
    assert s["ok"] is True and s["study_errors"] == [] and s["pathogen"] == "Neisseria meningitidis serogroup B"
    assert [c["candidate_id"] for c in s["candidates"]] == ["fixture_fHbp", "fixture_NadA"]  # the top 2 by rank
    for c in s["candidates"]:
        assert c["s9_all_pass"] is True
        assert list(c["s9_tally"]) == ["template"] and list(c["s9_tally"]["template"]) == ["PASS"]
    assert "fixture_fHbp: S9" in simulated.stdout and "S10: 0 validation error(s)" in simulated.stdout
    assert "**Overall: OK**" in read_text(simulated.out / "stream_c_summary.md")


@needs_ot
def test_simulated_run_writes_the_documented_tree(simulated):
    out = simulated.out
    for name in ("stream_c_summary.json", "stream_c_summary.md", "animal_study/animal_study.json", "animal_study/animal_study.md"):
        assert (out / name).is_file(), name
    assert sorted(p.name for p in (out / "candidates").iterdir()) == ["fixture_fhbp", "fixture_nada"]  # ids made filesystem-safe
    for cid in ("fixture_fhbp", "fixture_nada"):
        cdir = out / "candidates" / cid
        for name in ("protocol.json", "protocol.md", "robot_report.json", "robot_report.md", "workflow.md"):
            assert (cdir / name).is_file(), (cid, name)
        for suffix in (".py", ".setup.md", ".plan.json", ".sim.json", ".grade.json"):
            assert list(robot_dir(out, cid).glob("*" + suffix)), (cid, suffix)
        assert not (cdir / "animation").exists()  # --no-animation
        assert read_json(cdir / "robot_report.json")["simulated"] is True


@needs_ot
def test_simulated_run_reports_only_relative_paths(simulated):
    assert_summary_paths_are_relative(simulated.out)


@needs_ot
def test_no_file_in_the_simulated_tree_names_a_local_path(simulated):
    here = {str(simulated.out), simulated.out.as_posix(), str(simulated.out.parent), simulated.out.parent.as_posix()}
    files = [p for p in simulated.out.rglob("*") if p.is_file()]
    assert len(files) > 50
    for p in files:
        text = read_text(p)
        assert not any(h in text for h in here), p.name
        assert not DRIVE_PATH.search(text) and not USERS_PATH.search(text), p.name


# --------------------------------------------------------------------------- run, --no-simulate


def test_run_without_a_simulator_is_unverified_and_exits_one(unsimulated):
    assert unsimulated.code == 1 and unsimulated.stderr == ""  # UNVERIFIED is not a pass
    s = read_json(unsimulated.out / "stream_c_summary.json")
    assert s["ok"] is False and s["study_errors"] == []  # the design is fine; the robot code was never run
    assert len(s["candidates"]) == 2
    for c in s["candidates"]:
        tally = c["s9_tally"]["template"]
        assert c["s9_all_pass"] is False and list(tally) == ["UNVERIFIED"] and tally["UNVERIFIED"] >= 1
    assert "fixture_fHbp: S9 {'template': {'UNVERIFIED'" in unsimulated.stdout
    assert "**Overall: NOT OK**" in read_text(unsimulated.out / "stream_c_summary.md")


def test_unsimulated_run_still_writes_code_and_marks_it_unverified(unsimulated):
    robot = robot_dir(unsimulated.out)
    assert list(robot.glob("*.py")) and not list(robot.glob("*.sim.json"))
    grades = [read_json(p) for p in robot.glob("*.grade.json")]
    assert grades and all(g["verdict"] == "UNVERIFIED" and g["sim_ok"] is None for g in grades)
    assert read_json(unsimulated.out / "candidates" / "fixture_fhbp" / "robot_report.json")["simulated"] is False


def test_the_tree_and_the_animal_study_are_written_without_a_simulator(unsimulated):
    out = unsimulated.out
    assert sorted(p.name for p in (out / "candidates").iterdir()) == ["fixture_fhbp", "fixture_nada"]
    d = read_json(out / "animal_study" / "animal_study.json")
    assert d["candidates_included"] == ["fixture_fHbp", "fixture_NadA"] and d["pathogen_taxid"] == 487  # one design for the shortlist
    md = read_text(out / "animal_study" / "animal_study.md")
    assert md.startswith(TITLE) and "DESIGN ONLY" in md
    assert_summary_paths_are_relative(out)


def test_the_summary_echoes_the_assumptions_about_upstream_data(unsimulated):
    s = read_json(unsimulated.out / "stream_c_summary.json")
    md = read_text(unsimulated.out / "stream_c_summary.md")
    assert s["assumptions"] == ASSUMPTIONS and all(a in md for a in ASSUMPTIONS)
    assert "Everything here is a DESIGN" in md and "no assay results exist" in md


def test_input_warnings_are_echoed_in_the_summary(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "candidates.json").write_text(json.dumps([dict(id="solo", name="Solo", rank=1)]), encoding="utf-8")  # no pathogen file
    code, _, _ = cli("run", run, "--out", tmp_path / "out", "--no-simulate", "--no-animation")
    assert code == 1
    assert any("no pathogen file" in w for w in read_json(tmp_path / "out" / "stream_c_summary.json")["warnings"])
    assert "## Input warnings" in read_text(tmp_path / "out" / "stream_c_summary.md")


def first_code(out):
    return read_text(sorted(robot_dir(out).glob("*.py"))[0])


def test_generated_code_says_the_s12_gate_is_not_evidenced_unless_told_otherwise(unsimulated, tmp_path):
    assert "S12 biosecurity gate NOT EVIDENCED" in first_code(unsimulated.out)
    code, _, _ = cli("run", FIXTURE, "--out", tmp_path / "cleared", "--top-k", 1, "--no-simulate", "--no-animation", "--s12", "cleared")
    assert code == 1  # still unverified
    cleared = first_code(tmp_path / "cleared")
    assert "CLEARED by the caller" in cleared and "NOT EVIDENCED" not in cleared


def test_endotoxin_limit_and_readouts_reach_the_s8_protocol(tmp_path):
    cli("run", FIXTURE, "--out", tmp_path / "o", "--top-k", 1, "--no-simulate", "--no-animation",
        "--endotoxin-limit", "0.5", "--readouts", "elisa,facs")
    steps = read_json(tmp_path / "o" / "candidates" / "fixture_fhbp" / "protocol.json")["steps"]
    assert "<= 0.5 EU/ug" in next(s for s in steps if s["kind"] == "lal_assay")["gate"]
    assert [s["kind"] for s in steps if s["kind"] in ("elisa", "rt_qpcr", "facs")] == ["elisa", "facs"]


def test_the_endotoxin_limit_is_not_defaulted_and_readouts_default_to_elisa_and_rt_qpcr(unsimulated):
    steps = read_json(unsimulated.out / "candidates" / "fixture_fhbp" / "protocol.json")["steps"]
    assert "NOT YET SET" in next(s for s in steps if s["kind"] == "lal_assay")["gate"]
    assert [s["kind"] for s in steps if s["kind"] in ("elisa", "rt_qpcr", "facs")] == ["elisa", "rt_qpcr"]


def test_top_k_limits_the_shortlist(tmp_path):
    cli("run", FIXTURE, "--out", tmp_path / "o", "--top-k", 1, "--no-simulate", "--no-animation")
    assert [p.name for p in (tmp_path / "o" / "candidates").iterdir()] == ["fixture_fhbp"]
    assert read_json(tmp_path / "o" / "animal_study" / "animal_study.json")["candidates_included"] == ["fixture_fHbp"]


def test_the_readme_study_flags_are_accepted_by_run_too(tmp_path):
    code, _, stderr = cli("run", FIXTURE, "--out", tmp_path / "o", "--top-k", 1, "--no-simulate", "--no-animation",
                          "--species", "hla-transgenic", "--power", "0.8", "--arms", "4")
    assert code == 1 and stderr == ""  # exit 1 only because nothing was simulated
    cfg = read_json(tmp_path / "o" / "animal_study" / "animal_study.json")["config"]
    assert cfg["species"] == "mouse_hla_transgenic" and cfg["power"] == 0.8


# --------------------------------------------------------------------------- compile-protocol


def test_compile_protocol_from_a_written_protocol_json(unsimulated, tmp_path):
    proto = unsimulated.out / "candidates" / "fixture_fhbp" / "protocol.json"
    code, stdout, stderr = cli("compile-protocol", proto, "--out", tmp_path / "robot_out", "--no-simulate")
    assert code == 1 and stderr == ""  # UNVERIFIED is not a pass
    assert stdout.startswith("[template] ") and "UNVERIFIED" in stdout
    out = tmp_path / "robot_out"
    report = read_json(out / "robot_report.json")
    assert report["candidate_id"] == "fixture_fHbp" and report["simulated"] is False
    assert (out / "workflow.md").is_file() and (out / "robot_report.md").is_file()
    # the template backend is deterministic: the same code `run` wrote for this candidate
    ran, now = robot_dir(unsimulated.out), out / "robot" / "template"
    names = sorted(p.name for p in ran.glob("*.py"))
    assert names and names == sorted(p.name for p in now.glob("*.py"))
    for n in names:
        assert read_text(ran / n) == read_text(now / n), n


def test_compile_protocol_accepts_the_readme_target_flag(unsimulated, tmp_path):
    proto = unsimulated.out / "candidates" / "fixture_fhbp" / "protocol.json"
    code, stdout, _ = cli("compile-protocol", proto, "--out", tmp_path / "o", "--no-simulate", "--target", "opentrons-ot2")
    assert code == 1 and "UNVERIFIED" in stdout
    with pytest.raises(SystemExit) as exc:
        cli("compile-protocol", proto, "--out", tmp_path / "o2", "--no-simulate", "--target", "flex")
    assert exc.value.code == 2


def test_compile_protocol_refuses_a_protocol_that_breaks_the_s8_rules(tmp_path):
    p = build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1))
    stim = next(s for s in p.steps if s.kind is StepKind.ANTIGEN_STIMULATION)
    stim.controls = ["vehicle/buffer only"]  # no LPS control: S8 forbids it, so S9 must not write robot code for it
    (tmp_path / "bad.json").write_text(to_json(p), encoding="utf-8")
    code, stdout, stderr = cli("compile-protocol", tmp_path / "bad.json", "--out", tmp_path / "out", "--no-simulate")
    assert code == 2 and stdout == "" and stderr.startswith("refused: ") and "LPS control" in stderr
    assert not (tmp_path / "out").exists()  # nothing was written


# --------------------------------------------------------------------------- animal-study


def test_animal_study_prints_the_proposal_and_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    code, stdout, stderr = cli("animal-study", FIXTURE)
    assert code == 0 and stderr == ""
    assert stdout.startswith(TITLE) and "DESIGN ONLY" in stdout
    assert list(tmp_path.iterdir()) == []


def test_animal_study_out_writes_markdown_and_json(tmp_path):
    code, stdout, _ = cli("animal-study", FIXTURE, "--out", tmp_path / "study")
    assert code == 0 and "animal_study.md" in stdout
    md = read_text(tmp_path / "study" / "animal_study.md")
    d = read_json(tmp_path / "study" / "animal_study.json")
    assert d["pathogen"] == "Neisseria meningitidis serogroup B"
    assert d["candidates_included"] == ["fixture_fHbp", "fixture_NadA", "fixture_NHBA"]  # --top-k defaults to 3
    assert cli("animal-study", FIXTURE)[1] == md + "\n"  # stdout and the file are the same document


def test_animal_study_top_k(tmp_path):
    cli("animal-study", FIXTURE, "--top-k", 10, "--out", tmp_path / "all")
    cli("animal-study", FIXTURE, "--top-k", 1, "--out", tmp_path / "one")
    assert len(read_json(tmp_path / "all" / "animal_study.json")["candidates_included"]) == 4  # more than exist: all of them
    assert read_json(tmp_path / "one" / "animal_study.json")["candidates_included"] == ["fixture_fHbp"]


def test_readme_flags_are_accepted_and_applied(tmp_path):
    flags = ("--species", "hla-transgenic", "--power", "0.8")
    code, _, stderr = cli("animal-study", FIXTURE, *flags, "--arms", "4", "--out", tmp_path / "with_arms")
    assert code == 0 and stderr == ""
    cfg = read_json(tmp_path / "with_arms" / "animal_study.json")["config"]
    assert cfg["species"] == "mouse_hla_transgenic" and cfg["goal"] == "hla_epitope_validation" and cfg["power"] == 0.8
    # `--arms` is accepted for README compatibility and ignored: the arms follow from the candidates and the controls
    cli("animal-study", FIXTURE, *flags, "--out", tmp_path / "without_arms")
    assert read_text(tmp_path / "with_arms" / "animal_study.json") == read_text(tmp_path / "without_arms" / "animal_study.json")


@pytest.mark.parametrize(
    "flags,expect",
    [
        (["--species", "rabbit"], dict(species="rabbit", goal="polyclonal_sera")),
        (["--species", "mouse"], dict(species="mouse_wild_type", goal="basic_immunogenicity")),
        (["--species", "wild-type"], dict(species="mouse_wild_type", goal="basic_immunogenicity")),
        (["--goal", "polyclonal_sera"], dict(goal="polyclonal_sera")),
        (["--power", "0.9", "--adjuvant", "MF59", "--route", "SC"], dict(power=0.9, adjuvant="MF59", route="SC")),
        (["--hla-lines", "A2.DR1, B7 ,"], dict(hla_transgenic_lines=["A2.DR1", "B7"])),
        (["--licensed-vaccine", "yes"], dict(licensed_vaccine_exists=True)),
        (["--licensed-vaccine", "no"], dict(licensed_vaccine_exists=False)),
        (["--licensed-vaccine", "unknown"], dict(licensed_vaccine_exists=None)),  # unknown stays unknown
    ],
)
def test_study_flags_land_in_the_design_config(tmp_path, flags, expect):
    cli("animal-study", FIXTURE, "--out", tmp_path / "s", *flags)
    cfg = read_json(tmp_path / "s" / "animal_study.json")["config"]
    for key, value in expect.items():
        assert cfg[key] == value, key


def test_a_study_config_file_is_the_base_and_flags_override_it(tmp_path):
    base = tmp_path / "study.json"
    base.write_text(json.dumps(dict(power=0.9, mouse_strain="C57BL/6", alpha=0.01)), encoding="utf-8")
    cli("animal-study", FIXTURE, "--study-config", base, "--power", "0.85", "--out", tmp_path / "s")
    cfg = read_json(tmp_path / "s" / "animal_study.json")["config"]
    assert cfg["power"] == 0.85 and cfg["mouse_strain"] == "C57BL/6" and cfg["alpha"] == 0.01


def test_a_scientifically_invalid_design_exits_one_and_says_why():
    code, stdout, stderr = cli("animal-study", FIXTURE, "--species", "mouse", "--goal", "hla_epitope_validation")
    assert code == 1
    assert "validation: " in stderr and "HLA-transgenic" in stderr
    assert stdout.startswith(TITLE)  # the proposal is still printed so the problem can be seen in context


def test_backend_choice_builds_the_requested_backends():
    assert [type(b).__name__ for b in _backends("template", None)] == ["TemplateBackend"]
    assert [type(b).__name__ for b in _backends("llm", "my-model")] == ["LLMBackend"]
    assert [type(b).__name__ for b in _backends("both", "my-model")] == ["TemplateBackend", "LLMBackend"]
    assert _backends("llm", "my-model")[0].model_id == "my-model"


# --------------------------------------------------------------------------- bad input


def put(path, text):
    path.write_text(text, encoding="utf-8")
    return path


def candidates_dir(tmp, content):
    run = tmp / "run"
    run.mkdir(exist_ok=True)
    put(run / "candidates.json", content)
    return run


BAD_INPUT = dict(
    missing_run_dir=lambda t: ["animal-study", t / "nope"],
    missing_run_dir_for_run=lambda t: ["run", t / "nope", "--out", t / "out"],
    no_input_at_all=lambda t: ["animal-study"],
    candidates_not_json=lambda t: ["animal-study", candidates_dir(t, "not json")],
    no_candidates=lambda t: ["animal-study", candidates_dir(t, "[]")],
    candidates_are_strings=lambda t: ["animal-study", candidates_dir(t, json.dumps(["fHbp", "NadA"]))],
    duplicate_ids=lambda t: ["animal-study", candidates_dir(t, json.dumps([dict(id="a"), dict(id="a")]))],
    rank_is_text=lambda t: ["animal-study", candidates_dir(t, json.dumps([dict(id="a", rank="first")]))],
    compile_protocol_missing_file=lambda t: ["compile-protocol", t / "nope.json", "--out", t / "out"],
    compile_protocol_not_json=lambda t: ["compile-protocol", put(t / "p.json", "{oops"), "--out", t / "out"],
    compile_protocol_wrong_schema=lambda t: ["compile-protocol", put(t / "p.json", "[]"), "--out", t / "out"],
    top_k_zero=lambda t: ["animal-study", FIXTURE, "--top-k", 0],
    top_k_negative=lambda t: ["run", FIXTURE, "--out", t / "out", "--top-k", -1],
    power_as_a_percentage=lambda t: ["animal-study", FIXTURE, "--power", 80],
    study_config_missing=lambda t: ["animal-study", FIXTURE, "--study-config", t / "nope.json"],
    study_config_not_json=lambda t: ["animal-study", FIXTURE, "--study-config", put(t / "s.json", "{oops")],
    unknown_readout=lambda t: ["run", FIXTURE, "--out", t / "out", "--readouts", "elisa,western"],
    robot_params_missing=lambda t: ["run", FIXTURE, "--out", t / "out", "--robot-params", t / "nope.json"],
    robot_params_wrong_schema=lambda t: ["run", FIXTURE, "--out", t / "out", "--robot-params", put(t / "r.json", "[1]")],
)


@pytest.mark.parametrize("case", sorted(BAD_INPUT))
def test_bad_input_exits_two_with_one_line_and_writes_nothing(case, tmp_path):
    code, stdout, stderr = cli(*BAD_INPUT[case](tmp_path))
    assert code == 2 and stdout == ""
    assert stderr.startswith("input error: ") and stderr.count("\n") == 1  # one line, not a traceback
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize(
    "argv",
    [
        [],  # no subcommand
        ["run", str(FIXTURE)],  # --out is required
        ["animal-study", str(FIXTURE), "--species", "dragon"],
        ["animal-study", str(FIXTURE), "--power", "high"],
        ["animal-study", str(FIXTURE), "--licensed-vaccine", "maybe"],
    ],
)
def test_argument_errors_exit_two(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        main(argv)
    assert exc.value.code == 2
    assert "usage:" in capsys.readouterr().err


# --------------------------------------------------------------------------- one folder per candidate


def test_candidate_dir_names_are_unique_deterministic_and_safe():
    assert candidate_dir_names(["NadA", "nada", "P1/2", "P1_2"]) == ["nada", "nada-2", "p1_2", "p1_2-2"]  # rank order decides who keeps the plain name
    assert candidate_dir_names(["a", "A", "a"]) == ["a", "a-2", "a-3"]
    assert candidate_dir_names(["a", "a-2", "A"]) == ["a", "a-2", "a-3"]  # a suffixed name is never handed out twice
    assert candidate_dir_names(["A", "a-2", "a"]) == ["a", "a-2", "a-3"]
    assert candidate_dir_names(["sp|Q9JXV4|FHBP_NEIMB"]) == ["sp_q9jxv4_fhbp_neimb"]
    again = ["x/1", "X_1", "x-1", "X 1"]
    assert candidate_dir_names(again) == candidate_dir_names(again)
    for hostile in (["..", "."], ["", "   "], ["con", "NUL.txt", "a" * 200]):
        names = candidate_dir_names(hostile)
        assert len(set(names)) == len(names)
        for n in names:  # never '.', '..', a Windows device name, a leading dot, or longer than 64 characters
            assert n not in (".", "..") and not n.startswith(".") and re.fullmatch(r"[a-z0-9_][a-z0-9._-]*", n) and len(n) <= 70
            assert n.split(".")[0] not in ("con", "nul", "prn", "aux")


def collision_inputs():
    cands = [Candidate(candidate_id=cid, protein_name=f"protein {cid}", rank=i) for i, cid in enumerate(["NadA", "nada", "P1/2", "P1_2"], start=1)]
    return RunInputs(candidates=cands, pathogen=PathogenContext(name="Neisseria meningitidis"))


def test_candidates_whose_ids_differ_only_in_case_or_punctuation_get_their_own_folders(tmp_path):
    """Old behaviour: `_safe` mapped NadA and nada (and P1/2 and P1_2) to ONE folder and the second silently overwrote the first."""
    out = tmp_path / "out"
    summary = run_stream_c(collision_inputs(), out, top_k=4, simulate_runs=False, animate=False)
    folders = sorted(p.name for p in (out / "candidates").iterdir())
    assert folders == ["nada", "nada-2", "p1_2", "p1_2-2"]
    # every folder holds ITS OWN candidate: nothing was overwritten
    assert {f: read_json(out / "candidates" / f / "protocol.json")["candidate_id"] for f in folders} == {
        "nada": "NadA", "nada-2": "nada", "p1_2": "P1/2", "p1_2-2": "P1_2"}
    assert [c.candidate_id for c in summary.candidates] == ["NadA", "nada", "P1/2", "P1_2"]
    assert [c.protocol_file for c in summary.candidates] == [f"candidates/{f}/protocol.json" for f in ("nada", "nada-2", "p1_2", "p1_2-2")]
    for c in summary.candidates:
        assert (out / c.protocol_file).is_file() and (out / c.robot_report).is_file()
    # deterministic: the same input names the same folders
    again = run_stream_c(collision_inputs(), tmp_path / "again", top_k=4, simulate_runs=False, animate=False)
    assert [c.protocol_file for c in again.candidates] == [c.protocol_file for c in summary.candidates]


def test_the_summary_table_survives_ids_that_contain_pipes(tmp_path):
    cand = Candidate(candidate_id="sp|X1|NAME_NEIMB", protein_name="Some protein (gene)", rank=1)
    run_stream_c(RunInputs(candidates=[cand], pathogen=PathogenContext(name="X")), tmp_path / "o", top_k=1, simulate_runs=False, animate=False)
    row = next(line for line in read_text(tmp_path / "o" / "stream_c_summary.md").splitlines() if "NAME_NEIMB" in line)
    assert len(re.findall(r"(?<!\\)\|", row)) == 4  # three columns: the pipes inside the id are escaped


# --------------------------------------------------------------------------- the stand-alone CLI on a contract run folder

CONTRACT_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "nmb_fixture"


def contract_copy(tmp_path):
    dest = tmp_path / "run"
    shutil.copytree(CONTRACT_FIXTURE, dest)  # never the committed fixture itself
    return dest


def test_run_accepts_a_contract_run_folder_and_the_proteins_you_name(tmp_path):
    run = contract_copy(tmp_path)
    code, stdout, stderr = cli("run", run, "--proteins", "fhbp,NADA", "--out", tmp_path / "out", "--no-simulate", "--no-animation")
    assert code == 1 and stderr == ""  # exit 1 only because nothing was simulated
    s = read_json(tmp_path / "out" / "stream_c_summary.json")
    assert s["pathogen"] == "Neisseria meningitidis" and s["study_errors"] == []
    assert [c["candidate_id"] for c in s["candidates"]] == ["sp|Q9JXV4|FHBP_NEIMB", "sp|Q9JXK7|NADA1_NEIMB"]
    assert sorted(p.name for p in (tmp_path / "out" / "candidates").iterdir()) == ["sp_q9jxk7_nada1_neimb", "sp_q9jxv4_fhbp_neimb"]
    assert STAND_IN_WARNING in s["warnings"]
    assert not (run / "s8_protocol.json").exists()  # `run` writes a plain tree under --out, not stage artifacts


def test_run_on_a_contract_run_folder_without_proteins_asks_which_ones(tmp_path):
    run = contract_copy(tmp_path)
    code, stdout, stderr = cli("run", run, "--out", tmp_path / "out", "--no-simulate")
    assert code == 2 and stdout == ""
    assert stderr.startswith("input error: No candidates selected") and "streamc.proteins" in stderr
    assert stderr.count("\n") == 1 and not (tmp_path / "out").exists()  # one line, nothing written


def test_animal_study_on_a_contract_run_folder_writes_the_s10_artifact_and_the_files(tmp_path):
    run = contract_copy(tmp_path)
    code, stdout, stderr = cli("animal-study", run, "--proteins", "fhbp", "--species", "hla-transgenic", "--out", tmp_path / "study")
    assert code == 0 and stderr == "" and "s10: ok" in stdout
    assert (run / "s10_animal.json").is_file() and (run / "data" / "s10" / "animal_study.md").is_file()
    assert read_text(tmp_path / "study" / "animal_study.md") == read_text(run / "data" / "s10" / "animal_study.md")
    assert read_json(tmp_path / "study" / "animal_study.json")["config"]["species"] == "mouse_hla_transgenic"


def test_compile_protocol_on_a_contract_run_folder_without_s8_is_not_run_and_exits_two(tmp_path):
    run = contract_copy(tmp_path)
    code, stdout, stderr = cli("compile-protocol", run, "--no-simulate")
    assert code == 2 and stdout.startswith("s9: ok, payload.status not_run")
    assert "no s8 artifact in this run" in stderr and "run S8 first" in stderr
    art = read_json(run / "s9_robot.json")  # an `ok` artifact, so `vaxforge run` is not stopped by it
    assert art["status"] == "ok" and art["payload"]["status"] == "not_run" and art["inputs"] == {}


def test_the_committed_fixture_is_left_untouched():
    assert not list(CONTRACT_FIXTURE.glob("s8_*.json")) and not list(CONTRACT_FIXTURE.glob("s10_*.json")) and not (CONTRACT_FIXTURE / "data" / "s9").exists()
