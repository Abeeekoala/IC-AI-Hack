import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]

import pytest  # noqa: E402
from vaxforge import cli, validate_cli  # noqa: E402


def entry(rank, pid, gene="", name=""):
    return {"protein_id": pid, "gene": gene, "name": name, "rank": rank, "composite_score": 1.0 - 0.01 * rank, "protegenicity": None}


def test_validate_lopov_forwards_its_arguments_to_the_tool(tmp_path, capsys):
    src = tmp_path / "in.json"
    src.write_text(json.dumps({"proteins": {"A": ["a1", "a2", "a3"], "B": ["b1", "b2", "b3"]}, "positives": ["a1", "b2"]}))
    assert cli.main(["validate", "lopov", str(src), "--k", "1", "2"]) == 0
    out = capsys.readouterr().out
    assert "2 pathogens" in out  # lopov's own summary line


def test_validate_bexsero_loads_even_though_the_tool_imports_lopov_by_name(tmp_path, capsys):
    es = [entry(i, f"sp|F{i:03d}|FILL", f"g{i}", f"Filler protein {i}") for i in range(1, 11)]
    es[1] = entry(2, "sp|FHBP|FHBP_NEIMB", "fhbP", "Factor H binding protein")
    s3 = tmp_path / "s3.json"
    s3.write_text(json.dumps({"stage": "s3", "payload": {"context": {"taxid": 487}, "ranker": {"backend": "test"},
                                                          "leakage_audit": {"holdout_enforced": True}, "ranking": es}}))
    before = list(sys.path)
    assert cli.main(["validate", "bexsero", str(s3), "--k", "3"]) == 0
    assert "fHbp" in capsys.readouterr().out
    assert sys.path == before  # the path is only extended while loading (other tests may already have added it themselves)


def test_a_missing_checkout_is_a_clear_error(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(validate_cli, "VALIDATION", tmp_path)
    assert cli.main(["validate", "lopov", "x.json", "--k", "1"]) == 1
    assert "needs a source checkout" in capsys.readouterr().err
