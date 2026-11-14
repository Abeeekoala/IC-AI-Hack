"""FASTA I/O, sequence sanitising and overlap chunking."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

STANDARD_AA = set("ACDEFGHIKLMNPQRSTVWY")
# Position-preserving substitutions so tools that reject non-standard letters still run and
# residue coordinates stay valid. Every replacement is counted and surfaced as a warning.
_SUBST = {"U": "C", "O": "K", "B": "D", "Z": "E", "J": "L", "X": "A"}


def read_fasta(path: str | Path) -> list[tuple[str, str, str]]:
    """Return ``[(id, description, sequence), ...]``; id is the first header token."""
    out: list[tuple[str, str, str]] = []
    hdr: str | None = None
    buf: list[str] = []
    for line in Path(path).read_text().splitlines():
        if line.startswith(">"):
            if hdr is not None:
                out.append(_split_header(hdr) + ("".join(buf),))
            hdr, buf = line[1:].strip(), []
        elif line.strip():
            buf.append(line.strip())
    if hdr is not None:
        out.append(_split_header(hdr) + ("".join(buf),))
    return out


def _split_header(h: str) -> tuple[str, str]:
    parts = h.split(None, 1)
    return (parts[0] if parts else ""), (parts[1] if len(parts) > 1 else "")


def write_fasta(path: str | Path, items: Iterable[tuple[str, str]], width: int = 60) -> Path:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for ident, seq in items:
        lines.append(f">{ident}")
        lines.extend(seq[i:i + width] for i in range(0, len(seq), width) or [0])
    p.write_text("\n".join(lines) + "\n")
    return p


def clean_sequence(seq: str) -> str:
    """Upper-case, drop whitespace and a trailing stop '*'."""
    s = re.sub(r"\s+", "", seq).upper()
    return s[:-1] if s.endswith("*") else s


@dataclass(frozen=True)
class Sanitised:
    sequence: str
    n_replaced: int
    fraction_replaced: float


def sanitise(seq: str) -> Sanitised:
    """Replace non-standard residues (position-preserving). Unknown letters become 'A'."""
    s = clean_sequence(seq)
    n = 0
    out = []
    for ch in s:
        if ch in STANDARD_AA:
            out.append(ch)
        else:
            out.append(_SUBST.get(ch, "A"))
            n += 1
    return Sanitised("".join(out), n, (n / len(s)) if s else 0.0)


def chunk_sequence(seq: str, size: int, overlap: int) -> list[tuple[int, str]]:
    """Split into overlapping windows ``[(offset, subsequence), ...]`` covering the whole sequence.

    BepiPred-3.0's source can truncate long inputs (not seen on 1,144 aa in a real run), so long proteins are windowed and the
    per-residue scores are merged back (see ``merge_chunk_scores``).
    """
    if size <= 0 or overlap < 0 or overlap >= size:
        raise ValueError("need size > 0 and 0 <= overlap < size")
    n = len(seq)
    if n <= size:
        return [(0, seq)]
    step = size - overlap
    chunks: list[tuple[int, str]] = []
    start = 0
    while True:
        end = min(start + size, n)
        chunks.append((start, seq[start:end]))
        if end >= n:
            break
        start += step
    return chunks


def merge_chunk_scores(total_len: int, chunks: list[tuple[int, list[float]]]) -> list[float | None]:
    """Merge per-residue scores from overlapping windows.

    Each residue takes the score from the window in which it is most *central* (largest
    distance to either window edge); ties go to the earlier window. Returns ``None`` for any
    residue no window covered (callers treat that as a truncation failure).
    """
    best_dist = [-1] * total_len
    out: list[float | None] = [None] * total_len
    for offset, scores in chunks:
        m = len(scores)
        for i, sc in enumerate(scores):
            pos = offset + i
            if pos >= total_len:
                break
            dist = min(i, m - 1 - i)
            if dist > best_dist[pos]:
                best_dist[pos] = dist
                out[pos] = sc
    return out
