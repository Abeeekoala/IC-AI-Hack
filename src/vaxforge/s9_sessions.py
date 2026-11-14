"""S9 session library: one builder per S8 step kind that the OT-2 can (partly) do.

Each builder returns a `SessionPlan` (see s9_plan.py): ONE robot run on ONE deck layout.
Between sessions a human does the things the README's automatability matrix says the
robot cannot (incubators, centrifuges, readers, sterile culture). Those hand-offs are
explicit `Pause` actions whose text starts `HUMAN [<step>]:`.

PLACEHOLDER PARAMETERS. Every number in `RobotParams` is a coarse default so the
pipeline produces a runnable, internally consistent design. None is validated for any
kit, enzyme, cell line or assay. Replace them from the kit insert / your lab SOP before
anything is run. S9's reports and generated files say so in plain words.

Pipette-rule note: sessions are written so that, under Text2WetLab's five physical rules
(see s9_validate.py), a fresh tip is used whenever the liquid source changes or a tip has
been inside a destination well. That is why e.g. the ELISA wash block consumes many tips.
"""

from __future__ import annotations

from typing import Callable

from pydantic import BaseModel, Field, model_validator

from vaxforge.s8_protocol import Protocol, Step, StepKind
from vaxforge.s9_plan import PlanBuilder, PlanError, Ref, SessionPlan, well_names

ROWS = "ABCDEFGH"

PLACEHOLDER_BANNER = (
    "PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, "
    "enzyme, cell line or assay. Set them from the kit insert / your SOP before use."
)

DEFAULT_CONDITIONS = [
    "vehicle / buffer only",
    "LPS positive control",
    "endotoxin-matched LPS control",
    "antigen low dose",
    "antigen mid dose",
    "antigen high dose",
    "antigen mid dose + polymyxin B",
    "mock-purification eluate",
]


class RobotParams(BaseModel):
    """All S9 numbers in one place. PLACEHOLDERS - see the module docstring."""

    # ---- experiment layout (shared by stimulation / ELISA / qPCR / FACS sessions) ----
    conditions: list[str] = Field(default_factory=lambda: list(DEFAULT_CONDITIONS))  # rows A.. (max 8)
    replicates: int = Field(3, ge=1, le=3)  # columns 1..  (max 3 so a qPCR gene block fits 4 columns)
    dead_volume_ul: dict[str, float] = Field(default_factory=lambda: {"tubes": 30.0, "reservoir": 500.0, "plate": 10.0, "waste": 0.0})

    # ---- s02 plasmid assembly ----
    assembly_total_ul: float = 20.0
    assembly_vector_ul: float = 2.0
    assembly_insert_ul: float = 2.0
    assembly_buffer_ul: float = 2.0
    assembly_enzyme_ul: float = 2.0
    assembly_incubation_c: float = 37.0
    assembly_incubation_min: float = 60.0

    # ---- s03 transformation ----
    cells_ul: float = 25.0
    dna_ul: float = 1.0
    ice_min: float = 30.0
    heatshock_c: float = 42.0
    heatshock_s: float = 45.0
    recovery_ice_min: float = 2.0
    soc_ul: float = 50.0

    # ---- s04 colony PCR ----
    colonies: int = Field(4, ge=1, le=20)
    pcr_total_ul: float = 20.0
    pcr_mm2x_ul: float = 10.0
    pcr_primer_ul: float = 1.0  # each of forward / reverse
    pcr_template_ul: float = 1.0
    pcr_template_stock_ul: float = 10.0  # colony resuspension prepared by the human

    # ---- s05 expression / induction ----
    induced_wells: int = Field(3, ge=1, le=8)
    uninduced_wells: int = Field(3, ge=1, le=8)
    culture_ul: float = 1000.0
    inducer_stock_mM: float = 100.0
    inducer_final_mM: float = 0.5
    culture_mix_ul: float = 100.0

    # ---- s06 lysis ----
    lysis_buffer_ul: float = 300.0
    lysis_mix_reps: int = 5
    lysis_mix_ul: float = 200.0

    # ---- s07 Ni-NTA magnetic-bead purification ----
    lysate_ul: float = 500.0
    bead_slurry_ul: float = 50.0
    bead_bind_min: float = 30.0
    bead_bind_cycles: int = 3  # robot mixing cycles during binding (a rotator is better if validated)
    bead_wash_ul: float = 300.0
    bead_wash_cycles: int = 2
    elution_ul: float = 100.0
    elute_min: float = 10.0
    magnet_settle_s: float = 120.0
    residual_ul: float = 10.0  # left behind on beads so they are not aspirated

    # ---- s08 endotoxin removal (method itself is NOT fixed by S8) ----
    removal_sample_ul: float = 80.0
    removal_reagent_ul: float = 50.0

    # ---- s09 LAL ----
    lal_points: int = Field(6, ge=3, le=7)
    lal_dilution_factor: float = 10.0
    lal_well_ul: float = 50.0  # sample / standard per well; LAL reagent added in equal volume
    lal_reagent_ul: float = 50.0

    # ---- s11 THP-1 stimulation ----
    stim_ul: float = 20.0
    well_medium_ul: float = 180.0

    # ---- s12 ELISA (one analyte per plate; extend by running the session per analyte) ----
    analyte: str = "TNF-alpha"
    supernatant_ul: float = 100.0
    elisa_sample_ul: float = 50.0
    elisa_std_columns: int = Field(1, ge=1, le=2)
    elisa_std_top_ul: float = 100.0
    elisa_wash_ul: float = 180.0
    elisa_wash_cycles: int = Field(3, ge=1, le=5)
    elisa_detect_ul: float = 100.0
    elisa_hrp_ul: float = 100.0
    elisa_substrate_ul: float = 100.0
    elisa_stop_ul: float = 50.0
    elisa_sample_incubation_min: float = 120.0
    elisa_detect_incubation_min: float = 60.0
    elisa_hrp_incubation_min: float = 30.0
    elisa_substrate_incubation_min: float = 20.0

    # ---- s13 RT-qPCR ----
    qpcr_targets: list[str] = Field(default_factory=lambda: ["GAPDH (reference)", "TNF", "IL1B"])
    qpcr_total_ul: float = 20.0
    qpcr_mix2x_ul: float = 10.0
    qpcr_primers_ul: float = 1.0  # forward+reverse premix per reaction
    qpcr_cdna_ul: float = 4.0
    qpcr_cdna_stock_ul: float = 20.0

    # ---- s14 FACS staining prep ----
    facs_cells_ul: float = 100.0
    facs_stain_ul: float = 50.0

    @model_validator(mode="after")
    def _check(self) -> "RobotParams":
        if not 1 <= len(self.conditions) <= 8:
            raise ValueError("conditions must have 1-8 entries (they occupy plate rows A-H)")
        if not 1 <= len(self.qpcr_targets) <= 3:
            raise ValueError("qpcr_targets must have 1-3 entries (each gene block uses 4 plate columns)")
        return self


# --------------------------------------------------------------------------- helpers


def _human(step_id: str, text: str) -> str:
    return f"HUMAN [{step_id}]: {text}"


def seq_wells(n: int, start: int = 0) -> list[str]:
    """Column-major well names A1, B1 .. H1, A2 ..."""
    names = [f"{ROWS[i % 8]}{i // 8 + 1}" for i in range(96)]
    return names[start:start + n]


def cond_well(row: int, col: int) -> str:
    return f"{ROWS[row]}{col}"


def condition_wells(p: RobotParams) -> dict[str, list[str]]:
    """condition -> its replicate wells. Conditions are rows A.., replicates are columns 1.."""
    return {c: [cond_well(i, r + 1) for r in range(p.replicates)] for i, c in enumerate(p.conditions)}


_CONTROL_KEYWORDS = ("vehicle", "lps positive", "endotoxin-matched", "polymyxin", "mock")


def _check_s8_controls(step: Step, labels: list[str]) -> None:
    """Every control S8 promised for this step must exist in the session's layout."""
    low = [l.lower() for l in labels]
    for control in step.controls:
        cl = control.lower()
        for kw in _CONTROL_KEYWORDS:
            if kw in cl and not any(kw in l for l in low):
                raise PlanError(f"S8 control {control!r} has no matching condition in the plate layout (looked for {kw!r})")


def _need_positive(**kv: float) -> None:
    for k, v in kv.items():
        if v < 0:
            raise PlanError(f"parameter {k} resolved to a negative volume ({v:g} uL); check RobotParams")


# --------------------------------------------------------------------------- s02


def build_assembly(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    b = PlanBuilder(f"{sid}_plasmid_assembly", "Plasmid assembly reaction set-up", [sid], p.dead_volume_ul)
    temp = b.module("temp", "temperature module gen2", "3")
    rxn = b.labware("rxn", "opentrons_96_aluminumblock_nest_wellplate_100ul", on=temp, label="Assembly reactions on the 4 C block")
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="Reagent tubes")
    water = b.reagent(tubes["A1"], "nuclease-free water")
    buf = b.reagent(tubes["A2"], "assembly buffer (10x)")
    vec = b.reagent(tubes["A3"], "linearised expression vector")
    ins = b.reagent(tubes["A4"], "insert (synthesised gene fragment)", note="vendor delivery; S12 biosecurity screening must precede ordering")
    enz = b.reagent(tubes["A5"], "assembly enzyme mix")

    shared = p.assembly_vector_ul + p.assembly_buffer_ul + p.assembly_enzyme_ul
    w_cand = p.assembly_total_ul - shared - p.assembly_insert_ul
    w_ctrl = p.assembly_total_ul - shared
    _need_positive(water_candidate=w_cand, water_control=w_ctrl)
    mix_ul = min(10.0, p.assembly_total_ul / 2)

    b.step(sid)
    b.layout("Reaction block", {"candidate assembly": "A1", "no-insert assembly control": "A2"})
    b.set_temp(temp, 4)
    if w_cand > 0:
        b.transfer(w_cand, water, rxn["A1"])
    if w_ctrl > 0:
        b.transfer(w_ctrl, water, rxn["A2"])
    b.transfer(p.assembly_buffer_ul, buf, [rxn["A1"], rxn["A2"]])
    b.transfer(p.assembly_vector_ul, vec, [rxn["A1"], rxn["A2"]])
    b.transfer(p.assembly_insert_ul, ins, rxn["A1"])
    b.transfer(p.assembly_enzyme_ul, enz, [rxn["A1"], rxn["A2"]], mix_after=(5, mix_ul))
    b.set_temp(temp, p.assembly_incubation_c)
    b.delay(p.assembly_incubation_min * 60, "assembly incubation (enzyme-specific; replace with the supplier programme)")
    b.set_temp(temp, 4)
    b.pause(_human(sid, "If the supplier programme needs thermal cycling, run it in a thermocycler (not part of this robot run). "
                        "Keep the reactions cold, then resume to finish the session."))
    b.deactivate_temp(temp)
    b.note("Assembly chemistry and thermal programme are not specified by S8; the 4 C hold + single incubation here is a stand-in.")
    return b.build()


# --------------------------------------------------------------------------- s03


def build_transformation(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    b = PlanBuilder(f"{sid}_transformation", "Heat-shock transformation set-up", [sid], p.dead_volume_ul)
    temp = b.module("temp", "temperature module gen2", "3")
    blk = b.labware("blk", "opentrons_96_aluminumblock_nest_wellplate_100ul", on=temp, label="Competent cells on the temperature block")
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="Recovery medium")
    dna = b.labware("dna", "biorad_96_wellplate_200ul_pcr", slot="5", label="Assembly products (from the assembly session)")
    soc = b.reagent(tubes["A1"], "SOC / recovery medium")
    d1 = b.reagent(dna["A1"], "assembled plasmid (candidate)", note="from the candidate assembly reaction", dead_ul=3.0)
    d2 = b.reagent(dna["A2"], "assembled plasmid (no-insert control)", note="from the no-insert assembly control", dead_ul=3.0)
    cells = {"A1": "candidate assembly", "A2": "no-insert assembly control", "A3": "untransformed host control (no DNA)"}
    for well, what in cells.items():
        b.preload(blk[well], "competent cells", p.cells_ul, note=what)

    b.step(sid)
    b.layout("Transformation block", {v: k for k, v in cells.items()})
    b.set_temp(temp, 4)
    b.pause(_human(sid, f"Confirm {p.cells_ul:g} uL competent cells are in A1-A3 of the block, held at 4 C, then resume."))
    b.transfer(p.dna_ul, d1, blk["A1"])
    b.transfer(p.dna_ul, d2, blk["A2"])
    b.delay(p.ice_min * 60, "incubation on the cold block")
    b.set_temp(temp, p.heatshock_c)
    b.delay(p.heatshock_s, "heat shock (block ramp time dominates; validate on your hardware)")
    b.set_temp(temp, 4)
    b.delay(p.recovery_ice_min * 60, "return to cold")
    b.transfer(p.soc_ul, soc, [blk["A1"], blk["A2"], blk["A3"]])
    b.pause(_human(sid, "Recover the cells (typically shaking at 37 C), then plate on selective agar BY HAND. Plating is not automated."))
    b.deactivate_temp(temp)
    b.note("The untransformed-host control is plated by the human; S8 lists it as 'untransformed host on selection plate'.")
    return b.build()


# --------------------------------------------------------------------------- s04


def build_colony_pcr(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    n_col = p.colonies
    n_rx = n_col + 2  # candidate colonies + empty-vector colony + no-template control
    mm_vol = p.pcr_total_ul - p.pcr_template_ul
    water_per = mm_vol - p.pcr_mm2x_ul - 2 * p.pcr_primer_ul
    _need_positive(water_per_reaction=water_per)
    b = PlanBuilder(f"{sid}_colony_pcr", "Colony PCR reaction set-up", [sid], p.dead_volume_ul)
    temp = b.module("temp", "temperature module gen2", "3")
    rxn = b.labware("rxn", "opentrons_96_aluminumblock_nest_wellplate_100ul", on=temp, label="PCR reactions on the 4 C block")
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="PCR reagents + master-mix tube")
    tmpl = b.labware("tmpl", "biorad_96_wellplate_200ul_pcr", slot="5", label="Colony resuspensions (human-prepared)")
    water = b.reagent(tubes["A1"], "nuclease-free water")
    mm2x = b.reagent(tubes["A2"], "2x PCR master mix")
    fwd = b.reagent(tubes["A3"], "forward primer")
    rev = b.reagent(tubes["A4"], "reverse primer")
    mm_tube = tubes["B1"]  # EMPTY: the robot assembles the master mix here

    wells = seq_wells(n_rx)
    names = [f"candidate colony {i + 1}" for i in range(n_col)] + ["empty-vector colony control", "no-template control"]
    for i in range(n_col + 1):
        b.preload(tmpl[wells[i]], names[i] + " resuspension", p.pcr_template_stock_ul)

    batches = n_rx + 2  # master mix for two spare reactions
    b.step(sid)
    b.layout("Reaction block", {names[i]: wells[i] for i in range(n_rx)})
    b.set_temp(temp, 4)
    b.comment("assemble the master mix in tube B1")
    b.transfer(water_per * batches, water, mm_tube)
    b.transfer(p.pcr_mm2x_ul * batches, mm2x, mm_tube)
    b.transfer(p.pcr_primer_ul * batches, fwd, mm_tube)
    b.transfer(p.pcr_primer_ul * batches, rev, mm_tube)
    b.mix(5, min(50.0, mm_vol * batches / 2), mm_tube)
    b.comment("distribute master mix, then add template")
    b.distribute(mm_vol, mm_tube, [rxn[w] for w in wells], reagent="master mix")
    for i in range(n_col + 1):
        b.transfer(p.pcr_template_ul, tmpl[wells[i]], rxn[wells[i]], mix_after=(3, min(10.0, p.pcr_total_ul / 2)))
    b.transfer(p.pcr_template_ul, water, rxn[wells[n_col + 1]], mix_after=(3, min(10.0, p.pcr_total_ul / 2)))
    b.pause(_human(sid, "Seal the plate and run the PCR programme and gel/readout OFF the robot (thermocycling and reading are not automated)."))
    b.deactivate_temp(temp)
    b.note("Master mix is assembled for two spare reactions; the leftover stays in tube B1.")
    return b.build()


# --------------------------------------------------------------------------- s05


def build_induction(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    inducer_ul = p.culture_ul * p.inducer_final_mM / p.inducer_stock_mM
    if inducer_ul < 1.0:
        raise PlanError(f"inducer volume {inducer_ul:g} uL is below the p20 minimum; dilute the stock (inducer_stock_mM)")
    b = PlanBuilder(f"{sid}_expression_induction", "Inducer addition to expression cultures", [sid], p.dead_volume_ul)
    cult = b.labware("cult", "nest_96_wellplate_2ml_deep", slot="5", label="Expression cultures (human-inoculated)")
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="Inducer + vehicle")
    ind = b.reagent(tubes["A1"], "inducer stock")
    veh = b.reagent(tubes["A2"], "vehicle (same solvent, no inducer)")
    wells = seq_wells(p.induced_wells + p.uninduced_wells)
    induced, uninduced = wells[:p.induced_wells], wells[p.induced_wells:]
    for w in induced:
        b.preload(cult[w], "expression culture (to be induced)", p.culture_ul)
    for w in uninduced:
        b.preload(cult[w], "expression culture (uninduced control)", p.culture_ul)

    b.step(sid)
    b.layout("Culture plate", {"induced": ",".join(induced), "uninduced control": ",".join(uninduced)})
    b.transfer(inducer_ul, ind, [cult[w] for w in induced])
    b.transfer(inducer_ul, veh, [cult[w] for w in uninduced])
    for w in wells:
        b.mix(5, p.culture_mix_ul, cult[w], pipette="p300")
    b.pause(_human(sid, "Move the plate to the shaking incubator for expression. Shaking incubation is not automated."))
    b.note(f"Inducer volume = culture x final/stock = {p.culture_ul:g} x {p.inducer_final_mM:g}/{p.inducer_stock_mM:g} = {inducer_ul:g} uL.")
    return b.build()


# --------------------------------------------------------------------------- s06


def build_lysis(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    b = PlanBuilder(f"{sid}_lysis", "Lysis reagent addition", [sid], p.dead_volume_ul)
    pel = b.labware("pel", "nest_96_wellplate_2ml_deep", slot="5", label="Cell pellets (supernatant removed by the human)")
    res = b.labware("res", "nest_12_reservoir_15ml", slot="2", label="Lysis buffer")
    lys = b.reagent(res["A1"], "lysis buffer")
    wells = seq_wells(p.induced_wells + p.uninduced_wells)

    b.step(sid)
    b.layout("Pellet plate", {"induced": ",".join(wells[:p.induced_wells]), "uninduced control": ",".join(wells[p.induced_wells:])})
    b.transfer(p.lysis_buffer_ul, lys, [pel[w] for w in wells], pipette="p300", mix_after=(p.lysis_mix_reps, p.lysis_mix_ul))
    b.pause(_human(sid, "Incubate / sonicate as the lysis method requires, then centrifuge to clarify. Sonication and centrifugation "
                        "are not automated. Transfer clarified lysate to the purification plate (next session)."))
    return b.build()


# --------------------------------------------------------------------------- s07


def build_ni_nta(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    b = PlanBuilder(f"{sid}_ni_nta_purification", "Ni-NTA magnetic-bead purification", [sid], p.dead_volume_ul)
    waste = b.labware("waste", "nest_1_reservoir_195ml", slot="1", label="Liquid waste")
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="Bead slurry")
    eluate = b.labware("eluate", "nest_96_wellplate_200ul_flat", slot="3", label="Eluate collection plate")
    res = b.labware("res", "nest_12_reservoir_15ml", slot="5", label="Wash and elution buffers")
    mag = b.module("mag", "magnetic module gen2", "6")
    mp = b.labware("mp", "nest_96_wellplate_2ml_deep", on=mag, label="Clarified lysates on the magnetic module")
    beads = b.reagent(tubes["A1"], "Ni-NTA magnetic bead slurry")
    wash = b.reagent(res["A1"], "wash buffer")
    elu = b.reagent(res["A2"], "elution buffer")
    wells = {"A1": "candidate lysate", "B1": "empty-vector lysate (mock purification control)"}
    for w, what in wells.items():
        b.preload(mp[w], what, p.lysate_ul, note="clarified lysate placed by the human")
    wl = list(wells)

    def remove_supernatant() -> None:
        for w in wl:
            vol = b.volume_in(mp[w]) - p.residual_ul
            if vol >= 1:
                b.transfer(vol, mp[w], waste["A1"], pipette="p300", src_z=2.0)

    b.step(sid)
    b.layout("Magnetic plate", {v: k for k, v in wells.items()})
    b.comment("bind: add beads, mix periodically (robot mixing stands in for a rotator)")
    b.transfer(p.bead_slurry_ul, beads, [mp[w] for w in wl], pipette="p300", mix_before=(3, 40.0), mix_after=(5, 100.0))
    for _ in range(p.bead_bind_cycles):
        b.delay(p.bead_bind_min * 60 / p.bead_bind_cycles, "bead binding")
        for w in wl:
            b.mix(5, 100.0, mp[w], pipette="p300")
    b.magnet(mag, True)
    b.delay(p.magnet_settle_s, "beads settle")
    remove_supernatant()
    b.magnet(mag, False)
    for k in range(p.bead_wash_cycles):
        b.comment(f"wash {k + 1} of {p.bead_wash_cycles}")
        b.transfer(p.bead_wash_ul, wash, [mp[w] for w in wl], pipette="p300", mix_after=(5, 150.0))
        b.magnet(mag, True)
        b.delay(p.magnet_settle_s, "beads settle")
        remove_supernatant()
        b.magnet(mag, False)
    b.comment("elute")
    b.transfer(p.elution_ul, elu, [mp[w] for w in wl], pipette="p300", mix_after=(5, 60.0))
    b.delay(p.elute_min * 60, "elution")
    b.magnet(mag, True)
    b.delay(p.magnet_settle_s, "beads settle")
    for w in wl:
        vol = b.volume_in(mp[w]) - p.residual_ul
        b.transfer(vol, mp[w], eluate[w], pipette="p300", src_z=2.0)
    b.magnet(mag, False)
    b.pause(_human(sid, "Seal and store the eluates; take any gel / protein-assay aliquots by hand (not automated). Continue to endotoxin removal."))
    b.note("Gravity-column and FPLC formats are NOT supported; this is the magnetic-bead His-tag format only.")
    return b.build()


# --------------------------------------------------------------------------- s08


def build_endotoxin_removal(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    b = PlanBuilder(f"{sid}_endotoxin_removal", "Endotoxin-removal plate set-up", [sid], p.dead_volume_ul)
    el = b.labware("el", "nest_96_wellplate_200ul_flat", slot="3", label="Eluates from purification")
    rem = b.labware("rem", "nest_96_wellplate_2ml_deep", slot="4", label="Endotoxin-removal plate")
    res = b.labware("res", "nest_12_reservoir_15ml", slot="5", label="Removal reagent")
    reag = b.reagent(res["A1"], "endotoxin-removal reagent")
    srcs = {"A1": "candidate eluate", "B1": "empty-vector mock eluate"}
    for w, what in srcs.items():
        b.preload(el[w], what, p.elution_ul)

    b.step(sid)
    b.layout("Removal plate", {v: k for k, v in srcs.items()})
    for w in srcs:
        b.transfer(p.removal_sample_ul, el[w], rem[w], pipette="p300")
    b.transfer(p.removal_reagent_ul, reag, [rem[w] for w in srcs], pipette="p300", mix_after=(5, min(100.0, p.removal_sample_ul)))
    b.pause(_human(sid, "Incubate / separate per the chosen endotoxin-removal method (phase separation, resin or column), then recover "
                        "the endotoxin-reduced protein into endotoxin-free tubes. These steps are not automated."))
    b.note("S8 does not fix the endotoxin-removal chemistry; choose a method and replace this set-up accordingly.")
    return b.build()


# --------------------------------------------------------------------------- s09


def build_lal(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    f = p.lal_dilution_factor
    t = round(p.lal_well_ul / (f - 1), 2)  # carry-over volume for the standard series
    sample_dil = round(p.lal_well_ul / f, 2)
    b = PlanBuilder(f"{sid}_lal_assay", "LAL assay plate set-up", [sid], p.dead_volume_ul)
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="LAL reagents and samples")
    assay = b.labware("assay", "nest_96_wellplate_200ul_flat", slot="3", label="LAL assay plate (endotoxin-free)")
    dil = b.reagent(tubes["A1"], "LAL reagent water (diluent)")
    std = b.reagent(tubes["A2"], "endotoxin standard (top concentration)")
    cs = b.reagent(tubes["A3"], "candidate sample (endotoxin-reduced)", note="from endotoxin removal")
    ms = b.reagent(tubes["A4"], "mock eluate sample (endotoxin-reduced)", note="from endotoxin removal")
    lal = b.reagent(tubes["A5"], "LAL reagent")
    std_wells = [f"{ROWS[i]}1" for i in range(p.lal_points)]
    blank = f"{ROWS[p.lal_points]}1"

    b.step(sid)
    b.comment("standard curve: serial dilution down column 1")
    b.transfer(p.lal_well_ul + t, std, assay[std_wells[0]])
    for w in std_wells[1:-1]:
        b.transfer(p.lal_well_ul, dil, assay[w])
    b.transfer(p.lal_well_ul - t, dil, assay[std_wells[-1]])
    for a, c in zip(std_wells, std_wells[1:]):
        b.transfer(t, assay[a], assay[c], mix_after=(3, min(20.0, p.lal_well_ul / 2)))
    b.transfer(p.lal_well_ul, dil, assay[blank])
    b.comment("samples in column 2: neat, diluted, and spiked (positive product control)")
    layout = {f"standard {i + 1}": w for i, w in enumerate(std_wells)} | {"blank (diluent only)": blank}
    col2 = iter(f"{ROWS[i]}2" for i in range(6))
    for label, tube in (("candidate", cs), ("mock eluate", ms)):
        neat, dilw, ppc = next(col2), next(col2), next(col2)
        layout |= {f"{label} neat": neat, f"{label} 1:{f:g}": dilw, f"{label} + endotoxin spike (PPC)": ppc}
        b.transfer(p.lal_well_ul, tube, assay[neat])
        b.transfer(p.lal_well_ul - sample_dil, dil, assay[dilw])
        b.transfer(sample_dil, tube, assay[dilw], mix_after=(3, min(20.0, p.lal_well_ul / 2)))
        b.transfer(p.lal_well_ul - sample_dil, tube, assay[ppc])
        b.transfer(sample_dil, std, assay[ppc], mix_after=(3, min(20.0, p.lal_well_ul / 2)))
    b.layout("LAL plate", layout)
    used = std_wells + [blank] + [w for w in layout.values() if w.endswith("2")]
    b.comment("add LAL reagent to every used well (one tip, no contact with the wells)")
    b.transfer(p.lal_reagent_ul, lal, [assay[w] for w in used], pipette="p300", new_tip="once", dst_pos="top", dst_z=-2.0)
    b.pause(_human(sid, "Move the plate to the incubating plate reader and read per the kit. Reading is not automated."))
    b.note(f"S8 gate for this step: {step.gate or '(none)'}")
    b.note("Use certified endotoxin-free tips and plates for every liquid-handling step of this assay.")
    return b.build()


# --------------------------------------------------------------------------- s11


def build_stimulation(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    _check_s8_controls(step, p.conditions)
    cw = condition_wells(p)
    b = PlanBuilder(f"{sid}_antigen_stimulation", "Antigen / control addition to seeded THP-1 plate", [sid], p.dead_volume_ul)
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="Stimulus tubes (human-prepared)")
    cells = b.labware("cells", "corning_96_wellplate_360ul_flat", slot="5", label="Seeded, differentiated THP-1 plate")
    tube_wells = well_names("opentrons_24_tuberack_nest_1.5ml_snapcap")
    for i, cond in enumerate(p.conditions):
        note = "prepare at the LPS level measured by LAL for the matching antigen dose" if "endotoxin-matched" in cond.lower() else "human-prepared stimulus"
        b.reagent(tubes[tube_wells[i]], cond, note=note)
        for w in cw[cond]:
            b.preload(cells[w], "THP-1 cells in medium", p.well_medium_ul, note="seeded and differentiated by the human")

    b.step(sid)
    b.layout("Stimulation plate (rows = conditions, columns = replicates)", {c: ",".join(ws) for c, ws in cw.items()})
    for i, cond in enumerate(p.conditions):
        b.transfer(p.stim_ul, tubes[tube_wells[i]], [cells[w] for w in cw[cond]], reagent=cond)
    b.pause(_human(sid, "Return the plate to the 37 C / 5% CO2 incubator for the stimulation period, then collect supernatants (ELISA) "
                        "and lyse cells (RT-qPCR). The incubator is not automated."))
    b.note("Sterility: the OT-2 is not sterile by itself. Run inside a biosafety cabinet / HEPA enclosure with sterile filter tips.")
    b.note("S8 control list for this step: " + "; ".join(step.controls))
    return b.build()


# --------------------------------------------------------------------------- s12 (two sessions)


def build_elisa_setup(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    cw = condition_wells(p)
    b = PlanBuilder(f"{sid}_elisa_setup", f"ELISA ({p.analyte}) standards and sample loading", [sid], p.dead_volume_ul)
    waste = b.labware("waste", "nest_1_reservoir_195ml", slot="1", label="Liquid waste")
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="Assay diluent + top standard")
    assay = b.labware("assay", "nest_96_wellplate_200ul_flat", slot="3", label="ELISA plate (pre-coated and blocked)")
    sup = b.labware("sup", "nest_96_wellplate_200ul_flat", slot="5", label="Collected supernatants (human-prepared)")
    dil = b.reagent(tubes["A1"], "assay diluent")
    top = b.reagent(tubes["A2"], f"{p.analyte} top standard")
    for cond, ws in cw.items():
        for w in ws:
            b.preload(sup[w], f"supernatant: {cond}", p.supernatant_ul, note="collected from the stimulation plate")

    half = p.elisa_sample_ul
    b.step(sid)
    layout: dict[str, str] = {}
    for c in range(1, p.elisa_std_columns + 1):
        col = [f"{r}{c}" for r in ROWS]
        b.comment(f"standard curve in column {c}: 2-fold serial dilution (top standard in {col[0]})")
        b.transfer(p.elisa_std_top_ul, top, assay[col[0]])
        b.transfer(half, dil, [assay[w] for w in col[1:7]])
        for a, d in zip(col[:6], col[1:7]):
            b.transfer(half, assay[a], assay[d], mix_after=(3, min(60.0, half + half)))
        b.transfer(half, assay[col[6]], waste["A1"])
        b.transfer(half, dil, assay[col[7]])
        layout[f"standard column {c} (A=top ... G=lowest, H=blank)"] = f"{col[0]}-{col[7]}"
    b.comment("samples")
    for cond, ws in cw.items():
        row = ROWS[p.conditions.index(cond)]
        for r, src in enumerate(ws):
            dst = f"{row}{p.elisa_std_columns + r + 1}"
            b.transfer(p.elisa_sample_ul, sup[src], assay[dst])
        layout[cond] = ",".join(f"{row}{p.elisa_std_columns + r + 1}" for r in range(len(ws)))
    b.layout("ELISA plate", layout)
    b.pause(_human(sid, f"Seal the plate and incubate {p.elisa_sample_incubation_min:g} min (room temperature, shaking if the kit says so). "
                        "Then put the plate back on the deck for the develop session. Sealing and incubation are not automated."))
    b.note("One analyte per plate. A single standard column is used by default; set elisa_std_columns=2 for duplicates (costs more tips).")
    return b.build()


def build_elisa_develop(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    cols = list(range(1, p.elisa_std_columns + p.replicates + 1))
    b = PlanBuilder(f"{sid}_elisa_develop", f"ELISA ({p.analyte}) washes, detection and development", [sid], p.dead_volume_ul)
    rea = b.labware("rea", "nest_12_reservoir_15ml", slot="2", label="Detection reagents")
    assay = b.labware("assay", "nest_96_wellplate_200ul_flat", slot="3", label="ELISA plate (sealed incubation finished)")
    waste = b.labware("waste", "nest_1_reservoir_195ml", slot="4", label="Liquid waste")
    wash = b.labware("wash", "nest_1_reservoir_195ml", slot="5", label="Wash buffer")
    wb = b.reagent(wash["A1"], "wash buffer", dead_ul=2000.0)
    det = b.reagent(rea["A1"], "detection antibody")
    hrp = b.reagent(rea["A2"], "streptavidin-HRP")
    sub = b.reagent(rea["A3"], "substrate")
    stp = b.reagent(rea["A4"], "stop solution")
    for c in cols:
        for r in ROWS:
            b.preload(assay[f"{r}{c}"], "standard / sample (from the setup session)", p.elisa_sample_ul)
    top = [assay[f"A{c}"] for c in cols]  # the multichannel head addresses each column by its A-row well

    def aspirate_all() -> None:
        for c in cols:
            vol = b.volume_in(assay[f"A{c}"])
            b.transfer(vol, assay[f"A{c}"], waste["A1"], pipette="p300m", src_z=1.0)

    def wash_block() -> None:
        for _ in range(p.elisa_wash_cycles):
            b.transfer(p.elisa_wash_ul, wb, top, pipette="p300m", new_tip="once", dst_pos="top", dst_z=-2.0)
            aspirate_all()

    def add(reagent: Ref, ul: float) -> None:
        b.transfer(ul, reagent, top, pipette="p300m", new_tip="once", dst_pos="top", dst_z=-2.0)

    b.step(sid)
    b.comment("remove sample, then wash")
    aspirate_all()
    wash_block()
    b.comment("detection antibody")
    add(det, p.elisa_detect_ul)
    b.pause(_human(sid, f"Seal and incubate {p.elisa_detect_incubation_min:g} min, then unseal and resume."))
    aspirate_all()
    wash_block()
    b.comment("streptavidin-HRP")
    add(hrp, p.elisa_hrp_ul)
    b.pause(_human(sid, f"Seal and incubate {p.elisa_hrp_incubation_min:g} min, then unseal and resume."))
    aspirate_all()
    wash_block()
    b.comment("substrate")
    add(sub, p.elisa_substrate_ul)
    b.pause(_human(sid, f"Protect from light and incubate {p.elisa_substrate_incubation_min:g} min, then resume."))
    add(stp, p.elisa_stop_ul)
    b.pause(_human(sid, "Read the plate in a plate reader at the kit's wavelength. Reading is not automated."))
    b.note("Fresh tip per column for every aspiration (no cross-contamination between samples); this is why the session is tip-hungry.")
    return b.build()


# --------------------------------------------------------------------------- s13


def build_rt_qpcr(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    cw = condition_wells(p)
    genes = p.qpcr_targets
    per_gene_mm = p.qpcr_total_ul - p.qpcr_cdna_ul
    water_per = per_gene_mm - p.qpcr_mix2x_ul - p.qpcr_primers_ul
    _need_positive(qpcr_water_per_reaction=water_per)
    b = PlanBuilder(f"{sid}_rt_qpcr", "RT-qPCR reaction set-up", [sid], p.dead_volume_ul)
    temp = b.module("temp", "temperature module gen2", "3")
    q = b.labware("q", "opentrons_96_aluminumblock_nest_wellplate_100ul", on=temp, label="qPCR plate on the 4 C block")
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="qPCR reagents + master-mix tubes")
    cd = b.labware("cd", "biorad_96_wellplate_200ul_pcr", slot="5", label="cDNA (human-prepared; same layout as the stimulation plate)")
    water = b.reagent(tubes["A1"], "nuclease-free water")
    mix2x = b.reagent(tubes["A2"], "2x qPCR master mix")
    primers = [b.reagent(tubes[f"A{3 + g}"], f"{genes[g]} primer pair") for g in range(len(genes))]
    mm_tubes = [tubes[f"B{1 + g}"] for g in range(len(genes))]  # EMPTY: filled by the robot
    for cond, ws in cw.items():
        for w in ws:
            b.preload(cd[w], f"cDNA: {cond}", p.qpcr_cdna_stock_ul)
    b.preload(cd["A4"], "no-RT control (vehicle RNA, reverse transcriptase omitted)", p.qpcr_cdna_stock_ul)

    b.step(sid)
    b.set_temp(temp, 4)
    layout: dict[str, str] = {}
    for g, gene in enumerate(genes):
        c0 = g * 4  # gene block = columns c0+1 .. c0+4
        sample_wells = [(src, f"{src[0]}{c0 + int(src[1:])}") for ws in cw.values() for src in ws]
        ntc, norT = f"A{c0 + 4}", f"B{c0 + 4}"
        targets = [d for _, d in sample_wells] + [ntc, norT]
        n_batches = len(targets) + 2
        b.comment(f"{gene}: assemble master mix in tube {mm_tubes[g].well}")
        b.transfer(water_per * n_batches, water, mm_tubes[g])
        b.transfer(p.qpcr_mix2x_ul * n_batches, mix2x, mm_tubes[g])
        b.transfer(p.qpcr_primers_ul * n_batches, primers[g], mm_tubes[g])
        b.mix(5, min(50.0, per_gene_mm * n_batches / 2), mm_tubes[g])
        b.distribute(per_gene_mm, mm_tubes[g], [q[w] for w in targets], reagent=f"{gene} master mix")
        for src, dst in sample_wells:
            b.transfer(p.qpcr_cdna_ul, cd[src], q[dst], mix_after=(3, min(10.0, p.qpcr_total_ul / 2)))
        b.transfer(p.qpcr_cdna_ul, water, q[ntc], mix_after=(3, min(10.0, p.qpcr_total_ul / 2)))
        b.transfer(p.qpcr_cdna_ul, cd["A4"], q[norT], mix_after=(3, min(10.0, p.qpcr_total_ul / 2)))
        layout[f"{gene}: samples"] = f"columns {c0 + 1}-{c0 + p.replicates} (rows = conditions)"
        layout[f"{gene}: no-template control"] = ntc
        layout[f"{gene}: no-RT control"] = norT
    b.layout("qPCR plate", layout)
    b.pause(_human(sid, "Seal the plate, spin it down and load the qPCR instrument. Thermocycling and reading are not automated."))
    b.deactivate_temp(temp)
    return b.build()


# --------------------------------------------------------------------------- s14


def build_facs_prep(proto: Protocol, step: Step, p: RobotParams) -> SessionPlan:
    sid = step.step_id
    cw = condition_wells(p)
    b = PlanBuilder(f"{sid}_facs_prep", "FACS staining-cocktail addition", [sid], p.dead_volume_ul)
    tubes = b.labware("tubes", "opentrons_24_tuberack_nest_1.5ml_snapcap", slot="2", label="Staining cocktail")
    cells = b.labware("cells", "nest_96_wellplate_200ul_flat", slot="5", label="Cells to stain (human-transferred)")
    cock = b.reagent(tubes["A1"], "staining antibody cocktail")
    wells = [w for ws in cw.values() for w in ws]
    for cond, ws in cw.items():
        for w in ws:
            b.preload(cells[w], f"cells: {cond}", p.facs_cells_ul)

    b.step(sid)
    b.layout("Staining plate", {c: ",".join(ws) for c, ws in cw.items()})
    b.transfer(p.facs_stain_ul, cock, [cells[w] for w in wells], new_tip="once", dst_pos="top", dst_z=-2.0)
    b.pause(_human(sid, "Incubate protected from light, wash / centrifuge and acquire on the cytometer. None of this is automated."))
    b.note("A V-bottom plate is usual for cytometry; the S9 catalogue only has flat-bottom plates, so substitute labware before use.")
    return b.build()


# --------------------------------------------------------------------------- registry

Builder = Callable[[Protocol, Step, RobotParams], SessionPlan]

BUILDERS: dict[StepKind, list[Builder]] = {
    StepKind.PLASMID_ASSEMBLY: [build_assembly],
    StepKind.TRANSFORMATION: [build_transformation],
    StepKind.COLONY_PCR: [build_colony_pcr],
    StepKind.EXPRESSION_INDUCTION: [build_induction],
    StepKind.LYSIS: [build_lysis],
    StepKind.NI_NTA_PURIFICATION: [build_ni_nta],
    StepKind.ENDOTOXIN_REMOVAL: [build_endotoxin_removal],
    StepKind.LAL_ASSAY: [build_lal],
    StepKind.ANTIGEN_STIMULATION: [build_stimulation],
    StepKind.ELISA: [build_elisa_setup, build_elisa_develop],
    StepKind.RT_QPCR: [build_rt_qpcr],
    StepKind.FACS: [build_facs_prep],
}

# S8 step kinds with NO robot session (README automatability matrix: a human or other instrument).
HUMAN_ONLY = {
    StepKind.GENE_SYNTHESIS: "Outsourced to a vendor; sequence must clear the S12 biosecurity gate before ordering.",
    StepKind.THP1_CULTURE: "CO2 incubator and sterile cell culture; done by a person.",
}


def plan_sessions(protocol: Protocol, params: RobotParams | None = None) -> list[SessionPlan]:
    """Build every robot session the S8 protocol calls for, in step order."""
    params = params or RobotParams()
    plans: list[SessionPlan] = []
    for step in protocol.steps:
        for builder in BUILDERS.get(step.kind, []):
            plans.append(builder(protocol, step, params))
    return plans
