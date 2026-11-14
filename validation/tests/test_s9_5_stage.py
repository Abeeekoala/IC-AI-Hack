"""S9.5 as a pipeline stage and as `vaxforge finetune ...`, exercised through stream A's real RunStore and CLI."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "src")]

import pytest  # noqa: E402
from vaxforge import cli  # noqa: E402
from vaxforge import s9_5_finetune as ft  # noqa: E402
from vaxforge.artifacts import RunStore  # noqa: E402
from vaxforge.config import Config  # noqa: E402

UNSET = {"finetune": {"base_model_id": None, "model_id": None, "eval_tasks": 11, "seed": 20261114}}
SET = {"finetune": {"base_model_id": "Qwen/Qwen3.5-9B", "model_id": "run-1", "eval_tasks": 11, "seed": 20261114}}


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "runs" / "t", cache_dir=tmp_path / "cache")


def cfg(tmp_path, data):
    return Config(data, root=tmp_path)


def test_with_no_upstream_and_no_fine_tune_the_stage_writes_an_ok_not_run_artifact(store, tmp_path):
    art = ft.run(store, cfg(tmp_path, UNSET))
    assert art.status == "ok"  # a non-ok status would stop `vaxforge run`
    fine_tune = art.payload["fine_tune"]
    assert fine_tune["status"] == "not_run"
    text = " ".join(fine_tune["reasons"])
    assert "s8, s9" in text and "finetune.base_model_id is not set" in text and "finetune.model_id is not set" in text
    assert art.warnings and art.warnings[0].startswith("S9.5 not run:")
    assert art.inputs == {} and store.verify() == []
    assert art.payload["base_model_id"] is None and art.payload["model_id"] is None


def test_with_s8_and_s9_present_both_digests_are_recorded_and_the_upstream_reason_goes_away(store, tmp_path):
    store.write_artifact("s8", {}, inputs=[], status="ok")
    store.write_artifact("s9", {}, inputs=[], status="ok")
    art = ft.run(store, cfg(tmp_path, UNSET))
    assert set(art.inputs) == {"s8", "s9"}
    assert "upstream" not in " ".join(art.payload["fine_tune"]["reasons"])
    assert store.verify() == []


def test_configured_ids_are_recorded_but_no_result_is_invented(store, tmp_path):
    art = ft.run(store, cfg(tmp_path, SET))
    assert art.payload["base_model_id"] == "Qwen/Qwen3.5-9B" and art.payload["model_id"] == "run-1"
    assert art.payload["fine_tune"]["status"] == "not_run"
    assert set(art.payload) == {"fine_tune", "base_model_id", "model_id"}  # no win rate, no code, nothing computed
    assert art.params == {"eval_tasks": 11, "seed": 20261114}


def test_the_stage_is_the_one_vaxforge_run_calls(store, tmp_path):
    outcome, art = cli.run_extra_stage("s9_5", store, cfg(tmp_path, UNSET))
    assert outcome == "ran" and art.status == "ok" and art.stage == "s9_5"


def test_the_shipped_config_has_the_finetune_section():
    section = cli.load_config(None).section("finetune")
    assert section == {"base_model_id": None, "model_id": None, "eval_tasks": 11, "seed": 20261114}


def _candidates(path, n):
    rows = [{"id": f"p{i}", "source": "opentrons-apache", "prompt": f"prompt {i}", "code": f"code {i}",
             "heldout_family": "none", "family_doubt": False, "refused_by_base": False, "simulate_pass": True}
            for i in range(n)]
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def test_finetune_manifest_runs_through_the_real_cli(tmp_path, capsys):
    src, out, train = tmp_path / "c.jsonl", tmp_path / "m.json", tmp_path / "t.jsonl"
    _candidates(src, 3)
    code = cli.main(["finetune", "manifest", str(src), "--out", str(out), "--training-out", str(train),
                     "--min-rows", "2"])
    assert code == 0
    assert json.loads(out.read_text())["counts"]["included"] == 3
    assert len(train.read_text().splitlines()) == 3
    assert "3 of 3 candidates included" in capsys.readouterr().out


def test_finetune_winrate_and_check_pass_their_arguments_and_exit_codes_through(tmp_path, capsys):
    empty = tmp_path / "r.jsonl"
    empty.write_text("", encoding="utf-8")
    assert cli.main(["finetune", "winrate", str(empty), "--resamples", "10"]) == 0
    assert json.loads(capsys.readouterr().out)["n"] == 0
    assert cli.main(["finetune", "check", str(tmp_path / "missing.json"), str(empty), "--heldout-dir", str(tmp_path)]) == 1
    empty.write_text("{oops\n", encoding="utf-8")
    assert cli.main(["finetune", "winrate", str(empty)]) == 1


def test_a_missing_checkout_is_a_clear_error_not_a_crash(monkeypatch, tmp_path, capsys):
    monkeypatch.setattr(ft, "_validation_module", lambda name: (_ for _ in ()).throw(ValueError("no validation/ here")))
    assert cli.main(["finetune", "winrate", str(tmp_path / "r.jsonl")]) == 1
    assert "no validation/ here" in capsys.readouterr().err
