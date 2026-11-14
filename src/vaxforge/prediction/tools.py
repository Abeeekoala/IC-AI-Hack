"""Running external tools: local binaries and Docker, with honest availability reporting."""
from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Sequence

from .errors import ToolFailed, ToolUnavailable


@dataclass
class CmdResult:
    args: list[str]
    returncode: int
    stdout: str
    stderr: str


def split_command(cmd: str | Sequence[str]) -> list[str]:
    """Accept ``"python /opt/x.py"`` or ``["python", "/opt/x.py"]``."""
    if isinstance(cmd, str):
        if os.name == "nt":                                   # POSIX-mode shlex would eat the backslashes of C:\\tools\\x.exe
            return [t[1:-1] if len(t) > 1 and t[0] == t[-1] and t[0] in "\"'" else t for t in shlex.split(cmd, posix=False)]
        return shlex.split(cmd)
    return list(cmd)


def resolve_executable(cmd: str | Sequence[str] | None, default: str | None = None) -> list[str] | None:
    """Resolve a configured command to an argv prefix, or ``None`` if it cannot be found.

    A command whose first token is an existing file or an executable on PATH is accepted.
    """
    raw = cmd if cmd else default
    if not raw:
        return None
    argv = split_command(raw)
    head = argv[0]
    if os.path.sep in head:
        return argv if Path(head).exists() else None
    found = shutil.which(head)
    return ([found] + argv[1:]) if found else None


def run_cmd(args: Sequence[str], *, cwd: str | Path | None = None, env: Mapping[str, str] | None = None,
            timeout: float | None = None, input_text: str | None = None, check: bool = True,
            what: str | None = None) -> CmdResult:
    """Run a subprocess, capturing output. Raises ``ToolFailed`` (with stderr tail) on failure."""
    argv = [str(a) for a in args]
    full_env = None if env is None else {**os.environ, **env}
    try:
        cp = subprocess.run(argv, cwd=None if cwd is None else str(cwd), env=full_env, input=input_text,
                            capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError as e:
        raise ToolUnavailable(f"{what or argv[0]}: executable not found") from e
    except subprocess.TimeoutExpired as e:
        raise ToolFailed(f"{what or argv[0]}: timed out after {timeout}s") from e
    res = CmdResult(argv, cp.returncode, cp.stdout or "", cp.stderr or "")
    if check and cp.returncode != 0:
        tail = (res.stderr.strip() or res.stdout.strip())[-1500:]
        raise ToolFailed(f"{what or argv[0]} exited {cp.returncode}: {tail}")
    return res


def docker_available(docker: str = "docker") -> bool:
    if not shutil.which(docker):
        return False
    try:
        return subprocess.run([docker, "info"], capture_output=True, timeout=20).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def docker_wrap(image: str, argv: Sequence[str], mounts: Sequence[str | Path], *, docker: str = "docker",
                sudo: bool = False, extra: Sequence[str] = (), workdir: str | Path | None = None) -> list[str]:
    """Build ``docker run --rm -v p:p ... image argv``.

    Every host path is mounted at the identical path inside the container (the same trick
    Vaxign-ML's own wrapper script uses), so file arguments need no translation.
    """
    cmd: list[str] = (["sudo"] if sudo else []) + [docker, "run", "--rm"]
    seen: set[str] = set()
    for m in mounts:
        mp = str(Path(m).resolve())
        if mp not in seen:
            seen.add(mp)
            cmd += ["-v", f"{mp}:{mp}"]
    if workdir:
        cmd += ["-w", str(workdir)]
    cmd += list(extra) + [image] + [str(a) for a in argv]
    return cmd


def tool_version(argv: Sequence[str], pattern: str | None = None, timeout: float = 30) -> str | None:
    """Best-effort version string (first non-empty line, or first regex group). Never raises."""
    try:
        cp = subprocess.run([str(a) for a in argv], capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if cp.returncode != 0:                      # e.g. an unsupported --version prints usage text; that is not a version
        return None
    text = (cp.stdout or "") + "\n" + (cp.stderr or "")
    if pattern:
        m = re.search(pattern, text)
        return m.group(1) if m else None
    for line in text.splitlines():
        if line.strip():
            return line.strip()[:120]
    return None


def package_version(script: str | Path, package: str, timeout: float = 60) -> str | None:
    """Version of a pip-installed CLI's package, via the interpreter that sits next to its script
    (``<env>/bin/boltz`` -> ``<env>/bin/python``). Never raises."""
    py = Path(script).resolve().parent / "python"
    if not py.exists():
        return None
    try:
        cp = subprocess.run([str(py), "-c", f"import importlib.metadata as m; print(m.version({package!r}))"],
                            capture_output=True, text=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired):
        return None
    out = cp.stdout.strip()
    return out if cp.returncode == 0 and out else None


def tool_record(name: str, *, version: str | None, backend: str, fidelity: str, **extra) -> dict:
    """Provenance entry for an artifact's ``tools`` list.

    ``fidelity`` is ``reference`` (the tool the README names), ``alternative`` (an open-source
    stand-in for the named tool) or ``fallback`` (a transparent heuristic, NOT the named tool).
    """
    return {"name": name, "version": version, "backend": backend, "fidelity": fidelity, **extra}
