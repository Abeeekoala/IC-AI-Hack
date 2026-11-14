import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "src")]

import pytest  # noqa: E402
from vaxforge import s9_5_finetune as ft  # noqa: E402

HELD = {"colony-pcr", "golden-gate"}


def cand(i, **kw):
    c = {"id": i, "source": "opentrons-apache", "prompt": f"prompt {i}", "code": f"code {i}",
         "heldout_family": "none", "family_doubt": False, "refused_by_base": False, "simulate_pass": True}
    c.update(kw)
    return c


def why(**kw):
    c = cand("x", **kw)
    for k in [k for k, v in kw.items() if v == "<absent>"]:
        del c[k]
    return ft.reasons_for(c, HELD)


def test_a_clean_candidate_is_included_with_hashes_and_the_fixed_settings():
    m = ft.build_manifest([cand("a")], HELD, min_rows=1)
    assert [e["id"] for e in m["included"]] == ["a"] and m["excluded"] == []
    assert m["included"][0]["prompt_sha256"] == ft._sha("prompt a")
    assert m["training_method"] == "instruction" and m["augmentation"] == "none"
    assert m["heldout_families"] == ["colony-pcr", "golden-gate"] and m["meets_min_rows"] is True


@pytest.mark.parametrize("kw, reason", [
    (dict(source="protocols.io"), "source-excluded: protocols.io"),
    (dict(source="text2wetlab"), "source-excluded: text2wetlab"),
    (dict(heldout_family="<absent>"), "family-unassigned"),
    (dict(heldout_family="colony-pcr"), "heldout-family: colony-pcr"),
    (dict(heldout_family="colony-pcrr"), "unknown-family-label: colony-pcrr"),
    (dict(family_doubt=True), "family-in-doubt"),
    (dict(refused_by_base=None), "refusal-filter-not-run"),
    (dict(refused_by_base=True), "base-model-refused"),
    (dict(simulate_pass=None), "simulate-not-run"),
    (dict(simulate_pass=False), "fails-opentrons_simulate"),
])
def test_each_rule_excludes_and_names_its_reason(kw, reason):
    assert any(r.startswith(reason) for r in why(**kw))


def test_every_failed_rule_is_listed_not_just_the_first():
    reasons = why(source="protocols.io", heldout_family="golden-gate", simulate_pass=False)
    assert len(reasons) == 3


def test_refused_prompts_become_the_probe_unless_their_source_is_off_limits():
    cs = [cand("r1", refused_by_base=True), cand("r2", refused_by_base=True, source="protocols.io"), cand("ok")]
    m = ft.build_manifest(cs, HELD, min_rows=1)
    assert m["dropped_for_refusal"] == ["r1"]
    assert {e["id"] for e in m["excluded"]} == {"r1", "r2"}


def test_duplicate_ids_are_rejected():
    with pytest.raises(ValueError, match="not unique"):
        ft.build_manifest([cand("a"), cand("a")], HELD)


def test_the_row_minimum_is_reported():
    assert ft.build_manifest([cand("a")], HELD)["meets_min_rows"] is False
    assert ft.build_manifest([cand("a"), cand("b")], HELD, min_rows=2)["meets_min_rows"] is True


def test_training_rows_are_exactly_the_included_pairs_and_detect_edits():
    cs = [cand("a"), cand("b", simulate_pass=False)]
    m = ft.build_manifest(cs, HELD, min_rows=1)
    assert ft.training_rows(cs, m) == [{"prompt": "prompt a", "completion": "code a"}]
    cs[0]["code"] = "edited after the manifest"
    with pytest.raises(ValueError, match="changed since"):
        ft.training_rows(cs, m)


def test_the_guard_names_every_unmet_precondition():
    m = ft.build_manifest([cand("a")], HELD)  # 1 row against the 1,000-row minimum
    with pytest.raises(ft.RunNotAllowed) as e:
        ft.assert_run_allowed(m, None, {}, None)
    msg = str(e.value)
    for part in ("base_model_id is not pinned", "under the 1000-row minimum", "no recorded licence",
                 "leakage check has not been run"):
        assert part in msg


def test_the_guard_passes_only_when_everything_is_recorded():
    m = ft.build_manifest([cand("a")], HELD, min_rows=1)
    cleared = {"opentrons-apache": "Apache-2.0, repository LICENSE"}
    ft.assert_run_allowed(m, "org/model", cleared, [])
    with pytest.raises(ft.RunNotAllowed, match="1 violation"):
        ft.assert_run_allowed(m, "org/model", cleared, ["leak"])
    with pytest.raises(ft.RunNotAllowed, match="no recorded licence"):
        ft.assert_run_allowed(m, "org/model", {"opentrons-apache": "  "}, [])


def test_the_guard_refuses_an_excluded_source_even_if_someone_records_a_clearance():
    m = ft.build_manifest([cand("a")], HELD, min_rows=1)
    m["included"][0]["source"] = "protocols.io"  # a tampered manifest
    with pytest.raises(ft.RunNotAllowed, match="is excluded"):
        ft.assert_run_allowed(m, "org/model", {"protocols.io": "we asked nicely"}, [])


def test_cli_writes_the_manifest_and_the_training_file(tmp_path, capsys):
    src = tmp_path / "c.jsonl"
    src.write_text("\n".join(json.dumps(c) for c in [cand("a"), cand("b", heldout_family="golden-gate")]) + "\n")
    out, train = tmp_path / "m.json", tmp_path / "t.jsonl"
    assert ft.main([str(src), "--out", str(out), "--training-out", str(train), "--min-rows", "1"]) == 0
    assert json.loads(out.read_text())["counts"] == {"candidates": 2, "included": 1, "excluded": 1}
    assert [json.loads(l) for l in train.read_text().splitlines()] == [{"prompt": "prompt a", "completion": "code a"}]
    assert "1 of 2 candidates included" in capsys.readouterr().out
    src.write_text("{oops\n")
    assert ft.main([str(src), "--out", str(out)]) == 1
