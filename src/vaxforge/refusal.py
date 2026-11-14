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
  5. refusal wording in the first 800 characters of a text with no code  -> refused, kind "text_pattern".
  6. otherwise NOT a refusal, kind "no_code": the reply holds no protocol, which is a failed attempt, not a refusal.

`text` may also be an SDK message, a dict or an exception message (as `detect_refusal(response)` always took): its text is read
from `.content` / ["content"], and its `stop_reason` counts as a finish reason next to an explicit `finish_reason`. Either one
being a refusal decides step 1, so a provider refusal is never masked by the other argument.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

__all__ = ["REFUSAL_FINISH_REASONS", "RefusalVerdict", "detect_refusal", "extract_python", "refusal_verdict"]

# Provider end-of-turn labels that mean "the provider refused": Anthropic's stop_reason "refusal", and the "content_filter"
# finish_reason of OpenAI-style APIs. Compared case-insensitively.
REFUSAL_FINISH_REASONS = ("refusal", "content_filter")

_HEAD_CHARS = 800  # refusal wording is looked for in this many leading characters of a reply that has no code


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
    for pat in _REFUSAL_PATTERNS:
        m = pat.search(head)
        if m:
            return RefusalVerdict(True, "text_pattern", m.group(0))
    return RefusalVerdict(False, "no_code", "no protocol code in the reply and no refusal wording")


def detect_refusal(text: Any = None, code: str | None = None, finish_reason: str | None = None) -> bool:
    """True when the model REFUSED (as opposed to failing to produce valid code). Arguments: see `refusal_verdict`."""
    return refusal_verdict(text, code, finish_reason).refused
