"""Small scoring helpers shared by S3 (composite) and S7 (final coarse ranking)."""
from __future__ import annotations

from typing import Hashable, Mapping, Sequence


def tie_key_round(x: float | None, ndigits: int = 9) -> float | None:
    """Round a score for tie detection so float noise (1e-12) does not hide a real tie; ``None`` stays ``None``."""
    return None if x is None else round(float(x), ndigits)


def tie_groups(keys: Sequence[Hashable], start: int = 1) -> list[dict | None]:
    """Per position of an already-sorted list: ``None`` if its key is unique, else ``{"size", "competition_rank"}``.

    ``keys`` must be everything the sort used *except* the final arbitrary tie-breaker (protein_id), so a group is a
    set of candidates the evidence cannot tell apart. ``competition_rank`` is the rank of the first member
    (``start`` is the rank of the first position), so seven candidates tied for first all carry ``competition_rank`` 1.
    """
    out: list[dict | None] = [None] * len(keys)
    i = 0
    while i < len(keys):
        j = i
        while j + 1 < len(keys) and keys[j + 1] == keys[i]:
            j += 1
        if j > i:
            for t in range(i, j + 1):
                out[t] = {"size": j - i + 1, "competition_rank": start + i}
        i = j + 1
    return out


def tie_summary(groups: Sequence[dict | None]) -> dict:
    """``{"n_groups", "n_tied_candidates", "largest_group": {"size", "competition_rank"} | None}`` for a tie_groups() result."""
    seen = {g["competition_rank"]: g for g in groups if g}
    largest = max(seen.values(), key=lambda g: (g["size"], -g["competition_rank"]), default=None)
    return {"n_groups": len(seen), "n_tied_candidates": sum(g["size"] for g in seen.values()),
            "largest_group": dict(largest) if largest else None}


def groups_split_by_cut(groups: Sequence[dict | None], kept: Sequence[bool]) -> list[dict]:
    """Tie groups that a selection cut splits: some members kept, some not (``kept`` is aligned with ``groups``)."""
    acc: dict[int, dict] = {}
    for g, k in zip(groups, kept):
        if g is None:
            continue
        d = acc.setdefault(g["competition_rank"], {"size": g["size"], "competition_rank": g["competition_rank"], "kept": 0})
        d["kept"] += 1 if k else 0
    return [d for d in acc.values() if 0 < d["kept"] < d["size"]]


def composite(terms: Mapping[str, float | None], weights: Mapping[str, float]) -> float | None:
    """Weighted mean over the terms that are present *and* have a positive weight.

    Missing terms are dropped and the remaining weights renormalised (the caller records which
    terms were used). Returns ``None`` when nothing is usable.
    """
    avail = {k: w for k, w in weights.items() if w and w > 0 and terms.get(k) is not None}
    if not avail:
        return None
    return sum(w * float(terms[k]) for k, w in avail.items()) / sum(avail.values())


def percentile_ranks(values: Mapping[str, float]) -> dict[str, float]:
    """Map each value to its percentile rank in [0, 1] within ``values`` (ties share the average rank)."""
    items = sorted(values.items(), key=lambda kv: kv[1])
    n = len(items)
    if n == 0:
        return {}
    if n == 1:
        return {items[0][0]: 0.5}
    out: dict[str, float] = {}
    i = 0
    while i < n:
        j = i
        while j + 1 < n and items[j + 1][1] == items[i][1]:
            j += 1
        avg = (i + j) / 2.0
        for t in range(i, j + 1):
            out[items[t][0]] = avg / (n - 1)
        i = j + 1
    return out
