"""Population coverage of an epitope set (S6).

Our implementation of the IEDB Population Coverage method, written after reading the official
tool's source (``IEDB_Population_Coverage-3.0.2``, ``population_calculation.py``) and cross-checked
against a line-by-line port of its algorithm in the tests. Verified from that source:

  * Per locus an individual's two alleles are drawn i.i.d. from the allele frequencies (random mating).
  * **Loci are treated as independent**: per-locus hit distributions are convolved (``merge_loci``).
    (The IEDB *documentation* is silent on this; the code does it. HLA loci are in linkage
    disequilibrium, so this is an approximation shared with the IEDB tool.)
  * "Hits" = number of epitope/HLA combinations: a heterozygote's hits are the SUM over its two
    alleles, a homozygote's hits are counted ONCE (``total_hit = hit1`` when ``i == j``).
  * Frequencies summing to > 1 are renormalised; < 1 adds an ``UNKNOWN`` allele with no hits.
  * ``coverage`` = P(hits >= 1); ``average_hit`` = mean hits; ``pc90`` is a *linearly interpolated*
    real number (``calculate_pc90``), e.g. 0.18 when coverage is below 90 %. We report that value as
    ``pc90`` and the integer definition (largest n with >= 90 % of people having >= n hits) as ``pc90_integer``.
  * The tool matches allele names as exact strings; DQ/DP heterodimers are not understood. We look a
    heterodimer up by its exact name and otherwise fall back to its beta chain (``approximation``).
  * The IEDB data set has class I loci A, B, C, E, G and class II loci DMA, DMB, DPA1, DPB1, DQA1,
    DQB1, DRB1 - **no DRB3/DRB4/DRB5**, so those panel alleles are reported as unmapped.

Presentable != recognised != sufficient response != clinical protection. This module says nothing
about dose, adjuvant, durability or safety.
"""
from __future__ import annotations

import csv
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Mapping

from .errors import ContractError
from .hla import allele_class, allele_locus, beta_chain_allele, normalise_allele

Mode = str  # 'class_i' | 'class_ii' | 'combined'


@dataclass
class FrequencyTable:
    populations: list[str]
    # class ('I'|'II') -> locus -> allele -> {population: frequency}
    data: dict[str, dict[str, dict[str, dict[str, float]]]]
    sample_sizes: dict[str, int | None] = field(default_factory=dict)
    skipped_rows: int = 0
    _index: dict[str, tuple[str, str]] = field(default_factory=dict, repr=False)   # allele -> (class, locus)

    @classmethod
    def from_tsv(cls, path: str | Path, sample_sizes: str | Path | None = None) -> "FrequencyTable":
        p = Path(path)
        if not p.exists():
            raise ContractError(f"allele frequency table not found: {p}")
        with p.open(newline="") as fh:
            rows = list(csv.reader(fh, delimiter="\t"))
        if not rows:
            raise ContractError(f"{p.name}: empty")
        head = [h.strip() for h in rows[0]]
        low = [h.lower().replace(" ", "") for h in head[:3]]
        if low != ["mhcclass", "mhclocus", "mhcallele"]:
            raise ContractError(f"{p.name}: first three columns must be 'MHC class', 'MHC locus', 'MHC allele' "
                                f"(IEDB user-population format); got {head[:3]}")
        pops = head[3:]
        data: dict[str, dict[str, dict[str, dict[str, float]]]] = {"I": {}, "II": {}}
        skipped = 0
        for r in rows[1:]:
            if len(r) < 4 or not r[0].strip():
                continue
            try:
                allele = normalise_allele(r[2])
            except ValueError:
                skipped += 1
                continue
            cls_key = "I" if r[0].strip().upper() == "I" else "II"
            locus = r[1].strip() or allele_locus(allele)
            freqs = {}
            for pop, v in zip(pops, r[3:]):
                v = v.strip()
                if v not in ("", "-", "NA", "nan"):
                    freqs[pop] = float(v)
            data[cls_key].setdefault(locus, {}).setdefault(allele, {}).update(freqs)
        table = cls(pops, data, _load_sample_sizes(sample_sizes, pops), skipped)
        table._index = {a: (c, l) for c, loci in data.items() for l, als in loci.items() for a in als}
        return table

    def locus_freqs(self, population: str, classes: Iterable[str]) -> dict[str, dict[str, float]]:
        out: dict[str, dict[str, float]] = {}
        for c in classes:
            for locus, als in self.data.get(c, {}).items():
                fs = {a: m[population] for a, m in als.items() if population in m}
                if fs:
                    out[f"{c}:{locus}"] = fs
        return out

    def map_allele(self, allele: str) -> tuple[str, str, str] | None:
        """-> (class, locus, table_allele) or None. Falls back to the beta chain for heterodimers."""
        if allele in self._index:
            c, l = self._index[allele]
            return c, l, allele
        beta = beta_chain_allele(allele)
        if beta and beta in self._index:
            c, l = self._index[beta]
            return c, l, beta
        return None


def _load_sample_sizes(path: str | Path | None, pops: list[str]) -> dict[str, int | None]:
    sizes: dict[str, int | None] = {p: None for p in pops}     # unknown stays None; never invented
    if not path:
        return sizes
    p = Path(path)
    if p.suffix == ".json":
        raw = json.loads(p.read_text())
    else:
        raw = {}
        for line in p.read_text().splitlines():
            parts = [x.strip() for x in line.split("\t")]
            if len(parts) >= 2 and parts[1].isdigit():
                raw[parts[0]] = int(parts[1])
    for k, v in raw.items():
        if k in sizes:
            sizes[k] = int(v)
    return sizes


@dataclass
class CoverageResult:
    population: str
    mode: str
    coverage: float
    average_hits: float
    pc90: float                     # IEDB-style interpolated value
    pc90_integer: int               # largest n with >= 90% of people having >= n hits
    min_locus_frequency_mass: float | None   # weakest sum of allele frequencies over loci with hits (low = sparse source data)
    n_epitopes: int
    n_epitopes_covered_by_table: int
    unmapped_alleles: list[str]
    approximations: list[str]
    sample_size: int | None
    hit_distribution: list[float] = field(default_factory=list, repr=False)   # P(H == k)

    def brief(self) -> dict:
        return {"population": self.population, "mode": self.mode, "coverage": round(self.coverage, 6),
                "average_hits": round(self.average_hits, 6), "pc90": round(self.pc90, 6), "pc90_integer": self.pc90_integer,
                "min_locus_frequency_mass": None if self.min_locus_frequency_mass is None else round(self.min_locus_frequency_mass, 4),
                "n_epitopes": self.n_epitopes,
                "n_epitopes_covered_by_table": self.n_epitopes_covered_by_table,
                "unmapped_alleles": self.unmapped_alleles, "approximations": self.approximations,
                "sample_size": self.sample_size}


def _classes(mode: Mode) -> tuple[str, ...]:
    return {"class_i": ("I",), "class_ii": ("II",), "combined": ("I", "II")}[mode]


def _convolve(a: list[float], b: list[float]) -> list[float]:
    out = [0.0] * (len(a) + len(b) - 1)
    for i, x in enumerate(a):
        if x:
            for j, y in enumerate(b):
                out[i + j] += x * y
    return out


def locus_hit_pmf(freqs: Mapping[str, float], hits: Mapping[str, int]) -> list[float]:
    """Distribution of the number of epitope/HLA hits an individual has at one locus.

    Mirrors IEDB ``compute_frequency``: ordered allele pairs (i, j) occur with probability f_i*f_j;
    heterozygous pairs contribute ``hit_i + hit_j``, homozygous pairs contribute ``hit_i`` once.
    Alleles with no hits (and the ``UNKNOWN`` remainder) are pooled, which does not change the result.
    """
    total = sum(freqs.values())
    scale = 1.0 / total if total > 1.0 else 1.0                      # IEDB: renormalise when the sum exceeds 1
    hit_alleles = [(a, f * scale, hits[a]) for a, f in freqs.items() if hits.get(a, 0) > 0]
    f0 = max(0.0, 1.0 - sum(f for _, f, _ in hit_alleles))            # zero-hit alleles + UNKNOWN mass
    size = (max((h for _, _, h in hit_alleles), default=0) * 2) + 1
    pmf = [0.0] * size
    pmf[0] += f0 * f0
    for a, f, h in hit_alleles:
        pmf[h] += 2.0 * f * f0 + f * f                                # (a, zero-hit) both orders + homozygous
        for b, g, k in hit_alleles:
            if b != a:
                pmf[h + k] += f * g                                   # ordered heterozygous pair
    return pmf


def pc90_iedb(pmf: list[float]) -> float:
    """IEDB ``calculate_pc90``: linear interpolation of the hit count at which P(H >= n) crosses 90 %."""
    n = len(pmf)
    if n < 2:
        return 0.0
    data = [0.0] * n                                                   # data[i] = 100 * P(H >= i), accumulated from the top
    run = 0.0
    for i in range(n - 1, -1, -1):
        run += pmf[i] * 100.0
        data[i] = run
    index1 = 0
    for i in range(n - 2, -1, -1):
        if data[i] > 90:
            index1 = i
            break
    index2 = min(index1 + 1, n - 1)
    cur = data[index2]
    for i in range(index2, n):
        if data[i] == cur:
            index2 = i
        if data[i] < cur:
            break
    rng = data[index1] - data[index2]
    if rng == 0:
        return float(index1)
    return index2 - ((90 - data[index2]) / rng) * (index2 - index1)


def _hits_by_allele(epitope_alleles: Mapping[str, Iterable[str]], table: FrequencyTable, mode: Mode):
    """-> (hits[(class,locus,allele)] -> n epitopes, per_epitope_keys, unmapped, approximations)."""
    classes = set(_classes(mode))
    hits: dict[tuple[str, str, str], int] = {}
    per_epitope: dict[str, set[tuple[str, str, str]]] = {}
    unmapped: set[str] = set()
    approx: set[str] = set()
    for epi, alleles in epitope_alleles.items():
        keys: set[tuple[str, str, str]] = set()
        for a in alleles:
            a = normalise_allele(a)
            if allele_class(a) not in classes:
                continue
            m = table.map_allele(a)
            if m is None:
                unmapped.add(a)
                continue
            if m[2] != a:
                approx.add("beta_chain_frequency_for_heterodimer")
            keys.add(m)
        per_epitope[epi] = keys
        for k in keys:
            hits[k] = hits.get(k, 0) + 1
    return hits, per_epitope, sorted(unmapped), sorted(approx)


def coverage_stats(epitope_alleles: Mapping[str, Iterable[str]], table: FrequencyTable, population: str,
                   mode: Mode = "combined") -> CoverageResult:
    """Coverage, average hits and PC90 of an epitope set in one population."""
    hits, per_epitope, unmapped, approx = _hits_by_allele(epitope_alleles, table, mode)
    locus_freqs = table.locus_freqs(population, _classes(mode))
    pmf = [1.0]
    masses: list[float] = []
    for locus_key, freqs in locus_freqs.items():
        c, l = locus_key.split(":", 1)
        lh = {a: hits.get((c, l, a), 0) for a in freqs}
        if not any(lh.values()):
            continue                                                  # locus contributes no hits
        masses.append(min(1.0, sum(freqs.values())))
        pmf = _convolve(pmf, locus_hit_pmf(freqs, lh))
    coverage = max(0.0, min(1.0, 1.0 - pmf[0]))
    avg = sum(k * p for k, p in enumerate(pmf))
    pc90_int, run = 0, 0.0
    for n in range(len(pmf) - 1, 0, -1):                              # run = P(H >= n), growing as n falls
        run += pmf[n]
        if run >= 0.90:
            pc90_int = n
            break
    return CoverageResult(
        population=population, mode=mode, coverage=coverage, average_hits=avg, pc90=pc90_iedb(pmf), pc90_integer=pc90_int,
        min_locus_frequency_mass=min(masses) if masses else None,
        n_epitopes=len(per_epitope), n_epitopes_covered_by_table=sum(1 for k in per_epitope.values() if k),
        unmapped_alleles=unmapped, approximations=approx, sample_size=table.sample_sizes.get(population),
        hit_distribution=pmf)


def _coverage_from_union(union: set[tuple[str, str, str]], locus_freqs: dict[str, dict[str, float]]) -> float:
    miss = 1.0
    for locus_key, freqs in locus_freqs.items():
        c, l = locus_key.split(":", 1)
        s = sum(f for a, f in freqs.items() if (c, l, a) in union)
        miss *= (1.0 - min(1.0, s)) ** 2
    return 1.0 - miss


def greedy_select(epitope_alleles: Mapping[str, Iterable[str]], table: FrequencyTable, population: str,
                  k: int, mode: Mode = "combined") -> list[str]:
    """Greedily pick up to ``k`` epitopes maximising coverage in ``population``.

    Ties on marginal coverage break by marginal expected hits, then by epitope id, so the
    result is deterministic.
    """
    _, per_epitope, _, _ = _hits_by_allele(epitope_alleles, table, mode)
    lf = {}
    for lk, fs in table.locus_freqs(population, _classes(mode)).items():
        total = sum(fs.values())
        lf[lk] = {a: f / total for a, f in fs.items()} if total > 1.0 else fs      # same renormalisation as coverage_stats
    freq = {(lk.split(":", 1)[0], lk.split(":", 1)[1], a): f for lk, fs in lf.items() for a, f in fs.items()}
    chosen: list[str] = []
    union: set[tuple[str, str, str]] = set()
    cur = 0.0
    remaining = dict(per_epitope)
    while remaining and len(chosen) < k:
        best_id, best_gain, best_hits = None, 0.0, -1.0
        for epi in sorted(remaining):
            keys = {x for x in remaining[epi] if x in freq}
            if not keys:
                continue
            gain = _coverage_from_union(union | keys, lf) - cur
            hits_gain = sum(2.0 * freq[x] for x in keys)
            if gain > best_gain + 1e-15 or (abs(gain - best_gain) <= 1e-15 and hits_gain > best_hits + 1e-15 and gain > 0):
                best_id, best_gain, best_hits = epi, gain, hits_gain
        if best_id is None or best_gain <= 1e-15:
            break
        chosen.append(best_id)
        union |= {x for x in remaining.pop(best_id) if x in freq}
        cur += best_gain
    return chosen


def fairness_report(epitope_alleles: Mapping[str, Iterable[str]], table: FrequencyTable, *,
                    reference_population: str, populations: Iterable[str], k: int,
                    mode: Mode = "combined") -> dict:
    """How much coverage is lost elsewhere if the epitope budget is optimised on one population only."""
    if reference_population not in table.populations:
        return {"available": False, "reason": f"reference population {reference_population!r} not in frequency table"}
    ref_set = greedy_select(epitope_alleles, table, reference_population, k, mode)
    ref_sub = {e: epitope_alleles[e] for e in ref_set}
    rows = []
    for pop in populations:
        if pop not in table.populations:
            continue
        cov_ref = coverage_stats(ref_sub, table, pop, mode).coverage
        own_set = greedy_select(epitope_alleles, table, pop, k, mode)
        # the reference set is itself a feasible answer to this population's problem, so the optimum is >= it
        cov_own = max(cov_ref, coverage_stats({e: epitope_alleles[e] for e in own_set}, table, pop, mode).coverage)
        rows.append({"population": pop, "coverage_with_reference_optimised_set": round(cov_ref, 6),
                     "coverage_with_own_optimised_set": round(cov_own, 6),
                     "coverage_lost": round(cov_own - cov_ref, 6), "sample_size": table.sample_sizes.get(pop)})
    others = [r for r in rows if r["population"] != reference_population]
    return {
        "available": True, "reference_population": reference_population, "budget_k": k, "mode": mode,
        "reference_set": ref_set, "rows": rows,
        "mean_coverage_lost_excluding_reference": round(sum(r["coverage_lost"] for r in others) / len(others), 6) if others else None,
        "max_coverage_lost": max(others, key=lambda r: r["coverage_lost"]) if others else None,
        "note": ("coverage_lost = coverage achievable with a set optimised for that population (best of greedy and the reference set) "
                 "minus coverage of the set optimised only on the reference population, same epitope budget. Greedy selection, so "
                 "this is a LOWER BOUND on the true loss. With a large epitope pool the gap closes as the budget grows (see fairness_by_budget)."),
    }


def export_iedb_epitope_allele_file(epitope_alleles: Mapping[str, Iterable[str]], path: str | Path,
                                    table: "FrequencyTable | None" = None) -> Path:
    """Write the IEDB Population Coverage epitope/MHC-restriction file (tab-separated, no header).

    The IEDB tool compares allele names as exact strings and does not understand DQ/DP heterodimers,
    so when ``table`` is given every allele is written under the name our own calculation used for it
    (heterodimer -> beta chain) and alleles without frequency data are omitted. The numbers from the
    official tool then line up with ``coverage_stats``.
    """
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    for epi, alleles in epitope_alleles.items():
        names: set[str] = set()
        for a in alleles:
            a = normalise_allele(a)
            if table is None:
                names.add(a)
            else:
                m = table.map_allele(a)
                if m is not None:
                    names.add(m[2])
        if names:
            lines.append(f"{epi}\t{','.join(sorted(names))}")
    p.write_text("\n".join(lines) + "\n")
    return p
