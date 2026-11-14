"""`vaxforge.refusal`: the dependency-free refusal detector (README S9 "Expect safety refusals", S9.5 refusal rule).

Each positive case has a negative control next to it, because a detector that always says "refused" (or never does) passes
half of any one-sided suite. The file also pins what stream D relies on: one stdlib-only module, the callable
`detect_refusal(text, code, finish_reason)`, and `vaxforge.s9_llm.detect_refusal` still being that same function.
"""

import ast
import dataclasses
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaxforge import refusal, s9_llm
from vaxforge.refusal import REFUSAL_FINISH_REASONS, RefusalVerdict, detect_refusal, extract_python, refusal_verdict

CODE = "from opentrons import protocol_api\nrequirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}\ndef run(ctx):\n    pass\n"
FENCED = "Here is the protocol.\n```python\n" + CODE + "```\n"
REFUSAL = "I can't help with that."
HEDGE = "I can't vouch for these volumes, but here is the protocol:\n```python\n" + CODE + "```\n"
KINDS = {"api_stop_reason", "harness_error", "text_pattern", "no_code", None}
ALLOWED_IMPORTS = {"re", "typing", "dataclasses", "__future__"}


def msg(text, stop_reason="end_turn"):
    """An SDK-shaped message: `.content` blocks that carry `.text`, and `.stop_reason` (the Anthropic Message shape)."""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)


# --------------------------------------------------------------------------- finish_reason: the refusal with no text


def test_the_refusal_finish_reasons_are_a_module_constant():
    assert REFUSAL_FINISH_REASONS == ("refusal", "content_filter")


@pytest.mark.parametrize("text", [None, "", "   \n"], ids=["none", "empty", "blank"])
def test_a_refusal_finish_reason_with_an_empty_body_is_a_refusal(text):
    # The gap: a provider stop_reason="refusal" can arrive with NO text, so no wording rule can ever see it.
    verdict = refusal_verdict(text, None, "refusal")
    assert verdict.refused is True and verdict.kind == "api_stop_reason"
    assert detect_refusal(text, None, "refusal") is True and detect_refusal(text, finish_reason="refusal") is True
    # control: the same empty body without the finish reason is a failed attempt, not a refusal
    assert detect_refusal(text) is False and refusal_verdict(text).kind == "no_code"


def test_content_filter_is_a_refusal_finish_reason():
    verdict = refusal_verdict("", None, "content_filter")
    assert verdict.refused is True and verdict.kind == "api_stop_reason" and "content_filter" in verdict.detail


@pytest.mark.parametrize("reason", ["REFUSAL", "Refusal", "CONTENT_FILTER", "Content_Filter", " refusal ", "refusal\n"])
def test_the_finish_reason_is_compared_case_insensitively(reason):
    assert detect_refusal("", None, reason) is True


@pytest.mark.parametrize("reason", ["end_turn", "stop", "length", "max_tokens", "tool_use", "", "   ", None, 0])
def test_any_other_finish_reason_changes_nothing(reason):
    assert detect_refusal("", None, reason) is False  # nothing to refuse with
    assert detect_refusal(REFUSAL, None, reason) is True  # wording still refuses ...
    assert detect_refusal(FENCED, None, reason) is False  # ... and code still wins
    assert refusal_verdict(REFUSAL, None, reason) == refusal_verdict(REFUSAL)


# --------------------------------------------------------------------------- code, markers, wording


def test_code_present_beats_refusal_wording():
    assert detect_refusal(REFUSAL, CODE) is False
    assert refusal_verdict(REFUSAL, CODE).kind is None
    assert detect_refusal(REFUSAL) is True  # control: without the code, the same text is a refusal


def test_code_present_does_not_beat_a_refusal_finish_reason():
    assert detect_refusal(FENCED, CODE, "refusal") is True
    assert detect_refusal("", CODE, "content_filter") is True
    assert refusal_verdict(FENCED, CODE, "refusal").kind == "api_stop_reason"
    assert detect_refusal(FENCED, CODE, "end_turn") is False  # control


@pytest.mark.parametrize("code", [None, "", "   ", "\n\t "], ids=["none", "empty", "spaces", "whitespace"])
def test_blank_code_counts_as_no_code(code):
    assert detect_refusal(REFUSAL, code) is True
    assert detect_refusal("AgentSafetyRefusalError: declined", code) is True
    assert refusal_verdict("a plan, no code", code).kind == "no_code"


@pytest.mark.parametrize(
    "text",
    [
        "AgentSafetyRefusalError: the [bio] safeguard declined the request",
        "RuntimeError: the request was refused by the provider",
        "BadRequestError: content policy violation",
        "SAFETY REFUSAL",
    ],
    ids=["agent-safety-error", "refused-by-provider", "content-policy", "safety-refusal"],
)
def test_a_harness_marker_is_a_refusal(text):
    verdict = refusal_verdict(text)
    assert verdict.refused is True and verdict.kind == "harness_error"
    assert detect_refusal(text.lower()) is True and detect_refusal(text.upper()) is True  # markers ignore case


def test_a_marker_refuses_a_text_that_has_a_code_block_but_not_when_the_caller_supplies_code():
    marked = "AgentSafetyRefusalError\n" + FENCED
    assert detect_refusal(marked) is True  # as before: markers are checked before the text's own code block
    assert detect_refusal(marked, CODE) is False  # the caller's own code argument is checked before any text rule


def test_a_hedged_reply_with_code_is_not_a_refusal():
    assert detect_refusal(HEDGE) is False and refusal_verdict(HEDGE).kind is None
    assert detect_refusal(HEDGE, CODE) is False
    assert detect_refusal(msg(HEDGE)) is False
    # control: the same hedge with no code in it is a refusal
    assert detect_refusal("I can't vouch for these volumes, so I cannot provide this protocol.") is True


@pytest.mark.parametrize(
    "text",
    [
        "I can't help with writing a protocol for transforming bacteria.",
        "I can’t help with that.",
        "I won’t write that protocol.",
        "I'm unable to assist with this request.",
        "I must decline to generate that.",
        "That is against my guidelines.",
        "I cannot provide this protocol.",
        "Sorry, can't help with this.",
    ],
    ids=["cant-help", "curly-cant", "curly-wont", "unable-to-assist", "must-decline", "against-guidelines", "cannot-provide", "bare-cant"],
)
def test_refusal_wording_without_code_is_a_refusal(text):
    verdict = refusal_verdict(text)
    assert verdict.refused is True and verdict.kind == "text_pattern" and verdict.detail


def test_wording_is_only_looked_for_in_the_first_800_characters():
    inside, beyond = "x" * 700 + " " + REFUSAL, "x" * 801 + " " + REFUSAL
    assert detect_refusal(inside) is True
    assert detect_refusal(beyond) is False and refusal_verdict(beyond).kind == "no_code"


@pytest.mark.parametrize(
    "text",
    [None, "", "   \n", "First I would pick up a tip, then move 50 uL into well A1.", "Sure! Let me think about the deck layout first."],
    ids=["none", "empty", "blank", "plan-only", "thinking-aloud"],
)
def test_no_code_and_no_refusal_wording_is_not_a_refusal(text):
    verdict = refusal_verdict(text)
    assert verdict.refused is False and verdict.kind == "no_code"
    assert detect_refusal(text) is False


def test_calling_it_with_no_arguments_is_not_a_refusal():
    assert detect_refusal() is False
    assert refusal_verdict() == refusal_verdict(None, None, None)


def test_every_kind_is_reachable_and_there_are_no_others():
    cases = [("", None, "refusal"), (REFUSAL, None, None), ("AgentSafetyRefusalError", None, None), ("a plan only", None, None), (FENCED, None, None)]
    assert {refusal_verdict(*case).kind for case in cases} == KINDS


def test_a_verdict_is_a_frozen_record_that_says_where_the_reason_came_from():
    verdict = refusal_verdict(REFUSAL)
    assert (verdict.refused, verdict.kind) == (True, "text_pattern") and isinstance(verdict.detail, str)
    with pytest.raises(dataclasses.FrozenInstanceError):
        verdict.refused = False
    # the text of the stop_reason detail is unchanged from before the move; an explicit argument says finish_reason
    assert refusal_verdict(msg("", "refusal")).detail == "the provider ended the turn with stop_reason='refusal'"
    assert refusal_verdict("", None, "content_filter").detail == "the provider ended the turn with finish_reason='content_filter'"


def test_extract_python_moved_with_its_behaviour():
    assert extract_python(FENCED) == CODE.strip() + "\n"
    assert extract_python(CODE) == CODE.strip() + "\n"  # a bare file
    assert extract_python("no code here") is None and extract_python("") is None and extract_python(None) is None


# --------------------------------------------------------------------------- the first argument: message, dict, string, None


@pytest.mark.parametrize(
    "response,refused,kind",
    [
        (msg(REFUSAL), True, "text_pattern"),
        (msg("", "refusal"), True, "api_stop_reason"),
        (msg("", "content_filter"), True, "api_stop_reason"),
        (msg(FENCED, "refusal"), True, "api_stop_reason"),
        (msg(FENCED), False, None),
        (msg(HEDGE), False, None),
        (msg("AgentSafetyRefusalError: declined"), True, "harness_error"),
        (msg("Let me think."), False, "no_code"),
        ({"content": [{"type": "text", "text": REFUSAL}], "stop_reason": "end_turn"}, True, "text_pattern"),
        ({"content": [], "stop_reason": "refusal"}, True, "api_stop_reason"),
        ({"content": REFUSAL}, True, "text_pattern"),
        ({"content": [{"type": "text", "text": FENCED}]}, False, None),
        ({}, False, "no_code"),
        (SimpleNamespace(content=REFUSAL), True, "text_pattern"),
        (REFUSAL, True, "text_pattern"),
        (FENCED, False, None),
        ("RuntimeError: AgentSafetyRefusalError: declined", True, "harness_error"),
        (None, False, "no_code"),
    ],
    ids=[
        "sdk-wording", "sdk-stop-refusal", "sdk-stop-content-filter", "sdk-code-but-stop-refusal", "sdk-code", "sdk-hedge-with-code",
        "sdk-harness-marker", "sdk-no-code", "dict-blocks", "dict-stop-refusal", "dict-string-content", "dict-code", "dict-empty",
        "object-string-content", "string-wording", "string-code", "string-exception-text", "none",
    ],
)
def test_the_first_argument_may_be_a_message_a_dict_an_exception_text_a_string_or_none(response, refused, kind):
    verdict = refusal_verdict(response)
    assert (verdict.refused, verdict.kind) == (refused, kind)
    assert detect_refusal(response) is refused


def test_explicit_arguments_still_apply_when_the_first_argument_is_a_message():
    assert detect_refusal(msg(REFUSAL), CODE) is False  # code beats wording
    assert detect_refusal(msg("Here you go."), None, "refusal") is True
    assert detect_refusal(msg("", "end_turn"), None, "content_filter") is True
    assert detect_refusal(msg("", "refusal"), CODE) is True  # the message's own stop_reason beats code
    assert detect_refusal(msg("", "refusal"), None, "end_turn") is True  # an explicit non-refusal never masks it


def test_it_works_as_the_plain_callable_stream_d_passes_around():
    check = detect_refusal
    assert check("", None, "refusal") is True
    assert check(text="", code=None, finish_reason="refusal") is True
    assert check(text=REFUSAL, code=CODE, finish_reason=None) is False
    assert check(None, None, None) is False


# --------------------------------------------------------------------------- one stdlib-only file, and the old import path


def test_the_module_imports_only_re_typing_dataclasses_and_future():
    tree = ast.parse(Path(refusal.__file__).read_text(encoding="utf-8"))
    found = set()
    for node in ast.walk(tree):  # every import statement, including any hidden inside a function
        if isinstance(node, ast.Import):
            found |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "a relative import ties the file to its package"
            found.add((node.module or "").split(".")[0])
    assert {"re", "__future__"} <= found  # the walk saw the imports it is meant to police
    assert found <= ALLOWED_IMPORTS, sorted(found - ALLOWED_IMPORTS)
    dynamic = {n.id for n in ast.walk(tree) if isinstance(n, ast.Name) and n.id in {"__import__", "importlib"}}
    assert not dynamic, f"no dynamic imports: {sorted(dynamic)}"


def test_the_file_works_when_copied_alone_into_a_directory_without_vaxforge(tmp_path):
    lone = tmp_path / "lone"
    lone.mkdir()
    (lone / "refusal.py").write_bytes(Path(refusal.__file__).read_bytes())
    script = (
        "import sys\n"
        "sys.path.insert(0, sys.argv[1])\n"
        "import refusal\n"
        "assert refusal.__file__.startswith(sys.argv[1]), refusal.__file__\n"
        "assert refusal.detect_refusal(\"I can't help with that.\") is True\n"
        "assert refusal.detect_refusal('', None, 'refusal') is True\n"
        "assert refusal.detect_refusal('no code and no wording') is False\n"
        "assert refusal.detect_refusal('x', 'def run(): pass') is False\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('vaxforge', 'pydantic'))\n"
        "assert not loaded, loaded\n"
        "print('ok')\n"
    )
    # `-I` drops PYTHONPATH and the working directory from sys.path, so `vaxforge` cannot be imported by accident; the
    # program arrives on stdin, which avoids quoting a multi-line `-c` argument on Windows.
    proc = subprocess.run([sys.executable, "-I", "-", str(lone)], input=script, capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert proc.returncode == 0, proc.stderr[-400:]
    assert proc.stdout.strip() == "ok"


def test_vaxforge_s9_llm_detect_refusal_is_the_same_function():
    from vaxforge.s9_llm import detect_refusal as via_s9_llm

    assert via_s9_llm is detect_refusal
    assert s9_llm.refusal_verdict is refusal_verdict and s9_llm.extract_python is extract_python
    assert s9_llm.RefusalVerdict is RefusalVerdict
    assert via_s9_llm("", None, "refusal") is True  # the three-argument form works through the old import path
    assert via_s9_llm(msg(REFUSAL)) is True  # and so does the one-argument form every existing caller uses
