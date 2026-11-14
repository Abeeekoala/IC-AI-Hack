"""S0 taxonomy resolution, replayed offline against recorded NCBI/UniProt responses."""
from __future__ import annotations

import pytest

from vaxforge import s0_taxonomy as S0
from vaxforge.cache import CacheMiss
from vaxforge.testing import FakeTransport


def run_s0(store, cfg, replay, pathogen=None, taxid=None, chooser=None):
    return S0.run(store, cfg, pathogen=pathogen, taxid=taxid, chooser=chooser, transport=replay)


# ------------------------------------------------------------------ resolution outcomes
def test_exact_species_resolves_and_classifies_organism(store, cfg, s0_replay):
    art = run_s0(store, cfg, s0_replay, "Neisseria meningitidis")
    p = S0.require_resolved(art)
    assert art.status == "ok"
    assert p["taxon"]["taxid"] == 487 and p["taxon"]["rank"] == "species" and p["taxon"]["species_taxid"] == 487
    assert p["resolution"]["method"] == "exact" and p["resolution"]["synonym_matched"] is False
    org = p["organism"]
    assert org["domain"] == "bacteria" and org["gram_stain"] == "negative" and org["psortb_flag"] == "-n"
    assert org["virulence_db_applicable"] and org["localisation_applicable"]
    assert any("taxa below species level" in n for n in p["resolution"]["notes"])   # strain info surfaced
    assert store.verify() == []


def test_genus_is_never_silently_resolved(store, cfg, s0_replay):
    art = run_s0(store, cfg, s0_replay, "Neisseria")
    assert art.status == "needs_user_input"
    res = art.payload["resolution"]
    assert res["reason"] == "rank_too_broad" and art.payload["taxon"] is None and art.payload["organism"] is None
    ids = {c["taxid"] for c in art.payload["candidates"]}
    assert 15 >= len(ids) > 3 and all(c["rank"] == "species" for c in art.payload["candidates"])
    with pytest.raises(S0.NeedsUserInput):
        S0.require_resolved(art)
    assert "Neisseria" in res["message"]


def test_renamed_species_resolves_through_synonym_and_says_so(store, cfg, s0_replay):
    art = run_s0(store, cfg, s0_replay, "Clostridium difficile")
    p = S0.require_resolved(art)
    assert p["taxon"]["taxid"] == 1496 and p["taxon"]["scientific_name"] == "Clostridioides difficile"
    r = p["resolution"]
    assert r["synonym_matched"] is True and r["matched_name"].startswith("Clostridium difficile")
    assert any("current scientific name" in n for n in r["notes"])
    assert p["organism"]["gram_stain"] == "positive"


def test_abbreviation_and_alias(store, cfg, s0_replay, tmp_path):
    art = run_s0(store, cfg, s0_replay, "N. meningitidis")
    assert S0.require_resolved(art)["taxon"]["taxid"] == 487
    assert art.payload["resolution"]["method"] == "abbreviation"

    from vaxforge.artifacts import RunStore
    art2 = run_s0(RunStore(tmp_path / "runs" / "b"), cfg, s0_replay, "meningococcus")
    assert S0.require_resolved(art2)["taxon"]["taxid"] == 487 and art2.payload["resolution"]["method"] == "alias"

    art3 = run_s0(RunStore(tmp_path / "runs" / "c"), cfg, s0_replay, "Staph aureus")
    assert S0.require_resolved(art3)["taxon"]["scientific_name"] == "Staphylococcus aureus"


def test_typo_returns_suggestion_not_a_guess(store, cfg, s0_replay):
    art = run_s0(store, cfg, s0_replay, "Neiseria meningitidis")
    assert art.status == "needs_user_input" and art.payload["resolution"]["reason"] == "spelling_suggestion"
    assert [c["taxid"] for c in art.payload["candidates"]] == [487]


def test_chooser_confirms_a_suggestion(store, cfg, s0_replay):
    seen = {}

    def chooser(res):
        seen["reason"] = res.reason
        return res.candidates[0]["taxid"]

    art = run_s0(store, cfg, s0_replay, "Neiseria meningitidis", chooser=chooser)
    assert seen["reason"] == "spelling_suggestion"
    assert art.status == "ok" and art.payload["taxon"]["taxid"] == 487
    r = art.payload["resolution"]
    assert r["method"] == "user_choice" and r["user_confirmed"] is True
    assert art.payload["query"] == "Neiseria meningitidis"       # original text is preserved


def test_chooser_declining_leaves_unresolved(store, cfg, s0_replay):
    art = run_s0(store, cfg, s0_replay, "Neisseria", chooser=lambda res: None)
    assert art.status == "needs_user_input"


def test_virus_has_no_gram_stain_and_no_vfdb(store, cfg, s0_replay):
    art = run_s0(store, cfg, s0_replay, "SARS-CoV-2")
    p = S0.require_resolved(art)
    assert p["taxon"]["taxid"] == 2697049 and p["taxon"]["rank"] == "no rank"
    # NCBI now files SARS-CoV-2 under the species 'Betacoronavirus pandemicum'; the strain-level taxid is kept
    assert p["taxon"]["species_taxid"] == 3418604 and p["taxon"]["species_name"] == "Betacoronavirus pandemicum"
    org = p["organism"]
    assert org["domain"] == "virus" and org["gram_stain"] == "not_applicable"
    assert org["psortb_flag"] is None and not org["virulence_db_applicable"] and not org["localisation_applicable"]
    # "SARS-CoV-2" is an acronym of the current name, not an old name: must not be reported as a renaming
    assert p["resolution"]["matched_name_class"] == "acronym" and p["resolution"]["synonym_matched"] is False


def test_unknown_name_fails_with_message_not_exception(store, cfg, s0_replay):
    art = run_s0(store, cfg, s0_replay, "zzqx notanorganism")
    assert art.status == "failed" and art.payload["taxon"] is None and art.payload["candidates"] == []
    assert "No taxon matches" in art.payload["resolution"]["message"]


def test_explicit_taxid_and_digit_query(store, cfg, s0_replay, tmp_path):
    art = run_s0(store, cfg, s0_replay, taxid=487)
    assert S0.require_resolved(art)["taxon"]["taxid"] == 487 and art.payload["resolution"]["method"] == "taxid"
    from vaxforge.artifacts import RunStore
    art2 = run_s0(RunStore(tmp_path / "runs" / "d"), cfg, s0_replay, "taxid:487")
    assert S0.require_resolved(art2)["taxon"]["taxid"] == 487


def test_second_run_is_served_from_cache(store, cfg, s0_replay):
    run_s0(store, cfg, s0_replay, "Neisseria meningitidis")
    n_first = len(s0_replay.calls)
    assert n_first > 0
    run_s0(store, cfg, s0_replay, "Neisseria meningitidis")
    assert len(s0_replay.calls) == n_first          # nothing new fetched


def test_offline_with_cold_cache_fails_clearly(store, cfg):
    cfg.set("network.offline", True)
    with pytest.raises(CacheMiss, match="warm-cache"):
        S0.run(store, cfg, pathogen="Neisseria meningitidis", transport=FakeTransport())


def test_offline_after_warm_cache_works(store, cfg, s0_replay):
    run_s0(store, cfg, s0_replay, "Neisseria meningitidis")
    cfg.set("network.offline", True)
    art = S0.run(store, cfg, pathogen="Neisseria meningitidis", transport=FakeTransport())
    assert art.status == "ok"


# ------------------------------------------------------------------ units
def test_strip_authority_and_scoring():
    assert S0.strip_authority("Clostridium difficile (Hall and O'Toole 1935) Prevot 1938 (Approved Lists 1980)") == "Clostridium difficile"
    assert S0.strip_authority("Neisseria meningitidis serogroup B (strain ATCC BAA-335 / MC58)").endswith("MC58)")
    assert S0.score_name("Neisseria meningitidis", "neisseria  meningitidis") == 1.0
    assert S0.score_name("N. meningitidis", "Neisseria meningitidis") == pytest.approx(0.9)
    assert S0.score_name("Staphylococcus", "Staphylococcus aureus") == pytest.approx(0.75)
    assert S0.score_name("zzz", "Neisseria meningitidis") < 0.4


def _tax(rank, *lineage_names, name="X y", division="Bacteria"):
    return {"taxid": 1, "scientific_name": name, "rank": rank, "division": division,
            "lineage": [{"taxid": i, "name": n, "rank": "no rank"} for i, n in enumerate(lineage_names)], "other_names": []}


@pytest.mark.parametrize("lineage,gram,flag", [
    (("cellular organisms", "Bacteria", "Pseudomonadota"), "negative", "-n"),
    (("cellular organisms", "Bacteria", "Bacillota", "Bacilli"), "positive", "-p"),
    (("cellular organisms", "Bacteria", "Bacillota", "Negativicutes"), "negative", "-n"),     # more specific wins
    # PSORTb's own advice for organisms whose Gram stain does not match their cell envelope:
    (("cellular organisms", "Bacteria", "Bacillota", "Mollicutes"), "negative_without_outer_membrane", "-p"),
    (("cellular organisms", "Bacteria", "Actinomycetota"), "positive", "-p"),
    (("cellular organisms", "Bacteria", "Actinomycetota", "Actinomycetes", "Mycobacteriales", "Mycobacteriaceae"),
     "positive_with_outer_membrane", "-n"),                                              # Mycobacterium tuberculosis
    (("cellular organisms", "Bacteria", "Deinococcota"), "positive_with_outer_membrane", "-n"),
    (("cellular organisms", "Bacteria", "SomeNewPhylum"), "unknown", None),
    (("cellular organisms", "Archaea", "Euryarchaeota"), "archaea", "-a"),
    (("cellular organisms", "Eukaryota", "Apicomplexa"), "not_applicable", None),
    (("Viruses", "Riboviria"), "not_applicable", None),
])
def test_gram_classification_rules(lineage, gram, flag):
    org = S0.classify_organism(_tax("species", *lineage, division="Viruses" if lineage[0] == "Viruses" else "Bacteria"))
    assert (org["gram_stain"], org["psortb_flag"]) == (gram, flag)
    assert org["gram_basis"]


def test_psortb_extra_args_only_for_organisms_that_need_them():
    mollicutes = S0.classify_organism(_tax("species", "cellular organisms", "Bacteria", "Mycoplasmatota", "Mollicutes"))
    assert mollicutes["psortb_flag"] == "-p" and mollicutes["psortb_extra_args"] == ["--x-skip-localization=Cellwall"]
    assert "Gram-negative without an outer membrane" in mollicutes["gram_basis"]
    for lineage in (("cellular organisms", "Bacteria", "Pseudomonadota"), ("cellular organisms", "Bacteria", "Bacillota"),
                    ("cellular organisms", "Archaea")):
        assert S0.classify_organism(_tax("species", *lineage))["psortb_extra_args"] == []
    assert S0.classify_organism(_tax("species", "Viruses", "Riboviria", division="Viruses"))["psortb_extra_args"] == []


def test_xml_parser_reads_synonyms_and_drops_noise():
    xml = b"""<TaxaSet><Taxon><TaxId>9</TaxId><ScientificName>New name</ScientificName><ParentTaxId>1</ParentTaxId>
    <Rank>species</Rank><Division>Bacteria</Division><OtherNames><Synonym>Old name (Foo 1900) Bar 1950</Synonym>
    <GenbankCommonName>the bug</GenbankCommonName><Name><ClassCDE>type material</ClassCDE><DispName>ATCC 1</DispName></Name></OtherNames>
    <LineageEx><Taxon><TaxId>2</TaxId><ScientificName>Bacteria</ScientificName><Rank>domain</Rank></Taxon></LineageEx></Taxon></TaxaSet>"""
    t = S0.parse_taxa_xml(xml)[0]
    assert t["taxid"] == 9 and t["lineage"][0]["name"] == "Bacteria"
    classes = {o["class"] for o in t["other_names"]}
    assert {"synonym", "genbank common name", "type material"} <= classes
    assert S0.best_name_match("Old name", t)[:2] == (1.0, "Old name (Foo 1900) Bar 1950")
    assert S0.best_name_match("ATCC 1", t)[0] < 1.0       # type-material strings never match user queries
