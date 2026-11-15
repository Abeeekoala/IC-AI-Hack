"""CLI behaviour: end to end on fakes, exit codes, offline reuse, and the hooks other streams plug into."""
from __future__ import annotations

import json
import re
import shutil
import sys
import types
from pathlib import Path

import pytest

from vaxforge import artifacts as A, cli, s0_taxonomy as S0, s1_acquire as S1, s2_annotate as S2
from vaxforge.artifacts import RunStore
from vaxforge.config import ConfigError
from vaxforge.http import client_from_config as real_client_from_config
from vaxforge.testing import FakeTransport, ReplayTransport
from vaxforge.tools import BlastRunner, ascii_safe_dir

from conftest import DATA, ROOT, make_config
from fakes import build_server

needs_blast = pytest.mark.skipif(not (shutil.which("blastp") and shutil.which("makeblastdb")), reason="BLAST+ not installed")

CONFIG_YAML = """
paths:
  cache_dir: cache
  runs_dir: runs
network:
  retries: 0
  min_interval_s:
    eutils.ncbi.nlm.nih.gov: 0
    rest.uniprot.org: 0
"""


@pytest.fixture
def project(tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(CONFIG_YAML, encoding="utf-8")
    replay, server = ReplayTransport(DATA / "http_s0"), build_server()

    def transport(url, headers, timeout):
        if "eutils.ncbi" in url or "/taxonomy/search" in url:
            return replay(url, headers, timeout)
        return server(url, headers, timeout)

    for mod in (S0, S1):
        monkeypatch.setattr(mod, "client_from_config", lambda config, t=None: real_client_from_config(config, transport))
    monkeypatch.chdir(tmp_path)
    return tmp_path


def run_cli(*argv: str) -> int:
    return cli.main(list(argv))


@needs_blast
def test_run_end_to_end_then_verify_show_and_offline_rerun(project, capsys, monkeypatch):
    # No PSORTb on purpose: with Docker installed but idle the online run would record "PSORTb failed: <docker error>" and the
    # offline re-run a different reason, so the two S2 digests could never match. Absent PSORTb gives one stable reason.
    monkeypatch.setattr(S2, "find_executable", lambda *a, **k: None)
    # Stream A's own stages only: a plain `run` walks S3-S12 too, and those start real tools (for example a Docker container
    # for Vaxign-ML, which also pulls a 4.9 GB image, whenever a Docker daemon happens to be running), which a unit test must not do.
    stages = ("--stages", "s0,s1,s2")
    out = project / "runs" / "nmb"
    code = run_cli("run", "--pathogen", "Neisseria meningitidis", "--out", str(out), "--non-interactive", "--top-k", "7", *stages)
    text = capsys.readouterr().out
    assert code == 0, text
    assert "S0  resolved: Neisseria meningitidis" in text and "S1  proteome UP000000001" in text and "S2  annotated 3 proteins" in text
    assert "'Unknown' kept explicit" in text and "verify: ok)" in text            # "(degraded: ...; verify: ok)" when PSORTb is absent
    store = RunStore(out)
    assert store.stages_present() == ["s0", "s1", "s2"]
    assert json.loads((out / "config.resolved.json").read_text(encoding="utf-8"))["pipeline"]["top_k"] == 7

    assert run_cli("verify", str(out)) == 0
    assert "OK - digests" in capsys.readouterr().out
    assert run_cli("show", str(out)) == 0
    assert "S0  resolved" in capsys.readouterr().out

    # warm cache is enough to run again with the network switched off
    out2 = project / "runs" / "again"
    assert run_cli("run", "--pathogen", "Neisseria meningitidis", "--out", str(out2), "--non-interactive", "--offline", *stages) == 0
    capsys.readouterr()
    assert RunStore(out2).read_artifact("s2").digest == store.read_artifact("s2").digest        # content-addressed: identical


def test_ambiguous_name_exits_2_with_candidates_and_taxid_fixes_it(project, capsys):
    out = project / "runs" / "g"
    code = run_cli("run", "--pathogen", "Neisseria", "--out", str(out), "--non-interactive", "--stages", "s0")
    text = capsys.readouterr().out
    assert code == 2 and "NOT RESOLVED" in text and "too broad" in text and "taxid 487" in text and "--taxid" in text
    assert RunStore(out).read_artifact("s0").status == "needs_user_input"
    code = run_cli("run", "--taxid", "487", "--out", str(out), "--non-interactive", "--stages", "s0")
    assert code == 0 and RunStore(out).read_artifact("s0").payload["taxon"]["taxid"] == 487


def test_resolve_command_json_and_exit_codes(project, capsys):
    assert run_cli("resolve", "Clostridium difficile", "--json") == 0
    data = json.loads(capsys.readouterr().out)
    assert data["taxon"]["scientific_name"] == "Clostridioides difficile" and data["resolution"]["synonym_matched"] is True
    assert run_cli("resolve", "zzqx notanorganism") == 2
    assert "No taxon matches" in capsys.readouterr().out


def test_s1_failure_exits_3(project, capsys, monkeypatch):
    empty = build_server(primary=None, others=())
    replay = ReplayTransport(DATA / "http_s0")
    monkeypatch.setattr(S0, "client_from_config", lambda c, t=None: real_client_from_config(c, replay))
    monkeypatch.setattr(S1, "client_from_config", lambda c, t=None: real_client_from_config(c, empty))
    code = run_cli("run", "--pathogen", "Neisseria meningitidis", "--out", "runs/x", "--non-interactive", "--stages", "s0,s1")
    assert code == 3 and "S1  FAILED" in capsys.readouterr().out


def test_warm_cache_command(project, capsys):
    assert run_cli("warm-cache", "--pathogens", "Neisseria meningitidis, zzqx notanorganism") == 3
    text = capsys.readouterr().out
    assert "Neisseria meningitidis: S1 ok" in text and "zzqx notanorganism: not resolved" in text
    assert (project / "cache" / "uniprot" / "proteomes" / "UP000000001.faa").is_file()


def test_unknown_stage_and_missing_args_are_clean_errors(project, capsys):
    assert run_cli("run") == 1
    assert "--pathogen" in capsys.readouterr().err
    assert run_cli("run", "--pathogen", "x", "--stages", "s0,s99") == 1


def test_doctor_runs(project, capsys):
    code = run_cli("doctor")
    out = capsys.readouterr().out
    assert code in (0, 1) and "BLAST+" in out and "PSORTb" in out and "cache dir" in out


def test_interactive_chooser_prompts_until_valid(monkeypatch, capsys):
    res = S0.Resolution("needs_user_input", "ambiguous", "exact", "q", "pick one",
                        candidates=[{"taxid": 487, "scientific_name": "A b", "rank": "species"},
                                    {"taxid": 485, "scientific_name": "C d", "rank": "species"}])
    answers = iter(["zzz", "2"])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert cli.interactive_chooser(res) == 485                    # invalid text re-prompts, then "2" picks the 2nd candidate
    monkeypatch.setattr("builtins.input", lambda prompt="": "9999")
    assert cli.interactive_chooser(res) == 9999                   # a number beyond the list is taken as a taxid
    monkeypatch.setattr("builtins.input", lambda prompt="": "")
    assert cli.interactive_chooser(res) is None                   # blank aborts
    assert "pick one" in capsys.readouterr().out


# ------------------------------------------------------------------ hooks for other streams
def test_later_stage_modules_are_picked_up_and_extension_commands_registered(project, capsys, monkeypatch):
    def fake_run(store, config):
        a = store.read_artifact("s2")
        return store.write_artifact("s3", {"ranking": [], "n_input": len(a.payload["proteins"])}, inputs=["s2"])

    s3 = types.ModuleType("vaxforge.s3_rank")
    s3.run = fake_run
    monkeypatch.setitem(sys.modules, "vaxforge.s3_rank", s3)

    ext = types.ModuleType("vaxforge.validate_cli")
    ext.register_cli = lambda sub, common: sub.add_parser("validate", parents=[common]).set_defaults(func=lambda a: 42)
    monkeypatch.setitem(sys.modules, "vaxforge.validate_cli", ext)

    out = project / "runs" / "hook"
    code = run_cli("run", "--pathogen", "Neisseria meningitidis", "--out", str(out), "--non-interactive", "--stages", "s0,s1,s2,s3")
    text = capsys.readouterr().out
    assert code == 0, text
    assert RunStore(out).read_artifact("s3").payload["n_input"] == 3
    assert RunStore(out, cache_dir=project / "cache").verify() == []       # s3's recorded upstream digest matches s2
    assert run_cli("validate") == 42


# ------------------------------------------------------------------ tools
def test_ascii_safe_dir_and_runner_discovery(tmp_path):
    plain = tmp_path / "plain"
    assert ascii_safe_dir(plain, "x") == plain
    odd = tmp_path / "数据" / "cache"
    safe = ascii_safe_dir(odd, "x")
    assert str(safe).isascii() and safe.is_dir() and safe == ascii_safe_dir(odd, "x")      # stable across calls
    cfg = make_config(tmp_path)
    runner = BlastRunner(cfg, odd)
    assert runner.mode in ("native", "docker", "missing") and str(runner.db_root()).isascii()
    with pytest.raises(Exception):
        BlastRunner(make_config(tmp_path, tools={"blast_mode": "docker", "docker": "definitely-not-docker"}), odd).require() \
            if shutil.which("docker") is None else (_ for _ in ()).throw(Exception("docker present"))


def test_closing_line_reports_degraded_s2():
    from types import SimpleNamespace
    from vaxforge.cli import closing_line

    def art(v, loc):
        return SimpleNamespace(payload={"summary": {"virulence": {"run_status": v}, "localisation": {"run_status": loc}}})

    assert closing_line(["s0", "s1", "s2"], art("ok", "ok")).endswith("(verify: ok)")
    assert "(degraded: S2 virulence and localisation not_run; verify: ok)" in closing_line(["s2"], art("not_run", "not_run"))
    assert "degraded: S2 localisation not_run;" in closing_line(["s2"], art("ok", "not_run"))
    assert closing_line(["s0"], None).endswith("(verify: ok)")


def test_stage_returning_a_path_and_a_failed_stage_reason(project, capsys, monkeypatch):
    def path_run(store, config):                       # like stream B: returns the artifact's Path, not an Artifact
        store.write_artifact("s3", {"ranking": []}, inputs=["s2"])
        return store.artifact_path("s3")

    def failing_run(store, config):
        return store.write_artifact("s4", {"gates": []}, inputs=["s3"], status="failed", warnings=["gate tool crashed: boom"])

    for stage in ("s3", "s4"):                         # another test may have imported the real stage and registered its keys
        monkeypatch.delitem(A.REQUIRED_PAYLOAD_KEYS, stage, raising=False)
    for name, fn in (("s3_rank", path_run), ("s4_filter", failing_run)):
        mod = types.ModuleType(f"vaxforge.{name}")
        mod.run = fn
        monkeypatch.setitem(sys.modules, f"vaxforge.{name}", mod)
    out = project / "runs" / "pathrun"
    code = run_cli("run", "--pathogen", "Neisseria meningitidis", "--out", str(out), "--non-interactive", "--stages", "s0,s1,s2,s3,s4")
    text = capsys.readouterr().out
    assert re.search(r"^s3\s+ok\s*$", text, re.M), text        # the returned Path was turned back into the artifact
    assert RunStore(out).read_artifact("s3").status == "ok"
    assert code == cli.EXIT_ERROR and "reason: gate tool crashed: boom" in text


# ------------------------------------------------------------------ audit items: later stages without --pathogen, demo, degraded, doctor, --set
def fixture_copy(tmp_path: Path) -> Path:
    dst = tmp_path / "fixture_run"
    shutil.copytree(ROOT / "fixtures" / "nmb_fixture", dst)
    return dst


def fake_stage(monkeypatch, module: str, stage: str, payload: dict, inputs: list[str]) -> None:
    monkeypatch.delitem(A.REQUIRED_PAYLOAD_KEYS, stage, raising=False)
    mod = types.ModuleType(f"vaxforge.{module}")
    mod.run = lambda store, config: store.write_artifact(stage, payload, inputs=inputs)
    monkeypatch.setitem(sys.modules, f"vaxforge.{module}", mod)


def test_later_stages_run_without_pathogen_but_s0_and_a_missing_out_still_need_input(project, tmp_path, capsys, monkeypatch):
    run = fixture_copy(tmp_path)
    fake_stage(monkeypatch, "s3_rank", "s3", {"ranking": [], "degraded": ["fallback ranker"]}, ["s2"])
    assert run_cli("run", "--out", str(run), "--stages", "s3", "--offline") == 0
    out = capsys.readouterr().out
    assert re.search(r"^s3\s+ok\s*$", out, re.M) and "  S3: fallback ranker" in out          # the reason, printed above the closing line
    assert "(degraded: S3 (1 reason); verify: ok)" in out
    assert run_cli("run", "--stages", "s0,s1") != 0 and "give --pathogen" in capsys.readouterr().err      # S0 needs a target
    assert run_cli("run", "--stages", "s3") != 0 and "give --out RUN_DIR" in capsys.readouterr().err       # no name to derive a folder


def test_closing_line_names_every_kind_of_degradation():
    from types import SimpleNamespace

    def art(v, loc):
        return SimpleNamespace(payload={"summary": {"virulence": {"run_status": v}, "localisation": {"run_status": loc}}})

    line = cli.closing_line(["s2", "s3"], art("ok", "fixture_stand_in"), {"s3": ["fallback ranker"], "s4": ["0 of 4 gates ran", "no AlgPred2"]})
    assert "S2 localisation fixture_stand_in" in line and "S3 (1 reason)" in line
    assert "S4 (2 reasons)" in line and line.endswith("; verify: ok)") and "fallback ranker" not in line   # sentences go above the line
    assert cli.closing_line(["s2"], art("ok", "not_applicable")).endswith("(verify: ok)")          # a virus is not a degradation
    assert cli.closing_line(["s2"], art("ok", "ok"), {}).endswith("(verify: ok)")


def test_degraded_by_stage_reads_only_non_empty_lists(tmp_path, monkeypatch):
    run = fixture_copy(tmp_path)
    store = RunStore(run)
    for stage in ("s3", "s4", "s5", "s6"):
        monkeypatch.delitem(A.REQUIRED_PAYLOAD_KEYS, stage, raising=False)
    store.write_artifact("s3", {"x": 1, "degraded": ["fallback ranker", "no scores file"]}, inputs=["s2"])
    store.write_artifact("s4", {"x": 1, "degraded": []}, inputs=["s3"])                  # empty list: nothing degraded
    store.write_artifact("s5", {"x": 1}, inputs=["s4"])                                   # key absent: not reported
    store.write_artifact("s6", {"x": 1, "degraded": "not a list"}, inputs=["s5"])         # wrong shape: ignored, never a crash
    assert cli.degraded_by_stage(store) == {"s3": ["fallback ranker", "no scores file"]}


def test_demo_offline_copies_the_fixture_and_keeps_every_stage_offline(tmp_path, monkeypatch, capsys):
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv=None: calls.append(list(argv)) or 0)
    default_scores = tmp_path / "default_scores.tsv"                                     # stands in for stream B's file in docs/examples
    default_scores.write_text("protein_id\tscore\nQ1\t90\n", encoding="utf-8")
    monkeypatch.setattr(cli, "DEFAULT_DEMO_SCORES", default_scores)
    evidence, manifest = tmp_path / "evidence.json", tmp_path / "manifest.json"          # stand in for stream D's real earlier run
    evidence.write_text(json.dumps({"_about": {"what": "A REAL earlier run."}, "caveat": "best_win_rate is not protocol quality."}), encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(cli, "DEMO_EVIDENCE", evidence)
    monkeypatch.setattr(cli, "DEMO_MANIFEST", manifest)
    out = tmp_path / "demo"

    def go(*extra: str) -> int:
        args = cli.build_parser().parse_args(["demo-offline", "--out", str(out), *extra])
        return args.func(args)

    def sets(argv: list[str]) -> list[str]:
        return [argv[i + 1] for i, a in enumerate(argv) if a == "--set"]

    def scores_used(argv: list[str]) -> Path:
        key, value = cli.parse_override(next(x for x in sets(argv) if x.startswith("s3.scores_file=")))
        assert key == "s3.scores_file"
        return Path(value)

    assert go() == 0                                                                      # default: the real Vaxign-ML scores file
    assert (out / "manifest.json").is_file() and (out / "s2_annotate.json").is_file()
    argv = calls[-1]
    assert argv[0] == "run" and "--offline" in argv and "--non-interactive" in argv and "--pathogen" not in argv
    assert argv[argv.index("--stages") + 1] == cli.DEMO_STAGES and "s0" not in cli.DEMO_STAGES.split(",")
    assert "s3.backend=scores_file" in sets(argv) and scores_used(argv) == default_scores.resolve()
    assert "s7.boltz.msa=empty" in sets(argv)                                             # no MSA server, no container (never backend auto)
    text = capsys.readouterr().out
    assert "holdout_enforced is false" in text and "not a validation" in text             # said plainly, not buried in a warning
    assert "NOT produced by this demo" in text and "A REAL earlier run." in text and "caveat: best_win_rate is not protocol quality." in text
    s = sets(argv)
    ev_key, ev_value = cli.parse_override(next(x for x in s if x.startswith("finetune.evidence=")))
    mf_key, mf_value = cli.parse_override(next(x for x in s if x.startswith("finetune.manifest=")))
    assert (ev_key, Path(ev_value)) == ("finetune.evidence", evidence.resolve())
    assert (mf_key, Path(mf_value)) == ("finetune.manifest", manifest.resolve())

    with pytest.raises(ConfigError, match="not empty"):                                  # never overwrite silently
        go()
    (out / "marker.txt").write_text("x", encoding="utf-8")
    own = tmp_path / "own_scores.tsv"
    own.write_text("protein_id\tscore\nQ1\t0.9\n", encoding="utf-8")
    assert go("--force", "--scores-file", str(own)) == 0
    assert not (out / "marker.txt").exists()                                              # --force replaced the folder
    assert "s3.backend=scores_file" in sets(calls[-1]) and scores_used(calls[-1]) == own.resolve()
    assert "holdout_enforced is false" not in capsys.readouterr().out                    # the caveat belongs to the default file only

    assert go("--force", "--rule-based", "--no-evidence") == 0
    assert "s3.backend=subtractive_filter" in sets(calls[-1]) and not any(x.startswith("s3.scores_file=") for x in sets(calls[-1]))
    assert not any(x.startswith("finetune.") for x in sets(calls[-1]))                    # --no-evidence: S9.5 records that no fine-tune ran
    with pytest.raises(ConfigError, match="exclude each other"):
        go("--force", "--rule-based", "--scores-file", str(own))
    with pytest.raises(ConfigError, match="scores file not found"):
        go("--force", "--scores-file", str(tmp_path / "missing.tsv"))


def test_demo_offline_writes_repo_relative_paths_when_run_from_the_repo_root(tmp_path, monkeypatch):
    """A committed demo folder must not carry local folder names: relative from the repo root, absolute from anywhere else."""
    calls: list[list[str]] = []
    monkeypatch.setattr(cli, "main", lambda argv=None: calls.append(list(argv)) or 0)
    root = tmp_path / "repo"
    examples = root / "docs" / "examples"
    examples.mkdir(parents=True)
    scores, evidence, manifest = examples / "scores.tsv", examples / "evidence.json", examples / "manifest.json"
    scores.write_text("protein_id\tscore\nQ1\t90\n", encoding="utf-8")
    evidence.write_text("{}", encoding="utf-8")
    manifest.write_text("{}", encoding="utf-8")
    fixture = root / "fixture"
    fixture.mkdir()
    (fixture / "manifest.json").write_text("{}", encoding="utf-8")
    for name, value in (("REPO_ROOT", root), ("DEFAULT_DEMO_SCORES", scores), ("DEMO_EVIDENCE", evidence), ("DEMO_MANIFEST", manifest)):
        monkeypatch.setattr(cli, name, value)

    def sets(argv: list[str]) -> dict[str, object]:
        return dict(cli.parse_override(argv[i + 1]) for i, a in enumerate(argv) if a == "--set")

    def run_demo(out: Path) -> dict[str, object]:
        args = cli.build_parser().parse_args(["demo-offline", "--out", str(out), "--fixture", str(fixture)])
        assert args.func(args) == 0
        return sets(calls[-1])

    monkeypatch.chdir(root)
    here = run_demo(root / "demo_data" / "run")
    assert here["s3.scores_file"] == "docs/examples/scores.tsv"
    assert here["finetune.evidence"] == "docs/examples/evidence.json" and here["finetune.manifest"] == "docs/examples/manifest.json"
    other = tmp_path / "elsewhere"
    other.mkdir()
    monkeypatch.chdir(other)
    away = run_demo(tmp_path / "demo2")
    assert Path(str(away["s3.scores_file"])).is_absolute() and Path(str(away["finetune.evidence"])).is_absolute()


def test_set_option_overrides_config_values_and_rejects_garbage(project):
    args = cli.build_parser().parse_args(["show", "x", "--set", "s3.backend=scores_file", "--set", "a.b=3",
                                          "--set", 's3.scores_file="C:\\\\data\\\\s.tsv"', "--set", "flag=true"])
    cfg = cli.load_from_args(args)
    assert cfg.get("s3.backend") == "scores_file" and cfg.get("a.b") == 3 and cfg.get("flag") is True
    assert cfg.get("s3.scores_file") == "C:\\data\\s.tsv"                                  # JSON string: backslashes survive
    with pytest.raises(ConfigError, match="KEY=VALUE"):
        cli.load_from_args(cli.build_parser().parse_args(["show", "x", "--set", "novalue"]))


def test_pipeline_tool_checks_say_found_or_not_found_and_never_run_anything(tmp_path, monkeypatch):
    import vaxforge.prediction.tools as pt

    cfg = make_config(tmp_path)
    fake_py = tmp_path / "ot-python.exe"
    fake_py.write_text("", encoding="utf-8")
    monkeypatch.setenv("VAXFORGE_OT_PYTHON", str(fake_py))
    monkeypatch.setattr(cli.tools, "docker_state", lambda d, image=None, timeout=10.0: ("no_image", f"image not pulled: {image}"))
    monkeypatch.setattr(cli.importlib.util, "find_spec", lambda name, *a, **k: object() if name == "mhcflurry" else None)
    monkeypatch.setattr(pt, "resolve_executable", lambda cmd, default=None: ["/opt/bin/boltz"] if default == "boltz" else None)
    rows = {label: (text, level) for label, text, level in cli.pipeline_tool_checks(cfg)}
    assert list(rows) == ["Opentrons (S9)", "Vaxign-ML (S3)", "AlgPred2 (S4)", "ToxinPred2 (S4)", "NetMHCpan (S5)",
                          "NetMHCIIpan (S5)", "BepiPred3 (S5)", "DiscoTope3 (S5)", "Boltz (S7)", "MHCflurry (S5)"]
    assert rows["Opentrons (S9)"][1] == "ok" and "not probed" in rows["Opentrons (S9)"][0]
    assert rows["Vaxign-ML (S3)"][1] == "warn" and "image not pulled" in rows["Vaxign-ML (S3)"][0] and "rule-based" in rows["Vaxign-ML (S3)"][0]
    assert rows["Boltz (S7)"] == ("found: /opt/bin/boltz", "ok") and rows["MHCflurry (S5)"][1] == "ok"
    assert rows["AlgPred2 (S4)"][1] == "warn" and "not found" in rows["AlgPred2 (S4)"][0]
    monkeypatch.setenv("VAXFORGE_OT_PYTHON", str(tmp_path / "gone.exe"))                 # points nowhere: a warning, not a pass
    assert dict((l, v) for l, _, v in cli.pipeline_tool_checks(cfg))["Opentrons (S9)"] == "warn"


def test_doctor_prints_the_later_stage_tools(project, capsys):
    assert run_cli("doctor") == 0
    text = capsys.readouterr().out
    assert "later stages S3-S12 (advisory" in text and "Boltz (S7)" in text and "Vaxign-ML (S3)" in text
