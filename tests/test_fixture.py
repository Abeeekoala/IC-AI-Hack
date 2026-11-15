"""The committed fixture run is what streams B, C and D build against: keep it valid and honest."""
from __future__ import annotations

import csv

import pytest

from vaxforge import s2_annotate as S2
from vaxforge.artifacts import RunStore, iter_file_refs

from conftest import ROOT

FIX = ROOT / "fixtures" / "nmb_fixture"
pytestmark = pytest.mark.skipif(not FIX.is_dir(), reason="fixture not built (python scripts/make_fixture.py)")


@pytest.fixture(scope="module")
def run():
    return RunStore(FIX)


def test_fixture_chain_and_files_verify_with_no_cache_present(run):
    assert run.stages_present() == ["s0", "s1", "s2"]
    assert run.verify() == []                      # digests, upstream chain, every referenced file and hash
    for stage in ("s0", "s1", "s2"):
        art = run.read_artifact(stage)
        assert art.status == "ok"
        assert all(ref["root"] == "run" for ref in iter_file_refs(art.payload)), "fixtures must not point into a cache"


def test_fixture_carries_no_protegen_data(run):
    assert not (FIX / "data" / "protegen_target.faa").exists()
    s1 = run.read_artifact("s1").payload
    assert "target_file" not in s1["reference_dbs"]["protegen"]


def test_fixture_s2_table_matches_the_documented_columns(run):
    s2 = run.read_artifact("s2").payload
    assert s2["columns"] == S2.TSV_COLUMNS
    with open(run.resolve(s2["tsv"]), encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh, delimiter="\t"))
    assert len(rows) == len(s2["proteins"]) == s2["summary"]["n_proteins"] == 40
    assert list(rows[0]) == S2.TSV_COLUMNS


def test_fixture_contains_the_bexsero_edge_cases(run):
    prots = {p["accession"]: p for p in run.read_artifact("s2").payload["proteins"]}
    assert {"Q9JXV4", "Q9JXK7", "Q7DD37", "P0DH58"} <= prots.keys()           # fHbp, NadA, NHBA, PorA
    fhbp, nhba = prots["Q9JXV4"]["virulence"], prots["Q7DD37"]["virulence"]
    assert fhbp["evidence"] == "strong" and fhbp["best_hit"]["identity_pct"] > 99
    assert nhba["status"] == "no_hit" and nhba["is_virulence_associated"] is False      # a true negative stays visible


def test_fixture_localisation_is_flagged_as_stand_in_and_keeps_unknown(run):
    s2 = run.read_artifact("s2")
    assert s2.warnings[0].startswith("FIXTURE:")
    locs = [p["localisation"] for p in s2.payload["proteins"]]
    assert {x["status"] for x in locs} == {"fixture_stand_in"} and all("fixture_stand_in" in x["source"] for x in locs)
    assert any(x["label"] == "Unknown" and x["accessibility"] == "unknown" for x in locs)       # Unknown is not collapsed
    assert {x["accessibility"] for x in locs} >= {"accessible", "unknown"}
    assert s2.payload["tools"]["localisation"]["tool"] == "NOT PSORTb"


def test_fixture_s0_and_s1_have_the_fields_downstream_reads(run):
    s0 = run.read_artifact("s0").payload
    assert s0["taxon"]["taxid"] == 487 and s0["organism"]["gram_stain"] == "negative"
    assert s0["organism"]["psortb_flag"] == "-n" and s0["organism"]["psortb_extra_args"] == []
    s1 = run.read_artifact("s1").payload
    assert s1["proteome"]["upid"] == "UP000000425" and s1["proteome"]["n_sequences"] == 40
    assert s1["proteome"]["fixture_subset"]["of_n_sequences"] == 2003


def test_s2_on_fixture_without_vfdb_cache_file_is_not_run_not_a_crash(tmp_path):
    import shutil
    from vaxforge import tools
    from vaxforge.config import load_config

    dst = tmp_path / "run"
    shutil.copytree(FIX, dst)
    (dst / "s2_annotate.json").unlink()
    store = RunStore(dst)
    s1 = store.read_artifact("s1").payload
    s1["reference_dbs"]["vfdb"].pop("cache_file", None)
    org = store.read_artifact("s0").payload["organism"]
    cfg = load_config()
    runner = tools.BlastRunner(cfg, tmp_path / "cache")
    warnings: list[str] = []
    blocks, info = S2.run_virulence(cfg, store, None, s1, runner, [], "x", org, warnings)
    assert info["status"] == "not_run" and "no VFDB cache file" in info["reason"]


def test_docker_state_reports_missing_and_dead_daemon():
    from vaxforge import tools

    assert tools.docker_state(None)[0] == "missing"
    state, detail = tools.docker_state("definitely-not-a-docker-binary-xyz", "img")
    assert state == "no_daemon" and "daemon not running" in detail
