"""Refusal detection and protocol extraction for model replies. Standard library only: this ONE file can be copied into
another repository unchanged (tests/wetlab/test_refusal.py checks its imports with `ast`).

    detect_refusal(text=None, code=None, finish_reason=None) -> bool        # refusal_verdict(...) also says why

A refusal is a distinct outcome, never a crash and never something to engineer around (README S9, "Expect safety
refusals"). It is not the same as failing to write valid code. The checks run in this order and the first one that applies decides:

  1. finish_reason is in REFUSAL_FINISH_REASONS (case-insensitive)  -> refused, kind "api_stop_reason". Even with an EMPTY
     body and even when code is present: a provider-side refusal can carry no text, so the text rules cannot see it.
  2. `code` (the protocol the caller extracted from the reply) is not blank  -> NOT a refusal.
  3. the text holds a harness or provider error marker ("AgentSafetyRefusalError", ...)  -> refused, kind "harness_error".
  4. the text contains protocol code  -> NOT a refusal, even if it hedges ("I can't vouch for the volumes, but here it is").
  5. refusal wording in a text with no code (the wording rules are below)  -> refused, kind "text_pattern".
  6. otherwise NOT a refusal, kind "no_code": the reply holds no protocol, which is a failed attempt, not a refusal. That
     includes a reply cut off by the token limit (finish_reason "length" or "max_tokens"): the only finish reasons ever read are
     the two refusal labels of step 1, so an empty or truncated reply with any other label is a failed attempt too.

`text` may also be an SDK message, a dict or an exception message (as `detect_refusal(response)` always took): its text is read
from `.content` / ["content"], and its `stop_reason` counts as a finish reason next to an explicit `finish_reason`. Either one
being a refusal decides step 1, so a provider refusal is never masked by the other argument.

The wording rules (step 5) apply only to a reply with no code. Either of these is enough:

  a. A first-person refusal that STARTS within the first 300 characters: "I" (also "I'm", "I am"), then a negation or a refusal
     verb, then what is refused.
       negation or refusal verb   can't, cannot, won't, will not (also "won't be able to"); am unable to, am not able to, am not
                                  going to, am not willing to, am not allowed to; refuse to, decline to. "I decline", "I must
                                  decline" and "I have to decline" need nothing after them.
       what is refused            a verb that names the deliverable (help, assist, provide, write, generate, create, support,
                                  comply, fulfil), whatever follows it: "I can't help with that.", "I refuse to write this
                                  protocol."; or give, share or produce with a protocol, instructions, code, steps or details
                                  (or this / that / it) after it: "As an AI I am unable to give lab protocols"; or a generic verb
                                  (do, continue, proceed, go on) ONLY when the clause ends right there: "I can't do that.",
                                  "I can't continue", "I'm not going to do this."
     What comes before is ignored ("Sorry, ", "I'm sorry, but ", "Unfortunately, ", "As an AI language model, "), and so are
     capitals and the style of apostrophe (straight or curly); a word such as "really" or "just" may stand before the negation.
     A generic verb followed by more words gives a reason and carries on, so "I can't do it in one step, so I split the
     transfer into two:" is NOT a refusal. Neither is a statement about someone or something else ("You can't aspirate more
     than the tip holds, ..."), a question, a plan, or an empty reply.
  b. The original, narrower phrasings anywhere in the first 800 characters: I can't / cannot / won't / will not / am unable to /
     am not able to + help, assist, provide, write, generate, create, fulfil, comply or support; unable / not able to + help,
     assist, provide, comply or fulfil; "I must decline"; "against my / our guidelines / policy"; "can't help with this / that".

Known limit: wording detection is a heuristic. A refusal in another language, or in a phrasing the rules above do not list ("That
is not something I am able to take on", markup inside the phrase such as "I **cannot** help", a long preamble before it) is
missed, and the reply then counts as a failed attempt (kind "no_code"). The provider's finish reason (step 1) is the reliable
signal: pass it whenever the API gives one.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["REFUSAL_FINISH_REASONS", "RefusalVerdict", "detect_refusal", "extract_python", "refusal_verdict"]

# Provider end-of-turn labels that mean "the provider refused": Anthropic's stop_reason "refusal", and the "content_filter"
# finish_reason of OpenAI-style APIs. Compared case-insensitively.
REFUSAL_FINISH_REASONS = ("refusal", "content_filter")

_HEAD_CHARS = 800  # the original phrasings (rule b) are looked for in this many leading characters of a reply that has no code
_FIRST_PERSON_CHARS = 300  # a first-person refusal (rule a) has to START within this many leading characters


@dataclass(frozen=True)
class RefusalVerdict:
    refused: bool
    kind: str | None  # api_stop_reason | harness_error | text_pattern | no_code | None
    detail: str = ""


_FENCE_RE = re.compile(r"```(?:python|py)?[ \t]*\r?\n(.*?)```", re.DOTALL | re.IGNORECASE)
_REFUSAL_PATTERNS = [
    re.compile(r"\bI\s+(?:can(?:'|’)?t|cannot|can not|won(?:'|’)?t|will not|am unable to|(?:'|’)m unable to|am not able to|(?:'|’)m not able to)\s+"
               r"(?:help|assist|provide|write|generate|create|fulfil+|comply|support)", re.IGNORECASE),
    re.compile(r"\b(?:unable|not able) to (?:help|assist|provide|comply|fulfil+)", re.IGNORECASE),
    re.compile(r"\bI\s+must\s+decline\b", re.IGNORECASE),
    re.compile(r"\bagainst\s+(?:my|our)\s+(?:guidelines|polic(?:y|ies)|usage polic(?:y|ies))\b", re.IGNORECASE),
    re.compile(r"\bcan(?:'|’)?t help with (?:that|this)\b", re.IGNORECASE),
]
# Rule (a): "I" + a negation or a refusal verb + what is refused. Built from named parts so each one can be read on its own.
_APOS = "['" + chr(0x2019) + chr(0x2BC) + "]"  # a character class: straight, curly (U+2019) or modifier-letter (U+02BC) apostrophe
_ADV = r"(?:\s+(?:really|simply|just|truly|genuinely|honestly|respectfully|politely|unfortunately|sadly))?"
_NEGATION = (
    rf"(?:{_ADV}\s+(?:can{_APOS}?t|cannot|can\s+not|won{_APOS}?t|will\s+not)"  # I can't / cannot / won't / will not
    rf"|(?:{_APOS}?m|\s+am){_ADV}\s+(?:unable|not\s+able|not\s+going|not\s+willing|not\s+allowed|not\s+permitted)\s+to"  # I'm unable to
    rf"|{_ADV}\s+(?:refuse|decline)\s+to)"  # I refuse to / decline to
    r"(?:\s+be\s+able\s+to)?"  # I won't be able to
)
# What is refused. A verb that names the deliverable counts whatever follows it ...
_DELIVERS = r"(?:help|assist|provide|write|generate|create|fulfil+|comply|support)\b"
# ... give / share / produce only with a deliverable (or this / that / it) within a few words ("give lab protocols") ...
_HANDS_OVER = (rf"(?:give|share|produce)\b(?:\s+(?:[\w-]|{_APOS})+){{0,4}}?\s+"
               r"(?:protocols?|instructions?|code|scripts?|steps?|procedures?|methods?|recipes?|guidance|directions|details|this|that|it)\b")
# ... and a generic verb only when the clause ends right after it ("I can't do that." / "I can't continue"). More words carry on
# with a reason ("I can't do it in one step ..."), and so does ", so" ("I can't do that, so I split the transfer ...").
_CLAUSE_END = r"(?=[ \t]*(?:[^\w \t]|$)|[ \t]+for[ \t]+you\b)(?!,[ \t]+so\b)"
_GENERIC = (r"(?:do|continue|proceed|go\s+(?:on|ahead)|carry\s+on)"
            r"(?:\s+(?:with\s+)?(?:this|that|it|so|(?:this|that|the|your)\s+(?:request|task|protocol|conversation|prompt)))?"
            + _CLAUSE_END)
_FIRST_PERSON_PATTERNS = [
    re.compile(rf"\bI{_NEGATION}\s+(?:{_DELIVERS}|{_HANDS_OVER}|{_GENERIC})", re.IGNORECASE),
    # "I decline", "I must / have to / will / 'll have to decline": nothing has to follow it.
    re.compile(rf"\bI(?:\s+(?:will|must|should|have\s+to|need\s+to)|{_APOS}ll|(?:{_APOS}m|\s+am)\s+going\s+to)?(?:\s+have\s+to)?"
               rf"{_ADV}\s+decline\b", re.IGNORECASE),
]
_HARNESS_MARKERS = ("agentsafetyrefusalerror", "safety refusal", "refused by the provider", "content policy violation")


def extract_python(text: str) -> str | None:
    """The protocol source from a model reply: first fenced block that defines `run`, else a bare file."""
    blocks = _FENCE_RE.findall(text or "")
    for b in blocks:
        if re.search(r"def\s+run\s*\(", b):
            return b.strip() + "\n"
    if blocks:
        return blocks[0].strip() + "\n"
    if re.search(r"^\s*def\s+run\s*\(", text or "", re.MULTILINE) and "opentrons" in (text or ""):
        return text.strip() + "\n"
    return None


def _text_of(x: Any) -> str:
    if x is None:
        return ""
    if isinstance(x, str):
        return x
    content = x.get("content") if isinstance(x, dict) else getattr(x, "content", None)
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for block in content or []:
        t = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if isinstance(t, str):
            parts.append(t)
    return "\n".join(parts)


def _stop_reason(x: Any) -> str | None:
    if isinstance(x, dict):
        return x.get("stop_reason")
    return getattr(x, "stop_reason", None)


def _refusal_finish_reason(value: Any) -> str | None:
    """`value` as the provider wrote it (surrounding blanks dropped) when it is one of REFUSAL_FINISH_REASONS, else None."""
    if isinstance(value, str) and value.strip().lower() in REFUSAL_FINISH_REASONS:
        return value.strip()
    return None


def refusal_verdict(text: Any = None, code: str | None = None, finish_reason: str | None = None) -> RefusalVerdict:
    """Classify one model turn; the module docstring gives the order of the checks.

    `text`: the reply text, or an SDK message, a dict or an exception message. `code`: the protocol source the caller
    extracted from the reply (blank counts as none). `finish_reason`: the provider's own end-of-turn label.
    """
    for label, value in (("finish_reason", finish_reason), ("stop_reason", _stop_reason(text))):
        hit = _refusal_finish_reason(value)
        if hit is not None:
            return RefusalVerdict(True, "api_stop_reason", f"the provider ended the turn with {label}={hit!r}")
    if isinstance(code, str) and code.strip():
        return RefusalVerdict(False, None, "")
    body = _text_of(text)
    low = body.lower()
    for marker in _HARNESS_MARKERS:
        if marker in low:
            return RefusalVerdict(True, "harness_error", marker)
    if extract_python(body) is not None:
        return RefusalVerdict(False, None, "")
    head = body[:_HEAD_CHARS]
    for pat in _REFUSAL_PATTERNS:  # rule (b)
        m = pat.search(head)
        if m:
            return RefusalVerdict(True, "text_pattern", m.group(0))
    for pat in _FIRST_PERSON_PATTERNS:  # rule (a): searched in the whole text, so no clause end is an artefact of a cut
        m = pat.search(body)
        if m and m.start() < _FIRST_PERSON_CHARS:
            return RefusalVerdict(True, "text_pattern", m.group(0))
    return RefusalVerdict(False, "no_code", "no protocol code in the reply and no refusal wording")


def detect_refusal(text: Any = None, code: str | None = None, finish_reason: str | None = None) -> bool:
    """True when the model REFUSED (as opposed to failing to produce valid code). Arguments: see `refusal_verdict`."""
    return refusal_verdict(text, code, finish_reason).refused
