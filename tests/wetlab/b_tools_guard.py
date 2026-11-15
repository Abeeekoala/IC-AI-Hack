"""One guard for the tests that need a machine on which none of stream B's tools can run.

Used by test_s8_order_fasta.py (the real S12 on a real chain) and test_streamc_upstream.py (B's real stages, degraded). Both run B's own stages
as subprocesses (`python -m vaxforge.s3_rank ...`) and pin what they write when no predictor exists, so they must be skipped when a tool is
USABLE: B's stages would run it and the chain would not be the one these tests build.

"Usable" is what B's stages decide for themselves. A native executable on PATH counts. `docker` counts only when its daemon answers
`docker info` (B's `prediction.tools.docker_available` asks exactly that, and so does `vaxforge.tools.docker_state`). Finding a `docker`
executable proves nothing: with Docker Desktop stopped the executable exists and no container can run, and the old guard (`shutil.which`
alone) skipped these tests on every such machine.

This is a plain module and not a conftest.py on purpose: other test modules of this repository do `from conftest import ...`, and a second
module of that name on sys.path shadows theirs (tests/stream_b/b_fixtures.py keeps the same rule). Each test module imports from here.
"""

from __future__ import annotations

import importlib.util
import shutil

import pytest

from vaxforge.tools import docker_state

# Programs that B's stages look for on PATH; an install is taken as usable.
NATIVE_TOOLS = ("blastp", "makeblastdb", "netMHCpan", "netMHCIIpan", "algpred2", "toxinpred2", "boltz", "colabfold_batch")
# Python packages that B's stages use when they can be imported.
PYTHON_TOOLS = ("mhcflurry",)
DOCKER_PROBE_TIMEOUT_S = 5.0  # `docker info` against a stopped daemon fails at once; a hung one must not stall test collection


def usable_b_tools() -> list[str]:
    """What B's stages could really run here, one short phrase each. An empty list is the tool-less machine the guarded tests need."""
    found = [f"{tool} on PATH" for tool in NATIVE_TOOLS if shutil.which(tool)]
    found += [f"{package} importable" for package in PYTHON_TOOLS if importlib.util.find_spec(package) is not None]
    docker = shutil.which("docker")
    if docker:
        # image=None: B's own check is `docker info` and nothing else, so a running daemon means B's stages would start a container
        # (and pull its image); a stopped daemon, whatever executable sits on PATH, means they cannot.
        state, detail = docker_state(docker, None, timeout=DOCKER_PROBE_TIMEOUT_S)
        if state == "ok":
            found.append(detail)
    return found


USABLE = usable_b_tools()  # probed once per test process, when the first test module that needs it is imported

needs_toolless_machine = pytest.mark.skipif(
    bool(USABLE),
    reason=f"a stream B tool is usable here ({', '.join(USABLE)}), so B's real stages would run it and the chain would not be the "
           "degraded, tool-less one this test pins")
