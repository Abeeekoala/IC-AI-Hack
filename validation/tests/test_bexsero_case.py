import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation")]

import pytest  # noqa: E402
import bexsero_case as bc  # noqa: E402

FIXTURE_S2 = ROOT / "fixtures" / "nmb_fixture" / "s2_annotate.json"


def entry(rank, pid, gene="", name="", score=None, prot=None):
    return {"protein_id": pid, "gene": gene, "name": name, "rank": rank, "composite_score": score, "protegenicity": prot}


def s3(entries, **extra):
    return {"stage": "s3", "payload": {"context": {"taxid": 487}, "ranker": {"backend": "test"},
                                       "leakage_audit": {"holdout_enforced": True, "note": "as given by S3"},
                                       "ranking": entries, **extra}}


def filler(n, start=1, **kw):
    return [entry(start + i, f"sp|F{start + i:04d}|FILL_X", f"g{start + i}", f"Filler protein {start + i}",
                  score=1.0 - 0.01 * (start + i)) for i in range(n)]


def ranking_with(**positions):
    """40 distinct-score proteins, with the named antigens placed at the given ranks."""
    es = filler(40)
    names = {"fhbp": ("fhbP", "Factor H binding protein"), "nada": ("nadA", "Neisseria adhesin A"),
             "nhba": ("nhba", "Neisserial heparin binding antigen")}
    for key, rank in positions.items():
        gene, name = names[key]
        es[rank - 1] = entry(rank, f"sp|{key.upper()}|{key.upper()}_NEIMB", gene, name, score=1.0 - 0.01 * rank)
    return es


def by_label(report):
    return {a["antigen"]: a for a in report["antigens"]}


# ---- what the fixture holds ------------------------------------------------------------------
def test_the_fixture_contains_each_antigen_exactly_once_under_the_default_name_patterns():
    proteins = json.loads(FIXTURE_S2.read_text(encoding="utf-8"))["payload"]["proteins"]
    es = [entry(i, p["protein_id"], p["gene"], p["description"], score=1.0 - 0.001 * i) for i, p in enumerate(proteins, 1)]
    r = bc.bexsero_report(s3(es))
    a = by_label(r)
    assert {k: x["status"] for k, x in a.items()} == {"fHbp": "matched", "NadA": "matched", "NHBA": "matched"}
    assert [a[k]["matches"][0]["gene"].lower() for k in ("fHbp", "NadA", "NHBA")] == ["fhbp", "nada", "nhba"]
    assert r["unresolved_antigens"] == []
    assert r["s3"]["n_ranked"] == 40 == len(proteins)


# ---- the metric ------------------------------------------------------------------------------
def test_recall_and_ranks_for_three_antigens():
    r = bc.bexsero_report(s3(ranking_with(fhbp=1, nada=2, nhba=5)))
    assert {k: a["rank"] for k, a in by_label(r).items()} == {"fHbp": 1, "NadA": 2, "NHBA": 5}
    assert r["recall_at_k"]["value"] == pytest.approx(2 / 3)
    assert (r["recall_at_k"]["n_hit"], r["recall_at_k"]["n_truth"]) == (2, 3)
    assert r["s3"]["leakage_audit"] == {"holdout_enforced": True, "note": "as given by S3"}   # passed through, not judged
    assert r["scope"].startswith("S3 ranking only")


def test_k_larger_than_the_list_is_none_with_a_reason():
    r = bc.bexsero_report(s3(ranking_with(fhbp=1, nada=2, nhba=3)), k=41)
    assert r["recall_at_k"]["value"] is None and "exceeds the ranked list length 40" in r["recall_at_k"]["reason"]
    assert bc.bexsero_report(s3(ranking_with(fhbp=1, nada=2, nhba=3)), k=40)["recall_at_k"]["value"] == 1.0


def test_an_antigen_that_matches_nothing_is_reported_and_stays_in_the_denominator():
    truth = {**bc.DEFAULT_TRUTH, "PorA": ["porA"]}
    r = bc.bexsero_report(s3(ranking_with(fhbp=1, nada=2, nhba=3)), truth)
    assert by_label(r)["PorA"]["status"] == "not_found" and by_label(r)["PorA"]["rank"] is None
    assert r["unresolved_antigens"] == [{"antigen": "PorA", "status": "not_found"}]
    assert (r["recall_at_k"]["n_hit"], r["recall_at_k"]["n_truth"]) == (3, 4)


def test_an_antigen_that_matches_several_proteins_is_listed_with_every_rank_and_not_counted():
    es = ranking_with(fhbp=1, nada=2, nhba=3)
    es[9] = entry(10, "sp|FHBP2|FHBP2_NEIMB", "fhbp2", "Factor H binding protein, paralogue", score=0.9)
    a = by_label(bc.bexsero_report(s3(es)))["fHbp"]
    assert a["status"] == "ambiguous" and a["rank"] is None
    assert sorted(m["rank"] for m in a["matches"]) == [1, 10]
    r = bc.bexsero_report(s3(es))
    assert r["recall_at_k"]["n_hit"] == 2 and r["recall_at_k"]["n_truth"] == 3   # fHbp at rank 1 is not credited
    assert r["unresolved_antigens"] == [{"antigen": "fHbp", "status": "ambiguous"}]


def test_ties_are_reported_as_a_range_and_recall_is_given_for_both_extremes():
    es = filler(10)
    for i in (1, 2, 3):   # ranks 2-4 share one score
        es[i] = entry(i + 1, f"sp|T{i}|T_X", f"t{i}", f"Tied {i}", score=0.5, prot=None)
    es[2] = entry(3, "sp|NHBA1|N_X", "nhba", "Neisserial heparin binding antigen", score=0.5, prot=None)
    r = bc.bexsero_report(s3(es), {"NHBA": ["NHBA"]}, k=3)
    m = by_label(r)["NHBA"]["matches"][0]
    assert (m["rank"], m["rank_best_case"], m["rank_worst_case"]) == (3, 2, 4)
    rec = r["recall_at_k"]
    assert (rec["value"], rec["if_ties_go_best_case"], rec["if_ties_go_worst_case"]) == (1.0, 1.0, 0.0)


# ---- matching rules --------------------------------------------------------------------------
@pytest.mark.parametrize("term,expected", [
    ("FHBP", True),                  # gene, ignoring case
    ("Q9JXV4", True),                # accession inside a UniProt header id
    ("sp|q9jxv4|fhbp_neimb", True),  # the full id
    ("factor h binding", True),      # whole words of the name
    ("binding prot", False),         # not whole words
    ("hbp", False),                  # a substring of the gene is not the gene
])
def test_matching_rules(term, expected):
    e = entry(1, "sp|Q9JXV4|FHBP_NEIMB", "fhbP", "Factor H binding protein")
    assert bc._matches(e, term) is expected


def test_load_truth_forms(tmp_path):
    f = tmp_path / "t.json"
    f.write_text('{"X": ["a", "b"], "Y": "c"}', encoding="utf-8")
    assert bc.load_truth(f) == {"X": ["a", "b"], "Y": ["c"]}
    f.write_text('["p", "q"]', encoding="utf-8")
    assert bc.load_truth(f) == {"p": ["p"], "q": ["q"]}
    for bad in ('{}', '{"X": []}', '{"X": [""]}', '{"X": [1]}', '"x"'):
        f.write_text(bad, encoding="utf-8")
        with pytest.raises(ValueError):
            bc.load_truth(f)


def test_truth_source_says_unverified_for_the_defaults_only():
    es = ranking_with(fhbp=1, nada=2, nhba=3)
    assert "not verified" in bc.bexsero_report(s3(es))["truth_source"]
    assert bc.bexsero_report(s3(es), {"NHBA": ["NHBA"]}, truth_source="truth.json")["truth_source"] == "truth.json"


# ---- leakage assertion field -----------------------------------------------------------------
def test_leakage_is_not_checked_without_a_reference_set_and_never_reads_as_passed():
    r = bc.bexsero_report(s3(ranking_with(fhbp=1, nada=2, nhba=3)))
    assert r["leakage_assertion"]["status"] == "not checked"
    assert r["leakage_assertion"]["run"] is False and r["leakage_assertion"]["passed"] is None
    r = bc.bexsero_report(s3(ranking_with(fhbp=1, nada=2, nhba=3)), homologue_ids=["h1"])   # homologues alone prove nothing
    assert r["leakage_assertion"]["status"] == "not checked"


def test_leakage_passes_when_the_reference_set_holds_none_of_the_pathogen():
    r = bc.bexsero_report(s3(ranking_with(fhbp=1, nada=2, nhba=3)), reference_ids=["P1", "P2"], homologue_ids=["H1"])
    assert r["leakage_assertion"]["status"] == "passed"
    assert r["leakage_assertion"]["run"] is True and r["leakage_assertion"]["passed"] is True
    assert r["recall_at_k"]["value"] == 1.0


def test_leakage_violation_is_named_and_withholds_the_result():
    es = ranking_with(fhbp=1, nada=2, nhba=3)
    r = bc.bexsero_report(s3(es), reference_ids=["P1", "FHBP"], homologue_ids=["H1", "P1"])
    leak = r["leakage_assertion"]
    assert leak["status"] == "violated" and leak["passed"] is False
    assert leak["pathogen_ids_in_reference"] == ["sp|FHBP|FHBP_NEIMB"] and leak["homologue_ids_in_reference"] == ["P1"]
    assert r["recall_at_k"]["value"] is None and "antigens" not in r


def test_an_empty_reference_set_is_refused_not_passed():
    with pytest.raises(ValueError, match="reference set is empty"):
        bc.bexsero_report(s3(ranking_with(fhbp=1, nada=2, nhba=3)), reference_ids=[])


# ---- input checks ----------------------------------------------------------------------------
def test_bad_s3_artifacts_are_refused():
    with pytest.raises(ValueError, match="no payload.ranking"):
        bc.bexsero_report({"payload": {}})
    with pytest.raises(ValueError, match="duplicate"):
        bc.bexsero_report(s3([entry(1, "x"), entry(2, "x")]))
    with pytest.raises(ValueError, match="not in rank order"):
        bc.bexsero_report(s3([entry(2, "x"), entry(1, "y")]))
    with pytest.raises(ValueError, match="at least 1"):
        bc.bexsero_report(s3(filler(3)), k=0)


# ---- CLI -------------------------------------------------------------------------------------
def write(tmp_path, name, data):
    f = tmp_path / name
    f.write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    return str(f)


def test_cli_exit_codes_and_report(tmp_path, capsys):
    art = write(tmp_path, "s3.json", s3(ranking_with(fhbp=1, nada=2, nhba=5)))
    out = tmp_path / "bex.json"
    assert bc.main([art, "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["recall_at_k"]["n_hit"] == 2 and rep["leakage_assertion"]["status"] == "not checked"
    text = capsys.readouterr().out
    assert "leakage assertion: not checked" in text and "recall@3: 0.666" in text

    ref = write(tmp_path, "ref.txt", "P1\nP2\n")
    assert bc.main([art, "--reference", ref]) == 0
    assert "leakage assertion: passed" in capsys.readouterr().out

    bad_ref = write(tmp_path, "bad.json", ["sp|NHBA|NHBA_NEIMB"])
    assert bc.main([art, "--reference", bad_ref, "--out", str(out)]) == 2
    assert json.loads(out.read_text(encoding="utf-8"))["leakage_assertion"]["status"] == "violated"
    assert "sp|NHBA|NHBA_NEIMB" in capsys.readouterr().out

    truth = write(tmp_path, "truth.json", {"NHBA": ["nhba"]})
    assert bc.main([art, "--truth", truth, "--k", "5"]) == 0
    assert "recall@5: 1.0" in capsys.readouterr().out


def test_cli_exits_1_on_bad_input(tmp_path):
    assert bc.main([str(tmp_path / "missing.json")]) == 1
    assert bc.main([write(tmp_path, "notS3.json", {"payload": {}})]) == 1
    assert bc.main([write(tmp_path, "nokey.json", s3([{"gene": "x"}]))]) == 1
    art = write(tmp_path, "s3.json", s3(ranking_with(fhbp=1, nada=2, nhba=3)))
    assert bc.main([art, "--reference", write(tmp_path, "empty.txt", "")]) == 1
    assert bc.main([art, "--truth", write(tmp_path, "t.json", "{}")]) == 1
