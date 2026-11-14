"""S9 code-generation backends and the refusal detector (no network, no API key)."""

from types import SimpleNamespace

import pytest

from vaxforge.s8_protocol import Candidate, ProtocolConfig, build_protocol
from vaxforge.s9_llm import (
    CodegenBackend,
    CodegenRequest,
    SYSTEM_PROMPT,
    LLMBackend,
    TemplateBackend,
    build_prompt,
    detect_refusal,
    extract_python,
    refusal_verdict,
)
from vaxforge.s9_plan import API_LEVEL
from vaxforge.s9_sessions import plan_sessions

CODE = "from opentrons import protocol_api\nrequirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}\ndef run(ctx):\n    pass\n"


def msg(text, stop_reason="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop_reason)


class FakeClient:
    def __init__(self, fn):
        self.messages = self
        self.fn = fn
        self.calls = []

    def create(self, **kw):
        self.calls.append(kw)
        return self.fn(kw)


def request():
    proto = build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1), ProtocolConfig())
    plan = plan_sessions(proto)[0]
    info = {s.step_id: (s.title, "ROBOT", s.automation_note) for s in proto.steps}
    return CodegenRequest(session_id=plan.session_id, plan=plan, step_info={sid: info[sid] for sid in plan.covers},
                          header={"Candidate": "c1"})


# --------------------------------------------------------------------------- extraction / detection


def test_extract_python():
    assert extract_python("Here you go:\n```python\n" + CODE + "```\nDone.") == CODE.strip() + "\n"
    assert extract_python("```\nx = 1\n```\n```python\n" + CODE + "```") == CODE.strip() + "\n"  # prefers the block that defines run
    assert extract_python(CODE) == CODE.strip() + "\n"  # a bare file
    assert extract_python("no code here") is None and extract_python("") is None


@pytest.mark.parametrize("reply", [msg("", "refusal"), {"content": [], "stop_reason": "refusal"}])
def test_provider_refusals_are_detected_by_stop_reason(reply):
    v = refusal_verdict(reply)
    assert v.refused and v.kind == "api_stop_reason" and detect_refusal(reply)


@pytest.mark.parametrize(
    "text",
    [
        "I can't help with writing a protocol for transforming bacteria.",
        "I'm unable to assist with this request.",
        "I must decline to generate that.",
        "That is against my guidelines.",
        "I cannot provide this protocol.",
        "AgentSafetyRefusalError: the [bio] safeguard declined the request",
    ],
)
def test_text_refusals_are_detected(text):
    assert detect_refusal(text)


def test_a_reply_that_delivers_code_is_not_a_refusal_even_if_it_hedges():
    assert not detect_refusal("I can't verify the volumes are right for your kit, but here is the protocol:\n```python\n" + CODE + "```")
    assert not detect_refusal(msg("```python\n" + CODE + "```"))


def test_failing_to_produce_code_is_not_a_refusal():
    v = refusal_verdict("Sure! Let me think about the deck layout first.")
    assert not v.refused and v.kind == "no_code"
    assert not detect_refusal("") and not detect_refusal(None)


# --------------------------------------------------------------------------- backends


def test_template_backend_is_deterministic_and_needs_a_plan():
    b = TemplateBackend()
    assert isinstance(b, CodegenBackend) and b.label == "template" and b.model_id is None
    r1, r2 = b.generate(request()), b.generate(request())
    assert r1.code and r1.code == r2.code and not r1.refused
    assert "plan-sha256" in r1.code
    assert b.generate(CodegenRequest(session_id="x")).error


def test_llm_backend_returns_the_code_and_sends_the_spec():
    client = FakeClient(lambda kw: msg("```python\n" + CODE + "```"))
    b = LLMBackend(model_id="test-model", client=client)
    assert isinstance(b, CodegenBackend)
    res = b.generate(request())
    assert res.code == CODE.strip() + "\n" and not res.refused and res.error is None and res.model_id == "test-model"
    call = client.calls[0]
    assert call["model"] == "test-model" and "five" not in call["system"]  # was `... or True`, which made it a no-op
    assert "fresh tip" in call["system"] and "ctx.pause" in call["system"]
    prompt = call["messages"][0]["content"]
    plan = request().plan
    assert plan.session_id in prompt and "ACTIONS, in this order" in prompt and "STEP BANNERS" in prompt
    assert all(f"{i + 1}." in prompt for i in range(len(plan.actions)))
    for lw in plan.labware:
        assert lw.load_name in prompt


def test_system_prompt_warns_about_the_api_pitfalls_that_broke_a_real_run():
    # A real model wrote `ctx.fixed_trash["A1"]` (removed in API 2.16; the OT-2 trash is implicit) and the simulation failed.
    client = FakeClient(lambda kw: msg("```python\n" + CODE + "```"))
    LLMBackend(model_id="m", client=client).generate(request())
    sent = client.calls[0]["system"]
    assert sent == SYSTEM_PROMPT
    assert f"API {API_LEVEL} pitfalls" in sent
    # the trash is implicit: drop and blow out without an argument, never subscript a trash
    assert "trash is implicit" in sent and "`pipette.drop_tip()`" in sent and "`pipette.blow_out()`" in sent
    assert "no argument" in sent and "Never subscript a trash" in sent and 'ctx.fixed_trash["A1"]' in sent
    # slots, modules, labware on a module
    assert "strings or ints" in sent
    assert "`ctx.load_module(name, slot)`" in sent and "`module.load_labware(name)`" in sent
    # the prompts built from a plan must not contradict the warning
    assert "fixed_trash" not in build_prompt(request())


def test_llm_refusal_is_a_distinct_outcome_not_an_error():
    res = LLMBackend(model_id="m", client=FakeClient(lambda kw: msg("", "refusal"))).generate(request())
    assert res.refused and res.refusal_kind == "api_stop_reason" and res.code is None and res.error is None
    res = LLMBackend(model_id="m", client=FakeClient(lambda kw: msg("I can't help with that."))).generate(request())
    assert res.refused and res.refusal_kind == "text_pattern"


def test_llm_content_filter_stop_reason_is_a_refusal_even_with_an_empty_body():
    res = LLMBackend(model_id="m", client=FakeClient(lambda kw: msg("", "content_filter"))).generate(request())
    assert res.refused and res.refusal_kind == "api_stop_reason" and res.code is None and res.error is None
    assert res.stop_reason == "content_filter"
    # control: the same empty body with an ordinary stop reason is a failed generation, not a refusal
    res = LLMBackend(model_id="m", client=FakeClient(lambda kw: msg("", "end_turn"))).generate(request())
    assert not res.refused and res.code is None and "no Python" in res.error


def test_llm_provider_exception_carrying_a_safety_refusal_is_a_refusal():
    def boom(kw):
        raise RuntimeError("AgentSafetyRefusalError: declined")

    res = LLMBackend(model_id="m", client=FakeClient(boom)).generate(request())
    assert res.refused and res.refusal_kind == "harness_error"


def test_llm_other_failures_are_errors_not_refusals():
    def boom(kw):
        raise TimeoutError("network down")

    res = LLMBackend(model_id="m", client=FakeClient(boom)).generate(request())
    assert not res.refused and res.code is None and "TimeoutError" in res.error
    res = LLMBackend(model_id="m", client=FakeClient(lambda kw: msg("Let me think."))).generate(request())
    assert not res.refused and res.code is None and "no Python" in res.error


@pytest.mark.parametrize(
    "reply",
    ["I can't do that.", "I'm not going to do this.", "As an AI I am unable to give lab protocols", "I refuse to write this protocol."],
)
def test_llm_refusal_wording_the_detector_was_widened_for_is_a_refusal_not_an_error(reply):
    res = LLMBackend(model_id="m", client=FakeClient(lambda kw: msg(reply))).generate(request())
    assert res.refused and res.refusal_kind == "text_pattern" and res.code is None and res.error is None
    assert detect_refusal(reply) and detect_refusal(reply, None, "stop")


@pytest.mark.parametrize("stop", ["max_tokens", "length"])
def test_llm_reply_cut_off_by_the_token_limit_is_an_error_not_a_refusal(stop):
    for text in ("", "To move the liquid I first load the tip rack in slot 1, then the 96-well pl"):
        res = LLMBackend(model_id="m", client=FakeClient(lambda kw, t=text: msg(t, stop))).generate(request())
        assert not res.refused and res.code is None and "no Python" in res.error and res.stop_reason == stop


def test_missing_sdk_is_reported_not_raised(monkeypatch):
    b = LLMBackend(model_id="m")
    monkeypatch.setattr(LLMBackend, "_client", lambda self: (_ for _ in ()).throw(ImportError("no anthropic")))
    res = b.generate(request())
    assert res.error and "anthropic" in res.error and not res.refused


def test_default_model_comes_from_the_environment(monkeypatch):
    monkeypatch.setenv("VAXFORGE_CODEGEN_MODEL", "my-finetune")
    assert LLMBackend().model_id == "my-finetune"
    monkeypatch.delenv("VAXFORGE_CODEGEN_MODEL")
    assert LLMBackend().model_id == "claude-sonnet-5-5"


def test_free_mode_prompt_lists_s8_steps_and_no_plan_values():
    proto = build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1), ProtocolConfig())
    steps = [s.model_dump(mode="json") for s in proto.steps[:3]]
    p = build_prompt(CodegenRequest(session_id="free", plan=None, protocol_steps=steps))
    assert "Design the deck" in p and steps[1]["title"] in p and "ACTIONS" not in p
