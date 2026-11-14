import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation")]

import pytest  # noqa: E402
import lopov  # noqa: E402

RANKED = list("abcdefghij")   # a is rank 1
POS = {"a", "c", "f"}         # ranks 1, 3, 6 of 10


def v(metric):
    return metric["value"]


# ---- metrics: a hand-computed example --------------------------------------------------------
def test_hand_computed_example():
    # top 3 = a b c holds 2 of 3 positives; top 5 holds 2; base rate 3/10
    assert v(lopov.precision_at_k(RANKED, POS, 3)) == pytest.approx(2 / 3)
    assert v(lopov.precision_at_k(RANKED, POS, 5)) == pytest.approx(0.4)
    assert v(lopov.recall_at_k(RANKED, POS, 3)) == pytest.approx(2 / 3)
    assert v(lopov.recall_at_k(RANKED, POS, 6)) == 1.0
    assert v(lopov.fold_enrichment_at_k(RANKED, POS, 3)) == pytest.approx((2 / 3) / (3 / 10))
    assert v(lopov.fold_enrichment_at_k(RANKED, POS, 5)) == pytest.approx(4 / 3)
    assert v(lopov.rank_of_truth(RANKED, POS)) == {"a": 1, "c": 3, "f": 6}
    # average precision = (1/1 + 2/3 + 3/6) / 3 = 13/18
    assert v(lopov.pr_auc(RANKED, POS)) == pytest.approx(13 / 18)


def test_pr_auc_is_average_precision_not_a_trapezoid():
    # positives at ranks 1 and 3 of 3: (1/1 + 2/3) / 2 = 5/6 exactly
    assert v(lopov.pr_auc(["p", "n", "q"], {"p", "q"})) == pytest.approx(5 / 6)


def test_pr_auc_extremes():
    assert v(lopov.pr_auc(RANKED, {"a", "b"})) == 1.0
    assert v(lopov.pr_auc(RANKED, {"i", "j"})) == pytest.approx((1 / 9 + 2 / 10) / 2)


def test_everything_positive_has_no_enrichment():
    assert v(lopov.fold_enrichment_at_k(RANKED, set(RANKED), 4)) == 1.0


def test_no_positives_is_none_with_a_reason_never_zero():
    m = lopov.metrics(RANKED, set(), [3])
    for metric in (m["precision_at_k"]["3"], m["recall_at_k"]["3"], m["fold_enrichment_at_k"]["3"], m["pr_auc"], m["rank_of_truth"]):
        assert metric["value"] is None and metric["reason"] == "no positives in this set"


def test_k_larger_than_the_list_is_none_with_a_reason():
    for fn in (lopov.precision_at_k, lopov.recall_at_k, lopov.fold_enrichment_at_k):
        m = fn(RANKED, POS, 11)
        assert m["value"] is None and "exceeds the ranked list length 10" in m["reason"]
        assert fn(RANKED, POS, 10)["value"] is not None   # k equal to the length is fine


def test_bad_arguments_raise():
    with pytest.raises(ValueError, match="at least 1"):
        lopov.precision_at_k(RANKED, POS, 0)
    with pytest.raises(ValueError, match="not in the ranked list"):
        lopov.pr_auc(RANKED, {"a", "zzz"})
    with pytest.raises(ValueError, match="duplicate"):
        lopov.pr_auc(["a", "a"], {"a"})


# ---- leakage assertion -----------------------------------------------------------------------
def test_assert_absent_passes_and_reports_sizes():
    assert lopov.assert_absent({"x1", "x2"}, {"y1"}, homologue_ids=["h1"]) == {
        "n_pathogen_ids": 2, "n_homologue_ids": 1, "n_reference_ids": 1}


def test_assert_absent_names_the_offending_ids():
    with pytest.raises(lopov.LeakageError) as e:
        lopov.assert_absent({"x1", "x2"}, {"x2", "h1", "y"}, homologue_ids=["h1", "h2"])
    assert e.value.pathogen_ids == ["x2"] and e.value.homologue_ids == ["h1"]
    assert "x2" in str(e.value) and "h1" in str(e.value)


def test_assert_absent_sees_through_uniprot_headers_and_case():
    with pytest.raises(lopov.LeakageError) as e:
        lopov.assert_absent({"sp|Q9JXV4|FHBP_NEIMB"}, {"q9jxv4"})
    assert e.value.pathogen_ids == ["sp|Q9JXV4|FHBP_NEIMB"]


def test_assert_absent_refuses_to_pass_on_nothing():
    with pytest.raises(ValueError, match="reference set is empty"):
        lopov.assert_absent({"x"}, set())
    with pytest.raises(ValueError, match="no ids"):
        lopov.assert_absent(set(), {"y"})


def test_leakage_assertion_field_never_says_passed_when_not_run():
    assert lopov.leakage_assertion({"x"}) == {
        "run": False, "passed": None, "status": "not checked", "reason": lopov.leakage_assertion({"x"})["reason"]}
    assert lopov.leakage_assertion({"x"}, {"y"})["status"] == "passed"
    bad = lopov.leakage_assertion({"x"}, {"x"}, ["h"])
    assert bad["status"] == "violated" and bad["passed"] is False and bad["pathogen_ids_in_reference"] == ["x"]


# ---- LOPOV splits ----------------------------------------------------------------------------
GROUPS = {"A": ["a1", "a2", "a3", "a4", "a5"], "B": ["b1", "b2", "b3", "b4"], "C": ["c1", "c2"]}


def folds(groups=GROUPS, excluded=None):
    return {f.pathogen: f for f in lopov.lopov_splits(groups, excluded)}


def test_each_pathogen_is_entirely_absent_from_its_own_train():
    fs = folds()
    assert list(fs) == ["A", "B", "C"]
    for p, f in fs.items():
        assert f.test == set(GROUPS[p])
        assert f.train == set().union(*(set(v_) for q, v_ in GROUPS.items() if q != p))
        assert not (f.train & f.test)


def test_listed_homologues_are_removed_from_train_for_that_fold_only():
    fs = folds(excluded={"A": ["b2", "c1", "not-in-any-pathogen"]})
    assert fs["A"].train == {"b1", "b3", "b4", "c2"}
    assert fs["A"].homologues_removed == 2 and len(fs["A"].homologues) == 3
    assert fs["B"].train == {"a1", "a2", "a3", "a4", "a5", "c1", "c2"}   # no list for B: nothing removed


def test_a_flat_exclusion_set_applies_to_every_fold():
    fs = folds(excluded={"c2"})
    assert "c2" not in fs["A"].train and "c2" not in fs["B"].train
    assert fs["C"].test == {"c1", "c2"}   # the held-out pathogen's own ids are never hidden from its test set


def test_an_id_shared_with_another_pathogen_is_removed_from_train():
    fs = folds({"A": ["x", "a1"], "B": ["x", "b1"], "C": ["c1"]})
    assert "x" not in fs["A"].train and "x" not in fs["B"].train and "x" in fs["C"].train


def test_a_bare_accession_in_another_pathogen_is_caught_as_the_same_protein():
    fs = folds({"A": ["sp|Q1|X_A", "a2"], "B": ["Q1", "b2"], "C": ["c1"]})
    assert fs["A"].train == {"b2", "c1"}


def test_lopov_needs_two_pathogens_and_no_empty_pathogen():
    with pytest.raises(ValueError, match="at least two"):
        list(lopov.lopov_splits({"A": ["a1"]}))
    with pytest.raises(ValueError, match="no proteins"):
        list(lopov.lopov_splits({"A": [], "B": ["b1"]}))


# ---- LOPOV report ----------------------------------------------------------------------------
POSITIVES = {"a2", "a4", "b1"}
SCORES = {"A": {"a1": .9, "a2": .8, "a3": .7, "a4": .6, "a5": .5},
          "B": {"b1": .4, "b2": .4, "b3": .4, "b4": .1},
          "C": {"c1": .3, "c2": .2}}


def report(**kw):
    kw.setdefault("scores", SCORES)
    return lopov.lopov_report(GROUPS, POSITIVES, kw.pop("ks", [2]), **kw)


def test_per_pathogen_metrics_match_hand_calculation():
    a = report()["per_pathogen"]["A"]["metrics"]
    # ranking a1 a2 a3 a4 a5, positives at ranks 2 and 4
    assert v(a["precision_at_k"]["2"]) == pytest.approx(0.5)
    assert v(a["recall_at_k"]["2"]) == pytest.approx(0.5)
    assert v(a["fold_enrichment_at_k"]["2"]) == pytest.approx(0.5 / (2 / 5))
    assert v(a["pr_auc"]) == pytest.approx((1 / 2 + 2 / 4) / 2)
    assert v(a["rank_of_truth"]) == {"a2": 2, "a4": 4}


def test_tied_scores_rank_positives_after_negatives():
    b = report()["per_pathogen"]["B"]["metrics"]
    # b1 (positive) ties with b2 and b3 at .4: it must come after them, rank 3, not rank 1
    assert v(b["rank_of_truth"]) == {"b1": 3}
    assert v(b["pr_auc"]) == pytest.approx(1 / 3)


def test_a_pathogen_without_positives_is_listed_and_not_averaged_in():
    r = report()
    c = r["per_pathogen"]["C"]["metrics"]
    assert v(c["pr_auc"]) is None and c["pr_auc"]["reason"] == "no positives in this set"
    s = r["summary"]
    assert s["pathogens_without_positives"] == ["C"]
    assert s["macro_average"]["pr_auc"] == {"mean": pytest.approx((1 / 2 + 1 / 3) / 2), "n": 2}
    assert s["macro_average"]["precision_at_k"]["2"] == {"mean": pytest.approx((0.5 + 0.0) / 2), "n": 2}
    assert r["pr_auc_estimator"] == "average_precision"


def test_metrics_are_none_when_no_scores_were_supplied():
    r = report(scores={"A": SCORES["A"]})
    assert r["per_pathogen"]["B"]["metrics"] is None
    assert r["per_pathogen"]["B"]["metrics_reason"] == "no scores supplied for this pathogen"
    assert r["summary"]["pathogens_without_metrics"] == ["B", "C"]
    assert report(scores=None)["summary"]["macro_average"]["pr_auc"] == {"mean": None, "n": 0}


def test_missing_or_non_finite_scores_are_bad_input():
    with pytest.raises(ValueError, match="no score"):
        report(scores={**SCORES, "A": {"a1": .9}})
    with pytest.raises(ValueError, match="finite"):
        report(scores={**SCORES, "A": {**SCORES["A"], "a1": float("nan")}})


def test_positives_outside_every_pathogen_are_counted_not_dropped():
    r = lopov.lopov_report(GROUPS, POSITIVES | {"zz1", "zz2"}, [2], SCORES)
    assert r["summary"]["positives_in_no_pathogen"] == 2


def test_training_set_assertion_is_run_only_when_the_used_set_is_given():
    clean = report(train_used={"A": ["b1", "c1"]})["per_pathogen"]
    assert clean["A"]["training_set_assertion"]["status"] == "passed"
    assert clean["B"]["training_set_assertion"]["status"] == "not checked"
    assert clean["A"]["split_assertion"] == "passed"


def test_a_violated_training_set_withholds_metrics_and_is_summarised():
    r = report(excluded={"A": ["b2"]}, train_used={"A": ["a3", "b2", "c1"], "B": ["a1"]})
    a = r["per_pathogen"]["A"]
    assert a["training_set_assertion"]["status"] == "violated"
    assert a["training_set_assertion"]["pathogen_ids_in_reference"] == ["a3"]
    assert a["training_set_assertion"]["homologue_ids_in_reference"] == ["b2"]
    assert a["metrics"] is None and a["metrics_reason"].startswith("withheld")
    assert r["summary"]["pathogens_violating_leakage"] == ["A"]
    assert r["summary"]["macro_average"]["pr_auc"]["n"] == 1   # only B contributed a defined value


# ---- CLI -------------------------------------------------------------------------------------
def write_input(tmp_path, **over):
    d = {"proteins": GROUPS, "positives": sorted(POSITIVES), "scores": SCORES, **over}
    f = tmp_path / "in.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    return str(f)


def test_cli_writes_a_report_and_exits_zero(tmp_path, capsys):
    out = tmp_path / "report.json"
    assert lopov.main([write_input(tmp_path), "--k", "2", "3", "--out", str(out)]) == 0
    rep = json.loads(out.read_text(encoding="utf-8"))
    assert rep["ks"] == [2, 3] and rep["summary"]["pathogens_without_positives"] == ["C"]
    assert "3 pathogens; 1 without positives" in capsys.readouterr().out


def test_cli_exits_2_on_a_violated_assertion_and_still_writes_the_report(tmp_path, capsys):
    out = tmp_path / "report.json"
    inp = write_input(tmp_path, train_ids_used={"A": ["a1"]})
    assert lopov.main([inp, "--k", "2", "--out", str(out)]) == 2
    assert json.loads(out.read_text(encoding="utf-8"))["summary"]["pathogens_violating_leakage"] == ["A"]
    assert "a1" in capsys.readouterr().out


def test_cli_exits_1_on_bad_input(tmp_path):
    assert lopov.main([str(tmp_path / "missing.json"), "--k", "2"]) == 1
    assert lopov.main([write_input(tmp_path, scores={"A": {"a1": 1.0}}), "--k", "2"]) == 1
    bare = tmp_path / "bare.json"
    bare.write_text("{}", encoding="utf-8")
    assert lopov.main([str(bare), "--k", "2"]) == 1
    with pytest.raises(SystemExit):   # k is required: choose it before you look at results
        lopov.main([write_input(tmp_path)])


def test_read_ids_json_and_text(tmp_path):
    (tmp_path / "a.json").write_text('["x", "y"]', encoding="utf-8")
    (tmp_path / "b.txt").write_text("# comment\nx\n\n  y  \n", encoding="utf-8")
    assert lopov.read_ids(tmp_path / "a.json") == lopov.read_ids(tmp_path / "b.txt") == ["x", "y"]
    (tmp_path / "c.json").write_text('{"x": 1}', encoding="utf-8")
    with pytest.raises(ValueError, match="list of id strings"):
        lopov.read_ids(tmp_path / "c.json")
