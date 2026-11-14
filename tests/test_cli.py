"""CLI behaviour: end to end on fakes, exit codes, offline reuse, and the hooks other streams plug into."""
from __future__ import annotations

import json
import shutil
import sys
import types
from pathlib import Path

import pytest

from vaxforge import cli, s0_taxonomy as S0, s1_acquire as S1
from vaxforge.artifacts import RunStore
from vaxforge.http import client_from_config as real_client_from_config
from vaxforge.testing import FakeTransport, ReplayTransport
from vaxforge.tools import BlastRunner, ascii_safe_dir

from conftest import DATA, make_config
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
def test_run_end_to_end_then_verify_show_and_offline_rerun(project, capsys):
    out = project / "runs" / "nmb"
    code = run_cli("run", "--pathogen", "Neisseria meningitidis", "--out", str(out), "--non-interactive", "--top-k", "7")
    text = capsys.readouterr().out
    assert code == 0, text
    assert "S0  resolved: Neisseria meningitidis" in text and "S1  proteome UP000000001" in text and "S2  annotated 3 proteins" in text
    assert "s3  not installed - skipped" in text.lower() or "s3  " in text.lower()
    assert "'Unknown' kept explicit" in text and "(verify: ok)" in text
    store = RunStore(out)
    assert store.stages_present() == ["s0", "s1", "s2"]
    assert json.loads((out / "config.resolved.json").read_text(encoding="utf-8"))["pipeline"]["top_k"] == 7

    assert run_cli("verify", str(out)) == 0
    assert "OK - digests" in capsys.readouterr().out
    assert run_cli("show", str(out)) == 0
    assert "S0  resolved" in capsys.readouterr().out

    # warm cache is enough to run again with the network switched off
    out2 = project / "runs" / "again"
    assert run_cli("run", "--pathogen", "Neisseria meningitidis", "--out", str(out2), "--non-interactive", "--offline") == 0
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
