"""MHC binding prediction backends: NetMHCpan / NetMHCIIpan (local install) and MHCflurry.

Parsing is header-driven because the column set differs between versions and between the
DTU web service (``HLA`` / ``Rnk_EL``) and the local binaries (``MHC`` / ``%Rank_EL``).

Documented behaviours relied on (DTU NetMHCpan-4.1 instructions page):
  * ``Pos`` is the residue number of the peptide start *starting from 0*.
  * ``BindLevel`` is ``<= SB`` (strong) / ``<= WB`` (weak) or blank.
  * ``%Rank`` is percentile vs random natural peptides; strong < 0.5 %, weak < 2 % by default.
Everything else (CLI flag spelling, allele-name spelling) is flagged UNVERIFIED in docs/TOOLS_SETUP.md
until run against a real install.
"""
from __future__ import annotations

import importlib.util
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .errors import ToolFailed, ToolUnavailable
from .fasta import write_fasta
from .hla import allele_class, normalise_allele, to_netmhciipan, to_netmhcpan
from .tools import run_cmd

_RANK_NAMES = ("%Rank_EL", "Rnk_EL", "%Rank", "Rank_EL", "EL_Rank")
_SCORE_NAMES = ("Score_EL", "Score", "EL-score")


@dataclass(frozen=True)
class Binding:
    protein_id: str
    start: int            # 1-based inclusive
    peptide: str
    allele: str           # canonical
    core: str
    rank: float           # %Rank (eluted-ligand / presentation)
    score: float | None
    bind_level: str | None   # 'SB' | 'WB' | None (as printed by the tool; stage re-derives from rank)
    group: str | None = None  # collapse key; None -> use `core`. MHCflurry reports no core, so it groups lengths by start

    @property
    def end(self) -> int:
        return self.start + len(self.peptide) - 1


def parse_netmhc_output(text: str, id_map: Mapping[str, str] | None = None) -> list[Binding]:
    """Parse netMHCpan / netMHCIIpan stdout (any version with a ``Pos ...`` header line)."""
    header: list[str] | None = None
    out: list[Binding] = []
    for line in text.splitlines():
        s = line.strip()
        if not s or s.startswith("#") or set(s) <= {"-"}:
            continue
        toks = s.split()
        if toks[0] == "Pos":
            header = toks[:-1] if toks[-1] == "BindLevel" else toks
            continue
        if header is None or not toks[0].isdigit():
            continue
        level = None
        if len(toks) >= 2 and toks[-2] == "<=" and toks[-1] in ("SB", "WB"):
            level, toks = toks[-1], toks[:-2]
        elif toks[-1] in ("SB", "WB") and len(toks) > len(header):
            level, toks = toks[-1], toks[:-1]
        if len(toks) < len(header):
            continue
        row = dict(zip(header, toks))
        allele_raw = row.get("MHC") or row.get("HLA")
        rank_key = next((k for k in _RANK_NAMES if k in row), None)
        if allele_raw is None or "Peptide" not in row or rank_key is None:
            raise ToolFailed(f"unrecognised NetMHC header: {header}")
        try:
            allele = normalise_allele(allele_raw)
        except ValueError as e:
            raise ToolFailed(str(e)) from e
        pid = row.get("Identity", "")
        score_key = next((k for k in _SCORE_NAMES if k in row), None)
        out.append(Binding(
            protein_id=(id_map or {}).get(pid, pid),
            start=int(row["Pos"]) + 1,
            peptide=row["Peptide"],
            allele=allele,
            core=row.get("Core") or row["Peptide"],
            rank=float(row[rank_key]),
            score=float(row[score_key]) if score_key else None,
            bind_level=level,
        ))
    return out


class NetMHCRunner:
    """Drives a local ``netMHCpan`` (class I) or ``netMHCIIpan`` (class II) install."""

    def __init__(self, kind: str, argv_prefix: Sequence[str], *, timeout: float = 3600):
        if kind not in ("netmhcpan", "netmhciipan"):
            raise ValueError(kind)
        self.kind, self.argv, self.timeout = kind, list(argv_prefix), timeout
        self.name = "NetMHCpan" if kind == "netmhcpan" else "NetMHCIIpan"
        self.fidelity = "reference"

    def _spell(self, allele: str) -> str:
        return to_netmhcpan(allele) if self.kind == "netmhcpan" else to_netmhciipan(allele)

    def predict(self, seqs: Mapping[str, str], alleles: Sequence[str], lengths: Sequence[int], *,
                alleles_per_call: int = 6, proteins_per_call: int = 10,
                max_rank: float | None = None) -> list[Binding]:
        """Predict binding for every window of every protein.

        ``max_rank`` keeps only rows with ``%Rank <= max_rank`` (memory: the tools print every
        window). Output is checked so that no allele is silently dropped.
        """
        expect = "I" if self.kind == "netmhcpan" else "II"
        for a in alleles:
            if allele_class(a) != expect:
                raise ValueError(f"{a} is not class {expect}")
        # Short surrogate ids: netMHC tools truncate / mangle long FASTA identifiers.
        id_map = {f"P{i:05d}": pid for i, pid in enumerate(seqs)}
        rev = {v: k for k, v in id_map.items()}
        pids = list(seqs)
        results: list[Binding] = []
        with tempfile.TemporaryDirectory(prefix="vf_netmhc_") as td:
            for pi in range(0, len(pids), proteins_per_call):
                pbatch = pids[pi:pi + proteins_per_call]
                fasta = write_fasta(Path(td) / f"in_{pi}.fsa", [(rev[pid], seqs[pid]) for pid in pbatch])
                for i in range(0, len(alleles), alleles_per_call):
                    batch = list(alleles[i:i + alleles_per_call])
                    args = self.argv + ["-a", ",".join(self._spell(a) for a in batch), "-f", str(fasta)]
                    args += ["-l", ",".join(map(str, lengths))] if self.kind == "netmhcpan" else ["-length", str(lengths[0])]
                    res = run_cmd(args, timeout=self.timeout, what=self.name)
                    rows = parse_netmhc_output(res.stdout, id_map)
                    seen = {r.allele for r in rows}
                    missing = [a for a in batch if a not in seen]
                    if missing:
                        raise ToolFailed(f"{self.name} returned no rows for {missing}; the spelling "
                                         f"{[self._spell(a) for a in missing]} may be unsupported by this install")
                    results.extend(r for r in rows if max_rank is None or r.rank <= max_rank)
        return results


class MHCflurryRunner:
    """Open-source class I stand-in (``pip install mhcflurry``). ``fidelity='alternative'``."""

    name = "MHCflurry"
    kind = "mhcflurry"
    fidelity = "alternative"

    @staticmethod
    def installed() -> bool:
        return importlib.util.find_spec("mhcflurry") is not None

    def __init__(self) -> None:
        if not self.installed():
            raise ToolUnavailable("mhcflurry is not installed (pip install mhcflurry; mhcflurry-downloads fetch)")
        self.unsupported_alleles: list[str] = []

    def predict(self, seqs: Mapping[str, str], alleles: Sequence[str], lengths: Sequence[int], *,
                max_rank: float | None = None, **_) -> list[Binding]:
        from mhcflurry import Class1PresentationPredictor  # type: ignore

        predictor = Class1PresentationPredictor.load()
        windows: list[tuple[str, int, str]] = []
        for pid, seq in seqs.items():
            for L in lengths:
                windows += [(pid, i + 1, seq[i:i + L]) for i in range(len(seq) - L + 1)]
        peptides = sorted({w[2] for w in windows})
        out: list[Binding] = []
        for allele in alleles:
            try:
                df = predictor.predict(peptides=peptides, alleles=[allele], verbose=0)
            except Exception:                                  # noqa: BLE001 - unsupported allele etc.
                self.unsupported_alleles.append(allele)
                continue
            col = "presentation_percentile" if "presentation_percentile" in df.columns else None
            if col is None:
                raise ToolFailed("this mhcflurry version returns no presentation_percentile column")
            rank = dict(zip(df["peptide"], df[col]))
            score = dict(zip(df["peptide"], df["presentation_score"])) if "presentation_score" in df.columns else {}
            for pid, start, pep in windows:
                if max_rank is not None and float(rank[pep]) > max_rank:
                    continue
                out.append(Binding(pid, start, pep, allele, pep, float(rank[pep]),
                                   float(score[pep]) if pep in score else None, None, group=str(start)))
        return out
