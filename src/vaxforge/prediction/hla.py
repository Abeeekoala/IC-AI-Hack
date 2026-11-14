"""HLA allele handling: name normalisation, tool name conversion, reference panels.

Canonical forms (2-field resolution, matching the IEDB Population Coverage data):
  class I   ``HLA-A*02:01``
  class II  ``HLA-DRB1*01:01``;  heterodimers ``HLA-DQA1*05:01/DQB1*02:01``
"""
from __future__ import annotations

import re

_CLASS_I_LOCI = {"A", "B", "C", "E", "F", "G"}


def _fields(text: str) -> tuple[str, str]:
    """Split '0201' / '02:01' / '15:101' into two fields."""
    if ":" in text:
        a, b = text.split(":")[:2]
        return a.zfill(2), b.zfill(2)
    if len(text) == 4:
        return text[:2], text[2:]
    if len(text) == 5:
        return text[:2], text[2:]
    if len(text) == 6:           # three-digit first field, e.g. 'B*15101' (rare); treat as 3+3? ambiguous
        raise ValueError(f"ambiguous allele digits {text!r}; use a colon (e.g. 15:101)")
    raise ValueError(f"cannot parse allele digits {text!r}")


_PAIR = re.compile(r"^(?:HLA-)?(D[PQ])A1\*?([\d:]+)[-/](?:HLA-)?D[PQ]B1\*?([\d:]+)$", re.I)
# single-chain class II names: DRB1/3/4/5 (tool form 'DRB1_0101') and the DQ/DP chains that
# appear individually in allele-frequency tables (e.g. 'HLA-DQB1*02:01').
_DRB = re.compile(r"^(?:HLA-)?(DRB[1345]|DRA|DQA1|DQB1|DPA1|DPB1|DMA|DMB)[_*]?([\d:]+)$", re.I)
_CLS1 = re.compile(r"^(?:HLA-)?([ABCEFG])\*?([\d:]+)$", re.I)


def normalise_allele(name: str) -> str:
    """Accept tool/IEDB/user spellings and return the canonical 2-field form (raises ValueError)."""
    s = name.strip().replace(" ", "")
    m = _PAIR.match(s)
    if m:
        locus = m.group(1).upper()
        a1, a2 = _fields(m.group(2))
        b1, b2 = _fields(m.group(3))
        return f"HLA-{locus}A1*{a1}:{a2}/{locus}B1*{b1}:{b2}"
    m = _DRB.match(s)
    if m:
        f1, f2 = _fields(m.group(2))
        return f"HLA-{m.group(1).upper()}*{f1}:{f2}"
    m = _CLS1.match(s)
    if m:
        digits = m.group(2)
        # allow 3+ field names like A*02:01:01:01 -> keep first two fields
        f1, f2 = _fields(":".join(digits.split(":")[:2]) if ":" in digits else digits)
        return f"HLA-{m.group(1).upper()}*{f1}:{f2}"
    raise ValueError(f"unrecognised HLA allele name: {name!r}")


def is_pair(allele: str) -> bool:
    return "/" in allele


def allele_class(allele: str) -> str:
    """'I' or 'II' for a canonical allele."""
    if is_pair(allele):
        return "II"
    locus = allele.split("*")[0].replace("HLA-", "")
    return "I" if locus in _CLASS_I_LOCI else "II"


def allele_locus(allele: str) -> str:
    """Locus label used for frequency lookup. Heterodimers map to the beta-chain locus."""
    if is_pair(allele):
        beta = allele.split("/")[1]                      # 'DQB1*02:01'
        return "HLA-" + beta.split("*")[0]
    return allele.split("*")[0] if allele.startswith("HLA-") else "HLA-" + allele.split("*")[0]


def beta_chain_allele(allele: str) -> str | None:
    """'HLA-DQA1*05:01/DQB1*02:01' -> 'HLA-DQB1*02:01'; None for non-pairs."""
    return ("HLA-" + allele.split("/")[1]) if is_pair(allele) else None


def to_netmhcpan(allele: str) -> str:
    """'HLA-A*02:01' -> 'HLA-A02:01' (the spelling netMHCpan's -a option takes)."""
    if allele_class(allele) != "I":
        raise ValueError(f"{allele} is not a class I allele")
    return allele.replace("*", "")


def to_netmhciipan(allele: str) -> str:
    """'HLA-DRB1*01:01' -> 'DRB1_0101'; 'HLA-DQA1*05:01/DQB1*02:01' -> 'HLA-DQA10501-DQB10201'."""
    if allele_class(allele) != "II":
        raise ValueError(f"{allele} is not a class II allele")
    if is_pair(allele):
        a, b = allele[4:].split("/")                     # 'DQA1*05:01', 'DQB1*02:01'
        fmt = lambda x: x.replace("*", "").replace(":", "")
        return f"HLA-{fmt(a)}-{fmt(b)}"
    gene, rest = allele[4:].split("*")
    return f"{gene}_{rest.replace(':', '')}"


# --------------------------------------------------------------------------------------
# Reference panels.
# NOTE: reproduced from memory of the published IEDB reference panels (class I: Weiskopf
# et al. 2013, 27 alleles; class II: Greenbaum et al. 2011, 26 alleles). Verify against the
# primary sources before quoting the panels in a paper or slide. Both are configurable.
# --------------------------------------------------------------------------------------
REFERENCE_PANEL_CLASS_I = [
    "HLA-A*01:01", "HLA-A*02:01", "HLA-A*02:03", "HLA-A*02:06", "HLA-A*03:01", "HLA-A*11:01",
    "HLA-A*23:01", "HLA-A*24:02", "HLA-A*26:01", "HLA-A*30:01", "HLA-A*30:02", "HLA-A*31:01",
    "HLA-A*32:01", "HLA-A*33:01", "HLA-A*68:01", "HLA-A*68:02",
    "HLA-B*07:02", "HLA-B*08:01", "HLA-B*15:01", "HLA-B*35:01", "HLA-B*40:01", "HLA-B*44:02",
    "HLA-B*44:03", "HLA-B*51:01", "HLA-B*53:01", "HLA-B*57:01", "HLA-B*58:01",
]

REFERENCE_PANEL_CLASS_II = [
    "HLA-DRB1*01:01", "HLA-DRB1*03:01", "HLA-DRB1*04:01", "HLA-DRB1*04:05", "HLA-DRB1*07:01",
    "HLA-DRB1*08:02", "HLA-DRB1*09:01", "HLA-DRB1*11:01", "HLA-DRB1*12:01", "HLA-DRB1*13:02",
    "HLA-DRB1*15:01", "HLA-DRB3*01:01", "HLA-DRB3*02:02", "HLA-DRB4*01:01", "HLA-DRB5*01:01",
    "HLA-DQA1*01:01/DQB1*05:01", "HLA-DQA1*01:02/DQB1*06:02", "HLA-DQA1*03:01/DQB1*03:02",
    "HLA-DQA1*04:01/DQB1*04:02", "HLA-DQA1*05:01/DQB1*02:01", "HLA-DQA1*05:01/DQB1*03:01",
    "HLA-DPA1*01:03/DPB1*02:01", "HLA-DPA1*01:03/DPB1*04:01", "HLA-DPA1*02:01/DPB1*01:01",
    "HLA-DPA1*02:01/DPB1*05:01", "HLA-DPA1*03:01/DPB1*04:02",
]

PANELS = {"reference27": REFERENCE_PANEL_CLASS_I, "reference26": REFERENCE_PANEL_CLASS_II}


def resolve_panel(spec: str | list[str], expected_class: str) -> list[str]:
    """Panel name ('reference27' / 'reference26') or an explicit allele list -> canonical alleles."""
    names = PANELS[spec] if isinstance(spec, str) and spec in PANELS else spec
    if isinstance(names, str):
        raise ValueError(f"unknown panel {names!r}; use {sorted(PANELS)} or a list of alleles")
    out = []
    for n in names:
        a = normalise_allele(n)
        if allele_class(a) != expected_class:
            raise ValueError(f"{a} is class {allele_class(a)}, expected class {expected_class}")
        out.append(a)
    return list(dict.fromkeys(out))
