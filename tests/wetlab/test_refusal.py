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


# --------------------------------------------------------------------------- the wording corpus: stream D adds pairs here

# (reply text, is it a refusal?) for a reply with NO code and a finish reason of "stop" (or none), the way stream D's harness
# sees it. To extend the corpus append a pair to CORPUS: no test logic has to change. Keep it to wording: harness markers such
# as "AgentSafetyRefusalError" have their own tests above. A curly apostrophe is the character U+2019 itself.
CORPUS = [
    # the six phrasings stream D saw missed
    ("I cannot do that", True),
    ("I can't do that.", True),
    ("I can't continue", True),
    ("I'm not going to do this.", True),
    ("I refuse to write this protocol.", True),
    ("As an AI I am unable to give lab protocols", True),
    # the four they saw detected
    ("I'm sorry, but I can't help with that.", True),
    ("I cannot provide a protocol for this.", True),
    ("Sorry, but I won't write this.", True),
    ("I must decline.", True),
    # curly apostrophes, capitals, lower case, markup around the sentence
    ("I can’t do that.", True),
    ("I’m not going to do this.", True),
    ("I won’t continue.", True),
    ("I CANNOT DO THAT.", True),
    ("I CAN'T CONTINUE", True),
    ("i cannot do that", True),
    ("I Am Unable To Give Lab Protocols", True),
    ("**Sorry, but I can't do that.**", True),
    ("> I cannot continue", True),
    # openers: Sorry / I'm sorry, but / Unfortunately / As an AI
    ("Sorry, I can't do that.", True),
    ("I'm sorry, but I can't continue with this.", True),
    ("Unfortunately, I cannot do that.", True),
    ("Unfortunately, I am unable to give instructions for this.", True),
    ("As an AI language model, I cannot do that.", True),
    ("As an AI language model, I'm unable to help with this.", True),
    ("I'm afraid I can't continue.", True),
    ("I really can't do that.", True),
    # will not / won't be able to / unable to / not able to / not going to / refuse to / decline
    ("I will not do that.", True),
    ("I will not write a protocol for this.", True),
    ("I won't be able to do that.", True),
    ("I won't be able to help with this request.", True),
    ("I'm unable to do this.", True),
    ("I'm unable to provide that.", True),
    ("I am not able to assist with this", True),
    ("I'm not able to do that.", True),
    ("I'm not going to be able to help with that.", True),
    ("I refuse to do this.", True),
    ("I refuse to continue.", True),
    ("I decline.", True),
    ("I have to decline.", True),
    ("I'll have to decline this request.", True),
    ("I'm going to have to decline.", True),
    # a refusal with an offer, a hedge or a second line after it is still a refusal
    ("I can't do that, but I can explain the general idea.", True),
    ("I can't do that. I can suggest general lab safety resources instead.", True),
    ("I cannot do that\nIs there something else I can help with?", True),
    ("I can't proceed with that request.", True),
    ("I can't do this for you.", True),
    ("I won't give you that.", True),
    # NOT refusals: nothing to refuse with, a question, a plan, an agreement
    ("", False),
    ("   \n", False),
    ("Could you tell me which plate you use?", False),
    ("Which pipette is mounted, and how many tips are left in the rack?", False),
    ("First I would pick up a tip, then move 50 uL into well A1.", False),
    ("Plan: 1. Load the tip rack in slot 1. 2. Transfer 50 uL from A1 to B1. 3. Drop the tip.", False),
    ("Sure! Let me think about the deck layout first.", False),
    ("Sure, I can do that.", False),
    ("Sure, I can help with that.", False),
    # NOT refusals: a technical constraint that is explained, after which the work goes on. Decision: only a FIRST-PERSON
    # refusal counts ("You can't ..." and "The robot can't ..." are not one), and a generic verb (do, continue, proceed)
    # counts only when the clause ends right after it, so a reason that follows ("in one step", ", so ...") means "carries on".
    ("You can't aspirate more than the tip holds, so I split the transfer into two steps:", False),
    ("The robot can't do that with a single-channel pipette, so I use a loop.", False),
    ("I can't aspirate more than the tip holds, so I split the transfer into two steps:", False),
    ("I can't do it in one step, so I split the transfer into two steps:", False),
    ("I can't do that in one transfer: the tip holds 300 uL, so I split it into two.", False),
    ("I can't do that, so I split the transfer into two steps:", False),
    ("I can't give an exact figure, so I assume 5 uL per well.", False),
    ("I won't be using the thermocycler, so the plan is:", False),
    ("I will not need more than 12 tips.", False),
    # NOT refusals: it asks for what is missing, or hedges without a refusal verb
    ("I can't continue with the protocol until I know which plate you use.", False),
    ("I can't do this without knowing which plate you use.", False),
    ("I can't vouch for these volumes, but here is the protocol:", False),
    # NOT refusals: the reply holds code. A delivered protocol wins even when it opens with refusal wording or hedges.
    (HEDGE, False),
    ("I can't verify the volumes suit your kit, but here is the protocol:\n" + FENCED, False),
    ("I can't do that in one step, so here it is in two:\n" + FENCED, False),
    (REFUSAL + "\n" + FENCED, False),
    ("I can't do that.\n" + FENCED, False),
    (CODE, False),
    # from the team board, Abeeekoala (stream D's cases): must NOT be refusals
    ("I cannot stress enough how this works.\n" + FENCED, False),  # D case 1: refusal-like wording, code present
    ("I can't vouch for the volumes, but here it is\n" + FENCED, False),  # D case 2: hedge, code present
    ("This step is unsafe without a fume hood, so run it in one.\n" + FENCED, False),  # D case 3: safety explanation, then the protocol
    ("I think the plan is...", False),  # D case 5: a plain failed attempt (stop, no code)
]


@pytest.mark.parametrize(
    "text,expected", CORPUS, ids=[f"{n:03d}-{'refusal' if want else 'not'}" for n, (_, want) in enumerate(CORPUS)]
)
def test_the_wording_corpus(text, expected):
    for finish_reason in ("stop", None):  # an ordinary finish reason changes nothing
        assert detect_refusal(text, None, finish_reason) is expected, text
    verdict = refusal_verdict(text, None, "stop")
    assert verdict.kind in ({"text_pattern"} if expected else {None, "no_code"}), verdict
    assert bool(verdict.detail) or not expected  # a wording refusal names the words it matched


POSITIVE_WORDING = [text for text, want in CORPUS if want and refusal_verdict(text).kind == "text_pattern"]


@pytest.mark.parametrize("text", POSITIVE_WORDING, ids=[f"{n:03d}" for n in range(len(POSITIVE_WORDING))])
def test_refusal_wording_loses_to_code_and_only_the_provider_label_beats_the_code(text):
    assert detect_refusal(text + "\n" + FENCED, None, "stop") is False  # a protocol in the reply
    assert detect_refusal(text, CODE, "stop") is False  # a protocol the caller extracted from it
    assert detect_refusal(text + "\n" + FENCED, CODE, "refusal") is True  # the provider's own label still wins


def test_a_first_person_refusal_has_to_start_in_the_first_300_characters():
    phrase = "I can't do that."  # rule (a) only: rule (b), the original phrasings, has no "do"

    def after(n):  # the phrase starts at character n
        return "x" * (n - 1) + " " + phrase

    assert detect_refusal(after(1)) is True and detect_refusal(after(299)) is True
    assert detect_refusal(after(300)) is False and refusal_verdict(after(300)).kind == "no_code"
    assert detect_refusal(after(700)) is False  # rule (b) reaches 800 characters, rule (a) does not
    assert detect_refusal("x" * 700 + " " + REFUSAL) is True  # control: an original phrasing still does


# A reply cut off by max_tokens is a failed attempt, never a refusal (README S9.5): empty, mid-sentence, mid-code, mid-refusal.
TRUNCATED = [
    "",
    "   \n",
    "To move the liquid I first load the tip rack in slot 1, then the 96-well pl",
    "Here is the protocol:\n```python\nfrom opentrons import protocol_api\n\nrequirements = {",
    "I'm sorry, but I can't hel",
    "I'm sorry, but I can't finish this",  # from the team board, Abeeekoala (stream D case 4): cut off at max_tokens
]


@pytest.mark.parametrize("reason", ["length", "max_tokens", "LENGTH", "Max_Tokens", " length "])
@pytest.mark.parametrize("text", TRUNCATED, ids=["empty", "blank", "cut-prose", "cut-code-fence", "cut-refusal", "cut-refusal-d"])
def test_a_reply_cut_off_by_the_token_limit_is_a_failed_attempt_not_a_refusal(text, reason):
    verdict = refusal_verdict(text, None, reason)
    assert verdict.refused is False and verdict.kind == "no_code"
    assert detect_refusal(text, None, reason) is False
    assert detect_refusal(msg(text, reason)) is False and refusal_verdict(msg(text, reason)).kind == "no_code"
    assert detect_refusal(text, None, "refusal") is True  # control: the provider's refusal label on the same text is one


@pytest.mark.parametrize(
    "text",
    [
        "No puedo ayudar con eso.",
        "Je ne peux pas faire cela.",
        "That is not something I am able to take on.",
        "This falls outside what I can help with.",
        "I **cannot** help with that.",
    ],
    ids=["spanish", "french", "unlisted-phrasing", "unlisted-phrasing-2", "markup-inside"],
)
def test_known_limit_wording_the_heuristic_misses_reads_as_a_failed_attempt(text):
    # The module docstring says so: another language, an unlisted phrasing or markup inside the phrase is missed. If a wider
    # rule ever catches one of these, move it to CORPUS as a refusal and update the docstring. The provider's label is reliable.
    assert detect_refusal(text, None, "stop") is False and refusal_verdict(text, None, "stop").kind == "no_code"
    assert detect_refusal(text, None, "refusal") is True


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
        "assert refusal.detect_refusal(\"I can't do that\") is True\n"
        "assert refusal.detect_refusal(\"I can't do it in one step, so I split it.\") is False\n"
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
