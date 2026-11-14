"""Small scoring helpers shared by S3 (composite) and S7 (final coarse ranking)."""
from __future__ import annotations

from typing import Mapping


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
