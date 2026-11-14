"""S9 plan layer: a small, validated intermediate representation (IR) for OT-2 sessions.

Why an IR instead of emitting Python text directly?
  * Physical rules (well names, pipette volume range, well capacity, tip budget)
    are checked BEFORE any code exists.
  * The same IR yields the *expected* liquid movements, which is the ground truth
    S9's validator compares a simulation against. That check is what makes an
    LLM-written protocol (see s9_llm.py) auditable: the code must do what the IR says.
  * The renderer below is the only place that knows OT-2 API syntax.

A `SessionPlan` is ONE robot run on ONE deck layout. A wet-lab workflow spans days
and human steps, so S9 emits several sessions, each covering the robot-owned
(or robot-assisted) part of one or more S8 steps. Nothing here claims a plan is
*correct for the lab*: every numeric parameter upstream of this module is a
placeholder to be set from the real kit insert / SOP (see s9_sessions.RobotParams).
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Annotated, Literal, Union

from pydantic import BaseModel, ConfigDict, Field

API_LEVEL = "2.19"
ROBOT_TYPE = "OT-2"

# --------------------------------------------------------------------------- catalogue

LabwareKind = Literal["tiprack", "plate", "reservoir", "tubes", "waste"]


class LabwareInfo(BaseModel):
    kind: LabwareKind
    rows: int
    cols: int
    well_max_ul: float
    description: str


# Capacities are cross-checked against the simulator's own labware definitions in
# the test-suite (tests/wetlab/test_s9_robot.py::test_catalog_matches_simulator).
CATALOG: dict[str, LabwareInfo] = {
    "opentrons_96_tiprack_20ul": LabwareInfo(kind="tiprack", rows=8, cols=12, well_max_ul=20, description="OT-2 96 tip rack 20 uL"),
    "opentrons_96_tiprack_300ul": LabwareInfo(kind="tiprack", rows=8, cols=12, well_max_ul=300, description="OT-2 96 tip rack 300 uL"),
    "opentrons_24_tuberack_nest_1.5ml_snapcap": LabwareInfo(kind="tubes", rows=4, cols=6, well_max_ul=1500, description="24 x 1.5 mL snap-cap tube rack"),
    "opentrons_96_aluminumblock_nest_wellplate_100ul": LabwareInfo(kind="plate", rows=8, cols=12, well_max_ul=100, description="96-well aluminium block + 100 uL PCR plate"),
    "biorad_96_wellplate_200ul_pcr": LabwareInfo(kind="plate", rows=8, cols=12, well_max_ul=200, description="96-well 200 uL PCR plate"),
    "nest_96_wellplate_200ul_flat": LabwareInfo(kind="plate", rows=8, cols=12, well_max_ul=200, description="96-well 200 uL flat plate"),
    "corning_96_wellplate_360ul_flat": LabwareInfo(kind="plate", rows=8, cols=12, well_max_ul=360, description="96-well 360 uL flat cell-culture plate"),
    "nest_96_wellplate_2ml_deep": LabwareInfo(kind="plate", rows=8, cols=12, well_max_ul=2000, description="96 deep-well 2 mL plate"),
    "nest_12_reservoir_15ml": LabwareInfo(kind="reservoir", rows=1, cols=12, well_max_ul=15000, description="12-well 15 mL reservoir"),
    "nest_1_reservoir_195ml": LabwareInfo(kind="waste", rows=1, cols=1, well_max_ul=195000, description="1-well 195 mL reservoir (waste)"),
}

MODULES = {
    "temperature module gen2": {"slots": ("1", "3", "4", "6", "7", "9", "10"), "short": "temp"},
    "magnetic module gen2": {"slots": ("1", "3", "4", "6", "7", "9", "10"), "short": "mag"},
}

# role -> (api name, mount, min uL, max uL, channels, tip rack load name)
PIPETTES = {
    "p20": ("p20_single_gen2", "left", 1.0, 20.0, 1, "opentrons_96_tiprack_20ul"),
    "p300": ("p300_single_gen2", "right", 20.0, 300.0, 1, "opentrons_96_tiprack_300ul"),
    "p300m": ("p300_multi_gen2", "right", 20.0, 300.0, 8, "opentrons_96_tiprack_300ul"),
}

# Preferred slots for tip racks, tried in order when they are free.
TIP_SLOT_PREFERENCE = ("11", "10", "9", "8", "7", "6", "5", "4", "3", "2", "1")


class PlanError(ValueError):
    """The plan cannot be physically realised on an OT-2 as described."""


def well_names(load_name: str) -> list[str]:
    info = CATALOG[load_name]
    return [f"{chr(ord('A') + r)}{c}" for c in range(1, info.cols + 1) for r in range(info.rows)]


_WELL_RE = re.compile(r"^([A-H])(\d{1,2})$")


def valid_well(load_name: str, well: str) -> bool:
    info = CATALOG.get(load_name)
    m = _WELL_RE.match(well)
    if info is None or m is None:
        return False
    return ord(m.group(1)) - ord("A") < info.rows and 1 <= int(m.group(2)) <= info.cols


def expand_multi(load_name: str, well: str, channels: int) -> list[tuple[str, int]]:
    """Wells touched by one multichannel (or single) action addressed at `well`.

    Returns (well, multiplicity). On a 96-format plate an 8-channel head touches the
    whole column; on a reservoir all channels share the one well.
    """
    if channels == 1:
        return [(well, 1)]
    info = CATALOG[load_name]
    if info.rows >= channels:
        col = _WELL_RE.match(well).group(2)  # type: ignore[union-attr]
        return [(f"{chr(ord('A') + r)}{col}", 1) for r in range(channels)]
    return [(well, channels)]


# --------------------------------------------------------------------------- IR models


class Ref(BaseModel):
    model_config = ConfigDict(frozen=True)
    labware: str
    well: str

    def __str__(self) -> str:
        return f"{self.labware}:{self.well}"


class LabwareSpec(BaseModel):
    key: str
    load_name: str
    slot: str  # for module-mounted labware this is the MODULE's slot
    module: str | None = None  # module key when mounted on a module
    label: str | None = None
    kind: LabwareKind


class ModuleSpec(BaseModel):
    key: str
    name: str
    slot: str


class PipetteSpec(BaseModel):
    key: str
    api_name: str
    mount: str
    channels: int
    tipracks: list[str]


class Liquid(BaseModel):
    ref: Ref
    reagent: str
    volume_ul: float
    source: Literal["robot_loaded", "human_prepared"]
    note: str | None = None


class _Action(BaseModel):
    step: str | None = None  # S8 step id this action belongs to


class Comment(_Action):
    op: Literal["comment"] = "comment"
    text: str


class Pause(_Action):
    op: Literal["pause"] = "pause"
    text: str


class Delay(_Action):
    op: Literal["delay"] = "delay"
    seconds: float
    text: str | None = None


class SetTemp(_Action):
    op: Literal["set_temp"] = "set_temp"
    module: str
    celsius: float
    wait: bool = True


class DeactivateTemp(_Action):
    op: Literal["deactivate_temp"] = "deactivate_temp"
    module: str


class Magnet(_Action):
    op: Literal["magnet"] = "magnet"
    module: str
    engage: bool


class PickUp(_Action):
    op: Literal["pick_up"] = "pick_up"
    pipette: str


class Drop(_Action):
    op: Literal["drop"] = "drop"
    pipette: str


class Transfer(_Action):
    op: Literal["transfer"] = "transfer"
    pipette: str
    volume: float
    src: Ref
    dst: list[Ref]
    new_tip: Literal["always", "once", "never"] = "always"
    mix_before: tuple[int, float] | None = None
    mix_after: tuple[int, float] | None = None
    src_pos: Literal["bottom", "top"] = "bottom"
    src_z: float | None = None
    dst_pos: Literal["bottom", "top"] = "bottom"
    dst_z: float | None = None
    reagent: str | None = None


class Distribute(_Action):
    op: Literal["distribute"] = "distribute"
    pipette: str
    volume: float
    src: Ref
    dst: list[Ref]
    disposal_ul: float | None = None
    reagent: str | None = None


class Mix(_Action):
    op: Literal["mix"] = "mix"
    pipette: str
    reps: int
    volume: float
    at: Ref
    new_tip: Literal["once", "never"] = "once"


Action = Annotated[
    Union[Comment, Pause, Delay, SetTemp, DeactivateTemp, Magnet, PickUp, Drop, Transfer, Distribute, Mix],
    Field(discriminator="op"),
]


class SessionPlan(BaseModel):
    session_id: str
    title: str
    covers: list[str]  # S8 step ids
    modules: list[ModuleSpec]
    labware: list[LabwareSpec]
    pipettes: list[PipetteSpec]
    liquids: list[Liquid]
    actions: list[Action]
    tips_needed: dict[str, int]  # pipette key -> pickups (a multichannel pickup counts once)
    notes: list[str] = Field(default_factory=list)
    layouts: dict[str, dict[str, str]] = Field(default_factory=dict)  # doc only: label -> {condition: wells}

    # ---- helpers -------------------------------------------------------------------
    def labware_by_key(self, key: str) -> LabwareSpec:
        for lw in self.labware:
            if lw.key == key:
                return lw
        raise KeyError(key)

    def pipette_by_key(self, key: str) -> PipetteSpec:
        for p in self.pipettes:
            if p.key == key:
                return p
        raise KeyError(key)

    def slot_of(self, labware_key: str) -> str:
        return self.labware_by_key(labware_key).slot

    def plan_hash(self) -> str:
        blob = json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()


# --------------------------------------------------------------------------- expectations


def expected_dispensed(plan: SessionPlan) -> dict[tuple[str, str], float]:
    """Net volume each (slot, well) must RECEIVE from non-mix actions, in uL.

    This is the ground truth the simulation is compared against.
    """
    out: dict[tuple[str, str], float] = {}
    for a in plan.actions:
        if not isinstance(a, (Transfer, Distribute)):
            continue
        channels = plan.pipette_by_key(a.pipette).channels
        for dst in a.dst:
            lw = plan.labware_by_key(dst.labware)
            for well, mult in expand_multi(lw.load_name, dst.well, channels):
                key = (lw.slot, well)
                out[key] = out.get(key, 0.0) + a.volume * mult
    return {k: round(v, 6) for k, v in out.items()}


def expected_aspirated_min(plan: SessionPlan) -> dict[tuple[str, str], float]:
    """Minimum volume each (slot, well) must SUPPLY (disposal volumes excluded), in uL."""
    out: dict[tuple[str, str], float] = {}
    for a in plan.actions:
        if not isinstance(a, (Transfer, Distribute)):
            continue
        channels = plan.pipette_by_key(a.pipette).channels
        lw = plan.labware_by_key(a.src.labware)
        for well, mult in expand_multi(lw.load_name, a.src.well, channels):
            key = (lw.slot, well)
            out[key] = out.get(key, 0.0) + a.volume * len(a.dst) * mult
    return {k: round(v, 6) for k, v in out.items()}


# --------------------------------------------------------------------------- builder


class Handle:
    """`h["A1"]` -> Ref, so session code reads naturally."""

    def __init__(self, key: str):
        self.key = key

    def __getitem__(self, well: str) -> Ref:
        return Ref(labware=self.key, well=well)


_EPS = 1e-9


class PlanBuilder:
    """Builds a `SessionPlan` and models liquid volumes as it goes.

    Volume tracking is constructive correctness: a plan that would aspirate from an empty
    well, overfill a well, or mix more liquid than a well holds raises `PlanError` here,
    before any code exists. The validator in s9_validate.py later re-checks the *generated
    code* with an independent replay; two implementations that agree are the point.
    """

    def __init__(self, session_id: str, title: str, covers: list[str], dead_volume_ul: dict[str, float] | None = None):
        self.session_id = session_id
        self.title = title
        self.covers = covers
        self.dead = {"tubes": 30.0, "reservoir": 500.0, "plate": 10.0, "waste": 0.0, **(dead_volume_ul or {})}
        self._modules: dict[str, ModuleSpec] = {}
        self._labware: dict[str, LabwareSpec] = {}
        self._pipettes: dict[str, PipetteSpec] = {}
        self._liquids: dict[Ref, Liquid] = {}
        self._stock: dict[Ref, tuple[str, str | None]] = {}  # robot-loaded reagents (volume computed in build())
        self._drawn: dict[Ref, float] = {}  # total drawn from each stock well
        self._dead_override: dict[Ref, float] = {}
        self._vol: dict[Ref, float] = {}  # running volume of every NON-stock well
        self._actions: list[Action] = []
        self._tips: dict[str, int] = {}
        self._step: str | None = None
        self._notes: list[str] = []
        self._layouts: dict[str, dict[str, str]] = {}

    # ---- deck ----------------------------------------------------------------------
    def _used_slots(self) -> set[str]:
        return {m.slot for m in self._modules.values()} | {l.slot for l in self._labware.values() if l.module is None}

    def module(self, key: str, name: str, slot: str) -> str:
        if name not in MODULES:
            raise PlanError(f"unsupported module {name!r}")
        if slot not in MODULES[name]["slots"]:
            raise PlanError(f"{name} cannot sit in slot {slot}")
        if slot in self._used_slots():
            raise PlanError(f"slot {slot} already used")
        self._modules[key] = ModuleSpec(key=key, name=name, slot=slot)
        return key

    def labware(self, key: str, load_name: str, *, slot: str | None = None, on: str | None = None, label: str | None = None) -> Handle:
        if load_name not in CATALOG:
            raise PlanError(f"labware {load_name!r} is not in the S9 catalogue")
        if key in self._labware:
            raise PlanError(f"duplicate labware key {key!r}")
        info = CATALOG[load_name]
        if (slot is None) == (on is None):
            raise PlanError("give exactly one of slot= or on=")
        if on is not None:
            if on not in self._modules:
                raise PlanError(f"unknown module {on!r}")
            if any(l.module == on for l in self._labware.values()):
                raise PlanError(f"module {on!r} already carries labware")
            self._labware[key] = LabwareSpec(key=key, load_name=load_name, slot=self._modules[on].slot, module=on, label=label, kind=info.kind)
        else:
            assert slot is not None
            if slot in self._used_slots():
                raise PlanError(f"slot {slot} already used")
            if slot == "12":
                raise PlanError("slot 12 is the fixed trash")
            self._labware[key] = LabwareSpec(key=key, load_name=load_name, slot=slot, label=label, kind=info.kind)
        return Handle(key)

    def step(self, step_id: str) -> None:
        self._step = step_id

    def note(self, text: str) -> None:
        self._notes.append(text)

    def layout(self, label: str, mapping: dict[str, str]) -> None:
        self._layouts[label] = mapping

    # ---- liquids -------------------------------------------------------------------
    def reagent(self, ref: Ref, name: str, note: str | None = None, dead_ul: float | None = None) -> Ref:
        """Declare a robot-loaded stock; its load volume = everything drawn + dead volume.

        `dead_ul` overrides the per-labware-kind default (e.g. a few uL for a PCR-plate well).
        """
        self._check_ref(ref)
        if ref in self._liquids or ref in self._vol:
            raise PlanError(f"{ref} already holds other content")
        if ref in self._stock and self._stock[ref][0] != name:
            raise PlanError(f"{ref} already holds {self._stock[ref][0]!r}")
        self._stock[ref] = (name, note)
        if dead_ul is not None:
            self._dead_override[ref] = dead_ul
        self._drawn.setdefault(ref, 0.0)
        return ref

    def preload(self, ref: Ref, name: str, ul: float, note: str | None = None) -> Ref:
        """Declare content a HUMAN puts on the deck (cells, lysate, a seeded plate...)."""
        self._check_ref(ref)
        if ref in self._stock:
            raise PlanError(f"{ref} is a robot-loaded stock")
        cap = CATALOG[self._labware[ref.labware].load_name].well_max_ul
        if ul > cap:
            raise PlanError(f"{ref}: preload {ul} uL exceeds well capacity {cap} uL")
        self._liquids[ref] = Liquid(ref=ref, reagent=name, volume_ul=ul, source="human_prepared", note=note)
        self._vol[ref] = ul
        return ref

    # ---- validation helpers ------------------------------------------------------------
    def _check_ref(self, ref: Ref) -> None:
        lw = self._labware.get(ref.labware)
        if lw is None:
            raise PlanError(f"unknown labware {ref.labware!r}")
        if lw.kind == "tiprack":
            raise PlanError(f"{ref}: tip racks are not liquid sources or destinations")
        if not valid_well(lw.load_name, ref.well):
            raise PlanError(f"{ref}: no such well on {lw.load_name}")

    def _pipette(self, role: str) -> str:
        if role not in PIPETTES:
            raise PlanError(f"unknown pipette role {role!r}")
        other = {"p300": "p300m", "p300m": "p300"}.get(role)
        if other and other in self._pipettes:
            raise PlanError("p300 single and p300 multi share the right mount; use only one per session")
        if role not in self._pipettes:
            api, mount, _lo, _hi, ch, _rack = PIPETTES[role]
            self._pipettes[role] = PipetteSpec(key=role, api_name=api, mount=mount, channels=ch, tipracks=[])
        return role

    def _auto_pipette(self, volume: float, requested: str | None) -> str:
        if requested is not None:
            return self._pipette(requested)
        return self._pipette("p20" if volume <= PIPETTES["p20"][3] else "p300")

    @staticmethod
    def _check_volume(role: str, volume: float, what: str) -> None:
        _api, _mount, lo, hi, _ch, _rack = PIPETTES[role]
        if volume < lo - _EPS:
            raise PlanError(f"{what}: {volume} uL is below the {role} minimum of {lo} uL")
        if role == "p20" and volume > hi + _EPS:
            raise PlanError(f"{what}: {volume} uL exceeds the {role} maximum of {hi} uL")

    def _cells(self, ref: Ref, role: str) -> list[tuple[Ref, int]]:
        lw = self._labware[ref.labware]
        return [(Ref(labware=ref.labware, well=w), m) for w, m in expand_multi(lw.load_name, ref.well, PIPETTES[role][4])]

    def _take(self, ref: Ref, role: str, volume: float, what: str) -> None:
        """Remove `volume` (per channel) from `ref`; stocks are unlimited but counted."""
        for cell, mult in self._cells(ref, role):
            total = volume * mult
            if cell in self._stock:
                self._drawn[cell] = self._drawn.get(cell, 0.0) + total
                continue
            have = self._vol.get(cell, 0.0)
            if have + _EPS < total:
                raise PlanError(f"{what}: aspirating {total:g} uL from {cell} but only {have:g} uL is there")
            self._vol[cell] = have - total

    def _put(self, ref: Ref, role: str, volume: float, what: str) -> None:
        for cell, mult in self._cells(ref, role):
            if cell in self._stock:
                raise PlanError(f"{what}: {cell} is a robot-loaded stock; do not dispense into it")
            cap = CATALOG[self._labware[cell.labware].load_name].well_max_ul
            new = self._vol.get(cell, 0.0) + volume * mult
            if new > cap + _EPS:
                raise PlanError(f"{what}: {cell} would hold {new:g} uL but the well holds {cap:g} uL")
            self._vol[cell] = new

    def _need_mixable(self, ref: Ref, role: str, volume: float, what: str) -> None:
        for cell, _mult in self._cells(ref, role):
            if cell in self._stock:
                continue
            if self._vol.get(cell, 0.0) + _EPS < volume:
                raise PlanError(f"{what}: mixing {volume:g} uL in {cell} which holds {self._vol.get(cell, 0.0):g} uL")

    def _push(self, action: Action) -> None:
        if action.step is None:
            action.step = self._step
        self._actions.append(action)

    def volume_in(self, ref: Ref) -> float:
        """Current modelled volume of a non-stock well (for session code that needs it)."""
        return self._vol.get(ref, 0.0)

    # ---- actions -------------------------------------------------------------------
    def comment(self, text: str) -> None:
        self._push(Comment(text=text))

    def pause(self, text: str) -> None:
        self._push(Pause(text=text))

    def delay(self, seconds: float, text: str | None = None) -> None:
        if seconds < 0:
            raise PlanError("negative delay")
        self._push(Delay(seconds=seconds, text=text))

    def set_temp(self, module: str, celsius: float, wait: bool = True) -> None:
        mod = self._modules.get(module)
        if mod is None or mod.name != "temperature module gen2":
            raise PlanError(f"{module!r} is not a temperature module on this deck")
        if not 4 <= celsius <= 95:
            raise PlanError(f"temperature {celsius} C outside the module's 4-95 C range")
        self._push(SetTemp(module=module, celsius=celsius, wait=wait))

    def deactivate_temp(self, module: str) -> None:
        self._push(DeactivateTemp(module=module))

    def magnet(self, module: str, engage: bool) -> None:
        mod = self._modules.get(module)
        if mod is None or mod.name != "magnetic module gen2":
            raise PlanError(f"{module!r} is not a magnetic module on this deck")
        self._push(Magnet(module=module, engage=engage))

    def pick_up(self, pipette: str) -> None:
        p = self._pipette(pipette)
        self._tips[p] = self._tips.get(p, 0) + 1
        self._push(PickUp(pipette=p))

    def drop(self, pipette: str) -> None:
        self._push(Drop(pipette=self._pipette(pipette)))

    def transfer(
        self,
        volume: float,
        src: Ref,
        dst: Ref | list[Ref],
        *,
        pipette: str | None = None,
        new_tip: Literal["always", "once", "never"] = "always",
        mix_before: tuple[int, float] | None = None,
        mix_after: tuple[int, float] | None = None,
        src_pos: Literal["bottom", "top"] = "bottom",
        src_z: float | None = None,
        dst_pos: Literal["bottom", "top"] = "bottom",
        dst_z: float | None = None,
        reagent: str | None = None,
    ) -> None:
        dsts = dst if isinstance(dst, list) else [dst]
        if not dsts:
            raise PlanError("transfer with no destination")
        role = self._auto_pipette(volume, pipette)
        self._check_volume(role, volume, "transfer")
        for m in (mix_before, mix_after):
            if m is not None:
                self._check_volume(role, m[1], "mix volume")
        for r in (src, *dsts):
            self._check_ref(r)
        if mix_after is not None and len(dsts) > 1 and new_tip != "always":
            # One tip that mixes in a destination and then visits another is cross-contamination
            # under Text2WetLab's rule; forbid it by construction.
            raise PlanError("mix_after with several destinations needs new_tip='always'")
        what = f"transfer {volume:g} uL {src}->"
        for d in dsts:
            if mix_before is not None:
                self._need_mixable(src, role, mix_before[1], what + str(d))
            self._take(src, role, volume, what + str(d))
            self._put(d, role, volume, what + str(d))
            if mix_after is not None:
                self._need_mixable(d, role, mix_after[1], what + str(d))
        if new_tip == "always":
            self._tips[role] = self._tips.get(role, 0) + len(dsts)
        elif new_tip == "once":
            self._tips[role] = self._tips.get(role, 0) + 1
        self._push(Transfer(pipette=role, volume=volume, src=src, dst=dsts, new_tip=new_tip, mix_before=mix_before,
                            mix_after=mix_after, src_pos=src_pos, src_z=src_z, dst_pos=dst_pos, dst_z=dst_z, reagent=reagent))

    def distribute(self, volume: float, src: Ref, dst: list[Ref], *, pipette: str | None = None,
                   disposal_ul: float | None = None, reagent: str | None = None) -> None:
        role = self._auto_pipette(volume, pipette)
        if PIPETTES[role][4] != 1:
            raise PlanError("distribute is single-channel only in S9")
        self._check_volume(role, volume, "distribute")
        if not dst:
            raise PlanError("distribute with no destination")
        for r in (src, *dst):
            self._check_ref(r)
        disposal = PIPETTES[role][2] if disposal_ul is None else disposal_ul
        what = f"distribute {volume:g} uL {src}"
        cap = PIPETTES[role][3]
        if volume + disposal > cap + _EPS:
            raise PlanError(f"{what}: {volume:g} + {disposal:g} uL disposal exceeds the {role} capacity {cap:g} uL")
        # Verified against the simulator: every aspiration trip also draws the disposal volume,
        # which is blown out to trash, and a trip serves floor((capacity - disposal) / volume) wells.
        per_trip = max(1, int((cap - disposal + _EPS) // volume))
        trips = math.ceil(len(dst) / per_trip)
        self._take(src, role, volume * len(dst) + trips * disposal, what)
        for d in dst:
            self._put(d, role, volume, what + f"->{d}")
        self._tips[role] = self._tips.get(role, 0) + 1
        self._push(Distribute(pipette=role, volume=volume, src=src, dst=dst, disposal_ul=disposal, reagent=reagent))

    def mix(self, reps: int, volume: float, at: Ref, *, pipette: str | None = None, new_tip: Literal["once", "never"] = "once") -> None:
        role = self._auto_pipette(volume, pipette)
        self._check_volume(role, volume, "mix")
        self._check_ref(at)
        self._need_mixable(at, role, volume, f"mix at {at}")
        if new_tip == "once":
            self._tips[role] = self._tips.get(role, 0) + 1
        self._push(Mix(pipette=role, reps=reps, volume=volume, at=at, new_tip=new_tip))

    # ---- finish --------------------------------------------------------------------
    def build(self) -> SessionPlan:
        liquids = dict(self._liquids)
        for ref, (name, note) in self._stock.items():
            lw = self._labware[ref.labware]
            drawn = self._drawn.get(ref, 0.0)
            vol = math.ceil(drawn + self._dead_override.get(ref, self.dead[lw.kind]))
            cap = CATALOG[lw.load_name].well_max_ul
            if vol > cap:
                raise PlanError(f"{ref}: needs {vol} uL ({name}) but the well holds {cap} uL")
            liquids[ref] = Liquid(ref=ref, reagent=name, volume_ul=float(vol), source="robot_loaded", note=note)

        # tip racks: separate racks for single and multichannel use
        pipettes: list[PipetteSpec] = []
        labware = list(self._labware.values())
        used = self._used_slots()
        free = [s for s in TIP_SLOT_PREFERENCE if s not in used]
        for key in sorted(self._pipettes):
            api, mount, _lo, _hi, ch, rack = PIPETTES[key]
            pickups = self._tips.get(key, 0)
            per_rack = 12 if ch == 8 else 96
            n_racks = max(1, math.ceil(pickups / per_rack))
            rack_keys = []
            for i in range(n_racks):
                if not free:
                    raise PlanError(f"no free deck slot for tip rack {i + 1} of {key}; split the session")
                slot = free.pop(0)
                rk = f"tips_{key}_{i + 1}"
                labware.append(LabwareSpec(key=rk, load_name=rack, slot=slot, label=f"{key} tips", kind="tiprack"))
                rack_keys.append(rk)
            pipettes.append(PipetteSpec(key=key, api_name=api, mount=mount, channels=ch, tipracks=rack_keys))

        return SessionPlan(
            session_id=self.session_id, title=self.title, covers=self.covers,
            modules=list(self._modules.values()), labware=labware, pipettes=pipettes,
            liquids=sorted(liquids.values(), key=lambda l: (l.ref.labware, l.ref.well)),
            actions=self._actions, tips_needed=dict(self._tips), notes=list(self._notes), layouts=dict(self._layouts),
        )


# --------------------------------------------------------------------------- rendering

_OWNER_BY_AUTOMATION = {"full": "ROBOT", "partial": "ROBOT-PARTIAL", "manual": "HUMAN"}
LIQUID_PALETTE = ("#33AAFF", "#E8A33D", "#6FBF73", "#C86DD7", "#E0605E", "#4DB6AC", "#9E9D24", "#8D6E63")


def banner(step_id: str, title: str, owner: str, note: str) -> str:
    """The annotation line S9+ parses: `[<step_id>] <title> | <OWNER> | <note>`."""
    clean = lambda s: str(s).replace("|", "/").replace('"', "'").replace("\n", " ").strip()  # noqa: E731
    return f"[{step_id}] {clean(title)} | {owner} | {clean(note)}"


def _num(x: float) -> str:
    return repr(int(x)) if float(x).is_integer() else repr(round(float(x), 6))


def _loc(ref: Ref, pos: str, z: float | None) -> str:
    base = f'{ref.labware}["{ref.well}"]'
    if z is None and pos == "bottom":
        return base
    return f"{base}.{pos}(z={_num(z if z is not None else 0.0)})"


def _loc_list(refs: list[Ref], pos: str, z: float | None) -> str:
    items = [_loc(r, pos, z) for r in refs]
    if len(items) == 1:
        return items[0]
    one = "[" + ", ".join(items) + "]"
    if len(one) <= 88:
        return one
    return "[\n" + "".join(f"            {i},\n" for i in items) + "        ]"


def _py_str(s: str) -> str:
    return json.dumps(s, ensure_ascii=True)


def render_python(
    plan: SessionPlan,
    *,
    step_info: dict[str, tuple[str, str, str]],
    header: dict[str, str] | None = None,
) -> str:
    """Render a plan to OT-2 Python (API 2.19).

    `step_info[step_id] = (title, OWNER, note)` drives the per-step annotation comments
    so a human reading the file sees what the robot owns and what it does not.
    """
    L: list[str] = []
    w = L.append
    w("# Generated by VaxForge S9 (stream C). Review before running; this is a DESIGN artefact.")
    w(f"# Session: {plan.session_id} - {plan.title}")
    w(f"# Covers S8 steps: {', '.join(plan.covers)}")
    for k, v in (header or {}).items():
        w(f"# {k}: {' '.join(str(v).split())}")
    w(f"# plan-sha256: {plan.plan_hash()}")
    w("")
    w("from opentrons import protocol_api")
    w("")
    w(f'requirements = {{"robotType": "{ROBOT_TYPE}", "apiLevel": "{API_LEVEL}"}}')
    w("metadata = {")
    w(f'    "protocolName": {_py_str("VaxForge " + plan.session_id)},')
    w(f'    "description": {_py_str(plan.title)},')
    w('    "author": "VaxForge S9 (generated)",')
    w("}")
    w("")
    w("")
    w("def run(ctx: protocol_api.ProtocolContext):")
    w("    # ---- deck -------------------------------------------------------------")
    for m in plan.modules:
        w(f'    {m.key} = ctx.load_module("{m.name}", "{m.slot}")')
    for lw in plan.labware:
        label = f'  # {lw.label}' if lw.label else ""
        if lw.module:
            w(f'    {lw.key} = {lw.module}.load_labware("{lw.load_name}"){label}')
        else:
            w(f'    {lw.key} = ctx.load_labware("{lw.load_name}", "{lw.slot}"){label}')
    for p in plan.pipettes:
        racks = ", ".join(p.tipracks)
        w(f'    {p.key} = ctx.load_instrument("{p.api_name}", "{p.mount}", tip_racks=[{racks}])')
    if plan.liquids:
        # Declared initial contents make the file self-describing: the operator's setup sheet and
        # any grader (S9 validator, S9.5 win-rate harness) can read what each well holds at start.
        w("")
        w("    # ---- initial deck contents (what the operator loads; see the setup sheet) ----")
        var_of: dict[str, str] = {}
        for liq in plan.liquids:
            if liq.reagent not in var_of:
                var = f"liq_{len(var_of) + 1}"
                var_of[liq.reagent] = var
                color = LIQUID_PALETTE[(len(var_of) - 1) % len(LIQUID_PALETTE)]
                w(f"    {var} = ctx.define_liquid(name={_py_str(liq.reagent)}, description={_py_str(liq.source)}, "
                  f'display_color="{color}")')
        for liq in plan.liquids:
            w(f'    {liq.ref.labware}["{liq.ref.well}"].load_liquid(liquid={var_of[liq.reagent]}, volume={_num(liq.volume_ul)})')
    w("")
    w("    # ---- steps ------------------------------------------------------------")
    current: str | None = None
    for a in plan.actions:
        if a.step is not None and a.step != current:
            current = a.step
            title, owner, note = step_info.get(a.step, (a.step, "ROBOT-PARTIAL", ""))
            text = banner(a.step, title, owner, note)
            w("")
            w(f"    # {text}")
            w(f"    ctx.comment({_py_str(text)})")
        if isinstance(a, Comment):
            w(f"    ctx.comment({_py_str(a.text)})")
        elif isinstance(a, Pause):
            w(f"    ctx.pause({_py_str(a.text)})")
        elif isinstance(a, Delay):
            msg = f", msg={_py_str(a.text)}" if a.text else ""
            w(f"    ctx.delay(seconds={_num(a.seconds)}{msg})")
        elif isinstance(a, SetTemp):
            if a.wait:
                w(f"    {a.module}.set_temperature({_num(a.celsius)})")
            else:
                w(f"    {a.module}.start_set_temperature({_num(a.celsius)})")
        elif isinstance(a, DeactivateTemp):
            w(f"    {a.module}.deactivate()")
        elif isinstance(a, Magnet):
            w(f"    {a.module}.{'engage' if a.engage else 'disengage'}()")
        elif isinstance(a, PickUp):
            w(f"    {a.pipette}.pick_up_tip()")
        elif isinstance(a, Drop):
            w(f"    {a.pipette}.drop_tip()")
        elif isinstance(a, Mix):
            if a.new_tip == "once":
                w(f"    {a.pipette}.pick_up_tip()")
            w(f"    {a.pipette}.mix({a.reps}, {_num(a.volume)}, {_loc(a.at, 'bottom', None)})")
            if a.new_tip == "once":
                w(f"    {a.pipette}.drop_tip()")
        elif isinstance(a, Transfer):
            args = [_num(a.volume), _loc(a.src, a.src_pos, a.src_z), _loc_list(a.dst, a.dst_pos, a.dst_z), f'new_tip="{a.new_tip}"']
            if a.mix_before:
                args.append(f"mix_before=({a.mix_before[0]}, {_num(a.mix_before[1])})")
            if a.mix_after:
                args.append(f"mix_after=({a.mix_after[0]}, {_num(a.mix_after[1])})")
            if a.reagent:
                w(f"    # {a.reagent}")
            w(f"    {a.pipette}.transfer({', '.join(args)})")
        elif isinstance(a, Distribute):
            args = [_num(a.volume), _loc(a.src, "bottom", None), _loc_list(a.dst, "bottom", None), 'new_tip="once"']
            if a.disposal_ul is not None:
                args.append(f"disposal_volume={_num(a.disposal_ul)}")
            if a.reagent:
                w(f"    # {a.reagent}")
            w(f"    {a.pipette}.distribute({', '.join(args)})")
    w("")
    return "\n".join(L)


# --------------------------------------------------------------------------- operator sheet


def setup_markdown(plan: SessionPlan, step_info: dict[str, tuple[str, str, str]]) -> str:
    """Human-readable deck set-up sheet: what to load where, and what the robot will NOT do."""
    out = [f"# {plan.title}", "", f"Session `{plan.session_id}` covers S8 step(s): {', '.join(plan.covers)}", "",
           "> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. "
           "Confirm against the kit insert / your SOP before use.", "", "## Deck", "", "| Slot | Labware | Notes |", "|---|---|---|"]
    for m in plan.modules:
        out.append(f"| {m.slot} | {m.name} | module |")
    for lw in sorted(plan.labware, key=lambda l: int(l.slot)):
        where = f"on {lw.module}" if lw.module else ""
        out.append(f"| {lw.slot} | `{lw.load_name}` | {(lw.label or '')} {where} |".replace("  ", " "))
    out.append("| 12 | fixed trash | |")
    out += ["", "## Pipettes and tips", ""]
    for p in plan.pipettes:
        n = plan.tips_needed.get(p.key, 0)
        out.append(f"- `{p.api_name}` ({p.mount}): {n} tip pick-up(s); {len(p.tipracks)} rack(s) loaded")
    out += ["", "## Load before starting", "", "| Where | What | Volume (uL) | Prepared by |", "|---|---|---|---|"]
    for liq in plan.liquids:
        lw = plan.labware_by_key(liq.ref.labware)
        who = "robot operator (load)" if liq.source == "robot_loaded" else "HUMAN (prepared beforehand)"
        out.append(f"| slot {lw.slot} {liq.ref.well} | {liq.reagent}{' - ' + liq.note if liq.note else ''} | {liq.volume_ul:g} | {who} |")
    if plan.layouts:
        out += ["", "## Plate layouts", ""]
        for label, mapping in plan.layouts.items():
            out.append(f"**{label}**")
            out.append("")
            for cond, wells in mapping.items():
                out.append(f"- {cond}: {wells}")
            out.append("")
    out += ["", "## Step ownership", ""]
    seen = []
    for a in plan.actions:
        if a.step and a.step not in seen:
            seen.append(a.step)
    for sid in seen:
        title, owner, note = step_info.get(sid, (sid, "", ""))
        out.append(f"- **{sid} {title}** - {owner}: {note}")
    pauses = [a for a in plan.actions if isinstance(a, Pause)]
    if pauses:
        out += ["", "## Human hand-offs inside this run (the robot pauses)", ""]
        out += [f"{i + 1}. {p.text}" for i, p in enumerate(pauses)]
    if plan.notes:
        out += ["", "## Notes", ""] + [f"- {n}" for n in plan.notes]
    return "\n".join(out) + "\n"
