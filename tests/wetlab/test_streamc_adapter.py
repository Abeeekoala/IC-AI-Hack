"""Stream C input adapter (`streamc_adapter`): alias spellings, dropped-epitope warnings, error messages, the demo fixture.

The adapter is the one file that changes when stream A's real artifact schema lands, so these tests pin what it
promises today: it accepts several spellings of each field, never invents a value it was not given, and fails with a
message that names the file and what was wrong.
"""

import json
from pathlib import Path

import pytest

from vaxforge.streamc_adapter import (
    ASSUMPTIONS,
    CANDIDATE_FILES,
    PATHOGEN_FILES,
    RunInputs,
    load_inputs,
    normalise_candidate,
    normalise_epitope,
    normalise_pathogen,
)

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "runs" / "nmb_demo"


def write(path, obj):
    path.write_text(json.dumps(obj), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- the demo fixture


def test_the_demo_fixture_loads():
    inputs = load_inputs(FIXTURE)
    assert isinstance(inputs, RunInputs)
    assert len(inputs.candidates) == 4
    assert [c.candidate_id for c in inputs.candidates] == ["fixture_fHbp", "fixture_NadA", "fixture_NHBA", "fixture_decoy"]
    assert [c.rank for c in inputs.candidates] == [1, 2, 3, 4]
    p = inputs.pathogen
    assert p.taxid == 487 and p.correlate_of_protection == "hSBA"
    assert p.susceptible_small_animal_model is None  # unknown stays unknown: never defaulted to True or False
    assert inputs.warnings == []
    assert set(inputs.sources) == {"candidates", "pathogen"}
    assert inputs.assumptions == ASSUMPTIONS


def test_fixture_values_come_through_and_missing_ones_stay_missing():
    fhbp, nada, nhba, decoy = load_inputs(FIXTURE).candidates
    assert len(fhbp.epitopes) == 8 and {e.kind for e in fhbp.epitopes} == {"T_class_I", "T_class_II", "B_linear"}
    assert fhbp.pc90 == 0.82 and fhbp.coverage_by_population["Europe"] == 0.91
    assert fhbp.structure_confidence == 0.71 and fhbp.structure_validators == ["esmfold", "af2-no-msa"]
    assert nada.structure_confidence is None and nada.structure_validators == []  # S7 is top-k only
    assert nhba.pc90 is None and nhba.coverage_by_population == {}
    assert decoy.epitopes == []


# --------------------------------------------------------------------------- field spellings


@pytest.mark.parametrize(
    "raw,expected",
    [
        (dict(id="P1", name="fHbp"), ("P1", "fHbp")),
        (dict(candidate_id="c9", gene="nadA"), ("c9", "nadA")),
        (dict(uniprot="Q9JXK4", product="NHBA"), ("Q9JXK4", "NHBA")),
        (dict(accession="A1", description="a lipoprotein"), ("A1", "a lipoprotein")),
        (dict(protein_id="WP_1", protein_name="explicit", name="loser"), ("WP_1", "explicit")),  # the first spelling wins
        (dict(), ("cand5", "cand5")),  # nothing to go on: positional id, and the name is the id
    ],
)
def test_candidate_id_and_name_spellings(raw, expected):
    c = normalise_candidate(raw, 5)
    assert (c.candidate_id, c.protein_name) == expected


def test_candidate_field_spellings_and_derived_values():
    c = normalise_candidate(
        dict(id="P1", seq="MKKKLL", localization="outer membrane", vaxign_ml="0.9", pop_coverage_pc90=0.8,
             population_coverage=dict(Europe="0.9", Asia=0.7), confidence=0.6, validators=["esmfold"], evidence=["s3:abc", 7]),
        1,
    )
    assert c.sequence == "MKKKLL" and c.length_aa == 6  # length comes from the sequence when it is not given
    assert c.localisation == "outer membrane"
    assert c.protegenicity == 0.9  # numeric strings are accepted
    assert c.pc90 == 0.8
    assert c.coverage_by_population == dict(Europe=0.9, Asia=0.7)  # values become floats, keys strings
    assert c.structure_confidence == 0.6 and c.structure_validators == ["esmfold"]
    assert c.evidence_refs == ["s3:abc", "7"]


def test_nothing_is_invented_for_a_bare_candidate():
    c = normalise_candidate(dict(id="x"), 1)
    assert c.epitopes == [] and c.coverage_by_population == {} and c.structure_validators == [] and c.evidence_refs == []
    assert c.pc90 is None and c.protegenicity is None and c.structure_confidence is None
    assert c.sequence is None and c.length_aa is None and c.localisation is None
    assert normalise_candidate(dict(id="x", protegenicity="n/a"), 1).protegenicity is None  # garbage becomes unknown, not a crash


@pytest.mark.parametrize(
    "spelling,kind",
    [
        ("MHC-I", "T_class_I"), ("mhc_i", "T_class_I"), ("class I", "T_class_I"), ("CD8", "T_class_I"), ("T_class_I", "T_class_I"),
        ("class II", "T_class_II"), ("MHC-II", "T_class_II"), ("CD4", "T_class_II"), (" Class_II ", "T_class_II"),
        ("linear", "B_linear"), ("B-cell linear", "B_linear"), ("BepiPred", "B_linear"),
        ("conformational", "B_conformational"), ("DiscoTope", "B_conformational"),
    ],
)
def test_epitope_kind_spellings(spelling, kind):
    e = normalise_epitope(dict(peptide="SIINFEKLQ", kind=spelling))
    assert e is not None and e.kind == kind


def test_epitope_field_spellings():
    e = normalise_epitope(dict(sequence="GILGFVFTL", type="MHC-I", begin=58, stop=66, affinity="0.12", allele="HLA-A*02:01"))
    assert (e.peptide, e.kind, e.start, e.end, e.score, e.hla) == ("GILGFVFTL", "T_class_I", 58, 66, 0.12, ["HLA-A*02:01"])
    e = normalise_epitope({"seq": "KLAAAAAKL", "class": "conformational", "alleles": ["HLA-DRB1*01:01"], "rank_score": 0.5})
    assert (e.peptide, e.kind, e.score, e.hla) == ("KLAAAAAKL", "B_conformational", 0.5, ["HLA-DRB1*01:01"])
    assert normalise_epitope(dict(epitope="AAAAAAAAA", category="class II")).kind == "T_class_II"
    assert normalise_epitope(dict(peptide="AAAAAAAAA", kind="MHC-I")).hla == []  # no allele given: none invented


@pytest.mark.parametrize(
    "hla,expected",
    [
        ("HLA-A*02:01", ["HLA-A*02:01"]),
        ("HLA-A*02:01, HLA-B*07:02", ["HLA-A*02:01", "HLA-B*07:02"]),
        ("HLA-A*02:01;HLA-B*07:02 ;", ["HLA-A*02:01", "HLA-B*07:02"]),
        ("", []),
        (["HLA-DRB1*01:01"], ["HLA-DRB1*01:01"]),
    ],
)
def test_hla_given_as_a_string_or_a_list(hla, expected):
    assert normalise_epitope(dict(peptide="AAAAAAAAA", kind="MHC-I", hla=hla)).hla == expected


def test_pathogen_spellings_and_an_explicit_false_is_kept():
    p = normalise_pathogen(dict(organism="Neisseria meningitidis", tax_id="487", small_animal_model=False, correlate="hSBA", containment="CL2"))
    assert (p.name, p.taxid, p.susceptible_small_animal_model, p.correlate_of_protection, p.containment_level_note) == (
        "Neisseria meningitidis", 487, False, "hSBA", "CL2")  # False means "no model"; only null means "unknown"
    assert normalise_pathogen(dict(name="x", susceptible_small_animal_model=None)).susceptible_small_animal_model is None
    bare = normalise_pathogen(dict())
    assert bare.name == "unknown pathogen" and bare.taxid is None and bare.correlate_of_protection is None


# --------------------------------------------------------------------------- loading a run directory


def test_unrecognised_epitopes_are_dropped_with_a_warning(tmp_path):
    write(tmp_path / "candidates.json", [dict(id="a", rank=1, epitopes=[
        dict(peptide="GOODPEPTI", kind="MHC-I"),
        dict(peptide="BADKINDXX", kind="phage display"),
        dict(kind="linear"),  # no peptide
        dict(peptide="NOKINDYYY"),  # no kind
    ])])
    inputs = load_inputs(tmp_path)
    assert [e.peptide for e in inputs.candidates[0].epitopes] == ["GOODPEPTI"]
    dropped = [w for w in inputs.warnings if "dropped an epitope" in w]
    assert len(dropped) == 3
    assert any("phage display" in w for w in dropped) and any("peptide=None" in w for w in dropped)
    assert normalise_epitope(dict(peptide="AAAAAAAAA", kind="nonsense")) is None  # without a warnings list it is still dropped quietly


def test_duplicate_candidate_ids_raise(tmp_path):
    write(tmp_path / "candidates.json", [dict(id="a", rank=1), dict(id="b", rank=2), dict(uniprot="a", rank=3)])  # same id, two spellings
    with pytest.raises(ValueError, match=r"duplicate candidate ids in .*\['a'\]"):
        load_inputs(tmp_path)


def test_missing_candidates_file_says_what_was_looked_for(tmp_path):
    with pytest.raises(FileNotFoundError) as exc:
        load_inputs(tmp_path)
    msg = str(exc.value)
    assert all(name in msg for name in CANDIDATE_FILES) and "--candidates" in msg and str(tmp_path) in msg
    with pytest.raises(FileNotFoundError, match="--candidates"):
        load_inputs()  # no run directory and no explicit path
    with pytest.raises(FileNotFoundError, match="--candidates"):
        load_inputs(tmp_path, candidates_path=tmp_path / "nope.json")


def test_missing_pathogen_file_gives_an_unknown_pathogen_and_a_warning(tmp_path):
    write(tmp_path / "candidates.json", [dict(id="a", rank=1)])
    for kwargs in (dict(), dict(pathogen_path=tmp_path / "nope.json")):
        inputs = load_inputs(tmp_path, **kwargs)
        p = inputs.pathogen
        assert p.name == "unknown pathogen" and p.taxid is None
        assert p.susceptible_small_animal_model is None and p.correlate_of_protection is None
        assert any("no pathogen file" in w and "unknown pathogen" in w for w in inputs.warnings)
        assert "pathogen" not in inputs.sources


def test_a_pathogen_file_without_a_name_is_flagged_not_silently_unknown(tmp_path):
    write(tmp_path / "candidates.json", [dict(id="a", rank=1)])
    envelope = dict(stage="s0_taxonomy", status="ok", payload=dict(organism="Neisseria meningitidis"))  # a stage artifact: data under "payload"
    for content in (dict(taxid=487), envelope):
        write(tmp_path / "pathogen.json", content)
        inputs = load_inputs(tmp_path)
        assert inputs.pathogen.name == "unknown pathogen"
        assert any("no pathogen name" in w and "pathogen.json" in w for w in inputs.warnings)
    write(tmp_path / "pathogen.json", dict(organism="Neisseria meningitidis"))
    assert load_inputs(tmp_path).warnings == []  # a recognised spelling raises no warning


def test_candidates_are_sorted_by_rank_and_default_to_file_order(tmp_path):
    write(tmp_path / "candidates.json", [dict(id="third", rank=3), dict(id="first", rank="1"), dict(id="second", rank=2)])
    assert [c.candidate_id for c in load_inputs(tmp_path).candidates] == ["first", "second", "third"]
    write(tmp_path / "candidates.json", dict(candidates=[dict(id="x"), dict(id="y"), dict(id="z")]))  # wrapper form, no ranks
    assert [(c.candidate_id, c.rank) for c in load_inputs(tmp_path).candidates] == [("x", 1), ("y", 2), ("z", 3)]


def test_the_first_matching_candidates_file_wins(tmp_path):
    for name in CANDIDATE_FILES:
        write(tmp_path / name, [dict(id="from_" + name)])
    for name in CANDIDATE_FILES:  # remove the winner each time: the next name in the list takes over
        assert load_inputs(tmp_path).candidates[0].candidate_id == "from_" + name
        (tmp_path / name).unlink()


def test_the_first_matching_pathogen_file_wins(tmp_path):
    write(tmp_path / "candidates.json", [dict(id="a")])
    for name in PATHOGEN_FILES:
        write(tmp_path / name, dict(name="from_" + name))
    for name in PATHOGEN_FILES:
        assert load_inputs(tmp_path).pathogen.name == "from_" + name
        (tmp_path / name).unlink()
    assert load_inputs(tmp_path).pathogen.name == "unknown pathogen"


def test_explicit_paths_override_the_run_directory(tmp_path):
    cpath = write(tmp_path / "mine.json", dict(candidates=[dict(id="a", name="A", rank=1)]))
    ppath = write(tmp_path / "pp.json", dict(name="P", taxid=1))
    inputs = load_inputs(candidates_path=cpath, pathogen_path=ppath)
    assert [c.candidate_id for c in inputs.candidates] == ["a"] and inputs.pathogen.name == "P"
    assert inputs.sources == dict(candidates=str(cpath), pathogen=str(ppath))


# --------------------------------------------------------------------------- malformed input


@pytest.mark.parametrize(
    "content,match",
    [
        ("not json", "is not valid JSON"),
        ("[]", "has no candidates"),
        ("{}", "has no candidates"),
        (json.dumps(dict(candidates=[])), "has no candidates"),
        (json.dumps(["fHbp", "NadA"]), r"candidate #1 is not a JSON object"),
        (json.dumps([dict(id="a", rank="first")]), r"candidate #1 is malformed"),
        (json.dumps([dict(id="a"), dict(id="b", coverage_by_population=5)]), r"candidate #2 is malformed"),
    ],
)
def test_malformed_candidates_raise_a_value_error_naming_the_file(tmp_path, content, match):
    (tmp_path / "candidates.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=match) as exc:
        load_inputs(tmp_path)
    assert "candidates.json" in str(exc.value)


@pytest.mark.parametrize(
    "content,match",
    [
        ("not json", "is not valid JSON"),
        (json.dumps([1, 2]), "is not a JSON object"),
        (json.dumps(dict(name="x", taxid="abc")), "pathogen is malformed"),
        (json.dumps(dict(name="x", susceptible_small_animal_model="maybe")), "pathogen is malformed"),
    ],
)
def test_malformed_pathogen_raises_a_value_error_naming_the_file(tmp_path, content, match):
    write(tmp_path / "candidates.json", [dict(id="a")])
    (tmp_path / "pathogen.json").write_text(content, encoding="utf-8")
    with pytest.raises(ValueError, match=match) as exc:
        load_inputs(tmp_path)
    assert "pathogen.json" in str(exc.value)
