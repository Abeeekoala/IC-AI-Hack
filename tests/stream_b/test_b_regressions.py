"""Regression tests for bugs found while reviewing the stages."""
import b_fixtures  # adds src to sys.path
from b_fixtures import cfg_fixture, freq_file_fixture, run_dir_fixture

_FIXTURES = (cfg_fixture, freq_file_fixture, run_dir_fixture)   # pytest registers fixtures found as module attributes (names set in b_fixtures)
_ = b_fixtures
import pytest

from b_fakes import FakeScores
from vaxforge import s3_rank
from vaxforge.prediction import artifacts as art
from vaxforge.prediction.errors import ToolUnavailable
from vaxforge.s4_filter import db_prefix


def test_blast_db_prefix_changes_when_the_fasta_changes(tmp_path):
    f = tmp_path / "human.fa"
    f.write_text(">a\nMKV\n")
    p1 = db_prefix(tmp_path, f, "human")
    assert db_prefix(tmp_path, f, "human") == p1                       # stable for an unchanged file
    f.write_text(">a\nMKVLLL\n")
    assert db_prefix(tmp_path, f, "human") != p1                       # a stale database must never be reused
    with pytest.raises(ToolUnavailable, match="not found"):
        db_prefix(tmp_path, tmp_path / "missing.fa", "x")


def test_rule_based_fallback_artifact_does_not_shadow_a_later_vaxign_run(run_dir, cfg, monkeypatch):
    monkeypatch.setattr(s3_rank, "docker_available", lambda *_: False)
    s3_rank.run(run_dir, cfg)                                          # Docker down -> fallback artifact
    assert art.read_artifact(run_dir / "s3_rank.json")["payload"]["ranker"]["backend"] == "subtractive_filter"
    monkeypatch.setattr(s3_rank, "_make_backend", lambda c, name: FakeScores() if name == "vaxign_ml" else None)
    s3_rank.run(run_dir, cfg)                                          # Docker "up": must recompute, not reuse
    assert art.read_artifact(run_dir / "s3_rank.json")["payload"]["ranker"]["backend"] == "vaxign_ml"


# ---- found by running against real UniProt on 2026-11-14 ---------------------------------
def _fetch_factory(table):
    """table: substring-of-url -> body bytes. Records urls."""
    from vaxforge.prediction.refdata import Fetched
    seen = []

    def fetch(url):
        seen.append(url)
        for key, body in table.items():
            if key in url:
                return Fetched(body, {})
        raise AssertionError(f"unexpected url {url}")
    fetch.seen = seen
    return fetch


def test_non_reference_proteome_falls_back_to_uniparc_instead_of_caching_an_empty_file(tmp_path):
    from vaxforge.prediction.refdata import RefData
    f = _fetch_factory({"uniprotkb/stream": b"", "uniparc/stream": b">UPI000002EFA4 status=active\nMKV\n"})
    p = RefData(tmp_path, fetch=f).proteome_fasta("UP000000626")
    assert p.read_text().startswith(">UPI000002EFA4") and any("uniparc" in u for u in f.seen)


def test_empty_proteome_is_an_error_and_is_never_cached(tmp_path):
    from vaxforge.prediction.errors import ToolFailed
    from vaxforge.prediction.refdata import RefData
    rd = RefData(tmp_path, fetch=_fetch_factory({"uniprotkb/stream": b"", "uniparc/stream": b""}))
    with pytest.raises(ToolFailed, match="empty file"):
        rd.proteome_fasta("UP000000626")
    assert not (tmp_path / "refdata" / "proteome_UP000000626.fasta").exists()


def test_strain_selection_skips_proteomes_without_sequences(tmp_path):
    from vaxforge.prediction.refdata import RefData
    listing = b"Proteome Id\tOrganism\tOrganism Id\tProtein count\nUP1\tbig empty\t11\t3000\nUP2\tgood\t12\t2000\nUP3\ttarget\t13\t1900\n"

    class Fake:
        def __call__(self, url):
            from vaxforge.prediction.refdata import Fetched
            if "proteomes/search" in url:
                return Fetched(listing, {})
            if "UP1" in url:
                return Fetched(b"", {})
            return Fetched(b">x\nMKV\n", {})
    rd = RefData(tmp_path, fetch=Fake())
    out = rd.strain_proteomes(487, exclude_organism_ids=(13,), n=1)
    assert [label.split()[0] for label, _ in out] == ["UP2"]
    assert any(p.get("skipped_proteome") == "UP1" for p in rd.provenance)


def test_species_proteome_listing_is_cached_for_offline_use(tmp_path):
    from vaxforge.prediction.refdata import Fetched, RefData
    calls = []
    body = b"Proteome Id\tOrganism\tOrganism Id\tProtein count\nUP2\tgood\t12\t2000\n"

    def fetch(url):
        calls.append(url)
        return Fetched(body, {})
    RefData(tmp_path, fetch=fetch).species_proteomes(487)
    refs = RefData(tmp_path, offline=True, fetch=fetch).species_proteomes(487)        # offline: must come from the cache
    assert len(calls) == 1 and [r.upid for r in refs] == ["UP2"]


def test_tool_version_ignores_usage_text_from_unsupported_flags(tmp_path):
    from vaxforge.prediction.tools import package_version, tool_version
    cli = tmp_path / "fake_boltz.py"
    cli.write_text("import sys\nprint('Usage: boltz [OPTIONS] COMMAND [ARGS]...'); sys.exit(2)\n")
    assert tool_version(["python3", str(cli), "--version"]) is None                  # was recorded as the version before
    ok = tmp_path / "ok.py"
    ok.write_text("print('tool 1.2.3')\n")
    assert tool_version(["python3", str(ok)], r"tool ([\d.]+)") == "1.2.3"
    assert package_version(tmp_path / "nothing_here", "boltz") is None


def test_s6_uses_a_cached_frequency_table_when_offline(run_dir, cfg, tmp_path, freq_file):
    import shutil
    from b_fakes import FakeBepi, FakeBinder, FakeDisco, FakeGate, FakeScores
    from vaxforge import s3_rank, s4_filter, s5_epitopes, s6_coverage
    from vaxforge.prediction.config import deep_merge
    from vaxforge.prediction.refdata import RefData
    s3_rank.run(run_dir, cfg, backend=FakeScores())
    s4_filter.run(run_dir, cfg, gates={g: FakeGate(g) for g in s4_filter.GATES})
    s5_epitopes.run(run_dir, cfg, class_i=FakeBinder("I"), class_ii=FakeBinder("II"), bcell=FakeBepi(), discotope=FakeDisco(),
                    structure_lookup=lambda pid: None)
    cache = tmp_path / "cache"
    cfg2 = deep_merge(cfg, {"refdata": {"cache_dir": str(cache), "offline": True}})
    # pre-warm: put the table where RefData.afnd_table() would have written it
    rd = RefData(cache, offline=True)
    import hashlib
    tag = hashlib.sha1("|".join(cfg2["s6"]["regions"]).encode()).hexdigest()[:8]
    dest = cache / "refdata" / "afnd" / f"iedb_popcov_{tag}.tsv"
    dest.parent.mkdir(parents=True)
    shutil.copy(freq_file, dest)
    s6_coverage.run(run_dir, cfg2, refdata=rd)
    assert load_s6(run_dir)["payload"]["analysis"]["status"] == "ran"


def load_s6(run_dir):
    from vaxforge.prediction import artifacts as art
    return art.read_artifact(run_dir / "s6_coverage.json")
