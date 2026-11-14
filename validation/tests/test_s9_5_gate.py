"""The path that spends credits: manifest -> leakage check -> run guard -> create_run, wired with the real modules."""
import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "src")]

import pytest  # noqa: E402
import leakage_check as lc  # noqa: E402
from vaxforge import s9_5_adaption as ad  # noqa: E402
from vaxforge import s9_5_finetune as ft  # noqa: E402

HELD = {"colony-pcr"}
MODEL = "Qwen/Qwen3.5-9B"
CLEARED = {"opentrons-apache": "Apache-2.0, repository LICENSE"}
INSTRUCTION = ("Distribute 100 ul of the master mix from well A1 of the reservoir into every well of the "
               "first PCR plate column by column using a fresh tip for each column and then seal the plate")


def cand(i, prompt=None):
    return {"id": i, "source": "opentrons-apache", "prompt": prompt or f"unrelated prompt number {i} " + "word " * 15,
            "code": f"def run(protocol): pass  # {i} " + "filler " * 15, "heldout_family": "none",
            "family_doubt": False, "assigned_by": "human", "refused_by_base": False, "simulate_pass": True}


def fake_transport(sent):
    def send(method, url, headers, data, timeout):
        sent.append((method, url))
        body = json.dumps({"id": "run_1", "status": "pending"}).encode()
        return ad.Response(200, {}, io.BytesIO(body))
    return send


def launch(cands, min_rows, sent, cleared=CLEARED):
    manifest = ft.build_manifest(cands, HELD, min_rows=min_rows)
    violations = lc.check(manifest, cands, {"held.md": INSTRUCTION})["violations"]
    return ad.create_run(
        "ds_9", model=MODEL, max_iterations=3, target_win_rate=0.7, idempotency_key="k1", confirm=True,
        guard=lambda: ft.assert_run_allowed(manifest, MODEL, cleared, violations), transport=fake_transport(sent))


@pytest.fixture(autouse=True)
def _key(monkeypatch):
    monkeypatch.setenv("ADAPTION_API_KEY", "test-key-not-a-secret")


def test_a_clean_cleared_set_of_enough_rows_reaches_the_platform_exactly_once():
    sent = []
    run = launch([cand(f"p{i}") for i in range(3)], 3, sent)
    assert run["status"] == "pending"
    assert sent == [("POST", "https://api.prod.adaptionlabs.ai/api/v1/autoscientist")]


def test_a_held_out_near_duplicate_in_the_training_data_stops_the_run_before_any_request():
    sent = []
    with pytest.raises(ft.RunNotAllowed, match="leakage check found 1 violation"):
        launch([cand(f"p{i}") for i in range(3)] + [cand("leak", prompt=INSTRUCTION)], 4, sent)
    assert sent == []


def test_too_few_rows_stop_the_run_before_any_request():
    sent = []
    with pytest.raises(ft.RunNotAllowed, match="under the 1000-row minimum"):
        launch([cand("p1"), cand("p2")], ft.MIN_SFT_ROWS, sent)
    assert sent == []


def test_a_source_with_no_recorded_clearance_stops_the_run_before_any_request():
    sent = []
    with pytest.raises(ft.RunNotAllowed, match="no recorded licence or permission"):
        launch([cand(f"p{i}") for i in range(3)], 3, sent, cleared={})
    assert sent == []
