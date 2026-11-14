"""BLAST+ wrapper (local binaries or the official Docker image) and result summarisation.

The README forbids the NCBI BLAST URL API at demo time, so only local / Docker BLAST+ is
supported here.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

from .errors import ToolFailed, ToolUnavailable
from .tools import docker_available, docker_wrap, resolve_executable, run_cmd, tool_version

OUTFMT = "6 qseqid sseqid pident length qlen slen qstart qend sstart send evalue bitscore qseq sseq"
_FIELDS = OUTFMT.split()[1:]


@dataclass
class Hsp:
    qseqid: str
    sseqid: str
    pident: float          # percent, 0-100
    length: int
    qlen: int
    slen: int
    qstart: int            # 1-based inclusive
    qend: int
    sstart: int
    send: int
    evalue: float
    bitscore: float
    qseq: str = ""
    sseq: str = ""


@dataclass
class HitSummary:
    query_id: str
    subject_id: str
    identity: float        # 0-1, identity of the best HSP
    coverage: float        # 0-1, fraction of the query covered by the union of this subject's HSPs
    evalue: float
    bitscore: float
    hsps: list[Hsp] = field(default_factory=list, repr=False)

    def brief(self) -> dict:
        return {"subject": self.subject_id, "identity": round(self.identity, 4),
                "coverage": round(self.coverage, 4), "evalue": self.evalue, "bitscore": self.bitscore}


def parse_blast_tab(text: str) -> list[Hsp]:
    out: list[Hsp] = []
    for line in text.splitlines():
        if not line.strip() or line.startswith("#"):
            continue
        f = line.rstrip("\n").split("\t")
        if len(f) < 12:
            raise ToolFailed(f"unexpected BLAST row ({len(f)} columns): {line[:120]!r}")
        out.append(Hsp(qseqid=f[0], sseqid=f[1], pident=float(f[2]), length=int(f[3]), qlen=int(f[4]),
                       slen=int(f[5]), qstart=int(f[6]), qend=int(f[7]), sstart=int(f[8]), send=int(f[9]),
                       evalue=float(f[10]), bitscore=float(f[11]),
                       qseq=f[12] if len(f) > 12 else "", sseq=f[13] if len(f) > 13 else ""))
    return out


def _union_length(intervals: Iterable[tuple[int, int]]) -> int:
    total, cur_s, cur_e = 0, None, None
    for s, e in sorted((min(a, b), max(a, b)) for a, b in intervals):
        if cur_e is None or s > cur_e + 1:
            if cur_e is not None:
                total += cur_e - cur_s + 1
            cur_s, cur_e = s, e
        else:
            cur_e = max(cur_e, e)
    if cur_e is not None:
        total += cur_e - cur_s + 1
    return total


def summarise_by_subject(hsps: Iterable[Hsp]) -> dict[str, dict[str, HitSummary]]:
    """``{query: {subject: HitSummary}}`` with identity from the best HSP and union coverage."""
    grouped: dict[tuple[str, str], list[Hsp]] = defaultdict(list)
    for h in hsps:
        grouped[(h.qseqid, h.sseqid)].append(h)
    out: dict[str, dict[str, HitSummary]] = defaultdict(dict)
    for (q, s), hs in grouped.items():
        best = max(hs, key=lambda h: h.bitscore)
        cov = _union_length((h.qstart, h.qend) for h in hs) / best.qlen if best.qlen else 0.0
        out[q][s] = HitSummary(q, s, best.pident / 100.0, min(1.0, cov), min(h.evalue for h in hs),
                               best.bitscore, hs)
    return out


def best_hits(hsps: Iterable[Hsp]) -> dict[str, HitSummary]:
    """Best subject per query (highest bitscore)."""
    return {q: max(subj.values(), key=lambda s: s.bitscore) for q, subj in summarise_by_subject(hsps).items()}


def shared_exact_kmers(hsps: Iterable[Hsp], k: int) -> dict[str, list[int]]:
    """Exact ``k``-mers shared between query and subject inside the aligned regions.

    Returns ``{kmer: [1-based query start positions]}``. Only gap-free runs of identical
    residues count. This is the peptide-level self-similarity check used with the IEDB human
    MHC-ligand re-check (README S4 / NERVE 2.0 approach).
    """
    found: dict[str, set[int]] = defaultdict(set)
    for h in hsps:
        if not h.qseq or not h.sseq:
            continue
        qpos = h.qstart
        run: list[tuple[int, str]] = []
        for qc, sc in zip(h.qseq, h.sseq):
            if qc == "-":                 # gap in query: consumes no query residue
                run = []
                continue
            if sc != "-" and qc.upper() == sc.upper():
                run.append((qpos, qc.upper()))
                if len(run) >= k:
                    win = run[-k:]
                    found["".join(c for _, c in win)].add(win[0][0])
            else:
                run = []
            qpos += 1
    return {kmer: sorted(pos) for kmer, pos in found.items()}


class BlastRunner:
    """Thin wrapper; construct once per stage and reuse."""

    def __init__(self, mode: str = "local", *, docker: str = "docker", docker_image: str = "ncbi/blast:latest",
                 threads: int = 4):
        if mode not in ("local", "docker"):
            raise ValueError("mode must be 'local' or 'docker'")
        self.mode, self.docker, self.image, self.threads = mode, docker, docker_image, threads

    # -- availability ---------------------------------------------------------------
    def available(self) -> bool:
        if self.mode == "docker":
            return docker_available(self.docker)
        return resolve_executable("blastp") is not None and resolve_executable("makeblastdb") is not None

    def _require(self) -> None:
        if not self.available():
            hint = "docker daemon not reachable" if self.mode == "docker" else "blastp/makeblastdb not on PATH"
            raise ToolUnavailable(f"BLAST+ unavailable ({self.mode}): {hint}")

    def version(self) -> str | None:
        if self.mode == "docker":
            return f"docker:{self.image}"
        exe = resolve_executable("blastp")
        return tool_version(exe + ["-version"], r"blastp:\s*([\d.]+\+?)") if exe else None

    # -- execution ------------------------------------------------------------------
    def _argv(self, tool: str, args: Sequence[str | Path], mounts: Sequence[Path]) -> list[str]:
        if self.mode == "docker":
            return docker_wrap(self.image, [tool, *map(str, args)], [m.parent if m.is_file() else m for m in mounts],
                               docker=self.docker)
        exe = resolve_executable(tool)
        assert exe is not None
        return exe + [str(a) for a in args]

    def makeblastdb(self, fasta: Path, db_prefix: Path) -> Path:
        self._require()
        fasta, db_prefix = Path(fasta).resolve(), Path(db_prefix).resolve()
        db_prefix.parent.mkdir(parents=True, exist_ok=True)
        pin = db_prefix.with_name(db_prefix.name + ".pin")
        pdb = db_prefix.with_name(db_prefix.name + ".pdb")
        if (pin.exists() or pdb.exists()) and max(p.stat().st_mtime for p in (pin, pdb) if p.exists()) >= fasta.stat().st_mtime:
            return db_prefix
        run_cmd(self._argv("makeblastdb", ["-in", fasta, "-dbtype", "prot", "-out", db_prefix],
                           [fasta.parent, db_prefix.parent]), what="makeblastdb")
        return db_prefix

    def blastp(self, query_fasta: Path, db_prefix: Path, *, evalue: float = 1e-3, max_target_seqs: int = 5,
               timeout: float | None = 3600) -> list[Hsp]:
        self._require()
        q, db = Path(query_fasta).resolve(), Path(db_prefix).resolve()
        res = run_cmd(self._argv("blastp", ["-query", q, "-db", db, "-outfmt", OUTFMT, "-evalue", f"{evalue:g}",
                                            "-max_target_seqs", str(max_target_seqs), "-num_threads", str(self.threads)],
                                 [q.parent, db.parent]), timeout=timeout, what="blastp")
        return parse_blast_tab(res.stdout)
