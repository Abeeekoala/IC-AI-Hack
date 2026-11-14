"""S1 cache-first acquisition against a fake UniProt / VFDB / Protegen server."""
from __future__ import annotations

import json

import pytest

from vaxforge import s1_acquire as S1
from vaxforge.artifacts import ContractError
from vaxforge.fasta import read_fasta

from conftest import make_config
from fakes import FakeRunner, build_server, proteome_json, write_s0


def run_s1(store, cfg, server, tmp_path, **kw):
    return S1.run(store, cfg, transport=server, runner=FakeRunner(tmp_path / "bdb"), **kw)


@pytest.fixture
def s0store(store):
    write_s0(store)
    return store


def test_happy_path_acquires_everything_and_records_it(s0store, cfg, tmp_path):
    server = build_server()
    runner = FakeRunner(tmp_path / "bdb")
    art = S1.run(s0store, cfg, transport=server, runner=runner)
    assert art.status == "ok", art.warnings
    p = art.payload

    prim = p["proteome"]
    assert prim["upid"] == "UP000000001" and prim["n_sequences"] == 3 and prim["status"] == "ok"
    assert prim["release"] == "2026_04" and p["proteome_selection"]["method"] == "reference_proteome"
    assert [r.id for r in read_fasta(s0store.resolve(prim["file"]))][0] == "sp|Q100001|P1_NEIMB"

    comp = p["comparison_proteome"]            # the 1-protein fragment (<60% of 3) must not win
    assert comp["upid"] == "UP000000002" and "other strain" in comp["selection_reason"]

    ref = p["reference_dbs"]
    assert ref["vfdb"]["set"] == "A" and ref["vfdb"]["n_sequences"] == 2
    assert ref["protegen"]["target_matches"] == 1 and "Neisseria meningitidis" in ref["protegen"]["target_names_searched"]
    assert [r.id for r in read_fasta(s0store.resolve(ref["protegen"]["target_file"]))] == ["Protegen:"]
    assert ref["human_proteome"]["reviewed_only"] is True and ref["human_proteome"]["n_sequences"] == 1
    assert ref["iedb"]["status"] == "disabled" and ref["afnd"]["status"] == "deferred_to_s6"

    assert sorted(runner.built) == ["comparison_proteome", "human_proteome", "proteome", "vfdb"]
    assert p["blast_dbs"]["vfdb"]["status"] == "ok" and p["blast_dbs"]["vfdb"]["fasta_sha256"]
    assert "prefix_hint" not in p["blast_dbs"]["vfdb"] and "vfdb" in art.volatile["blast_db_prefixes"]   # machine-specific -> volatile
    assert not any(art.volatile["cache_hits"].values())                                                  # first run: all downloaded
    assert p["resource_status"]["proteome"] == "ok" and p["resource_status"]["vfdb"] == "ok"
    assert s0store.verify() == []
    assert art.inputs.keys() == {"s0"}


def test_second_run_is_entirely_cache_hits(s0store, cfg, tmp_path):
    server = build_server()
    first = run_s1(s0store, cfg, server, tmp_path)
    n = len(server.calls)
    art = run_s1(s0store, cfg, server, tmp_path)
    hits = art.volatile["cache_hits"]
    assert hits["proteome"] and hits["vfdb"] and hits["protegen"] and hits["human_proteome"] and hits["comparison_proteome"]
    # only the already-cached JSON/FASTA were needed: zero new requests
    assert len(server.calls) == n
    # content-addressing: HOW the data was obtained must not change the digest, or re-running S1 would stale S2
    assert art.digest == first.digest


def test_refresh_of_unchanged_data_keeps_the_digest(s0store, cfg, tmp_path, monkeypatch):
    """Found live: --refresh re-downloaded identical data but changed the digest through 'retrieved_at', staling S2."""
    import itertools

    from vaxforge import cache as cache_mod, uniprot as uniprot_mod
    ticks = itertools.count(1)
    stamp = lambda: "2026-01-01T00:00:%02dZ" % next(ticks)               # every fetch gets a different clock reading
    for mod in (cache_mod, uniprot_mod, S1):
        monkeypatch.setattr(mod, "utcnow_iso", stamp)
    server = build_server()
    first = run_s1(s0store, cfg, server, tmp_path)
    cfg.set("cache.refresh", True)
    n = len(server.calls)
    again = run_s1(s0store, cfg, server, tmp_path)
    assert len(server.calls) > n and not any(again.volatile["cache_hits"].values())      # it really re-downloaded
    assert again.digest == first.digest                                                 # same data -> same digest
    assert again.volatile["retrieved_at"]["proteome"] != first.volatile["retrieved_at"]["proteome"]   # provenance moved
    assert "retrieved_at" not in again.payload["proteome"] and "retrieved_at" not in again.payload["reference_dbs"]["vfdb"]


def test_offline_after_warm_cache_works_and_cold_offline_fails_cleanly(s0store, cfg, tmp_path):
    run_s1(s0store, cfg, build_server(), tmp_path)
    cfg.set("network.offline", True)
    art = run_s1(s0store, cfg, build_server(), tmp_path)
    assert art.status == "ok" and art.volatile["offline"] is True

    cold = make_config(tmp_path / "cold", network={"offline": True})
    from vaxforge.artifacts import RunStore
    st = RunStore(tmp_path / "cold" / "run", cache_dir=cold.cache_dir)
    write_s0(st)
    art2 = S1.run(st, cold, transport=build_server(), runner=FakeRunner(tmp_path))
    assert art2.status == "failed" and "warm-cache" in art2.warnings[0]
    with pytest.raises(ContractError, match="failed"):
        art2.require_ok()


def test_virus_skips_vfdb_and_its_blast_db(store, cfg, tmp_path):
    write_s0(store, domain="virus", name="Severe acute respiratory syndrome coronavirus 2", gram="not_applicable")
    server = build_server(primary=proteome_json("UP000000001", 122586, "SARS2", "Reference proteome", 3), others=())
    runner = FakeRunner(tmp_path / "bdb")
    art = S1.run(store, cfg, transport=server, runner=runner)
    assert art.status == "ok"
    assert art.payload["reference_dbs"]["vfdb"]["status"] == "skipped_not_applicable"
    assert "vfdb" not in runner.built and art.payload["blast_dbs"]["vfdb"]["status"] == "skipped"
    assert art.payload["comparison_proteome"]["status"] == "unavailable"       # nothing comparable
    assert not any("VFDB" in w for w in art.warnings)


def test_optional_failure_is_reported_not_fatal(s0store, cfg, tmp_path):
    server = build_server(with_vfdb=False, with_protegen=False)
    art = run_s1(s0store, cfg, server, tmp_path)
    assert art.status == "ok"
    assert art.payload["resource_status"]["vfdb"] == "unavailable" and "reason" in art.payload["reference_dbs"]["vfdb"]
    assert art.payload["resource_status"]["protegen"] == "unavailable"
    assert any(w.startswith("vfdb unavailable") for w in art.warnings)
    assert art.payload["blast_dbs"]["vfdb"]["status"] == "skipped"


def test_no_proteome_found_is_a_clear_failure(s0store, cfg, tmp_path):
    art = run_s1(s0store, cfg, build_server(primary=None, others=()), tmp_path)
    assert art.status == "failed"
    assert "--proteome-fasta" in art.warnings[0] and art.payload["proteome"]["status"] == "unavailable"


def test_no_reference_proteome_falls_back_to_best_other(s0store, cfg, tmp_path):
    server = build_server(primary=None, others=(proteome_json("UP000000001", 5, "X strain", "Non Reference proteome", 3),
                                              proteome_json("UP000000002", 6, "Y strain", "Non Reference proteome", 3, busco=50)))
    art = run_s1(s0store, cfg, server, tmp_path)
    assert art.status == "ok" and art.payload["proteome_selection"]["method"] == "best_available_non_reference"
    assert art.payload["proteome"]["upid"] == "UP000000001"          # higher BUSCO wins


def test_pinned_upid_and_custom_fasta(s0store, cfg, tmp_path):
    server = build_server()
    server.add(lambda u: "/proteomes/UP000000002" in u and "search" not in u,
               json.dumps(proteome_json("UP000000002", 122587, "Z2491", "Non Reference proteome", 3)), headers={})
    art = run_s1(s0store, cfg, server, tmp_path, proteome_upid="UP000000002")
    assert art.payload["proteome"]["upid"] == "UP000000002" and art.payload["proteome_selection"]["method"] == "pinned"

    from vaxforge.fasta import FastaRecord, write_fasta
    mine = tmp_path / "mine.faa"
    write_fasta([FastaRecord("my1 test", "MKTAYIAKQR" * 5), FastaRecord("my2", "MSTNPKPQRK" * 4)], mine)
    art2 = run_s1(s0store, cfg, build_server(), tmp_path, proteome_fasta=mine)
    assert art2.payload["proteome"]["proteome_type"] == "user-supplied FASTA" and art2.payload["proteome"]["n_sequences"] == 2
    assert art2.payload["comparison_proteome"]["status"] == "skipped"


def test_disabled_resources_and_downstream_s2_contract(s0store, cfg, tmp_path):
    cfg.set("acquire.protegen.enabled", False)
    cfg.set("acquire.human.enabled", False)
    cfg.set("acquire.comparison.enabled", False)
    art = run_s1(s0store, cfg, build_server(), tmp_path)
    rs = art.payload["resource_status"]
    assert rs["protegen"] == "disabled" and rs["human_proteome"] == "disabled" and rs["comparison_proteome"] == "disabled"
    assert art.status == "ok"


def test_comparison_falls_back_when_best_candidate_has_no_downloadable_entries(s0store, cfg, tmp_path):
    """Found live: UniProt lists proteomes (e.g. UP000005526) that report a protein count but have no entries."""
    import urllib.parse
    ghost = proteome_json("UP000000009", 123, "ghost strain", "Non Reference proteome", 3, busco=99)   # ranks first
    server = build_server(others=(ghost, __import__("fakes").SECOND))
    server.routes.insert(0, (lambda u: "/uniprotkb/stream" in u and "UP000000009" in urllib.parse.unquote_plus(u), 200, {}, b""))
    art = run_s1(s0store, cfg, server, tmp_path)
    comp = art.payload["comparison_proteome"]
    assert art.status == "ok" and comp["upid"] == "UP000000002"
    assert comp["skipped_candidates"] and comp["skipped_candidates"][0].startswith("UP000000009")


def test_comparison_uses_uniparc_when_proteome_is_not_in_uniprotkb(s0store, cfg, tmp_path):
    """Found live: for N. meningitidis only the reference proteome is in UniProtKB; other strains are in UniParc."""
    import urllib.parse
    from fakes import SEQ_A, SEQ_B, SEQ_C, fasta
    other = proteome_json("UP000000009", 123, "Z2491-like strain", "Non Reference proteome", 3, busco=99)
    server = build_server(others=(other,))
    unq = lambda u: urllib.parse.unquote_plus(u)
    server.routes.insert(0, (lambda u: "/uniparc/stream" in u and "upid:UP000000009" in unq(u), 200, {},
                             fasta(("UPI0000000001 status=active", SEQ_A), ("UPI0000000002 status=active", SEQ_B),
                                   ("UPI0000000003 status=active", SEQ_C))))
    server.routes.insert(0, (lambda u: "/uniprotkb/stream" in u and "UP000000009" in unq(u), 200, {}, b""))
    art = run_s1(s0store, cfg, server, tmp_path)
    comp = art.payload["comparison_proteome"]
    assert comp["upid"] == "UP000000009" and comp["sequence_source"] == "uniparc" and comp["n_sequences"] == 3
    assert "UniParc" in comp["note"] and art.payload["proteome"]["sequence_source"] == "uniprotkb"
    assert art.payload["blast_dbs"]["comparison_proteome"]["status"] == "ok"
    first = [r.id for r in read_fasta(s0store.resolve(comp["file"]))][0]
    assert first == "UPI0000000001"
    # second run: served from cache, still recorded as UniParc
    art2 = run_s1(s0store, cfg, server, tmp_path)
    assert art2.payload["comparison_proteome"]["sequence_source"] == "uniparc" and art2.digest == art.digest


def test_comparison_unavailable_lists_what_was_tried(s0store, cfg, tmp_path):
    import urllib.parse
    ghost = proteome_json("UP000000009", 123, "ghost strain", "Non Reference proteome", 3)
    server = build_server(others=(ghost,))
    server.routes.insert(0, (lambda u: "/uniprotkb/stream" in u and "UP000000009" in urllib.parse.unquote_plus(u), 200, {}, b""))
    art = run_s1(s0store, cfg, server, tmp_path)
    comp = art.payload["comparison_proteome"]
    assert comp["status"] == "unavailable" and comp["skipped_candidates"] and art.status == "ok"
    assert any("comparison proteome" in w for w in art.warnings)


def test_protegen_name_matching_units():
    taxon = {"species_name": "Neisseria meningitidis", "scientific_name": "Neisseria meningitidis serogroup B",
             "other_names": [{"name": "Neisseria weichselbaumii (Trevisan 1889) X 1900", "class": "synonym"},
                             {"name": "ATCC 13077", "class": "type material"}, {"name": "MnB", "class": "acronym"}]}
    names = S1.protegen_target_names(taxon)
    assert "Neisseria weichselbaumii" in names and "ATCC 13077" not in names and "MnB" not in names   # <4 chars dropped
    assert S1._protegen_link('<a href="protegen-all-4.0-2019-01-09.faa">', "all") == "protegen-all-4.0-2019-01-09.faa"
    with pytest.raises(ValueError):
        S1._protegen_link("<html></html>", "all")
