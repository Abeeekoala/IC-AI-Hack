"""S8 narratives: a short plain-language paragraph for each step, written from THAT step's structured fields and nothing else.

README S8 says "LLM + protocol templates". The structured fields (steps, controls, gates, numbers) stay the source of truth; a narrative is a
reading aid and never changes them. Every step gets `narrative` and `narrative_source`:

  template   (default) built by rule from the fields: deterministic, offline, the same bytes for the same protocol.
  llm        (`streamc.s8_backend: llm`, model `streamc.s8_model`) a chat model writes it from ONLY that step's facts (`step_facts`). The reply is
             accepted only if `check_narrative` finds none of: a number, a unit or a number word that the step's own facts lack; a control that is
             not one of the step's controls; an empty text; a text over MAX_CHARS; a text cut off by the token limit. Otherwise - and on a refusal
             or any error - the TEMPLATE text is used and a warning says why. It never raises. The source is then "llm:<model id>" or "template".

The client pattern is `s9_llm.LLMBackend`'s: anything with `.messages.create(...)` (the Anthropic SDK shape; inject a fake in tests), created
lazily from the optional `anthropic` package, which reads the key itself - this module never reads, logs or stores it. Refusals are detected by
`vaxforge.refusal.refusal_verdict` and are an outcome, not an error. After the first non-refusal error (no package, no key, network) the model is not
called again in that run: the rest of the steps use the template and ONE warning says so.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from vaxforge.config import ConfigError
from vaxforge.refusal import refusal_verdict
from vaxforge.streamc_adapter import setting

BACKENDS = ("template", "llm")
SOURCE_TEMPLATE = "template"
MAX_CHARS = 600  # a narrative is one short paragraph; a longer reply is rejected, never cut
MAX_TOKENS = 400  # what the model may write; a reply that stops here is cut off mid-sentence and is rejected
GATE_INLINE_MAX = 200  # the template quotes a gate in full only up to this length; a longer one is pointed to

_OWNER_FACT = {
    "full": "the robot can do the whole step",
    "partial": "the robot does part of the step and a person does the rest",
    "manual": "a person does the step; the robot does not",
}
_OWNER_SENTENCE = {
    "full": "The robot can do this step.",
    "partial": "The robot does part of this step and a person does the rest.",
    "manual": "A person does this step; the robot does not.",
}

SYSTEM_PROMPT = f"""You write one short plain-language paragraph about ONE step of a wet-lab protocol, for a lab technician.

Use ONLY the facts you are given for that step. Rules:
- 1 to 3 sentences, at most {MAX_CHARS} characters, plain prose: no lists, no markdown, no headings, no code.
- Do not add, remove, rename or reorder steps, controls, gates or reagents.
- Do not mention any number, unit, control, reagent or step that is not in the facts. Write numbers as digits, exactly as given. Do not round or convert.
- Do not predict results, give advice or add instructions beyond the facts.
Reply with the paragraph only."""


# --------------------------------------------------------------------------- the facts of one step


def _num(x: float) -> str:
    return f"{x:g}"


def step_facts(step: Any) -> str:
    """The structured text of one step: what the model is shown and what a narrative is measured against. Labels carry no digit and no unit."""
    lines = [f"step id: {step.step_id}", f"title: {step.title}", f"who does it: {_OWNER_FACT[step.automation.value]}",
             f"automation note: {step.automation_note}"]
    if step.depends_on:
        lines.append("follows: " + ", ".join(step.depends_on))
    if step.reagents:
        lines.append("reagents: " + "; ".join(step.reagents))
    if step.controls:
        lines.append("controls: " + "; ".join(step.controls))
    if step.gate:
        lines.append("gate (a condition that must hold before the next step): " + step.gate)
    if step.est_duration_days:
        lo, hi = step.est_duration_days
        lines.append(f"coarse duration estimate (not measured): {_num(lo)}-{_num(hi)} days")
    if step.quantities:
        lines.append("reagent volumes (placeholders from the robot plan): " + "; ".join(f"{q.name} {_num(q.microlitres)} uL" for q in step.quantities))
    return "\n".join(lines)


def template_narrative(step: Any) -> str:
    """The deterministic narrative: the step's own fields in plain sentences. Passes `check_narrative` by construction."""
    parts = [f"{step.title}.", _OWNER_SENTENCE[step.automation.value], step.automation_note]
    if step.depends_on:
        parts.append("It follows " + ", ".join(step.depends_on) + ".")
    if step.reagents:
        parts.append("Reagents: " + ", ".join(step.reagents) + ".")
    if step.controls:
        parts.append("Controls: " + "; ".join(step.controls) + ".")
    if step.gate:
        parts.append("Gate before the next step: " + step.gate if len(step.gate) <= GATE_INLINE_MAX
                     else "A gate must hold before the next step; its full text is in the gate field of this step.")
    if step.est_duration_days:
        lo, hi = step.est_duration_days
        parts.append(f"Coarse duration estimate: {_num(lo)}-{_num(hi)} days (not measured).")
    # The reagent volumes are not repeated here: the quantities list and its markdown section carry them, and a narrative is meant to be short.
    return " ".join(" ".join(p.split()) for p in parts)


# --------------------------------------------------------------------------- the check an LLM narrative must pass

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_WORD = re.compile(r"[a-z][a-z0-9']*")
# Unit words count anywhere; short symbols (uL, mM, s, h, x, %, C ...) count only when written right after a number.
_UNIT_WORDS = frozenset(
    "second seconds sec secs minute minutes min mins hour hours hr hrs day days week weeks month months "
    "microlitre microlitres microliter microliters millilitre millilitres milliliter milliliters litre litres liter liters "
    "microgram micrograms milligram milligrams gram grams molar millimolar micromolar nanomolar degree degrees percent celsius rpm kda".split())
_NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s?(°\s?[CF]|%|(?:[mnuµμ]?l|[mnuµμ]?g|kg|[mnuµμ]?m|eu(?:/[a-z]+)?|xg|x|rpm|s|h|min|c|k)(?![a-z]))", re.IGNORECASE)
_NUMBER_WORDS = frozenset("two three four five six seven eight nine ten eleven twelve twenty thirty forty fifty hundred thousand million dozen twice thrice".split())
_CONTROL = re.compile(r"\bcontrols?\b", re.IGNORECASE)
_SKIP = frozenset("a an the and or of each every its this these those same all both as with in for to by at on".split())


def _words(text: str) -> list[str]:
    return _WORD.findall(text.lower().replace("’", "'"))


def _numbers(text: str) -> set[float]:
    return {float(n) for n in _NUMBER.findall(text)}


# Spellings of one unit compare equal ("microlitres", "uL" and "µL" are one unit), so a faithful rewording is not rejected; a NEW unit still is.
_CANON = {
    "microlitre": "ul", "microlitres": "ul", "microliter": "ul", "microliters": "ul", "millilitre": "ml", "millilitres": "ml", "milliliter": "ml",
    "milliliters": "ml", "litre": "l", "litres": "l", "liter": "l", "liters": "l", "microgram": "ug", "micrograms": "ug", "milligram": "mg",
    "milligrams": "mg", "gram": "g", "grams": "g", "millimolar": "mm", "micromolar": "um", "nanomolar": "nm", "molar": "m",
    "second": "s", "seconds": "s", "sec": "s", "secs": "s", "minute": "min", "minutes": "min", "mins": "min", "hour": "h", "hours": "h",
    "hr": "h", "hrs": "h", "day": "days", "week": "weeks", "month": "months", "degree": "degrees", "percent": "%", "°c": "°c", "celsius": "°c",
}


def _units(text: str) -> set[str]:
    found = {w for w in _words(text) if w in _UNIT_WORDS}
    found |= {re.sub(r"\s", "", m.group(2).lower()).replace("µ", "u").replace("μ", "u") for m in _NUM_UNIT.finditer(text)}
    return {_CANON.get(u, u) for u in found}


def check_narrative(text: str, step: Any, other_controls: Any = ()) -> list[str]:
    """Why `text` may not stand as the narrative of `step` (an empty list = acceptable). Measured against `step_facts(step)` only.

    `other_controls` are the controls of the protocol's other steps: quoting one of them in this step is naming a control it does not have.
    """
    problems: list[str] = []
    clean = " ".join(text.split())
    if not clean:
        return ["it is empty"]
    if len(clean) > MAX_CHARS:
        problems.append(f"it is {len(clean)} characters, over the {MAX_CHARS} limit")
    facts = step_facts(step)
    new_numbers = sorted(_numbers(clean) - _numbers(facts))
    if new_numbers:
        problems.append("it contains a number that is not in the step: " + ", ".join(_num(n) for n in new_numbers))
    new_units = sorted(_units(clean) - _units(facts))
    if new_units:
        problems.append("it contains a unit that is not in the step: " + ", ".join(new_units))
    words, fact_words = set(_words(clean)), set(_words(facts))
    new_words = sorted((words & _NUMBER_WORDS) - fact_words)
    if new_words:
        problems.append("it spells out a number that is not in the step: " + ", ".join(new_words))
    problems += _control_problems(clean, step, facts, other_controls)
    return problems


def _control_problems(clean: str, step: Any, facts: str, other_controls: Any) -> list[str]:
    # What the step itself says about controls: its controls, and any other phrase of its facts that holds the word (S9 names some reagents
    # "... control", e.g. "assembled plasmid (no-insert control)"; a narrative may repeat those).
    own = set(_words(" ".join(step.controls)))
    for phrase in re.split(r"[;\n]", facts):
        if _CONTROL.search(phrase):
            own |= set(_words(phrase))
    out: list[str] = []
    # (1) the word "control": a step whose facts have none cannot have one, and the word that qualifies it (the one right before it, in the same
    #     clause: "negative control", "isotype control") must be one the step itself uses
    for m in _CONTROL.finditer(clean):
        if not own:
            out.append("it mentions a control, but this step has no controls")
            break
        before = _words(re.split(r"[.;:!?,()]", clean[:m.start()])[-1])
        qualifier = before[-1] if before else None
        if qualifier is not None and qualifier not in _SKIP and qualifier not in own:
            out.append(f"it names a control that is not in the step ('{qualifier} control')")
    # (2) a control of ANOTHER step, quoted whole
    spoken = " " + " ".join(_words(clean)) + " "
    in_facts = " " + " ".join(_words(facts)) + " "
    for c in sorted(set(other_controls) - set(step.controls)):
        seq = " " + " ".join(_words(c)) + " "
        if seq.strip() and seq in spoken and seq not in in_facts:
            out.append(f"it names a control of another step: {c!r}")
    return out


# --------------------------------------------------------------------------- the model

@dataclass
class NarrativeReply:
    text: str | None = None
    refused: bool = False
    refusal_kind: str | None = None
    refusal_detail: str | None = None
    error: str | None = None
    stop_reason: str | None = None


def _reply_text(resp: Any) -> str:
    """The text of an SDK message, a dict or a string (what `refusal.refusal_verdict` also accepts)."""
    if isinstance(resp, str):
        return resp
    content = resp.get("content") if isinstance(resp, dict) else getattr(resp, "content", None)
    if isinstance(content, str):
        return content
    parts = []
    for block in content or []:
        t = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(t, str):
            parts.append(t)
    return "\n".join(parts)


def default_model_id() -> str:
    from vaxforge.s9_llm import default_model_id as s9_default  # the same default (and VAXFORGE_CODEGEN_MODEL override) as S9's model

    return s9_default()


class NarrativeLLM:
    """Writes one step's narrative with a chat model. `client` is anything with `.messages.create(...)` (the Anthropic SDK shape)."""

    def __init__(self, model_id: str | None = None, client: Any | None = None, max_tokens: int = MAX_TOKENS):
        self.model_id = model_id or default_model_id()
        self.client = client
        self.max_tokens = max_tokens
        self.label = f"llm:{self.model_id}"
        self.unavailable: str | None = None  # set after the first non-refusal error: the model is not called again

    def _client(self) -> Any:
        if self.client is None:
            import anthropic  # optional dependency; ANTHROPIC_API_KEY is read by the SDK, never by us

            self.client = anthropic.Anthropic()
        return self.client

    def write(self, facts: str) -> NarrativeReply:
        if self.unavailable:
            return NarrativeReply(error=self.unavailable)
        try:
            client = self._client()
        except ImportError:
            return self._fail("the `anthropic` package is not installed; pip install anthropic, or inject a client")
        except Exception as exc:  # noqa: BLE001
            return self._fail(f"could not create the model client: {type(exc).__name__}")
        try:
            resp = client.messages.create(model=self.model_id, max_tokens=self.max_tokens, system=SYSTEM_PROMPT,
                                          messages=[{"role": "user", "content": facts}])
        except Exception as exc:  # noqa: BLE001 - provider failures are data here, never a crash
            verdict = refusal_verdict(f"{type(exc).__name__}: {exc}")
            if verdict.refused:
                return NarrativeReply(refused=True, refusal_kind=verdict.kind, refusal_detail=verdict.detail)
            return self._fail(f"{type(exc).__name__}: {' '.join(str(exc).split())[:200]}")
        stop = resp.get("stop_reason") if isinstance(resp, dict) else getattr(resp, "stop_reason", None)
        verdict = refusal_verdict(resp)
        if verdict.refused:
            return NarrativeReply(refused=True, refusal_kind=verdict.kind, refusal_detail=verdict.detail, stop_reason=stop)
        return NarrativeReply(text=_reply_text(resp), stop_reason=stop)

    def _fail(self, why: str) -> NarrativeReply:
        self.unavailable = why
        return NarrativeReply(error=why)


# --------------------------------------------------------------------------- applying it to a protocol


@dataclass
class NarrativeRun:
    """One stage run's narrative state: the model (None = template), what was already decided, the warnings and the counts."""

    llm: NarrativeLLM | None = None
    warnings: list[str] = field(default_factory=list)
    n_llm: int = 0
    n_template: int = 0
    _decided: dict[str, tuple[str, str]] = field(default_factory=dict)
    _announced: bool = False

    @property
    def backend(self) -> str:
        return "llm" if self.llm is not None else "template"

    def summary(self) -> dict[str, Any]:
        return {"backend": self.backend, "model": self.llm.model_id if self.llm is not None else None,
                "n_steps": self.n_llm + self.n_template, "n_llm": self.n_llm, "n_template": self.n_template}

    def narrate(self, step: Any, other_controls: Any) -> tuple[str, str]:
        """(text, source) for one step. Never raises: whatever goes wrong with the model, the template text is returned and a warning is kept."""
        template = template_narrative(step)
        if self.llm is None:
            self.n_template += 1
            return template, SOURCE_TEMPLATE
        facts = step_facts(step)
        key = facts + "\0" + "\1".join(sorted(other_controls))
        if key not in self._decided:  # the same facts (the same step of another candidate) are decided once
            self._decided[key] = self._ask(step, facts, template, other_controls)
        text, source = self._decided[key]
        if source == SOURCE_TEMPLATE:
            self.n_template += 1
        else:
            self.n_llm += 1
        return text, source

    def _ask(self, step: Any, facts: str, template: str, other_controls: Any) -> tuple[str, str]:
        assert self.llm is not None
        label = f"{step.step_id} ({step.title})"
        reply = self.llm.write(facts)
        if reply.refused:
            self.warnings.append(f"S8 narrative for {label}: the model refused ({reply.refusal_kind}: {reply.refusal_detail}); the template text is used")
        elif reply.error is not None:
            if not self._announced:  # one warning for the whole run: after the first error the model is not called again
                self._announced = True
                self.warnings.append(f"S8 narrative model unavailable ({reply.error}); the template text is used for this and every later step")
        elif reply.stop_reason == "max_tokens":
            self.warnings.append(f"S8 narrative for {label} rejected: the model's reply was cut off by the token limit; the template text is used")
        else:
            text = " ".join((reply.text or "").split())
            problems = check_narrative(text, step, other_controls)
            if not problems:
                return text, self.llm.label
            self.warnings.append(f"S8 narrative for {label} rejected: {'; '.join(problems)}; the template text is used")
        return template, SOURCE_TEMPLATE


def with_narratives(protocol: Any, run: NarrativeRun) -> Any:
    """A copy of `protocol` whose every step carries `narrative` and `narrative_source`. Nothing else about it changes."""
    controls = {c for s in protocol.steps for c in s.controls}
    steps = []
    for s in protocol.steps:
        text, source = run.narrate(s, controls - set(s.controls))
        steps.append(s.model_copy(update={"narrative": text, "narrative_source": source}))
    return protocol.model_copy(update={"steps": steps})


def settings_from(config: Any) -> tuple[str, str | None]:
    """`streamc.s8_backend` (template | llm; default template) and `streamc.s8_model` -> (backend, model id).

    The model id is the EFFECTIVE one for `llm` (the configured id, else S9's default) and None for `template`, so it can go into the stage's params.
    """
    backend = str(setting(config, "s8_backend", "template")).strip().lower()
    if backend not in BACKENDS:
        raise ConfigError(f"streamc.s8_backend must be one of {list(BACKENDS)}, got {backend!r}")
    model = setting(config, "s8_model")
    if model is not None and not (isinstance(model, str) and model.strip()):
        raise ConfigError(f"streamc.s8_model must be a model id (text) or null, got {model!r}")
    if backend == "template":
        return backend, None
    return backend, (model.strip() if isinstance(model, str) else default_model_id())
