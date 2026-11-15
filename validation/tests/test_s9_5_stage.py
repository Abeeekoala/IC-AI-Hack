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
    assert section == {"base_model_id": None, "model_id": None, "eval_tasks": 11, "seed": 20261114,
                       "evidence": None, "manifest": None}


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


# ---- attaching real Adaption evidence ----------------------------------------------------------------------------------

EVIDENCE = {"schema_version": 1, "run_id": "82c660bc-2c35-4917-b4d1-e7c58039b904", "status": "succeeded", "training_method": "instruction",
            "base_model_id": "Qwen/Qwen3.5-4B", "trained_model_name": "adaption_ot2_liquid_handling_sft", "training_type": "lora",
            "iterations_completed": 1, "max_iterations": 2, "best_win_rate": 0.7832, "target_win_rate": 0.6, "target_reached": True,
            "checkpoint_sha256": "c" * 64, "created_at_utc": "2026-11-15T01:43:38Z", "completed_at_utc": "2026-11-15T02:24:45Z",
            "caveat": "best_win_rate is Adaption's own optimisation target"}
MANIFEST = {"counts": {"candidates": 3121, "included": 1000, "included_synthetic": 571}, "meets_min_rows": True, "max_row_chars": 32000}


def write_json(path, doc):
    path.write_text(json.dumps(doc), encoding="utf-8")
    return str(path)


def test_configured_evidence_is_attached_hashed_and_summarised_without_inventing_a_win_rate_of_ours(store, tmp_path):
    ev, man = write_json(tmp_path / "ev.json", EVIDENCE), write_json(tmp_path / "man.json", MANIFEST)
    data = {"finetune": {**UNSET["finetune"], "evidence": ev, "manifest": man}}
    art = ft.run(store, cfg(tmp_path, data))
    fine_tune = art.payload["fine_tune"]
    assert fine_tune["status"] == "evidence_attached" and fine_tune["reasons"] == []
    assert fine_tune["adaption"]["best_win_rate"] == 0.7832 and fine_tune["adaption"]["run_id"] == EVIDENCE["run_id"]
    assert fine_tune["manifest_counts"] == {"candidates": 3121, "included": 1000, "included_synthetic": 571,
                                            "meets_min_rows": True, "max_row_chars": 32000}
    assert fine_tune["held_out_win_rate"] is None  # ours is not computed here
    assert art.payload["base_model_id"] == "Qwen/Qwen3.5-4B" and art.payload["model_id"] == EVIDENCE["run_id"]
    assert any("platform's own number" in w for w in art.warnings) and art.status == "ok"
    assert (store.run_dir / "data" / "s9_5" / "adaption_evidence.json").is_file() and store.verify() == []


def test_a_file_that_is_not_adaption_evidence_is_a_config_error(store, tmp_path):
    from vaxforge.config import ConfigError
    bad = write_json(tmp_path / "bad.json", {"hello": 1})
    with pytest.raises(ConfigError, match="not an adaption_evidence.json"):
        ft.run(store, cfg(tmp_path, {"finetune": {**UNSET["finetune"], "evidence": bad}}))
    with pytest.raises(ConfigError, match="cannot read"):
        ft.run(store, cfg(tmp_path, {"finetune": {**UNSET["finetune"], "evidence": str(tmp_path / "missing.json")}}))
    with pytest.raises(ConfigError, match="finetune.evidence is not"):
        ft.run(store, cfg(tmp_path, {"finetune": {**UNSET["finetune"], "manifest": write_json(tmp_path / "m.json", MANIFEST)}}))
