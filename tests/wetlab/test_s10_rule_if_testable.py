"""S10: every falsification-map row carries `rule_if_testable`, the README's fixed "falsified if" rule for its animal readout.

The field is the rule that WOULD apply if the row were testable. It is shown for EVERY row, also the rows whose `falsified_if` is empty because they
are not testable, and it is never a claim that the row was tested (`status` says that). What is pinned here:

  * the field is on every row of every kind: at design level (all six readouts, all three statuses) and in a real `run()` over the S2
    stand-in and over an S7-selected chain;
  * it equals the module's README constant for the readout, and that constant matches the text of README.md section S10;
  * `falsified_if` keeps its exact earlier values and stays empty for rows that are not testable;
  * a row or a whole design written before the field existed still validates, and null (no README rule) is allowed and warning-free;
  * the study markdown labels the rule ("Rule if testable: ...") and still reads a not-testable row as NOT tested;
  * the output is deterministic (two runs, same digest) and carries no local path.

Everything works on COPIES of `fixtures/nmb_fixture` under `tmp_path`. The S7-selected case uses the synthetic S3-S7 chain of
test_streamc_upstream.py (B's real predictors are not installed here), imported rather than copied.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
from pathlib import Path
from typing import get_args

import pytest
from test_streamc_upstream import HLA_STUDY, write_synthetic_chain

from vaxforge import s10_animal
from vaxforge.artifacts import RunStore
from vaxforge.config import Config
from vaxforge.s10_animal import (
    README_FALSIFIED_IF,
    T_CELL_RULE_CAVEAT,
    FalsificationRow,
    StudyConfig,
    StudyDesign,
    StudyGoal,
    design_study,
    from_json,
    render_markdown,
    to_json,
    validate_design,
)
from vaxforge.streamc_models import RANK_SOURCE_S7_FINAL, RANK_SOURCE_USER_SELECTION, Candidate, Epitope, PathogenContext

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
README = ROOT / "README.md"

STAGES = ("S3", "S5_T_cell", "S5_B_cell", "S6", "S7", "functional")
STATUSES = {"testable", "not_testable_no_upstream_prediction", "not_testable_in_selected_species"}
# the first cell of each row of the README S10 "Falsification mapping" table
README_ROW = {
    "S3": "S3 antigenicity rank",
    "S5_T_cell": "S5 T-cell epitopes",
    "S5_B_cell": "S5 B-cell epitopes",
    "S6": "S6 population coverage",
    "S7": "S7 structure / conformation",
    "functional": "Functional protection",
}
# The exact `falsified_if` strings of a testable row as s10_animal.py wrote them BEFORE `rule_if_testable` existed (pinned, not imported).
FALSIFIED_IF_BEFORE = {
    "S3": "No titre rise over the adjuvant-only arm.",
    "S5_T_cell": (
        "Response to whole antigen but NOT to the predicted pools - antigen right, epitope prediction wrong. "
        "(No response even to whole antigen is not informative about epitopes.)"
    ),
    "S5_B_cell": "Sera bind the antigen but not the predicted epitopes.",
    "S6": "Response absent in backgrounds predicted to be covered.",
    "S7": "Sera recognise only the denatured form - the conformational epitope was an artifact.",
    "functional": "Titres rise but functional activity is absent.",
}

MENB = PathogenContext(name="Neisseria meningitidis serogroup B", taxid=487, correlate_of_protection="hSBA")
NO_CORRELATE = PathogenContext(name="Hypothetical pathogen", correlate_of_protection=None)
WILD = StudyConfig()  # wild-type mice: the human-HLA rows (S5 T-cell, S6) that have data are not_testable_in_selected_species
HLA = StudyConfig(goal=StudyGoal.HLA_EPITOPE_VALIDATION, hla_transgenic_lines=["A2.DR1"], adjuvant="CpG")  # those rows are testable


def full() -> Candidate:  # every upstream field present: all six rows testable (functional needs a correlate)
    eps = [
        Epitope(peptide="AAKLTVAGS", kind="T_class_I", start=10, hla=["HLA-A*02:01"]),
        Epitope(peptide="KLTVAGSEKLTVAGS", kind="T_class_II", start=60, hla=["HLA-DRB1*01:01"]),
        Epitope(peptide="GSEKLTVAGS", kind="B_linear", start=31),
        Epitope(peptide="TVAGSEKLTVAG", kind="B_conformational"),
    ]
    return Candidate(candidate_id="c1", protein_name="fHbp", rank=1, protegenicity=0.93, epitopes=eps, pc90=0.88,
                     coverage_by_population={"Europe": 0.91}, structure_confidence=0.82, structure_validators=["esmfold", "af2-no-msa"])


def t_only() -> Candidate:  # T-cell epitopes only: B-cell, S6 and S7 rows have no upstream prediction
    return Candidate(candidate_id="c2", protein_name="NadA", rank=2, protegenicity=0.88,
                     epitopes=[Epitope(peptide="LLAGSEKLA", kind="T_class_I", start=5, hla=["HLA-B*07:02"])])


def bare() -> Candidate:  # protegenicity only
    return Candidate(candidate_id="c3", protein_name="NHBA", rank=3, protegenicity=0.80)


def chosen() -> Candidate:  # a person's pick from the S2 table: no S3 prediction to refute
    return Candidate(candidate_id="c4", protein_name="UserPick", rank=4, rank_source=RANK_SOURCE_USER_SELECTION)


def s7_final() -> Candidate:  # S7's order without an S3 protegenicity score: no S3 prediction either
    return Candidate(candidate_id="c5", protein_name="Fallback", rank=5, rank_source=RANK_SOURCE_S7_FINAL)


def all_designs() -> list[StudyDesign]:
    cands = [full(), t_only(), bare(), chosen(), s7_final()]
    return [design_study(cands, pathogen, cfg) for pathogen in (MENB, NO_CORRELATE) for cfg in (WILD, HLA)]


@pytest.fixture(scope="module")
def designs() -> list[StudyDesign]:
    return all_designs()  # never mutated by a test: those that edit a design deep-copy it first


def all_rows(ds: list[StudyDesign]) -> list[FalsificationRow]:
    return [r for d in ds for r in d.falsification_map]


def rule_lines(md: str) -> list[str]:
    return [ln for ln in md.splitlines() if "Rule if testable:" in ln]


def table_lines(md: str) -> list[str]:
    return [ln for ln in md.splitlines() if re.match(r"\| (S3|S5_T_cell|S5_B_cell|S6|S7|functional) \| ", ln)]


# ============================================================================ the field at design level


def test_every_row_of_every_kind_carries_the_readme_rule_for_its_readout(designs):
    rows = all_rows(designs)
    assert {r.upstream_stage for r in rows} == set(STAGES) and {r.status for r in rows} == STATUSES  # the sample really spans every readout and status
    for r in rows:
        assert r.rule_if_testable and r.rule_if_testable == README_FALSIFIED_IF[r.upstream_stage], (r.candidate_id, r.upstream_stage, r.status)


def test_the_rule_table_covers_exactly_the_readouts_a_row_can_have():
    in_the_model = get_args(FalsificationRow.model_fields["upstream_stage"].annotation)
    assert set(README_FALSIFIED_IF) == set(in_the_model) == set(s10_animal._ALL_STAGES) == set(STAGES)  # so no row ever falls to null by accident


def test_the_field_is_in_the_serialised_design_on_every_row(designs):
    for d in designs:
        for row in json.loads(to_json(d))["falsification_map"]:
            assert row["rule_if_testable"] == README_FALSIFIED_IF[row["upstream_stage"]]
    assert from_json(to_json(designs[0])) == designs[0]  # and it round-trips


def test_falsified_if_keeps_its_exact_earlier_values_and_stays_empty_when_the_row_is_not_testable(designs):
    for r in all_rows(designs):
        if r.status == "testable":
            assert r.falsified_if == FALSIFIED_IF_BEFORE[r.upstream_stage], (r.candidate_id, r.upstream_stage)
        else:
            assert r.falsified_if is None, (r.candidate_id, r.upstream_stage, r.status)


def test_a_row_that_is_not_testable_names_the_rule_and_is_still_not_a_prediction(designs):
    seen = set()
    for r in all_rows(designs):
        if r.status == "testable":
            continue
        seen.add(r.status)
        assert r.falsified_if is None and r.rule_if_testable == README_FALSIFIED_IF[r.upstream_stage]
        if r.status == "not_testable_no_upstream_prediction":  # the rule is not content the upstream stage supplied
            assert r.upstream_prediction is None and not r.upstream_data and not r.peptide_pools
    assert seen == STATUSES - {"testable"}
    for d in designs:
        assert validate_design(d) == [], d.pathogen  # a populated rule on a not-testable row is not "fabricated prediction content"


def test_falsified_if_and_rule_if_testable_are_built_from_the_same_strings(designs):
    for r in all_rows(designs):
        if r.status == "testable":
            caveat = f" {T_CELL_RULE_CAVEAT}" if r.upstream_stage == "S5_T_cell" else ""
            assert r.falsified_if == r.rule_if_testable + caveat, (r.candidate_id, r.upstream_stage)
    # the caveat is this module's own sentence, not README text, so it is not part of the README rule
    assert all(T_CELL_RULE_CAVEAT not in rule for rule in README_FALSIFIED_IF.values())


# ============================================================================ the wording is the README's


def readme_falsified_if() -> dict[str, str]:
    """README.md section S10 'Falsification mapping' table: first cell -> 'Falsified if' cell."""
    text = README.read_text(encoding="utf-8")
    cells_by_row: dict[str, str] = {}
    for line in text[text.index("#### Falsification mapping"):].splitlines()[1:]:
        if line.startswith("#"):  # the next heading ends the section
            break
        if line.startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) == 3 and not set(cells[0]) <= {"-", " "}:
                cells_by_row[cells[0]] = cells[2]
    return cells_by_row


def norm(text: str) -> str:
    """README markdown and this module's ASCII rendering, compared alike: no bold marks, em dash = hyphen, no final full stop, no case."""
    return re.sub(r"\s+", " ", text.replace("**", "").replace("—", "-")).strip().rstrip(".").casefold()


def test_each_rule_is_the_text_of_the_readme_row_for_that_readout():
    readme = readme_falsified_if()
    for stage, label in README_ROW.items():
        assert label in readme, f"README.md section S10 has no falsification row {label!r}"
        assert norm(README_FALSIFIED_IF[stage]) == norm(readme[label]), stage


# ============================================================================ old artifacts, and null


def test_a_row_written_before_the_field_existed_still_validates():
    old = dict(candidate_id="c1", protein_name="fHbp", upstream_stage="S3", animal_readout="Antigen-specific serum IgG ELISA titre.",
               falsified_if=FALSIFIED_IF_BEFORE["S3"], status="testable")  # the keys an earlier artifact has: no rule_if_testable
    row = FalsificationRow.model_validate(old)
    assert row.rule_if_testable is None and row.falsified_if == old["falsified_if"] and row.status == "testable"


def test_a_whole_design_without_the_field_loads_validates_and_renders(designs):
    d = designs[0]
    data = json.loads(to_json(d))
    for row in data["falsification_map"]:
        del row["rule_if_testable"]  # an artifact written before the field existed
    old = from_json(json.dumps(data))
    assert all(r.rule_if_testable is None for r in old.falsification_map)
    assert validate_design(old) == []
    for before, after in zip(d.falsification_map, old.falsification_map):  # purely additive: every other field is what it was
        assert before.model_dump(exclude={"rule_if_testable"}) == after.model_dump(exclude={"rule_if_testable"})
    md = render_markdown(old)
    assert "Rule if testable: None" not in md and md.count("Rule if testable: none (no README rule recorded") == len(old.falsification_map)


def test_null_means_no_readme_rule_and_is_stated_without_a_warning(designs):
    d = copy.deepcopy(designs[0])
    row = d.falsification_map[0]
    row.rule_if_testable = None
    assert validate_design(d) == []  # null is allowed: no violation (the stage turns violations into warnings and a failed status)
    assert f"- {row.upstream_stage}: Rule if testable: none (no README rule recorded for this readout)." in render_markdown(d)
    assert json.loads(to_json(d))["falsification_map"][0]["rule_if_testable"] is None


# ============================================================================ the study markdown


def test_markdown_labels_the_rule_for_every_row_and_not_testable_rows_still_read_as_not_tested(designs):
    for d in designs:
        md = render_markdown(d)
        lines, rows = rule_lines(md), d.falsification_map
        assert len(lines) == len(rows) and "Under each table, `Rule if testable`" in md
        for line, r in zip(lines, rows):  # same order as the rows: each candidate's rows follow its table
            assert line.startswith(f"- {r.upstream_stage}: Rule if testable: {r.rule_if_testable}"), (r.candidate_id, r.upstream_stage)
            if r.status == "testable":
                assert "NOT tested" not in line
            else:
                assert f"`{r.status}`: it is NOT tested." in line and "not a claim that it was tested" in line
        for line, r in zip(table_lines(md), rows):  # the table is as it was: empty "Falsified if" cell for a row that is not testable
            assert line.endswith(f"| {r.falsified_if or '-'} | `{r.status}` |"), (r.candidate_id, r.upstream_stage)


# ============================================================================ determinism


def test_two_runs_give_identical_output_without_timestamps_or_local_paths(designs):
    again = all_designs()
    for a, b in zip(designs, again):
        assert to_json(a) == to_json(b) and render_markdown(a) == render_markdown(b)
    rows_json = json.dumps([r.model_dump(mode="json") for r in all_rows(designs)])
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", rows_json) and not re.search(r"[A-Za-z]:[\\/]|/Users/|/home/|/tmp/", rows_json)


# ============================================================================ a real S10 run


def cfg(**streamc) -> Config:
    return Config(dict(streamc=streamc))


def copy_run(dest: Path) -> RunStore:
    shutil.copytree(FIXTURE, dest)
    return RunStore(dest, cache_dir=dest.parent / "cache")


def check_run(store: RunStore, art, source: str) -> list[dict]:
    """What must hold for every real S10 artifact, whatever its candidates and study: returns the rows."""
    assert art.status == "ok" and art.payload["selection"]["source"] == source
    rows = art.payload["design"]["falsification_map"]
    assert rows and {r["upstream_stage"] for r in rows} == set(STAGES)
    for r in rows:
        assert r["rule_if_testable"] == README_FALSIFIED_IF[r["upstream_stage"]], (r["candidate_id"], r["upstream_stage"])
        assert r["falsified_if"] == (FALSIFIED_IF_BEFORE[r["upstream_stage"]] if r["status"] == "testable" else None), (r["candidate_id"], r["upstream_stage"])
    assert set(art.payload["falsification_rows"]) == {"total", "by_status"} and art.payload["falsification_rows"]["total"] == len(rows)  # a count summary: no row is serialised there
    md = store.resolve(art.payload["study_file"]).read_text(encoding="utf-8")
    assert len(rule_lines(md)) == len(rows)
    for line, r in zip(rule_lines(md), rows):
        assert f"Rule if testable: {r['rule_if_testable']}" in line and (("NOT tested" in line) == (r["status"] != "testable"))
    assert store.verify() == []
    assert not any("rule if testable" in w.lower() or "rule_if_testable" in w for w in art.warnings)  # a populated rule is not a warning
    text = json.dumps(art.payload)  # json.dumps doubles a Windows path's backslashes, so the escaped form is checked too
    leaks = (str(store.run_dir), store.run_dir.as_posix(), json.dumps(str(store.run_dir))[1:-1], store.run_dir.parent.name)  # last: tmp_path's unique name
    assert not any(leak in text for leak in leaks), "a local path is in the hashed payload"
    return rows


STAND_IN = dict(proteins=["fhbp", "NADA", "q7dd37"], top_k=3)


def test_stand_in_run_every_row_has_the_rule_and_none_is_tested(tmp_path):
    store = copy_run(tmp_path / "a")
    art = s10_animal.run(store, cfg(**STAND_IN))
    rows = check_run(store, art, "s2_stand_in")
    assert len(rows) == 18 and {r["status"] for r in rows} == {"not_testable_no_upstream_prediction"}  # the S2 stand-in has no upstream S3-S7 data
    assert all(r["falsified_if"] is None and not r["upstream_prediction"] for r in rows)
    md = store.resolve(art.payload["study_file"]).read_text(encoding="utf-8")
    assert md.count("it is NOT tested.") == 18  # every one of the 18 rows reads as not tested, with its rule beside it


def test_stand_in_run_is_deterministic_across_two_run_folders(tmp_path):
    first = s10_animal.run(copy_run(tmp_path / "a"), cfg(**STAND_IN))
    second = s10_animal.run(copy_run(tmp_path / "b"), cfg(**STAND_IN))
    assert first.digest == second.digest and first.payload == second.payload


def s7_selected(tmp_path: Path, name: str) -> RunStore:
    store = copy_run(tmp_path / name)
    write_synthetic_chain(store)  # S3-S7 as stream B writes them; S10 then takes the candidates from S7's final order
    return store


def test_s7_selected_run_with_hla_transgenic_mice_has_testable_and_not_testable_rows_all_with_the_rule(tmp_path):
    store = s7_selected(tmp_path, "a")
    art = s10_animal.run(store, cfg(top_k=3, study=HLA_STUDY))
    rows = check_run(store, art, "s7_final_ranking")
    assert {r["status"] for r in rows} == {"testable", "not_testable_no_upstream_prediction"}
    assert {r["upstream_stage"] for r in rows if r["status"] == "testable"} == set(STAGES) - {"functional"}  # no correlate is known: never invented


def test_s7_selected_run_with_wild_type_mice_also_covers_the_not_testable_in_species_rows(tmp_path):
    store = s7_selected(tmp_path, "a")
    art = s10_animal.run(store, cfg(top_k=3))  # default study: wild-type mice
    rows = check_run(store, art, "s7_final_ranking")
    assert {r["status"] for r in rows} == STATUSES
    assert {r["upstream_stage"] for r in rows if r["status"] == "not_testable_in_selected_species"} == {"S5_T_cell", "S6"}
    assert all(r["falsified_if"] is None and r["rule_if_testable"] for r in rows if r["status"] == "not_testable_in_selected_species")


def test_s7_selected_run_is_deterministic_across_two_run_folders(tmp_path):
    first = s10_animal.run(s7_selected(tmp_path, "a"), cfg(top_k=3, study=HLA_STUDY))
    second = s10_animal.run(s7_selected(tmp_path, "b"), cfg(top_k=3, study=HLA_STUDY))
    assert first.digest == second.digest and first.payload == second.payload
