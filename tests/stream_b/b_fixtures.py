"""Stream B's shared pytest fixtures, imported explicitly by each test module.

There is deliberately NO conftest.py in tests/stream_b: another stream's tests do ``from conftest import ...`` and a second module named
``conftest`` on sys.path would shadow theirs (found by scripts/stream_b_merge_dry_run.py). Importing this module also puts ``src`` on
sys.path, so these tests run without a pytest.ini or pyproject entry.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

pytest.importorskip("numpy")                              # without numpy these tests skip cleanly instead of breaking collection
from b_fakes import FREQ_TSV, make_s2                      # noqa: E402


@pytest.fixture(name="run_dir")
def run_dir_fixture(tmp_path):
    d = tmp_path / "runs" / "synthetic"
    d.mkdir(parents=True)
    make_s2(d / "s2_annotate.json")
    return d


@pytest.fixture(name="freq_file")
def freq_file_fixture(tmp_path):
    p = tmp_path / "freq.tsv"
    p.write_text(FREQ_TSV)
    return p


@pytest.fixture(name="cfg")
def cfg_fixture(tmp_path):
    from vaxforge.prediction.config import load_config
    return load_config(overrides={
        "refdata": {"cache_dir": str(tmp_path / "cache"), "offline": True},
        "s3": {"min_candidates": 4, "max_candidates": 10},
        "s5": {"class_i": {"panel": ["HLA-A*01:01", "HLA-A*02:01", "HLA-A*24:02", "HLA-B*07:02", "HLA-B*35:01", "HLA-B*58:01"]},
               "class_ii": {"panel": ["HLA-DRB1*01:01", "HLA-DRB1*07:01", "HLA-DRB1*15:01", "HLA-DQA1*05:01/DQB1*02:01",
                                      "HLA-DQA1*03:01/DQB1*03:02"]}},
        "s6": {"regions": ["World", "Europe", "East Asia", "West Africa"], "fairness": {"budget_k": 3}},
        "s7": {"top_k": 4},
    })
