"""Tiny FASTA helpers (no Biopython dependency)."""
from __future__ import annotations

import gzip
import io
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

PROTEIN_ALPHABET = set("ACDEFGHIKLMNPQRSTVWY")
AMBIGUOUS = set("XBZUOJ")


class FastaError(Exception):
    pass


@dataclass
class FastaRecord:
    header: str           # without the leading '>'
    seq: str

    @property
    def id(self) -> str:
        """First whitespace-delimited token of the header (what BLAST calls the id)."""
        return self.header.split(None, 1)[0] if self.header.strip() else ""

    @property
    def description(self) -> str:
        parts = self.header.split(None, 1)
        return parts[1] if len(parts) > 1 else ""


def _open_text(path: str | os.PathLike):
    p = Path(path)
    if p.suffix == ".gz":
        return io.TextIOWrapper(gzip.open(p, "rb"), encoding="utf-8", errors="replace")
    return open(p, "r", encoding="utf-8", errors="replace", newline=None)


def read_fasta(path: str | os.PathLike) -> Iterator[FastaRecord]:
    header: str | None = None
    chunks: list[str] = []
    with _open_text(path) as fh:
        for line in fh:
            line = line.rstrip("\r\n")
            if not line:
                continue
            if line.startswith(">"):
                if header is not None:
                    yield FastaRecord(header, "".join(chunks))
                header = line[1:].strip()
                chunks = []
            elif header is not None:
                chunks.append(line.strip())
            else:
                raise FastaError(f"{path}: sequence data before first header")
        if header is not None:
            yield FastaRecord(header, "".join(chunks))


def write_fasta(records: Iterable[FastaRecord], path: str | os.PathLike, width: int = 60) -> int:
    from .cache import atomic_write_text

    out: list[str] = []
    n = 0
    for rec in records:
        out.append(">" + rec.header)
        seq = rec.seq
        for i in range(0, len(seq), width):
            out.append(seq[i:i + width])
        n += 1
    atomic_write_text(path, "\n".join(out) + ("\n" if out else ""))
    return n


def count_records(path: str | os.PathLike) -> int:
    n = 0
    with _open_text(path) as fh:
        for line in fh:
            if line.startswith(">"):
                n += 1
    return n


def fasta_stats(path: str | os.PathLike) -> dict:
    n = 0
    total = 0
    lens: list[int] = []
    seen: dict[str, int] = {}
    dup: list[str] = []
    nonstd = 0
    empty = 0
    for rec in read_fasta(path):
        n += 1
        L = len(rec.seq)
        total += L
        lens.append(L)
        if L == 0:
            empty += 1
        rid = rec.id
        if rid in seen:
            dup.append(rid)
        seen[rid] = 1
        letters = set(rec.seq.upper())
        if letters - PROTEIN_ALPHABET - AMBIGUOUS:
            nonstd += 1
    return {
        "records": n,
        "residues": total,
        "min_length": min(lens) if lens else 0,
        "max_length": max(lens) if lens else 0,
        "duplicate_ids": dup,
        "empty_sequences": empty,
        "records_with_nonstandard_residues": nonstd,
    }


def validate_protein_fasta(path: str | os.PathLike, min_records: int = 1) -> dict:
    """Return stats, or raise FastaError if the file cannot be used as a proteome."""
    stats = fasta_stats(path)
    if stats["records"] < min_records:
        raise FastaError(f"{path}: expected at least {min_records} sequence(s), found {stats['records']}")
    if stats["empty_sequences"]:
        raise FastaError(f"{path}: {stats['empty_sequences']} record(s) have an empty sequence")
    if stats["duplicate_ids"]:
        sample = ", ".join(stats["duplicate_ids"][:3])
        raise FastaError(f"{path}: duplicate sequence ids (e.g. {sample}); protein_id must be unique")
    if stats["records_with_nonstandard_residues"] > 0.05 * stats["records"]:
        raise FastaError(
            f"{path}: {stats['records_with_nonstandard_residues']} of {stats['records']} records contain "
            f"non-protein residues; is this a nucleotide FASTA?"
        )
    return stats


_UNIPROT_RE = re.compile(
    r"^(?P<db>sp|tr)\|(?P<acc>[A-Za-z0-9_-]+)\|(?P<entry>\S+)\s+(?P<desc>.*?)"
    r"(?:\s+OS=(?P<os>.*?))?(?:\s+OX=(?P<ox>\d+))?(?:\s+GN=(?P<gn>\S+))?(?:\s+PE=(?P<pe>\d))?(?:\s+SV=(?P<sv>\d+))?$"
)


def parse_uniprot_header(header: str) -> dict:
    """Parse a UniProt FASTA header; non-UniProt headers fall back to id/description."""
    m = _UNIPROT_RE.match(header.strip())
    if not m:
        rid, _, desc = header.strip().partition(" ")
        return {"accession": rid, "entry_name": None, "description": desc or None, "organism": None,
                "taxid": None, "gene": None, "reviewed": None, "protein_existence": None}
    return {
        "accession": m.group("acc"),
        "entry_name": m.group("entry"),
        "description": (m.group("desc") or "").strip() or None,
        "organism": m.group("os"),
        "taxid": int(m.group("ox")) if m.group("ox") else None,
        "gene": m.group("gn"),
        "reviewed": m.group("db") == "sp",
        "protein_existence": int(m.group("pe")) if m.group("pe") else None,
    }
