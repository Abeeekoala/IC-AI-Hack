"""S9 attempt records (README S9.5): one JSON line per (task, model) attempt, written for stream D's win-rate harness.

The refusal, no-code, lint, argument and file tests need no simulator. The `needs_ot` tests run real `opentrons_simulate`
7.5.0 runs and are skipped when no interpreter is available. A cannot-be-launched interpreter name stands in for "no
simulator" and doubles as proof that a test never reached the simulator. Most tests are NEGATIVE controls, because a
recorder that only ever says "pass" proves nothing.
"""

import hashlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from vaxforge import s9_validate
from vaxforge.s9_attempt import BadReferenceError, NoSimulatorError, append_jsonl, main, make_record
from vaxforge.s9_llm import extract_python, refusal_verdict
from vaxforge.s9_validate import RULES, find_ot_python

needs_ot = pytest.mark.skipif(find_ot_python() is None, reason="no Opentrons 7.5.0 interpreter (set VAXFORGE_OT_PYTHON)")

SRC = Path(__file__).resolve().parents[2] / "src"
BOGUS_PYTHON = "definitely-not-a-python"
IDS = {"task_id": "t01", "family": "plate_distribution", "model": "base", "split": "heldout"}
MINIMAL_KEYS = {"task_id", "family", "model", "split", "outcome", "simulate_pass", "rule_violations", "gt_diff"}
DETAIL_KEYS = {"rule_counts", "error_kind", "n_events", "code_sha256", "gt_diff_parts", "gt_diff_mode", "grader", "opentrons_version",
               "finish_reason"}
GT_PARTS = ("labware_mismatches", "wells_with_different_volume", "step_order_edit_distance")
REFUSAL = "I can't help with that."
HEDGE = "I can't help with the biology behind this, but here is the liquid handling:"

HEAD = '''
from opentrons import protocol_api
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}
def run(ctx: protocol_api.ProtocolContext):
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")
    plate = ctx.load_labware("nest_96_wellplate_200ul_flat", "3")
    p = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])
    water = ctx.define_liquid(name="water", description="d", display_color="#33AAFF")
    tubes["A1"].load_liquid(liquid=water, volume=500)
'''


def body(*lines):
    return HEAD + "".join(f"    {line}\n" for line in lines)


GOOD = body('p.transfer(50, tubes["A1"], plate["A1"])')
TIP_LEFT_ON = body("p.pick_up_tip()", 'p.aspirate(50, tubes["A1"])', 'p.dispense(50, plate["A1"])')
CRASHES = body('plate["Z99"]')  # a well that does not exist: KeyError inside run()
LINT_FAILS = "import os\n" + GOOD  # the static lint will not execute it, so no simulator is involved
# Three violations of two rules: two aspirations from wells that were never loaded, and a tip left on at the end.
TWO_RULES =body('p.transfer(50, tubes["B1"], plate["A1"])', 'p.transfer(50, tubes["B2"], plate["A2"])', "p.pick_up_tip()")
# The same two moves in the other order: identical deck and final volumes, different step order.
TWO_MOVES = body('p.transfer(50, tubes["A1"], plate["A1"])', 'p.transfer(50, tubes["A1"], plate["A2"])')
TWO_MOVES_SWAPPED = body('p.transfer(50, tubes["A1"], plate["A2"])', 'p.transfer(50, tubes["A1"], plate["A1"])')


def fenced(code, intro="Here is the protocol."):
    return f"{intro}\n```python\n{code}```\n"


def record_of(**kw):
    return make_record(**{**IDS, **kw})


def write(tmp_path, name, text):
    path = tmp_path / name
    path.write_bytes(text.encode("utf-8"))  # bytes: no newline translation on Windows
    return str(path)


def lines_of(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()]


def argv_for(tmp_path, **over):
    """`record` arguments. A keyword overrides the flag of that name (task_id -> --task-id); None drops it; True is a switch."""
    opts = {"task-id": "t01", "family": "plate_distribution", "model": "base", "split": "heldout", "out": tmp_path / "attempts.jsonl"}
    opts.update({key.replace("_", "-"): value for key, value in over.items()})
    argv = ["record"]
    for flag, value in opts.items():
        if value is True:
            argv.append(f"--{flag}")
        elif value is not None:
            argv += [f"--{flag}", str(value)]
    return argv


def sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def without_finish_reason(rec):
    """`rec` with detail.finish_reason reset to null, to compare records that differ only in the label they were given."""
    return {**rec, "detail": {**rec["detail"], "finish_reason": None}}


# --------------------------------------------------------------------------- refusals, no code, lint (no simulator)


@pytest.mark.parametrize(
    "reply,kind",
    [
        ("AgentSafetyRefusalError: the provider ended the turn", "harness_error"),
        ("The request was refused by the provider (content policy violation).", "harness_error"),
        (REFUSAL, "text_pattern"),
        ("I must decline to write a protocol for transforming bacteria.", "text_pattern"),
        ("That is against my guidelines, so I cannot assist with writing it.", "text_pattern"),
        ("I'm not going to do this.", "text_pattern"),  # wording stream D saw missed before the detector was widened
        ("As an AI I am unable to give lab protocols", "text_pattern"),
    ],
    ids=["stop-reason-marker", "provider-refusal-wording", "cant-help", "must-decline", "against-guidelines", "not-going-to",
         "unable-to-give"],
)
def test_a_refusal_is_a_distinct_outcome_with_every_result_null(reply, kind):
    assert refusal_verdict(reply).refused and refusal_verdict(reply).kind == kind
    rec = record_of(reply=reply, ot_python=BOGUS_PYTHON)  # a refusal is recorded without a simulator
    assert rec["outcome"] == "refused"
    assert rec["simulate_pass"] is None and rec["rule_violations"] is None and rec["gt_diff"] is None
    detail = rec["detail"]
    assert detail["error_kind"] is None and detail["code_sha256"] is None and detail["n_events"] is None
    assert detail["gt_diff_parts"] is None and detail["opentrons_version"] is None


def test_a_refusal_needs_neither_a_simulator_nor_a_working_reference():
    rec = record_of(reply=REFUSAL, reference_code="def run(:\n", ot_python=BOGUS_PYTHON)
    assert rec["outcome"] == "refused" and rec["gt_diff"] is None


def test_a_provider_stop_reason_must_reach_the_detector_as_text():
    # With no --finish-reason the detector only sees the reply text. An empty body whose provider stop_reason was
    # "refusal" then reads as a code-free attempt, so a text-only harness has to write a marker the detector knows.
    # (Passing the finish reason closes the gap: see the finish_reason tests below.)
    dump = json.dumps({"stop_reason": "refusal", "content": []})
    assert record_of(reply=dump)["outcome"] == "attempted"
    assert record_of(reply="AgentSafetyRefusalError " + dump)["outcome"] == "refused"


def test_a_reply_that_hedges_but_contains_code_is_attempted_not_refused():
    reply = fenced(LINT_FAILS, HEDGE)
    assert refusal_verdict(reply).refused is False  # the detector's rule: a delivered protocol is not a refusal
    rec = record_of(reply=reply, ot_python=BOGUS_PYTHON)
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is False and rec["rule_violations"] is None
    assert rec["detail"]["error_kind"] == "LintError"
    assert rec["detail"]["code_sha256"] == sha(extract_python(reply))  # the extracted block, not the whole reply


@pytest.mark.parametrize(
    "reply",
    [
        "",
        "   \n",
        "First I would pick up a tip, then move 50 uL into well A1.",
        "Could you tell me which plate you use?",
        "You can't aspirate more than the tip holds, so I split the transfer into two steps:",  # a limit, explained; no refusal
        "I can't do it in one step, so I split the transfer into two steps:",
    ],
)
def test_a_reply_with_no_code_and_no_refusal_is_a_failed_attempt(reply):
    rec = record_of(reply=reply, ot_python=BOGUS_PYTHON)
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is False
    assert rec["rule_violations"] is None and rec["gt_diff"] is None
    assert rec["detail"]["error_kind"] == "no_code" and rec["detail"]["code_sha256"] is None


def test_empty_code_counts_as_no_code():
    rec = record_of(code="  \n", ot_python=BOGUS_PYTHON)
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is False and rec["detail"]["error_kind"] == "no_code"


def test_code_the_lint_refuses_is_a_failed_simulation_not_a_harness_error():
    rec = record_of(code=LINT_FAILS, reference_code=None, ot_python=BOGUS_PYTHON)
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is False
    assert rec["rule_violations"] is None and rec["gt_diff"] is None
    assert rec["detail"]["error_kind"] == "LintError" and rec["detail"]["code_sha256"] == sha(LINT_FAILS)


def test_code_input_is_never_refusal_checked():
    rec = record_of(code=REFUSAL + "\n", ot_python=BOGUS_PYTHON)  # refusal wording, but it was handed over as code
    assert rec["outcome"] == "attempted" and rec["detail"]["error_kind"] == "LintError"  # a syntax error


# --------------------------------------------------------------------------- finish_reason: a refusal that has no text


@pytest.mark.parametrize("reason", ["refusal", "content_filter", "REFUSAL", " Content_Filter "])
def test_a_refusal_finish_reason_records_a_refusal_even_when_the_reply_is_empty(reason):
    rec = record_of(reply="", finish_reason=reason, ot_python=BOGUS_PYTHON)  # a refusal needs no simulator
    assert rec["outcome"] == "refused"
    assert rec["simulate_pass"] is None and rec["rule_violations"] is None and rec["gt_diff"] is None
    detail = rec["detail"]
    assert detail["error_kind"] is None and detail["code_sha256"] is None and detail["n_events"] is None
    assert detail["gt_diff_parts"] is None and detail["opentrons_version"] is None
    assert record_of(reply="", ot_python=BOGUS_PYTHON)["outcome"] == "attempted"  # control: the same reply without it


def test_a_refusal_finish_reason_beats_code_in_the_reply_and_in_the_code_argument():
    reply = fenced(LINT_FAILS)  # without the finish reason this is graded as an attempt (a LintError)
    assert record_of(reply=reply, ot_python=BOGUS_PYTHON)["detail"]["error_kind"] == "LintError"
    for given in ({"reply": reply}, {"code": GOOD}):
        rec = record_of(finish_reason="refusal", ot_python=BOGUS_PYTHON, **given)  # no simulator: nothing is graded
        assert rec["outcome"] == "refused" and rec["simulate_pass"] is None and rec["detail"]["code_sha256"] is None


@pytest.mark.parametrize("reason", ["end_turn", "stop", "length", "max_tokens", "tool_use", ""])
def test_a_finish_reason_that_is_not_a_refusal_is_ignored(reason):
    for given in ({"reply": ""}, {"reply": "a plan, no code"}, {"reply": REFUSAL}, {"reply": fenced(LINT_FAILS)},
                  {"code": "  \n"}, {"code": LINT_FAILS}):
        plain = record_of(ot_python=BOGUS_PYTHON, **given)
        with_reason = record_of(finish_reason=reason, ot_python=BOGUS_PYTHON, **given)
        assert with_reason["detail"]["finish_reason"] == reason and plain["detail"]["finish_reason"] is None  # the one new fact
        assert without_finish_reason(with_reason) == plain, given  # the rest of the record, unchanged


@pytest.mark.parametrize("bad", [42, ["refusal"], b"refusal"])
def test_a_finish_reason_that_is_not_text_is_a_bad_argument(bad):
    with pytest.raises(ValueError, match="finish_reason"):
        record_of(reply=REFUSAL, finish_reason=bad)


def test_record_cli_finish_reason_records_a_refusal_with_an_empty_body(tmp_path, capsys):
    empty = write(tmp_path, "reply.txt", "")  # the provider ended the turn with stop_reason "refusal" and sent no text
    assert main(argv_for(tmp_path, reply=empty, ot_python=BOGUS_PYTHON)) == 0  # without the option this reads as a failed attempt
    attempted = json.loads(capsys.readouterr().out)
    assert attempted["outcome"] == "attempted" and attempted["detail"]["error_kind"] == "no_code"
    assert main(argv_for(tmp_path, reply=empty, finish_reason="refusal", ot_python=BOGUS_PYTHON)) == 0
    refused = json.loads(capsys.readouterr().out)
    assert refused["outcome"] == "refused"
    assert refused["simulate_pass"] is None and refused["rule_violations"] is None and refused["gt_diff"] is None
    assert refused["detail"]["error_kind"] is None and refused["detail"]["code_sha256"] is None
    assert lines_of(tmp_path / "attempts.jsonl") == [attempted, refused]


def test_record_cli_ignores_a_finish_reason_that_is_not_a_refusal(tmp_path, capsys):
    empty = write(tmp_path, "reply.txt", "")
    assert main(argv_for(tmp_path, reply=empty, finish_reason="end_turn", ot_python=BOGUS_PYTHON)) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["outcome"] == "attempted" and rec["detail"]["error_kind"] == "no_code"
    wording = write(tmp_path, "wording.txt", REFUSAL)
    assert main(argv_for(tmp_path, reply=wording, finish_reason="stop", ot_python=BOGUS_PYTHON)) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "refused"  # a finish reason never un-refuses a refusal


# --------------------------------------------------------------------------- detail.finish_reason

NO_SIMULATOR_NEEDED = ({"reply": ""}, {"reply": REFUSAL}, {"reply": "a plan, no code"}, {"reply": fenced(LINT_FAILS)}, {"code": LINT_FAILS})
CUT_OFF = [
    "",
    "   \n",
    "To move the liquid I first load the tip rack in slot 1, then the 96-well pl",
    "Here is the protocol:\n```python\nfrom opentrons import protocol_api\n\nrequirements = {",  # cut inside the fence, before `def run`
]


@pytest.mark.parametrize("reason", ["refusal", "content_filter", "stop", "end_turn", "length", "max_tokens", " Content_Filter ", ""])
def test_detail_finish_reason_stores_the_value_as_given(reason):
    for given in NO_SIMULATOR_NEEDED:
        rec = record_of(finish_reason=reason, ot_python=BOGUS_PYTHON, **given)
        assert rec["detail"]["finish_reason"] == reason, given  # verbatim: no case folding, no stripping
        assert set(rec["detail"]) == DETAIL_KEYS  # in detail, with the other keys; the eight required keys are untouched
        assert set(rec) == MINIMAL_KEYS | {"detail"}


def test_detail_finish_reason_is_null_when_none_was_given():
    for given in NO_SIMULATOR_NEEDED:
        for rec in (record_of(ot_python=BOGUS_PYTHON, **given), record_of(finish_reason=None, ot_python=BOGUS_PYTHON, **given)):
            assert "finish_reason" in rec["detail"] and rec["detail"]["finish_reason"] is None, given


@pytest.mark.parametrize("reason", ["length", "max_tokens", "LENGTH"])
@pytest.mark.parametrize("reply", CUT_OFF, ids=["empty", "blank", "cut-prose", "cut-code-fence"])
def test_a_reply_cut_off_by_the_token_limit_is_a_failed_attempt_not_a_refusal(reply, reason):
    rec = record_of(reply=reply, finish_reason=reason, ot_python=BOGUS_PYTHON)  # no simulator: there is no code to run
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is False
    assert rec["rule_violations"] is None and rec["gt_diff"] is None
    detail = rec["detail"]
    assert detail["error_kind"] == "no_code" and detail["code_sha256"] is None and detail["finish_reason"] == reason
    for label in ("refusal", "content_filter"):  # control: the provider's refusal label on the very same reply is a refusal
        refused = record_of(reply=reply, finish_reason=label, ot_python=BOGUS_PYTHON)
        assert refused["outcome"] == "refused" and refused["simulate_pass"] is None and refused["detail"]["error_kind"] is None
        assert refused["detail"]["finish_reason"] == label


def test_record_cli_stores_the_finish_reason_in_detail(tmp_path, capsys):
    empty = write(tmp_path, "reply.txt", "")
    got = []
    for extra in ({"finish_reason": "max_tokens"}, {}, {"finish_reason": "refusal"}):
        assert main(argv_for(tmp_path, reply=empty, ot_python=BOGUS_PYTHON, **extra)) == 0
        got.append(json.loads(capsys.readouterr().out))
    cut, none_given, refused = got
    assert cut["outcome"] == "attempted" and cut["simulate_pass"] is False and cut["detail"]["error_kind"] == "no_code"
    assert none_given["outcome"] == "attempted" and none_given["detail"]["error_kind"] == "no_code"
    assert refused["outcome"] == "refused" and refused["simulate_pass"] is None
    assert [rec["detail"]["finish_reason"] for rec in got] == ["max_tokens", None, "refusal"]
    assert lines_of(tmp_path / "attempts.jsonl") == got  # the line that was appended is the line that was printed


def test_minimal_drops_the_finish_reason_with_the_rest_of_detail(tmp_path, capsys):
    empty = write(tmp_path, "reply.txt", "")
    assert main(argv_for(tmp_path, reply=empty, finish_reason="max_tokens", minimal=True, ot_python=BOGUS_PYTHON)) == 0
    printed = json.loads(capsys.readouterr().out)
    assert set(printed) == MINIMAL_KEYS and printed["outcome"] == "attempted"
    minimal = record_of(reply="", finish_reason="max_tokens", minimal=True, ot_python=BOGUS_PYTHON)
    full = record_of(reply="", finish_reason="max_tokens", ot_python=BOGUS_PYTHON)
    assert set(minimal) == MINIMAL_KEYS and "detail" not in minimal
    assert minimal == {key: full[key] for key in MINIMAL_KEYS}  # the eight required keys are the same with or without detail


def test_record_help_says_where_the_finish_reason_is_kept(capsys):
    assert main(["record", "--help"]) == 0
    assert "detail.finish_reason" in capsys.readouterr().out


# --------------------------------------------------------------------------- real simulations


@needs_ot
def test_working_code_passes_all_three_checks():
    rec = record_of(code=GOOD)
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is True
    assert rec["rule_violations"] == 0 and rec["gt_diff"] is None  # no reference was given
    detail = rec["detail"]
    assert detail["rule_counts"] == dict.fromkeys(RULES, 0) and detail["error_kind"] is None
    assert detail["gt_diff_parts"] is None and detail["n_events"] > 0
    assert detail["opentrons_version"] == "7.5.0" and detail["grader"] == "vaxforge.s9_validate"
    assert detail["code_sha256"] == sha(GOOD)


@needs_ot
def test_a_hedging_reply_with_working_code_is_graded_as_an_attempt():
    reply = fenced(GOOD, HEDGE)
    rec = record_of(reply=reply)
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is True and rec["rule_violations"] == 0
    assert rec["detail"]["code_sha256"] == sha(extract_python(reply))


@needs_ot
def test_leaving_a_tip_on_violates_exactly_one_rule():
    rec = record_of(code=TIP_LEFT_ON)
    assert rec["simulate_pass"] is True and rec["rule_violations"] == 1  # the code ran; it broke one physical rule
    assert rec["detail"]["rule_counts"] == {**dict.fromkeys(RULES, 0), "tip_dropped_at_end": 1}


@needs_ot
def test_rule_violations_counts_rules_not_violations():
    rec = record_of(code=TWO_RULES)
    counts = rec["detail"]["rule_counts"]
    assert counts["no_aspirate_from_empty_well"] == 2 and counts["tip_dropped_at_end"] == 1  # three violations ...
    assert rec["rule_violations"] == 2  # ... of two rules


@needs_ot
def test_code_that_crashes_in_the_simulator_is_a_failed_attempt_not_an_error():
    rec = record_of(code=CRASHES, reference_code=GOOD)
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is False
    assert rec["rule_violations"] is None and rec["gt_diff"] is None  # nothing to count or compare
    detail = rec["detail"]
    assert detail["error_kind"] == "KeyError" and detail["n_events"] is None and detail["gt_diff_parts"] is None
    assert detail["rule_counts"] == dict.fromkeys(RULES, 0) and detail["code_sha256"] == sha(CRASHES)


@needs_ot
def test_gt_diff_is_zero_against_an_identical_reference():
    rec = record_of(code=GOOD, reference_code=GOOD)
    assert rec["gt_diff"] == 0 and rec["detail"]["gt_diff_parts"] == dict.fromkeys(GT_PARTS, 0)


@needs_ot
def test_gt_diff_adds_labware_wells_and_step_order_edits():
    # The reference moves 60 uL instead of 50 (2 wells end with another volume, 2 steps differ) and loads one more labware.
    reference = body('ctx.load_labware("nest_12_reservoir_15ml", "5")', 'p.transfer(60, tubes["A1"], plate["A1"])')
    rec = record_of(code=GOOD, reference_code=reference)
    parts = rec["detail"]["gt_diff_parts"]
    assert parts == {"labware_mismatches": 1, "wells_with_different_volume": 2, "step_order_edit_distance": 2}
    assert rec["gt_diff"] == sum(parts.values()) == 5
    assert isinstance(rec["gt_diff"], int) and rec["simulate_pass"] is True and rec["rule_violations"] == 0


@needs_ot
def test_gt_diff_modes_on_runs_that_differ_only_in_step_order():
    full = record_of(code=TWO_MOVES, reference_code=TWO_MOVES_SWAPPED)  # "full" is the default
    end_state = record_of(code=TWO_MOVES, reference_code=TWO_MOVES_SWAPPED, gt_diff_mode="end_state")
    for rec in (full, end_state):
        parts = rec["detail"]["gt_diff_parts"]
        assert parts["labware_mismatches"] == 0 and parts["wells_with_different_volume"] == 0  # the same end state ...
        assert parts["step_order_edit_distance"] > 0  # ... reached in another order
        assert rec["simulate_pass"] is True and rec["rule_violations"] == 0
    assert full["detail"]["gt_diff_parts"] == end_state["detail"]["gt_diff_parts"]  # both modes report all three parts
    assert full["gt_diff"] == full["detail"]["gt_diff_parts"]["step_order_edit_distance"] > 0
    assert end_state["gt_diff"] == 0
    assert (full["detail"]["gt_diff_mode"], end_state["detail"]["gt_diff_mode"]) == ("full", "end_state")
    assert record_of(code=TWO_MOVES, reference_code=TWO_MOVES)["gt_diff"] == 0  # control: the same order is 0 in full mode too


@needs_ot
def test_gt_diff_modes_on_a_pair_with_a_different_volume():
    reference = body('p.transfer(60, tubes["A1"], plate["A1"])')  # 2 wells end with another volume, 2 steps differ
    full = record_of(code=GOOD, reference_code=reference)
    end_state = record_of(code=GOOD, reference_code=reference, gt_diff_mode="end_state")
    parts = {"labware_mismatches": 0, "wells_with_different_volume": 2, "step_order_edit_distance": 2}
    assert full["detail"]["gt_diff_parts"] == end_state["detail"]["gt_diff_parts"] == parts
    assert full["gt_diff"] == 4 and end_state["gt_diff"] == 2  # a different volume is an end-state difference: both > 0
    # the pair from the test above with one more labware: end_state leaves out exactly the step-order term (5 -> 3)
    more = body('ctx.load_labware("nest_12_reservoir_15ml", "5")', 'p.transfer(60, tubes["A1"], plate["A1"])')
    rec = record_of(code=GOOD, reference_code=more, gt_diff_mode="end_state")
    assert rec["detail"]["gt_diff_parts"] == {"labware_mismatches": 1, "wells_with_different_volume": 2, "step_order_edit_distance": 2}
    assert rec["gt_diff"] == 3


@needs_ot
def test_record_cli_gt_diff_mode_end_to_end(tmp_path, capsys):
    attempt, reference = write(tmp_path, "a.py", TWO_MOVES), write(tmp_path, "b.py", TWO_MOVES_SWAPPED)
    got = {}
    for mode in ("full", "end_state"):
        assert main(argv_for(tmp_path, code=attempt, reference=reference, gt_diff_mode=mode)) == 0
        got[mode] = json.loads(capsys.readouterr().out)
    assert got["full"]["gt_diff"] > 0 and got["end_state"]["gt_diff"] == 0
    assert [rec["detail"]["gt_diff_mode"] for rec in lines_of(tmp_path / "attempts.jsonl")] == ["full", "end_state"]
    assert main(argv_for(tmp_path, code=attempt, reference=reference)) == 0  # no option: the same as "full"
    assert json.loads(capsys.readouterr().out) == got["full"]


# --------------------------------------------------------------------------- harness errors write nothing


@needs_ot
def test_a_reference_that_crashes_is_exit_4_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "attempts.jsonl"
    argv = argv_for(tmp_path, code=write(tmp_path, "attempt.py", GOOD), reference=write(tmp_path, "ref.py", CRASHES))
    assert main(argv) == 4
    assert not out.exists()
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1 and "reference" in err and "KeyError" in err
    with pytest.raises(BadReferenceError, match="reference"):
        record_of(code=GOOD, reference_code=CRASHES)


@pytest.mark.parametrize("reference", [LINT_FAILS, "def run(:\n"], ids=["lint", "syntax-error"])
def test_a_reference_the_lint_rejects_is_a_bad_reference(reference, tmp_path, capsys):
    with pytest.raises(BadReferenceError):
        record_of(code=GOOD, reference_code=reference)
    out = tmp_path / "attempts.jsonl"
    argv = argv_for(tmp_path, code=write(tmp_path, "attempt.py", GOOD), reference=write(tmp_path, "ref.py", reference))
    assert main(argv) == 4 and not out.exists()
    assert len(capsys.readouterr().err.strip().splitlines()) == 1


def test_no_simulator_is_exit_3_and_writes_nothing(tmp_path, capsys):
    out = tmp_path / "attempts.jsonl"
    code = write(tmp_path, "attempt.py", GOOD)
    assert main(argv_for(tmp_path, code=code, ot_python=BOGUS_PYTHON)) == 3
    assert not out.exists()
    err = capsys.readouterr().err
    assert len(err.strip().splitlines()) == 1 and "simulator" in err
    with pytest.raises(NoSimulatorError):
        record_of(code=GOOD, ot_python=BOGUS_PYTHON)


def test_a_missing_simulator_is_not_mistaken_for_a_bad_reference(tmp_path):
    good = write(tmp_path, "good.py", GOOD)
    assert main(argv_for(tmp_path, code=good, reference=good, ot_python=BOGUS_PYTHON)) == 3
    assert not (tmp_path / "attempts.jsonl").exists()
    with pytest.raises(NoSimulatorError):
        record_of(code=GOOD, reference_code=GOOD, ot_python=BOGUS_PYTHON)


def test_no_interpreter_found_anywhere_is_exit_3(tmp_path, monkeypatch):
    monkeypatch.setattr(s9_validate, "find_ot_python", lambda: None)
    assert main(argv_for(tmp_path, code=write(tmp_path, "attempt.py", GOOD))) == 3
    assert not (tmp_path / "attempts.jsonl").exists()
    with pytest.raises(NoSimulatorError):
        record_of(code=GOOD)


@pytest.mark.skipif(importlib.util.find_spec("opentrons") is not None, reason="the main interpreter has opentrons")
def test_an_interpreter_without_opentrons_is_exit_3_not_a_failed_attempt(tmp_path, capsys):
    # The runner starts under this interpreter, dies on `import opentrons`, and writes no result: every attempt would
    # read as a failed simulation if that were recorded as one.
    assert main(argv_for(tmp_path, code=write(tmp_path, "attempt.py", GOOD), ot_python=sys.executable)) == 3
    assert not (tmp_path / "attempts.jsonl").exists()
    assert "opentrons" in capsys.readouterr().err


# --------------------------------------------------------------------------- record shape


def test_minimal_has_exactly_the_eight_result_keys():
    minimal = record_of(reply=REFUSAL, minimal=True)
    assert set(minimal) == MINIMAL_KEYS
    full = record_of(reply=REFUSAL)
    assert set(full) == MINIMAL_KEYS | {"detail"} and set(full["detail"]) == DETAIL_KEYS
    assert {key: full[key] for key in MINIMAL_KEYS} == minimal
    assert minimal["task_id"] == "t01" and minimal["family"] == "plate_distribution"
    assert minimal["model"] == "base" and minimal["split"] == "heldout"
    assert full["detail"]["rule_counts"] == dict.fromkeys(RULES, 0) and full["detail"]["grader"] == "vaxforge.s9_validate"


@pytest.mark.parametrize(
    "over",
    [
        {"model": "gpt"}, {"model": "Base"}, {"model": None}, {"split": "train"}, {"split": ""},
        {"task_id": ""}, {"task_id": "   "}, {"task_id": None}, {"task_id": 7}, {"family": ""}, {"family": "\t"},
        {"reply": None},  # neither a reply nor code
        {"code": GOOD},  # both
        {"reply": 42}, {"reference_code": 7},
    ],
)
def test_make_record_rejects_bad_arguments(over):
    with pytest.raises(ValueError):
        make_record(**{**IDS, "reply": REFUSAL, **over})


def test_gt_diff_mode_defaults_to_full_and_is_recorded_in_detail_for_every_outcome():
    for given in ({"reply": REFUSAL}, {"reply": "no code"}, {"code": LINT_FAILS}, {"reply": "", "finish_reason": "refusal"}):
        assert record_of(ot_python=BOGUS_PYTHON, **given)["detail"]["gt_diff_mode"] == "full"
        assert record_of(ot_python=BOGUS_PYTHON, gt_diff_mode="end_state", **given)["detail"]["gt_diff_mode"] == "end_state"


def test_minimal_still_emits_exactly_the_eight_keys_with_the_new_options(tmp_path, capsys):
    empty = write(tmp_path, "reply.txt", "")
    assert main(argv_for(tmp_path, reply=empty, finish_reason="refusal", gt_diff_mode="end_state", minimal=True)) == 0
    printed = json.loads(capsys.readouterr().out)
    assert set(printed) == MINIMAL_KEYS and printed["outcome"] == "refused"
    assert lines_of(tmp_path / "attempts.jsonl") == [printed]
    minimal = record_of(reply="", finish_reason="refusal", gt_diff_mode="end_state", minimal=True)
    assert set(minimal) == MINIMAL_KEYS  # the mode and the parts live in detail, which --minimal drops
    full = record_of(reply="", finish_reason="refusal", gt_diff_mode="end_state")
    assert {key: full[key] for key in MINIMAL_KEYS} == minimal and full["detail"]["gt_diff_mode"] == "end_state"


def test_an_invalid_gt_diff_mode_is_exit_2_and_writes_nothing(tmp_path, capsys):
    reply = write(tmp_path, "reply.txt", REFUSAL)
    for mode in ("partial", "Full", "end-state", ""):
        assert main(argv_for(tmp_path, reply=reply, gt_diff_mode=mode)) == 2, mode
        capsys.readouterr()
    assert not (tmp_path / "attempts.jsonl").exists()
    for mode in ("partial", "Full", "", None, 3):
        with pytest.raises(ValueError, match="gt_diff_mode"):
            record_of(reply=REFUSAL, gt_diff_mode=mode)  # checked before anything is graded, even for a refusal


# --------------------------------------------------------------------------- the jsonl file


def test_append_jsonl_writes_one_ascii_line_per_call_and_creates_parent_directories(tmp_path):
    path = tmp_path / "deep" / "er" / "attempts.jsonl"
    first = record_of(reply=REFUSAL, task_id="té ")  # non-ASCII, and a Unicode line separator
    second = record_of(reply="no code here", model="finetune", split="probe")
    append_jsonl(path, first)
    append_jsonl(str(path), second)  # a str path works too, and appends
    raw = path.read_bytes()
    assert raw.isascii() and raw.endswith(b"\n") and b"\r" not in raw
    assert len(raw.decode("ascii").splitlines()) == 2
    assert lines_of(path) == [first, second]


def test_append_jsonl_does_not_glue_a_record_onto_an_unterminated_line(tmp_path):
    path = tmp_path / "attempts.jsonl"
    path.write_bytes(b'{"half": ')  # an earlier writer died mid-line
    rec = record_of(reply=REFUSAL)
    append_jsonl(path, rec)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 and json.loads(lines[1]) == rec


# --------------------------------------------------------------------------- command line


def test_record_cli_appends_and_prints_one_line_per_call(tmp_path, capsys):
    out = tmp_path / "nested" / "attempts.jsonl"
    reply = write(tmp_path, "reply.txt", REFUSAL + "\n")
    for n in (1, 2, 3):
        assert main(argv_for(tmp_path, reply=reply, out=out)) == 0
        printed = capsys.readouterr().out
        assert printed.count("\n") == 1
        lines = out.read_text(encoding="utf-8").splitlines()
        assert len(lines) == n and lines[-1] == printed.strip()  # the printed line is the appended line
        assert json.loads(printed)["outcome"] == "refused"


def test_record_cli_minimal_switch(tmp_path, capsys):
    reply = write(tmp_path, "reply.txt", REFUSAL)
    assert main(argv_for(tmp_path, reply=reply, minimal=True)) == 0
    assert set(json.loads(capsys.readouterr().out)) == MINIMAL_KEYS
    assert set(lines_of(tmp_path / "attempts.jsonl")[0]) == MINIMAL_KEYS


def test_reply_dash_reads_standard_input(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(("I must decline — " + REFUSAL + "\n").encode("utf-8")), encoding="utf-8"))
    assert main(argv_for(tmp_path, reply="-", task_id="from-stdin")) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["task_id"] == "from-stdin" and rec["outcome"] == "refused"
    assert lines_of(tmp_path / "attempts.jsonl") == [rec]


def test_reply_dash_also_reads_a_text_stream_without_a_buffer(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("First I would pick up a tip."))
    assert main(argv_for(tmp_path, reply="-")) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["outcome"] == "attempted" and rec["detail"]["error_kind"] == "no_code"


def test_a_utf8_byte_order_mark_does_not_hide_a_refusal(tmp_path, capsys):
    reply = tmp_path / "reply.txt"
    reply.write_bytes(b"\xef\xbb\xbf" + REFUSAL.encode("utf-8"))
    assert main(argv_for(tmp_path, reply=reply)) == 0
    assert json.loads(capsys.readouterr().out)["outcome"] == "refused"


def test_bad_arguments_exit_2_and_write_nothing(tmp_path, capsys):
    reply = write(tmp_path, "reply.txt", REFUSAL)
    code = write(tmp_path, "attempt.py", GOOD)
    not_utf8 = tmp_path / "not_utf8.py"
    not_utf8.write_bytes(b"\xff\xfe\x00\x00")
    bad = [
        {"reply": reply, "model": "gpt"},
        {"reply": reply, "split": "train"},
        {"reply": reply, "task_id": ""},
        {"reply": reply, "family": "   "},
        {},  # neither --code nor --reply
        {"reply": reply, "code": code},  # both
        {"reply": reply, "out": None},
        {"code": tmp_path / "missing.py"},
        {"reply": tmp_path / "missing.txt"},
        {"reply": reply, "reference": tmp_path / "missing.py"},
        {"code": not_utf8},
    ]
    for over in bad:
        assert main(argv_for(tmp_path, **over)) == 2, over
        capsys.readouterr()
    assert main([]) == 2  # no subcommand
    assert main(["refusal-check", str(tmp_path / "missing.txt")]) == 2
    assert not (tmp_path / "attempts.jsonl").exists()


def test_an_unwritable_out_path_exits_2(tmp_path, capsys):
    blocker = tmp_path / "blocker"
    blocker.write_text("a file, not a directory", encoding="utf-8")
    assert main(argv_for(tmp_path, reply=write(tmp_path, "reply.txt", REFUSAL), out=blocker / "attempts.jsonl")) == 2
    assert "cannot write" in capsys.readouterr().err


@needs_ot
def test_record_cli_grades_code_against_a_reference_end_to_end(tmp_path, capsys):
    good = write(tmp_path, "good.py", GOOD)
    argv = argv_for(tmp_path, code=good, reference=good, ot_python=find_ot_python())
    assert main(argv) == 0
    rec = json.loads(capsys.readouterr().out)
    assert rec["outcome"] == "attempted" and rec["simulate_pass"] is True and rec["rule_violations"] == 0 and rec["gt_diff"] == 0
    assert rec["detail"]["code_sha256"] == sha(GOOD)
    assert lines_of(tmp_path / "attempts.jsonl") == [rec]


@pytest.mark.parametrize(
    "text,refused,kind",
    [
        (REFUSAL, True, "text_pattern"),
        ("AgentSafetyRefusalError: stopped", True, "harness_error"),
        (fenced(GOOD), False, None),
        (fenced(GOOD, HEDGE), False, None),
        ("Just a plan, no code.", False, "no_code"),
    ],
    ids=["wording", "stop-reason-marker", "code", "hedge-with-code", "no-code"],
)
def test_refusal_check_prints_the_verdict_and_exits_0(tmp_path, capsys, text, refused, kind):
    path = write(tmp_path, "reply.txt", text)
    assert main(["refusal-check", path]) == 0
    # the verdict, then the arguments it was computed from (none of the optional ones was given)
    assert json.loads(capsys.readouterr().out) == {"refused": refused, "kind": kind, "file": path, "code": None, "finish_reason": None}


def test_refusal_check_reads_standard_input(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(REFUSAL.encode("utf-8")), encoding="utf-8"))
    assert main(["refusal-check", "-"]) == 0
    assert json.loads(capsys.readouterr().out) == {"refused": True, "kind": "text_pattern", "file": "-", "code": None, "finish_reason": None}


def test_refusal_check_finish_reason_sees_a_refusal_that_has_no_text(tmp_path, capsys):
    empty = write(tmp_path, "reply.txt", "")
    used = {"file": empty, "code": None}
    for extra, want in [
        ([], {"refused": False, "kind": "no_code", "finish_reason": None}),  # control: nothing says it was refused
        (["--finish-reason", "refusal"], {"refused": True, "kind": "api_stop_reason", "finish_reason": "refusal"}),
        (["--finish-reason", "content_filter"], {"refused": True, "kind": "api_stop_reason", "finish_reason": "content_filter"}),
        (["--finish-reason", "end_turn"], {"refused": False, "kind": "no_code", "finish_reason": "end_turn"}),  # ignored, but echoed
    ]:
        assert main(["refusal-check", empty, *extra]) == 0
        assert json.loads(capsys.readouterr().out) == {**used, **want}, extra


def test_refusal_check_code_beats_wording_but_not_a_refusal_finish_reason(tmp_path, capsys):
    reply = write(tmp_path, "reply.txt", REFUSAL)
    code, blank = write(tmp_path, "code.py", GOOD), write(tmp_path, "blank.py", "  \n")
    for extra, refused, kind in [
        (["--code", code], False, None),
        (["--code", blank], True, "text_pattern"),  # blank code is no code
        (["--code", code, "--finish-reason", "refusal"], True, "api_stop_reason"),
        (["--code", code, "--finish-reason", "end_turn"], False, None),
    ]:
        assert main(["refusal-check", reply, *extra]) == 0
        out = json.loads(capsys.readouterr().out)
        assert (out["refused"], out["kind"]) == (refused, kind), extra
        assert out["file"] == reply and out["code"] == extra[1]  # the arguments it used are echoed


def test_refusal_check_reads_an_empty_reply_from_standard_input_with_a_finish_reason(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(b""), encoding="utf-8"))
    assert main(["refusal-check", "-", "--finish-reason", "content_filter"]) == 0
    want = {"refused": True, "kind": "api_stop_reason", "file": "-", "code": None, "finish_reason": "content_filter"}
    assert json.loads(capsys.readouterr().out) == want


def test_refusal_check_with_an_unreadable_code_file_exits_2(tmp_path, capsys):
    reply = write(tmp_path, "reply.txt", REFUSAL)
    assert main(["refusal-check", reply, "--code", str(tmp_path / "missing.py")]) == 2
    captured = capsys.readouterr()
    assert captured.out == "" and "missing.py" in captured.err


def test_help_exits_0_and_lists_both_commands(capsys):
    assert main(["--help"]) == 0
    out = capsys.readouterr().out
    assert "record" in out and "refusal-check" in out


def test_the_module_runs_as_python_dash_m(tmp_path):
    proc = subprocess.run([sys.executable, "-m", "vaxforge.s9_attempt", "refusal-check", "-"], input=REFUSAL + "\n",
                          capture_output=True, text=True, timeout=120, cwd=tmp_path, env={**os.environ, "PYTHONPATH": str(SRC)})
    assert proc.returncode == 0, proc.stderr[-400:]
    assert json.loads(proc.stdout) == {"refused": True, "kind": "text_pattern", "file": "-", "code": None, "finish_reason": None}
