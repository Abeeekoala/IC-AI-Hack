"""The guard of the "tool-less machine" tests (b_tools_guard.py): usable tools skip them, an unusable `docker` executable does not.

The case that matters is a `docker` executable on PATH whose daemon is stopped (Docker Desktop not started): the old guard looked for the
executable only and skipped four tests there, although no tool could run. The executables below are tiny stand-ins written under `tmp_path` and
put first on PATH; they only exit with a given code, which is all `docker info` is asked for.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

import b_tools_guard
import pytest
from b_tools_guard import needs_toolless_machine, usable_b_tools


def put_on_path(monkeypatch, folder: Path, name: str, exit_code: int = 0) -> str:
    """An executable `name` that does nothing but exit with `exit_code`, first on PATH. Returns what `shutil.which` now finds for it."""
    if os.name == "nt":
        (folder / f"{name}.cmd").write_text(f"@echo off\r\nexit /b {exit_code}\r\n", encoding="ascii")
    else:
        stand_in = folder / name
        stand_in.write_text(f"#!/bin/sh\nexit {exit_code}\n", encoding="ascii")
        stand_in.chmod(0o755)
    monkeypatch.setenv("PATH", str(folder) + os.pathsep + os.environ.get("PATH", ""))
    found = shutil.which(name)
    assert found and Path(found).parent == folder  # the stand-in, not a real install further down PATH
    return found


@pytest.fixture
def only_docker(monkeypatch):
    """Looks at nothing but `docker`: the machine's own tools (a developer may have blastp) must not decide these tests."""
    monkeypatch.setattr(b_tools_guard, "NATIVE_TOOLS", ())
    monkeypatch.setattr(b_tools_guard, "PYTHON_TOOLS", ())


def test_a_docker_executable_with_a_stopped_daemon_is_not_a_usable_tool(tmp_path, monkeypatch, only_docker):
    put_on_path(monkeypatch, tmp_path, "docker", exit_code=1)  # `docker info` fails: "Cannot connect to the Docker daemon"
    # what the old guard asked, and answered "a tool is here" to:
    assert shutil.which("docker") is not None
    assert usable_b_tools() == []  # the new one asks the daemon: nothing can run, so the tool-less tests may run


def test_a_docker_daemon_that_answers_is_a_usable_tool(tmp_path, monkeypatch, only_docker):
    put_on_path(monkeypatch, tmp_path, "docker", exit_code=0)
    assert usable_b_tools() == ["docker daemon running"]


def test_docker_is_asked_for_its_daemon_only_and_with_a_short_timeout(tmp_path, monkeypatch, only_docker):
    exe = put_on_path(monkeypatch, tmp_path, "docker", exit_code=1)
    calls = []

    def spy(docker, image=None, timeout=10.0):
        calls.append((docker, image, timeout))
        return "no_daemon", "stand-in"

    monkeypatch.setattr(b_tools_guard, "docker_state", spy)
    assert usable_b_tools() == []
    assert calls == [(exe, None, b_tools_guard.DOCKER_PROBE_TIMEOUT_S)]  # no image: B's own check is `docker info` alone
    assert 0 < b_tools_guard.DOCKER_PROBE_TIMEOUT_S <= 10


def test_no_docker_at_all_is_not_a_usable_tool(tmp_path, monkeypatch, only_docker):
    monkeypatch.setenv("PATH", str(tmp_path))  # an empty folder: nothing is found anywhere
    assert shutil.which("docker") is None and usable_b_tools() == []


def test_a_native_tool_on_path_is_usable_whatever_docker_does(tmp_path, monkeypatch):
    put_on_path(monkeypatch, tmp_path, "docker", exit_code=1)  # daemon stopped
    put_on_path(monkeypatch, tmp_path, "blastp", exit_code=0)
    assert "blastp on PATH" in usable_b_tools()
    assert not any(item.startswith("docker") for item in usable_b_tools())


def test_an_importable_python_tool_is_usable(tmp_path, monkeypatch, only_docker):
    package = "b_tools_guard_stand_in_package"  # stands for mhcflurry: a package that can be found on sys.path
    monkeypatch.setattr(b_tools_guard, "PYTHON_TOOLS", (package,))
    assert usable_b_tools() == []
    (tmp_path / package).mkdir()
    (tmp_path / package / "__init__.py").write_text("", encoding="ascii")
    monkeypatch.syspath_prepend(str(tmp_path))
    assert usable_b_tools() == [f"{package} importable"]
    assert package not in sys.modules  # found, never imported


def test_the_tool_lists_still_hold_every_tool_the_old_guard_looked_for():
    old = {"blastp", "makeblastdb", "docker", "netMHCpan", "netMHCIIpan", "algpred2", "toxinpred2", "boltz", "colabfold_batch"}
    assert set(b_tools_guard.NATIVE_TOOLS) | {"docker"} == old and b_tools_guard.PYTHON_TOOLS == ("mhcflurry",)  # narrower would be a weaker guard


def test_the_marker_skips_exactly_when_something_is_usable_and_says_what():
    mark = needs_toolless_machine.mark
    assert mark.name == "skipif" and mark.args == (bool(b_tools_guard.USABLE),)
    if b_tools_guard.USABLE:
        assert all(item in mark.kwargs["reason"] for item in b_tools_guard.USABLE)
