import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "src")]

import pytest  # noqa: E402
from vaxforge import s9_5_finetune as ft  # noqa: E402
from vaxforge import s9_5_generate  # noqa: E402

HELD = {"colony-pcr", "golden-gate"}


def cand(i, **kw):
    c = {"id": i, "source": "opentrons-apache", "prompt": f"prompt {i}", "code": f"code {i}",
         "heldout_family": "none", "family_doubt": False, "assigned_by": "human", "refused_by_base": False,
         "simulate_pass": True}
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


def test_the_manifest_records_who_assigned_each_label_and_defaults_to_the_rule_labeller():
    absent = cand("b")
    del absent["assigned_by"]
    m = ft.build_manifest([cand("a"), cand("c", assigned_by="agent-review"), absent], HELD, min_rows=1)
    assert {e["id"]: e["assigned_by"] for e in m["included"]} == {"a": "human", "c": "agent-review", "b": "rule"}


def test_the_manifest_carries_the_custom_labware_and_recovery_tags_only_when_they_are_set():
    m = ft.build_manifest([cand("a"), cand("b", custom_labware=True), cand("c", recovered_by="data-files"),
                           cand("d", custom_labware=False)], HELD, min_rows=1)
    by_id = {e["id"]: e for e in m["included"]}
    assert "custom_labware" not in by_id["a"] and "recovered_by" not in by_id["a"]
    assert by_id["b"]["custom_labware"] is True and by_id["c"]["recovered_by"] == "data-files"
    assert "custom_labware" not in by_id["d"]


def test_the_guard_accepts_labels_from_a_person_an_agent_or_the_rule():
    cleared = {"opentrons-apache": "Apache-2.0, repository LICENSE"}
    m = ft.build_manifest([cand("a"), cand("b", assigned_by="agent-review"), cand("c", assigned_by="rule")], HELD, min_rows=1)
    ft.assert_run_allowed(m, "org/model", cleared, [])
    del m["included"][0]["assigned_by"]  # a manifest from before the field existed passes too
    ft.assert_run_allowed(m, "org/model", cleared, [])


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
    assert json.loads(out.read_text())["counts"] == {"candidates": 2, "included": 1, "excluded": 1, "included_synthetic": 0}
    assert [json.loads(l) for l in train.read_text().splitlines()] == [{"prompt": "prompt a", "completion": "code a"}]
    assert "1 of 2 candidates included" in capsys.readouterr().out
    src.write_text("{oops\n")
    assert ft.main([str(src), "--out", str(out)]) == 1


def gen(c, text="def run(protocol): pass", code="def run(protocol): pass", finish="stop", **kw):
    row = {"task_id": ft.candidate_task_id(c["id"]), "split": "candidate", "arm": "base", "prompt_sha256": ft._sha(c["prompt"]),
           "text": text, "code": code, "finish_reason": finish}
    row.update(kw)
    return row


def says_no(text, code, finish_reason):
    return code is None and "cannot" in text


def test_apply_refusals_sets_the_flag_from_the_detector_and_does_not_mutate_the_input():
    ok, no = cand("ok", refused_by_base=None), cand("no", refused_by_base=None)
    out = ft.apply_refusals([ok, no], [gen(ok), gen(no, text="I cannot help with that.", code=None)], says_no)
    assert [c["refused_by_base"] for c in out] == [False, True]
    assert ok["refused_by_base"] is None  # the input is untouched


def test_a_reply_cut_off_by_max_tokens_is_not_a_refusal_even_if_the_detector_would_say_so():
    c = cand("c", refused_by_base=None)
    out = ft.apply_refusals([c], [gen(c, text="I cannot", code=None, finish="length")], lambda *a: True)
    assert out[0]["refused_by_base"] is False


def test_a_candidate_with_no_base_generation_stays_unrun_and_is_excluded():
    c = cand("c", refused_by_base=None)
    other_arm = gen(c, arm="finetune")
    other_split = gen(c, split="heldout")
    out = ft.apply_refusals([c], [other_arm, other_split], says_no)
    assert out[0]["refused_by_base"] is None
    m = ft.build_manifest(out, HELD, min_rows=1)
    assert m["excluded"][0]["reasons"] == ["refusal-filter-not-run"]


def test_a_reply_generated_for_a_different_prompt_is_an_error():
    c = cand("c", refused_by_base=None)
    stale = gen(c, prompt_sha256=ft._sha("an older prompt"))
    with pytest.raises(ValueError, match="different prompt"):
        ft.apply_refusals([c], [stale], says_no)


def test_the_refusal_chain_ends_in_the_probe_list():
    a, b = cand("a", refused_by_base=None), cand("b", refused_by_base=None)
    flagged = ft.apply_refusals([a, b], [gen(a), gen(b, text="I cannot.", code=None)], says_no)
    m = ft.build_manifest(flagged, HELD, min_rows=1)
    assert [e["id"] for e in m["included"]] == ["a"] and m["dropped_for_refusal"] == ["b"]


# ---- vaxforge finetune generate ---------------------------------------------------------------------------------------

ENDPOINT = "http://127.0.0.1:8081/v1"
TASKS = [{"task_id": "colony-pcr", "family": "colony-pcr", "split": "heldout", "prompt": "Write the colony PCR protocol."},
         {"task_id": "golden-gate", "family": "golden-gate", "split": "heldout", "prompt": "Write the Golden Gate protocol."}]


def _tasks_file(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text("".join(json.dumps(t) + "\n" for t in TASKS), encoding="utf-8")
    return path


def test_generate_builds_the_arms_decoding_and_provenance_from_the_command_line(tmp_path, monkeypatch, capsys):
    seen = {}

    def fake_generate(tasks, arms, endpoint, decoding, out_dir, **kw):
        seen.update(tasks=tasks, arms=arms, endpoint=endpoint, decoding=decoding, out=out_dir, **kw)
        return {"recorded": 1, "skipped": 0, "rows": 1}
    monkeypatch.setattr(s9_5_generate, "generate", fake_generate)
    code = ft.generate_main([str(_tasks_file(tmp_path)), "--endpoint", ENDPOINT, "--arm", "base=mlx/Qwen3.5-4B-4bit",
                             "--arm", "finetune=mlx/Qwen3.5-4B-4bit", "--max-tokens", "8192", "--decoding", "top_p=0.8",
                             "--decoding", 'chat_template_kwargs={"enable_thinking": false}', "--workers", "4",
                             "--extra", "adapter_sha256=abc", "--out", str(tmp_path / "out")])
    assert code == 0 and json.loads(capsys.readouterr().out) == {"recorded": 1, "skipped": 0, "rows": 1}
    assert [t["task_id"] for t in seen["tasks"]] == ["colony-pcr", "golden-gate"]
    assert seen["arms"] == {"base": "mlx/Qwen3.5-4B-4bit", "finetune": "mlx/Qwen3.5-4B-4bit"}
    assert seen["decoding"] == {"max_tokens": 8192, "top_p": 0.8, "chat_template_kwargs": {"enable_thinking": False}}
    assert seen["extra"] == {"adapter_sha256": "abc"} and seen["workers"] == 4 and seen["endpoint"] == ENDPOINT


def test_generate_leaves_unnamed_decoding_fields_to_the_client_and_reports_bad_input_without_a_traceback(tmp_path, monkeypatch, capsys):
    seen = {}
    monkeypatch.setattr(s9_5_generate, "generate", lambda tasks, arms, endpoint, decoding, out_dir, **kw: seen.update(decoding=decoding) or {})
    argv = [str(_tasks_file(tmp_path)), "--endpoint", ENDPOINT, "--arm", "base=m", "--max-tokens", "10", "--out", str(tmp_path / "o")]
    assert ft.generate_main(argv) == 0 and seen["decoding"] == {"max_tokens": 10}  # temperature is left to its default of 0
    capsys.readouterr()
    assert ft.generate_main(argv + ["--decoding", "temperature=warm"]) == 1
    assert "must be JSON" in capsys.readouterr().err
    assert ft.generate_main(argv[:3] + ["--arm", "no-equals-sign"] + argv[5:]) == 1
    assert "NAME=VALUE" in capsys.readouterr().err
    assert ft.generate_main([str(tmp_path / "missing.jsonl")] + argv[1:]) == 1
    assert "s9_5_generate:" in capsys.readouterr().err


def test_the_finetune_command_lists_every_tool_and_routes_generate_here(monkeypatch):
    import argparse
    common = argparse.ArgumentParser(add_help=False)
    parent = argparse.ArgumentParser()
    ft.register_cli(parent.add_subparsers(dest="command"), common)
    routed = []
    monkeypatch.setattr(ft, "generate_main", lambda argv: routed.append(argv) or 0)
    args = parent.parse_args(["finetune", "generate", "tasks.jsonl", "--endpoint", ENDPOINT])
    assert args.func(args) == 0 and routed == [["tasks.jsonl", "--endpoint", ENDPOINT]]
    for name in ("manifest", "check", "tasks", "generate", "grade", "winrate"):
        assert parent.parse_args(["finetune", name, "x"]).finetune_command == name


def test_candidate_tasks_use_ids_the_generation_client_accepts_even_for_repo_at_commit_path_ids(tmp_path):
    ids = ["AD-SDL/biomedal_workcell@24b0954:applications/exp1/protocols/plate_prep.py", "b"]
    tasks = ft.candidate_tasks([cand(i) for i in ids])
    path = tmp_path / "tasks.jsonl"
    path.write_text("".join(json.dumps(t) + "\n" for t in tasks), encoding="utf-8")
    assert [t["prompt"] for t in s9_5_generate.load_tasks(path)] == ["prompt " + i for i in ids]  # none is refused as unsafe
    assert {t["split"] for t in tasks} == {"candidate"} and len({t["task_id"] for t in tasks}) == 2
    assert tasks[0]["task_id"] == ft.candidate_task_id(ids[0]) != ids[0]
