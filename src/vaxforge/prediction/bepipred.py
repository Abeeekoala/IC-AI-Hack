"""BepiPred-3.0 runner with chunking, truncation detection and linear-epitope calling.

Facts verified from the BepiPred-3.0 repository (UberClifford/BepiPred-3.0):
  * CLI: ``python bepipred3_CLI.py -i in.fasta -o outdir -pred {mjv_pred,vt_pred} [-t 0.1512] [-esm_dir DIR]``
  * ``raw_output.csv`` header ``Accession,Residue,BepiPred-3.0 score,BepiPred-3.0 linear epitope score``
    with one row per residue.
  * The source has a branch for sequences longer than the ESM-2 window that prints
    "Outputting predictions up till N position" and writes fewer rows (the project README calls this
    "truncates above 1,023 aa"). **Run for real (2026-11-14) the local CLI did NOT truncate a
    1,144-residue protein** - it returned 1,144 rows. We still (a) window long proteins, because ESM-2
    was trained on <= 1024-token crops (chunked vs unchunked scores: r = 0.989, same region count), and
    (b) verify that every window got a row for every residue, so a version that does truncate fails loudly.
  * Licence: free for academic, non-commercial use (DTU Health Tech); a separate licence is
    needed for any for-profit use.
"""
from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .errors import ToolFailed
from .fasta import chunk_sequence, merge_chunk_scores, write_fasta
from .tools import run_cmd

CSV_HEADER = ("Accession", "Residue", "BepiPred-3.0 score", "BepiPred-3.0 linear epitope score")


@dataclass
class ResidueScores:
    protein_id: str
    scores: list[float]            # BepiPred-3.0 residue score
    linear_scores: list[float]     # rolling-mean "linear epitope score"
    n_chunks: int


@dataclass
class Region:
    start: int      # 1-based inclusive
    end: int
    peptide: str
    mean_score: float
    max_score: float


def parse_raw_output(text: str) -> dict[str, tuple[str, list[float], list[float]]]:
    """``raw_output.csv`` -> ``{accession: (residues, scores, linear_scores)}`` (row order kept)."""
    lines = [l for l in text.splitlines() if l.strip()]
    if not lines:
        raise ToolFailed("BepiPred-3.0 produced an empty raw_output.csv")
    head = [h.strip() for h in lines[0].split(",")]
    if tuple(head[:4]) != CSV_HEADER:
        raise ToolFailed(f"unexpected BepiPred-3.0 header: {head}")
    acc_order: dict[str, tuple[list[str], list[float], list[float]]] = {}
    for ln in lines[1:]:
        parts = [p.strip() for p in ln.split(",")]
        if len(parts) < 4:
            raise ToolFailed(f"malformed BepiPred-3.0 row: {ln[:80]!r}")
        res, s1, s2 = acc_order.setdefault(parts[0], ([], [], []))
        res.append(parts[1])
        s1.append(float(parts[2]))
        s2.append(float(parts[3]))
    return {a: ("".join(r), s, l) for a, (r, s, l) in acc_order.items()}


def assemble_scores(seqs: Mapping[str, str], parsed: Mapping[str, tuple[str, list[float], list[float]]],
                    *, chunk_size: int, chunk_overlap: int,
                    chunk_ids: Mapping[str, list[tuple[str, int, str]]]) -> dict[str, ResidueScores]:
    """Verify per-chunk row counts / residues and merge chunk scores into per-protein arrays.

    ``chunk_ids[pid] = [(chunk_accession, offset, chunk_sequence), ...]``.
    """
    out: dict[str, ResidueScores] = {}
    for pid, seq in seqs.items():
        s_chunks, l_chunks = [], []
        for acc, offset, csq in chunk_ids[pid]:
            if acc not in parsed:
                raise ToolFailed(f"BepiPred-3.0 returned no rows for {acc}")
            residues, sc, ls = parsed[acc]
            if len(sc) != len(csq):
                raise ToolFailed(
                    f"BepiPred-3.0 truncated {acc}: {len(sc)} scored residues for a {len(csq)}-residue window "
                    f"(chunk_size={chunk_size}). Lower s5.bcell.chunk_size; the tool's limit is below it.")
            if residues != csq.upper():
                raise ToolFailed(f"BepiPred-3.0 residue mismatch for {acc}: output residues differ from the input window")
            s_chunks.append((offset, sc))
            l_chunks.append((offset, ls))
        merged = merge_chunk_scores(len(seq), s_chunks)
        merged_l = merge_chunk_scores(len(seq), l_chunks)
        if any(v is None for v in merged) or any(v is None for v in merged_l):
            raise ToolFailed(f"BepiPred-3.0 windows did not cover every residue of {pid}")
        out[pid] = ResidueScores(pid, [float(v) for v in merged], [float(v) for v in merged_l], len(chunk_ids[pid]))
    return out


def call_linear_epitopes(seq: str, scores: Sequence[float], *, threshold: float, min_len: int = 5,
                         merge_gap: int = 1) -> list[Region]:
    """Contiguous stretches of residues with score >= ``threshold``.

    Runs separated by at most ``merge_gap`` residues are merged; stretches shorter than
    ``min_len`` are dropped. ``threshold`` defaults to BepiPred-3.0's own 0.1512 upstream.
    Note (tool docs): predictions are antibody-contact residues, so some residues inside a
    stretch are not direct contacts; treat regions as hypotheses for peptide-array mapping.
    """
    n = len(seq)
    runs: list[list[int]] = []
    i = 0
    while i < n:
        if scores[i] >= threshold:
            j = i
            while j + 1 < n and scores[j + 1] >= threshold:
                j += 1
            runs.append([i, j])
            i = j + 1
        else:
            i += 1
    merged: list[list[int]] = []
    for r in runs:
        if merged and r[0] - merged[-1][1] - 1 <= merge_gap:
            merged[-1][1] = r[1]
        else:
            merged.append(r)
    out = []
    for a, b in merged:
        if b - a + 1 >= min_len:
            seg = scores[a:b + 1]
            out.append(Region(a + 1, b + 1, seq[a:b + 1], sum(seg) / len(seg), max(seg)))
    return out


def top_fraction_mean(scores: Sequence[float], fraction: float = 0.2) -> float:
    if not scores:
        return 0.0
    k = max(1, int(round(len(scores) * fraction)))
    top = sorted(scores, reverse=True)[:k]
    return sum(top) / len(top)


class BepiPredRunner:
    name = "BepiPred-3.0"
    fidelity = "reference"

    def __init__(self, argv_prefix: Sequence[str], *, pred: str = "vt_pred", threshold: float = 0.1512,
                 esm_dir: str | Path | None = None, timeout: float = 4 * 3600):
        self.argv, self.pred, self.threshold = list(argv_prefix), pred, threshold
        self.esm_dir, self.timeout = esm_dir, timeout

    def predict(self, seqs: Mapping[str, str], *, chunk_size: int = 1000, chunk_overlap: int = 100,
                workdir: str | Path | None = None) -> dict[str, ResidueScores]:
        short = {pid: f"P{i:05d}" for i, pid in enumerate(seqs)}
        chunk_ids: dict[str, list[tuple[str, int, str]]] = {}
        entries: list[tuple[str, str]] = []
        for pid, seq in seqs.items():
            lst = []
            for ci, (off, sub) in enumerate(chunk_sequence(seq, chunk_size, chunk_overlap)):
                acc = f"{short[pid]}c{ci:03d}"
                lst.append((acc, off, sub))
                entries.append((acc, sub))
            chunk_ids[pid] = lst
        ctx = tempfile.TemporaryDirectory(prefix="vf_bepipred_") if workdir is None else None
        base = Path(ctx.name if ctx else workdir)
        try:
            fasta = write_fasta(base / "in.fasta", entries)
            out_dir = base / "out"
            args = self.argv + ["-i", str(fasta), "-o", str(out_dir), "-pred", self.pred, "-t", str(self.threshold)]
            if self.esm_dir:
                args += ["-esm_dir", str(self.esm_dir)]
            run_cmd(args, timeout=self.timeout, what="BepiPred-3.0")
            raw = out_dir / "raw_output.csv"
            if not raw.exists():
                raise ToolFailed("BepiPred-3.0 finished without writing raw_output.csv")
            parsed = parse_raw_output(raw.read_text())
            return assemble_scores(seqs, parsed, chunk_size=chunk_size, chunk_overlap=chunk_overlap, chunk_ids=chunk_ids)
        finally:
            if ctx:
                ctx.cleanup()
