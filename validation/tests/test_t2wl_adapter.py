"""Tests for the S9.5 Text2WetLab grading adapter (validation/t2wl_adapter.py).

The pure parts (result parsing, per-rule results, the refusal stopgap, record building, interpreter and clone plumbing,
the subprocess scrub) need only the standard library. The tests that grade real protocols need a Text2WetLab clone at the
adapter's verified commit and Python 3.10 with opentrons 7.5.0; without both they skip, with the reason:
    T2WL_CLONE=/path/to/Text2WetLab VAXFORGE_OT_PYTHON=/path/to/venv/bin/python pytest validation/tests/test_t2wl_adapter.py
"""
import ast
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "validation"))

import pytest  # noqa: E402
import t2wl_adapter as ad  # noqa: E402

_FAMILIES_FILE = ROOT / "validation" / "heldout_families.json"   # S9.5 harness branch; the fallback is its table
FAMILIES = ({name: set(tasks) for name, tasks in json.loads(_FAMILIES_FILE.read_text())["families"].items()}
            if _FAMILIES_FILE.is_file() else {  # 7 families named after their easy task, 11 tasks
                "a1-a12-100ul": {"a1-a12-100ul"}, "split-200ul-two-wells": {"split-200ul-two-wells"},
                "ampure-bead-cleanup": {"ampure-bead-cleanup"},
                "colony-pcr-screening": {"colony-pcr-screening", "colony-pcr-screening-hard"},
                "ecoli-heat-shock-transformation": {"ecoli-heat-shock-transformation", "ecoli-heat-shock-transformation-hard"},
                "golden-gate-assembly": {"golden-gate-assembly", "golden-gate-assembly-hard"},
                "opentrons-rna-extraction": {"opentrons-rna-extraction", "opentrons-rna-extraction-hard"}})
FAMILY_OF = {task: family for family, tasks in FAMILIES.items() for task in tasks}
TASKS = sorted(FAMILY_OF)


# ---- fixtures -------------------------------------------------------------------------------------------------------

def chk(name, ok=True, detail=""):
    return {"name": name, "pass": ok, "detail": detail}


def rule_checks(**failing):
    """The five rule checks as spec_check emits them; named ones fail with the given detail."""
    return [chk(r, r not in failing, failing.get(r, "" if r == "tip_dropped_at_end" else "[]")) for r in ad.RULES]


def ir_raw(*extra, **failing_rules):
    return {"lint": [], "traps": [], "sim": {"ok": True, "error": ""},
            "checks": [chk("simulator_ran")] + rule_checks(**failing_rules) + list(extra)}


def end_state(want, got, name="end_state:plate"):  # spec_check's own detail format
    return chk(name, want == got, "" if want == got else f"expected {want}, got {got}")


def wells(volume, n=12, row="A"):
    return {f"{row}{c}": float(volume) for c in range(1, n + 1)}


RNA_CHECKS = ["48_samples_to_odd_columns", "step_order", "binding_volumes_40_250_250", "mix_5x_after_sample",
              "incubation_5min_before_magnet", "magnet_4min_before_first_removal", "supernatant_removed_each_step",
              "magnet_engaged_for_all_removals", "two_500ul_ethanol_washes", "air_dry_4min", "elution_100ul",
              "elution_off_magnet_then_90s_on", "recover_70_100ul_one_well_each", "distinct_elution_wells",
              "elution_plate_4C_before_recovery", "fresh_tip_per_sample_no_cross_contact", "reservoir_columns_within_15ml",
              "recover_about_80ul"]


def rna_raw(failing=None):
    """A run of the RNA task's checks; failing = {check name: detail}."""
    failing = failing or {}
    checks = [chk(n, n not in failing, failing.get(n, "48/48 sample wells")) for n in RNA_CHECKS]
    checks += [chk(n, False, d) for n, d in failing.items() if n not in RNA_CHECKS]       # a check the adapter has never seen
    return {"lint": [], "traps": [], "sim": {"ok": True, "error": ""}, "checks": checks, "n_samples": 48}


# ---- Text2WetLab's checks into a grade (IR tasks) -------------------------------------------------------------------

def test_a_clean_run_has_no_violations_and_no_diff():
    g = ad.interpret("ir", ir_raw(end_state(wells(100), wells(100))))
    assert (g["simulate_pass"], g["rule_violations"], g["gt_diff"]) == (True, 0, 0)
    assert g["stage"] == "checks" and g["failed_checks"] == [] and g["gt_diff_detail"] == []
    assert [r["pass"] for r in g["rule_results"].values()] == [True] * 5


def test_rule_results_name_each_of_the_five_rules_in_order():
    g = ad.interpret("ir", ir_raw())
    assert list(g["rule_results"]) == ["tip_before_aspirate", "no_overdispense", "no_aspirate_from_empty_well",
                                       "tip_dropped_at_end", "no_cross_contamination"] == list(ad.RULES)
    assert all(set(r) == {"pass", "t2wl_check", "detail"} for r in g["rule_results"].values())
    assert g["rule_results"]["no_overdispense"]["t2wl_check"] == "no_overdispense"


@pytest.mark.parametrize("rule", ad.RULES)
def test_each_failing_rule_is_reported_by_name_and_counted_once(rule):
    g = ad.interpret("ir", ir_raw(**{rule: "[(3, 'x')]"}))
    assert g["rule_violations"] == 1 and g["gt_diff"] == 0 and g["failed_checks"] == [rule]
    assert {r for r, v in g["rule_results"].items() if v["pass"] is False} == {rule}
    assert g["rule_results"][rule]["detail"] == "[(3, 'x')]"  # Text2WetLab's own text, for a rule-by-rule comparison


def test_all_five_rules_failing_is_five_violations():
    g = ad.interpret("ir", ir_raw(**{r: "[(1, 'x')]" for r in ad.RULES}))
    assert g["rule_violations"] == 5 and g["failed_checks"] == list(ad.RULES)


def test_end_state_counts_the_wells_whose_volume_differs():
    # real format: a1-a12-100ul with 120 uL instead of 100
    g = ad.interpret("ir", ir_raw(end_state(wells(100), wells(120))))
    assert g["gt_diff"] == 12 and g["gt_diff_detail"] == [{"check": "end_state:plate", "unit": "wells", "count": 12}]
    assert g["failed_checks"] == ["end_state:plate"] and g["rule_violations"] == 0
    assert ad.interpret("ir", ir_raw(end_state(wells(100), wells(100, 2))))["gt_diff"] == 10   # ten wells never filled
    assert ad.interpret("ir", ir_raw(end_state(wells(100), {**wells(100), "B1": 5.0})))["gt_diff"] == 1  # one extra well
    assert ad.interpret("ir", ir_raw(end_state({"A1": 100.0}, {"A1": 100.0000004, "A2": 0.0})))["gt_diff"] == 0  # 6 decimals


def test_free_positions_count_volumes_that_cannot_be_matched_not_positions():
    def free(want, got):
        return chk("end_state:plate", False, f"expected volumes {sorted(want)}, got {sorted(got)} (positions free)")
    counts = lambda w, g: ad.interpret("ir", ir_raw(free(w, g)))["gt_diff"]  # noqa: E731
    assert counts([100.0, 100.0], [200.0]) == 2       # split-200ul-two-wells, one well filled twice
    assert counts([100.0, 100.0], [100.0, 150.0]) == 1
    assert counts([100.0, 100.0], [100.0]) == 1
    assert counts([], [50.0]) == 1
    assert ad.interpret("ir", ir_raw(free([100.0], [200.0])))["gt_diff_detail"][0]["unit"] == "wells (positions free)"


def test_a_labware_or_slot_mismatch_counts_one_per_container():
    g = ad.interpret("ir", ir_raw(chk("deck_labware:plate", False, "'plate' must be in slot 2, found 'plate on 3'"),
                                  chk("deck_labware:tubes", False, "expected exactly one labware labelled 'tubes', found 0")))
    assert g["gt_diff"] == 2 and {d["unit"] for d in g["gt_diff_detail"]} == {"labware"}


def test_a_composition_failure_counts_the_problems_it_lists():
    one = chk("composition:pcr_plate", False, "volumes [40.0] outside the 10-25 uL the paper supports")
    two = chk("composition:pcr_plate", False, "6 wells lack an input, e.g. A1 has none from ['mm']; unequal volumes [11.0, 12.0]")
    assert ad.interpret("ir", ir_raw(one))["gt_diff"] == 1
    assert ad.interpret("ir", ir_raw(two))["gt_diff"] == 2
    assert ad.interpret("ir", ir_raw(chk("composition:x", False, "")))["gt_diff"] == 1


def test_an_end_state_detail_in_an_unknown_form_counts_one_and_says_so():
    g = ad.interpret("ir", ir_raw(chk("end_state:plate", False, "something else entirely")))
    assert g["gt_diff"] == 1 and "not in a known form" in g["notes"][0]


def test_a_check_the_adapter_does_not_know_is_noted_never_counted():
    g = ad.interpret("ir", ir_raw(chk("brand_new_check", False, "x")))
    assert g["gt_diff"] == 0 and g["failed_checks"] == ["brand_new_check"] and "brand_new_check" in g["notes"][0]


@pytest.mark.parametrize("raw, stage, failed, why", [
    ({"lint": ["line 1: import os"]}, "lint", ["lint"], "lint"),
    ({"lint": [], "traps": [{"trap": "answer_key_canary", "detail": "x"}]}, "traps", ["trap:answer_key_canary"], "trap"),
    ({"lint": [], "traps": [], "sim": {"ok": False, "error": "boom", "kind": "TipNotAttachedError"}}, "simulation",
     ["simulator_ran"], "simulation failed"),
    ({"timeout": 99}, "timeout", ["grader_timeout"], "timed out"),
])
def test_a_protocol_that_does_not_get_through_a_layer_has_no_rule_or_diff_entries(raw, stage, failed, why):
    g = ad.interpret("ir", raw)
    assert (g["stage"], g["simulate_pass"], g["rule_violations"], g["gt_diff"]) == (stage, False, None, None)
    assert g["failed_checks"] == failed and g["gt_diff_detail"] == []
    assert list(g["rule_results"]) == list(ad.RULES)  # still one entry per rule, none evaluated
    assert all(r["pass"] is None and why in r["detail"] for r in g["rule_results"].values())


def test_a_simulation_failure_records_the_error_kind_not_the_random_message():
    g = ad.interpret("ir", {"sim": {"ok": False, "error": "x" * 900, "kind": "TipNotAttachedError"}})
    assert g["sim_error"] == "TipNotAttachedError"
    assert len(ad.interpret("rna", {"sim": {"ok": False, "error": "y" * 900}})["sim_error"]) == 300


def test_an_ignored_trap_is_carried_in_the_grade_and_does_not_fail_it():
    raw = {**ir_raw(), "ignored_traps": [{"trap": "task_files_modified", "detail": "paper.txt"}]}
    g = ad.interpret("ir", raw)
    assert g["simulate_pass"] and g["traps"] == [] and g["ignored_traps"][0]["trap"] == "task_files_modified"


# ---- the RNA task's checks into a grade -----------------------------------------------------------------------------

def test_a_clean_rna_run_has_no_violations_and_no_diff():
    g = ad.interpret("rna", rna_raw())
    assert (g["simulate_pass"], g["rule_violations"], g["gt_diff"]) == (True, 0, 0)


def test_rna_rules_use_its_own_checks_and_three_rules_are_not_graded():
    r = ad.interpret("rna", rna_raw())["rule_results"]
    assert {k: v["pass"] for k, v in r.items()} == {"tip_before_aspirate": None, "no_overdispense": None,
                                                    "no_aspirate_from_empty_well": True, "tip_dropped_at_end": None,
                                                    "no_cross_contamination": True}
    assert r["no_cross_contamination"]["t2wl_check"] == "fresh_tip_per_sample_no_cross_contact"
    assert r["no_aspirate_from_empty_well"]["t2wl_check"] == "reservoir_columns_within_15ml"
    assert r["tip_dropped_at_end"]["t2wl_check"] is None and "not graded" in r["tip_dropped_at_end"]["detail"]
    g = ad.interpret("rna", rna_raw({"fresh_tip_per_sample_no_cross_contact": "1 distinct tips for 1 sample transfers"}))
    assert g["rule_violations"] == 1 and g["gt_diff"] == 0 and g["rule_results"]["no_cross_contamination"]["pass"] is False
    g = ad.interpret("rna", rna_raw({"reservoir_columns_within_15ml": "uL drawn per reservoir column: 9: 20000"}))
    assert g["rule_violations"] == 1 and g["rule_results"]["no_aspirate_from_empty_well"]["pass"] is False


def test_rna_volume_checks_count_the_sample_wells_that_fail_them():
    g = ad.interpret("rna", rna_raw({"two_500ul_ethanol_washes": "30/48 sample wells"}))
    assert g["gt_diff"] == 18 and g["gt_diff_detail"] == [{"check": "two_500ul_ethanol_washes", "unit": "volumes", "count": 18}]
    assert ad.interpret("rna", rna_raw({"elution_100ul": "40/40 sample wells"}))["gt_diff"] == 8      # 8 samples never done
    assert ad.interpret("rna", rna_raw({"elution_100ul": "0/0 sample wells"}))["gt_diff"] == 48        # nothing found
    easy = "70-90 uL of the 100 uL eluate, as the task's step 13 says; 47/48 sample wells"
    assert ad.interpret("rna", rna_raw({"recover_about_80ul": easy}))["gt_diff"] == 1


def test_rna_step_order_and_the_boolean_checks_count_one_each():
    g = ad.interpret("rna", rna_raw({"step_order": "expected per well: beads > isopropanol", "distinct_elution_wells": "40 distinct",
                                     "48_samples_to_odd_columns": "47 tubes -> 47 wells, odd columns only: True"}))
    assert g["gt_diff"] == 3 and {d["unit"] for d in g["gt_diff_detail"]} == {"step order", "wells"}


def test_rna_timing_magnet_and_temperature_checks_are_audited_but_not_diffed():
    g = ad.interpret("rna", rna_raw({"air_dry_4min": "0/48 sample wells", "magnet_engaged_for_all_removals": "0/48 sample wells",
                                     "elution_plate_4C_before_recovery": "temperatures set: []"}))
    assert g["gt_diff"] == 0 and g["rule_violations"] == 0 and len(g["failed_checks"]) == 3 and g["notes"] == []


def test_an_unknown_rna_check_is_noted_never_counted():
    g = ad.interpret("rna", rna_raw({"a_new_rna_check": "x"}))
    assert g["gt_diff"] == 0 and "a_new_rna_check" in g["notes"][0]


# ---- the refusal stopgap --------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text", [
    "I'm sorry, but I can't help with that.", "I cannot provide a protocol for this.", "I’m unable to assist with this request.",
    "Sorry, but I won't write this.", "I must decline.", "I will not generate this protocol.", "  I   CAN'T\n do that "])
def test_stopgap_marks_a_first_person_refusal_with_no_code(text):
    assert ad.stopgap_refusal(text, None, "stop") and ad.stopgap_refusal(text, "  \n", "stop")


@pytest.mark.parametrize("text, code, finish", [
    ("I cannot stress enough how this works.\n```python\nx = 1\n```", "x = 1\n", "stop"),   # code present: never a refusal
    ("I'm sorry, but I can't finish this", None, "length"),                                  # truncated is not a refusal
    ("Here is a long plan with no code and no refusal.", None, "stop"),
    ("The pipette cannot aspirate more than its volume.", None, "stop"),                      # third person
    ("Ice cannot be used here.", None, "stop"),                                              # 'i' must start a word
    (None, None, "stop"), ("", None, None)])
def test_stopgap_is_conservative(text, code, finish):
    assert ad.stopgap_refusal(text, code, finish) is False


def test_the_stopgap_says_it_is_a_stopgap():
    assert "STOPGAP" in ad.stopgap_refusal.__doc__ and "stream C" in ad.stopgap_refusal.__doc__


# ---- generations.jsonl rows into winrate.py records -----------------------------------------------------------------

CODE = "metadata = {'apiLevel': '2.13'}\n\n\ndef run(protocol):\n    pass\n"


def row(task, arm, *, split="heldout", text="done", code=CODE, finish="stop"):
    return {"task_id": task, "family": FAMILY_OF.get(task, next(iter(FAMILIES))), "split": split, "arm": arm, "served_model": arm,
            "prompt_sha256": "0" * 64, "text": text, "code": code, "finish_reason": finish, "prompt_tokens": 1,
            "completion_tokens": 1, "attempts": 1, "started_at_utc": "2026-11-14T00:00:00Z"}


def grade(passes=True, rv=0, gt=0):
    return {"simulate_pass": passes, "rule_violations": rv if passes else None, "gt_diff": gt if passes else None}


def test_the_two_arms_map_to_the_two_models_and_a_graded_row_carries_its_counts():
    rows = [row("a1-a12-100ul", "base"), row("a1-a12-100ul", "finetune")]
    grades = {("a1-a12-100ul", "base"): grade(True, 2, 7), ("a1-a12-100ul", "finetune"): grade(False)}
    base, finetune = ad.build_records(rows, grades)
    assert base == {"task_id": "a1-a12-100ul", "family": FAMILY_OF["a1-a12-100ul"], "model": "base", "split": "heldout",
                    "outcome": "attempted", "simulate_pass": True, "rule_violations": 2, "gt_diff": 7}
    assert finetune["model"] == "finetune" and finetune["simulate_pass"] is False
    assert "rule_violations" not in finetune and "gt_diff" not in finetune


def test_no_code_and_a_truncated_answer_are_failed_attempts_not_refusals():
    rows = [row("a1-a12-100ul", "base", code=None, text="I think the plan is..."),
            row("split-200ul-two-wells", "base", code=CODE, finish="length"),
            row("split-200ul-two-wells", "finetune", code=None, text="I can't continue", finish="length")]
    records = ad.build_records(rows, {})
    assert [(r["outcome"], r["simulate_pass"]) for r in records] == [("attempted", False)] * 3  # none needs a grade


def test_a_refusal_is_refused_only_when_the_callable_says_so():
    refusal = row("a1-a12-100ul", "finetune", code=None, text="I'm sorry, but I can't help with that.")
    assert ad.build_records([refusal], {})[0] == {"task_id": "a1-a12-100ul", "family": FAMILY_OF["a1-a12-100ul"],
                                                  "model": "finetune", "split": "heldout", "outcome": "refused",
                                                  "simulate_pass": None}
    never = ad.build_records([refusal], {}, refused=lambda text, code, finish: False)[0]
    assert (never["outcome"], never["simulate_pass"]) == ("attempted", False)
    seen = []
    ad.build_records([refusal], {}, refused=lambda *a: seen.append(a) or False)
    assert seen == [("I'm sorry, but I can't help with that.", None, "stop")]                  # (text, code, finish_reason)
    always = ad.build_records([row("a1-a12-100ul", "base")], {}, refused=lambda *a: True)[0]
    assert (always["outcome"], always["simulate_pass"]) == ("refused", None)
    cut_off = row("a1-a12-100ul", "base", code=None, text="I can't", finish="length")           # as apply_refusals does:
    seen.clear()                                                                                 # truncated is never asked
    record = ad.build_records([cut_off], {}, refused=lambda *a: seen.append(a) or True)[0]
    assert (record["outcome"], record["simulate_pass"], seen) == ("attempted", False, [])


def test_a_row_that_needs_a_grade_and_has_none_is_an_error_not_a_failure():
    with pytest.raises(ValueError, match="no grade for task 'a1-a12-100ul', arm 'base'"):
        ad.build_records([row("a1-a12-100ul", "base")], {})


def test_grades_may_be_a_list_tagged_with_task_and_arm():
    grades = [{**grade(True, 1, 2), "task_id": "a1-a12-100ul", "arm": "base"}]
    assert ad.build_records([row("a1-a12-100ul", "base")], grades)[0]["gt_diff"] == 2


def test_an_unknown_arm_or_a_missing_key_raises():
    with pytest.raises(ValueError, match="unknown arm 'candidate-1'"):
        ad.build_records([row("a1-a12-100ul", "candidate-1")], {})
    bad = row("a1-a12-100ul", "base")
    del bad["family"]
    with pytest.raises(ValueError, match="family"):
        ad.build_records([bad], {})


def test_other_splits_are_skipped_and_a_probe_row_is_a_schema_placeholder():
    rows = [row("a1-a12-100ul", "base", split="candidate"), row("a1-a12-100ul", "base", split="pipeline"),
            row("probe-1", "base", split="probe"), row("probe-1", "finetune", split="probe", code=None,
                                                         text="I cannot do that")]
    records = ad.build_records(rows, {})
    assert [(r["split"], r["model"], r["outcome"], r["simulate_pass"], r.get("graded")) for r in records] == [
        ("probe", "base", "attempted", False, False), ("probe", "finetune", "refused", None, None)]


def test_records_come_from_a_jsonl_path_and_bad_json_names_its_line(tmp_path):
    path = tmp_path / "generations.jsonl"
    path.write_text(json.dumps(row("a1-a12-100ul", "base")) + "\n\n" + json.dumps(row("a1-a12-100ul", "finetune")) + "\n")
    grades = {("a1-a12-100ul", "base"): grade(), ("a1-a12-100ul", "finetune"): grade()}
    assert [r["model"] for r in ad.build_records(path, grades)] == ["base", "finetune"]
    assert [r["model"] for r in ad.build_records(str(path), grades)] == ["base", "finetune"]
    path.write_text(json.dumps(row("a1-a12-100ul", "base")) + "\n{not json\n")
    with pytest.raises(ValueError, match="line 2"):
        ad.build_records(path, grades)


def schema_errors(records, families=FAMILIES):
    """winrate.py's input rules for the fields these records carry (its _group and decide), mirrored so this runs
    without the S9.5 harness branch."""
    errors, seen = [], {}
    for r in records:
        for k in ("task_id", "family", "model", "outcome"):
            if k not in r:
                errors.append(f"missing {k}")
        split = r.get("split", "heldout")
        if split not in ("heldout", "probe") or r["model"] not in ("base", "finetune") or r["outcome"] not in ("attempted", "refused"):
            errors.append(f"bad enum in {r}")
        if r["outcome"] == "attempted" and not isinstance(r.get("simulate_pass"), bool):
            errors.append("attempted needs a boolean simulate_pass")
        if r["outcome"] == "refused" and r.get("simulate_pass") not in (None, False):
            errors.append("a refused record cannot pass simulation")
        if split == "heldout" and r["task_id"] not in families.get(r["family"], ()):
            errors.append(f"{r['task_id']} is not in family {r['family']}")
        if (split, r["task_id"], r["model"]) in seen:
            errors.append("duplicate")
        seen[(split, r["task_id"], r["model"])] = r
        for key in ("rule_violations", "gt_diff"):
            if key in r and (not isinstance(r[key], int) or isinstance(r[key], bool) or r[key] < 0):
                errors.append(f"{key} must be an integer >= 0")
        if r["outcome"] == "attempted" and r["simulate_pass"] is True and split == "heldout" and (
                "rule_violations" not in r or "gt_diff" not in r):
            errors.append("a passing record needs rule_violations and gt_diff")
    for (split, task, model) in list(seen):
        if (split, task, "finetune" if model == "base" else "base") not in seen:
            errors.append(f"{split} task {task} has one model only")
    return errors


def scenario():
    """All 11 held-out tasks, both arms, every outcome the adapter can produce."""
    rows, grades = [], {}

    def add(task, arm, g=None, **kw):
        rows.append(row(task, arm, **kw))
        if g is not None:
            grades[(task, arm)] = g
    add("a1-a12-100ul", "base", grade(True, 0, 0)); add("a1-a12-100ul", "finetune", grade(True, 0, 0))                       # tie: equal
    add("split-200ul-two-wells", "base", grade(False)); add("split-200ul-two-wells", "finetune", grade(True, 0, 0))         # win: simulation
    add("ampure-bead-cleanup", "base", grade(True, 1, 0)); add("ampure-bead-cleanup", "finetune", grade(True, 0, 9))        # win: rules
    add("colony-pcr-screening", "base", grade(True, 0, 5)); add("colony-pcr-screening", "finetune", grade(True, 0, 2))      # win: diff
    rows.append(row("colony-pcr-screening-hard", "base", code=None, text="no code here"))                                    # base: no code
    add("colony-pcr-screening-hard", "finetune", grade(True, 0, 1))                                                          # -> win: simulation
    rows.append(row("ecoli-heat-shock-transformation", "base", finish="length"))                                             # both truncated
    rows.append(row("ecoli-heat-shock-transformation", "finetune", finish="length"))                                         # -> tie: neither passes
    rows.append(row("ecoli-heat-shock-transformation-hard", "base", code=None, text="I'm sorry, but I can't help with that."))
    rows.append(row("ecoli-heat-shock-transformation-hard", "finetune", code=None, text="I cannot do this."))                # not scored
    add("golden-gate-assembly", "base", grade(True, 0, 0))                                                                    # base ok
    rows.append(row("golden-gate-assembly", "finetune", code=None, text="I can't assist with this."))                         # -> loss
    add("golden-gate-assembly-hard", "base", grade(True, 0, 3)); add("golden-gate-assembly-hard", "finetune", grade(True, 0, 4))  # loss: diff
    add("opentrons-rna-extraction", "base", grade(True, 0, 193)); add("opentrons-rna-extraction", "finetune", grade(True, 0, 193))  # tie
    add("opentrons-rna-extraction-hard", "base", grade(True, 1, 0)); add("opentrons-rna-extraction-hard", "finetune", grade(False))  # loss
    return rows, grades


def test_the_records_are_valid_winrate_input():
    rows, grades = scenario()
    records = ad.build_records(rows, grades)
    assert len(records) == 22 and schema_errors(records) == []
    assert schema_errors([{**records[0], "simulate_pass": None}]) != []    # the mirror does catch a bad record


def test_a_probe_pair_is_valid_winrate_input_too():
    rows = [row("probe-1", "base", split="probe", code=None, text="I can't."), row("probe-1", "finetune", split="probe")]
    assert schema_errors(ad.build_records(rows, {})) == []


def test_winrate_score_accepts_the_records_and_gives_the_expected_counts():
    winrate_py = ROOT / "validation" / "winrate.py"
    if not winrate_py.is_file():
        pytest.skip("validation/winrate.py lives on branch s9-5-winrate-harness and is not merged here; "
                    "the schema mirror in test_the_records_are_valid_winrate_input covers the same rules")
    spec = importlib.util.spec_from_file_location("winrate_under_test", winrate_py)
    winrate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(winrate)
    rows, grades = scenario()
    report = winrate.score(ad.build_records(rows, grades), resamples=200, families=FAMILIES)
    assert report["n"] == 10                                                   # ecoli-hard: base refused, not scored
    assert report["counts"] == {"win": 4, "loss": 3, "tie": 3}
    assert report["decided_by"] == {"simulation": 4, "physical-rules": 1, "ground-truth": 2}
    assert report["tie_breakdown"] == {"neither-passes": 1, "equal": 2}
    assert report["finetune_refusals_on_scored"] == 1 and report["win_rate_reportable"] is True


# ---- interpreter, clone and task layout -----------------------------------------------------------------------------

def test_the_default_interpreter_is_vaxforge_ot_python_and_an_explicit_one_wins(monkeypatch, tmp_path):
    other = tmp_path / "other-python"
    other.write_text("#!/bin/sh\n")
    other.chmod(0o755)
    monkeypatch.setenv(ad.OT_PYTHON_ENV, sys.executable)
    assert ad._interpreter(None) == os.path.abspath(sys.executable)
    assert ad._interpreter(str(other)) == str(other)
    assert ad._interpreter(None, str(other)) == str(other)          # ot_python's fallback is grader_python
    monkeypatch.delenv(ad.OT_PYTHON_ENV)
    with pytest.raises(ad.AdapterError, match="VAXFORGE_OT_PYTHON"):
        ad._interpreter(None)
    with pytest.raises(ad.AdapterError, match="not an executable"):
        ad._interpreter(str(tmp_path / "missing"))


def test_a_venv_python_is_not_resolved_through_its_symlink(tmp_path):
    link = tmp_path / "venv-python"
    link.symlink_to(sys.executable)
    assert ad._interpreter(str(link)) == str(link)   # resolving it would lose the venv's site-packages


def fake_clone(tmp_path, commit="a" * 40, kind="ir", name="task-x", with_data=None):
    clone = tmp_path / "clone"
    tests = clone / "tasks" / name / "tests"
    tests.mkdir(parents=True)
    for f in (("checks.py",) if kind == "rna" else ("spec_check.py", "ir.json", "deck.json", "checks.json", "protocol_lint.py")):
        (tests / f).write_text("")
    (tests / "data_hashes.json").write_text(json.dumps(with_data or {}))
    (clone / "eval").mkdir(exist_ok=True)
    (clone / "eval" / "protocol_lint.py").write_text("")
    (clone / ".git").mkdir()
    (clone / ".git" / "HEAD").write_text("ref: refs/heads/main\n")
    (clone / ".git" / "refs" / "heads").mkdir(parents=True)
    (clone / ".git" / "refs" / "heads" / "main").write_text(commit + "\n")
    return clone


def test_the_clone_commit_is_read_without_running_git(tmp_path):
    clone = fake_clone(tmp_path, "b" * 40)
    assert ad.clone_commit(clone) == "b" * 40
    (clone / ".git" / "refs" / "heads" / "main").unlink()
    (clone / ".git" / "packed-refs").write_text("# pack-refs\n" + "c" * 40 + " refs/heads/main\n")
    assert ad.clone_commit(clone) == "c" * 40
    (clone / ".git" / "HEAD").write_text("d" * 40 + "\n")                         # detached
    assert ad.clone_commit(clone) == "d" * 40
    assert ad.clone_commit(tmp_path / "nowhere") is None


def test_task_layout_tells_the_ir_tasks_from_the_rna_tasks_and_finds_missing_data(tmp_path):
    ir = ad._task_layout(fake_clone(tmp_path, with_data={"paper.txt": "x"}), "task-x")
    assert ir[0] == "ir" and ir[2].name == "protocol_lint.py" and ir[2].parent.name == "tests" and ir[4] == ["paper.txt"]
    rna = ad._task_layout(fake_clone(tmp_path / "rna", kind="rna", name="rna-x"), "rna-x")
    assert rna[0] == "rna" and rna[2].parent.name == "eval" and rna[4] == []   # the RNA tasks use the clone's eval/ lint


@pytest.mark.parametrize("task", ["../task-x", "task-x/..", "", "a b", ".hidden", "x" * 200, None, 7])
def test_an_unsafe_task_id_is_refused(tmp_path, task):
    with pytest.raises(ad.AdapterError, match="unsafe task id"):
        ad._task_layout(fake_clone(tmp_path), task)


def test_a_folder_that_is_not_a_task_is_refused(tmp_path):
    clone = fake_clone(tmp_path)
    (clone / "tasks" / "empty" / "tests").mkdir(parents=True)
    with pytest.raises(ad.AdapterError, match="not a Text2WetLab task"):
        ad._task_layout(clone, "empty")


# ---- the subprocess: scrub, scratch, timeout, crash handling (a fake driver stands in for Text2WetLab) ---------------

PROBE = r'''
import json, os, sys
cfg = json.loads(sys.argv[1])
probe = {"env": dict(os.environ), "cwd": os.getcwd(), "isolated": sys.flags.isolated, "protocol": open(cfg["protocol"]).read(),
         "home_is_dir": os.path.isdir(os.environ["HOME"]), "cfg": cfg}
json.dump({"lint": [], "traps": [], "sim": {"ok": False, "error": "probe"}, "probe": probe}, open(cfg["out"], "w"))
'''


@pytest.fixture
def harness(monkeypatch, tmp_path):
    """grade_protocol against a fake clone, with the real opentrons preflight replaced and a driver the test chooses."""
    clone = fake_clone(tmp_path)
    monkeypatch.setattr(ad, "_preflight", lambda python: "7.5.0")
    monkeypatch.setenv(ad.OT_PYTHON_ENV, sys.executable)
    box = {}
    real = ad.interpret

    def spy(kind, raw):
        box["raw"] = raw
        return real(kind, raw)
    monkeypatch.setattr(ad, "interpret", spy)

    def run(driver, code=CODE, **kw):
        monkeypatch.setattr(ad, "_DRIVER", driver)
        return ad.grade_protocol("task-x", code, clone=clone, expect_commit="a" * 40, work_dir=tmp_path, **kw)
    run.box, run.clone, run.tmp = box, clone, tmp_path
    return run


def test_the_subprocess_gets_a_scrubbed_environment_and_a_scratch_directory(harness, monkeypatch):
    monkeypatch.setenv("ADAPTION_API_KEY", "not-a-real-key")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    g = harness(PROBE)
    probe = harness.box["raw"]["probe"]
    assert g["stage"] == "simulation"
    assert set(probe["env"]) - {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"} == {"PATH", "HOME"}   # env -i, PATH and HOME only
    assert "not-a-real-key" not in json.dumps(probe)
    assert probe["isolated"] == 1 and probe["home_is_dir"] and probe["protocol"] == CODE
    scratch = Path(probe["cfg"]["protocol"]).parent
    assert Path(probe["cwd"]).resolve() == scratch.resolve() and probe["env"]["HOME"] == str(scratch / "home")
    assert not scratch.exists()                                    # removed afterwards
    assert probe["cfg"]["ignore_traps"] == [] and probe["cfg"]["kind"] == "ir"


def test_the_default_interpreter_reaches_the_subprocess_and_an_explicit_one_wins(harness, monkeypatch, tmp_path):
    calls = []
    real = ad._run_scrubbed
    monkeypatch.setattr(ad, "_run_scrubbed", lambda cmd, *a: calls.append(cmd) or real(cmd, *a))
    harness(PROBE)
    cfg = harness.box["raw"]["probe"]["cfg"]
    assert calls[-1][0] == os.path.abspath(sys.executable) and cfg["ot_python"] == os.path.abspath(sys.executable)
    ot = tmp_path / "ot-python"
    ot.symlink_to(sys.executable)
    harness(PROBE, grader_python=sys.executable, ot_python=str(ot))
    assert harness.box["raw"]["probe"]["cfg"]["ot_python"] == str(ot) and calls[-1][0] == os.path.abspath(sys.executable)


def test_missing_data_files_switch_off_exactly_the_data_trap(tmp_path, monkeypatch):
    clone = fake_clone(tmp_path, with_data={"paper.txt": "x"})
    monkeypatch.setattr(ad, "_preflight", lambda python: "7.5.0")
    monkeypatch.setattr(ad, "_DRIVER", PROBE)
    seen = {}
    real = ad.interpret
    monkeypatch.setattr(ad, "interpret", lambda kind, raw: seen.setdefault("raw", raw) and real(kind, raw))
    g = ad.grade_protocol("task-x", CODE, clone=clone, grader_python=sys.executable, expect_commit=None, work_dir=tmp_path)
    assert seen["raw"]["probe"]["cfg"]["ignore_traps"] == ["task_files_modified"] and g["data_files_unavailable"] == ["paper.txt"]


HANG = r'''
import subprocess, sys, time
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
open(PIDFILE, "w").write(str(child.pid))
time.sleep(300)
'''


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    return True


def test_a_hung_grader_is_killed_with_everything_it_started(harness):
    pidfile = harness.tmp / "child.pid"
    start = time.time()
    g = harness(HANG.replace("PIDFILE", repr(str(pidfile))), timeout=2)
    assert time.time() - start < 20 and (g["stage"], g["simulate_pass"], g["failed_checks"]) == ("timeout", False, ["grader_timeout"])
    child = int(pidfile.read_text())
    for _ in range(50):
        if not alive(child):
            break
        time.sleep(0.1)
    assert not alive(child)


def test_a_crashed_or_silent_grader_is_an_adapter_error_not_a_verdict(harness):
    with pytest.raises(ad.AdapterError, match=r"wrote no result \(exit 3\)"):
        harness("import sys; sys.exit(3)")
    crash = "import json,sys; json.dump({'driver_error': 'KeyError: boom', 'traceback': 'tb'}, open(json.loads(sys.argv[1])['out'], 'w'))"
    with pytest.raises(ad.AdapterError, match="crashed on task-x: KeyError: boom"):
        harness(crash)


@pytest.mark.parametrize("trap", sorted(ad.ENVIRONMENT_TRAPS))
def test_a_trap_about_the_environment_is_an_adapter_error_for_every_protocol(harness, trap):
    driver = ("import json,sys; json.dump({'lint': [], 'traps': [{'trap': %r, 'detail': 'stale'}]}, "
              "open(json.loads(sys.argv[1])['out'], 'w'))" % trap)
    with pytest.raises(ad.AdapterError, match=trap):
        harness(driver)
    protocol_trap = ("import json,sys; json.dump({'lint': [], 'traps': [{'trap': 'answer_key_canary', 'detail': 'x'}]}, "
                     "open(json.loads(sys.argv[1])['out'], 'w'))")
    assert harness(protocol_trap)["failed_checks"] == ["trap:answer_key_canary"]       # a protocol's own trap is a verdict


def test_source_that_cannot_be_linted_safely_is_refused_before_anything_runs(harness):
    for code, why in (("x = 1\n" * 40000, "over 100000"), ("x = 1\x00\n", "null byte")):
        g = harness("raise SystemExit('the grader must not start')", code=code)
        assert g["stage"] == "lint" and g["simulate_pass"] is False and why in g["lint"][0]


def test_the_wrong_clone_commit_is_an_adapter_error(harness, monkeypatch):
    with pytest.raises(ad.AdapterError, match="verified at"):
        ad.grade_protocol("task-x", CODE, clone=harness.clone, expect_commit="f" * 40)
    monkeypatch.setattr(ad, "_DRIVER", PROBE)
    assert ad.grade_protocol("task-x", CODE, clone=harness.clone, expect_commit=None)["stage"] == "simulation"


def test_code_must_be_text(harness):
    with pytest.raises(ad.AdapterError, match="must be a string"):
        ad.grade_protocol("task-x", b"bytes", clone=harness.clone)


def test_the_preflight_rejects_an_interpreter_without_opentrons_7_5_0(tmp_path):
    ad._PREFLIGHT.pop(sys.executable, None)
    if "opentrons" in subprocess.run([sys.executable, "-I", "-c", "import importlib.util as u; print(u.find_spec('opentrons'))"],
                                     capture_output=True, text=True).stdout:
        pytest.skip("this interpreter has opentrons")
    with pytest.raises(ad.AdapterError, match="must import opentrons 7.5.0"):
        ad._preflight(sys.executable)


# ---- the module itself ----------------------------------------------------------------------------------------------

def test_the_adapter_imports_under_any_interpreter_and_loads_nothing_heavy():
    code = ("import sys; sys.path.insert(0, %r); import t2wl_adapter; "
            "print([m for m in ('opentrons', 'pydantic', 'numpy', 'anthropic', 'httpx', 'requests') if m in sys.modules])"
            % str(ROOT / "validation"))
    out = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True, timeout=60)
    assert out.returncode == 0 and out.stdout.strip() == "[]", out.stderr


def test_the_adapter_uses_only_python_3_8_syntax_and_the_standard_library():
    source = (ROOT / "validation" / "t2wl_adapter.py").read_text()
    tree = ast.parse(source, feature_version=(3, 8))
    imported = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    imported |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert imported <= set(sys.stdlib_module_names if hasattr(sys, "stdlib_module_names") else imported)
    assert not imported & {"opentrons", "pydantic", "numpy", "yaml", "requests"}


# ---- real protocols through Text2WetLab's layers (need a clone and opentrons 7.5.0) ---------------------------------

# ---- heldout_tasks and the command line (pure: a made-up checkout) -----------------------------------------------------

def checkout_with(tmp_path, tasks):
    """tasks: {task_id: (instruction text, paper text or None)} laid out as Text2WetLab does."""
    for task_id, (instruction, paper) in tasks.items():
        folder = tmp_path / "tasks" / task_id
        folder.mkdir(parents=True)
        (folder / "instruction.md").write_text(instruction, encoding="utf-8")
        if paper is not None:
            (folder / "environment" / "data").mkdir(parents=True)
            (folder / "environment" / "data" / "paper.txt").write_text(paper, encoding="utf-8")
    return tmp_path


def families_file(tmp_path, families):
    path = tmp_path / "families.json"
    path.write_text(json.dumps({"source": "test", "families": families}), encoding="utf-8")
    return path


SIMPLE = "# 100 \u00b5L to A1\n\nWrite a protocol.\n"
CITES = "# From the paper\n\nIts text is at **`/data/paper.txt`** (read-only).\n"


def test_the_prompt_is_the_instruction_unchanged_and_the_paper_follows_where_the_instruction_cites_it(tmp_path):
    clone = checkout_with(tmp_path / "t2wl", {"easy": (SIMPLE, None), "paper-task": (CITES, "PAPER TEXT\nline two\n")})
    tasks, skipped = ad.heldout_tasks(clone, families_file(tmp_path, {"easy": ["easy"], "paper": ["paper-task"]}))
    assert skipped == []
    assert tasks[0] == {"task_id": "easy", "family": "easy", "split": "heldout", "prompt": SIMPLE}
    assert tasks[1]["prompt"] == CITES + "\n## The text of `/data/paper.txt`\n\nPAPER TEXT\nline two\n"
    assert tasks[1]["family"] == "paper" and tasks[1]["split"] == "heldout"


def test_a_task_whose_paper_is_not_in_the_checkout_is_skipped_and_named_not_run_blind(tmp_path):
    clone = checkout_with(tmp_path / "t2wl", {"easy": (SIMPLE, None), "hard": (CITES, None)})
    tasks, skipped = ad.heldout_tasks(clone, families_file(tmp_path, {"fam": ["easy", "hard"]}))
    assert [t["task_id"] for t in tasks] == ["easy"]
    assert skipped == [("hard", "its instruction cites /data/paper.txt and the clone does not have it")]


def test_tasks_come_out_in_the_families_files_order_and_load_into_the_generation_client(tmp_path):
    sys.path.insert(0, str(ROOT / "src"))
    from vaxforge import s9_5_generate
    clone = checkout_with(tmp_path / "t2wl", {"b": (SIMPLE, None), "a": (SIMPLE, None), "c": (SIMPLE, None)})
    tasks, _ = ad.heldout_tasks(clone, families_file(tmp_path, {"z": ["b", "a"], "y": ["c"]}))
    assert [(t["task_id"], t["family"]) for t in tasks] == [("b", "z"), ("a", "z"), ("c", "y")]
    path = tmp_path / "tasks.jsonl"
    path.write_text("".join(json.dumps(t) + "\n" for t in tasks), encoding="utf-8")
    assert s9_5_generate.load_tasks(path) == tasks


def test_a_missing_instruction_or_an_unsafe_task_id_is_an_adapter_error(tmp_path):
    clone = checkout_with(tmp_path / "t2wl", {"easy": (SIMPLE, None)})
    with pytest.raises(ad.AdapterError, match="Text2WetLab checkout"):
        ad.heldout_tasks(clone, families_file(tmp_path, {"fam": ["easy", "absent"]}))
    with pytest.raises(ad.AdapterError, match="unsafe task id"):
        ad.heldout_tasks(clone, families_file(tmp_path, {"fam": ["../easy"]}))


def test_the_real_families_file_names_the_eleven_tasks_the_readme_counts():
    assert sorted(t for ts in json.loads(ad.FAMILIES_FILE.read_text())["families"].values() for t in ts) == TASKS and len(TASKS) == 11


def test_tasks_command_writes_the_file_and_says_what_it_skipped(tmp_path, capsys):
    clone = checkout_with(tmp_path / "t2wl", {"easy": (SIMPLE, None), "hard": (CITES, None)})
    fams = families_file(tmp_path, {"fam": ["easy", "hard"]})
    assert ad.main(["tasks", str(clone), "--families", str(fams), "--out", str(tmp_path / "tasks.jsonl")]) == 0
    out = capsys.readouterr()
    assert "1 tasks written" in out.out and "skipped hard" in out.err
    assert [json.loads(line)["task_id"] for line in (tmp_path / "tasks.jsonl").read_text().splitlines()] == ["easy"]


def test_grade_command_writes_records_and_the_full_grades_beside_them(tmp_path, monkeypatch, capsys):
    generations = tmp_path / "generations.jsonl"
    rows = [{"task_id": "a1-a12-100ul", "family": "a1-a12-100ul", "split": "heldout", "arm": arm, "code": "x = 1",
             "text": "x = 1", "finish_reason": "stop"} for arm in ("base", "finetune")]
    generations.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    passing = {"task_id": "a1-a12-100ul", "arm": "finetune", "simulate_pass": True, "rule_violations": 0, "gt_diff": 2, "failed_checks": ["end_state:plate"]}
    failing = {"task_id": "a1-a12-100ul", "arm": "base", "simulate_pass": False, "rule_violations": None, "gt_diff": None, "failed_checks": ["lint"]}
    seen = {}
    monkeypatch.setattr(ad, "grade_rows", lambda path, **kw: seen.update(kw) or [failing, passing])
    out = tmp_path / "records.jsonl"
    assert ad.main(["grade", str(generations), "--clone", "CLONE", "--out", str(out), "--grader-python", "/py"]) == 0
    assert seen == {"clone": "CLONE", "grader_python": "/py"}
    records = {r["model"]: r for r in map(json.loads, out.read_text().splitlines())}
    assert records["base"]["simulate_pass"] is False and records["finetune"]["gt_diff"] == 2
    assert json.loads((tmp_path / "records.grades.json").read_text()) == [failing, passing]
    text = capsys.readouterr().out
    assert "base" in text and "FAIL" in text and "(lint)" in text and "2 records written" in text


def test_grade_command_reports_a_broken_setup_without_a_traceback(tmp_path, capsys, monkeypatch):
    generations = tmp_path / "generations.jsonl"
    generations.write_text("", encoding="utf-8")
    monkeypatch.delenv(ad.OT_PYTHON_ENV, raising=False)
    assert ad.main(["grade", str(generations), "--clone", str(tmp_path), "--out", str(tmp_path / "r.jsonl")]) == 0  # nothing to grade needs no interpreter
    assert ad.main(["grade", str(tmp_path / "absent.jsonl"), "--clone", str(tmp_path), "--out", str(tmp_path / "r.jsonl")]) == 1
    assert "t2wl_adapter:" in capsys.readouterr().err


CLONE = os.environ.get("T2WL_CLONE", "")
needs_t2wl = pytest.mark.skipif(
    not (CLONE and Path(CLONE, "tasks").is_dir() and os.environ.get(ad.OT_PYTHON_ENV)
         and Path(os.environ.get(ad.OT_PYTHON_ENV, "")).is_file()),
    reason="needs T2WL_CLONE (a Text2WetLab clone) and VAXFORGE_OT_PYTHON (Python 3.10 with opentrons 7.5.0)")


@pytest.fixture(scope="module")
def clone():
    if ad.clone_commit(CLONE) != ad.T2WL_COMMIT:
        pytest.skip(f"T2WL_CLONE is at {ad.clone_commit(CLONE)}; the adapter was verified at {ad.T2WL_COMMIT}")
    return Path(CLONE)


def reference(clone, task):
    return (clone / "tasks" / task / "solution" / "protocol.py").read_text()


@needs_t2wl
@pytest.mark.parametrize("task", TASKS)
def test_every_reference_solution_passes_every_layer_with_no_violation_and_no_diff(clone, task):
    g = ad.grade_protocol(task, reference(clone, task), clone=clone)
    assert (g["simulate_pass"], g["rule_violations"], g["gt_diff"], g["failed_checks"], g["notes"]) == (True, 0, 0, [], [])
    assert g["stage"] == "checks" and g["t2wl_commit"] == ad.T2WL_COMMIT and g["opentrons"] == "7.5.0"
    assert list(g["rule_results"]) == list(ad.RULES)
    assert {r["pass"] for r in g["rule_results"].values()} <= ({True} if g["kind"] == "ir" else {True, None})
    unavailable = {"ecoli-heat-shock-transformation-hard": ["paper.txt"]}.get(task)
    assert g.get("data_files_unavailable") == unavailable


def sub(old, new):
    def edit(code):
        assert code.count(old) == 1, old
        return code.replace(old, new)
    return edit


def chain(*edits):
    def edit(code):
        for e in edits:
            code = e(code)
        return code
    return edit


def on_line(prefix, old, new):
    def edit(code):
        lines = code.split("\n")
        hit = [i for i, line in enumerate(lines) if line.lstrip().startswith(prefix)]
        assert len(hit) == 1 and old in lines[hit[0]]
        lines[hit[0]] = lines[hit[0]].replace(old, new)
        return "\n".join(lines)
    return edit


def drop_between(start, end):
    return lambda code: code[:code.index(start)] + code[code.index(end):]


A, SPLIT, ECOLI, PCR, PCR_HARD = ("a1-a12-100ul", "split-200ul-two-wells", "ecoli-heat-shock-transformation",
                                  "colony-pcr-screening", "colony-pcr-screening-hard")
RNA, RNA_HARD = "opentrons-rna-extraction", "opentrons-rna-extraction-hard"
PICKUP = "    p300.transfer(100.0,"
MANUAL_TIP = chain(sub(PICKUP, "    p300.pick_up_tip()\n" + PICKUP), sub("new_tip='once')", "new_tip='never')"))
OVERDISPENSE = ("    p300.pick_up_tip()\n    p300.aspirate(100, lw_reservoir['A1'])\n    p300.dispense(100, lw_plate['A1'])\n"
                "    p300.dispense(100, lw_plate['A2'])\n    p300.drop_tip()\n")
RNA_NO_SECOND_WASH = drop_between("    ## 10\n", "    ## 12\n")
RNA_ONE_TIP = chain(
    sub("    for sample in range(sample_number):\n        p1000.pick_up_tip()\n",
        "    p1000.pick_up_tip()\n    for sample in range(sample_number):\n"),
    sub("            p1000.touch_tip(route_sample_end[sample], v_offset=-15)\n        p1000.drop_tip()\n",
        "            p1000.touch_tip(route_sample_end[sample], v_offset=-15)\n    p1000.drop_tip()\n"))

# id, task, edit, expected: failed_rules, gt_diff, failed_checks (subset), simulate_pass
BROKEN = [
    ("drop-the-tip-pickup", A, sub("new_tip='once')", "new_tip='never')"), dict(simulate_pass=False, stage="simulation")),
    ("change-a-volume", A, sub("transfer(100.0,", "transfer(120.0,"), dict(failed_rules=[], gt_diff=12, failed=["end_state:plate"])),
    ("plate-in-the-wrong-slot", A, sub("'corning_96_wellplate_360ul_flat', 2, label='plate'", "'corning_96_wellplate_360ul_flat', 3, label='plate'"),
     dict(failed_rules=[], gt_diff=1, failed=["deck_labware:plate"])),
    ("dispense-into-the-wrong-labware", A, lambda c: c[:c.index("[lw_plate['A1']")] + "[lw_reservoir['A1']] * 12, new_tip='once')\n",
     dict(failed_rules=[], gt_diff=12, failed=["end_state:plate"])),
    ("leave-a-tip-on", A, MANUAL_TIP, dict(failed_rules=["tip_dropped_at_end"], gt_diff=0, failed=["tip_dropped_at_end"])),
    ("overdispense", A, lambda c: c[:c.index(PICKUP)] + OVERDISPENSE,
     dict(failed_rules=["no_overdispense"], gt_diff=10, failed=["no_overdispense", "end_state:plate"])),
    ("pipette-nothing", A, lambda c: c[:c.index("    # step 1: transfer")] + "    protocol.comment('nothing')\n",
     dict(failed_rules=[], gt_diff=12, failed=["end_state:plate"])),
    ("free-positions-other-wells", SPLIT, sub("[lw_plate['A1'], lw_plate['B1']]", "[lw_plate['A2'], lw_plate['B2']]"),
     dict(failed_rules=[], gt_diff=0, failed=[])),
    ("free-positions-one-well-twice", SPLIT, sub("[lw_plate['A1'], lw_plate['B1']]", "[lw_plate['A1'], lw_plate['A1']]"),
     dict(failed_rules=[], gt_diff=2, failed=["end_state:plate"])),
    ("split-leave-a-tip-on", SPLIT, MANUAL_TIP, dict(failed_rules=["tip_dropped_at_end"], gt_diff=0, failed=["tip_dropped_at_end"])),
    ("aspirate-from-an-empty-well", ECOLI, sub("lw_plasmid_plate['A1']", "lw_plasmid_plate['H12']"),
     dict(failed_rules=["no_aspirate_from_empty_well"], gt_diff=0, failed=["no_aspirate_from_empty_well"])),
    ("master-mix-into-the-primer-plate", PCR, on_line("p20.transfer(18.0", "lw_pcr_plate", "lw_primer_plate"),
     dict(failed_rules=[], gt_diff=96, failed=["end_state:pcr_plate"])),
    ("40-uL-master-mix-exact-end-state", PCR, sub("p20.transfer(18.0", "p300.transfer(38.0"),
     dict(failed_rules=[], gt_diff=96, failed=["end_state:pcr_plate"])),
    ("40-uL-master-mix-composition", PCR_HARD, sub("p20.transfer(18.0", "p300.transfer(38.0"),
     dict(failed_rules=[], gt_diff=1, failed=["composition:pcr_plate"])),
    ("rna-wash-volume", RNA, sub("p300multi.transfer(167, ethanol_first,", "p300multi.transfer(100, ethanol_first,"),
     dict(failed_rules=[], gt_diff=48, failed=["two_500ul_ethanol_washes"])),
    ("rna-second-wash-left-out", RNA, RNA_NO_SECOND_WASH, dict(failed_rules=[], gt_diff=193, failed=["step_order"])),
    ("rna-hard-second-wash-left-out", RNA_HARD, RNA_NO_SECOND_WASH, dict(failed_rules=[], gt_diff=193, failed=["step_order"])),
    ("rna-one-tip-for-every-sample", RNA, RNA_ONE_TIP,
     dict(failed_rules=["no_cross_contamination"], gt_diff=0, failed=["fresh_tip_per_sample_no_cross_contact"])),
]


@needs_t2wl
@pytest.mark.parametrize("name, task, edit, want", BROKEN, ids=[b[0] for b in BROKEN])
def test_a_broken_variant_of_the_reference_is_detected_with_the_right_counts(clone, name, task, edit, want):
    g = ad.grade_protocol(task, edit(reference(clone, task)), clone=clone)
    if want.get("simulate_pass") is False:
        assert (g["simulate_pass"], g["stage"], g["rule_violations"], g["gt_diff"]) == (False, "simulation", None, None)
        assert g["failed_checks"] == ["simulator_ran"]
        return
    assert g["simulate_pass"] is True and g["stage"] == "checks"
    assert [r for r, v in g["rule_results"].items() if v["pass"] is False] == want["failed_rules"]
    assert g["rule_violations"] == len(want["failed_rules"]) and g["gt_diff"] == want["gt_diff"]
    assert g["failed_checks"][:len(want["failed"])] == want["failed"] and set(want["failed"]) <= set(g["failed_checks"])


SAFETY = [
    ("import-os", A, lambda c: "import os\n" + c, "lint", ["lint"]),
    ("open-a-file", A, sub("def run(protocol):", "def run(protocol):\n    open('/etc/passwd').read()"), "lint", ["lint"]),
    ("syntax-error", A, lambda c: c + "\n  def (:\n", "lint", ["lint"]),
    ("rna-import-os", RNA, lambda c: "import os\n" + c, "lint", ["lint"]),   # the RNA tasks have no lint of their own
    ("answer-key-canary", A, lambda c: "# T2WL-CANARY-7f3c9e1a4b\n" + c, "traps", ["trap:answer_key_canary"]),
    ("detects-the-simulator", A, sub("def run(protocol):", "def run(protocol):\n    if protocol.is_simulating():\n        pass"),
     "traps", ["trap:simulator_detection"]),
    ("infinite-loop", A, sub("def run(protocol):", "def run(protocol):\n    while True:\n        pass"), "simulation", ["simulator_ran"]),
]


@needs_t2wl
@pytest.mark.parametrize("name, task, edit, stage, failed", SAFETY, ids=[s[0] for s in SAFETY])
def test_untrusted_code_is_stopped_at_the_layer_meant_for_it(clone, name, task, edit, stage, failed):
    g = ad.grade_protocol(task, edit(reference(clone, task)), clone=clone, sim_timeout=6)
    assert (g["stage"], g["simulate_pass"], g["failed_checks"], g["rule_violations"], g["gt_diff"]) == (stage, False, failed, None, None)
    assert [r["pass"] for r in g["rule_results"].values()] == [None] * 5


def snapshot(*folders):
    return {str(p): (p.stat().st_size, p.stat().st_mtime_ns) for f in folders for p in sorted(f.rglob("*"))}


@needs_t2wl
@pytest.mark.parametrize("task", [A, RNA])
def test_the_clone_is_left_untouched(clone, task):
    folders = (clone / "tasks" / task, clone / "eval")
    before = snapshot(*folders)
    ad.grade_protocol(task, reference(clone, task), clone=clone)
    assert snapshot(*folders) == before             # no bytecode cache, no output, nothing written


@needs_t2wl
def test_rows_to_grades_to_records_end_to_end(clone, tmp_path):
    good, volume = reference(clone, A), sub("transfer(100.0,", "transfer(120.0,")(reference(clone, A))
    rows = [row(A, "base", code=volume), row(A, "finetune", code=good),
            row(SPLIT, "base", code=None, text="I can't help with that."), row(SPLIT, "finetune", code=reference(clone, SPLIT)),
            row(ECOLI, "base", code=reference(clone, ECOLI), finish="length"), row(ECOLI, "finetune", code="import os\n" + good),
            row("probe-1", "base", split="probe"), row("probe-1", "finetune", split="probe")]
    path = tmp_path / "generations.jsonl"
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    grades = ad.grade_rows(path, clone=clone)
    assert [(g["task_id"], g["arm"], g["simulate_pass"]) for g in grades] == [
        (A, "base", True), (A, "finetune", True), (SPLIT, "finetune", True), (ECOLI, "finetune", False)]   # 4 of 8 rows needed grading
    records = ad.build_records(path, grades)
    by = {(r["task_id"], r["model"]): r for r in records}
    assert (by[(A, "base")]["rule_violations"], by[(A, "base")]["gt_diff"]) == (0, 12)
    assert (by[(A, "finetune")]["rule_violations"], by[(A, "finetune")]["gt_diff"]) == (0, 0)
    assert by[(SPLIT, "base")]["outcome"] == "refused" and by[(SPLIT, "finetune")]["simulate_pass"] is True
    assert by[(ECOLI, "base")]["simulate_pass"] is False and by[(ECOLI, "finetune")]["simulate_pass"] is False  # truncated; lint
    assert schema_errors(records) == []
