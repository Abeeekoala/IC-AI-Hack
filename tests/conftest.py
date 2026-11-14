from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vaxforge.artifacts import RunStore  # noqa: E402
from vaxforge.config import Config  # noqa: E402
from vaxforge.testing import ReplayTransport  # noqa: E402

DATA = ROOT / "tests" / "data"


def make_config(tmp_path: Path, **sections) -> Config:
    data = {
        "paths": {"cache_dir": str(tmp_path / "cache"), "runs_dir": str(tmp_path / "runs")},
        "network": {"retries": 0, "min_interval_s": {"eutils.ncbi.nlm.nih.gov": 0, "rest.uniprot.org": 0}},
    }
    for k, v in sections.items():
        data.setdefault(k, {}).update(v)
    return Config(data, root=tmp_path)


@pytest.fixture
def cfg(tmp_path):
    return make_config(tmp_path)


@pytest.fixture
def store(tmp_path):
    return RunStore(tmp_path / "runs" / "t", cache_dir=tmp_path / "cache")


@pytest.fixture
def s0_replay():
    return ReplayTransport(DATA / "http_s0")
