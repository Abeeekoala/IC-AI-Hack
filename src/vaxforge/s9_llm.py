"""S9 code-generation backends and the refusal detector.

README S9 says the LLM writes the protocol code; README S9.5 says a fine-tune may replace
the base model, with the SAME validation applied to both. So code generation sits behind
one small interface, `CodegenBackend`:

    TemplateBackend   deterministic renderer of the S9 plan (no model, works offline; the
                      reproducible baseline and the demo default)
    LLMBackend        a chat model writes the code from either the plan ("spec" mode, the
                      run can then be checked against the plan) or only the S8 protocol
                      ("free" mode, only the generic rules apply)

A fine-tuned model (stream S9.5) plugs in by implementing `CodegenBackend.generate`.

Refusals are a distinct outcome, never a crash and never something to engineer around
(README S9, "Expect safety refusals"). The detector lives in `vaxforge/refusal.py` (standard
library only, so that one file can be copied elsewhere) and is re-exported here, so
`detect_refusal`, `refusal_verdict`, `RefusalVerdict` and `extract_python` can still be imported
from this module. S9.5's training-data filter and win-rate harness use the same detector as S9.
"""

from __future__ import annotations

import os
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from vaxforge.refusal import RefusalVerdict, _stop_reason, _text_of, detect_refusal, extract_python, refusal_verdict
from vaxforge.s9_plan import (
    API_LEVEL,
    Action,
    Comment,
    Delay,
    DeactivateTemp,
    Distribute,
    Drop,
    Magnet,
    Mix,
    Pause,
    PickUp,
    SessionPlan,
    SetTemp,
    Transfer,
    banner,
    render_python,
)

DEFAULT_MODEL = "claude-sonnet-5-5"


def default_model_id() -> str:
    return os.environ.get("VAXFORGE_CODEGEN_MODEL", DEFAULT_MODEL)


# --------------------------------------------------------------------------- request / result


class CodegenRequest(BaseModel):
    session_id: str
    plan: SessionPlan | None = None  # None -> "free" mode
    step_info: dict[str, tuple[str, str, str]] = Field(default_factory=dict)  # step id -> (title, OWNER, note)
    header: dict[str, str] = Field(default_factory=dict)
    protocol_steps: list[dict[str, Any]] = Field(default_factory=list)  # S8 steps, for free mode


class GenerationResult(BaseModel):
    backend: str
    model_id: str | None = None
    code: str | None = None
    refused: bool = False
    refusal_kind: str | None = None
    refusal_detail: str | None = None
    error: str | None = None
    stop_reason: str | None = None
    raw_text: str | None = None


@runtime_checkable
class CodegenBackend(Protocol):
    label: str
    model_id: str | None

    def generate(self, request: CodegenRequest) -> GenerationResult: ...


class TemplateBackend:
    """Deterministic renderer of the plan. Needs a plan; produces byte-identical output for the same plan."""

    label = "template"
    model_id: str | None = None

    def generate(self, request: CodegenRequest) -> GenerationResult:
        if request.plan is None:
            return GenerationResult(backend=self.label, error="the template backend needs a plan (spec mode)")
        code = render_python(request.plan, step_info=request.step_info, header=request.header)
        return GenerationResult(backend=self.label, code=code)


# --------------------------------------------------------------------------- refusal detection
#
# Moved to vaxforge/refusal.py (imported at the top): RefusalVerdict, detect_refusal, refusal_verdict, extract_python.

__all__ = [
    "DEFAULT_MODEL", "SYSTEM_PROMPT", "CodegenBackend", "CodegenRequest", "GenerationResult", "LLMBackend", "RefusalVerdict",
    "TemplateBackend", "build_prompt", "default_model_id", "detect_refusal", "extract_python", "refusal_verdict",
]


# --------------------------------------------------------------------------- prompts

SYSTEM_PROMPT = f"""You write Opentrons OT-2 protocols in Python for a wet-lab automation pipeline.

Output exactly ONE fenced ```python code block and nothing else.

Hard requirements
- Opentrons Python API {API_LEVEL}, OT-2. Start with `from opentrons import protocol_api`, then
  `requirements = {{"robotType": "OT-2", "apiLevel": "{API_LEVEL}"}}` and `def run(ctx: protocol_api.ProtocolContext):`.
- Imports allowed: opentrons, math, typing, itertools, string, collections, functools, dataclasses. No file, network,
  OS, eval/exec, getattr/setattr, or underscore-prefixed attributes.
- Declare the initial contents of every well the robot draws from with ctx.define_liquid(...) and well.load_liquid(liquid=..., volume=...).
- Physical rules the code is graded on (a run that breaks any of them FAILS):
  1. a tip is on the pipette before every aspiration and dispense;
  2. never dispense more than the tip holds;
  3. never aspirate more than a non-reservoir well holds;
  4. no tip is left on the pipette when the protocol ends;
  5. use a fresh tip whenever the liquid source changes, and never reuse a tip that has drawn from a well it dispensed into.
- Annotate every step with ctx.comment("[<step id>] <title> | <OWNER> | <note>") using the exact banner text you are given.
  Steps a person must do use ctx.pause("HUMAN [<step id>]: <instruction>") with the exact text you are given.
- Follow the specification EXACTLY: same labware in the same slots, same volumes, same wells, same order. Do not add, drop or
  change steps, and do not invent values. If something cannot be done, leave it out and say nothing; do not improvise.

API {API_LEVEL} pitfalls
- The OT-2 trash is implicit: drop tips with `pipette.drop_tip()` and blow out with `pipette.blow_out()`, both with no argument.
  Never subscript a trash. `ctx.fixed_trash["A1"]` was removed in API 2.16 and raises TypeError.
- Labware slots are strings or ints (`"1"` or `1`).
- Load modules with `ctx.load_module(name, slot)`; load labware that sits on a module with `module.load_labware(name)`.
"""


def _action_line(i: int, a: Action) -> str:
    if isinstance(a, Comment):
        return f"{i}. comment: {a.text}"
    if isinstance(a, Pause):
        return f'{i}. pause (human hand-off): ctx.pause("{a.text}")'
    if isinstance(a, Delay):
        return f"{i}. delay {a.seconds:g} s" + (f" ({a.text})" if a.text else "")
    if isinstance(a, SetTemp):
        return f"{i}. set temperature module {a.module} to {a.celsius:g} C and wait"
    if isinstance(a, DeactivateTemp):
        return f"{i}. deactivate temperature module {a.module}"
    if isinstance(a, Magnet):
        return f"{i}. {'engage' if a.engage else 'disengage'} magnetic module {a.module}"
    if isinstance(a, PickUp):
        return f"{i}. pick up a tip with {a.pipette}"
    if isinstance(a, Drop):
        return f"{i}. drop the tip held by {a.pipette}"
    if isinstance(a, Mix):
        tip = "with a fresh tip (pick up, mix, drop)" if a.new_tip == "once" else "with the tip already held"
        return f"{i}. mix {a.reps}x {a.volume:g} uL at {a.at} using {a.pipette}, {tip}"
    if isinstance(a, Transfer):
        dst = ", ".join(str(d) for d in a.dst)
        extra = []
        if a.mix_before:
            extra.append(f"mix before {a.mix_before[0]}x{a.mix_before[1]:g} uL")
        if a.mix_after:
            extra.append(f"mix after {a.mix_after[0]}x{a.mix_after[1]:g} uL")
        if a.src_pos != "bottom" or a.src_z is not None:
            extra.append(f"aspirate at {a.src_pos}(z={a.src_z})")
        if a.dst_pos != "bottom" or a.dst_z is not None:
            extra.append(f"dispense at {a.dst_pos}(z={a.dst_z})")
        return f"{i}. {a.pipette}.transfer({a.volume:g} uL, {a.src} -> [{dst}], new_tip={a.new_tip!r}" + (", " + "; ".join(extra) if extra else "") + ")"
    if isinstance(a, Distribute):
        dst = ", ".join(str(d) for d in a.dst)
        return f"{i}. {a.pipette}.distribute({a.volume:g} uL, {a.src} -> [{dst}], disposal_volume={a.disposal_ul:g}, new_tip='once')"
    return f"{i}. {a!r}"


def build_prompt(req: CodegenRequest) -> str:
    """The user message. Spec mode lists the exact plan; free mode lists only the S8 steps."""
    lines: list[str] = []
    if req.plan is not None:
        plan = req.plan
        lines += [f"Session: {plan.session_id} - {plan.title}", f"Covers S8 steps: {', '.join(plan.covers)}", "", "DECK"]
        for m in plan.modules:
            lines.append(f"- module {m.key} = ctx.load_module('{m.name}', '{m.slot}')")
        for lw in plan.labware:
            where = f"{lw.module}.load_labware('{lw.load_name}')" if lw.module else f"ctx.load_labware('{lw.load_name}', '{lw.slot}')"
            lines.append(f"- {lw.key} = {where}   # {lw.label or ''}")
        for p in plan.pipettes:
            lines.append(f"- {p.key} = ctx.load_instrument('{p.api_name}', '{p.mount}', tip_racks=[{', '.join(p.tipracks)}])")
        lines += ["", "INITIAL CONTENTS (declare with define_liquid / load_liquid)"]
        for liq in plan.liquids:
            lines.append(f"- {liq.ref}: {liq.reagent}, {liq.volume_ul:g} uL ({liq.source})")
        lines += ["", "STEP BANNERS (emit each as ctx.comment(...) exactly once, where its step begins)"]
        for sid in plan.covers:
            title, owner, note = req.step_info.get(sid, (sid, "ROBOT-PARTIAL", ""))
            lines.append(f"- {banner(sid, title, owner, note)}")
        lines += ["", "ACTIONS, in this order"]
        lines += [_action_line(i + 1, a) for i, a in enumerate(plan.actions)]
        if req.header:
            lines += ["", "HEADER COMMENTS to include at the top of the file"] + [f"# {k}: {v}" for k, v in req.header.items()]
    else:
        lines += [f"Session: {req.session_id}", "", "Design the deck and liquid handling yourself for these S8 steps (robot-owned parts only):"]
        for s in req.protocol_steps:
            lines.append(f"- [{s['step_id']}] {s['title']} (automation: {s['automation']}; {s['automation_note']})")
            if s.get("reagents"):
                lines.append(f"    reagents: {', '.join(s['reagents'])}")
            if s.get("controls"):
                lines.append(f"    controls: {', '.join(s['controls'])}")
            if s.get("gate"):
                lines.append(f"    gate: {s['gate']}")
        lines += ["", "Use placeholder-sized volumes and say so in a comment; put a ctx.pause('HUMAN [<id>]: ...') wherever a person must act."]
    return "\n".join(lines)


# --------------------------------------------------------------------------- LLM backend


class LLMBackend:
    """Chat-model code generation. `client` is anything with `.messages.create(...)` (the Anthropic SDK shape)."""

    def __init__(self, model_id: str | None = None, client: Any | None = None, max_tokens: int = 12000):
        self.model_id = model_id or default_model_id()
        self.client = client
        self.max_tokens = max_tokens
        self.label = f"llm_{self.model_id}"

    def _client(self) -> Any:
        if self.client is None:
            import anthropic  # optional dependency; ANTHROPIC_API_KEY is read by the SDK, never by us

            self.client = anthropic.Anthropic()
        return self.client

    def generate(self, request: CodegenRequest) -> GenerationResult:
        base = dict(backend=self.label, model_id=self.model_id)
        try:
            client = self._client()
        except ImportError:
            return GenerationResult(**base, error="the `anthropic` package is not installed; pip install anthropic, or inject a client")
        except Exception as exc:  # noqa: BLE001
            return GenerationResult(**base, error=f"could not create the model client: {type(exc).__name__}")
        try:
            resp = client.messages.create(
                model=self.model_id, max_tokens=self.max_tokens, system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": build_prompt(request)}],
            )
        except Exception as exc:  # noqa: BLE001 - surface provider failures as data
            verdict = refusal_verdict(f"{type(exc).__name__}: {exc}")
            if verdict.refused:
                return GenerationResult(**base, refused=True, refusal_kind=verdict.kind, refusal_detail=verdict.detail)
            return GenerationResult(**base, error=f"{type(exc).__name__}: {str(exc)[:300]}")
        stop = _stop_reason(resp)
        text = _text_of(resp)
        verdict = refusal_verdict(resp)
        if verdict.refused:
            return GenerationResult(**base, refused=True, refusal_kind=verdict.kind, refusal_detail=verdict.detail,
                                    stop_reason=stop, raw_text=text[:2000])
        code = extract_python(text)
        if code is None:
            return GenerationResult(**base, error="the reply contained no Python protocol", stop_reason=stop, raw_text=text[:2000])
        return GenerationResult(**base, code=code, stop_reason=stop, raw_text=None)
