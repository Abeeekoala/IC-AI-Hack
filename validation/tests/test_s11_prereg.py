"""S11 pre-registration: what is frozen, what is refused, and what `vaxforge prereg verify` can detect."""
import json
import shutil
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

import pytest  # noqa: E402
from vaxforge import cli  # noqa: E402
from vaxforge import s11_prereg as pr  # noqa: E402
from vaxforge.artifacts import RunStore, compute_digest  # noqa: E402
from vaxforge.config import Config  # noqa: E402

FIXTURE = ROOT / "fixtures" / "nmb_fixture"  # a real, committed S0-S2 run
CONFIG = {"reproducibility": {"seed": 20261114, "pin_versions": True}, "finetune": {"seed": 7}, "pipeline": {"top_k": 20},
          "thresholds": {"protegenicity": [0.9, 0.85], "allergenicity": 0.45}, "epitopes": {"mhc_class_ii_weight": 0.3},
          "structure": {"engine": "boltz-2"}}
T0, T1 = "2026-11-14T12:00:00Z", "2026-11-14T13:00:00Z"

S3 = {"candidates": [{"protein_id": "A", "sequence": "MKV", "rank": 1}],
      "ranking": [{"protein_id": "A", "rank": 1, "composite_score": 0.9, "protegenicity": 0.9},
                  {"protein_id": "B", "rank": 2, "composite_score": 0.5},
                  {"protein_id": "C", "rank": 3, "composite_score": 0.1}]}
S4 = {"candidates": [{"protein_id": "A", "sequence": "MKV"}, {"protein_id": "C", "sequence": "MKL"}],
      "rejected": [{"protein_id": "B", "rejected_by": ["toxicity"], "recoverable": True}],
      "incomplete_screening": False, "unavailable_gates": []}


def cfg(tmp_path, data=CONFIG):
    return Config(data, root=tmp_path)


@pytest.fixture
def store(tmp_path):
    shutil.copytree(FIXTURE, tmp_path / "runs" / "t")
    return RunStore(tmp_path / "runs" / "t")


def write_s3_s4(store, s3=S3, s4=S4):
    store.write_artifact("s3", s3, params={"seed": 1}, tools={"vaxign-ml": "1.0"}, inputs=["s2"])
    store.write_artifact("s4", s4, params={}, tools={"blastp": "2.17.0+"}, inputs=["s3", "s1"])


def frozen_of(art):
    return art.payload["frozen"]


def test_it_freezes_upstream_digests_tool_versions_seeds_thresholds_and_the_prediction_lists(store, tmp_path):
    write_s3_s4(store)
    art = pr.run(store, cfg(tmp_path), clock=lambda: T0)
    f = frozen_of(art)
    assert art.status == "ok" and f["registered_at"] == T0
    for stage in ("s0", "s1", "s2", "s3", "s4"):
        assert f["upstream"][stage]["digest"] == store.digest_of(stage) and f["upstream"][stage]["status"] == "ok"
    assert f["upstream"]["s3"]["tools"] == {"vaxign-ml": "1.0"} and f["upstream"]["s4"]["tools"] == {"blastp": "2.17.0+"}
    assert f["config"]["reproducibility.seed"] == 20261114 and f["config"]["finetune.seed"] == 7
    assert f["config"]["thresholds"] == CONFIG["thresholds"] and f["config_digest"] == cfg(tmp_path).digest()
    assert f["predictions"]["s3"]["ranking"] == [{"protein_id": "A", "rank": 1, "composite_score": 0.9},
                                                {"protein_id": "B", "rank": 2, "composite_score": 0.5},
                                                {"protein_id": "C", "rank": 3, "composite_score": 0.1}]
    assert f["predictions"]["s4"] == {"status": "ok", "survivors": ["A", "C"],
                                      "rejected": [{"protein_id": "B", "rejected_by": ["toxicity"]}],
                                      "incomplete_screening": False, "unavailable_gates": []}
    assert "MKV" not in json.dumps(art.payload)  # sequences are not copied into the manifest
    assert art.payload["frozen_digest"] == pr.frozen_digest(f) and "published" in art.payload["note"]
    assert set(art.inputs) == {"s0", "s1", "s2", "s3", "s4"} and store.verify() == []


def test_a_stage_that_has_not_run_is_an_explicit_not_run_never_an_empty_list(store, tmp_path):
    art = pr.run(store, cfg(tmp_path), clock=lambda: T0)
    f = frozen_of(art)
    assert art.status == "ok"
    assert f["predictions"] == {"s3": {"status": "not_run"}, "s4": {"status": "not_run"}}
    assert all(f["upstream"][s] == {"status": "not_run"} for s in ("s3", "s4", "s7", "s8", "s9", "s9_5", "s10"))
    assert any("no prediction list frozen for: s3 (not_run), s4 (not_run)" in w for w in art.warnings)
    assert "s3" not in art.inputs and store.verify() == []


def test_s3_alone_freezes_the_ranking_and_marks_s4_not_run(store, tmp_path):
    store.write_artifact("s3", S3, inputs=["s2"])
    f = frozen_of(pr.run(store, cfg(tmp_path), clock=lambda: T0))
    assert len(f["predictions"]["s3"]["ranking"]) == 3 and f["predictions"]["s4"] == {"status": "not_run"}


def test_a_stage_that_ran_without_status_ok_is_recorded_with_its_status_and_no_list(store, tmp_path):
    store.write_artifact("s3", {}, inputs=["s2"], status="failed", warnings=["Vaxign-ML did not run"])
    art = pr.run(store, cfg(tmp_path), clock=lambda: T0)
    assert frozen_of(art)["predictions"]["s3"] == {"status": "failed"}
    assert frozen_of(art)["upstream"]["s3"]["status"] == "failed"
    assert any("s3 (failed)" in w for w in art.warnings)


def test_config_values_that_are_not_set_are_frozen_as_null_and_warned_about(store, tmp_path):
    art = pr.run(store, cfg(tmp_path, {"reproducibility": {"pin_versions": True}}), clock=lambda: T0)
    c = frozen_of(art)["config"]
    assert c["reproducibility.seed"] is None and c["finetune.seed"] is None and c["thresholds"] is None
    assert any(w.startswith("not set in config, frozen as null:") and "reproducibility.seed" in w for w in art.warnings)


def test_the_shipped_config_is_frozen_as_it_stands(store):
    shipped = cli.load_config(None)
    c = frozen_of(pr.run(store, shipped, clock=lambda: T0))["config"]
    assert c["reproducibility.seed"] == shipped.get("reproducibility.seed") is not None
    assert c["thresholds"] == shipped.get("thresholds")


def test_same_inputs_and_clock_give_the_same_digests_and_a_different_clock_gives_a_different_one(store, tmp_path):
    write_s3_s4(store)
    a, b = pr.run(store, cfg(tmp_path), clock=lambda: T0), pr.run(store, cfg(tmp_path), clock=lambda: T0)
    c = pr.run(store, cfg(tmp_path), clock=lambda: T1)
    assert a.payload["frozen_digest"] == b.payload["frozen_digest"] and a.digest == b.digest
    assert c.payload["frozen_digest"] != a.payload["frozen_digest"]  # the timestamp is part of what is bound


def test_it_never_touches_the_network_and_the_default_clock_is_a_utc_timestamp(store, tmp_path, monkeypatch):
    def no_socket(*a, **k):
        raise AssertionError("S11 must not use the network")
    monkeypatch.setattr(socket, "socket", no_socket)
    art = pr.run(store, cfg(tmp_path))
    assert frozen_of(art)["registered_at"].endswith("Z") and art.status == "ok"


def test_it_refuses_while_any_validation_output_exists_and_writes_nothing(store, tmp_path, capsys):
    (store.run_dir / "validation" / "bexsero").mkdir(parents=True)
    (store.run_dir / "validation" / "bexsero" / "recall.json").write_text("{}")
    art = pr.run(store, cfg(tmp_path), clock=lambda: T0)
    assert art.status == "failed" and "validation/bexsero/recall.json" in art.warnings[0]
    assert "not one" in art.warnings[0]
    assert not store.has("s11") and "s11" not in store.read_manifest().get("stages", {})
    assert "refusing to pre-register" in capsys.readouterr().err


def test_an_empty_validation_folder_is_not_a_validation_output(store, tmp_path):
    (store.run_dir / "validation").mkdir()
    assert pr.run(store, cfg(tmp_path), clock=lambda: T0).status == "ok"


def test_a_refusal_leaves_an_existing_manifest_untouched(store, tmp_path):
    write_s3_s4(store)
    first = pr.run(store, cfg(tmp_path), clock=lambda: T0)
    before = store.artifact_path("s11").read_bytes()
    (store.run_dir / "validation").mkdir()
    (store.run_dir / "validation" / "winrate.json").write_text("{}")
    assert pr.run(store, cfg(tmp_path), clock=lambda: T1).status == "failed"
    assert store.artifact_path("s11").read_bytes() == before
    assert store.read_artifact("s11").digest == first.digest and pr.verify(store) == []


def test_verify_passes_on_an_untouched_manifest(store, tmp_path):
    write_s3_s4(store)
    pr.run(store, cfg(tmp_path), clock=lambda: T0)
    assert pr.verify(store) == []


def test_verify_says_so_when_the_run_was_never_pre_registered(store):
    assert pr.verify(store) == ["s11_prereg.json not found: this run was never pre-registered"]


def edit_manifest(store, change, redo_envelope_digest):
    path = store.artifact_path("s11")
    doc = json.loads(path.read_text())
    change(doc["payload"]["frozen"])
    if redo_envelope_digest:
        doc["digest"] = compute_digest(doc)
    path.write_text(json.dumps(doc))


def drop_the_best_prediction(frozen):
    frozen["predictions"]["s3"]["ranking"].pop(0)


def test_verify_detects_an_edit_of_the_frozen_manifest_even_if_the_envelope_digest_was_recomputed(store, tmp_path):
    write_s3_s4(store)
    pr.run(store, cfg(tmp_path), clock=lambda: T0)
    edit_manifest(store, drop_the_best_prediction, redo_envelope_digest=True)
    assert pr.verify(store) == ["the frozen manifest does not match frozen_digest: it was edited after the fact"]


def test_verify_detects_a_plain_edit_with_both_checks(store, tmp_path):
    write_s3_s4(store)
    pr.run(store, cfg(tmp_path), clock=lambda: T0)
    edit_manifest(store, drop_the_best_prediction, redo_envelope_digest=False)
    problems = pr.verify(store)
    assert len(problems) == 2 and "envelope digest" in problems[0] and "edited after the fact" in problems[1]


def test_verify_detects_an_upstream_artifact_that_changed_since(store, tmp_path):
    write_s3_s4(store)
    pr.run(store, cfg(tmp_path), clock=lambda: T0)
    store.write_artifact("s3", {**S3, "ranking": S3["ranking"][::-1]}, params={"seed": 1}, tools={"vaxign-ml": "1.0"}, inputs=["s2"])
    problems = pr.verify(store)
    assert len(problems) == 1 and problems[0].startswith("s3: changed since registration (was sha256:")


def test_verify_detects_an_upstream_artifact_that_appeared_or_vanished_since(store, tmp_path):
    write_s3_s4(store)
    pr.run(store, cfg(tmp_path), clock=lambda: T0)
    store.write_artifact("s7", {}, inputs=[], status="ok")
    store.artifact_path("s4").unlink()
    problems = pr.verify(store)
    assert any(p.startswith("s7: changed since registration (was not_run, now sha256:") for p in problems)
    assert any(p.startswith("s4: changed since registration (was sha256:") and "now not_run" in p for p in problems)


def test_verify_reports_an_upstream_artifact_edited_without_a_new_digest(store, tmp_path):
    write_s3_s4(store)
    pr.run(store, cfg(tmp_path), clock=lambda: T0)
    path = store.artifact_path("s3")
    doc = json.loads(path.read_text())
    doc["payload"]["ranking"].pop(0)
    path.write_text(json.dumps(doc))
    problems = pr.verify(store)
    assert len(problems) == 1 and problems[0].startswith("s3: ") and "digest mismatch" in problems[0]


def test_the_stage_is_the_one_vaxforge_run_calls(store, tmp_path):
    outcome, art = cli.run_extra_stage("s11", store, cfg(tmp_path))
    assert outcome == "ran" and art.status == "ok" and art.stage == "s11"


@pytest.fixture
def with_prereg_command(monkeypatch):
    # cli.py (stream A) has to list s11_prereg in EXTENSION_MODULES for the command to exist outside this test
    monkeypatch.setattr(cli, "EXTENSION_MODULES", [*cli.EXTENSION_MODULES, "s11_prereg"])


def test_prereg_verify_through_the_real_cli(with_prereg_command, store, tmp_path, capsys):
    write_s3_s4(store)
    art = pr.run(store, cfg(tmp_path), clock=lambda: T0)
    assert cli.main(["prereg", "verify", str(store.run_dir)]) == 0
    out = capsys.readouterr().out
    assert f"registered {T0}" in out and art.payload["frozen_digest"] in out and "OK - " in out
    edit_manifest(store, drop_the_best_prediction, redo_envelope_digest=True)
    assert cli.main(["prereg", "verify", str(store.run_dir)]) == 1
    out = capsys.readouterr().out
    assert "PROBLEM: the frozen manifest does not match frozen_digest" in out and "1 problem(s)" in out


def test_prereg_verify_on_a_missing_folder_is_an_error_and_creates_nothing(with_prereg_command, tmp_path, capsys):
    assert cli.main(["prereg", "verify", str(tmp_path / "typo")]) == 1
    assert "is not a folder" in capsys.readouterr().err and not (tmp_path / "typo").exists()


def test_prereg_verify_on_a_run_with_no_manifest_fails(with_prereg_command, store, capsys):
    assert cli.main(["prereg", "verify", str(store.run_dir)]) == 1
    assert "never pre-registered" in capsys.readouterr().out
