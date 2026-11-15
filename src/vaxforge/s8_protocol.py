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
    payload  pathogen, selection, candidates[] = {candidate_id, protein_name, rank, rank_source, protocol: <Protocol>, ...},
             order_fasta (file ref of data/s8/order.fasta, or None), order_fasta_missing[] = {candidate_id, reason},
             cost (CostEstimate over the whole shortlist: quantities x streamc.prices; None when not run), narratives (backend summary)
    status   `ok`, for a normal result AND for "nothing to work on" (then `payload.status == "not_run"` and `payload.reasons` say what is
             missing and how to fix it: no S3-S7 artifact and no protein named, a name that does not resolve, S3-S7 unusable);
             `failed` only for a real defect (a protocol broke its own rules). `needs_user_input` and `blocked` are never written.
Config (all optional, `streamc.<key>`): proteins, top_k (3), readouts ([elisa, rt_qpcr]), endotoxin_limit_eu_per_ug (None),
    robot_params ({}: the S9 placeholders the reagent quantities and the cost quantities come from), prices ({}: see s8_cost),
    s8_backend (template | llm) and s8_model (None): who writes each step's plain-language `narrative` (see s8_narrative).

Order list for synthesis: the stage writes `data/s8/order.fasta` (protein sequences, header = candidate id, protocol order) in the format
`python -m vaxforge.s12_biosafety check ORDER.fasta --run RUN_DIR` reads. The vendor back-translates; a candidate without a sequence is listed
in `payload.order_fasta_missing`, never invented. The file is NOT cleared for synthesis: S12 and a provider screening record come first.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, ValidationError

from vaxforge import s8_cost, s8_narrative
from vaxforge.artifacts import Artifact, RunStore
from vaxforge.config import ConfigError
from vaxforge.fasta import FastaRecord, write_fasta
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
from vaxforge.streamc_models import QUANTITY_SOURCE, CostEstimate, StepQuantity

STAGE = "s8"
PAYLOAD_KEYS = ("pathogen", "selection", "candidates")  # A's structural check for status "ok", applied while this stage writes

# The order list for synthesis, a sidecar of the run (docs/ARTIFACT_CONTRACT.md: data/ holds sidecar files).
ORDER_FASTA_PARTS = ("s8", "order.fasta")  # -> <run>/data/s8/order.fasta
ORDER_FASTA_REL = "data/" + "/".join(ORDER_FASTA_PARTS)
# S12 (stream D) reads exactly this: `check ORDER.fasta --run RUN_DIR [--screening FILE]`, exit 0 cleared | 2 blocked | 3 not screened.
# `vaxforge biosafety check ...` is the same command (`s12_biosafety` is registered in cli.EXTENSION_MODULES since PR #39); the `python -m` form below also works.
S12_COMMAND = f"python -m vaxforge.s12_biosafety check RUN_DIR/{ORDER_FASTA_REL} --run RUN_DIR --screening PROVIDER_RECORD.json"
GENE_SYNTHESIS_GATE = (
    "Sequence cleared by S12 biosecurity screening before ordering. The S8 stage writes the order list (protein FASTA; the vendor "
    f"back-translates it) to RUN_DIR/{ORDER_FASTA_REL}; check it with `{S12_COMMAND}` (PROVIDER_RECORD.json is the screening record "
    "from the synthesis provider). Order only on exit code 0 (cleared); 2 = blocked, 3 = not screened."
)


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
    # Optional extras the stage adds (an older S8 JSON without them still loads; `build_protocol` leaves them empty):
    quantities: list[StepQuantity] = Field(default_factory=list)  # reagent volumes of the S9 PLACEHOLDER plan; empty for a step with no robot session
    narrative: str | None = None  # plain-language summary written from this step's fields only; never a source of truth
    narrative_source: str | None = None  # "template" or "llm:<model id>"


class Protocol(BaseModel):
    candidate_id: str
    protein_name: str
    steps: list[Step]
    falsifier: str
    does_not_measure: list[str] = Field(default_factory=lambda: list(NOT_MEASURED))
    cost_gbp_range: tuple[float, float] | None = None  # None unless the stage priced every item in GBP (then low == high: one price per item)
    estimate_basis: str = "Durations are coarse engineering estimates, not measured; cost is unsourced."
    cost: CostEstimate | None = None  # quantities x user-supplied prices (s8_cost); None when the protocol was only built, not costed
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
              gate=GENE_SYNTHESIS_GATE, days=(7, 21)),
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
    out += _narrative_lines(p)
    out += _quantity_lines(p)
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
    if p.cost is not None:
        out += _cost_lines(p.cost)
    out += ["", f"_{p.estimate_basis}_", ""]
    return "\n".join(out)


def _narrative_lines(p: Protocol) -> list[str]:
    """`## Step narratives`: each step's plain-language paragraph with its source (`template` or `llm:<model>`). Nothing when none was written."""
    steps = [s for s in p.steps if s.narrative]
    if not steps:
        return []
    out = ["", "## Step narratives", "",
           "> A plain-language reading of each step, written from that step's own fields; the table, controls, gates and numbers in this document are the "
           "source of truth. Source `template` = built by rule; `llm:<model>` = written by a language model and checked (it may not add a number, "
           "unit or control that the step lacks).", ""]
    out += [f"- **{s.step_id} {s.title}** (narrative source: {s.narrative_source or 'not stated'}): {s.narrative}" for s in steps]
    return out


def _quantity_lines(p: Protocol) -> list[str]:
    """`## Reagent quantities (PLACEHOLDERS from the S9 plan)`: for each step with a robot session, what the plan has an operator prepare.
    Nothing at all when no step carries quantities (a protocol that was only built)."""
    steps = [s for s in p.steps if s.quantities]
    if not steps:
        return []
    out = ["", "## Reagent quantities (PLACEHOLDERS from the S9 plan)", "",
           f"> Source: {QUANTITY_SOURCE}. These volumes are NOT validated for any kit, enzyme, cell line or assay: each is what the robot draws from "
           "that stock plus dead volume, taken from the coarse defaults in `s9_sessions.RobotParams` (override with `streamc.robot_params`). "
           "Samples that a person prepares (cells, lysates, supernatants) are not listed, and a step with no robot session has no quantities.", ""]
    for s in steps:
        out.append(f"- **{s.step_id} {s.title}**: " + "; ".join(f"{q.name} {q.microlitres:,.10g} uL" for q in s.quantities))
    return out


def _cost_lines(c: CostEstimate) -> list[str]:
    """The `## Cost (placeholder quantities x your prices)` section: the status in words, then every item with its quantity, unit and price."""
    out = ["", "## Cost (placeholder quantities x your prices)", "",
           "> The quantities come from the S9 PLACEHOLDER plan (coarse defaults, not a kit insert or an SOP). The prices are only those in "
           "`streamc.prices`; none is built in.", ""]
    n_priced = sum(i.unit_price is not None for i in c.items)
    if c.status == "estimated" and c.total is not None:
        out.append(f"**Total: {c.total.currency} {c.total.amount:,.2f}** - every item is priced, from your prices (a sum of what you supplied, not a vendor quote).")
    elif c.status == "partial" and c.priced_subtotal is not None:
        out.append(f"**Partly priced.** Priced subtotal {c.priced_subtotal.currency} {c.priced_subtotal.amount:,.2f} over {n_priced} of {len(c.items)} items - "
                   f"NOT a total: {len(c.items) - n_priced} item(s) have no price.")
    else:
        out.append("**Not estimated.** " + ("No price was supplied for any item." if c.items else "No quantity could be derived, so there is nothing to price."))
    if c.items:
        out += ["", "| Item | Quantity | Unit | Unit price | Subtotal |", "|---|---|---|---|---|"]
        for i in c.items:
            price = "-" if i.unit_price is None else f"{i.currency} {i.unit_price:g}"
            sub = "-" if i.subtotal is None else f"{i.currency} {i.subtotal:,.2f}"
            out.append(f"| {i.item.replace('|', '/')} | {i.quantity:,.10g} | {i.unit} | {price} | {sub} |")
    out += ["", c.note + " Amounts are shown to 2 decimals; the JSON holds the exact sums."]
    return out


# --------------------------------------------------------------------------- order list for synthesis (S12 reads it)

MISSING_SEQUENCE_REASON = (
    "no protein sequence is available for this candidate in the upstream artifacts (S2 proteome / S7 record), so it cannot be listed; "
    "none is invented"
)


def order_list(candidates: list[Candidate]) -> tuple[list[FastaRecord], list[dict[str, str]]]:
    """The protein records to order (header = candidate id, in the order given) and the candidates left out, each with its reason.

    The PROTEIN sequence is listed, not DNA: the vendor back-translates and codon-optimises. A candidate with no sequence is never
    given one. An id that cannot be the single identifier of a FASTA header (empty, whitespace, repeated) is left out too, because S12
    reads the first token of the header as the record id and needs it unique.
    """
    records: list[FastaRecord] = []
    missing: list[dict[str, str]] = []
    seen: set[str] = set()
    for c in candidates:
        seq = "".join((c.sequence or "").split())
        cid = c.candidate_id
        if not seq:
            reason = MISSING_SEQUENCE_REASON
        elif not cid or any(ch.isspace() for ch in cid):
            reason = "the candidate id is empty or holds whitespace, so it cannot be the one identifier of a FASTA header (S12 matches on it)"
        elif cid in seen:
            reason = "the candidate id repeats an earlier one; every record of an order list needs a unique id"
        else:
            seen.add(cid)
            records.append(FastaRecord(header=cid, seq=seq))
            continue
        missing.append({"candidate_id": cid, "reason": reason})
    return records, missing


def write_order_fasta(store: RunStore, candidates: list[Candidate]) -> tuple[dict[str, Any] | None, list[dict[str, str]]]:
    """Write `data/s8/order.fasta` (LF line ends, 60 residues per line) -> (its file ref with `records`, or None if nothing can be listed; the left-out list)."""
    records, missing = order_list(candidates)
    if not records:
        return None, missing
    path = store.data_path(*ORDER_FASTA_PARTS)
    write_fasta(records, path)
    return store.run_ref(path, records=len(records)), missing


def _clear_data_dir(store: RunStore) -> None:
    """`data/s8` belongs to this stage and a re-run replaces it, so an order list from an earlier run never outlives its artifact."""
    data = store.run_dir / "data"
    folder = data / ORDER_FASTA_PARTS[0]
    if folder.exists():
        if folder.resolve().parent != data.resolve():
            raise ValueError(f"refusing to clear {folder}: it is not directly inside {data}")
        shutil.rmtree(folder)


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


def robot_params_from(config):
    """`streamc.robot_params` -> `s9_sessions.RobotParams`, validated exactly as the S9 stage validates it ({} keeps the placeholder defaults)."""
    from vaxforge.s9_sessions import RobotParams  # imported here: s9_sessions imports this module

    try:
        return RobotParams(**(setting(config, "robot_params") or {}))
    except (ValidationError, TypeError) as exc:
        raise ConfigError(f"streamc.robot_params is not valid RobotParams: {' '.join(str(exc).split())[:300]}") from None


def plan_protocol(protocol: Protocol, params=None):
    """The S9 session plans of `protocol` (every number is a RobotParams PLACEHOLDER). Raises PlanError (a ValueError) if they cannot be built.

    Imported lazily and kept out of `build_protocol`, which stays pure and fast: the quantities and the cost are computed only where asked for.
    """
    from vaxforge.s9_sessions import RobotParams, plan_sessions  # imported here: s9_sessions imports this module

    return plan_sessions(protocol, params or RobotParams())


def step_quantities(plans) -> dict[str, list[StepQuantity]]:
    """step_id -> the reagents the S9 PLACEHOLDER plan loads for that step, volumes summed over the step's sessions (ELISA has two).

    A volume is what the robot draws from a stock plus that stock's dead volume: what an operator has to prepare. Samples that a person
    prepares (cells, lysates, supernatants) are not listed. The order is the deck order of the plan, so it is deterministic.
    """
    acc: dict[str, dict[str, float]] = {}
    for plan in plans:
        for sid in plan.covers:
            reagents = acc.setdefault(sid, {})
            for liq in plan.liquids:
                if liq.source == "robot_loaded":
                    reagents[liq.reagent] = reagents.get(liq.reagent, 0.0) + liq.volume_ul
    return {sid: [StepQuantity(name=name, microlitres=int(v) if float(v).is_integer() else v) for name, v in reagents.items()]
            for sid, reagents in acc.items()}


def with_quantities(protocol: Protocol, params=None, plans=None) -> Protocol:
    """A copy of `protocol` whose steps that have a robot session carry `quantities` (reagent name, microlitres, source "S9 placeholder plan").

    A step with no robot session (gene synthesis, THP-1 culture) keeps an empty list: nothing is invented for it. `plans` may be passed to
    reuse plans already built; otherwise they are built from `params` (RobotParams, default placeholders), importing s9_sessions lazily.
    """
    by_step = step_quantities(plans if plans is not None else plan_protocol(protocol, params))
    return protocol.model_copy(update={"steps": [s.model_copy(update={"quantities": by_step.get(s.step_id, [])}) for s in protocol.steps]})


def _jsonable(x: Any) -> Any:
    return json.loads(json.dumps(x, default=str, sort_keys=True))


def run(store: RunStore, config, *, client: Any | None = None) -> Artifact:
    """S8 stage: one protocol per shortlisted candidate (`streamc.top_k`, default 3). Returns the written artifact.

    `client` is only for `streamc.s8_backend: llm`: anything with `.messages.create(...)` (the Anthropic SDK shape; tests inject a fake).
    None creates the SDK client lazily; the key is read by the SDK, never here.

    Candidates come from `streamc_adapter.load_inputs_from_store`: stream B's S7 final order when S3-S7 artifacts exist, otherwise
    the proteins the caller NAMED (`streamc.proteins`) out of S2 (the stand-in). Nothing to work on (nothing named, a name that does not
    resolve, S3-S7 unusable) -> an `ok` artifact with `payload.status == "not_run"` and the reasons (first warning too); nothing is picked silently.

    On top of the template protocol the stage adds, per candidate: the order list (`data/s8/order.fasta`), a cost estimate over the S9 placeholder
    quantities (`s8_cost`: priced only from `streamc.prices`). Everything it adds is optional in the JSON and computed here, not in `build_protocol`.
    """
    top_k = int_setting(config, "top_k", DEFAULT_TOP_K)
    pcfg = protocol_config_from(config)
    robot_params = robot_params_from(config)
    prices = s8_cost.prices_from(config)
    backend, model_id = s8_narrative.settings_from(config)
    params = {
        "proteins": requested_proteins(config) or None,
        "top_k": top_k,
        "readouts": [k.value for k in pcfg.readouts],
        "endotoxin_limit_eu_per_ug": pcfg.endotoxin_limit_eu_per_ug,
        # What the quantities, the cost, the animal count and the narratives are computed from: a different value must give a different digest.
        "robot_params": robot_params.model_dump(mode="json"),
        "prices": s8_cost.prices_as_params(prices),
        "study": _jsonable(setting(config, "study") or {}),
        "s8_backend": backend,
        "s8_model": model_id,
    }
    try:
        inputs = load_inputs_from_store(store, config, top_k=top_k)
    except NoCandidatesError as exc:
        _clear_data_dir(store)
        empty = {"candidates": [], "order_fasta": None, "order_fasta_missing": [], "cost": None, "narratives": None}
        return write_no_candidates(store, STAGE, exc, PAYLOAD_KEYS, empty, params=params)

    n = len(inputs.candidates)
    basis = s8_cost.basis_text(n)
    narrator = s8_narrative.NarrativeRun(s8_narrative.NarrativeLLM(model_id, client=client) if backend == "llm" else None)
    entries, problems, quantity_sets, plan_failures = [], [], [], []
    for cand in inputs.candidates:
        proto = build_protocol(cand, pcfg)
        problems += [f"{cand.candidate_id}: protocol rule broken: {e}" for e in validate_protocol(proto)]
        try:
            plans = plan_protocol(proto, robot_params)
        except ValueError as exc:  # PlanError: these RobotParams cannot be realised on the deck; S9 will say the same, S8 must not crash on it
            plans = None
            plan_failures.append(f"{cand.candidate_id}: {' '.join(str(exc).split())[:200]}")
            proto.cost = s8_cost.not_estimated(basis, f"No quantities, so no estimate: the S9 plan cannot be built from these RobotParams ({plan_failures[-1]}).")
        if plans is not None:
            proto = with_quantities(proto, plans=plans)  # a copy: the steps with a robot session now list their placeholder reagent volumes
            quantities = s8_cost.protocol_quantities(plans)
            quantity_sets.append(quantities)
            proto.cost = s8_cost.estimate(quantities, prices, basis=basis)
        proto.cost_gbp_range = s8_cost.gbp_range(proto.cost)
        proto.estimate_basis = s8_cost.estimate_basis_for(proto.cost, proto.estimate_basis)
        proto = s8_narrative.with_narratives(proto, narrator)  # last: a narrative is written from the step as it now stands (quantities included)
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
    _clear_data_dir(store)
    order_ref, order_missing = write_order_fasta(store, inputs.candidates)
    order_warnings: list[str] = []
    if order_ref is None:
        order_warnings.append("S8 order list not written: no shortlisted candidate can be listed (" + (order_missing[0]["reason"] if order_missing else "none shortlisted") + ")")
    elif order_missing:
        order_warnings.append(f"S8 order list: {len(order_missing)} of {len(inputs.candidates)} shortlisted candidate(s) are not in {ORDER_FASTA_REL} "
                              f"({', '.join(m['candidate_id'] for m in order_missing)}): {order_missing[0]['reason']}")
    cost_warnings: list[str] = []
    if plan_failures:
        cost_warnings.append(f"S8 quantities and cost not derived: the S9 plan cannot be built from streamc.robot_params ({plan_failures[0]})")
        stage_cost = s8_cost.not_estimated(basis, f"No quantities, so no estimate: the S9 plan cannot be built from these RobotParams ({plan_failures[0]}).")
    else:
        merged = s8_cost.merge_quantities(quantity_sets)
        animals, why_not = s8_cost.animal_quantity(inputs.candidates, inputs.pathogen, config)
        if animals is not None:
            merged = s8_cost.merge_quantities([merged, [animals]])
            extra = "The animal item is `total_animals` of the S10 design for these candidates and streamc.study, with that design's assumptions."
        else:
            extra = f"No animal item: {why_not}."
            cost_warnings.append(f"S8 cost: no animal item ({why_not})")
        stage_cost = s8_cost.estimate(merged, prices, basis=basis, report_unused=True, extra_note=extra)
    payload = {
        "pathogen": {"scientific_name": inputs.pathogen.name, "taxid": inputs.pathogen.taxid},
        "selection": inputs.selection,
        "candidates": entries,
        "order_fasta": order_ref,
        "order_fasta_missing": order_missing,
        "cost": stage_cost.model_dump(mode="json"),
        "narratives": narrator.summary(),
    }
    # `inputs` overrides artifacts.DEFAULT_INPUTS["s8"] (s4, s5, s6, s7): S8 records the stages it really read (s0, s2 and, when stream B's
    # S3-S7 exist, s7: B's S7 artifact is self-contained, so s4-s6 are not read here).
    with payload_keys_applied(STAGE, PAYLOAD_KEYS):
        return store.write_artifact(
            STAGE, payload, params=params, inputs=inputs.stages, tools={},
            status="failed" if problems else "ok", warnings=[*problems, *inputs.warnings, *order_warnings, *cost_warnings, *narrator.warnings])
