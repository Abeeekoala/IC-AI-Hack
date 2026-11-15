"""External tool runners: BLAST+ (native or Docker).

Use :class:`BlastRunner` for every BLAST call in the pipeline (streams B and D
too) - it hides two real portability problems:

* BLAST+ on Windows mangles non-ASCII paths (a project under ``D:\\数据\\...``
  makes ``makeblastdb`` silently build nothing).  Databases are therefore built
  in an ASCII-only directory and query files are staged there.
* BLAST+ may be installed natively or only be available through Docker.
"""
from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Sequence

from .config import Config


class ToolError(Exception):
    pass


def is_ascii_path(p: str | os.PathLike) -> bool:
    return str(p).isascii()


def ascii_safe_dir(preferred: Path, tag: str) -> Path:
    """``preferred`` if it is pure ASCII, else a stable ASCII-only directory for the same purpose."""
    preferred = Path(preferred)
    if is_ascii_path(preferred.resolve()):
        preferred.mkdir(parents=True, exist_ok=True)
        return preferred
    suffix = hashlib.sha1(str(preferred.resolve()).encode("utf-8")).hexdigest()[:10]
    candidates = [
        Path(tempfile.gettempdir()) / "vaxforge",
        Path(os.environ.get("PUBLIC", "C:\\Users\\Public")) / "vaxforge",
        Path(os.environ.get("ProgramData", "C:\\ProgramData")) / "vaxforge",
        Path("/tmp/vaxforge"),
    ]
    for base in candidates:
        d = base / f"{tag}-{suffix}"
        if not is_ascii_path(d):
            continue
        try:
            d.mkdir(parents=True, exist_ok=True)
            return d
        except OSError:
            continue
    raise ToolError(
        f"{preferred} contains non-ASCII characters, which BLAST+ cannot open, and no ASCII-only "
        f"fallback directory is writable. Set paths.cache_dir to an ASCII-only folder."
    )


def find_executable(name: str, search_dir: str | None = None) -> str | None:
    exe = name + (".exe" if os.name == "nt" else "")
    if search_dir:
        cand = Path(search_dir) / exe
        if cand.is_file():
            return str(cand)
    return shutil.which(name)


def run_command(cmd: Sequence[str], cwd: str | os.PathLike | None = None, timeout: float | None = None,
                input_text: str | None = None) -> subprocess.CompletedProcess:
    try:
        # Never let a tool inherit our stdin: it must not block on it, and under some launchers
        # (IDEs, test runners, services) that handle is invalid and Windows refuses to spawn at all.
        proc = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, timeout=timeout,
                              input=input_text, stdin=None if input_text is not None else subprocess.DEVNULL,
                              encoding="utf-8", errors="replace")
    except FileNotFoundError as exc:
        raise ToolError(f"command not found: {cmd[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        raise ToolError(f"timed out after {timeout}s: {' '.join(cmd[:3])} ...") from exc
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-800:]
        raise ToolError(f"{cmd[0]} exited with {proc.returncode}: {tail}")
    return proc


def docker_state(docker: str | None, image: str | None = None, timeout: float = 10.0) -> tuple[str, str]:
    """Probe Docker for real: ``("ok"|"no_daemon"|"no_image"|"missing", detail)``.

    Finding a ``docker`` executable proves nothing: the daemon may be stopped and the image not pulled.
    """
    if not docker:
        return "missing", "docker executable not found"
    try:
        run_command([docker, "info"], timeout=timeout)
    except ToolError as exc:
        return "no_daemon", f"docker found, daemon not running ({str(exc)[-120:]})"
    if image:
        try:
            run_command([docker, "image", "inspect", image], timeout=timeout)
        except ToolError:
            return "no_image", f"image not pulled: {image} (docker pull {image})"
    return "ok", "docker daemon running" + (f", image {image} present" if image else "")


_PROTEIN_DB_SUFFIXES = (".pin", ".phr", ".psq")


class BlastRunner:
    def __init__(self, config: Config, cache_dir: str | os.PathLike | None = None):
        self.config = config
        self.cache_dir = Path(cache_dir) if cache_dir else config.cache_dir
        want = str(config.get("tools.blast_mode", "auto"))
        bin_dir = config.get("tools.blast_bin_dir")
        self.blastp_exe = find_executable("blastp", bin_dir)
        self.makeblastdb_exe = find_executable("makeblastdb", bin_dir)
        self.docker = find_executable(str(config.get("tools.docker", "docker")))
        self.image = str(config.get("tools.blast_image", "ncbi/blast:2.16.0"))
        if want in ("auto", "native") and self.blastp_exe and self.makeblastdb_exe:
            self.mode = "native"
        elif want in ("auto", "docker") and self.docker:
            self.mode = "docker"
        else:
            self.mode = "missing"
        self._version: str | None = None

    # -- discovery ---------------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.mode != "missing"

    @property
    def version(self) -> str | None:
        if self._version is None and self.available:
            try:
                out = self._run("blastp", ["-version"], mounts={}).stdout
                m = re.search(r"blastp:\s*([\w.+-]+)", out)
                self._version = m.group(1) if m else "unknown"
            except ToolError:
                self._version = "unknown"
        return self._version

    def describe(self) -> dict:
        return {"mode": self.mode, "blastp": self.blastp_exe, "makeblastdb": self.makeblastdb_exe,
                "docker": self.docker, "image": self.image if self.mode == "docker" else None,
                "version": self.version if self.available else None}

    def require(self) -> None:
        if not self.available:
            raise ToolError(
                "BLAST+ is not available: install it (https://ftp.ncbi.nlm.nih.gov/blast/executables/blast+/LATEST/), "
                "put blastp/makeblastdb on PATH or set tools.blast_bin_dir / BLAST_BIN, or install Docker."
            )

    # -- low level -------------------------------------------------------------------
    def _run(self, tool: str, args: Sequence[str], mounts: dict[Path, str], cwd: Path | None = None,
             timeout: float | None = None) -> subprocess.CompletedProcess:
        """Run ``tool`` natively, or in Docker with ``mounts`` {host dir: container dir}."""
        if self.mode == "native":
            exe = self.blastp_exe if tool == "blastp" else self.makeblastdb_exe
            return run_command([exe, *args], cwd=cwd, timeout=timeout)
        cmd = [self.docker, "run", "--rm"]
        for host, ctr in mounts.items():
            cmd += ["-v", f"{host}:{ctr}"]
        if cwd is not None and cwd in mounts:
            cmd += ["-w", mounts[cwd]]
        cmd += [self.image, tool, *args]
        return run_command(cmd, timeout=timeout)

    # -- databases --------------------------------------------------------------------------
    def db_root(self) -> Path:
        return ascii_safe_dir(self.cache_dir / "blastdb", "blastdb")

    def db_prefix(self, name: str, fasta_sha256: str) -> Path:
        """Deterministic database prefix for a FASTA with the given hash (consumers can recompute it)."""
        return self.db_root() / f"{name}-{fasta_sha256[:12]}" / name

    @staticmethod
    def db_exists(prefix: Path) -> bool:
        return all(Path(str(prefix) + s).is_file() for s in _PROTEIN_DB_SUFFIXES)

    def make_db(self, fasta: str | os.PathLike, name: str, fasta_sha256: str, title: str | None = None) -> dict:
        self.require()
        prefix = self.db_prefix(name, fasta_sha256)
        if self.db_exists(prefix):
            return {"status": "cached", "prefix": str(prefix)}
        d = prefix.parent
        d.mkdir(parents=True, exist_ok=True)
        local = d / f"{name}.fasta"
        shutil.copyfile(fasta, local)          # keeps the DB self-contained and the path ASCII
        self._run("makeblastdb",
                  ["-in", local.name if self.mode == "native" else f"/db/{local.name}", "-dbtype", "prot",
                   "-out", prefix.name if self.mode == "native" else f"/db/{prefix.name}",
                   "-title", title or name],
                  mounts={d: "/db"}, cwd=d, timeout=3600)
        if not self.db_exists(prefix):
            shutil.rmtree(d, ignore_errors=True)
            raise ToolError(f"makeblastdb finished but produced no database at {prefix}")
        return {"status": "built", "prefix": str(prefix)}

    # -- searching ---------------------------------------------------------------------------
    @contextmanager
    def _scratch(self) -> Iterator[Path]:
        base = ascii_safe_dir(Path(tempfile.gettempdir()) / "vaxforge-scratch", "scratch")
        tmp = Path(tempfile.mkdtemp(prefix="blast_", dir=str(base)))
        try:
            yield tmp
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def blastp(self, query: str | os.PathLike, db_prefix: str | os.PathLike, outfmt: str, evalue: float = 1e-3,
               max_target_seqs: int = 5, threads: int = 0, max_hsps: int = 1, extra: Sequence[str] = (),
               timeout: float | None = 3600) -> str:
        """Run blastp and return stdout (tabular formats).  ``threads=0`` means all CPUs."""
        self.require()
        db_prefix = Path(db_prefix)
        n = threads if threads and threads > 0 else (os.cpu_count() or 1)
        with self._scratch() as tmp:
            shutil.copyfile(query, tmp / "query.faa")
            if self.mode == "native":
                args = ["-query", "query.faa", "-db", str(db_prefix)]
                mounts: dict[Path, str] = {}
            else:
                args = ["-query", "/work/query.faa", "-db", f"/db/{db_prefix.name}"]
                mounts = {tmp: "/work", db_prefix.parent: "/db"}
            args += ["-outfmt", outfmt, "-evalue", repr(float(evalue)), "-max_target_seqs", str(max_target_seqs),
                     "-max_hsps", str(max_hsps), "-num_threads", str(n), *extra]
            return self._run("blastp", args, mounts=mounts, cwd=tmp, timeout=timeout).stdout
