"""Tests for S10 (animal study proposal). Fixture: a Bexsero-like candidate set for
N. meningitidis serogroup B (fHbp, NadA, NHBA; correlate hSBA).

The peptides below are SYNTHETIC placeholders standing in for S5 output; they are not real
epitope predictions and no result of any kind is simulated.
"""

import json
import re

import pytest

from vaxforge.s10_animal import (
    ARRIVE_ESSENTIAL_10,
    EFFECT_ASSUMPTION_LABEL,
    FORBIDDEN_PROCEDURE_TERMS,
    ArmRole,
    DoseRanging,
    Route,
    Species,
    StudyConfig,
    StudyDesign,
    StudyGoal,
    design_study,
    from_json,
    outstanding_human_inputs,
    power_two_sample_t,
    render_markdown,
    required_n_per_group,
    result_like_field_names,
    scan_forbidden_procedure_text,
    to_json,
    validate_design,
)
from vaxforge.streamc_models import RANK_SOURCE_USER_SELECTION, Candidate, Epitope, PathogenContext

FHBP_T1 = ["AAKLTVAGS", "GSEKLTVAA", "TVAGSEKLT"]
FHBP_T2 = ["KLTVAGSEKLTVAGS", "AGSEKLTVAGSEKLT"]
FHBP_B_LIN = "GSEKLTVAGS"
FHBP_B_CONF = "TVAGSEKLTVAG"


def fhbp() -> Candidate:
    eps = [Epitope(peptide=p, kind="T_class_I", start=10 + 5 * i, score=0.5, hla=["HLA-A*02:01"]) for i, p in enumerate(FHBP_T1)]
    eps += [Epitope(peptide=p, kind="T_class_II", start=60 + 5 * i, hla=["HLA-DRB1*01:01"]) for i, p in enumerate(FHBP_T2)]
    eps.append(Epitope(peptide=FHBP_B_LIN, kind="B_linear", start=31))
    eps.append(Epitope(peptide=FHBP_B_CONF, kind="B_conformational"))
    return Candidate(
        candidate_id="c1", protein_name="fHbp", rank=1, length_aa=274, localisation="outer membrane lipoprotein",
        protegenicity=0.93, epitopes=eps, pc90=0.88, coverage_by_population={"Europe": 0.91, "East Asia": 0.84},
        structure_confidence=0.82, structure_validators=["esmfold", "af2-no-msa"],
    )


def nada() -> Candidate:  # T-class-I epitopes only; no B, S6 or S7 data
    return Candidate(
        candidate_id="c2", protein_name="NadA", rank=2, protegenicity=0.88,
        epitopes=[Epitope(peptide="LLAGSEKLA", kind="T_class_I", start=5, hla=["HLA-B*07:02"])],
    )


def nhba() -> Candidate:  # S3 only: every other upstream field missing
    return Candidate(candidate_id="c3", protein_name="NHBA", rank=3, protegenicity=0.80)


MENB = PathogenContext(name="Neisseria meningitidis serogroup B", taxid=487, correlate_of_protection="hSBA")
NO_CORRELATE = PathogenContext(name="Hypothetical pathogen", correlate_of_protection=None)

HLA_CFG = StudyConfig(goal=StudyGoal.HLA_EPITOPE_VALIDATION, hla_transgenic_lines=["HLA-A2 / DR1 line (placeholder)"], adjuvant="CpG")


def trio() -> list:
    return [fhbp(), nada(), nhba()]


def row(design: StudyDesign, cid: str, stage: str):
    return next(r for r in design.falsification_map if r.candidate_id == cid and r.upstream_stage == stage)


def many(n: int) -> list:
    return [Candidate(candidate_id=f"c{i}", protein_name=f"P{i}", rank=i) for i in range(1, n + 1)]


# ------------------------------------------------------------------ power analysis


def test_power_matches_textbook_values():
    # Cohen's classic two-sample t-test table: alpha 0.05 two-sided, power 0.8.
    assert required_n_per_group(0.5, 0.05, 0.8) == 64
    assert required_n_per_group(0.8, 0.05, 0.8) == 26
    assert required_n_per_group(1.0, 0.05, 0.8) == 17


def test_n_is_minimal_integer_meeting_power():
    for d, a, p in [(1.4, 0.05, 0.8), (1.4, 0.05 / 3, 0.9), (0.9, 0.01, 0.8)]:
        n = required_n_per_group(d, a, p)
        assert power_two_sample_t(n, d, a) >= p
        assert power_two_sample_t(n - 1, d, a) < p


def test_n_monotone_in_number_of_candidates():
    ns = [design_study(many(k), MENB).power_analysis.n_per_group_analysed for k in range(1, 8)]
    assert all(b >= a for a, b in zip(ns, ns[1:]))
    assert ns[-1] > ns[0]


def test_n_monotone_in_effect_size():
    ns = [design_study(many(3), MENB, StudyConfig(assumed_effect_log10=e)).power_analysis.n_per_group_analysed
          for e in (1.2, 0.9, 0.7, 0.5, 0.35)]  # shrinking effect
    assert all(b >= a for a, b in zip(ns, ns[1:]))
    assert ns[-1] > ns[0]


def test_n_monotone_in_power():
    ns = [design_study(many(3), MENB, StudyConfig(power=p)).power_analysis.n_per_group_analysed for p in (0.7, 0.8, 0.9, 0.95)]
    assert all(b >= a for a, b in zip(ns, ns[1:]))
    assert ns[-1] > ns[0]


def test_n_grows_with_sd_assumption():
    lo = design_study(many(2), MENB, StudyConfig(assumed_sd_log10=0.3)).power_analysis.n_per_group_analysed
    hi = design_study(many(2), MENB, StudyConfig(assumed_sd_log10=0.8)).power_analysis.n_per_group_analysed
    assert hi > lo


def test_multiplicity_correction_is_applied_and_stated():
    pa = design_study(many(4), MENB, StudyConfig(alpha=0.05, multiplicity_method="bonferroni")).power_analysis
    assert pa.n_comparisons == 4 and pa.multiplicity_method == "bonferroni"
    assert pa.alpha_per_comparison == pytest.approx(0.05 / 4)
    assert pa.n_per_group_analysed == required_n_per_group(pa.effect_size_d, 0.05 / 4, pa.target_power)
    d = design_study(many(4), MENB, StudyConfig(multiplicity_method="holm"))
    assert "Holm" in d.analysis_plan.multiplicity_detail
    assert d.analysis_plan.multiplicity_method == "holm"


def test_n_candidates_compared_override_changes_n():
    base = design_study(many(2), MENB).power_analysis
    wider = design_study(many(2), MENB, StudyConfig(n_candidates_compared=10)).power_analysis
    assert wider.n_comparisons == 10 and wider.n_per_group_analysed > base.n_per_group_analysed
    # under-correcting is flagged
    d = design_study(many(5), MENB, StudyConfig(n_candidates_compared=1))
    assert any("multiplicity" in e for e in validate_design(d))


def test_attrition_allowance_is_a_stated_field():
    d0 = design_study(many(2), MENB).power_analysis
    assert d0.n_per_group_allocated == d0.n_per_group_analysed  # no padding by default
    d1 = design_study(many(2), MENB, StudyConfig(attrition_fraction=0.1)).power_analysis
    assert d1.n_per_group_analysed == d0.n_per_group_analysed
    assert d1.n_per_group_allocated > d1.n_per_group_analysed
    assert d1.total_animals == d1.n_arms * d1.n_per_group_allocated


def test_effect_size_labelled_assumption_by_default():
    d = design_study(trio(), MENB)
    assert d.power_analysis.effect_size_status == EFFECT_ASSUMPTION_LABEL
    assert EFFECT_ASSUMPTION_LABEL in render_markdown(d)
    pilot = design_study(trio(), MENB, StudyConfig(effect_size_source="pilot run P-001"))
    assert "pilot run P-001" in pilot.power_analysis.effect_size_status
    assert not pilot.power_analysis.effect_size_status.startswith("ASSUMPTION")
    assert validate_design(pilot) == []


def test_invalid_numeric_config_rejected():
    for bad in ({"alpha": 0.0}, {"power": 1.0}, {"assumed_sd_log10": 0.0}, {"assumed_effect_log10": -1.0}):
        with pytest.raises(ValueError):  # pydantic ValidationError subclasses ValueError (v1 and v2)
            StudyConfig(**bad)


# --------------------------------------------------------------------------- arms


@pytest.mark.parametrize(
    "cfg",
    [
        StudyConfig(),
        HLA_CFG,
        StudyConfig(goal=StudyGoal.POLYCLONAL_SERA),
        StudyConfig(route=Route.IN, adjuvant="CpG"),
        StudyConfig(licensed_vaccine_exists=True, licensed_vaccine_name="Bexsero (4CMenB)"),
        StudyConfig(licensed_vaccine_exists=False),
        StudyConfig(dose_ranging=DoseRanging.ALL_CANDIDATES),
    ],
)
def test_adjuvant_only_and_naive_arms_always_present(cfg):
    d = design_study(trio(), MENB, cfg)
    roles = [a.role for a in d.arms]
    assert roles.count(ArmRole.ADJUVANT_ONLY) == 1 and roles.count(ArmRole.NAIVE_PBS) == 1
    assert all(a.n_per_group == d.power_analysis.n_per_group_allocated for a in d.arms)


def test_every_candidate_has_an_arm_and_one_confirmatory_arm():
    d = design_study(trio(), MENB)
    for cid in ("c1", "c2", "c3"):
        mine = [a for a in d.arms if a.candidate_id == cid]
        assert mine and sum(a.primary_comparison for a in mine) == 1
    assert d.power_analysis.n_comparisons == 3


def test_dose_ranging_has_at_least_three_levels_for_top_candidate():
    d = design_study(trio(), MENB)
    assert len(d.dosing.dose_levels_ug) >= 3
    assert len([a for a in d.arms if a.candidate_id == "c1"]) >= 3
    assert len([a for a in d.arms if a.candidate_id == "c2"]) == 1  # default: top-ranked only
    allc = design_study(trio(), MENB, StudyConfig(dose_ranging=DoseRanging.ALL_CANDIDATES))
    assert all(len([a for a in allc.arms if a.candidate_id == c]) >= 3 for c in ("c1", "c2", "c3"))
    assert allc.power_analysis.total_animals > d.power_analysis.total_animals
    assert "ASSUMPTION" in d.dosing.levels_status  # placeholder doses are labelled


def test_positive_control_only_where_licensed_vaccine_exists_and_unknown_says_check():
    unknown = design_study(trio(), MENB)  # licensed_vaccine_exists=None
    assert ArmRole.POSITIVE_CONTROL not in [a.role for a in unknown.arms]
    assert unknown.positive_control.status == "check"
    assert "check" in render_markdown(unknown).lower()
    yes = design_study(trio(), MENB, StudyConfig(licensed_vaccine_exists=True, licensed_vaccine_name="Bexsero (4CMenB)"))
    assert [a.role for a in yes.arms].count(ArmRole.POSITIVE_CONTROL) == 1
    assert yes.positive_control.status == "included"
    no = design_study(trio(), MENB, StudyConfig(licensed_vaccine_exists=False))
    assert ArmRole.POSITIVE_CONTROL not in [a.role for a in no.arms]
    assert no.positive_control.status == "not_applicable_none_exists"
    for d in (unknown, yes, no):
        assert validate_design(d) == []


def test_adjuvant_and_route_have_rationale():
    d = design_study(trio(), MENB)
    assert d.adjuvant.th_bias == "Th2" and "Th2" in d.adjuvant.rationale
    tlr = design_study(trio(), MENB, StudyConfig(adjuvant="MPLA"))
    assert tlr.adjuvant.th_bias == "Th1"
    assert d.route.target_compartment and d.route.justification
    unknown = design_study(trio(), MENB, StudyConfig(adjuvant="MysteryAdj"))
    assert any("adjuvant has no rationale" in e for e in validate_design(unknown))
    ok = design_study(trio(), MENB, StudyConfig(adjuvant="MysteryAdj", adjuvant_rationale="Chosen by the lab lead."))
    assert not any("adjuvant" in e for e in validate_design(ok))


def test_default_schedule_is_prime_boost():
    s = design_study(trio(), MENB).schedule
    assert s.immunisation_days == [0, 21, 42] and s.bleed_days == [0, 20, 41, 56] and s.primary_timepoint_day == 56


def test_bad_schedule_is_flagged():
    d = design_study(trio(), MENB, StudyConfig(immunisation_days=[0, 21, 42], bleed_days=[0, 20, 41]))
    assert any("final bleed" in e or "primary timepoint" in e for e in validate_design(d))
    d2 = design_study(trio(), MENB, StudyConfig(dose_levels_ug=[1.0, 5.0]))
    assert any("3 distinct dose levels" in e for e in validate_design(d2))


# ------------------------------------------------------------- falsification mapping


def test_falsification_rows_built_from_real_epitope_data():
    d = design_study(trio(), MENB, HLA_CFG)
    t = row(d, "c1", "S5_T_cell")
    assert t.status == "testable"
    class_i = {p.peptide for pool in t.peptide_pools if pool.epitope_class == "T_class_I" for p in pool.peptides}
    class_ii = {p.peptide for pool in t.peptide_pools if pool.epitope_class == "T_class_II" for p in pool.peptides}
    assert class_i == set(FHBP_T1) and class_ii == set(FHBP_T2)
    assert t.upstream_data["n_class_I"] == 3 and t.upstream_data["n_class_II"] == 2
    assert set(t.upstream_data["hla_alleles"]) == {"HLA-A*02:01", "HLA-DRB1*01:01"}
    assert "ELISpot" in t.animal_readout and "NOT to the predicted pools" in t.falsified_if

    b = row(d, "c1", "S5_B_cell")
    peps = {p.peptide for pool in b.peptide_pools for p in pool.peptides}
    assert peps == {FHBP_B_LIN, FHBP_B_CONF}
    assert "Competition ELISA" in b.animal_readout and "conformational" in b.note

    s6 = row(d, "c1", "S6")
    assert s6.status == "testable" and s6.upstream_data["pc90"] == 0.88
    assert s6.upstream_data["coverage_by_population"] == {"Europe": 0.91, "East Asia": 0.84}
    assert "0.88" in s6.upstream_prediction and "Europe" in s6.upstream_prediction

    s7 = row(d, "c1", "S7")
    assert s7.status == "testable" and s7.upstream_data["structure_validators"] == ["esmfold", "af2-no-msa"]
    assert "esmfold" in s7.note and "denatured" in s7.animal_readout

    s3 = row(d, "c1", "S3")
    assert s3.upstream_data["rank"] == 1 and s3.upstream_data["protegenicity"] == 0.93
    assert "adjuvant-only" in s3.falsified_if

    f = row(d, "c1", "functional")
    assert f.status == "testable" and "hSBA" in f.animal_readout and "functional activity is absent" in f.falsified_if


def test_pools_respect_size_limit_and_are_deduplicated():
    eps = [Epitope(peptide=f"PEPTIDE{i:02d}", kind="T_class_I", start=i, hla=["HLA-A*02:01"]) for i in range(25)]
    eps.append(Epitope(peptide="PEPTIDE00", kind="T_class_I", start=0, hla=["HLA-A*24:02"]))  # duplicate, extra allele
    c = Candidate(candidate_id="cx", protein_name="X", rank=1, epitopes=eps)
    d = design_study([c], MENB, HLA_CFG)
    pools = row(d, "cx", "S5_T_cell").peptide_pools
    assert [len(p.peptides) for p in pools] == [20, 5]
    first = pools[0].peptides[0]
    assert first.peptide == "PEPTIDE00" and set(first.hla) == {"HLA-A*02:01", "HLA-A*24:02"}


def test_missing_upstream_data_degrades_to_not_testable():
    d = design_study(trio(), MENB, HLA_CFG)
    for stage in ("S5_B_cell", "S6", "S7"):
        r = row(d, "c2", stage)
        assert r.status == "not_testable_no_upstream_prediction", stage
        assert r.upstream_prediction is None and not r.upstream_data and not r.peptide_pools and r.falsified_if is None
    assert row(d, "c2", "S5_T_cell").status == "testable"  # NadA does have T-cell epitopes
    for stage in ("S5_T_cell", "S5_B_cell", "S6", "S7"):
        r = row(d, "c3", stage)
        assert r.status == "not_testable_no_upstream_prediction", stage
        assert r.upstream_prediction is None and not r.peptide_pools
    assert row(d, "c3", "S3").status == "testable"
    assert validate_design(d) == []


def test_a_users_order_is_never_reported_as_an_s3_rank():
    """rank_source = user_selection_order (the S2 stand-in): no S3 ranking exists, so the S3 row must not claim one."""
    chosen = Candidate(candidate_id="u1", protein_name="UserPick", rank=1, rank_source=RANK_SOURCE_USER_SELECTION)
    d = design_study([chosen], MENB)
    r = row(d, "u1", "S3")
    assert r.status == "not_testable_no_upstream_prediction"
    assert r.upstream_prediction is None and not r.upstream_data and r.falsified_if is None and "not a ranking" in r.note
    arm = next(a for a in d.arms if a.candidate_id == "u1")
    assert "user-selected #1" in arm.label and not re.search(r"S3 rank \d", arm.label)
    assert validate_design(d) == []


def test_a_users_order_with_a_protegenicity_score_claims_the_score_not_a_rank():
    c = Candidate(candidate_id="u2", protein_name="Scored", rank=2, rank_source=RANK_SOURCE_USER_SELECTION, protegenicity=0.9)
    r = row(design_study([c], MENB), "u2", "S3")
    assert r.status == "testable" and r.upstream_data == dict(protegenicity=0.9)
    assert "protegenicity 0.9" in r.upstream_prediction and "position" not in r.upstream_prediction and "ranks" not in r.upstream_prediction


def test_without_a_rank_source_the_s3_row_is_what_it_always_was():
    c = Candidate(candidate_id="c9", protein_name="P9", rank=4)  # rank_source None: read as an S3 rank, as before
    d = design_study([c], MENB)
    r = row(d, "c9", "S3")
    assert r.status == "testable" and r.upstream_data == dict(rank=4) and "S3 ranks P9 at position 4" in r.upstream_prediction
    assert "S3 rank 4" in next(a for a in d.arms if a.candidate_id == "c9").label


def test_structure_row_notes_missing_second_validator():
    c = fhbp()
    c.structure_validators = ["esmfold"]
    assert "ideally two" in row(design_study([c], MENB), "c1", "S7").note
    c.structure_validators = []
    assert "not recorded" in row(design_study([c], MENB), "c1", "S7").upstream_prediction


def test_every_candidate_has_every_stage_row():
    d = design_study(trio(), MENB)
    for cid in ("c1", "c2", "c3"):
        stages = sorted(r.upstream_stage for r in d.falsification_map if r.candidate_id == cid)
        assert stages == sorted(["S3", "S5_T_cell", "S5_B_cell", "S6", "S7", "functional"])


def test_human_mhc_rows_not_testable_in_wild_type_mice():
    d = design_study(trio(), MENB)  # wild-type BALB/c, basic immunogenicity
    assert row(d, "c1", "S5_T_cell").status == "not_testable_in_selected_species"
    assert row(d, "c1", "S6").status == "not_testable_in_selected_species"
    assert row(d, "c1", "S5_T_cell").peptide_pools  # real peptides kept so the design can be switched
    assert row(d, "c1", "S3").status == "testable" and row(d, "c1", "S5_B_cell").status == "testable"
    assert validate_design(d) == []
    tg = design_study(trio(), MENB, HLA_CFG)
    assert row(tg, "c1", "S5_T_cell").status == "testable"


# ----------------------------------------------------------------- species selection


def test_wild_type_mouse_for_epitope_validation_is_a_validation_error():
    d = design_study(trio(), MENB, StudyConfig(goal=StudyGoal.HLA_EPITOPE_VALIDATION, species=Species.MOUSE_WILD_TYPE))
    errs = validate_design(d)
    assert any("requires HLA-transgenic mice" in e for e in errs)
    rabbit = design_study(trio(), MENB, StudyConfig(goal=StudyGoal.HLA_EPITOPE_VALIDATION, species=Species.RABBIT))
    assert any("requires HLA-transgenic mice" in e for e in validate_design(rabbit))


def test_epitope_validation_defaults_to_hla_transgenic_and_is_valid():
    d = design_study(trio(), MENB, HLA_CFG)
    assert d.species_selection.species is Species.MOUSE_HLA_TRANSGENIC
    assert d.species_selection.human_mhc_predictions_under_test is True
    assert "HLA-A*02:01" in d.species_selection.hla_alleles_to_match
    assert validate_design(d) == []
    # no line supplied: the tool does not invent one
    bare = design_study(trio(), MENB, StudyConfig(goal=StudyGoal.HLA_EPITOPE_VALIDATION))
    assert bare.species_selection.strain is None and bare.species_selection.strain_status == "needs_human_input"
    assert "needs_human_input" in bare.species_selection.strain_rationale


def test_species_defaults_and_strain_statement():
    assert design_study(trio(), MENB).species_selection.species is Species.MOUSE_WILD_TYPE
    assert design_study(trio(), MENB).species_selection.strain == "BALB/c"
    b6 = design_study(trio(), MENB, StudyConfig(mouse_strain="C57BL/6")).species_selection
    assert "IgG2c" in b6.strain_rationale and b6.strain_status == "addressed"
    assert design_study(trio(), MENB, StudyConfig(goal=StudyGoal.POLYCLONAL_SERA)).species_selection.species is Species.RABBIT


def test_non_human_primates_out_of_scope():
    d = design_study(trio(), MENB, StudyConfig(species=Species.NON_HUMAN_PRIMATE))
    assert any("out of scope" in e for e in validate_design(d))
    assert "OUT OF SCOPE" in design_study(trio(), MENB).species_selection.non_human_primates


def test_th2_adjuvant_with_epitope_validation_warns():
    d = design_study(trio(), MENB, StudyConfig(goal=StudyGoal.HLA_EPITOPE_VALIDATION, adjuvant="alum"))
    assert any("Th2" in w for w in d.warnings)
    assert design_study(trio(), MENB, StudyConfig(route=Route.IN)).warnings  # alum + IN
    assert any("mucosal" in w for w in design_study(trio(), MENB, StudyConfig(route=Route.IN)).warnings)


# -------------------------------------------------------- challenge model, correlate


def test_unknown_challenge_model_is_surfaced_not_invented():
    d = design_study(trio(), PathogenContext(name="X", susceptible_small_animal_model=None))
    cm = d.challenge_model
    assert cm.status == "unknown_check" and cm.challenge_phase_included is False
    assert "CHECK" in cm.statement and "No challenge model is established" in cm.statement
    assert "No challenge model is established" in render_markdown(d)
    none = design_study(trio(), PathogenContext(name="X", susceptible_small_animal_model=False)).challenge_model
    assert none.status == "none_established" and "No susceptible small-animal challenge model is established" in none.statement
    avail = design_study(trio(), PathogenContext(name="X", susceptible_small_animal_model=True))
    assert avail.challenge_model.status == "reported_available_verify"
    assert avail.challenge_model.challenge_phase_included is False  # still design-only
    assert validate_design(avail) == []


def test_no_correlate_statement_present():
    d = design_study(trio(), NO_CORRELATE)
    assert d.correlate.correlate is None
    assert "No correlate of protection is known or agreed" in d.correlate.statement
    assert "NOT evidence of protection" in d.correlate.statement
    assert "No correlate of protection is known or agreed" in render_markdown(d)
    f = row(d, "c1", "functional")
    assert f.status == "not_testable_no_upstream_prediction" and f.upstream_prediction is None
    assert validate_design(d) == []


def test_correlate_recorded_when_supplied_but_titre_is_not_the_correlate():
    d = design_study(trio(), MENB)
    assert d.correlate.correlate == "hSBA" and d.correlate.igg_titre_is_evidence_of_protection is False
    assert "not that correlate" in d.correlate.statement


# ---------------------------------------------------- design fields and the checklist


def test_structured_design_fields_present():
    d = design_study(trio(), MENB)
    assert d.randomisation.method and d.randomisation.seed_recording
    assert any(b.blinded_to_arm_allocation for b in d.blinding) and any(not b.blinded_to_arm_allocation for b in d.blinding)
    assert d.primary_endpoint.pre_specified and d.primary_endpoint.locked_before_data_collection
    assert d.primary_endpoint.timepoint_day == 56
    assert d.humane_endpoints.criteria and all(w.max_interval_hours > 0 for w in d.humane_endpoints.monitoring)
    assert d.analysis_plan.locked_before_data_collection and d.analysis_plan.n_hypotheses_in_family == 3
    assert d.analysis_plan.below_lloq_rule and d.analysis_plan.sensitivity_analyses


def test_checklist_is_structured_with_arrive_and_3rs():
    d = design_study(trio(), MENB)
    arrive = [i for i in d.ethics_checklist if i.framework == "ARRIVE 2.0 Essential 10"]
    assert [i.item for i in arrive] == list(ARRIVE_ESSENTIAL_10) and len(arrive) == 10
    assert ARRIVE_ESSENTIAL_10 == (
        "Study design", "Sample size", "Inclusion and exclusion criteria", "Randomisation", "Blinding",
        "Outcome measures", "Statistical methods", "Experimental animals", "Experimental procedures", "Results",
    )
    r3 = {i.item: i for i in d.ethics_checklist if i.framework == "3Rs"}
    for k in ("Replacement", "Reduction", "Refinement"):
        assert r3[k].status == "addressed"
    assert "power_analysis" in r3["Reduction"].where and str(d.power_analysis.n_per_group_analysed) in r3["Reduction"].note
    for it in d.ethics_checklist:
        assert it.status in ("addressed", "needs_human_input") and it.where.strip() and it.item.strip()


def test_needs_human_input_items_present_and_never_invented():
    d = design_study(trio(), MENB)
    by = {i.item: i for i in d.ethics_checklist}
    must = [k for k in by if k.startswith("Project licence")] + [k for k in by if k.startswith("Personal licence")]
    must += [k for k in by if k.startswith("AWERB")] + [k for k in by if k.startswith("IACUC protocol")]
    assert len(must) == 4
    assert all(by[k].status == "needs_human_input" for k in must)
    assert any(i.framework == "UK ASPA 1986" for i in d.ethics_checklist) and any(i.framework == "US IACUC" for i in d.ethics_checklist)
    todo = outstanding_human_inputs(d)
    assert todo and any("Project licence" in t for t in todo)
    # no digits that look like invented licence numbers
    assert not re.search(r"\bP[0-9A-Z]{7,}\b|PPL ?[0-9]{3,}|PIL ?[0-9]{3,}", to_json(d))
    # the validator rejects a tool that tries to mark a licence item as addressed
    bad = next(i for i in d.ethics_checklist if i.item.startswith("Project licence"))
    bad.status = "addressed"
    assert any("cannot be 'addressed'" in e for e in validate_design(d))


def test_honest_limits_cited_as_readme_sourced():
    d = design_study(trio(), MENB)
    text = " ".join(d.honest_limits.statements)
    assert "39.6%" in text and "6.8%" in text and "65.4%" in text
    assert "Mouse immunogenicity does not predict human protection" in text
    assert "immunogenic in one species" in text
    assert "README" in d.honest_limits.source
    md = render_markdown(d)
    assert "Honest limits" in md and "README" in md


# ------------------------------------------------------------------ scope limit


def test_no_pathogen_procedure_keywords_in_output():
    designs = [
        design_study(trio(), MENB),
        design_study(trio(), PathogenContext(name="X", susceptible_small_animal_model=True), HLA_CFG),
        design_study(trio(), NO_CORRELATE, StudyConfig(licensed_vaccine_exists=True)),
    ]
    for d in designs:
        text = (render_markdown(d) + to_json(d)).lower()
        for term in FORBIDDEN_PROCEDURE_TERMS:
            assert term not in text, term
        for term in ("inoculum preparation", "culture the pathogen"):
            assert term not in text
        assert scan_forbidden_procedure_text(text) == []
        assert "out of scope" in text  # the limit itself is stated
    assert scan_forbidden_procedure_text("Step 1: culture the pathogen to OD600 0.5") != []


def test_no_predicted_or_expected_titre_fields_or_text():
    d = design_study(trio(), MENB, HLA_CFG)
    assert result_like_field_names(json.loads(to_json(d))) == []
    assert result_like_field_names({"a": {"predicted_titre": 1}}) == ["a.predicted_titre"]
    assert result_like_field_names({"expected_elisa_od": 1.0}) == ["expected_elisa_od"]
    tokens = set()

    def walk(o):
        if isinstance(o, dict):
            for k, v in o.items():
                tokens.add(k)
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(json.loads(to_json(d)))
    for k in tokens:
        parts = re.split(r"[^a-z0-9]+", k.lower())
        assert not (any(p.startswith(("predict", "expect", "simulat")) for p in parts)
                    and any(p in ("titre", "titer", "elisa", "elispot", "od", "spot", "value") for p in parts)), k
    md = render_markdown(d).lower()
    assert not re.search(r"(predicted|expected|simulated) (\w+ )?(titre|titer|elisa|elispot)", md)


def test_design_contains_no_scalar_assay_results_only_assumptions():
    pa = design_study(trio(), MENB).power_analysis
    # the only titre-related numbers are explicitly assumed effect/SD
    assert pa.effect_size_status.startswith("ASSUMPTION")
    assert pa.assumed_log10_difference == StudyConfig().assumed_effect_log10


# ----------------------------------------------------------- validation, I/O, purity


def test_default_designs_are_valid():
    for cfg in (StudyConfig(), HLA_CFG, StudyConfig(goal=StudyGoal.POLYCLONAL_SERA),
                StudyConfig(route=Route.SC, mouse_strain="C57BL/6", adjuvant="MPLA")):
        d = design_study(trio(), MENB, cfg)
        assert validate_design(d) == [], cfg


def test_tampering_is_caught():
    d = design_study(trio(), MENB)
    d.power_analysis.n_per_group_analysed -= 2
    assert any("does not match the recomputed" in e for e in validate_design(d))

    d = design_study(trio(), MENB)
    d.arms = [a for a in d.arms if a.role is not ArmRole.ADJUVANT_ONLY]
    assert any("adjuvant-only arm is missing" in e for e in validate_design(d))

    d = design_study(trio(), MENB)
    d.arms = [a for a in d.arms if a.role is not ArmRole.NAIVE_PBS]
    assert any("naive/PBS arm is missing" in e for e in validate_design(d))

    d = design_study(trio(), MENB)
    d.power_analysis.alpha_per_comparison = d.power_analysis.alpha_familywise
    assert any("multiplicity" in e for e in validate_design(d))

    d = design_study(trio(), MENB)
    d.power_analysis.effect_size_status = "Established by the literature"
    assert any("ASSUMPTION" in e for e in validate_design(d))

    d = design_study(trio(), MENB)
    row(d, "c3", "S6").upstream_prediction = "fabricated coverage"
    assert any("fabricated" in e for e in validate_design(d))

    d = design_study(trio(), MENB)
    d.falsification_map = [r for r in d.falsification_map if r.candidate_id != "c2"]  # validator must not crash
    assert any("candidate c2" in e for e in validate_design(d))

    d = design_study(trio(), MENB)
    d.scope_statement += " Step 1: prepare the inoculum."
    d.randomisation.method += " Then inoculate the animals."
    assert any("scope limit" in e for e in validate_design(d))


def test_json_round_trip():
    d = design_study(trio(), MENB, HLA_CFG)
    data = json.loads(to_json(d))
    assert data["schema_version"] == "s10.v1" and data["pathogen"] == MENB.name
    assert data["power_analysis"]["effect_size_status"] == EFFECT_ASSUMPTION_LABEL
    assert data["species_selection"]["species"] == "mouse_hla_transgenic"
    back = from_json(to_json(d))
    assert back == d
    assert validate_design(back) == []


def test_markdown_renders_all_sections():
    md = render_markdown(design_study(trio(), MENB, HLA_CFG))
    assert md.startswith("# Animal immunogenicity study proposal")
    for heading in ("## 1. Assumptions", "## 2. Species and model", "## 4. Study arms", "## 6. Sample size",
                    "## 7. Endpoints", "## 8. Randomisation and blinding", "## 9. Humane endpoints",
                    "## 10. Statistical analysis plan", "## 11. Falsification map", "## 12. Ethics and regulatory checklist",
                    "## 13. Honest limits"):
        assert heading in md, heading
    assert "DESIGN ONLY" in md and "needs_human_input" in md and "not_testable_no_upstream_prediction" in md
    for pep in FHBP_T1 + FHBP_T2:  # the actual predicted peptides appear in the proposal
        assert pep in md


def test_design_study_is_pure_and_deterministic():
    def snap(c):  # pydantic v2 / v1 compatible
        return c.model_dump() if hasattr(c, "model_dump") else c.dict()

    cands = trio()
    before = [snap(c) for c in cands]
    a = design_study(cands, MENB, HLA_CFG)
    b = design_study(list(reversed(cands)), MENB, HLA_CFG)  # input order does not matter: sorted by rank
    assert a == b
    assert [snap(c) for c in cands] == before
    assert a.candidates_included == ["c1", "c2", "c3"]


def test_impossible_inputs_raise():
    with pytest.raises(ValueError):
        design_study([], MENB)
    with pytest.raises(ValueError):
        design_study([fhbp(), fhbp()], MENB)
