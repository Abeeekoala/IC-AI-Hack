"""S10 - Animal immunogenicity study PROPOSAL (stream C).

This module produces a study DESIGN, never results (README section 0 and S10).

What it emits, as structured fields rather than prose:
  * arms (candidate + adjuvant, adjuvant only, naive/PBS, licensed-vaccine control
    only where one exists), species/model selection, route, adjuvant, doses, schedule;
  * a COMPUTED group size (noncentral-t power analysis, multiplicity-corrected);
  * a falsification map that pairs every upstream prediction with the animal readout
    that can refute it, built from the candidate's real S3/S5/S6/S7 data and degrading
    to `not_testable_no_upstream_prediction` when that data is absent;
  * randomisation, blinding, ONE primary endpoint, humane endpoints, a statistical
    analysis plan;
  * a populated ethics/regulatory checklist (ARRIVE 2.0 Essential 10, 3Rs, UK ASPA 1986,
    US IACUC) in which anything the tool cannot know is `needs_human_input`;
  * the README's honest limits, cited as README-sourced.

What it never does (README section 0, S10 "Scope limit on challenge studies"):
  * no predicted titres, ELISA/ELISpot values, simulated data or any other result;
  * no pathogen handling, culture or challenge-inoculum procedures - those belong with
    the containment facility and its biosafety committee;
  * it does not invent a challenge model or a correlate of protection when the
    pathogen context says there is none / does not know.

The effect size that drives the sample size is NOT known from data. It is an explicit
config field whose default is labelled "ASSUMPTION - replace with pilot data".

Pure functions over upstream artifacts: `design_study` does no I/O. Written to run under both
pydantic v2 and v1 (the S10 serialisation helpers use `model_dump` when available and fall back
to `.json()`).

Stage entrypoint (artifact contract): `run(store, config)` reads the candidates and the pathogen through
`streamc_adapter.load_inputs_from_store` (S0, S2 and stream B's S7 when S3-S7 exist; S0 + S2 for the stand-in)
and writes `s10_animal.json` + the sidecar `data/s10/animal_study.md`:
    payload  design (the StudyDesign as a dict), study_file (file reference), outstanding_human_inputs (count), ...
    status   `ok`, for a normal result AND for "nothing to work on" (then `payload.status == "not_run"`, `payload.reasons` say what is missing
             and how to fix it, and design / study_file / outstanding_human_inputs are None); `failed` only for a real defect
             (`validate_design` found violations: one warning each). `needs_user_input` and `blocked` are never written.
Config (all optional, `streamc.<key>`): proteins, top_k (3), study ({} = StudyConfig defaults; any StudyConfig field).
"""

from __future__ import annotations

import json
import math
import re
import shutil
from collections import Counter
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from vaxforge.artifacts import Artifact, RunStore
from vaxforge.cache import atomic_write_text
from vaxforge.config import ConfigError
from vaxforge.streamc_adapter import (
    DEFAULT_TOP_K,
    NoCandidatesError,
    int_setting,
    load_inputs_from_store,
    payload_keys_applied,
    requested_proteins,
    setting,
    write_no_candidates,
)
from vaxforge.streamc_models import RANK_SOURCE_S7_FINAL, RANK_SOURCE_USER_SELECTION, Candidate, Epitope, PathogenContext  # noqa: F401

SCHEMA_VERSION = "s10.v1"
STAGE = "s10"
PAYLOAD_KEYS = ("design", "study_file", "outstanding_human_inputs", "candidates_included")  # A's check, applied while this stage writes

# Stated verbatim wherever the effect size / SD / dose levels are not data-derived.
EFFECT_ASSUMPTION_LABEL = "ASSUMPTION - replace with pilot data"

# Below this a variance estimate and a non-parametric sensitivity analysis are not
# meaningful, so the computed n is floored here and the floor is reported.
MIN_N_PER_GROUP = 3

README_SOURCE = "README.md, section S10 'Honest limits' (README-sourced; not independently verified by this module)"

SCOPE_STATEMENT = (
    "This document is a study DESIGN. It specifies arms, group sizes, schedule, endpoints, "
    "statistics and ethics documentation. It contains no results, no simulated data and no "
    "assay values. It does not contain pathogen handling, culture or challenge-inoculum "
    "procedures: those belong with the containment facility and its biosafety committee and "
    "are deliberately out of scope for this tool."
)

# Phrases that would indicate the proposal has drifted into procedure territory.
# Used by validate_design and by the scope-limit test.
FORBIDDEN_PROCEDURE_TERMS = (
    "inoculum preparation",
    "prepare the inoculum",
    "prepare inoculum",
    "culture the pathogen",
    "grow the pathogen",
    "pathogen culture",
    "inoculate",
    "od600",
    "heat-kill",
)

# Token sets used to detect a field that looks like a predicted/expected assay value.
_RESULT_VERB_TOKENS = ("predict", "expect", "simulat", "forecast", "projected")
_RESULT_NOUN_TOKENS = {"titre", "titer", "elisa", "elispot", "od", "spot", "signal", "reading", "value", "readings"}

ARRIVE_ESSENTIAL_10 = (
    "Study design",
    "Sample size",
    "Inclusion and exclusion criteria",
    "Randomisation",
    "Blinding",
    "Outcome measures",
    "Statistical methods",
    "Experimental animals",
    "Experimental procedures",
    "Results",
)

FW_ARRIVE = "ARRIVE 2.0 Essential 10"
FW_3RS = "3Rs"
FW_ASPA = "UK ASPA 1986"
FW_IACUC = "US IACUC"
FW_BIOSAFETY = "Biosafety / containment"
FW_STUDY = "Study-specific checks"
# Frameworks whose items depend on licence numbers, facilities or named people - the tool
# cannot know these, so they may never be marked `addressed`.
_HUMAN_ONLY_FRAMEWORKS = (FW_ASPA, FW_IACUC, FW_BIOSAFETY)


# --------------------------------------------------------------------------- enums


class StudyGoal(str, Enum):
    BASIC_IMMUNOGENICITY = "basic_immunogenicity"
    HLA_EPITOPE_VALIDATION = "hla_epitope_validation"
    POLYCLONAL_SERA = "polyclonal_sera"


class Species(str, Enum):
    MOUSE_WILD_TYPE = "mouse_wild_type"
    MOUSE_HLA_TRANSGENIC = "mouse_hla_transgenic"
    RABBIT = "rabbit"
    NON_HUMAN_PRIMATE = "non_human_primate"  # out of scope; accepted only so it can be rejected


class Route(str, Enum):
    IM = "IM"
    SC = "SC"
    IN = "IN"


class DoseRanging(str, Enum):
    TOP_RANKED_ONLY = "top_ranked_only"  # all dose levels for the rank-1 candidate only (Reduction)
    ALL_CANDIDATES = "all_candidates"    # all dose levels for every candidate (many more animals)


class ArmRole(str, Enum):
    CANDIDATE = "candidate_antigen_plus_adjuvant"
    ADJUVANT_ONLY = "adjuvant_only"
    NAIVE_PBS = "naive_pbs"
    POSITIVE_CONTROL = "licensed_vaccine_positive_control"


# ------------------------------------------------------------------------- config


class StudyConfig(BaseModel):
    """Every design parameter, explicit and documented. Defaults are design choices or
    labelled assumptions - none of them is a finding."""

    # Species / model ---------------------------------------------------------
    goal: StudyGoal = StudyGoal.BASIC_IMMUNOGENICITY
    # None = choose from the goal: basic -> wild-type mouse, epitope validation ->
    # HLA-transgenic mouse, polyclonal sera -> rabbit. Setting it explicitly allows (and
    # validate_design rejects) mismatches such as wild-type mice for epitope validation.
    species: Species | None = None
    # Wild-type mouse strain. Default BALB/c: Th2-leaning, robust antibody responses,
    # suited to a humoral primary endpoint. C57BL/6 is the Th1-leaning alternative.
    mouse_strain: str | None = None
    # HLA-transgenic line(s). No default on purpose: the lines must express the alleles
    # S5 predicted, and availability is a facility decision.
    hla_transgenic_lines: list[str] = Field(default_factory=list)
    rabbit_breed: str | None = None
    sex: Literal["female", "male", "both"] = "female"
    age_weeks_range: tuple[int, int] | None = None  # None -> (6, 8) for mice, unset otherwise

    # Intervention ------------------------------------------------------------
    route: Route = Route.IM
    adjuvant: str = "alum"
    adjuvant_rationale: str | None = None  # None -> built-in rationale if the adjuvant is known
    # Micrograms of antigen per animal per immunisation. None -> placeholder levels for the
    # species, labelled ASSUMPTION in the output (set from pilot data / literature).
    dose_levels_ug: list[float] | None = None
    dose_ranging: DoseRanging = DoseRanging.TOP_RANKED_ONLY
    # Dose used for the confirmatory candidate-vs-adjuvant-only comparison. None -> highest
    # level, so that a null result cannot be explained by under-dosing.
    primary_dose_ug: float | None = None
    immunisation_days: list[int] = Field(default_factory=lambda: [0, 21, 42])
    bleed_days: list[int] = Field(default_factory=lambda: [0, 20, 41, 56])
    primary_timepoint_day: int | None = None  # None -> last bleed day

    # Statistics --------------------------------------------------------------
    alpha: float = Field(0.05, gt=0, lt=1)  # family-wise, two-sided
    power: float = Field(0.80, gt=0, lt=1)
    # ASSUMPTION - replace with pilot data. Difference in mean log10 titre between a
    # candidate arm and the adjuvant-only arm (0.7 log10 = about a 5-fold titre ratio) ...
    assumed_effect_log10: float = Field(0.7, gt=0)
    # ... and the assumed between-animal SD of log10 titres within an arm.
    assumed_sd_log10: float = Field(0.5, gt=0)
    # Identifier/citation of the pilot dataset that justifies the two numbers above.
    # None = they are assumptions and are labelled as such in the output.
    effect_size_source: str | None = None
    # Number of candidate-vs-adjuvant-only comparisons in the confirmatory family.
    # None = number of candidates passed in.
    n_candidates_compared: int | None = Field(None, ge=1)
    multiplicity_method: Literal["bonferroni", "holm"] = "holm"
    # Allowance for animals lost before the primary timepoint. Default 0: padding without a
    # facility-specific loss rate conflicts with Reduction.
    attrition_fraction: float = Field(0.0, ge=0, lt=1)
    peptides_per_pool_max: int = Field(20, ge=1)

    # Controls and welfare ----------------------------------------------------
    # None = unknown: the output says "check" instead of guessing.
    licensed_vaccine_exists: bool | None = None
    licensed_vaccine_name: str | None = None
    weight_loss_humane_endpoint_pct: float = Field(15.0, gt=0, lt=100)  # design default; AWERB/IACUC/vet to confirm


# ----------------------------------------------------------------- output models


class SpeciesSelection(BaseModel):
    goal: StudyGoal
    species: Species
    strain: str | None = None
    strain_rationale: str
    strain_status: Literal["addressed", "needs_human_input"]
    sex: str
    age_weeks_range: tuple[int, int] | None = None
    rationale: str
    human_mhc_predictions_under_test: bool
    hla_alleles_to_match: list[str] = Field(default_factory=list)
    non_human_primates: str = (
        "Non-human primates are OUT OF SCOPE for this proposal (last resort; ethically and "
        "financially constrained, README S10). No NHP design is provided."
    )


class ChallengeModelStatement(BaseModel):
    curator_input: bool | None = None
    status: Literal["unknown_check", "none_established", "reported_available_verify"]
    statement: str
    challenge_phase_included: bool = False
    scope_note: str


class CorrelateStatement(BaseModel):
    correlate: str | None = None
    statement: str
    igg_titre_is_evidence_of_protection: bool = False


class AdjuvantChoice(BaseModel):
    name: str
    th_bias: str
    rationale: str
    rationale_source: Literal["built_in_table", "user_supplied", "missing"]


class RouteChoice(BaseModel):
    route: Route
    target_compartment: str
    justification: str


class DosingPlan(BaseModel):
    dose_levels_ug: list[float]
    levels_status: str
    primary_dose_ug: float | None = None
    primary_dose_rationale: str
    dose_ranging: DoseRanging
    dose_ranging_note: str


class Schedule(BaseModel):
    immunisation_days: list[int]
    bleed_days: list[int]
    primary_timepoint_day: int
    terminal_day: int
    terminal_procedures: list[str]
    rationale: str


class SensitivityRow(BaseModel):
    label: str
    effect_size_d: float
    n_per_group_analysed: int


class PowerAnalysis(BaseModel):
    test: str
    endpoint_scale: str
    comparator: str
    assumed_log10_difference: float
    assumed_sd_log10: float
    effect_size_d: float
    effect_size_status: str
    alpha_familywise: float
    n_comparisons: int
    multiplicity_method: str
    alpha_per_comparison: float
    target_power: float
    n_per_group_analysed: int
    n_floor_applied: bool
    achieved_power: float
    attrition_fraction: float
    n_per_group_allocated: int
    n_arms: int
    total_animals: int
    sensitivity: list[SensitivityRow] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)


class Arm(BaseModel):
    arm_id: str
    role: ArmRole
    label: str
    candidate_id: str | None = None
    antigen: str | None = None
    dose_ug: float | None = None
    adjuvant: str | None = None
    route: Route
    n_per_group: int
    primary_comparison: bool = False  # member of the confirmatory family
    purpose: str


class PositiveControl(BaseModel):
    status: Literal["included", "check", "not_applicable_none_exists"]
    vaccine_name: str | None = None
    note: str


class EpitopeRef(BaseModel):
    peptide: str
    kind: Literal["T_class_I", "T_class_II", "B_linear", "B_conformational"]
    start: int | None = None
    end: int | None = None
    score: float | None = None  # upstream S5 score, carried through unchanged
    hla: list[str] = Field(default_factory=list)


class PeptidePool(BaseModel):
    pool_id: str
    epitope_class: Literal["T_class_I", "T_class_II", "B_linear", "B_conformational"]
    peptides: list[EpitopeRef]


class FalsificationRow(BaseModel):
    candidate_id: str
    protein_name: str
    upstream_stage: Literal["S3", "S5_T_cell", "S5_B_cell", "S6", "S7", "functional"]
    # The claim being tested, built from the candidate's actual upstream data.
    # None when the upstream field is missing - never fabricated.
    upstream_prediction: str | None = None
    upstream_data: dict[str, Any] = Field(default_factory=dict)
    animal_readout: str
    falsified_if: str | None = None
    status: Literal["testable", "not_testable_no_upstream_prediction", "not_testable_in_selected_species"]
    peptide_pools: list[PeptidePool] = Field(default_factory=list)
    controls: list[str] = Field(default_factory=list)
    note: str = ""


class Randomisation(BaseModel):
    unit_of_randomisation: str
    method: str
    sequence_generation: str
    allocation_concealment: str
    stratification: list[str]
    seed_recording: str
    cage_effects: str
    assay_plate_layout: str


class BlindingRole(BaseModel):
    role: str
    blinded_to_arm_allocation: bool
    how: str
    limitation: str | None = None


class PrimaryEndpoint(BaseModel):
    name: str
    definition: str
    assay: str
    timepoint_day: int
    comparison: str
    summary_measure: str
    pre_specified: bool = True
    locked_before_data_collection: bool = True
    lock_mechanism: str


class HumaneMonitoringWindow(BaseModel):
    window: str
    max_interval_hours: int


class HumaneCriterion(BaseModel):
    criterion: str
    action: str


class HumaneEndpoints(BaseModel):
    anticipated_severity: str
    monitoring: list[HumaneMonitoringWindow]
    weighing_frequency: str
    criteria: list[HumaneCriterion]
    note: str


class StatisticalAnalysisPlan(BaseModel):
    primary_test: str
    analysis_scale: str
    below_lloq_rule: str
    confirmatory_family: list[str]
    n_hypotheses_in_family: int
    familywise_alpha: float
    multiplicity_method: str
    multiplicity_detail: str
    sensitivity_analyses: list[str]
    exploratory_analyses: list[str]
    analysis_population: str
    exclusion_rules: list[str]
    outlier_rule: str
    locked_before_data_collection: bool = True


class ChecklistItem(BaseModel):
    framework: str
    item: str
    status: Literal["addressed", "needs_human_input"]
    where: str
    note: str = ""


class HonestLimits(BaseModel):
    statements: list[str]
    phase1_to_approval_industry_pct: float = 39.6
    phase1_to_approval_non_industry_pct: float = 6.8
    phase2_to_3_pct: float = 65.4
    evidence_ladder: str = (
        "predicted -> immunogenic in one species. A positive result does not move a candidate to 'works'."
    )
    source: str = README_SOURCE


class StudyDesign(BaseModel):
    schema_version: str = SCHEMA_VERSION
    pathogen: str
    pathogen_taxid: int | None = None
    scope_statement: str = SCOPE_STATEMENT
    candidates_included: list[str]  # candidate ids, rank order
    config: StudyConfig
    species_selection: SpeciesSelection
    challenge_model: ChallengeModelStatement
    correlate: CorrelateStatement
    adjuvant: AdjuvantChoice
    route: RouteChoice
    dosing: DosingPlan
    schedule: Schedule
    power_analysis: PowerAnalysis
    arms: list[Arm]
    positive_control: PositiveControl
    falsification_map: list[FalsificationRow]
    randomisation: Randomisation
    blinding: list[BlindingRole]
    primary_endpoint: PrimaryEndpoint
    secondary_endpoints: list[str]
    humane_endpoints: HumaneEndpoints
    analysis_plan: StatisticalAnalysisPlan
    ethics_checklist: list[ChecklistItem]
    honest_limits: HonestLimits
    warnings: list[str] = Field(default_factory=list)


# ------------------------------------------------------------------ power analysis


def power_two_sample_t(n_per_group: int, effect_size_d: float, alpha: float) -> float:
    """Power of the two-sided, equal-n, two-sample t-test for standardised effect `d`.

    Uses the noncentral t distribution: noncentrality = d * sqrt(n / 2), df = 2n - 2.
    With equal n and equal SD the Welch test coincides with this test.
    """
    if n_per_group < 2:
        raise ValueError("n_per_group must be >= 2")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    from scipy import stats  # here, not at the top: scipy.stats takes about 1 s to import and `vaxforge` loads this module at start-up

    df = 2 * n_per_group - 2
    nc = effect_size_d * math.sqrt(n_per_group / 2.0)
    t_crit = float(stats.t.ppf(1.0 - alpha / 2.0, df))
    return float(stats.nct.sf(t_crit, df, nc) + stats.nct.cdf(-t_crit, df, nc))


def required_n_per_group(effect_size_d: float, alpha: float, power: float, max_n: int = 100_000) -> int:
    """Smallest integer n per group with power >= `power` (computed, not looked up)."""
    if effect_size_d <= 0:
        raise ValueError("effect_size_d must be > 0")
    if not 0 < alpha < 1:
        raise ValueError("alpha must be in (0, 1)")
    if not 0 < power < 1:
        raise ValueError("power must be in (0, 1)")
    if power_two_sample_t(2, effect_size_d, alpha) >= power:
        return 2
    lo, hi = 2, 4  # invariant: power(lo) < target
    while power_two_sample_t(hi, effect_size_d, alpha) < power:
        lo, hi = hi, hi * 2
        if hi > max_n:
            raise ValueError("required group size exceeds max_n; effect size too small for the stated alpha/power")
    while hi - lo > 1:
        mid = (lo + hi) // 2
        if power_two_sample_t(mid, effect_size_d, alpha) >= power:
            hi = mid
        else:
            lo = mid
    # Guard against any local non-monotonicity in the numerical tails.
    while hi > 2 and power_two_sample_t(hi - 1, effect_size_d, alpha) >= power:
        hi -= 1
    return hi


def _ceil_div_attrition(n: int, attrition: float) -> int:
    """ceil(n / (1 - attrition)) with a tolerance so 9 / 0.9 gives 10, not 11."""
    return int(math.ceil(n / (1.0 - attrition) - 1e-9))


def _plan_power(cfg: StudyConfig, n_cand: int) -> PowerAnalysis:
    """Sample-size plan. `n_arms` / `total_animals` are filled in by the caller once arms exist."""
    k = cfg.n_candidates_compared if cfg.n_candidates_compared is not None else n_cand
    d = cfg.assumed_effect_log10 / cfg.assumed_sd_log10
    alpha_pc = cfg.alpha / k
    raw_n = required_n_per_group(d, alpha_pc, cfg.power)
    n = max(raw_n, MIN_N_PER_GROUP)
    allocated = _ceil_div_attrition(n, cfg.attrition_fraction)
    status = (
        EFFECT_ASSUMPTION_LABEL
        if cfg.effect_size_source is None
        else f"Derived from pilot data: {cfg.effect_size_source} (verify before ethics submission)"
    )
    sens: list[SensitivityRow] = []
    for label, factor in (("as assumed", 1.0), ("effect 25% smaller", 0.75), ("effect 50% smaller", 0.5)):
        dd = d * factor
        sens.append(
            SensitivityRow(
                label=label,
                effect_size_d=round(dd, 4),
                n_per_group_analysed=max(required_n_per_group(dd, alpha_pc, cfg.power), MIN_N_PER_GROUP),
            )
        )
    method = cfg.multiplicity_method
    notes = [
        "Sample size is PLANNED at the Bonferroni level alpha/k for either method; Holm's step-down "
        "procedure is uniformly at least as powerful, so the plan is conservative for Holm.",
        "The adjuvant-only arm is shared by all k candidate comparisons, so the comparisons are "
        "positively correlated and Bonferroni is conservative; Dunnett's many-to-one test is a "
        "pre-specifiable alternative.",
        f"Group size is floored at {MIN_N_PER_GROUP} per group (variance estimation); floor applied: "
        f"{raw_n < MIN_N_PER_GROUP}.",
        "Titres at or below the assay limit of detection violate the normality assumption; see the "
        "non-parametric sensitivity analysis in the analysis plan.",
        "The result is only as good as the assumed effect size and SD. If the true effect is smaller, "
        "the study is under-powered (see sensitivity table).",
    ]
    return PowerAnalysis(
        test="Two-sided two-sample t-test (Welch in analysis; equal-n/equal-SD for planning), noncentral t",
        endpoint_scale="log10 antigen-specific serum IgG endpoint titre",
        comparator="adjuvant-only arm",
        assumed_log10_difference=cfg.assumed_effect_log10,
        assumed_sd_log10=cfg.assumed_sd_log10,
        effect_size_d=d,
        effect_size_status=status,
        alpha_familywise=cfg.alpha,
        n_comparisons=k,
        multiplicity_method=method,
        alpha_per_comparison=alpha_pc,
        target_power=cfg.power,
        n_per_group_analysed=n,
        n_floor_applied=raw_n < MIN_N_PER_GROUP,
        achieved_power=power_two_sample_t(n, d, alpha_pc),
        attrition_fraction=cfg.attrition_fraction,
        n_per_group_allocated=allocated,
        n_arms=0,
        total_animals=0,
        sensitivity=sens,
        notes=notes,
    )


# --------------------------------------------------------------------- lookup tables

_ADJUVANTS: dict[str, tuple[str, str]] = {
    "alum": (
        "Th2",
        "Aluminium-salt adjuvant: biases the response towards Th2 / IgG1 (README S10). Long clinical "
        "use makes it a defensible default for a first antibody-focused study; it is a weak inducer of "
        "IFN-gamma / Th1 responses, so it is a poor match for T-cell epitope readouts.",
    ),
    "cpg": (
        "Th1",
        "CpG oligodeoxynucleotide, a TLR9 agonist: TLR-agonist systems bias towards Th1 (README S10); "
        "supports IFN-gamma / IgG2a-c, so suited to T-cell epitope readouts. Choose the ODN class "
        "appropriate for the species.",
    ),
    "mpla": (
        "Th1",
        "Monophosphoryl lipid A, a TLR4 agonist: TLR-agonist systems bias towards Th1 (README S10); "
        "supports IFN-gamma / IgG2a-c.",
    ),
    "alum+mpla": (
        "Th1-skewed relative to alum alone",
        "TLR4 agonist (MPLA) combined with aluminium salt: keeps the antibody-supporting depot of alum "
        "while shifting the response towards Th1 relative to alum alone.",
    ),
}
_ADJUVANT_ALIASES = {
    "alum": "alum",
    "aluminium hydroxide": "alum",
    "aluminum hydroxide": "alum",
    "alhydrogel": "alum",
    "cpg": "cpg",
    "cpg odn": "cpg",
    "tlr9 agonist": "cpg",
    "mpla": "mpla",
    "mpl": "mpla",
    "tlr4 agonist": "mpla",
    "as04": "alum+mpla",
    "alum+mpla": "alum+mpla",
    "alum + mpla": "alum+mpla",
}

_ROUTES: dict[Route, tuple[str, str]] = {
    Route.IM: (
        "systemic (serum IgG)",
        "Intramuscular: depot in muscle, the usual route for injectable and alum-adjuvanted vaccines. "
        "Targets the systemic compartment, which is what the primary endpoint (serum IgG) measures. "
        "Mouse muscle volume is small: injection volume per site must respect licence limits.",
    ),
    Route.SC: (
        "systemic (serum IgG)",
        "Subcutaneous: depot in the subcutis; an alternative when intramuscular volume limits bite. "
        "Targets the systemic compartment measured by the primary endpoint (serum IgG).",
    ),
    Route.IN: (
        "mucosal (secretory IgA, respiratory-tract-resident T cells)",
        "Intranasal: targets the respiratory mucosal compartment, so mucosal secondary readouts "
        "(nasal wash / lavage IgA) are added. Needs an adjuvant that works at mucosal surfaces "
        "(alum is generally not considered one) and usually light anaesthesia for dosing (a Refinement "
        "cost). The primary endpoint (serum IgG) does not measure the mucosal compartment.",
    ),
}

_MOUSE_STRAIN_RATIONALE = {
    "balb/c": (
        "BALB/c: Th2-leaning, IgG1-prone background with robust antibody responses - chosen as the "
        "default because the primary endpoint is a serum IgG titre. A poor choice if IFN-gamma / Th1 "
        "readouts are primary. IgG subclass detection must include IgG2a."
    ),
    "c57bl/6": (
        "C57BL/6: Th1-leaning background (stronger IFN-gamma / IgG2c responses) - chosen when cellular "
        "readouts matter. C57BL/6 mice express IgG2c rather than IgG2a, so subclass ELISA detection must "
        "be IgG2c-specific."
    ),
}

_DEFAULT_DOSES_UG: dict[Species, list[float]] = {
    Species.MOUSE_WILD_TYPE: [1.0, 5.0, 20.0],
    Species.MOUSE_HLA_TRANSGENIC: [1.0, 5.0, 20.0],
    Species.RABBIT: [10.0, 50.0, 200.0],
}


def _is_mouse(species: Species) -> bool:
    return species in (Species.MOUSE_WILD_TYPE, Species.MOUSE_HLA_TRANSGENIC)


# ----------------------------------------------------------------------- builders


def _resolve_species(cfg: StudyConfig) -> Species:
    if cfg.species is not None:
        return cfg.species
    return {
        StudyGoal.BASIC_IMMUNOGENICITY: Species.MOUSE_WILD_TYPE,
        StudyGoal.HLA_EPITOPE_VALIDATION: Species.MOUSE_HLA_TRANSGENIC,
        StudyGoal.POLYCLONAL_SERA: Species.RABBIT,
    }[cfg.goal]


def _t_alleles(cands: list[Candidate]) -> list[str]:
    alleles: set[str] = set()
    for c in cands:
        for e in c.epitopes_of("T_class_I", "T_class_II"):
            alleles.update(e.hla)
    return sorted(alleles)


def _select_species(cfg: StudyConfig, species: Species, cands: list[Candidate]) -> SpeciesSelection:
    alleles = _t_alleles(cands)
    age = cfg.age_weeks_range if cfg.age_weeks_range is not None else ((6, 8) if _is_mouse(species) else None)
    strain: str | None = None
    strain_status: Literal["addressed", "needs_human_input"] = "needs_human_input"
    strain_rationale = ""

    if species is Species.MOUSE_WILD_TYPE:
        strain = cfg.mouse_strain or "BALB/c"
        key = strain.lower().replace(" ", "")
        hit = next((v for k, v in _MOUSE_STRAIN_RATIONALE.items() if key.startswith(k)), None)
        if hit is not None:
            strain_rationale, strain_status = hit, "addressed"
        else:
            strain_rationale = (
                f"Strain '{strain}' is not in the built-in table: the investigator must state why it was "
                "chosen (Th bias drives the response; README S10)."
            )
        rationale = (
            "Basic immunogenicity: cheap and fast (README S10). Wild-type mouse MHC (H-2) does NOT present "
            "the epitopes NetMHCpan predicted for human HLA, so the S5 T-cell and S6 population-coverage "
            "predictions are NOT under test in this design; only S3 (antibody titre), S5 B-cell epitopes "
            "and S7 (folded vs denatured) are."
        )
    elif species is Species.MOUSE_HLA_TRANSGENIC:
        lines = [x for x in cfg.hla_transgenic_lines if x.strip()]
        if lines:
            strain = "; ".join(lines)
            strain_rationale = (
                "Transgenic line(s) supplied by the investigator. Each line must express the restricting "
                "allele(s) of the peptides it is used to test (see hla_alleles_to_match) - verify."
            )
        else:
            strain_rationale = (
                "needs_human_input: select HLA-transgenic line(s) that express the alleles S5 predicted "
                f"({', '.join(alleles) if alleles else 'no restricting alleles recorded upstream'}). "
                "Line availability is a facility decision; this tool does not invent one."
            )
        rationale = (
            "HLA-transgenic mice are the ONLY design in which the human MHC predictions (S5/S6) are "
            "actually under test (README S10): wild-type mouse MHC does not present the epitopes predicted "
            "for humans. Without this design S5 and S6 remain unfalsifiable."
        )
    elif species is Species.RABBIT:
        strain = cfg.rabbit_breed or "New Zealand White (conventional default; confirm with the facility)"
        strain_rationale = "Rabbit: large serum volume per animal for polyclonal sera (README S10)."
        strain_status = "addressed" if cfg.rabbit_breed else "needs_human_input"
        rationale = (
            "Large-volume polyclonal sera (README S10). Rabbit MHC does not present human-HLA-restricted "
            "epitopes either, so S5 T-cell and S6 predictions are not under test. If rabbits are used only "
            "to raise sera with no hypothesis test intended, group size must be justified separately "
            "(needs_human_input); the power analysis below is for the candidate-vs-adjuvant-only comparison."
        )
    else:
        strain_rationale = "Not applicable: out of scope."
        rationale = (
            "Non-human primates are out of scope (last resort; README S10). This species selection is "
            "reported so that validate_design can reject it; no NHP design is provided."
        )

    return SpeciesSelection(
        goal=cfg.goal,
        species=species,
        strain=strain,
        strain_rationale=strain_rationale,
        strain_status=strain_status,
        sex=cfg.sex,
        age_weeks_range=age,
        rationale=rationale,
        human_mhc_predictions_under_test=species is Species.MOUSE_HLA_TRANSGENIC,
        hla_alleles_to_match=alleles,
    )


def _challenge_statement(pathogen: PathogenContext) -> ChallengeModelStatement:
    flag = pathogen.susceptible_small_animal_model
    scope = (
        "No challenge phase is designed here. Challenge dose, route and any pathogen handling belong with "
        "the containment facility and its biosafety committee and are out of scope for this tool."
    )
    if flag is None:
        return ChallengeModelStatement(
            curator_input=None,
            status="unknown_check",
            statement=(
                f"Whether a susceptible small-animal challenge model exists for {pathogen.name} is UNKNOWN in "
                "the supplied pathogen context - CHECK the literature. No challenge model is established in "
                "this proposal and none is proposed or invented. The study measures immunogenicity only and "
                "cannot test protection in vivo."
            ),
            scope_note=scope,
        )
    if flag is False:
        return ChallengeModelStatement(
            curator_input=False,
            status="none_established",
            statement=(
                f"No susceptible small-animal challenge model is established for {pathogen.name} (per the "
                "supplied pathogen context). None is invented here. The study measures immunogenicity only "
                "and cannot test protection in vivo."
            ),
            scope_note=scope,
        )
    return ChallengeModelStatement(
        curator_input=True,
        status="reported_available_verify",
        statement=(
            f"A susceptible small-animal model is recorded for {pathogen.name} (curator input; verify species, "
            "strain and route against primary literature). This proposal does not include a challenge phase."
        ),
        scope_note=scope,
    )


def _correlate_statement(pathogen: PathogenContext) -> CorrelateStatement:
    c = pathogen.correlate_of_protection
    if c is None or not c.strip():
        return CorrelateStatement(
            correlate=None,
            statement=(
                f"No correlate of protection is known or agreed for {pathogen.name} in the supplied pathogen "
                "context. Antibody titres from this study are therefore NOT evidence of protection. Most "
                "vaccines lack an agreed correlate (README S10); do not assume one - check, and read Plotkin "
                "(Clin Vaccine Immunol, 2010) before making correlate claims."
            ),
        )
    return CorrelateStatement(
        correlate=c,
        statement=(
            f"Recorded correlate of protection for {pathogen.name}: '{c}' (curator-supplied; verify against "
            "primary literature). The ELISA IgG titre in this study is not that correlate: it is evidence of "
            "immunogenicity only, and the functional assay for the correlate must be validated for the species "
            "used here (needs_human_input). An agreed correlate is unusual (README S10)."
        ),
    )


def _choose_adjuvant(cfg: StudyConfig) -> AdjuvantChoice:
    key = _ADJUVANT_ALIASES.get(cfg.adjuvant.strip().lower())
    if cfg.adjuvant_rationale and cfg.adjuvant_rationale.strip():
        bias = _ADJUVANTS[key][0] if key else "unspecified"
        return AdjuvantChoice(
            name=cfg.adjuvant, th_bias=bias, rationale=cfg.adjuvant_rationale.strip(), rationale_source="user_supplied"
        )
    if key:
        bias, why = _ADJUVANTS[key]
        return AdjuvantChoice(name=cfg.adjuvant, th_bias=bias, rationale=why, rationale_source="built_in_table")
    return AdjuvantChoice(
        name=cfg.adjuvant,
        th_bias="unspecified",
        rationale="MISSING: adjuvant is not in the built-in table and no rationale was supplied (needs_human_input).",
        rationale_source="missing",
    )


def _choose_route(cfg: StudyConfig) -> RouteChoice:
    compartment, why = _ROUTES[cfg.route]
    return RouteChoice(route=cfg.route, target_compartment=compartment, justification=why)


def _build_dosing(cfg: StudyConfig, species: Species) -> DosingPlan:
    if cfg.dose_levels_ug is not None:
        levels = sorted(cfg.dose_levels_ug)
        status = "supplied by the investigator"
    else:
        levels = list(_DEFAULT_DOSES_UG.get(species, _DEFAULT_DOSES_UG[Species.RABBIT]))
        status = "ASSUMPTION - placeholder levels for the species; set from pilot data or literature"
    primary = cfg.primary_dose_ug if cfg.primary_dose_ug is not None else (max(levels) if levels else None)
    if cfg.primary_dose_ug is None:
        why = (
            "Highest dose level, so that a null result cannot be explained simply by under-dosing - the "
            "falsification claim ('no titre rise over adjuvant-only') is only informative at a dose given a "
            "fair chance. Reactogenicity at this level must be checked against the humane endpoints."
        )
    else:
        why = "Supplied by the investigator."
    if cfg.dose_ranging is DoseRanging.TOP_RANKED_ONLY:
        note = (
            "All dose levels are run for the rank-1 candidate only; other candidates are run at the primary "
            "dose (Reduction). Dose-response is an exploratory, not confirmatory, comparison."
        )
    else:
        note = (
            "All dose levels are run for every candidate: the animal count rises accordingly. Dose-response "
            "is an exploratory, not confirmatory, comparison."
        )
    return DosingPlan(
        dose_levels_ug=levels,
        levels_status=status,
        primary_dose_ug=primary,
        primary_dose_rationale=why,
        dose_ranging=cfg.dose_ranging,
        dose_ranging_note=note,
    )


def _build_schedule(cfg: StudyConfig, t_cell_testable: bool) -> Schedule:
    imm = list(cfg.immunisation_days)
    bleeds = list(cfg.bleed_days)
    last_bleed = bleeds[-1] if bleeds else 0
    primary_day = cfg.primary_timepoint_day if cfg.primary_timepoint_day is not None else last_bleed
    terminal = [f"Terminal exsanguination and serum collection at day {last_bleed}."]
    if t_cell_testable:
        terminal.append(
            f"Spleen collection at day {last_bleed} for IFN-gamma ELISpot (a T-cell row in the falsification "
            "map is testable)."
        )
    gap = (primary_day - imm[-1]) if imm else 0
    return Schedule(
        immunisation_days=imm,
        bleed_days=bleeds,
        primary_timepoint_day=primary_day,
        terminal_day=last_bleed,
        terminal_procedures=terminal,
        rationale=(
            f"Prime-boost: immunise on days {imm}; bleed on days {bleeds}. Bleeds on or before an "
            f"immunisation day give baseline / pre-boost samples; the primary timepoint is day {primary_day}, "
            f"{gap} days after the final immunisation. Total bleed volume per animal must stay within the "
            "limits of the project licence / institutional guidance (needs_human_input)."
        ),
    )


def _build_arms(
    cands: list[Candidate],
    cfg: StudyConfig,
    adjuvant: AdjuvantChoice,
    dosing: DosingPlan,
    positive: PositiveControl,
    n_alloc: int,
) -> list[Arm]:
    arms: list[Arm] = []
    primary = dosing.primary_dose_ug
    levels = dosing.dose_levels_ug
    for i, c in enumerate(cands):
        if cfg.dose_ranging is DoseRanging.ALL_CANDIDATES or i == 0:
            doses = sorted(set(levels) | ({primary} if primary is not None else set()))
        else:
            doses = [primary] if primary is not None else []
        for dose in doses:
            is_primary = dose == primary
            arms.append(
                Arm(
                    arm_id=f"A{len(arms) + 1:02d}",
                    role=ArmRole.CANDIDATE,
                    label=f"{c.protein_name} ({c.candidate_id}, {_rank_text(c)}) {dose:g} ug + {adjuvant.name}",
                    candidate_id=c.candidate_id,
                    antigen=c.protein_name,
                    dose_ug=dose,
                    adjuvant=adjuvant.name,
                    route=cfg.route,
                    n_per_group=n_alloc,
                    primary_comparison=is_primary,
                    purpose=(
                        "Confirmatory arm: candidate vs adjuvant-only on the primary endpoint."
                        if is_primary
                        else "Exploratory dose-response arm (not in the confirmatory family)."
                    ),
                )
            )
    arms.append(
        Arm(
            arm_id=f"A{len(arms) + 1:02d}",
            role=ArmRole.ADJUVANT_ONLY,
            label=f"Adjuvant only ({adjuvant.name}) + antigen vehicle/buffer",
            adjuvant=adjuvant.name,
            route=cfg.route,
            n_per_group=n_alloc,
            purpose=(
                "Comparator for the primary endpoint: the adjuvant alone can raise non-specific responses. Same "
                "adjuvant dose, buffer composition, route and schedule as the candidate arms. One shared arm "
                "serves every candidate comparison (Reduction)."
            ),
        )
    )
    arms.append(
        Arm(
            arm_id=f"A{len(arms) + 1:02d}",
            role=ArmRole.NAIVE_PBS,
            label="Naive / PBS (no antigen, no adjuvant)",
            route=cfg.route,
            n_per_group=n_alloc,
            purpose="Baseline for assay background and handling effects; same route, schedule and bleeds.",
        )
    )
    if positive.status == "included":
        arms.append(
            Arm(
                arm_id=f"A{len(arms) + 1:02d}",
                role=ArmRole.POSITIVE_CONTROL,
                label=f"Licensed vaccine positive control: {positive.vaccine_name or 'product to be named'}",
                antigen=positive.vaccine_name,
                dose_ug=None,
                adjuvant="as formulated in the licensed product",
                route=cfg.route,
                n_per_group=n_alloc,
                purpose=(
                    "Assay and model sensitivity control: shows the model can mount a response in the readout the "
                    "candidates are judged by. Dose and volume for mice: needs_human_input. Not in the "
                    "confirmatory family."
                ),
            )
        )
    return arms


def _positive_control(cfg: StudyConfig) -> PositiveControl:
    if cfg.licensed_vaccine_exists is True:
        return PositiveControl(
            status="included",
            vaccine_name=cfg.licensed_vaccine_name,
            note="Licensed vaccine positive control arm included (existence supplied by the investigator).",
        )
    if cfg.licensed_vaccine_exists is False:
        return PositiveControl(
            status="not_applicable_none_exists",
            note=(
                "No licensed vaccine exists for this pathogen (supplied by the investigator): arm omitted. Assay "
                "validity must then be shown another way, e.g. a well-characterised immunogen control chosen by "
                "the investigator (needs_human_input)."
            ),
        )
    return PositiveControl(
        status="check",
        note=(
            "CHECK: whether a licensed vaccine exists for this pathogen was not supplied, so no positive control "
            "arm is added. If one exists, add the licensed-vaccine positive control arm before ethics submission "
            "(README S10 minimum arms)."
        ),
    )


# ---- falsification map


def _merge_epitopes(eps: list[Epitope]) -> list[EpitopeRef]:
    """De-duplicate by (peptide, kind), union the HLA lists, order by position then sequence
    (no assumption about score direction)."""
    merged: dict[tuple[str, str], EpitopeRef] = {}
    for e in eps:
        key = (e.peptide, e.kind)
        if key in merged:
            have = merged[key]
            have.hla = sorted(set(have.hla) | set(e.hla))
        else:
            merged[key] = EpitopeRef(
                peptide=e.peptide, kind=e.kind, start=e.start, end=e.end, score=e.score, hla=sorted(set(e.hla))
            )
    return sorted(merged.values(), key=lambda r: (r.start if r.start is not None else 10**9, r.peptide))


_POOL_TAG = {"T_class_I": "TI", "T_class_II": "TII", "B_linear": "BL", "B_conformational": "BC"}


def _pools(cid: str, refs: list[EpitopeRef], kind: str, per_pool: int) -> list[PeptidePool]:
    sel = [r for r in refs if r.kind == kind]
    return [
        PeptidePool(pool_id=f"{cid}_{_POOL_TAG[kind]}_{i // per_pool + 1}", epitope_class=kind, peptides=sel[i : i + per_pool])  # type: ignore[arg-type]
        for i in range(0, len(sel), per_pool)
    ]


def _fmt(x: float) -> str:
    return f"{x:g}"


def _rank_text(c: Candidate) -> str:
    """How to cite a candidate's rank without claiming an S3 ranking that does not exist."""
    if c.rank_source == RANK_SOURCE_USER_SELECTION:
        return f"user-selected #{c.rank}, no S3 rank"
    if c.rank_source == RANK_SOURCE_S7_FINAL:
        return f"S7 final rank {c.rank}"  # S3-informed, but S7's order is not the S3 rank
    return f"S3 rank {c.rank}"


def _unit_note(c: Candidate, *fields: str) -> str:
    """One sentence saying what the values are, for the fields that carry a unit/basis (empty when the source stated none)."""
    parts = [f"{f}: {c.units[f]}" for f in fields if c.units.get(f)]
    return ("Units - " + "; ".join(parts) + ".") if parts else ""


def _with_units(c: Candidate, data: dict[str, Any], *fields: str) -> None:
    """Add the stated units of `fields` to a row's upstream_data (nothing is added when the source stated none: legacy rows are unchanged)."""
    given = {f: c.units[f] for f in fields if c.units.get(f)}
    if given:
        data["units"] = given


def _s3_row(c: Candidate) -> FalsificationRow:
    """S3 -> IgG ELISA titre. An S3 rank is mandatory upstream, so the row is testable - unless the candidate has no protegenicity and its
    position is not an S3 rank either: a person chose it (rank_source = user_selection_order) or it comes from S7's final order
    (rank_source = s7_final_order; the S3 rank is a different number and an S3 that ran its rule-based fallback has no protegenicity).
    Then there is no S3 prediction to refute."""
    readout = "Antigen-specific serum IgG ELISA titre (endpoint titre, log10) at the primary timepoint."
    chosen = c.rank_source == RANK_SOURCE_USER_SELECTION
    final = c.rank_source == RANK_SOURCE_S7_FINAL
    if (chosen or final) and c.protegenicity is None:
        note = (
            "No S3 prediction: a person chose this candidate from the S2 table, its position is the order given "
            "(not a ranking) and there is no protegenicity score. The study can still show whether it is immunogenic."
            if chosen else
            "No S3 prediction: S3 gave this candidate no protegenicity score (its rule-based fallback ran, or no score was supplied). S7's final "
            "order also weighs T-cell, B-cell, coverage and structure terms and is not an antigenicity prediction, so there is nothing for an "
            "IgG titre to refute here. The study can still show whether the candidate is immunogenic."
        )
        return FalsificationRow(
            candidate_id=c.candidate_id,
            protein_name=c.protein_name,
            upstream_stage="S3",
            animal_readout=readout,
            status="not_testable_no_upstream_prediction",
            note=note,
        )
    data: dict[str, Any] = {} if chosen else ({"s7_final_rank": c.rank} if final else {"rank": c.rank})  # an order that is not S3's is never reported as an S3 rank
    if chosen:
        pred = f"S3 scores {c.protein_name} (protegenicity {_fmt(c.protegenicity)})"
    elif final:
        pred = (f"S3 scores {c.protein_name} (protegenicity {_fmt(c.protegenicity)}); S7's final order, which also weighs T-cell, B-cell, "
                f"coverage and structure terms, puts it at position {c.rank}")
    else:
        pred = f"S3 ranks {c.protein_name} at position {c.rank}"
    if c.protegenicity is not None:
        data["protegenicity"] = c.protegenicity
        _with_units(c, data, "protegenicity")
        if not chosen and not final:
            pred += f" (protegenicity {_fmt(c.protegenicity)})"
    if c.localisation is not None:
        data["localisation"] = c.localisation
    return FalsificationRow(
        candidate_id=c.candidate_id,
        protein_name=c.protein_name,
        upstream_stage="S3",
        upstream_prediction=pred + ", i.e. predicts it is immunogenic.",
        upstream_data=data,
        animal_readout=readout,
        falsified_if="No titre rise over the adjuvant-only arm.",
        status="testable",
        controls=["adjuvant-only arm", "naive/PBS arm", "pre-immunisation (day 0) serum from each animal"],
        note=(
            "Localisation is 'unknown' upstream (kept as an explicit value, README S2)."
            if (c.localisation or "").lower() == "unknown"
            else ""
        ),
    )


def _falsification_rows(
    c: Candidate,
    pathogen: PathogenContext,
    sel: SpeciesSelection,
    cfg: StudyConfig,
) -> list[FalsificationRow]:
    rows: list[FalsificationRow] = []
    tg = sel.human_mhc_predictions_under_test
    cid, pname = c.candidate_id, c.protein_name

    rows.append(_s3_row(c))

    # S5 T-cell epitopes -> IFN-gamma ELISpot against the predicted pools.
    t_refs = _merge_epitopes(c.epitopes_of("T_class_I", "T_class_II"))
    t_readout = (
        "IFN-gamma ELISpot on splenocytes stimulated with (a) the predicted class I pool(s), (b) the predicted "
        "class II pool(s), (c) whole recombinant antigen."
    )
    if not t_refs:
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="S5_T_cell",
                animal_readout=t_readout,
                status="not_testable_no_upstream_prediction",
                note="No T-cell epitopes (T_class_I / T_class_II) in the upstream S5 output for this candidate.",
            )
        )
    else:
        n1 = sum(r.kind == "T_class_I" for r in t_refs)
        n2 = sum(r.kind == "T_class_II" for r in t_refs)
        alleles = sorted({a for r in t_refs for a in r.hla})
        no_allele = sum(not r.hla for r in t_refs)
        pools = _pools(cid, t_refs, "T_class_I", cfg.peptides_per_pool_max) + _pools(
            cid, t_refs, "T_class_II", cfg.peptides_per_pool_max
        )
        data: dict[str, Any] = {
            "n_class_I": n1,
            "n_class_II": n2,
            "hla_alleles": alleles,
            "n_peptides_without_hla_annotation": no_allele,
        }
        pred = f"S5 predicts {n1} class I and {n2} class II T-cell epitopes presented by human HLA"
        pred += f" ({', '.join(alleles)})." if alleles else " (restricting alleles not recorded upstream)."
        ctrl = [
            "medium/vehicle only (negative)",
            "mitogen (positive)",
            "matched pool of NON-predicted peptides from the same antigen (specificity control)",
            "splenocytes from adjuvant-only and naive animals",
        ]
        if tg:
            note = (
                "Only peptides restricted by an allele that the chosen transgenic line expresses are informative; "
                "match lines to hla_alleles. Class II prediction accuracy lags class I (README S5)."
            )
            if no_allele:
                note += f" {no_allele} peptide(s) have no restricting allele recorded and cannot be assigned to a line."
            rows.append(
                FalsificationRow(
                    candidate_id=cid,
                    protein_name=pname,
                    upstream_stage="S5_T_cell",
                    upstream_prediction=pred,
                    upstream_data=data,
                    animal_readout=t_readout,
                    falsified_if=(
                        "Response to whole antigen but NOT to the predicted pools - antigen right, epitope "
                        "prediction wrong. (No response even to whole antigen is not informative about epitopes.)"
                    ),
                    status="testable",
                    peptide_pools=pools,
                    controls=ctrl,
                    note=note,
                )
            )
        else:
            rows.append(
                FalsificationRow(
                    candidate_id=cid,
                    protein_name=pname,
                    upstream_stage="S5_T_cell",
                    upstream_prediction=pred,
                    upstream_data=data,
                    animal_readout=t_readout,
                    status="not_testable_in_selected_species",
                    peptide_pools=pools,
                    controls=ctrl,
                    note=(
                        f"Selected species ({sel.species.value}) does not express human HLA, so its MHC does not "
                        "present these peptides: a response, or its absence, would not test the S5 prediction. Only "
                        "HLA-transgenic mice put it under test (README S10). Pools are listed so the design can be "
                        "switched without re-deriving them."
                    ),
                )
            )

    # S5 B-cell epitopes -> competition ELISA / peptide-array mapping.
    b_refs = _merge_epitopes(c.epitopes_of("B_linear", "B_conformational"))
    b_readout = (
        "Competition ELISA (immune sera pre-incubated with the predicted epitope peptide vs an irrelevant "
        "peptide) or peptide-array mapping (overlapping peptides spanning the predicted regions)."
    )
    if not b_refs:
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="S5_B_cell",
                animal_readout=b_readout,
                status="not_testable_no_upstream_prediction",
                note="No B-cell epitopes (B_linear / B_conformational) in the upstream S5 output for this candidate.",
            )
        )
    else:
        nl = sum(r.kind == "B_linear" for r in b_refs)
        nc = sum(r.kind == "B_conformational" for r in b_refs)
        note = ""
        if nc:
            note = (
                f"{nc} conformational epitope(s): linear peptide arrays cannot test these; use antigen variants "
                "(e.g. epitope-region mutants) in the same competition ELISA - assay design is needs_human_input."
            )
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="S5_B_cell",
                upstream_prediction=f"S5 predicts {nl} linear and {nc} conformational B-cell epitopes.",
                upstream_data={"n_linear": nl, "n_conformational": nc},
                animal_readout=b_readout,
                falsified_if="Sera bind the antigen but not the predicted epitopes.",
                status="testable",
                peptide_pools=_pools(cid, b_refs, "B_linear", cfg.peptides_per_pool_max)
                + _pools(cid, b_refs, "B_conformational", cfg.peptides_per_pool_max),
                controls=["irrelevant peptide", "adjuvant-only and naive sera", "whole-antigen binding as reference"],
                note=note,
            )
        )

    # S6 population coverage -> HLA-transgenic panel.
    s6_readout = (
        "IFN-gamma ELISpot to the predicted T-cell pools in a panel of HLA-transgenic lines, one line per "
        "restricting allele predicted to cover the populations of interest."
    )
    has_cov = c.pc90 is not None or bool(c.coverage_by_population)
    if not has_cov:
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="S6",
                animal_readout=s6_readout,
                status="not_testable_no_upstream_prediction",
                note="No S6 population-coverage data (pc90 / coverage_by_population) for this candidate.",
            )
        )
    elif not t_refs:
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="S6",
                animal_readout=s6_readout,
                status="not_testable_no_upstream_prediction",
                note="S6 coverage is present but S5 T-cell epitopes are absent, so there is no peptide to test.",
            )
        )
    else:
        s6_data: dict[str, Any] = {}
        parts = []
        if c.pc90 is not None:
            s6_data["pc90"] = c.pc90
            parts.append(f"PC90 = {_fmt(c.pc90)}")
        if c.coverage_by_population:
            s6_data["coverage_by_population"] = dict(c.coverage_by_population)
            parts.append(
                "per-population coverage: " + ", ".join(f"{k} = {_fmt(v)}" for k, v in c.coverage_by_population.items())
            )
        pred = "S6 projected population coverage (values as supplied by S6): " + "; ".join(parts) + "."
        panel_alleles = sorted({a for r in t_refs for a in r.hla})
        s6_data["hla_alleles"] = panel_alleles
        _with_units(c, s6_data, "pc90", "coverage_by_population")
        unit_note = _unit_note(c, "pc90", "coverage_by_population")
        s6_common = dict(
            candidate_id=cid,
            protein_name=pname,
            upstream_stage="S6",
            upstream_prediction=pred,
            upstream_data=s6_data,
            animal_readout=s6_readout,
        )
        if tg:
            rows.append(
                FalsificationRow(
                    **s6_common,  # type: ignore[arg-type]
                    falsified_if="Response absent in backgrounds predicted to be covered.",
                    status="testable",
                    controls=["transgenic line not expressing the allele", "adjuvant-only and naive animals per line"],
                    note=(
                        "Transgenic lines test single-allele presentation and response, not population coverage as "
                        "such. The line panel is needs_human_input"
                        + ("" if sel.strain else " (none supplied)")
                        + ". Presentable != recognised != protective (README S6)."
                        + (f" {unit_note}" if unit_note else "")
                    ),
                )
            )
        else:
            rows.append(
                FalsificationRow(
                    **s6_common,  # type: ignore[arg-type]
                    status="not_testable_in_selected_species",
                    note=(
                        f"Selected species ({sel.species.value}) does not express human HLA; only a panel of "
                        "HLA-transgenic lines tests the S6 prediction (README S10)."
                        + (f" {unit_note}" if unit_note else "")
                    ),
                )
            )

    # S7 structure -> folded vs denatured binding.
    s7_readout = (
        "ELISA binding of immune sera to the folded (native) vs denatured (heat or chemically denatured) antigen."
    )
    if c.structure_confidence is None:
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="S7",
                animal_readout=s7_readout,
                status="not_testable_no_upstream_prediction",
                note=(
                    "No S7 structure prediction (structure_confidence) for this candidate (S7 is top-k only)."
                    if c.rank_source != RANK_SOURCE_S7_FINAL
                    else "No S7 structure model for this candidate: S7 is top-k only, or its structure step did not run (see the artifact warnings)."
                ),
            )
        )
    else:
        vals = list(c.structure_validators)
        if not vals:
            vnote = "No structure validator recorded upstream - report which refolder was used (README S7)."
        elif len(vals) < 2:
            vnote = f"Only one validator recorded ({vals[0]}); README S7 asks for ideally two."
        else:
            vnote = f"Validators used: {', '.join(vals)}."
        s7_data: dict[str, Any] = {"structure_confidence": c.structure_confidence, "structure_validators": vals}
        _with_units(c, s7_data, "structure_confidence")
        unit_note = _unit_note(c, "structure_confidence")
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="S7",
                upstream_prediction=(
                    f"S7 structure model (confidence {_fmt(c.structure_confidence)}; validators: "
                    f"{', '.join(vals) if vals else 'not recorded'}) supports conformational epitopes."
                ),
                upstream_data=s7_data,
                animal_readout=s7_readout,
                falsified_if="Sera recognise only the denatured form - the conformational epitope was an artifact.",
                status="testable",
                controls=["adjuvant-only and naive sera", "native-vs-denatured status of the coating antigen confirmed"],
                note=vnote + " The immunogen itself must be demonstrably folded (method: needs_human_input)." + (f" {unit_note}" if unit_note else ""),
            )
        )

    # Functional protection -> species-appropriate functional assay.
    corr = pathogen.correlate_of_protection
    f_readout = "Species-appropriate functional assay on immune sera."
    if corr is None or not corr.strip():
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="functional",
                animal_readout=f_readout,
                status="not_testable_no_upstream_prediction",
                note=(
                    "No correlate of protection is known or agreed for this pathogen, so no functional assay can be "
                    "named as the test of protection and titres are not protection. An investigator may add a "
                    "mechanism-based functional assay (e.g. neutralisation or opsonophagocytosis) as an exploratory "
                    "readout; that is not a correlate."
                ),
            )
        )
    else:
        rows.append(
            FalsificationRow(
                candidate_id=cid,
                protein_name=pname,
                upstream_stage="functional",
                upstream_prediction=(
                    f"Implicit claim: antibodies raised by {pname} are functionally protective, as measured by the "
                    f"recorded correlate '{corr}'. No pipeline stage predicts functional activity."
                ),
                upstream_data={"correlate": corr},
                animal_readout=f"{corr} (or the species-appropriate equivalent) on immune sera.",
                falsified_if="Titres rise but functional activity is absent.",
                status="testable",
                controls=["adjuvant-only and naive sera", "positive control serum where one exists"],
                note=(
                    f"Confirm that the '{corr}' assay is validated for sera from the species used here "
                    "(needs_human_input). In-vivo protection is not tested: see challenge_model."
                ),
            )
        )
    return rows


# ---- randomisation, blinding, endpoints, welfare, statistics


def _randomisation() -> Randomisation:
    return Randomisation(
        unit_of_randomisation="individual animal (the experimental unit); cage ID is recorded for every animal",
        method="Stratified randomisation to arms using a computer-generated sequence",
        sequence_generation="Seeded pseudo-random permutation, generated by someone who does not run the experiment",
        allocation_concealment=(
            "The allocation key is held by a named custodian (needs_human_input); other staff see only coded "
            "syringe and cage labels"
        ),
        stratification=["baseline body weight", "cage/litter of origin", "sex (if both sexes are used)"],
        seed_recording="The random seed and generated allocation are recorded in the S11 pre-registration manifest",
        cage_effects=(
            "Animals of one arm may need to be co-housed for welfare; cage is then recorded and used as a "
            "cluster in a sensitivity analysis"
        ),
        assay_plate_layout=(
            "Samples are coded and arms are interleaved and randomised across assay plates so that plate and "
            "batch effects are not confounded with arm"
        ),
    )


def _blinding() -> list[BlindingRole]:
    return [
        BlindingRole(
            role="Allocation custodian / dose preparer",
            blinded_to_arm_allocation=False,
            how="Holds the allocation key and prepares coded syringes; takes no part in outcome assessment",
        ),
        BlindingRole(
            role="Animal technician administering immunisations and taking bleeds",
            blinded_to_arm_allocation=True,
            how="Works from coded syringes and cage cards",
            limitation="Partial: adjuvant formulations can look different from PBS (e.g. turbidity)",
        ),
        BlindingRole(
            role="Welfare / humane-endpoint assessor",
            blinded_to_arm_allocation=True,
            how="Scores from coded cage cards",
            limitation="Welfare decisions are never delayed to preserve blinding: unblind immediately if needed",
        ),
        BlindingRole(
            role="Assay operators (ELISA, ELISpot, functional assays)",
            blinded_to_arm_allocation=True,
            how="Coded sample IDs; arm labels withheld until the raw data are locked",
        ),
        BlindingRole(
            role="Statistician",
            blinded_to_arm_allocation=True,
            how="Analyses coded arm labels; labels revealed only after the analysis plan and dataset are locked",
        ),
    ]


def _primary_endpoint(sched: Schedule, n_cand: int) -> PrimaryEndpoint:
    return PrimaryEndpoint(
        name="Antigen-specific serum IgG endpoint titre (log10) at the primary timepoint",
        definition=(
            "Log10 of the reciprocal endpoint dilution of serum IgG binding the immunising antigen, one value per "
            "animal, from the primary-timepoint bleed"
        ),
        assay="Antigen-specific IgG ELISA with a pre-specified endpoint definition and lower limit of quantification",
        timepoint_day=sched.primary_timepoint_day,
        comparison=(
            f"Each candidate + adjuvant arm at the primary dose vs the adjuvant-only arm ({n_cand} comparison(s))"
        ),
        summary_measure="Difference in mean log10 titre (equivalently the geometric-mean titre ratio) with a 95% CI",
        lock_mechanism=(
            "Frozen, with the analysis plan, in the S11 pre-registration manifest (timestamped) and/or a public "
            "registry chosen by the investigators, before the first animal is dosed"
        ),
    )


def _secondary_endpoints(
    rows: list[FalsificationRow], route: Route, adjuvant: AdjuvantChoice, dosing: DosingPlan
) -> list[str]:
    out = [
        "IgG titre at every scheduled bleed (kinetics) - exploratory.",
        f"IgG subclass profile (IgG1 vs IgG2a/c) at the primary timepoint - exploratory; reports the Th bias "
        f"that the {adjuvant.name} rationale ({adjuvant.th_bias}) suggests.",
        f"Dose-response across levels {', '.join(_fmt(x) for x in dosing.dose_levels_ug)} ug - exploratory.",
    ]
    if route is Route.IN:
        out.append("Mucosal IgA in nasal wash / lavage - exploratory (intranasal route).")
    seen: set[str] = set()
    for r in rows:
        if r.status == "testable" and r.upstream_stage != "S3" and r.upstream_stage not in seen:
            seen.add(r.upstream_stage)
            out.append(f"{r.upstream_stage} readout: {r.animal_readout.rstrip('.')} - exploratory, no multiplicity claim.")
    return out


def _humane_endpoints(cfg: StudyConfig) -> HumaneEndpoints:
    return HumaneEndpoints(
        anticipated_severity="mild (proposal only; AWERB / IACUC to confirm)",
        monitoring=[
            HumaneMonitoringWindow(
                window="Day of each immunisation, after recovery and again at end of the working day",
                max_interval_hours=8,
            ),
            HumaneMonitoringWindow(window="Days 1-3 after each immunisation", max_interval_hours=24),
            HumaneMonitoringWindow(window="All remaining days of the schedule", max_interval_hours=24),
        ],
        weighing_frequency="On every immunisation and bleed day, and at least twice weekly otherwise",
        criteria=[
            HumaneCriterion(
                criterion=f"Body-weight loss >= {_fmt(cfg.weight_loss_humane_endpoint_pct)}% of baseline",
                action="Humane killing (design default threshold; confirm against the licence/protocol severity limit)",
            ),
            HumaneCriterion(
                criterion="Persistent hunched posture, piloerection or markedly reduced activity",
                action="Same-day consultation with the named animal-care / veterinary person; humane killing if not rapidly resolving",
            ),
            HumaneCriterion(criterion="Laboured breathing", action="Humane killing without delay"),
            HumaneCriterion(
                criterion="Injection-site ulceration, abscess or swelling impairing normal movement",
                action="Veterinary consultation; humane killing if normal function is impaired",
            ),
            HumaneCriterion(
                criterion="Unable to reach food or water, or unresponsive to gentle stimulation",
                action="Immediate humane killing",
            ),
            HumaneCriterion(
                criterion="Excessive bleeding or failure to recover after blood sampling",
                action="Stop sampling; veterinary consultation",
            ),
        ],
        note=(
            "Humane killing by a method approved for the project (UK: Schedule 1 / licence-specified; US: per the "
            "IACUC protocol and AVMA guidance). Animals reaching a humane endpoint are reported, not silently "
            "excluded."
        ),
    )


def _analysis_plan(arms: list[Arm], power: PowerAnalysis, cfg: StudyConfig) -> StatisticalAnalysisPlan:
    control = next(a for a in arms if a.role is ArmRole.ADJUVANT_ONLY)
    family = [
        f"{a.arm_id} ({a.label}) vs {control.arm_id} (adjuvant only)" for a in arms if a.primary_comparison
    ]
    method = cfg.multiplicity_method
    if method == "holm":
        detail = (
            f"Holm step-down across the {power.n_comparisons} candidate-vs-adjuvant-only comparison(s), "
            f"family-wise alpha {_fmt(cfg.alpha)} (two-sided); controls the family-wise error rate and is "
            "uniformly at least as powerful as Bonferroni. Sample size was planned at the Bonferroni level "
            f"alpha/k = {power.alpha_per_comparison:.4g}."
        )
    else:
        detail = (
            f"Bonferroni across the {power.n_comparisons} candidate-vs-adjuvant-only comparison(s): each tested at "
            f"alpha/k = {power.alpha_per_comparison:.4g}, family-wise alpha {_fmt(cfg.alpha)} (two-sided)."
        )
    return StatisticalAnalysisPlan(
        primary_test="Welch two-sample t-test on log10 titres, each candidate arm (primary dose) vs adjuvant-only",
        analysis_scale="log10 endpoint titre",
        below_lloq_rule=(
            "Values below the assay lower limit of quantification are assigned half the LLOQ, by one rule "
            "pre-specified for all arms"
        ),
        confirmatory_family=family,
        n_hypotheses_in_family=len(family),
        familywise_alpha=cfg.alpha,
        multiplicity_method=method,
        multiplicity_detail=detail,
        sensitivity_analyses=[
            "Mann-Whitney U on the same comparisons with the same multiplicity correction (censored titres)",
            "Cage as a cluster / random effect",
            "Analysis including animals that reached a humane endpoint, where data exist",
        ],
        exploratory_analyses=[
            "Dose-response, IgG subclass, kinetics and the falsification-map readouts: descriptive, with "
            "estimates and CIs, no confirmatory claims",
            "Positive control vs naive: assay/model sensitivity check, outside the confirmatory family",
        ],
        analysis_population=(
            "All randomised animals with a valid sample at the primary timepoint, analysed in the arm allocated; "
            "numbers analysed per arm are reported"
        ),
        exclusion_rules=[
            "Technical failure of dosing (documented leak or mis-injection), recorded before unblinding",
            "Failed or insufficient sample at the primary timepoint (no imputation)",
            "No exclusion of data points as 'outliers' after unblinding",
        ],
        outlier_rule="No post-hoc outlier removal; any exclusion must be one of the pre-specified rules above",
    )


# ---- ethics / regulatory checklist


def _checklist(
    cfg: StudyConfig,
    sel: SpeciesSelection,
    power: PowerAnalysis,
    positive: PositiveControl,
    challenge: ChallengeModelStatement,
    pathogen: PathogenContext,
) -> list[ChecklistItem]:
    A, H = "addressed", "needs_human_input"
    items: list[ChecklistItem] = []

    def add(fw: str, item: str, status: str, where: str, note: str = "") -> None:
        items.append(ChecklistItem(framework=fw, item=item, status=status, where=where, note=note))  # type: ignore[arg-type]

    add(FW_ARRIVE, "Study design", A, "arms; randomisation.unit_of_randomisation; primary_endpoint",
        "Groups, controls, experimental unit (individual animal) and the single primary endpoint are specified.")
    add(FW_ARRIVE, "Sample size", A, "power_analysis",
        f"n={power.n_per_group_allocated} per group computed (noncentral t); the effect size is: {power.effect_size_status}.")
    add(FW_ARRIVE, "Inclusion and exclusion criteria", A, "analysis_plan.exclusion_rules; species_selection (sex, age)",
        "Exclusions are pre-specified; inclusion by species, strain, sex and age range.")
    add(FW_ARRIVE, "Randomisation", A, "randomisation")
    add(FW_ARRIVE, "Blinding", A, "blinding")
    add(FW_ARRIVE, "Outcome measures", A, "primary_endpoint; secondary_endpoints")
    add(FW_ARRIVE, "Statistical methods", A, "analysis_plan")
    add(FW_ARRIVE, "Experimental animals", H, "species_selection (species/strain/sex/age set); supplier field",
        "Supplier, health status, body weight and housing conditions must be supplied; strain status: "
        f"{sel.strain_status}.")
    add(FW_ARRIVE, "Experimental procedures", H, "schedule; dosing; route",
        "Injection volumes, anaesthesia/analgesia, personnel and location must be supplied.")
    add(FW_ARRIVE, "Results", H, "post-study reporting",
        "Completed after the study; this tool produces no results.")

    add(FW_3RS, "Replacement", A, "species_selection.rationale; honest_limits",
        "The S8 in-vitro cascade cannot measure adaptive immunity, antibody titre, neutralisation, T-cell memory "
        "or protection, so a whole-animal study is the first experiment able to refute the S3/S5 predictions.")
    add(FW_3RS, "Reduction", A, "power_analysis; arms",
        f"Group size is computed, not conventional (n={power.n_per_group_analysed} analysed per group, "
        f"{power.n_comparisons} comparison(s), {power.multiplicity_method} correction); one shared adjuvant-only "
        f"and naive arm serves all candidates; dose-ranging is {cfg.dose_ranging.value}; padding only via the "
        f"stated attrition allowance ({_fmt(power.attrition_fraction)}).")
    add(FW_3RS, "Refinement", A, "humane_endpoints; schedule; blinding",
        "Explicit humane endpoints with monitoring frequency; bleed schedule minimised; welfare decisions never "
        "delayed for blinding.")
    add(FW_3RS, "Documented search for non-animal alternatives and prior data (incl. pilot data for the effect size)",
        H, "applicant", "A literature/database search record is required by the licensing bodies.")
    add(FW_3RS, "Housing, enrichment and anaesthesia/analgesia for bleeds and dosing", H, "facility", "Refinement detail.")

    add(FW_ASPA, "Project licence (PPL): number, holder, authorisation covering these procedures and severity", H,
        "applicant", "Licence numbers are never invented by this tool.")
    add(FW_ASPA, "Personal licence (PIL): named holders for immunisation and blood sampling", H, "applicant")
    add(FW_ASPA, "AWERB review: reference and date", H, "applicant / institution")
    add(FW_ASPA, "Establishment licence (facility)", H, "institution")
    add(FW_ASPA, "Named persons (NACWO, NVS)", H, "institution")
    add(FW_ASPA, "Harm-benefit assessment", H, "applicant",
        "Proposed severity is in humane_endpoints; the benefit side is a human judgement.")

    add(FW_IACUC, "IACUC protocol approval: number and date", H, "applicant / institution")
    add(FW_IACUC, "Attending veterinarian consultation (pain/distress category)", H, "institution")
    add(FW_IACUC, "Personnel training and occupational-health enrolment", H, "institution")

    note = f"Containment note supplied: {pathogen.containment_level_note}" if pathogen.containment_level_note else (
        "No containment note supplied for this pathogen."
    )
    add(FW_BIOSAFETY, "Containment level and biosafety committee review (mandatory for any challenge phase)", H,
        "challenge_model.scope_note; facility", note)

    add(FW_STUDY, "Strain / HLA-transgenic line availability and allele match", H if sel.species is Species.MOUSE_HLA_TRANSGENIC else A,
        "species_selection",
        "Each line must express the restricting alleles it is used to test." if sel.species is Species.MOUSE_HLA_TRANSGENIC
        else "Wild-type/rabbit design: no allele matching needed (S5/S6 are then not under test).")
    add(FW_STUDY, "Licensed-vaccine positive control: existence checked", A if positive.status != "check" else H,
        "positive_control", positive.note)
    add(FW_STUDY, "Challenge model existence verified", A if challenge.status == "none_established" else H,
        "challenge_model", challenge.statement)
    return items


def _honest_limits() -> HonestLimits:
    return HonestLimits(
        statements=[
            "Mouse immunogenicity does not predict human protection. It shows an antigen is immunogenic in a "
            "mouse: necessary, not sufficient. (README S10)",
            "Industry base rate for vaccines: Phase 1 to approval 39.6% (6.8% for non-industry sponsors). The "
            "narrowest transition is Phase 2 to 3 at 65.4% - precisely the step where immunogenicity data exists "
            "and efficacy data does not. No amount of preclinical work closes that gap. (README S10)",
            "A positive animal result moves a candidate from 'predicted' to 'immunogenic in one species'. It does "
            "not move it to 'works'. (README S10)",
            "This proposal reports no results of any kind and does not simulate assay readouts or clinical "
            "trials (README section 0).",
        ]
    )


def _warnings(
    cfg: StudyConfig,
    sel: SpeciesSelection,
    adjuvant: AdjuvantChoice,
    positive: PositiveControl,
    challenge: ChallengeModelStatement,
    n_cand: int,
    n_compared: int,
    t_ever_present: bool,
) -> list[str]:
    w: list[str] = []
    if cfg.goal is StudyGoal.HLA_EPITOPE_VALIDATION and adjuvant.th_bias == "Th2":
        w.append(
            f"Adjuvant '{adjuvant.name}' biases towards Th2, a weak driver of IFN-gamma responses, while the goal "
            "is T-cell epitope validation (IFN-gamma ELISpot): consider a TLR-agonist adjuvant."
        )
    if cfg.route is Route.IN and _ADJUVANT_ALIASES.get(cfg.adjuvant.strip().lower()) in ("alum", "alum+mpla"):
        w.append("Intranasal route with an alum-based adjuvant: alum is generally not an effective mucosal adjuvant.")
    if not sel.human_mhc_predictions_under_test and t_ever_present:
        w.append(
            "Selected species does not express human HLA: S5 T-cell and S6 coverage predictions are marked "
            "not_testable_in_selected_species."
        )
    if positive.status == "check":
        w.append("Licensed-vaccine availability not supplied: CHECK and add the positive control arm if one exists.")
    if challenge.status != "reported_available_verify":
        w.append("No established challenge model: the study can show immunogenicity only, not protection.")
    if cfg.dose_ranging is DoseRanging.ALL_CANDIDATES and n_cand > 1:
        w.append("Dose-ranging for every candidate multiplies the animal count; confirm this is justified (Reduction).")
    if n_compared < n_cand:
        w.append("n_candidates_compared is below the number of candidates: the multiplicity correction is too lenient.")
    if cfg.effect_size_source is None:
        w.append("Effect size and SD are ASSUMPTIONS (no pilot data cited); the sample size is only as good as they are.")
    return w


# ------------------------------------------------------------------- public API


def _prepare_candidates(candidates: list[Candidate]) -> list[Candidate]:
    if not candidates:
        raise ValueError("design_study needs at least one candidate")
    ids = [c.candidate_id for c in candidates]
    if len(set(ids)) != len(ids):
        raise ValueError("duplicate candidate_id in candidates")
    return sorted(candidates, key=lambda c: c.rank)  # stable: ties keep input order


def design_study(
    candidates: list[Candidate],
    pathogen: PathogenContext,
    config: StudyConfig | None = None,
) -> StudyDesign:
    """Build the animal immunogenicity study proposal. Pure: same input -> same design.

    Scientifically questionable configurations (e.g. wild-type mice for epitope validation, a
    two-level dose range) do not raise here: the design is built as asked and
    `validate_design` reports the violation. Only inputs that make a design impossible
    (no candidates, duplicate ids) raise ValueError.
    """
    cfg = config if config is not None else StudyConfig()
    cands = _prepare_candidates(candidates)
    species = _resolve_species(cfg)
    sel = _select_species(cfg, species, cands)
    challenge = _challenge_statement(pathogen)
    correlate = _correlate_statement(pathogen)
    adjuvant = _choose_adjuvant(cfg)
    route = _choose_route(cfg)
    dosing = _build_dosing(cfg, species)
    positive = _positive_control(cfg)

    rows = [r for c in cands for r in _falsification_rows(c, pathogen, sel, cfg)]
    t_testable = any(r.upstream_stage == "S5_T_cell" and r.status == "testable" for r in rows)
    t_present = any(r.upstream_stage == "S5_T_cell" and r.peptide_pools for r in rows) or any(
        r.upstream_stage == "S6" and r.status != "not_testable_no_upstream_prediction" for r in rows
    )
    schedule = _build_schedule(cfg, t_testable)

    # n depends on the number of comparisons (not arms); the arm count then fixes the animal total.
    power = _plan_power(cfg, len(cands))
    arms = _build_arms(cands, cfg, adjuvant, dosing, positive, power.n_per_group_allocated)
    power.n_arms = len(arms)
    power.total_animals = len(arms) * power.n_per_group_allocated

    n_family = sum(a.primary_comparison for a in arms)
    return StudyDesign(
        pathogen=pathogen.name,
        pathogen_taxid=pathogen.taxid,
        candidates_included=[c.candidate_id for c in cands],
        config=cfg,
        species_selection=sel,
        challenge_model=challenge,
        correlate=correlate,
        adjuvant=adjuvant,
        route=route,
        dosing=dosing,
        schedule=schedule,
        power_analysis=power,
        arms=arms,
        positive_control=positive,
        falsification_map=rows,
        randomisation=_randomisation(),
        blinding=_blinding(),
        primary_endpoint=_primary_endpoint(schedule, n_family),
        secondary_endpoints=_secondary_endpoints(rows, cfg.route, adjuvant, dosing),
        humane_endpoints=_humane_endpoints(cfg),
        analysis_plan=_analysis_plan(arms, power, cfg),
        ethics_checklist=_checklist(cfg, sel, power, positive, challenge, pathogen),
        honest_limits=_honest_limits(),
        warnings=_warnings(cfg, sel, adjuvant, positive, challenge, len(cands), power.n_comparisons, t_present),
    )


# ------------------------------------------------------------------- serialisation


def _plain(model: BaseModel) -> dict[str, Any]:
    """JSON-compatible dict under pydantic v2 (`model_dump`) or v1 (`.json()`)."""
    if hasattr(model, "model_dump"):
        return model.model_dump(mode="json")
    return json.loads(model.json())


def to_json(design: StudyDesign) -> str:
    return json.dumps(_plain(design), indent=2)


def from_json(text: str) -> StudyDesign:
    data = json.loads(text)
    if hasattr(StudyDesign, "model_validate"):
        return StudyDesign.model_validate(data)
    return StudyDesign.parse_obj(data)


# --------------------------------------------------------------------- validation


def scan_forbidden_procedure_text(text: str) -> list[str]:
    """Return forbidden procedure phrases found in `text` (case-insensitive)."""
    low = text.lower()
    return [t for t in FORBIDDEN_PROCEDURE_TERMS if t in low]


def result_like_field_names(obj: Any, _path: str = "") -> list[str]:
    """Paths of dict keys that look like a predicted/expected/simulated assay value."""
    found: list[str] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            toks = re.split(r"[^a-z0-9]+", str(k).lower())
            verb = any(t.startswith(_RESULT_VERB_TOKENS) for t in toks)
            noun = any(t in _RESULT_NOUN_TOKENS for t in toks)
            if verb and noun:
                found.append(f"{_path}.{k}".lstrip("."))
            found.extend(result_like_field_names(v, f"{_path}.{k}"))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            found.extend(result_like_field_names(v, f"{_path}[{i}]"))
    return found


_ALL_STAGES = ("S3", "S5_T_cell", "S5_B_cell", "S6", "S7", "functional")


def validate_design(d: StudyDesign) -> list[str]:
    """Return rule violations (empty list = valid)."""
    e: list[str] = []
    cfg = d.config
    sp = d.species_selection

    # Species / model -------------------------------------------------------
    if sp.goal is StudyGoal.HLA_EPITOPE_VALIDATION and sp.species is not Species.MOUSE_HLA_TRANSGENIC:
        e.append(
            f"goal hla_epitope_validation requires HLA-transgenic mice, but species is '{sp.species.value}': "
            "its MHC does not present human-HLA-restricted epitopes, so the S5/S6 predictions would not be under "
            "test (README S10)"
        )
    if sp.species is Species.NON_HUMAN_PRIMATE:
        e.append("non-human primates are out of scope for this proposal (README S10)")
    if sp.species is Species.MOUSE_WILD_TYPE and not (sp.strain or "").strip():
        e.append("wild-type mouse design must state the strain and why")
    if sp.human_mhc_predictions_under_test != (sp.species is Species.MOUSE_HLA_TRANSGENIC):
        e.append("human_mhc_predictions_under_test must be true only for HLA-transgenic mice")
    if d.challenge_model.challenge_phase_included:
        e.append("a challenge phase must not be included: challenge studies are out of scope (design only)")
    cm_expected = {None: "unknown_check", False: "none_established", True: "reported_available_verify"}
    if d.challenge_model.status != cm_expected.get(d.challenge_model.curator_input):
        e.append("challenge_model status does not follow the supplied pathogen context (a model must not be invented)")
    if d.challenge_model.curator_input is not True and "established" not in d.challenge_model.statement.lower():
        e.append("challenge_model statement must say that no challenge model is established / must be checked")
    if d.correlate.correlate is None and "no correlate" not in d.correlate.statement.lower():
        e.append("no correlate of protection is known, but the proposal does not say so")
    if d.correlate.igg_titre_is_evidence_of_protection:
        e.append("IgG titres must not be presented as evidence of protection")

    # Adjuvant, route, dosing ------------------------------------------------
    if not d.adjuvant.name.strip():
        e.append("adjuvant must be named")
    if d.adjuvant.rationale_source == "missing" or not d.adjuvant.rationale.strip():
        e.append("adjuvant has no rationale")
    ds = d.dosing
    if len(set(ds.dose_levels_ug)) < 3:
        e.append("at least 3 distinct dose levels are required for dose-ranging (README S10)")
    if any(x <= 0 for x in ds.dose_levels_ug):
        e.append("dose levels must be positive")
    if ds.primary_dose_ug is None or ds.primary_dose_ug not in ds.dose_levels_ug:
        e.append("primary dose must be one of the dose levels")

    # Schedule ----------------------------------------------------------------
    sc = d.schedule
    if not sc.immunisation_days or sc.immunisation_days != sorted(set(sc.immunisation_days)):
        e.append("immunisation days must be non-empty and strictly increasing")
    if not sc.bleed_days or sc.bleed_days != sorted(set(sc.bleed_days)):
        e.append("bleed days must be non-empty and strictly increasing")
    if sc.immunisation_days and sc.bleed_days:
        if sc.bleed_days[0] > sc.immunisation_days[0]:
            e.append("a baseline bleed on or before the first immunisation is required")
        if sc.bleed_days[-1] <= sc.immunisation_days[-1]:
            e.append("the final bleed must be after the last immunisation")
        if sc.primary_timepoint_day not in sc.bleed_days or sc.primary_timepoint_day <= sc.immunisation_days[-1]:
            e.append("primary timepoint must be a bleed day after the last immunisation")
    if d.primary_endpoint.timepoint_day != sc.primary_timepoint_day:
        e.append("primary endpoint timepoint differs from the schedule's primary timepoint")

    # Arms ---------------------------------------------------------------------
    roles = [a.role for a in d.arms]
    if ArmRole.ADJUVANT_ONLY not in roles:
        e.append("adjuvant-only arm is missing")
    if ArmRole.NAIVE_PBS not in roles:
        e.append("naive/PBS arm is missing")
    if len({a.arm_id for a in d.arms}) != len(d.arms):
        e.append("duplicate arm ids")
    for cid in d.candidates_included:
        mine = [a for a in d.arms if a.candidate_id == cid and a.role is ArmRole.CANDIDATE]
        if not mine:
            e.append(f"candidate {cid} has no arm")
        elif sum(a.primary_comparison for a in mine) != 1:
            e.append(f"candidate {cid} must have exactly one primary-comparison arm")
    has_pos = ArmRole.POSITIVE_CONTROL in roles
    if (d.positive_control.status == "included") != has_pos:
        e.append("positive control arm present/absent inconsistently with positive_control.status")
    if d.positive_control.status == "check" and "check" not in d.positive_control.note.lower():
        e.append("unknown licensed-vaccine status must be surfaced with 'check', not guessed")
    for a in d.arms:
        if a.n_per_group != d.power_analysis.n_per_group_allocated:
            e.append(f"arm {a.arm_id}: n_per_group differs from the power analysis allocation")
        if a.role is ArmRole.CANDIDATE and a.adjuvant != d.adjuvant.name:
            e.append(f"arm {a.arm_id}: adjuvant differs from the design adjuvant")

    # Power analysis -------------------------------------------------------------
    pa = d.power_analysis
    try:
        d_eff = pa.assumed_log10_difference / pa.assumed_sd_log10
        if abs(d_eff - pa.effect_size_d) > 1e-6:
            e.append("effect_size_d is inconsistent with assumed difference / SD")
        alpha_pc = pa.alpha_familywise / pa.n_comparisons
        if abs(alpha_pc - pa.alpha_per_comparison) > 1e-12:
            e.append("alpha_per_comparison is inconsistent with alpha / n_comparisons (multiplicity not applied)")
        n_chk = max(required_n_per_group(d_eff, alpha_pc, pa.target_power), MIN_N_PER_GROUP)
        if n_chk != pa.n_per_group_analysed:
            e.append(f"n_per_group_analysed {pa.n_per_group_analysed} does not match the recomputed {n_chk}")
        if pa.n_per_group_allocated < _ceil_div_attrition(pa.n_per_group_analysed, pa.attrition_fraction):
            e.append("allocated n does not cover the stated attrition allowance")
    except (ZeroDivisionError, ValueError) as exc:
        e.append(f"power analysis cannot be recomputed: {exc}")
    family = sum(a.primary_comparison for a in d.arms)
    if pa.n_comparisons < family:
        e.append("n_comparisons is below the number of candidate-vs-control comparisons (multiplicity too lenient)")
    if pa.total_animals != len(d.arms) * pa.n_per_group_allocated or pa.n_arms != len(d.arms):
        e.append("total animal count is inconsistent with arms x group size")
    if cfg.effect_size_source is None and not pa.effect_size_status.startswith("ASSUMPTION"):
        e.append("effect size must be labelled as an ASSUMPTION unless pilot data are cited")
    if pa.multiplicity_method not in ("bonferroni", "holm"):
        e.append("multiplicity method must be stated (bonferroni or holm)")

    # Primary endpoint, welfare, statistics, randomisation, blinding -----------------
    pe = d.primary_endpoint
    if not (pe.pre_specified and pe.locked_before_data_collection) or not pe.name.strip():
        e.append("a single primary endpoint must be pre-specified and locked before data collection")
    he = d.humane_endpoints
    if not he.criteria:
        e.append("humane endpoints missing")
    if not he.monitoring or any(w.max_interval_hours <= 0 for w in he.monitoring):
        e.append("humane-endpoint monitoring frequency missing or invalid")
    sap = d.analysis_plan
    if not sap.locked_before_data_collection:
        e.append("statistical analysis plan must be pre-specified")
    if sap.n_hypotheses_in_family > pa.n_comparisons:
        e.append("analysis family is larger than the number of comparisons the sample size was planned for")
    if not sap.multiplicity_detail.strip():
        e.append("analysis plan lacks the multiplicity correction")
    if not d.randomisation.method.strip():
        e.append("randomisation missing")
    if not any(b.blinded_to_arm_allocation for b in d.blinding) or not any(
        "assay" in b.role.lower() and b.blinded_to_arm_allocation for b in d.blinding
    ):
        e.append("blinding must include blinded assay operators")

    # Falsification map -------------------------------------------------------------
    for cid in d.candidates_included:
        stages = [r.upstream_stage for r in d.falsification_map if r.candidate_id == cid]
        for st in _ALL_STAGES:
            if stages.count(st) != 1:
                e.append(f"falsification map: candidate {cid} needs exactly one '{st}' row (found {stages.count(st)})")
    for r in d.falsification_map:
        tag = f"falsification row {r.candidate_id}/{r.upstream_stage}"
        if r.status == "testable":
            if not (r.upstream_prediction and r.falsified_if and r.animal_readout):
                e.append(f"{tag}: testable rows need an upstream prediction, readout and falsification condition")
            if r.upstream_stage == "S5_T_cell" and not r.peptide_pools:
                e.append(f"{tag}: testable T-cell row has no peptide pools")
            if r.upstream_stage in ("S5_T_cell", "S6") and not sp.human_mhc_predictions_under_test:
                e.append(f"{tag}: human-MHC prediction marked testable in a species without human HLA")
        elif r.status == "not_testable_no_upstream_prediction":
            if r.upstream_prediction is not None or r.upstream_data or r.peptide_pools or r.falsified_if:
                e.append(f"{tag}: row is not testable but carries fabricated prediction content")
        elif r.status == "not_testable_in_selected_species" and sp.human_mhc_predictions_under_test:
            e.append(f"{tag}: marked not testable in species although HLA-transgenic mice were selected")

    # Ethics checklist ------------------------------------------------------------------
    by_fw: dict[str, list[ChecklistItem]] = {}
    for it in d.ethics_checklist:
        by_fw.setdefault(it.framework, []).append(it)
        if it.status not in ("addressed", "needs_human_input"):
            e.append(f"checklist item '{it.item}' has invalid status '{it.status}'")
        if it.framework in _HUMAN_ONLY_FRAMEWORKS and it.status != "needs_human_input":
            e.append(f"checklist item '{it.item}' ({it.framework}) cannot be 'addressed' by the tool (licence/facility/people)")
        if not it.where.strip():
            e.append(f"checklist item '{it.item}' has no 'where'")
    arrive = [i.item for i in by_fw.get(FW_ARRIVE, [])]
    if arrive != list(ARRIVE_ESSENTIAL_10):
        e.append("ARRIVE 2.0 Essential 10 must list exactly the ten items in order")
    r3 = {i.item.split(" ")[0] for i in by_fw.get(FW_3RS, [])}
    for need in ("Replacement", "Reduction", "Refinement"):
        if need not in r3:
            e.append(f"3Rs: {need} not addressed explicitly")
    aspa = " ".join(i.item for i in by_fw.get(FW_ASPA, []))
    for need in ("Project licence", "Personal licence", "AWERB"):
        if need not in aspa:
            e.append(f"UK ASPA 1986: '{need}' item missing")
    if not by_fw.get(FW_IACUC):
        e.append("US IACUC items missing")

    # Honest limits --------------------------------------------------------------------------
    hl = d.honest_limits
    if (hl.phase1_to_approval_industry_pct, hl.phase1_to_approval_non_industry_pct, hl.phase2_to_3_pct) != (39.6, 6.8, 65.4):
        e.append("honest limits: base rates differ from the README values (39.6 / 6.8 / 65.4)")
    if "README" not in hl.source or len(hl.statements) < 3:
        e.append("honest limits must be present and cited as README-sourced")

    # Scope limit ---------------------------------------------------------------------------------
    hits = scan_forbidden_procedure_text(render_markdown(d) + to_json(d))
    if hits:
        e.append(f"scope limit: proposal contains procedure terms out of scope: {hits}")
    bad = result_like_field_names(_plain(d))
    if bad:
        e.append(f"scope limit: fields named like predicted/expected assay values: {bad}")
    return e


def outstanding_human_inputs(d: StudyDesign) -> list[str]:
    """Checklist items the tool could not fill in (licence numbers, facility, named people...)."""
    return [f"[{i.framework}] {i.item}" for i in d.ethics_checklist if i.status == "needs_human_input"]


# --------------------------------------------------------------------- markdown


def _c(x: Any) -> str:
    return str(x).replace("|", "\\|").replace("\n", " ")


def render_markdown(d: StudyDesign) -> str:
    """Reviewer-readable proposal. A design document: it contains no results."""
    L: list[str] = []
    a = L.append
    pa, sp, sc = d.power_analysis, d.species_selection, d.schedule

    a(f"# Animal immunogenicity study proposal - {d.pathogen}")
    a("")
    a(f"> **DESIGN ONLY.** {d.scope_statement}")
    a("")
    a(f"Schema {d.schema_version}. Candidates (rank order): {', '.join(d.candidates_included)}.")
    a("")

    a("## 1. Assumptions to replace before submission")
    a("")
    a(f"- Effect size: **{pa.effect_size_status}**. Assumed difference {_fmt(pa.assumed_log10_difference)} log10 "
      f"titre, assumed SD {_fmt(pa.assumed_sd_log10)} log10 (Cohen's d = {pa.effect_size_d:.3g}). This is not a finding.")
    a(f"- Dose levels (ug per immunisation): {', '.join(_fmt(x) for x in d.dosing.dose_levels_ug)} - {d.dosing.levels_status}.")
    a(f"- Humane-endpoint weight-loss threshold {_fmt(d.config.weight_loss_humane_endpoint_pct)}% is a design default "
      "to be confirmed by AWERB / IACUC / the named veterinary person.")
    for w in d.warnings:
        a(f"- Warning: {w}")
    a("")

    a("## 2. Species and model")
    a("")
    a(f"- Goal: `{sp.goal.value}`; species: **{sp.species.value}**; strain: {sp.strain or 'NOT SET (needs_human_input)'}.")
    a(f"- Rationale: {sp.rationale}")
    a(f"- Strain: {sp.strain_rationale}")
    a(f"- Sex: {sp.sex}; age (weeks): {sp.age_weeks_range or 'needs_human_input'}.")
    a(f"- Human MHC predictions (S5/S6) under test in this design: **{sp.human_mhc_predictions_under_test}**."
      + (
          f" Restricting alleles upstream{' (transgenic lines must match)' if sp.human_mhc_predictions_under_test else ' (not testable in this species)'}: "
          f"{', '.join(sp.hla_alleles_to_match)}."
          if sp.hla_alleles_to_match
          else ""
      ))
    a(f"- {sp.non_human_primates}")
    a("")
    a("### Challenge model")
    a("")
    a(f"- Status: `{d.challenge_model.status}`. {d.challenge_model.statement}")
    a(f"- {d.challenge_model.scope_note}")
    a("")
    a("### Correlate of protection")
    a("")
    a(f"- {d.correlate.statement}")
    a("")

    a("## 3. Intervention")
    a("")
    a(f"- Adjuvant: **{d.adjuvant.name}** ({d.adjuvant.th_bias}). {d.adjuvant.rationale}")
    a(f"- Route: **{d.route.route.value}** - target compartment: {d.route.target_compartment}. {d.route.justification}")
    a(f"- Primary dose: {d.dosing.primary_dose_ug if d.dosing.primary_dose_ug is None else _fmt(d.dosing.primary_dose_ug)} ug. "
      f"{d.dosing.primary_dose_rationale}")
    a(f"- Dose ranging: {d.dosing.dose_ranging_note}")
    a("")
    a("## 4. Study arms")
    a("")
    a("| Arm | Role | Label | Dose (ug) | n/group | Confirmatory | Purpose |")
    a("|---|---|---|---|---|---|---|")
    for r in d.arms:
        a(f"| {r.arm_id} | {r.role.value} | {_c(r.label)} | {_fmt(r.dose_ug) if r.dose_ug is not None else '-'} | "
          f"{r.n_per_group} | {'yes' if r.primary_comparison else 'no'} | {_c(r.purpose)} |")
    a("")
    a(f"Positive control: `{d.positive_control.status}`. {d.positive_control.note}")
    a("")

    a("## 5. Schedule")
    a("")
    a(f"- Immunise: days {sc.immunisation_days}. Bleed: days {sc.bleed_days}. Primary timepoint: day {sc.primary_timepoint_day}.")
    a(f"- {sc.rationale}")
    for t in sc.terminal_procedures:
        a(f"- {t}")
    a("")

    a("## 6. Sample size (power analysis)")
    a("")
    a(f"- Test: {pa.test}; endpoint: {pa.endpoint_scale}; comparator: {pa.comparator}.")
    a(f"- Effect size d = {pa.effect_size_d:.3g} ({pa.effect_size_status}); family-wise alpha = {_fmt(pa.alpha_familywise)}; "
      f"target power = {_fmt(pa.target_power)}.")
    a(f"- Multiplicity: {pa.multiplicity_method}; {pa.n_comparisons} comparison(s); planning alpha per comparison = "
      f"{pa.alpha_per_comparison:.4g}.")
    a(f"- **n per group (analysed) = {pa.n_per_group_analysed}** (power at that n = {pa.achieved_power:.3f}); attrition "
      f"allowance = {_fmt(pa.attrition_fraction)}; **n per group (allocated) = {pa.n_per_group_allocated}**; "
      f"{pa.n_arms} arms; **total animals = {pa.total_animals}**.")
    a("")
    a("| Sensitivity | Effect size d | n per group |")
    a("|---|---|---|")
    for s in pa.sensitivity:
        a(f"| {s.label} | {s.effect_size_d:.3g} | {s.n_per_group_analysed} |")
    a("")
    for n in pa.notes:
        a(f"- {n}")
    a("")

    a("## 7. Endpoints")
    a("")
    pe = d.primary_endpoint
    a(f"**Primary (single, pre-specified, locked before data collection):** {pe.name}.")
    a("")
    a(f"- Definition: {pe.definition}")
    a(f"- Assay: {pe.assay}; timepoint: day {pe.timepoint_day}.")
    a(f"- Comparison: {pe.comparison}; summary: {pe.summary_measure}.")
    a(f"- Lock: {pe.lock_mechanism}.")
    a("")
    a("**Secondary / exploratory:**")
    a("")
    for s in d.secondary_endpoints:
        a(f"- {s}")
    a("")

    a("## 8. Randomisation and blinding")
    a("")
    rz = d.randomisation
    a(f"- Unit: {rz.unit_of_randomisation}.")
    a(f"- Method: {rz.method}. Sequence: {rz.sequence_generation}.")
    a(f"- Allocation concealment: {rz.allocation_concealment}.")
    a(f"- Stratification: {', '.join(rz.stratification)}.")
    a(f"- Seed: {rz.seed_recording}.")
    a(f"- Cage effects: {rz.cage_effects}.")
    a(f"- Assay plates: {rz.assay_plate_layout}.")
    a("")
    a("| Role | Blinded to arm? | How | Limitation |")
    a("|---|---|---|---|")
    for b in d.blinding:
        a(f"| {_c(b.role)} | {'yes' if b.blinded_to_arm_allocation else 'no'} | {_c(b.how)} | {_c(b.limitation or '-')} |")
    a("")

    a("## 9. Humane endpoints")
    a("")
    he = d.humane_endpoints
    a(f"- Anticipated severity: {he.anticipated_severity}. Weighing: {he.weighing_frequency}.")
    for w in he.monitoring:
        a(f"- Monitoring: {w.window} - at least every {w.max_interval_hours} h.")
    a("")
    a("| Criterion | Action |")
    a("|---|---|")
    for c in he.criteria:
        a(f"| {_c(c.criterion)} | {_c(c.action)} |")
    a("")
    a(he.note)
    a("")

    a("## 10. Statistical analysis plan (pre-specified)")
    a("")
    sap = d.analysis_plan
    a(f"- Primary analysis: {sap.primary_test}; scale: {sap.analysis_scale}.")
    a(f"- Below-LLOQ rule: {sap.below_lloq_rule}.")
    a(f"- Confirmatory family ({sap.n_hypotheses_in_family}):")
    for f in sap.confirmatory_family:
        a(f"  - {f}")
    a(f"- Multiplicity ({sap.multiplicity_method}): {sap.multiplicity_detail}")
    a(f"- Analysis population: {sap.analysis_population}.")
    a("- Exclusion rules: " + "; ".join(sap.exclusion_rules) + ".")
    a(f"- Outliers: {sap.outlier_rule}.")
    a("- Sensitivity analyses: " + "; ".join(sap.sensitivity_analyses) + ".")
    a("- Exploratory: " + "; ".join(sap.exploratory_analyses) + ".")
    a("")

    a("## 11. Falsification map")
    a("")
    a("Each upstream prediction is paired with the readout that can refute it. Rows without an upstream prediction "
      "are `not_testable_no_upstream_prediction`; nothing is fabricated.")
    a("")
    for cid in d.candidates_included:
        rows = [r for r in d.falsification_map if r.candidate_id == cid]
        a(f"### {cid} - {rows[0].protein_name if rows else '(no falsification rows)'}")
        a("")
        a("| Stage | Upstream prediction (from data) | Animal readout | Falsified if | Status |")
        a("|---|---|---|---|---|")
        for r in rows:
            a(f"| {r.upstream_stage} | {_c(r.upstream_prediction or '-')} | {_c(r.animal_readout)} | "
              f"{_c(r.falsified_if or '-')} | `{r.status}` |")
        a("")
        for r in rows:
            if r.note:
                a(f"- {r.upstream_stage}: {r.note}")
            for pool in r.peptide_pools:
                pep = ", ".join(p.peptide for p in pool.peptides)
                a(f"- {r.upstream_stage} pool `{pool.pool_id}` ({pool.epitope_class}, {len(pool.peptides)} peptides): {pep}")
        a("")

    a("## 12. Ethics and regulatory checklist")
    a("")
    a("| Framework | Item | Status | Where |")
    a("|---|---|---|---|")
    for i in d.ethics_checklist:
        a(f"| {_c(i.framework)} | {_c(i.item)} | `{i.status}` | {_c(i.where)} |")
    a("")
    todo = outstanding_human_inputs(d)
    a(f"{len(todo)} item(s) need human input (licence numbers, facility, named people are never invented).")
    a("")

    a("## 13. Honest limits")
    a("")
    hl = d.honest_limits
    for s in hl.statements:
        a(f"- {s}")
    a(f"- Base rates: Phase 1 to approval {_fmt(hl.phase1_to_approval_industry_pct)}% (industry) / "
      f"{_fmt(hl.phase1_to_approval_non_industry_pct)}% (non-industry); Phase 2 to 3 {_fmt(hl.phase2_to_3_pct)}%.")
    a(f"- Evidence ladder: {hl.evidence_ladder}")
    a(f"- Source: {hl.source}")
    a("")
    return "\n".join(L)


# --------------------------------------------------------------------- stage entrypoint


def study_config_from(config) -> StudyConfig:
    """`streamc.study` (a mapping of StudyConfig fields; absent = every default) -> StudyConfig."""
    raw = setting(config, "study") or {}
    if not isinstance(raw, dict):
        raise ConfigError(f"streamc.study must be a mapping of StudyConfig fields, got {raw!r}")
    try:
        return StudyConfig(**raw)
    except (ValidationError, TypeError) as exc:
        raise ConfigError(f"streamc.study is not a valid StudyConfig: {' '.join(str(exc).split())[:300]}") from None


def run(store: RunStore, config) -> Artifact:
    """S10 stage: the animal immunogenicity study DESIGN for the shortlisted candidates. Returns the written artifact."""
    import scipy  # imported here: only the power analysis needs it, and its version is part of what was computed

    top_k = int_setting(config, "top_k", DEFAULT_TOP_K)
    study_cfg = study_config_from(config)
    params = {"proteins": requested_proteins(config) or None, "top_k": top_k, "study_config": _plain(study_cfg)}
    try:
        inputs = load_inputs_from_store(store, config, top_k=top_k)
    except NoCandidatesError as exc:
        empty = {"candidates_included": [], "design": None, "study_file": None, "outstanding_human_inputs": None, "falsification_rows": None}
        return write_no_candidates(store, STAGE, exc, PAYLOAD_KEYS, empty, params=params)

    design = design_study(inputs.candidates, inputs.pathogen, study_cfg)
    errors = validate_design(design)
    data = store.run_dir / "data"
    folder = data / "s10"
    if folder.exists():  # this stage owns data/s10
        if folder.resolve().parent != data.resolve():
            raise ValueError(f"refusing to clear {folder}: it is not directly inside {data}")
        shutil.rmtree(folder)
    md_path = store.data_path("s10", "animal_study.md")
    atomic_write_text(md_path, render_markdown(design))
    rows = design.falsification_map
    n_no_pred = sum(r.status == "not_testable_no_upstream_prediction" for r in rows)
    payload = {
        "pathogen": {"scientific_name": inputs.pathogen.name, "taxid": inputs.pathogen.taxid},
        "selection": inputs.selection,
        "candidates_included": list(design.candidates_included),
        "design": _plain(design),
        "study_file": store.run_ref(md_path),
        "outstanding_human_inputs": len(outstanding_human_inputs(design)),
        "falsification_rows": {"total": len(rows), "by_status": dict(sorted(Counter(r.status for r in rows).items()))},
    }
    warnings = [f"design validation: {e}" for e in errors] + list(inputs.warnings) + list(design.warnings)
    if n_no_pred:
        warnings.append(f"{n_no_pred} of {len(rows)} falsification rows are not_testable_no_upstream_prediction: "
                        "no upstream S3-S7 data exists for them, so no upstream prediction is under test in those rows")
    # `inputs` overrides artifacts.DEFAULT_INPUTS["s10"] (s4): S10 records the stages the adapter really read (s0, s2 and, when stream B's
    # S3-S7 exist, s7: its artifact is self-contained). It reads no S8 or S9 output.
    with payload_keys_applied(STAGE, PAYLOAD_KEYS):
        return store.write_artifact(STAGE, payload, params=params, inputs=inputs.stages, tools={"scipy": scipy.__version__},
                                    status="failed" if errors else "ok", warnings=warnings)


def register_cli(subparsers, common) -> None:
    """Add `animal-study` to the `vaxforge` command (called by cli.build_parser; see docs/ARTIFACT_CONTRACT.md)."""
    from vaxforge import streamc_cli  # imported here: it imports this module, and the flags live beside the logic they feed

    p = subparsers.add_parser("animal-study", parents=[common],
                              help="S10: animal immunogenicity study design for the shortlisted candidates")
    streamc_cli.add_animal_study_args(p)
    p.set_defaults(func=streamc_cli.cmd_animal_study)
