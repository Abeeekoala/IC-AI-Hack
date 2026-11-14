"""S8 - Protocol generation (stream C).

Turns one ranked candidate antigen into a structured wet-lab protocol artifact.
Output is a *design*: no assay readouts are simulated or predicted (README §0).

Every protocol carries:
  * per-step automatability (README §2 S9 matrix) so S9 knows what the robot owns,
  * explicit controls,
  * ONE mandatory falsification condition,
  * the list of things the in-vitro cascade does NOT measure.

The ordering rule that is easy to get wrong - endotoxin removal and the LPS
control must precede the THP-1 assay - is enforced by `validate_protocol`, not
left to convention.

Pure function over the upstream artifacts. `build_protocol` does no I/O; `run(store, config)` is the stage
entrypoint `vaxforge run` calls (artifact contract, docs/ARTIFACT_CONTRACT.md).

Stage `s8` -> `s8_protocol.json`:
    inputs   the stages the adapter really read: s0, s2 and s7 when stream B's S3-S7 exist; s0 and s2 for the S2 stand-in
    payload  pathogen, selection, candidates[] = {candidate_id, protein_name, rank, rank_source, protocol: <Protocol>, ...}
    status   `ok`, for a normal result AND for "nothing to work on" (then `payload.status == "not_run"` and `payload.reasons` say what is
             missing and how to fix it: no S3-S7 artifact and no protein named, a name that does not resolve, S3-S7 unusable);
             `failed` only for a real defect (a protocol broke its own rules). `needs_user_input` and `blocked` are never written.
Config (all optional, `streamc.<key>`): proteins, top_k (3), readouts ([elisa, rt_qpcr]), endotoxin_limit_eu_per_ug (None).
"""

from __future__ import annotations

import hashlib
import json
from enum import Enum

from pydantic import BaseModel, Field

from vaxforge.artifacts import Artifact, RunStore
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
from vaxforge.streamc_models import Candidate  # noqa: F401  (re-exported for S9/S10)

STAGE = "s8"
PAYLOAD_KEYS = ("pathogen", "selection", "candidates")  # A's structural check for status "ok", applied while this stage writes


class Automation(str, Enum):
    FULL = "full"        # README matrix: fully automatable on OT-2
    PARTIAL = "partial"  # liquid-handling portion only
    MANUAL = "manual"    # needs a human or another instrument


class StepKind(str, Enum):
    GENE_SYNTHESIS = "gene_synthesis"
    PLASMID_ASSEMBLY = "plasmid_assembly"
    TRANSFORMATION = "transformation"
    COLONY_PCR = "colony_pcr"
    EXPRESSION_INDUCTION = "expression_induction"
    LYSIS = "lysis"
    NI_NTA_PURIFICATION = "ni_nta_purification"
    ENDOTOXIN_REMOVAL = "endotoxin_removal"
    LAL_ASSAY = "lal_assay"
    THP1_CULTURE = "thp1_culture"
    ANTIGEN_STIMULATION = "antigen_stimulation"
    ELISA = "elisa"
    RT_QPCR = "rt_qpcr"
    FACS = "facs"


# README §2 "Automatability matrix". This is an engineering judgment about OT-2
# capability, not a literature claim - validate against the real deck/modules.
AUTOMATABILITY: dict[StepKind, tuple[Automation, str]] = {
    StepKind.GENE_SYNTHESIS: (Automation.MANUAL, "Outsourced to vendor."),
    StepKind.PLASMID_ASSEMBLY: (Automation.FULL, "Liquid handling + temperature module."),
    StepKind.TRANSFORMATION: (Automation.PARTIAL, "Temperature module; plating is manual."),
    StepKind.COLONY_PCR: (Automation.FULL, "Reaction assembly only."),
    StepKind.EXPRESSION_INDUCTION: (Automation.PARTIAL, "Inducer addition yes; shaking incubation no."),
    StepKind.LYSIS: (Automation.PARTIAL, "Reagent addition yes; sonication/centrifugation no."),
    StepKind.NI_NTA_PURIFICATION: (Automation.PARTIAL, "Magnetic-bead His-tag format yes; gravity column/FPLC no."),
    StepKind.ENDOTOXIN_REMOVAL: (Automation.PARTIAL, "Plate setup yes."),
    StepKind.LAL_ASSAY: (Automation.PARTIAL, "Plate setup yes; reading needs a plate reader."),
    StepKind.THP1_CULTURE: (Automation.MANUAL, "CO2 incubator, sterile handling."),
    StepKind.ANTIGEN_STIMULATION: (Automation.FULL, "Addition to seeded plate."),
    StepKind.FACS: (Automation.PARTIAL, "Staining prep yes; acquisition no."),
    StepKind.ELISA: (Automation.PARTIAL, "Plate setup and washes yes; reading no."),
    StepKind.RT_QPCR: (Automation.PARTIAL, "Reaction setup yes; thermocycling and reading no."),
}

# Stated in every protocol, because THP-1 -> ELISA/RT-qPCR is easy to over-read.
NOT_MEASURED = [
    "adaptive immunity",
    "antibody titre",
    "neutralisation",
    "T-cell memory",
    "protection",
    "population HLA diversity (THP-1 is a single-genotype cell line)",
]


class ProtocolConfig(BaseModel):
    # No default on purpose: choosing the release limit is a decision for the
    # wet-lab lead, not something to invent in code.
    endotoxin_limit_eu_per_ug: float | None = None
    readouts: list[StepKind] = Field(default_factory=lambda: [StepKind.ELISA, StepKind.RT_QPCR])


class Step(BaseModel):
    step_id: str
    kind: StepKind
    title: str
    automation: Automation
    automation_note: str
    reagents: list[str] = Field(default_factory=list)  # names only; volumes are set in S9 against real labware
    controls: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    gate: str | None = None  # a condition that must hold before the next step
    est_duration_days: tuple[float, float] | None = None  # coarse engineering estimate, unverified


class Protocol(BaseModel):
    candidate_id: str
    protein_name: str
    steps: list[Step]
    falsifier: str
    does_not_measure: list[str] = Field(default_factory=lambda: list(NOT_MEASURED))
    cost_gbp_range: tuple[float, float] | None = None  # None until sourced from vendor quotes
    estimate_basis: str = "Durations are coarse engineering estimates, not measured; cost is unsourced."
    config: ProtocolConfig


def _step(
    n: int,
    kind: StepKind,
    title: str,
    *,
    reagents: list[str] | None = None,
    controls: list[str] | None = None,
    depends_on: list[str] | None = None,
    gate: str | None = None,
    days: tuple[float, float] | None = None,
) -> Step:
    automation, note = AUTOMATABILITY[kind]
    return Step(
        step_id=f"s{n:02d}",
        kind=kind,
        title=title,
        automation=automation,
        automation_note=note,
        reagents=reagents or [],
        controls=controls or [],
        depends_on=depends_on or [],
        gate=gate,
        est_duration_days=days,
    )


def build_protocol(candidate: Candidate, config: ProtocolConfig | None = None) -> Protocol:
    """Build the recombinant-antigen -> THP-1 innate-activation protocol for one candidate."""
    config = config or ProtocolConfig()
    K = StepKind
    limit = (
        f"<= {config.endotoxin_limit_eu_per_ug} EU/ug"
        if config.endotoxin_limit_eu_per_ug is not None
        else "the release limit (NOT YET SET - decide before running)"
    )

    steps = [
        _step(1, K.GENE_SYNTHESIS, f"Order codon-optimised gene for {candidate.protein_name}",
              gate="Sequence cleared by S12 biosecurity screening before ordering.", days=(7, 21)),
        _step(2, K.PLASMID_ASSEMBLY, "Clone into His-tagged expression vector",
              reagents=["expression vector", "assembly mix"], controls=["no-insert assembly"],
              depends_on=["s01"], days=(1, 3)),
        _step(3, K.TRANSFORMATION, "Transform expression host",
              controls=["untransformed host on selection plate"], depends_on=["s02"], days=(1, 2)),
        _step(4, K.COLONY_PCR, "Colony PCR to confirm insert",
              controls=["empty-vector colony", "no-template"], depends_on=["s03"], days=(0.5, 1)),
        _step(5, K.EXPRESSION_INDUCTION, "Small-scale expression and induction",
              controls=["uninduced culture"], depends_on=["s04"], days=(1, 2)),
        _step(6, K.LYSIS, "Lyse cells; clarify lysate", depends_on=["s05"], days=(0.5, 1)),
        _step(7, K.NI_NTA_PURIFICATION, "Ni-NTA (His-tag) purification",
              controls=["empty-vector lysate mock purification"], depends_on=["s06"],
              gate="Soluble protein recovered; if not, candidate falsified at expression (see falsifier).",
              days=(1, 2)),
        _step(8, K.ENDOTOXIN_REMOVAL, "Endotoxin removal", depends_on=["s07"], days=(0.5, 1)),
        _step(9, K.LAL_ASSAY, "Quantify residual endotoxin (LAL)",
              controls=["LAL standard curve", "buffer blank"], depends_on=["s08"],
              gate=f"Residual endotoxin {limit} before ANY THP-1 work.", days=(0.5, 1)),
        _step(10, K.THP1_CULTURE, "THP-1 culture and PMA differentiation", depends_on=["s09"], days=(3, 5)),
        _step(11, K.ANTIGEN_STIMULATION, "Stimulate differentiated THP-1 with purified antigen",
              controls=[
                  "vehicle/buffer only",
                  "LPS positive control",
                  "endotoxin-matched LPS control (at the measured residual level)",
                  "polymyxin B-treated antigen",
                  "empty-vector mock-purification eluate",
              ],
              depends_on=["s09", "s10"], days=(1, 2)),
    ]
    n = len(steps)
    for kind in config.readouts:
        n += 1
        steps.append(_step(n, kind, f"{kind.value.replace('_', ' ').upper()} readout",
                           depends_on=["s11"], days=(1, 2)))

    falsifier = (
        "At the in-vitro stage this candidate is falsified if EITHER (a) it cannot be expressed "
        "soluble and purified to the endotoxin limit, OR (b) the cytokine/transcript response "
        "does not exceed the endotoxin-matched LPS control and the polymyxin B-treated arm. "
        "A response explained by residual endotoxin is not evidence for the antigen."
    )
    return Protocol(
        candidate_id=candidate.candidate_id,
        protein_name=candidate.protein_name,
        steps=steps,
        falsifier=falsifier,
        config=config,
    )


def validate_protocol(p: Protocol) -> list[str]:
    """Return rule violations (empty list = valid). Used by S9 before codegen."""
    errors: list[str] = []
    ids = [s.step_id for s in p.steps]
    index = {sid: i for i, sid in enumerate(ids)}
    by_kind = {s.kind: s for s in p.steps}

    if len(set(ids)) != len(ids):
        errors.append("duplicate step ids")
    if not p.falsifier.strip():
        errors.append("missing falsification condition")

    for s in p.steps:
        for d in s.depends_on:
            if d not in index:
                errors.append(f"{s.step_id}: unknown dependency {d}")
            elif index[d] >= index[s.step_id]:
                errors.append(f"{s.step_id}: depends on later step {d}")
        if s.automation != AUTOMATABILITY[s.kind][0]:
            errors.append(f"{s.step_id}: automation differs from the matrix for {s.kind.value}")

    stim = by_kind.get(StepKind.ANTIGEN_STIMULATION)
    if stim is not None:
        for need in (StepKind.ENDOTOXIN_REMOVAL, StepKind.LAL_ASSAY):
            req = by_kind.get(need)
            if req is None or index[req.step_id] >= index[stim.step_id]:
                errors.append(f"{need.value} must precede THP-1 antigen stimulation")
        if not any("LPS" in c for c in stim.controls):
            errors.append("antigen stimulation has no LPS control")
        if not any("endotoxin-matched" in c for c in stim.controls):
            errors.append("antigen stimulation has no endotoxin-matched control")
    return errors


def to_json(p: Protocol) -> str:
    return json.dumps(p.model_dump(mode="json"), indent=2)


def render_markdown(p: Protocol) -> str:
    """Reviewer-readable view of the protocol: owner of each step, controls, gates, falsifier."""
    owner = {Automation.FULL: "ROBOT", Automation.PARTIAL: "ROBOT-PARTIAL", Automation.MANUAL: "HUMAN"}
    out = [f"# Wet-lab protocol - {p.protein_name} ({p.candidate_id})", "",
           "> Design only. No assay results are simulated or predicted. Durations are coarse estimates and cost is unsourced.", "",
           "| Step | What | Owner | Not automated / note |", "|---|---|---|---|"]
    for s in p.steps:
        out.append(f"| {s.step_id} | {s.title} | {owner[s.automation]} | {s.automation_note} |")
    out += ["", "## Controls and gates", ""]
    for s in p.steps:
        if s.controls or s.gate:
            out.append(f"**{s.step_id} {s.title}**")
            out += [f"- control: {c}" for c in s.controls]
            if s.gate:
                out.append(f"- gate: {s.gate}")
            out.append("")
    out += ["## Falsification condition", "", p.falsifier, "", "## What this cascade does NOT measure", ""]
    out += [f"- {x}" for x in p.does_not_measure]
    out += ["", f"_{p.estimate_basis}_", ""]
    return "\n".join(out)


# --------------------------------------------------------------------------- stage entrypoint

READOUTS = {"elisa": StepKind.ELISA, "rt_qpcr": StepKind.RT_QPCR, "facs": StepKind.FACS}


def protocol_config_from(config) -> ProtocolConfig:
    """`streamc.readouts` (names from READOUTS) and `streamc.endotoxin_limit_eu_per_ug` -> ProtocolConfig. Absent keys use the defaults."""
    names = setting(config, "readouts", ["elisa", "rt_qpcr"])
    if isinstance(names, str):
        names = [n.strip() for n in names.split(",") if n.strip()]
    bad = [n for n in names if n not in READOUTS]
    if bad:
        raise ConfigError(f"streamc.readouts: unknown {bad}; choose from {sorted(READOUTS)}")
    limit = setting(config, "endotoxin_limit_eu_per_ug")  # no default on purpose: a decision for the wet-lab lead
    try:
        limit = None if limit is None else float(limit)
    except (TypeError, ValueError):
        raise ConfigError(f"streamc.endotoxin_limit_eu_per_ug must be a number, got {limit!r}") from None
    return ProtocolConfig(readouts=[READOUTS[n] for n in names], endotoxin_limit_eu_per_ug=limit)


def run(store: RunStore, config) -> Artifact:
    """S8 stage: one protocol per shortlisted candidate (`streamc.top_k`, default 3). Returns the written artifact.

    Candidates come from `streamc_adapter.load_inputs_from_store`: stream B's S7 final order when S3-S7 artifacts exist, otherwise
    the proteins the caller NAMED (`streamc.proteins`) out of S2 (the stand-in). Nothing to work on (nothing named, a name that does not
    resolve, S3-S7 unusable) -> an `ok` artifact with `payload.status == "not_run"` and the reasons (first warning too); nothing is picked silently.
    """
    top_k = int_setting(config, "top_k", DEFAULT_TOP_K)
    pcfg = protocol_config_from(config)
    params = {
        "proteins": requested_proteins(config) or None,
        "top_k": top_k,
        "readouts": [k.value for k in pcfg.readouts],
        "endotoxin_limit_eu_per_ug": pcfg.endotoxin_limit_eu_per_ug,
    }
    try:
        inputs = load_inputs_from_store(store, config, top_k=top_k)
    except NoCandidatesError as exc:
        return write_no_candidates(store, STAGE, exc, PAYLOAD_KEYS, {"candidates": []}, params=params)

    entries, problems = [], []
    for cand in inputs.candidates:
        proto = build_protocol(cand, pcfg)
        problems += [f"{cand.candidate_id}: protocol rule broken: {e}" for e in validate_protocol(proto)]
        entries.append({
            "candidate_id": cand.candidate_id,
            "protein_name": cand.protein_name,
            "rank": cand.rank,
            "rank_source": cand.rank_source,
            "length_aa": cand.length_aa,
            "sequence_sha256": hashlib.sha256(cand.sequence.encode("utf-8")).hexdigest() if cand.sequence else None,
            "localisation": cand.localisation,
            "protocol": proto.model_dump(mode="json"),
        })
    payload = {
        "pathogen": {"scientific_name": inputs.pathogen.name, "taxid": inputs.pathogen.taxid},
        "selection": inputs.selection,
        "candidates": entries,
    }
    # `inputs` overrides artifacts.DEFAULT_INPUTS["s8"] (s4, s5, s6, s7): S8 records the stages it really read (s0, s2 and, when stream B's
    # S3-S7 exist, s7: B's S7 artifact is self-contained, so s4-s6 are not read here).
    with payload_keys_applied(STAGE, PAYLOAD_KEYS):
        return store.write_artifact(
            STAGE, payload, params=params, inputs=inputs.stages, tools={},
            status="failed" if problems else "ok", warnings=[*problems, *inputs.warnings])
