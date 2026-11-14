"""S9+ - protocol animation (optional stage; cut it without touching the main line).

Pipeline position::

    protocol.py -> opentrons_simulate -> _ot_sim_runner.py -> sim JSON ("vaxforge.ot_sim/1")
                -> build_timeline() -> timeline.json ("vaxforge.timeline/1")
                -> render_html()    -> protocol_animation.html (one self-contained file)

What it is for: it turns "the generated robot code actually simulates" from a number into
something a judge can WATCH - deck, labware, tip pick-up, aspirate/dispense with source and
destination wells, volumes, modules, human pauses, and an estimated clock.

What it is NOT: a robot measurement. Every time in the output is an ESTIMATE computed from the
simulator's command list with the constants in ``TIME_MODEL`` (emitted verbatim as
``timeline["time_model"]``). The simulator also does not know the initial volume of any well, so
the player shows per-well NET CHANGE (dispensed minus aspirated), never an absolute volume, and it
invents no reagent colours or contents.

Public API
----------
``build_timeline(sim) -> dict``       pure; never raises for any JSON-shaped input
``render_html(timeline) -> str``      pure; offline, ASCII-only, no network resources of any kind
``write_animation(sim_json_path, out_dir) -> dict``   {"timeline": path, "html": path, "ok": bool}
``main(argv=None) -> int``            ``python -m vaxforge.s9plus_animate <sim.json> <out_dir>``
                                      exit code 0 = animation written, 1 = sim failed (an honest
                                      "simulation failed" page was written), 2 = usage error

Timeline schema ("vaxforge.timeline/1")
---------------------------------------
Top-level keys: ``schema, ok, error, source, time_model, deck, initial_state, frames, phases,
totals, warnings``.

``deck`` (millimetres; origin = front-left corner of slot 1, +x right, +y towards the BACK of the
robot - the player flips y so the front is at the bottom): ``extent {w,h}``, ``footprint [w,h]``,
``home [x,y]``, ``channel_pitch_mm``, ``slots[12]``, ``labware[]`` (each with ``slot, load_name,
display_name, label, kind, module, rect [x,y,w,h], wells{name: {x,y,shape,w,h,max_ul}}``),
``modules[]``, ``pipettes[]`` (with ``channel_offsets_y``: nozzle y-offsets from the head centre,
rear nozzle first), ``trash {slot, rect, point}``. Well coordinates are absolute deck coordinates,
copied from the simulator.

``frames[]`` - ONE frame per simulator event, in order (group headers such as transfer/mix are
frames too, with zero duration, so nothing is counted twice). Keys: ``index, event_idx, level,
parent, kind, raw_kind, is_group, caption, detail, text, start_s, end_s, duration_s, move_s,
action_s, human, human_step, pipette, head_mount, head_before, head_after, move_mm, volume_ul,
volume_total_ul, group_volume_ul, focus, focus_preview, well_net_ul, tips_consumed,
tips_used_total, modules, pipettes, phase``.

* ``start_s/end_s`` estimated elapsed seconds; ``duration_s`` = ``move_s`` (gantry travel) +
  ``action_s``. A pause is a HUMAN step: ``human: true``, ``duration_s: null`` and
  ``start_s == end_s`` (no estimated duration, nothing added to the total).
* ``head_before/head_after`` [x, y] of the pipette head CENTRE (for an 8-channel head the centre
  of its 8 nozzles; nozzle y = centre y + deck.pipettes[*].channel_offsets_y).
* ``well_net_ul`` - SPARSE: only the wells touched by this frame, mapped to their cumulative net
  change in microlitres AFTER the frame (dispensed minus aspirated; multi-channel volumes are per
  channel, so a reservoir well that feeds all 8 channels changes by 8x). Keys are ``"<slot>:<well>"``.
  ``net_volumes_after(timeline, i)`` returns the full cumulative map.
* ``tips_consumed`` - SPARSE: the tip positions (``"<slot>:<well>"``) newly consumed by this frame;
  an 8-channel pick-up consumes a whole column. ``tips_used_total`` is the running count of
  individual tips used. ``modules`` / ``pipettes`` are small FULL snapshots after the frame
  (temperature set-point / magnet engaged; tip attached and liquid held per mount).
* ``focus`` - wells to highlight: ``[{"key", "role"}]`` with role source/dest/tip/trash/here
  (``focus_preview`` is true on group headers, which only announce what the following primitive
  steps will do).
* ``phase`` - index into ``phases`` (see the annotation convention) or null.

Annotation convention (emitted by the S9 generator): robot comments
``[<step_id>] <title> | <OWNER> | <note>`` with OWNER in ROBOT / ROBOT-PARTIAL / HUMAN, and pauses
``HUMAN [<step_id>]: <instructions>``. They become ``phases`` (step_id, title, owner, note,
first_frame, last_frame, start_s, end_s); anything that does not match is just a caption.
"""

from __future__ import annotations

import copy
import html as _html
import json
import math
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from vaxforge.artifacts import REQUIRED_PAYLOAD_KEYS, Artifact, RunStore, register_payload_keys

__all__ = [
    "SIM_SCHEMA",
    "TIMELINE_SCHEMA",
    "TIME_MODEL",
    "build_timeline",
    "main",
    "net_volumes_after",
    "parse_human_pause",
    "parse_phase_comment",
    "render_html",
    "run",
    "write_animation",
]

STAGE = "s9plus"
PAYLOAD_KEYS = ("sessions", "summary")  # A's structural check for status "ok", applied only while this stage writes

TIMELINE_SCHEMA = "vaxforge.timeline/1"
SIM_SCHEMA = "vaxforge.ot_sim/1"

# --------------------------------------------------------------------------------------
# Deck geometry (OT-2, millimetres)
# --------------------------------------------------------------------------------------
SLOT_FOOTPRINT_MM = (127.76, 85.48)
SLOT_ORIGINS_MM: dict[str, tuple[float, float]] = {
    "1": (0.0, 0.0), "2": (132.5, 0.0), "3": (265.0, 0.0),
    "4": (0.0, 90.5), "5": (132.5, 90.5), "6": (265.0, 90.5),
    "7": (0.0, 181.0), "8": (132.5, 181.0), "9": (265.0, 181.0),
    "10": (0.0, 271.5), "11": (132.5, 271.5), "12": (265.0, 271.5),
}
DECK_EXTENT_MM = (265.0 + SLOT_FOOTPRINT_MM[0], 271.5 + SLOT_FOOTPRINT_MM[1])
CHANNEL_PITCH_MM = 9.0
MAX_CHANNELS = 8
_HIT_TOL_MM = 0.5  # how far outside a well's edge a nozzle still counts as "over" it

# --------------------------------------------------------------------------------------
# Time model: ALL constants in one dict, emitted verbatim in timeline["time_model"].
# Everything here is a PLACEHOLDER of a plausible order of magnitude. None of it was measured.
# --------------------------------------------------------------------------------------
TIME_MODEL: dict[str, Any] = {
    "gantry_speed_mm_s": 250.0,       # constant XY speed; no acceleration, no Z travel
    "default_flow_rate_ul_s": 10.0,   # only when the simulator printed no flow rate for a command
    "pick_up_tip_s": 3.0,             # fixed, on top of the travel to the tip rack
    "drop_tip_s": 2.0,
    "blow_out_s": 1.0,
    "touch_tip_s": 3.0,
    "temperature_set_s": 60.0,        # PLACEHOLDER: the real ramp depends on the module and delta-T
    "temperature_wait_s": 60.0,       # PLACEHOLDER
    "temperature_deactivate_s": 1.0,
    "magnet_move_s": 5.0,             # PLACEHOLDER for engage / disengage
    "home_s": 5.0,
    "comment_s": 0.0,
    "unknown_command_s": 0.0,
    "home_xy_mm": [385.0, 321.0],     # where the gantry parks (back-right); first move starts here
}

_TIME_RULES = [
    "Every time here is an ESTIMATE computed from the simulator's command list. Nothing was "
    "measured on a robot.",
    "Aspirate / dispense: volume (µL, per channel) divided by the flow rate. The flow rate is "
    "the one the simulator printed for that command; if it printed none, default_flow_rate_ul_s "
    "is used.",
    "Gantry travel: straight-line XY distance between consecutive pipette positions divided by "
    "gantry_speed_mm_s (constant speed; no acceleration, no Z travel, mount offset ignored). The "
    "first move starts from home_xy_mm.",
    "Tip pick-up, tip drop, blow-out, touch-tip: fixed seconds (pick_up_tip_s, drop_tip_s, "
    "blow_out_s, touch_tip_s) on top of the travel time.",
    "Delay: the exact number of seconds written in the protocol.",
    "Temperature module set / wait: a placeholder (temperature_set_s, temperature_wait_s). The "
    "real time depends on the module and the temperature change.",
    "Magnetic module engage / disengage, temperature deactivate, home: fixed placeholders "
    "(magnet_move_s, temperature_deactivate_s, home_s).",
    "Pause: a HUMAN step. The robot waits for a person, so no duration is estimated and none is "
    "added to the total.",
    "Comments, unrecognised commands and the headers of transfer / distribute / consolidate / "
    "mix take 0 s. Only their primitive steps (pick-up, aspirate, dispense, ...) carry time and "
    "volume, so nothing is counted twice.",
]

_KNOWN_KINDS = frozenset({
    "comment", "pick_up_tip", "drop_tip", "aspirate", "dispense", "blow_out", "touch_tip", "mix",
    "transfer", "distribute", "consolidate", "temperature_set", "temperature_wait",
    "temperature_deactivate", "magnet_engage", "magnet_disengage", "delay", "pause", "home",
    "other",
})
_GROUP_KINDS = frozenset({"transfer", "distribute", "consolidate", "mix"})
_PIPETTE_KINDS = frozenset({"pick_up_tip", "drop_tip", "aspirate", "dispense", "blow_out", "touch_tip"})
_LABWARE_KINDS = frozenset({"tiprack", "plate", "reservoir", "tubes", "other"})
_LABWARE_WORD = {
    "tiprack": "tip rack", "plate": "plate", "reservoir": "reservoir", "tubes": "tube rack",
    "other": "labware",
}
OWNERS = ("ROBOT", "ROBOT-PARTIAL", "HUMAN")

_MU = "µ"
_DEG = "°"


# --------------------------------------------------------------------------------------
# Small helpers
# --------------------------------------------------------------------------------------
def _num(x: Any) -> float | None:
    if isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _int(x: Any, default: int | None = None) -> int | None:
    v = _num(x)
    return int(v) if v is not None else default


def _str(x: Any, default: str = "") -> str:
    if x is None:
        return default
    return x if isinstance(x, str) else str(x)


def _r(v: float | None, nd: int = 3) -> float | None:
    if v is None:
        return None
    out = round(float(v), nd)
    return 0.0 if out == 0 else out


def _slot(x: Any) -> str | None:
    if x is None or isinstance(x, bool):
        return None
    s = str(x).strip()
    return s or None


def _mount(x: Any) -> str | None:
    if x is None:
        return None
    s = str(x).strip().lower().rsplit(".", 1)[-1]
    return s or None


def _as_list(x: Any) -> list:
    return x if isinstance(x, list) else []


def _fmt(v: float) -> str:
    s = f"{v:.2f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _fmt_dur(seconds: float) -> str:
    s = max(0.0, float(seconds))
    if s < 60:
        return f"{_fmt(s)} s"
    total = int(round(s))
    h, rem = divmod(total, 3600)
    m, sec = divmod(rem, 60)
    parts = []
    if h:
        parts.append(f"{h} h")
    if m:
        parts.append(f"{m} min")
    if sec:
        parts.append(f"{sec} s")
    return " ".join(parts) or "0 s"


def _basename(path: Any) -> str:
    """Last path component, whichever separator it uses (keeps local directories out of artifacts)."""
    s = _str(path).strip().replace("\\", "/")
    return s.rsplit("/", 1)[-1]


def _nozzle_offsets(channels: int) -> list[float]:
    """y-offsets of the nozzles from the head centre, rear-most nozzle first."""
    n = max(1, min(int(channels or 1), MAX_CHANNELS))
    return [((n - 1) / 2.0 - k) * CHANNEL_PITCH_MM for k in range(n)]


# --------------------------------------------------------------------------------------
# Annotation convention
# --------------------------------------------------------------------------------------
_PHASE_RE = re.compile(r"^\s*\[(?P<step>[A-Za-z0-9][A-Za-z0-9._\-]*)\]\s*(?P<rest>.*)$", re.S)
_PAUSE_RE = re.compile(
    r"^\s*HUMAN\s*\[(?P<step>[A-Za-z0-9][A-Za-z0-9._\-]*)\]\s*:\s*(?P<msg>.*?)\s*$", re.S | re.I
)


def parse_phase_comment(message: Any) -> dict[str, str] | None:
    """Parse ``[<step_id>] <title> | <OWNER> | <note>``; None when it does not match.

    OWNER must be ROBOT, ROBOT-PARTIAL or HUMAN (case-insensitive; normalised to upper case).
    The title is required, the note is optional. Anything else is "just a caption".
    """
    if not isinstance(message, str):
        return None
    m = _PHASE_RE.match(message)
    if not m:
        return None
    parts = m.group("rest").split("|", 2)
    if len(parts) < 2:
        return None
    title = parts[0].strip()
    owner = parts[1].strip().upper()
    note = parts[2].strip() if len(parts) > 2 else ""
    if not title or owner not in OWNERS:
        return None
    return {"step_id": m.group("step"), "title": title, "owner": owner, "note": note}


def parse_human_pause(message: Any) -> dict[str, str] | None:
    """Parse ``HUMAN [<step_id>]: <instructions>``; None when it does not match."""
    if not isinstance(message, str):
        return None
    m = _PAUSE_RE.match(message)
    if not m:
        return None
    return {"step_id": m.group("step"), "message": m.group("msg")}


# --------------------------------------------------------------------------------------
# Deck
# --------------------------------------------------------------------------------------
def _module_type(name: str, model: str) -> str:
    s = f"{name} {model}".lower()
    if "temperature" in s:
        return "temperature"
    if "magnetic" in s:
        return "magnetic"
    if "thermocycler" in s:
        return "thermocycler"
    if "heater" in s:
        return "heater_shaker"
    return "other"


def _pretty_name(display: str, load: str) -> str:
    s = display or load or "labware"
    return s if " " in s else s.replace("_", " ")


def _build_deck(sim_deck: Any, warnings: list[str]) -> dict[str, Any]:
    sd = sim_deck if isinstance(sim_deck, dict) else {}
    fw, fh = SLOT_FOOTPRINT_MM

    trash_slot = "12"
    tr = sd.get("trash")
    if isinstance(tr, dict) and _slot(tr.get("slot")):
        trash_slot = _slot(tr.get("slot")) or "12"

    slots = [
        {"slot": s, "x": ox, "y": oy, "w": fw, "h": fh, "kind": "trash" if s == trash_slot else "slot"}
        for s, (ox, oy) in SLOT_ORIGINS_MM.items()
    ]

    labware: list[dict[str, Any]] = []
    skipped_wells = 0
    for raw in _as_list(sd.get("labware")):
        if not isinstance(raw, dict):
            continue
        slot = _slot(raw.get("slot"))
        wells: dict[str, Any] = {}
        raw_wells = raw.get("wells") if isinstance(raw.get("wells"), dict) else {}
        for name, w in raw_wells.items():
            if not isinstance(w, dict):
                skipped_wells += 1
                continue
            x, y = _num(w.get("x")), _num(w.get("y"))
            if x is None or y is None:
                skipped_wells += 1
                continue
            circle = _str(w.get("shape")).lower().startswith("circ")
            if circle:
                d = _num(w.get("diameter"))
                wd = ht = d if d and d > 0 else 5.0
            else:
                ln, wi = _num(w.get("length")), _num(w.get("width"))
                wd = ln if ln and ln > 0 else 8.0
                ht = wi if wi and wi > 0 else 8.0
            wells[str(name)] = {
                "x": _r(x, 2), "y": _r(y, 2), "shape": "circle" if circle else "rect",
                "w": _r(wd, 2), "h": _r(ht, 2), "max_ul": _r(_num(w.get("max_volume_ul")), 2),
            }
        load_name = _str(raw.get("load_name"))
        display = _str(raw.get("display_name")) or load_name
        kind = _str(raw.get("kind"))
        origin = SLOT_ORIGINS_MM.get(slot or "")
        labware.append({
            "slot": slot,
            "load_name": load_name,
            "display_name": display,
            "label": _pretty_name(display, load_name),
            "kind": kind if kind in _LABWARE_KINDS else "other",
            "module": _str(raw.get("module")) or None,
            "rect": [origin[0], origin[1], fw, fh] if origin else None,
            "wells": wells,
        })
    if skipped_wells:
        warnings.append(f"{skipped_wells} well(s) in the deck listing had no usable coordinates and are not drawn.")

    modules = []
    for m in _as_list(sd.get("modules")):
        if not isinstance(m, dict):
            continue
        name, model = _str(m.get("name")), _str(m.get("model"))
        modules.append({"slot": _slot(m.get("slot")), "name": name or model or "module",
                        "model": model, "type": _module_type(name, model)})

    pipettes = []
    for p in _as_list(sd.get("pipettes")):
        if not isinstance(p, dict):
            continue
        ch = max(1, min(_int(p.get("channels"), 1) or 1, MAX_CHANNELS))
        pipettes.append(_pipette_entry(_mount(p.get("mount")) or "unknown", _str(p.get("name")) or None, ch,
                                       _num(p.get("min_volume_ul")), _num(p.get("max_volume_ul"))))

    t_origin = SLOT_ORIGINS_MM.get(trash_slot)
    trash = {
        "slot": trash_slot,
        "rect": [t_origin[0], t_origin[1], fw, fh] if t_origin else None,
        "point": [_r(t_origin[0] + fw / 2, 2), _r(t_origin[1] + fh / 2, 2)] if t_origin else None,
    }
    return {
        "units": "mm",
        "frame": "origin = front-left corner of slot 1; +x to the right; +y towards the back of the robot",
        "extent": {"w": _r(DECK_EXTENT_MM[0], 2), "h": _r(DECK_EXTENT_MM[1], 2)},
        "footprint": [fw, fh],
        "home": list(TIME_MODEL["home_xy_mm"]),
        "channel_pitch_mm": CHANNEL_PITCH_MM,
        "slots": slots,
        "labware": labware,
        "modules": modules,
        "pipettes": pipettes,
        "trash": trash,
    }


def _pipette_entry(mount: str, name: str | None, channels: int, vmin: float | None, vmax: float | None) -> dict:
    return {
        "mount": mount, "name": name, "channels": channels,
        "min_volume_ul": _r(vmin, 2), "max_volume_ul": _r(vmax, 2),
        "channel_offsets_y": [_r(o, 2) for o in _nozzle_offsets(channels)],
    }


class _DeckIndex:
    """Lookups over the deck dict used while resolving event targets."""

    def __init__(self, deck: dict[str, Any]):
        self.lw: dict[str, dict[str, Any]] = {}
        self.single_row: dict[str, bool] = {}
        for lw in deck["labware"]:
            slot = lw.get("slot")
            if not slot or slot in self.lw:
                continue
            self.lw[slot] = lw
            self.single_row[slot] = len({round(w["y"], 1) for w in lw["wells"].values()}) == 1
        self.trash_slot: str = deck["trash"]["slot"]
        tp = deck["trash"].get("point")
        self.trash_xy: tuple[float, float] = (tp[0], tp[1]) if tp else tuple(TIME_MODEL["home_xy_mm"])  # type: ignore[assignment]


def _well_at(lw: dict[str, Any], x: float, y: float) -> str | None:
    """Name of the well whose opening contains (x, y), nearest first; None if over no well."""
    best: str | None = None
    best_d: float | None = None
    for name, g in lw["wells"].items():
        dx, dy = abs(g["x"] - x), abs(g["y"] - y)
        if g["shape"] == "circle":
            d = math.hypot(dx, dy)
            if d > g["w"] / 2 + _HIT_TOL_MM:
                continue
        else:
            if dx > g["w"] / 2 + _HIT_TOL_MM or dy > g["h"] / 2 + _HIT_TOL_MM:
                continue
            d = math.hypot(dx, dy)
        if best_d is None or d < best_d:
            best, best_d = name, d
    return best


def _resolve(idx: _DeckIndex, target: Any, channels: int) -> dict[str, Any] | None:
    """Resolve an event target to the wells under the nozzles and the head-centre position.

    A 1-channel pipette is over exactly the named well. An 8-channel pipette has 8 nozzles 9 mm
    apart along y. Over a labware with several rows (96-well plate, tip rack) the rear nozzle is
    over the named well (row A) and the others over the next rows towards the front; over a
    single-row labware (reservoir) the head is centred on the well, so all 8 nozzles share it.
    """
    if not isinstance(target, dict):
        return None
    slot = _slot(target.get("slot"))
    well = _str(target.get("well")) or None
    pt = target.get("point_mm")
    px = py = None
    if isinstance(pt, (list, tuple)) and len(pt) >= 2:
        px, py = _num(pt[0]), _num(pt[1])
    channels = max(1, min(int(channels or 1), MAX_CHANNELS))
    offs = _nozzle_offsets(channels)

    lw = idx.lw.get(slot) if slot else None
    # The fixed trash is not in the labware listing; older Opentrons APIs report it as "A1" of slot 12.
    if _str(target.get("labware")) == "trash" or (lw is None and slot is not None and slot == idx.trash_slot):
        return {"kind": "trash", "slot": slot or idx.trash_slot, "well": None, "labware": None,
                "wells": [], "head": idx.trash_xy}

    if lw is not None and well and well in lw["wells"]:
        g = lw["wells"][well]
        if channels == 1:
            return {"kind": "well", "slot": slot, "well": well, "labware": lw,
                    "wells": [(well, 1)], "head": (g["x"], g["y"])}
        cy = g["y"] if idx.single_row.get(slot or "") else g["y"] - offs[0]
        counts: dict[str, int] = {}
        for off in offs:
            name = _well_at(lw, g["x"], cy + off)
            if name:
                counts[name] = counts.get(name, 0) + 1
        return {"kind": "well", "slot": slot, "well": well, "labware": lw,
                "wells": list(counts.items()), "head": (g["x"], cy)}

    if px is not None and py is not None:
        return {"kind": "point", "slot": slot, "well": well, "labware": lw, "wells": [],
                "head": (px, py - offs[0] if channels > 1 else py)}
    if lw is not None and lw.get("rect"):
        rx, ry, rw, rh = lw["rect"]
        return {"kind": "labware", "slot": slot, "well": well, "labware": lw, "wells": [],
                "head": (rx + rw / 2, ry + rh / 2)}
    if slot in SLOT_ORIGINS_MM:
        ox, oy = SLOT_ORIGINS_MM[slot]
        return {"kind": "slot", "slot": slot, "well": well, "labware": None, "wells": [],
                "head": (ox + SLOT_FOOTPRINT_MM[0] / 2, oy + SLOT_FOOTPRINT_MM[1] / 2)}
    return None


def _span(ref: dict[str, Any]) -> str | None:
    names = [n for n, _c in ref["wells"]]
    if len(names) > 1:
        return f"{names[0]}-{names[-1]}"
    return ref.get("well") or (names[0] if names else None)


def _loc(ref: dict[str, Any] | None) -> str:
    """Short human-readable place, e.g. 'A1 of reservoir (slot 5)' or 'A1-H1 of plate (slot 6)'."""
    if ref is None:
        return "an unreported location"
    if ref["kind"] == "trash":
        return f"trash (slot {ref['slot']})"
    lw = ref.get("labware")
    word = _LABWARE_WORD.get(lw["kind"], "labware") if lw else "labware"
    well = _span(ref)
    slot = ref.get("slot")
    if well and slot:
        return f"{well} of {word} (slot {slot})"
    if slot:
        return f"{word} (slot {slot})" if lw else f"slot {slot}"
    return well or "an unreported location"


def _multi_loc(refs: list[dict[str, Any]]) -> str:
    """'A2, A3 of plate (slot 3)' for several destinations; falls back to _loc for one or none."""
    if not refs:
        return "an unreported location"
    if len(refs) == 1:
        return _loc(refs[0])
    names = ", ".join(_span(r) or "?" for r in refs[:6]) + (" ..." if len(refs) > 6 else "")
    first = refs[0]
    if not first.get("slot"):
        return names
    lw = first.get("labware")
    word = _LABWARE_WORD.get(lw["kind"], "labware") if lw else "labware"
    return f"{names} of {word} (slot {first['slot']})"


def _key(slot: str | None, well: str) -> str:
    return f"{slot}:{well}"


# --------------------------------------------------------------------------------------
# Frame builder
# --------------------------------------------------------------------------------------
class _Builder:
    def __init__(self, deck: dict[str, Any], warnings: list[str]):
        self.deck = deck
        self.idx = _DeckIndex(deck)
        self.warnings = warnings
        self.tm = TIME_MODEL
        self.t = 0.0
        self.head: tuple[float, float] = (float(self.tm["home_xy_mm"][0]), float(self.tm["home_xy_mm"][1]))
        self.head_mount: str | None = None
        self.net: dict[str, float] = {}
        self.max_abs_net = 0.0
        self.consumed: set[str] = set()
        self.consumed_by_rack: dict[str, list[str]] = {}
        self.tips_used = 0
        self.pickups = 0
        self.aspirations = 0
        self.dispenses = 0
        self.vol_asp = 0.0
        self.vol_disp = 0.0
        self.gantry_mm = 0.0
        self.unresolved = 0
        self.time_by_kind: dict[str, float] = {}
        self.human_frames: list[int] = []
        self.mods: dict[str, dict[str, Any]] = {}
        for m in deck["modules"]:
            if not m.get("slot"):
                continue
            st: dict[str, Any] = {"type": m["type"]}
            if m["type"] == "temperature":
                st["setpoint_c"] = None
            elif m["type"] == "magnetic":
                st["engaged"] = False
            self.mods[m["slot"]] = st
        self.pips: dict[str, dict[str, Any]] = {}
        for p in deck["pipettes"]:
            self.pips[p["mount"]] = {"mount": p["mount"], "name": p["name"], "channels": p["channels"],
                                     "tip": False, "held_ul": 0.0, "last_keys": [], "last_loc": None}
        self.initial_state = {
            "modules": copy.deepcopy(self.mods),
            "pipettes": self._pip_snapshot(),
            "head": [_r(self.head[0], 2), _r(self.head[1], 2)],
        }
        self.frames: list[dict[str, Any]] = []
        self.phases: list[dict[str, Any]] = []
        self._phase: int | None = None
        self._stack: list[tuple[int, int]] = []

    # ---- state helpers -------------------------------------------------------------
    def _pip_snapshot(self) -> dict[str, dict[str, Any]]:
        return {m: {"tip": bool(p["tip"]), "held_ul": _r(p["held_ul"], 3)} for m, p in self.pips.items()}

    def _pipette(self, mount: str | None, raw: dict[str, Any] | None) -> dict[str, Any]:
        mount = mount or "unknown"
        st = self.pips.get(mount)
        if st is None:
            ch = max(1, min(_int((raw or {}).get("channels"), 1) or 1, MAX_CHANNELS))
            name = _str((raw or {}).get("name")) or None
            st = {"mount": mount, "name": name, "channels": ch, "tip": False, "held_ul": 0.0,
                  "last_keys": [], "last_loc": None}
            self.pips[mount] = st
            self.deck["pipettes"].append(_pipette_entry(mount, name, ch, None, None))
        return st

    def _open_phase(self, info: dict[str, str], frame_idx: int) -> int:
        if self.phases:
            self.phases[-1]["last_frame"] = frame_idx - 1
        self.phases.append({
            "index": len(self.phases), "step_id": info["step_id"], "title": info["title"],
            "owner": info["owner"], "note": info["note"], "first_frame": frame_idx,
            "last_frame": frame_idx, "start_s": None, "end_s": None,
        })
        self._phase = len(self.phases) - 1
        return self._phase

    def _bump_net(self, key: str, delta: float) -> float:
        v = self.net.get(key, 0.0) + delta
        self.net[key] = v
        self.max_abs_net = max(self.max_abs_net, abs(v))
        return v

    # ---- one event -> one frame ----------------------------------------------------
    def add_event(self, i: int, ev: Any) -> None:
        if not isinstance(ev, dict):
            self.warnings.append(f"event #{i} is not an object; kept as a caption.")
            ev = {"kind": "other", "text": _str(ev)}
        raw_kind = ev.get("kind")
        kind = raw_kind if isinstance(raw_kind, str) and raw_kind in _KNOWN_KINDS else "other"
        level = max(0, _int(ev.get("level"), 0) or 0)
        text = _str(ev.get("text")).strip()
        fidx = len(self.frames)
        is_group = kind in _GROUP_KINDS or (kind == "other" and bool(ev.get("is_group")))

        while self._stack and self._stack[-1][0] >= level:
            self._stack.pop()
        parent = self._stack[-1][1] if self._stack else None
        if is_group:
            self._stack.append((level, fidx))

        pip_raw = ev.get("pipette") if isinstance(ev.get("pipette"), dict) else None
        pst: dict[str, Any] | None = None
        if pip_raw is not None:
            pst = self._pipette(_mount(pip_raw.get("mount")), pip_raw)
        elif kind in _PIPETTE_KINDS:
            pst = self._pipette(self.head_mount, None)
        channels = pst["channels"] if pst else 1
        if pip_raw is not None and pst is not None:
            ch_ev = _int(pip_raw.get("channels"))
            if ch_ev and ch_ev != pst["channels"]:
                channels = max(1, min(ch_ev, MAX_CHANNELS))

        caption = ""
        detail = ""
        target_head: tuple[float, float] | None = None
        action_s = 0.0
        human = False
        human_step: dict[str, Any] | None = None
        focus: list[dict[str, str]] = []
        focus_preview = False
        touched: dict[str, float] = {}
        new_tips: list[str] = []
        vol: float | None = None
        vol_total: float | None = None
        group_vol: float | None = None
        new_phase: dict[str, str] | None = None
        tm = self.tm

        if kind == "pick_up_tip":
            ref = _resolve(self.idx, ev.get("target"), channels)
            if ref:
                target_head = ref["head"]
                for name, _c in ref["wells"]:
                    key = _key(ref["slot"], name)
                    if key not in self.consumed:
                        self.consumed.add(key)
                        new_tips.append(key)
                        self.consumed_by_rack.setdefault(ref["slot"] or "?", []).append(name)
                    focus.append({"key": key, "role": "tip"})
            self.tips_used += channels
            self.pickups += 1
            action_s = tm["pick_up_tip_s"]
            pst["tip"] = True  # type: ignore[index]
            pst["held_ul"] = 0.0  # type: ignore[index]
            if ref and ref["wells"]:
                span = _span(ref)
                where = f"from {_LABWARE_WORD.get(ref['labware']['kind'], 'labware')} (slot {ref['slot']})"
                caption = (f"Pick up {channels} tips ({span}) {where}" if len(ref["wells"]) > 1
                           else f"Pick up tip {span} {where}")
            else:
                caption = "Pick up tip (location not reported)"
                self.warnings.append(f"event #{i}: pick-up location not found on the deck; no tip rack update.")
            detail = f"{channels} tip(s) consumed" if channels > 1 else ""

        elif kind == "drop_tip":
            ref = _resolve(self.idx, ev.get("target"), channels)
            if ref is None:
                ref = {"kind": "trash", "slot": self.idx.trash_slot, "well": None, "labware": None,
                       "wells": [], "head": self.idx.trash_xy}
            target_head = ref["head"]
            if ref["kind"] == "trash":
                focus.append({"key": f"trash:{ref['slot']}", "role": "trash"})
                caption = f"Drop tip into trash (slot {ref['slot']})"
            else:
                focus.extend({"key": _key(ref["slot"], n), "role": "tip"} for n, _c in ref["wells"])
                caption = f"Return tip to {_loc(ref)}"
            action_s = tm["drop_tip_s"]
            pst["tip"] = False  # type: ignore[index]
            pst["held_ul"] = 0.0  # type: ignore[index]

        elif kind in ("aspirate", "dispense"):
            asp = kind == "aspirate"
            ref = _resolve(self.idx, ev.get("target"), channels)
            v = _num(ev.get("volume_ul"))
            if v is None or v < 0:
                if v is not None:
                    self.warnings.append(f"event #{i}: negative volume ignored.")
                v = 0.0
                detail_vol = "volume not reported"
            else:
                detail_vol = ""
            rate = _num(ev.get("flow_rate_ul_s"))
            rate_note = ""
            if not rate or rate <= 0:
                rate, rate_note = float(tm["default_flow_rate_ul_s"]), " (default rate)"
            action_s = v / rate
            vol = _r(v, 3)
            vol_total = _r(v * channels, 3)
            sign = -1.0 if asp else 1.0
            if ref:
                target_head = ref["head"]
                for name, cnt in ref["wells"]:
                    key = _key(ref["slot"], name)
                    touched[key] = self._bump_net(key, sign * cnt * v)
                    focus.append({"key": key, "role": "source" if asp else "dest"})
                if ref["kind"] == "trash":
                    focus.append({"key": f"trash:{ref['slot']}", "role": "trash"})
                if not ref["wells"] and ref["kind"] != "trash":
                    self.unresolved += 1
            else:
                self.unresolved += 1
            if pst is not None:
                pst["held_ul"] = pst["held_ul"] + v if asp else max(0.0, pst["held_ul"] - v)
                pst["last_keys"] = [f["key"] for f in focus]
                pst["last_loc"] = _loc(ref)
            if asp:
                self.aspirations += 1
                self.vol_asp += v * channels
            else:
                self.dispenses += 1
                self.vol_disp += v * channels
            per = f"{_fmt(v)} {_MU}L" + (
                f" × {channels} channels ({_fmt(v * channels)} {_MU}L in total)" if channels > 1 else "")
            caption = (f"Aspirate {per} from {_loc(ref)}" if asp
                       else f"Dispense {per} into {_loc(ref)}")
            detail = f"{_fmt(rate)} {_MU}L/s{rate_note}, about {_fmt(action_s)} s"
            if detail_vol:
                detail = detail_vol

        elif kind == "blow_out":
            ref = _resolve(self.idx, ev.get("target"), channels)
            if ref:
                target_head = ref["head"]
                if ref["kind"] == "trash":
                    focus.append({"key": f"trash:{ref['slot']}", "role": "trash"})
                else:
                    focus.extend({"key": _key(ref["slot"], n), "role": "here"} for n, _c in ref["wells"])
            action_s = tm["blow_out_s"]
            pst["held_ul"] = 0.0  # type: ignore[index]
            if pst is not None and ref is not None:
                pst["last_keys"] = [f["key"] for f in focus]
                pst["last_loc"] = _loc(ref)
            caption = f"Blow out into {_loc(ref)}" if ref and ref["kind"] == "trash" else (
                f"Blow out at {_loc(ref)}" if ref else "Blow out")

        elif kind == "touch_tip":
            action_s = tm["touch_tip_s"]
            ref = _resolve(self.idx, ev.get("target"), channels)
            if ref:
                target_head = ref["head"]
                focus.extend({"key": _key(ref["slot"], n), "role": "here"} for n, _c in ref["wells"])
                where = _loc(ref)
            else:
                keys = pst["last_keys"] if pst else []
                focus.extend({"key": k, "role": "here"} for k in keys if not k.startswith("trash:"))
                where = pst["last_loc"] if pst else None
            caption = f"Touch tip on {where}" if where else "Touch tip"

        elif is_group:
            focus_preview = True
            group_vol = _r(_num(ev.get("volume_ul")), 3)
            reps = _int(ev.get("repetitions"))
            v = _num(ev.get("volume_ul"))
            vtxt = f"{_fmt(v)} {_MU}L" if v is not None else "an unreported volume"
            ch_txt = f" ({channels}-channel)" if channels > 1 else ""
            if kind == "mix":
                ref = _resolve(self.idx, ev.get("target"), channels)
                if ref:
                    focus.extend({"key": _key(ref["slot"], n), "role": "here"} for n, _c in ref["wells"])
                caption = (f"Mix {reps}x with {vtxt} at {_loc(ref)}" if reps
                           else f"Mix with {vtxt} at {_loc(ref)}") + ch_txt
            else:
                src = _resolve(self.idx, ev.get("source"), channels)
                dst_raw = ev.get("dest")
                dsts = [_resolve(self.idx, d, channels) for d in (dst_raw if isinstance(dst_raw, list) else [dst_raw])]
                dsts = [d for d in dsts if d]
                if src:
                    focus.extend({"key": _key(src["slot"], n), "role": "source"} for n, _c in src["wells"])
                for d in dsts:
                    focus.extend({"key": _key(d["slot"], n), "role": "dest"} for n, _c in d["wells"])
                verb = {"transfer": "Transfer", "distribute": "Distribute", "consolidate": "Consolidate"}.get(kind, "Group")
                caption = f"{verb} {vtxt}: {_loc(src)} → {_multi_loc(dsts)}{ch_txt}"
            detail = "group header - the steps below do the work; no time or volume counted here"

        elif kind in ("temperature_set", "temperature_wait", "temperature_deactivate"):
            slots = [s for s, m in self.mods.items() if m["type"] == "temperature"]
            c = _num(ev.get("celsius"))
            if kind == "temperature_set":
                action_s = tm["temperature_set_s"]
                for s in slots:
                    if c is not None:
                        self.mods[s]["setpoint_c"] = _r(c, 2)
                caption = (f"Temperature module: set-point {_fmt(c)} {_DEG}C" if c is not None
                           else "Temperature module: set-point (value not reported)")
                detail = "set-point only; the time to reach it is a placeholder"
            elif kind == "temperature_wait":
                action_s = tm["temperature_wait_s"]
                caption = "Temperature module: wait for the set-point"
                detail = "the wait time is a placeholder"
            else:
                action_s = tm["temperature_deactivate_s"]
                for s in slots:
                    self.mods[s]["setpoint_c"] = None
                caption = "Temperature module: deactivate"
            if not slots:
                detail = "no temperature module in the deck listing; module state not shown"
            elif len(slots) > 1:
                detail = f"the log does not say which module; shown on all {len(slots)}"

        elif kind in ("magnet_engage", "magnet_disengage"):
            slots = [s for s, m in self.mods.items() if m["type"] == "magnetic"]
            action_s = tm["magnet_move_s"]
            for s in slots:
                self.mods[s]["engaged"] = kind == "magnet_engage"
            caption = "Magnetic module: engage" if kind == "magnet_engage" else "Magnetic module: disengage"
            if not slots:
                detail = "no magnetic module in the deck listing; module state not shown"
            elif len(slots) > 1:
                detail = f"the log does not say which module; shown on all {len(slots)}"

        elif kind == "delay":
            secs = _num(ev.get("seconds"))
            m = re.match(r"^Delaying for ([0-9.]+) minutes and ([0-9.]+) seconds", text)
            if secs is None and m:
                secs = (_num(m.group(1)) or 0.0) * 60.0 + (_num(m.group(2)) or 0.0)
            secs = max(0.0, secs or 0.0)
            action_s = secs
            msg = ""
            m2 = re.match(r"^Delaying for [0-9.]+ minutes and [0-9.]+ seconds\.\s*(.*)$", text, re.S)
            if m2:
                msg = m2.group(1).strip()
            caption = f"Delay {_fmt_dur(secs)}" + (f": {msg}" if msg else "")
            detail = "exact duration from the protocol"

        elif kind == "pause":
            human = True
            msg = _str(ev.get("message")).strip()
            if not msg:
                msg = text.split(":", 1)[-1].strip() if text else ""
            hs = parse_human_pause(msg)
            human_step = {"step_id": hs["step_id"] if hs else None, "message": hs["message"] if hs else msg}
            caption = "HUMAN STEP - robot paused" + (f": {human_step['message']}" if human_step["message"] else "")
            detail = "a person must act and resume; no duration is estimated"
            if hs and (self._phase is None or self.phases[self._phase]["step_id"] != hs["step_id"]):
                new_phase = {"step_id": hs["step_id"], "title": "Human step", "owner": "HUMAN",
                             "note": hs["message"]}

        elif kind == "home":
            target_head = (float(tm["home_xy_mm"][0]), float(tm["home_xy_mm"][1]))
            action_s = tm["home_s"]
            caption = "Home the gantry"

        elif kind == "comment":
            msg = _str(ev.get("message")).strip() or text
            info = parse_phase_comment(msg)
            if info:
                new_phase = info
                caption = f"Phase {info['step_id']}: {info['title']}"
                detail = info["owner"] + (f" - {info['note']}" if info["note"] else "")
            else:
                caption = msg or "(empty comment)"
            action_s = tm["comment_s"]

        else:  # "other" / unrecognised: keep it as a caption, never crash
            caption = text or (f"Unrecognised command ({_str(raw_kind)})" if raw_kind else "Unrecognised command")
            action_s = tm["unknown_command_s"]

        if new_phase is not None:
            self._open_phase(new_phase, fidx)

        # ---- movement and time ---------------------------------------------------------
        before = self.head
        move_mm = 0.0
        move_s = 0.0
        if target_head is not None:
            move_mm = math.hypot(target_head[0] - before[0], target_head[1] - before[1])
            move_s = move_mm / tm["gantry_speed_mm_s"]
            self.head = (float(target_head[0]), float(target_head[1]))
            self.gantry_mm += move_mm
        if pst is not None:
            self.head_mount = pst["mount"]

        start = self.t
        if human:
            duration: float | None = None
            end = start
            self.human_frames.append(fidx)
        else:
            duration = move_s + action_s
            end = start + duration
            self.t = end
            self.time_by_kind[kind] = self.time_by_kind.get(kind, 0.0) + duration

        frame = {
            "index": fidx,
            "event_idx": _int(ev.get("idx"), i),
            "level": level,
            "parent": parent,
            "kind": kind,
            "raw_kind": _str(raw_kind) if kind == "other" and raw_kind not in (None, "other") else None,
            "is_group": is_group,
            "caption": caption,
            "detail": detail,
            "text": text,
            "start_s": _r(start),
            "end_s": _r(end),
            "duration_s": None if human else _r(duration),
            "move_s": _r(move_s),
            "action_s": _r(action_s),
            "human": human,
            "human_step": human_step,
            "pipette": ({"mount": pst["mount"], "name": pst["name"], "channels": channels} if pst and (pip_raw or kind in _PIPETTE_KINDS) else None),
            "head_mount": self.head_mount,
            "head_before": [_r(before[0], 2), _r(before[1], 2)],
            "head_after": [_r(self.head[0], 2), _r(self.head[1], 2)],
            "move_mm": _r(move_mm, 2),
            "volume_ul": vol,
            "volume_total_ul": vol_total,
            "group_volume_ul": group_vol,
            "focus": focus,
            "focus_preview": focus_preview,
            "well_net_ul": {k: _r(v, 4) for k, v in touched.items()},
            "tips_consumed": new_tips,
            "tips_used_total": self.tips_used,
            "modules": copy.deepcopy(self.mods),
            "pipettes": self._pip_snapshot(),
            "phase": self._phase,
        }
        self.frames.append(frame)

    def finish(self) -> None:
        n = len(self.frames)
        if self.phases:
            self.phases[-1]["last_frame"] = n - 1
        for ph in self.phases:
            a = min(max(ph["first_frame"], 0), n - 1)
            b = min(max(ph["last_frame"], a), n - 1)
            ph["start_s"] = self.frames[a]["start_s"]
            ph["end_s"] = self.frames[b]["end_s"]
        if self.unresolved:
            self.warnings.append(
                f"{self.unresolved} aspirate/dispense step(s) referenced a location that is not a well on the "
                "deck listing; their per-well net change is not shown (they are still counted in the totals)."
            )

    def totals(self) -> dict[str, Any]:
        final = {k: _r(v, 4) for k, v in self.net.items() if abs(v) > 1e-9}
        return {
            "frames": len(self.frames),
            "estimated_total_s": _r(self.t),
            "human_pauses": len(self.human_frames),
            "human_frames": list(self.human_frames),
            "tip_pickups": self.pickups,
            "tips_used": self.tips_used,
            "tips_consumed_by_rack": {k: list(v) for k, v in self.consumed_by_rack.items()},
            "aspirations": self.aspirations,
            "dispenses": self.dispenses,
            "volume_aspirated_ul": _r(self.vol_asp, 3),
            "volume_dispensed_ul": _r(self.vol_disp, 3),
            "volume_moved_ul": _r(self.vol_disp, 3),
            "gantry_distance_mm": _r(self.gantry_mm, 1),
            "max_abs_net_ul": _r(self.max_abs_net, 4),
            "final_net_ul": final,
            "time_by_kind_s": {k: _r(v) for k, v in sorted(self.time_by_kind.items())},
            "notes": [
                "volume_aspirated_ul / volume_dispensed_ul sum channels x per-channel volume over the primitive "
                "steps, mixing strokes included; group headers are not counted.",
                "volume_moved_ul equals volume_dispensed_ul (liquid delivered).",
                "estimated_total_s excludes the time people take for human steps.",
            ],
        }


# --------------------------------------------------------------------------------------
# Public: build_timeline
# --------------------------------------------------------------------------------------
def _time_model() -> dict[str, Any]:
    return {
        "estimate_only": True,
        "headline": "Estimated time - not measured on a robot",
        "constants": copy.deepcopy(TIME_MODEL),
        "rules": list(_TIME_RULES),
    }


def _empty_totals() -> dict[str, Any]:
    b = _Builder(_build_deck({}, []), [])
    return b.totals()


def build_timeline(sim: dict) -> dict:
    """Turn a "vaxforge.ot_sim/1" document into a "vaxforge.timeline/1" document. Never raises."""
    warnings: list[str] = []
    if not isinstance(sim, dict):
        return _failed_timeline(
            {"type": "InvalidInput", "message": "the simulation document is not a JSON object"}, {}, warnings)

    sim_schema = _str(sim.get("schema"))
    if sim_schema and sim_schema != SIM_SCHEMA:
        warnings.append(f"unexpected simulation schema '{sim_schema}' (expected '{SIM_SCHEMA}'); read best-effort.")

    ok_raw = sim.get("ok", True)
    if isinstance(ok_raw, str):
        ok_raw = ok_raw.strip().lower() not in ("false", "0", "no", "")
    if not bool(ok_raw):
        err = sim.get("error")
        error: dict[str, Any] = dict(err) if isinstance(err, dict) else {
            "type": "SimulationFailed",
            "message": _str(err) or "The simulator reported failure without details.",
        }
        error.setdefault("type", "SimulationFailed")
        error["message"] = _str(error.get("message")) or "The simulator reported failure without details."
        return _failed_timeline(error, sim, warnings)

    try:
        deck = _build_deck(sim.get("deck"), warnings)
        builder = _Builder(deck, warnings)
        for i, ev in enumerate(_as_list(sim.get("events"))):
            builder.add_event(i, ev)
        builder.finish()
    except Exception as exc:  # defensive: a timeline must never take the pipeline down
        return _failed_timeline(
            {"type": type(exc).__name__, "message": f"the animation builder could not process this simulation: {exc}"},
            sim, warnings)

    if not builder.frames:
        warnings.append("The simulator logged no commands, so there is nothing to animate.")
    return {
        "schema": TIMELINE_SCHEMA,
        "ok": True,
        "error": None,
        "source": _source(sim),
        "time_model": _time_model(),
        "deck": deck,
        "initial_state": builder.initial_state,
        "frames": builder.frames,
        "phases": builder.phases,
        "totals": builder.totals(),
        "warnings": warnings,
    }


def _source(sim: dict) -> dict[str, Any]:
    return {
        "sim_schema": _str(sim.get("schema")) or None,
        "protocol_file": _basename(sim.get("protocol_file")) or None,
        "opentrons_version": _str(sim.get("opentrons_version")) or None,
        "api_level": _str(sim.get("api_level")) or None,
    }


def _failed_timeline(error: dict[str, Any], sim: dict, warnings: list[str]) -> dict:
    events = _as_list(sim.get("events")) if isinstance(sim, dict) else []
    last = []
    for ev in events[-8:]:
        if isinstance(ev, dict):
            t = _str(ev.get("text")).strip()
            if t:
                last.append(t)
    error = dict(error)
    error["events_logged"] = len(events)
    error["last_commands"] = last
    tb = error.get("traceback_tail")
    if isinstance(tb, list):
        error["traceback_tail"] = [_str(x) for x in tb]
    deck = _build_deck(None, [])
    return {
        "schema": TIMELINE_SCHEMA,
        "ok": False,
        "error": error,
        "source": _source(sim if isinstance(sim, dict) else {}),
        "time_model": _time_model(),
        "deck": deck,
        "initial_state": {"modules": {}, "pipettes": {}, "head": list(TIME_MODEL["home_xy_mm"])},
        "frames": [],
        "phases": [],
        "totals": _empty_totals(),
        "warnings": warnings,
    }


def net_volumes_after(timeline: dict, frame_index: int) -> dict[str, float]:
    """Full cumulative per-well net change (uL) after frame ``frame_index`` (frames hold sparse deltas)."""
    out: dict[str, float] = {}
    for f in _as_list(timeline.get("frames"))[: max(0, frame_index + 1)]:
        out.update(f.get("well_net_ul") or {})
    return out


# --------------------------------------------------------------------------------------
# HTML player (inline CSS + JS; ASCII only; no network resources)
# --------------------------------------------------------------------------------------
_CSS = r"""
:root{
  color-scheme: light dark;
  --c-bg:#f5f6f8; --c-surface:#ffffff; --c-ink:#1b2430; --c-muted:#546070; --c-line:#d2d8df;
  --c-deck:#e8ecf0; --c-slot:#b9c3ce; --c-lw:#ffffff; --c-lwline:#7a8696; --c-well:#6a7686;
  --c-tipfull:#66727f; --c-tipused:#aab3be; --c-src:#b04500; --c-dst:#005a9c;
  --c-gain:#0072b2; --c-loss:#c2540a; --c-ongain:#ffffff; --c-head:#1b2430; --c-trash:#d5dae0;
  --c-accent:#0b5cad; --c-on-accent:#ffffff; --c-hl:#e4effa; --c-warn:#7a4a00;
  --c-own-robot:#00695c; --c-own-part:#8a5a00; --c-own-human:#8e2c6b;
  --shadow:0 1px 2px rgba(20,30,45,.08);
}
@media (prefers-color-scheme: dark){
  :root{
    --c-bg:#12161b; --c-surface:#1b2129; --c-ink:#e7ecf2; --c-muted:#a3aebb; --c-line:#37414d;
    --c-deck:#0e1216; --c-slot:#3a4552; --c-lw:#222a34; --c-lwline:#7e8a99; --c-well:#9aa6b5;
    --c-tipfull:#9aa6b5; --c-tipused:#4a5460; --c-src:#ff9a52; --c-dst:#6db6ff;
    --c-gain:#56b4e9; --c-loss:#ff9a52; --c-ongain:#0b1118; --c-head:#e7ecf2; --c-trash:#2a323c;
    --c-accent:#6db6ff; --c-on-accent:#0b1118; --c-hl:#243447; --c-warn:#f0b84a;
    --c-own-robot:#4fd1b9; --c-own-part:#f0b84a; --c-own-human:#e79bc9;
    --shadow:none;
  }
}
*{box-sizing:border-box}
[hidden]{display:none !important}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--c-bg);color:var(--c-ink);
  font:15px/1.45 system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;overflow-wrap:anywhere}
.app{max-width:1320px;margin:0 auto;padding:12px 16px 36px}
h1{font-size:20px;margin:0;line-height:1.25}
h2{font-size:13px;margin:0 0 6px;letter-spacing:.06em;text-transform:uppercase;color:var(--c-muted)}
.top{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:8px 20px;margin-bottom:12px}
.sub{margin:2px 0 0;color:var(--c-muted);font-size:13px}
.clock{text-align:right}
.clock-t{font:600 22px/1.2 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-variant-numeric:tabular-nums}
.clock-t .of{font:400 13px system-ui,sans-serif;color:var(--c-muted)}
.clock-n{font-size:12px;font-weight:600;color:var(--c-warn)}
.fatal{border:2px solid var(--c-own-human);border-radius:8px;padding:10px 12px;background:var(--c-surface)}
.layout{display:grid;grid-template-columns:minmax(0,1fr) minmax(300px,410px);gap:16px;align-items:start}
.col-left,.col-right{min-width:0}
.col-right>section,.col-right>details,.col-right>.panel{margin-bottom:14px}
.below{margin-top:10px}
.banner{display:flex;flex-wrap:wrap;align-items:center;gap:6px 12px;min-height:56px;padding:8px 12px;
  border:1px solid var(--c-line);border-radius:10px;background:var(--c-surface);margin-bottom:10px;box-shadow:var(--shadow)}
.banner .bt{font-weight:600}
.banner .bn{color:var(--c-muted);font-size:13px;flex:1 1 220px}
.banner .muted{color:var(--c-muted);font-size:13px}
.badge{display:inline-flex;align-items:center;gap:6px;padding:2px 8px;border:2px solid currentColor;border-radius:6px;
  font-weight:700;font-size:12px;letter-spacing:.04em;white-space:nowrap;background:var(--c-surface)}
.badge .shape{display:inline-block;width:11px;height:11px;flex:none}
.own-ROBOT{color:var(--c-own-robot)}
.own-ROBOT .shape{border-radius:50%;background:currentColor}
.own-ROBOT-PARTIAL{color:var(--c-own-part);border-style:dashed}
.own-ROBOT-PARTIAL .shape{border-radius:50%;border:2px solid currentColor;background:linear-gradient(90deg,currentColor 50%,transparent 50%)}
.own-HUMAN{color:var(--c-own-human);border-style:double;border-width:4px;padding:0 6px}
.own-HUMAN .shape{background:currentColor;-webkit-clip-path:polygon(50% 0,100% 100%,0 100%);clip-path:polygon(50% 0,100% 100%,0 100%)}
.host{width:100%}
.box{position:relative;margin:0 auto}
#deck{display:block;border:1px solid var(--c-line);border-radius:10px;touch-action:manipulation}
.human{position:absolute;top:0;right:0;bottom:0;left:0;display:flex;align-items:center;justify-content:center;padding:12px;
  background:rgba(10,14,20,.45);border-radius:10px}
.human .card{max-width:440px;width:100%;background:var(--c-surface);color:var(--c-ink);border:4px double var(--c-own-human);
  border-radius:12px;padding:14px 16px;text-align:left}
.human .hd{display:flex;align-items:center;gap:8px;font-weight:800;letter-spacing:.03em;color:var(--c-own-human);margin-bottom:6px}
.human .hd .shape{display:inline-block;width:14px;height:14px;background:currentColor;-webkit-clip-path:polygon(50% 0,100% 100%,0 100%);clip-path:polygon(50% 0,100% 100%,0 100%)}
.human p{margin:0 0 10px}
.human .hs{font-size:12px;color:var(--c-muted)}
.capbox{margin:0;padding:10px 12px;border:1px solid var(--c-line);border-radius:10px;background:var(--c-surface);min-height:6.6em}
.caption{margin:0;font-weight:600;font-size:16px}
.meta{margin:4px 0 0;color:var(--c-muted);font-size:13px}
.controls{display:flex;flex-wrap:wrap;align-items:center;gap:8px;margin:10px 0 0}
button,select{font:inherit;color:var(--c-ink);background:var(--c-surface);border:1px solid var(--c-line);border-radius:8px;
  padding:8px 12px;min-height:40px}
button{cursor:pointer}
button:hover:not([disabled]){border-color:var(--c-accent)}
button[disabled]{opacity:.5;cursor:default}
button.primary{background:var(--c-accent);color:var(--c-on-accent);border-color:var(--c-accent);font-weight:700;min-width:84px}
:focus-visible{outline:3px solid var(--c-accent);outline-offset:2px}
.controls label{display:inline-flex;align-items:center;gap:6px;font-size:13px;color:var(--c-muted)}
.scrub{flex:1 1 100%;display:flex;align-items:center;gap:10px}
.scrub input{flex:1;min-width:0;height:28px;accent-color:var(--c-accent)}
.inspect{margin:0 0 8px;color:var(--c-muted);font-size:13px;min-height:1.4em}
.legend{list-style:none;display:flex;flex-wrap:wrap;gap:6px 16px;margin:0;padding:0;font-size:12.5px;color:var(--c-muted)}
.legend li{display:inline-flex;align-items:center;gap:6px}
.sw{display:inline-block;width:16px;height:16px;flex:none;text-align:center;line-height:14px;font:700 13px/14px system-ui,sans-serif}
.sw.tip{border-radius:50%;background:var(--c-tipfull)}
.sw.tipused{border-radius:50%;border:1.5px dashed var(--c-tipused)}
.sw.src{border:2.5px dashed var(--c-src);border-radius:50%}
.sw.dst{border:2.5px solid var(--c-dst);border-radius:50%}
.sw.gain{border-radius:50%;background:var(--c-gain);color:var(--c-ongain)}
.sw.loss{border-radius:50%;border:1.5px solid var(--c-loss);color:var(--c-loss);
  background:repeating-linear-gradient(45deg,var(--c-loss) 0 2px,transparent 2px 5px)}
.sw.head{border-radius:50%;background:var(--c-head)}
.sw.headh{border-radius:50%;border:2px solid var(--c-head)}
.stat{display:grid;grid-template-columns:auto 1fr;gap:2px 12px;margin:0 0 8px;font-size:13px}
.stat dt{color:var(--c-muted)} .stat dd{margin:0;font-variant-numeric:tabular-nums}
.chips{display:flex;flex-direction:column;gap:4px;margin:0 0 8px;padding:0;list-style:none;font-size:13px}
.chips li{border:1px solid var(--c-line);border-radius:8px;padding:4px 8px;background:var(--c-surface)}
.chips li.on{border-color:var(--c-accent);box-shadow:inset 3px 0 0 var(--c-accent)}
.steps{list-style:none;margin:0;padding:0;max-height:min(58vh,620px);overflow:auto;position:relative;
  border:1px solid var(--c-line);border-radius:10px;background:var(--c-surface)}
.steps li{margin:0;padding:0}
.steps .ph{display:flex;flex-wrap:wrap;gap:4px 8px;align-items:center;padding:6px 8px;background:var(--c-bg);
  border-bottom:1px solid var(--c-line);font-size:12.5px;font-weight:600}
.steps .stp{display:grid;grid-template-columns:2.4em 3.6em minmax(0,1fr) auto;gap:6px;align-items:baseline;width:100%;
  text-align:left;border:0;border-bottom:1px solid var(--c-line);border-radius:0;background:transparent;
  padding:5px 8px;min-height:32px;font-size:13px}
.steps .stp .n{color:var(--c-muted);font-variant-numeric:tabular-nums}
.steps .stp .k{font:700 10.5px/1.4 ui-monospace,Menlo,Consolas,monospace;color:var(--c-muted);letter-spacing:.03em}
.steps .stp .tm{color:var(--c-muted);font-variant-numeric:tabular-nums;font-size:12px}
.steps .stp.lv1 .tx{padding-left:12px}.steps .stp.lv2 .tx{padding-left:24px}.steps .stp.lv3 .tx{padding-left:36px}
.steps .stp.grp .tx{font-style:italic}
.steps .stp[aria-current="step"]{background:var(--c-hl);box-shadow:inset 4px 0 0 var(--c-accent);font-weight:700}
.steps .stp.hum .k{color:var(--c-own-human)}
details.tm summary{cursor:pointer;font-weight:600;padding:6px 0}
.tm table{width:100%;border-collapse:collapse;font-size:12.5px;margin:6px 0}
.tm td{padding:2px 6px;border-bottom:1px solid var(--c-line);vertical-align:top}
.tm td:last-child{text-align:right;font-variant-numeric:tabular-nums}
.tm ul{margin:6px 0 0;padding-left:18px;font-size:12.5px;color:var(--c-muted)}
.warn{margin:0 0 10px;padding:8px 12px;border:1px solid var(--c-warn);border-radius:8px;font-size:13px;color:var(--c-warn)}
.sr{position:absolute;width:1px;height:1px;margin:-1px;padding:0;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap;border:0}
.fail{max-width:760px;margin:24px auto;padding:18px 20px;border:2px solid var(--c-own-human);border-radius:12px;background:var(--c-surface)}
.fail h1{display:flex;align-items:center;gap:10px}
.fail pre{white-space:pre-wrap;overflow-wrap:anywhere;background:var(--c-bg);border:1px solid var(--c-line);border-radius:8px;padding:10px;font-size:13px}
.fail ul{padding-left:18px}
@media (max-width:900px){
  .layout{display:flex;flex-direction:column;gap:12px}
  .col-left,.col-right{display:contents}
  .banner{order:1}.host{order:2}.panel{order:3}.below{order:4}.run{order:5}.stepsec{order:6}.tm{order:7}
  .col-right>section,.col-right>details,.col-right>.panel{margin-bottom:0}
  .below{margin-top:0}
  .steps{max-height:50vh}
  .clock{text-align:left}
}
@media (max-width:520px){
  body{font-size:14px}
  .app{padding:8px 12px 28px}
  .caption{font-size:15px}
  button,select{padding:8px 10px}
  .controls{gap:6px}
}
@media (prefers-reduced-motion: reduce){
  *{animation:none !important;transition:none !important;scroll-behavior:auto !important}
}
"""

_PLAYER_BODY = """<div class="app">
<p id="fatal" class="fatal" role="alert" hidden></p>
<header class="top">
  <div>
    <h1>OT-2 protocol animation</h1>
    <p class="sub" id="sub"></p>
  </div>
  <div class="clock" role="group" aria-label="Estimated run time">
    <div class="clock-t"><span id="t-now">00:00</span> <span class="of">of</span> <span id="t-total">00:00</span></div>
    <div class="clock-n">Estimated time - not measured on a robot</div>
  </div>
</header>
<div id="warnings"></div>
<main class="layout">
  <div class="col-left">
    <div id="banner" class="banner" role="group" aria-label="Protocol phase"></div>
    <div class="host" id="host">
      <div class="box" id="box">
        <canvas id="deck" role="img" aria-label="Top-down view of the OT-2 deck. The caption describes the current step in words."></canvas>
        <div class="human" id="human" hidden>
          <div class="card" role="group" aria-label="Human step">
            <div class="hd"><i class="shape" aria-hidden="true"></i><span>HUMAN STEP - robot paused</span></div>
            <p id="human-msg"></p>
            <p class="hs" id="human-step"></p>
            <button type="button" class="primary" id="resume">Resume</button>
          </div>
        </div>
      </div>
    </div>
    <div class="below" id="below">
      <p class="inspect" id="inspect">Hover or tap a well to read its net change. Keys: Space = play/pause, Left/Right = step.</p>
      <ul class="legend" aria-label="Legend">
        <li><i class="sw tip" aria-hidden="true"></i>tip available</li>
        <li><i class="sw tipused" aria-hidden="true"></i>tip used (dashed)</li>
        <li><i class="sw src" aria-hidden="true"></i>source well (dashed ring)</li>
        <li><i class="sw dst" aria-hidden="true"></i>destination well (solid ring)</li>
        <li><i class="sw gain" aria-hidden="true">+</i>net gain</li>
        <li><i class="sw loss" aria-hidden="true">&minus;</i>net loss (hatched)</li>
        <li><i class="sw head" aria-hidden="true"></i>pipette with tip (filled)</li>
        <li><i class="sw headh" aria-hidden="true"></i>pipette, no tip (hollow)</li>
      </ul>
    </div>
  </div>
  <div class="col-right">
    <div class="panel" id="panel">
      <div class="capbox">
        <p class="caption" id="caption"></p>
        <p class="meta" id="meta"></p>
      </div>
      <div class="controls" role="group" aria-label="Playback controls">
        <button type="button" id="btn-restart">Restart</button>
        <button type="button" id="btn-back" aria-label="Step back">&larr; Back</button>
        <button type="button" id="btn-play" class="primary">Play</button>
        <button type="button" id="btn-next" aria-label="Step forward">Next &rarr;</button>
        <label>Speed
          <select id="speed" aria-label="Playback speed">
            <option value="0.5">0.5&times;</option><option value="1">1&times;</option><option value="2">2&times;</option>
            <option value="4" selected>4&times;</option><option value="8">8&times;</option><option value="16">16&times;</option>
            <option value="32">32&times;</option><option value="64">64&times;</option>
          </select>
        </label>
        <label><input type="checkbox" id="skipdelays" checked> Fast-forward waits</label>
        <div class="scrub"><input type="range" id="scrub" min="0" max="0" step="any" value="0" aria-label="Scrub through the run"><span id="steplab" class="meta"></span></div>
      </div>
    </div>
    <section class="run" aria-labelledby="h-run"><h2 id="h-run">Run state</h2>
      <dl class="stat" id="st-totals"></dl>
      <ul class="chips" id="st-pips" aria-label="Pipettes"></ul>
      <ul class="chips" id="st-mods" aria-label="Modules"></ul>
      <ul class="chips" id="st-racks" aria-label="Tip racks"></ul>
      <h2 id="h-nets">Largest net changes so far</h2>
      <ul class="chips" id="st-nets" aria-labelledby="h-nets"></ul>
    </section>
    <section class="stepsec" aria-labelledby="h-steps"><h2 id="h-steps">Steps</h2>
      <ol class="steps" id="steps"></ol>
    </section>
    <details class="tm"><summary>How the time is estimated</summary><div id="tm-body"></div></details>
  </div>
</main>
</div>
<div id="live" class="sr" aria-live="polite" aria-atomic="true"></div>
<noscript><p class="fatal">This player needs JavaScript. The timeline is embedded in this file as JSON.</p></noscript>
"""

_PLAYER_JS = r"""
(function () {
  'use strict';

  /* ------------------------------------------------------------------ data */
  var T = null;
  try {
    T = JSON.parse(document.getElementById('vf-timeline').textContent);
  } catch (err) {
    T = null;
  }
  var fatalEl = document.getElementById('fatal');
  function fatal(msg) {
    if (fatalEl) { fatalEl.hidden = false; fatalEl.textContent = msg; }
  }
  if (!T || typeof T !== 'object' || !T.deck || !Array.isArray(T.frames)) {
    fatal('The embedded timeline could not be read, so the player cannot start.');
    return;
  }
  var canvasEl = document.getElementById('deck');
  var ctx = canvasEl && canvasEl.getContext ? canvasEl.getContext('2d') : null;
  if (!ctx) {
    fatal('This browser cannot draw on a canvas. The step list and captions still work.');
  }

  var MINUS = '−', MU = 'µ', DEG = '°', TIMES = '×', ARROW = '→', ELL = '…', MID = '·';
  var DELAY_REAL_CAP_S = 2.0;  // with "fast-forward waits" on, a wait never takes longer than this in real time
  var CP_EVERY = 64;           // state checkpoint spacing (frames) for fast scrubbing
  var PAD_MM = 12;
  var FONT = 'system-ui, -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif';

  var D = T.deck;
  var FRAMES = T.frames;
  var N = FRAMES.length;
  var PHASES = T.phases || [];
  var TOT = T.totals || {};
  var EXT = D.extent || { w: 392.76, h: 356.98 };
  var HOME = (D.home && D.home.length === 2) ? D.home : [385, 321];
  var NETMAX = TOT.max_abs_net_ul || 1;

  /* ------------------------------------------------------------------ helpers */
  function $(id) { return document.getElementById(id); }
  function mk(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) { e.className = cls; }
    if (text !== undefined && text !== null) { e.textContent = text; }
    return e;
  }
  function clear(e) { while (e.firstChild) { e.removeChild(e.firstChild); } }
  function has(o, k) { return Object.prototype.hasOwnProperty.call(o, k); }
  function clamp(v, a, b) { return v < a ? a : (v > b ? b : v); }
  function lerp(a, b, t) { return a + (b - a) * t; }
  function ease(t) { return t < 0.5 ? 2 * t * t : 1 - Math.pow(-2 * t + 2, 2) / 2; }
  function fmtNum(v, dp) {
    if (typeof v !== 'number' || !isFinite(v)) { return '?'; }
    var p = Math.pow(10, dp === undefined ? 2 : dp);
    var r = Math.round(v * p) / p;
    if (r === 0) { r = 0; }
    return String(r);
  }
  function fmtSigned(v) {
    if (v > 0) { return '+' + fmtNum(v); }
    if (v < 0) { return MINUS + fmtNum(-v); }
    return '0';
  }
  function p2(x) { return (x < 10 ? '0' : '') + x; }
  function fmtClock(s) {
    var n = Math.max(0, Math.floor((s || 0) + 1e-6));
    var h = Math.floor(n / 3600), m = Math.floor((n % 3600) / 60), sec = n % 60;
    return (h > 0 ? h + ':' + p2(m) : p2(m)) + ':' + p2(sec);
  }

  var mqReduce = window.matchMedia ? window.matchMedia('(prefers-reduced-motion: reduce)') : null;
  var reduced = !!(mqReduce && mqReduce.matches);
  if (mqReduce && mqReduce.addEventListener) {
    mqReduce.addEventListener('change', function () { reduced = mqReduce.matches; markDirty(); });
  }

  /* ------------------------------------------------------------------ deck model */
  var PIP = {};
  (D.pipettes || []).forEach(function (p) { PIP[p.mount] = p; });
  var WELL = {}, WELL_LIST = [], LW_BY_SLOT = {}, SLOT_BY_ID = {};
  (D.slots || []).forEach(function (s) { SLOT_BY_ID[s.slot] = s; });
  (D.labware || []).forEach(function (lw) {
    if (lw.slot) { LW_BY_SLOT[lw.slot] = lw; }
    Object.keys(lw.wells || {}).forEach(function (name) {
      var w = lw.wells[name];
      var g = { key: lw.slot + ':' + name, slot: lw.slot, name: name, x: w.x, y: w.y, w: w.w, h: w.h,
                circle: w.shape === 'circle', max: w.max_ul, tip: lw.kind === 'tiprack', lw: lw };
      WELL[g.key] = g;
      WELL_LIST.push(g);
    });
  });
  var TRASH = D.trash || null;

  /* ------------------------------------------------------------------ run state (sparse deltas -> snapshots) */
  var INIT = T.initial_state || { modules: {}, pipettes: {} };
  function freshState() { return { net: {}, tips: {}, mods: INIT.modules || {}, pips: INIT.pipettes || {} }; }
  function cloneState(s) { return { net: Object.assign({}, s.net), tips: Object.assign({}, s.tips), mods: s.mods, pips: s.pips }; }
  function applyFrame(s, f) {
    var k, wn = f.well_net_ul, tc = f.tips_consumed;
    if (wn) { for (k in wn) { if (has(wn, k)) { s.net[k] = wn[k]; } } }
    if (tc) { for (var j = 0; j < tc.length; j++) { s.tips[tc[j]] = 1; } }
    if (f.modules) { s.mods = f.modules; }
    if (f.pipettes) { s.pips = f.pipettes; }
  }
  var CPS = [freshState()];
  (function buildCheckpoints() {
    var s = cloneState(CPS[0]);
    for (var i = 0; i < N; i++) {
      applyFrame(s, FRAMES[i]);
      if ((i + 1) % CP_EVERY === 0) { CPS.push(cloneState(s)); }
    }
  })();
  function stateBefore(i) {
    var c = Math.floor(i / CP_EVERY);
    var s = cloneState(CPS[c]);
    for (var j = c * CP_EVERY; j < i; j++) { applyFrame(s, FRAMES[j]); }
    return s;
  }

  /* ------------------------------------------------------------------ player state */
  var cur = 0, prog = 0, playing = false;
  var speed = 4, skipDelays = true;
  var base = stateBefore(0);
  var disp = null;
  var dirty = true, lastTs = null;
  var shownKey = '', shownStatus = '';

  function atEnd() { return N > 0 && cur === N - 1 && prog >= 1; }
  function isLongWait(f) { return f.kind === 'delay' || f.kind === 'temperature_set' || f.kind === 'temperature_wait'; }
  function markDirty() { dirty = true; }

  function setPos(i, p) {
    if (N === 0) { cur = 0; prog = 0; markDirty(); return; }
    i = clamp(Math.floor(i), 0, N - 1);
    p = clamp(p, 0, 1);
    if (FRAMES[i].human) { p = 0; }
    if (i === cur + 1) { applyFrame(base, FRAMES[cur]); }
    else if (i !== cur) { base = stateBefore(i); }
    cur = i;
    prog = p;
    markDirty();
  }
  function finishRun() {
    if (N === 0) { return; }
    if (cur !== N - 1) { base = stateBefore(N - 1); cur = N - 1; }
    prog = 1;
    playing = false;
    updateButtons();
    announce('End of run. ' + FRAMES[N - 1].caption, true);
    markDirty();
  }
  var focusResume = false;
  function enterHuman() {
    playing = false;
    prog = 0;
    focusResume = true;   // focus the Resume button once the overlay is visible (see updateText)
    updateButtons();
    markDirty();
    announce(FRAMES[cur].caption, true);
  }
  function resume() {
    var f = FRAMES[cur];
    if (!(f && f.human && prog < 1)) { return; }
    if (cur >= N - 1) { finishRun(); return; }
    applyFrame(base, f);
    cur += 1;
    prog = 0;
    playing = true;
    lastTs = null;
    updateButtons();
    markDirty();
    if (FRAMES[cur].human) { enterHuman(); }
  }
  function play() {
    if (N === 0) { return; }
    if (atEnd()) { setPos(0, 0); }
    var f = FRAMES[cur];
    if (f.human && prog < 1) { resume(); return; }
    playing = true;
    lastTs = null;
    updateButtons();
    markDirty();
  }
  function pause() {
    if (!playing) { return; }
    playing = false;
    updateButtons();
    markDirty();
    announce(N ? FRAMES[cur].caption : '', true);
  }
  function togglePlay() { if (playing) { pause(); } else { play(); } }
  function restart() { playing = false; setPos(0, 0); updateButtons(); announce('Restarted at the beginning.', true); }
  function stepBy(dir) {
    if (N === 0) { return; }
    playing = false;
    var f = FRAMES[cur];
    if (dir > 0) {
      if (atEnd()) { /* already at the end */ }
      else if (f.human && prog < 1) { if (cur >= N - 1) { finishRun(); } else { setPos(cur + 1, 1); } }
      else if (prog < 1) { setPos(cur, 1); }
      else if (cur < N - 1) { setPos(cur + 1, 1); }
    } else {
      if (cur === 0 && (prog === 0 || !f.human)) { setPos(0, 0); }
      else if (f.human && prog >= 1) { setPos(cur, 0); }
      else if (cur > 0) { setPos(cur - 1, 1); }
    }
    updateButtons();
    announce(FRAMES[cur].caption, true);
  }
  function setSpeed(v) { speed = v; markDirty(); }

  // Real seconds a zero-duration step (comment, group header, unknown command) stays on screen. The estimated
  // clock does not move during it. Comments get longer the more text they carry; everything shortens with speed.
  function dwellReal(f) {
    var b = 0.5;
    if (f.kind === 'comment' || f.kind === 'other') { b = clamp(0.8 + 0.03 * (f.caption || '').length, 0.8, 3.0); }
    return b / clamp(speed / 2, 1, 4);
  }

  function advance(dtReal) {
    var guard = 0;
    while (dtReal > 1e-9 && playing && guard++ < 20000) {
      var f = FRAMES[cur];
      if (f.human) { enterHuman(); return; }
      var dur = f.duration_s || 0;
      var remReal;
      if (dur > 0) {
        var rate = speed;
        if (skipDelays && isLongWait(f)) { rate = Math.max(rate, dur / DELAY_REAL_CAP_S); }
        remReal = (1 - prog) * dur / rate;
      } else {
        remReal = (1 - prog) * dwellReal(f);
      }
      if (remReal > dtReal) {
        prog += (dtReal / remReal) * (1 - prog);
        dtReal = 0;
      } else {
        dtReal -= remReal;
        if (cur >= N - 1) { prog = 1; finishRun(); return; }
        applyFrame(base, f);
        cur += 1;
        prog = 0;
        if (FRAMES[cur].human) { enterHuman(); return; }
      }
    }
  }

  /* ------------------------------------------------------------------ colours */
  var C = {};
  var COLOR_NAMES = ['bg', 'surface', 'ink', 'muted', 'line', 'deck', 'slot', 'lw', 'lwline', 'well', 'tipfull',
                     'tipused', 'src', 'dst', 'gain', 'loss', 'ongain', 'head', 'trash', 'accent'];
  var lossPattern = null;
  function readColors() {
    var cs = getComputedStyle(document.documentElement);
    COLOR_NAMES.forEach(function (n) { C[n] = (cs.getPropertyValue('--c-' + n) || '').trim() || '#888888'; });
    if (!ctx) { return; }
    var pc = document.createElement('canvas');
    pc.width = 6; pc.height = 6;
    var pg = pc.getContext('2d');
    pg.fillStyle = C.lw; pg.fillRect(0, 0, 6, 6);
    pg.strokeStyle = C.loss; pg.lineWidth = 1.6;
    pg.beginPath();
    pg.moveTo(-1, 7); pg.lineTo(7, -1); pg.moveTo(-1, 1); pg.lineTo(1, -1); pg.moveTo(5, 7); pg.lineTo(7, 5);
    pg.stroke();
    lossPattern = ctx.createPattern(pc, 'repeat');
  }
  if (window.matchMedia) {
    var mqDark = window.matchMedia('(prefers-color-scheme: dark)');
    if (mqDark.addEventListener) { mqDark.addEventListener('change', function () { readColors(); markDirty(); }); }
  }

  /* ------------------------------------------------------------------ canvas geometry */
  var host = $('host'), box = $('box'), panelEl = $('panel');
  var mqNarrow = window.matchMedia ? window.matchMedia('(max-width: 900px)') : null;
  var scale = 1, cssW = 0, cssH = 0, dpr = 1, lastHostW = 0;
  function S(mm) { return mm * scale; }
  function X(x) { return (x + PAD_MM) * scale; }
  function Y(y) { return (EXT.h - y + PAD_MM) * scale; }
  function layout() {
    if (!ctx) { return; }
    var hostW = host.clientWidth || 600;
    var worldW = EXT.w + 2 * PAD_MM, worldH = EXT.h + 2 * PAD_MM;
    // Keep the controls on screen: the deck may use the viewport height left under the banner (wide layout)
    // or left above the caption/controls panel (stacked layout).
    var narrow = !!(mqNarrow && mqNarrow.matches);
    var top = host.getBoundingClientRect().top + (window.pageYOffset || 0);
    var reserved = top + 14 + (narrow ? panelEl.offsetHeight + 12 : 0);
    var maxH = Math.max(300, (window.innerHeight || 800) - reserved);
    var w = Math.max(260, Math.min(hostW, maxH * worldW / worldH));
    scale = w / worldW;
    cssW = Math.round(w);
    cssH = Math.round(worldH * scale);
    dpr = Math.min(window.devicePixelRatio || 1, 3);
    box.style.width = cssW + 'px';
    canvasEl.style.width = cssW + 'px';
    canvasEl.style.height = cssH + 'px';
    canvasEl.width = Math.round(cssW * dpr);
    canvasEl.height = Math.round(cssH * dpr);
    lastHostW = hostW;
    markDirty();
  }

  /* ------------------------------------------------------------------ drawing helpers */
  var TWO_PI = Math.PI * 2;
  function rrPath(x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.lineTo(x + w - r, y); ctx.arcTo(x + w, y, x + w, y + r, r);
    ctx.lineTo(x + w, y + h - r); ctx.arcTo(x + w, y + h, x + w - r, y + h, r);
    ctx.lineTo(x + r, y + h); ctx.arcTo(x, y + h, x, y + h - r, r);
    ctx.lineTo(x, y + r); ctx.arcTo(x, y, x + r, y, r);
    ctx.closePath();
  }
  function fitText(text, maxW) {
    if (ctx.measureText(text).width <= maxW) { return text; }
    var s = text;
    while (s.length > 1 && ctx.measureText(s + ELL).width > maxW) { s = s.slice(0, -1); }
    return s + ELL;
  }
  function wellPath(g, grow, shrink) {
    var cx = X(g.x), cy = Y(g.y), k = shrink === undefined ? 1 : shrink;
    if (g.circle) {
      ctx.beginPath();
      ctx.arc(cx, cy, Math.max(1, S(g.w / 2) * k + grow), 0, TWO_PI);
    } else {
      var w = S(g.w) * k + 2 * grow, h = S(g.h) * k + 2 * grow;
      rrPath(cx - w / 2, cy - h / 2, w, h, Math.min(3, w / 3, h / 3));
    }
  }
  function slotRect(slot) {
    var lw = LW_BY_SLOT[slot];
    if (lw && lw.rect) { return lw.rect; }
    var s = SLOT_BY_ID[slot];
    return s ? [s.x, s.y, s.w, s.h] : null;
  }

  /* ------------------------------------------------------------------ display model for (cur, prog) */
  function computeDisp() {
    var f = N ? FRAMES[cur] : null;
    var d = { f: f, applied: false, ap: 0, mp: 1, mv: 0, head: [HOME[0], HOME[1]], headMount: null,
              mods: base.mods, pips: base.pips, newTips: {} };
    disp = d;
    if (!f) { return d; }
    var dur = f.duration_s || 0;
    var mv = Math.min(f.move_s || 0, dur);
    var t = prog * dur;
    d.mv = mv;
    d.applied = !f.human && prog > 0 && t >= mv - 1e-9;
    if (mv > 0) { d.mp = clamp(t / mv, 0, 1); }
    var act = dur - mv;
    d.ap = act > 0 ? clamp((t - mv) / act, 0, 1) : (d.applied ? 1 : 0);
    var e = reduced ? (prog > 0 ? 1 : 0) : ease(d.mp);
    if (mv <= 0) { e = 1; }
    var hb = f.head_before || HOME, ha = f.head_after || hb;
    d.head = [lerp(hb[0], ha[0], e), lerp(hb[1], ha[1], e)];
    d.from = hb;
    d.headMount = f.head_mount;
    if (d.applied) {
      if (f.modules) { d.mods = f.modules; }
      if (f.pipettes) { d.pips = f.pipettes; }
      (f.tips_consumed || []).forEach(function (k) { d.newTips[k] = 1; });
    }
    return d;
  }
  function netOf(key) {
    var b = has(base.net, key) ? base.net[key] : 0;
    var f = disp.f;
    if (f && f.well_net_ul && has(f.well_net_ul, key)) {
      var a = f.well_net_ul[key];
      if (reduced) { return disp.applied ? a : b; }
      return lerp(b, a, disp.ap);
    }
    return b;
  }

  /* ------------------------------------------------------------------ drawing */
  function drawAll() {
    if (!ctx || !C.bg) { return; }
    var W = cssW, H = cssH;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, W, H);
    ctx.fillStyle = C.bg; ctx.fillRect(0, 0, W, H);
    rrPath(X(-5), Y(EXT.h + 5), S(EXT.w + 10), S(EXT.h + 10), S(5));
    ctx.fillStyle = C.deck; ctx.fill();
    drawSlots();
    drawLabware();
    drawModules();
    drawFocus();
    drawHead();
    drawCallouts();
  }

  function drawSlots() {
    (D.slots || []).forEach(function (s) {
      var x = X(s.x), y = Y(s.y + s.h), w = S(s.w), h = S(s.h);
      ctx.save();
      if (s.kind === 'trash') {
        rrPath(x, y, w, h, S(3));
        ctx.fillStyle = C.trash; ctx.fill();
        ctx.strokeStyle = C.lwline; ctx.lineWidth = 1.25; ctx.stroke();
        ctx.save(); ctx.clip();
        ctx.strokeStyle = C.slot; ctx.lineWidth = 1;
        for (var k = -h; k < w; k += 9) { ctx.beginPath(); ctx.moveTo(x + k, y + h); ctx.lineTo(x + k + h, y); ctx.stroke(); }
        ctx.restore();
        ctx.fillStyle = C.ink; ctx.font = '700 ' + clamp(S(5), 9, 13) + 'px ' + FONT;
        ctx.textAlign = 'left'; ctx.textBaseline = 'top';
        ctx.fillText(s.slot + ' ' + MID + ' TRASH (fixed)', x + 5, y + 4);
      } else if (!LW_BY_SLOT[s.slot]) {
        rrPath(x, y, w, h, S(3));
        ctx.setLineDash([4, 4]); ctx.strokeStyle = C.slot; ctx.lineWidth = 1; ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = C.slot; ctx.font = '700 ' + clamp(S(20), 14, 40) + 'px ' + FONT;
        ctx.textAlign = 'center'; ctx.textBaseline = 'middle';
        ctx.fillText(s.slot, x + w / 2, y + h / 2);
      }
      ctx.restore();
    });
  }

  function drawLabware() {
    (D.labware || []).forEach(function (lw) {
      var r = lw.rect;
      if (r) {
        var x = X(r[0]), y = Y(r[1] + r[3]), w = S(r[2]), h = S(r[3]);
        ctx.save();
        rrPath(x, y, w, h, S(3));
        ctx.fillStyle = C.lw; ctx.fill();
        ctx.strokeStyle = C.lwline; ctx.lineWidth = 1.25; ctx.stroke();
        var fs = clamp(S(5.4), 8.5, 13);
        ctx.font = '700 ' + fs + 'px ' + FONT;
        ctx.fillStyle = C.ink; ctx.textAlign = 'left'; ctx.textBaseline = 'top';
        var title = scale >= 1.0 ? (lw.slot + ' ' + MID + ' ' + (lw.label || lw.load_name || '')) : String(lw.slot);
        ctx.fillText(fitText(title, w - 8), x + 4, y + 3);
        ctx.restore();
      }
      var names = Object.keys(lw.wells || {});
      for (var i = 0; i < names.length; i++) { drawWell(WELL[lw.slot + ':' + names[i]]); }
    });
  }

  function drawWell(g) {
    if (!g) { return; }
    var cx = X(g.x), cy = Y(g.y);
    ctx.save();
    if (g.tip) {
      var used = !!(base.tips[g.key] || disp.newTips[g.key]);
      var rr = Math.max(1.3, S(g.w / 2) * 0.8);
      ctx.beginPath(); ctx.arc(cx, cy, rr, 0, TWO_PI);
      if (used) { ctx.setLineDash([2, 2]); ctx.lineWidth = 1; ctx.strokeStyle = C.tipused; ctx.stroke(); }
      else { ctx.fillStyle = C.tipfull; ctx.fill(); }
      ctx.restore();
      return;
    }
    wellPath(g, 0);
    ctx.lineWidth = 1; ctx.strokeStyle = C.well; ctx.stroke();
    var net = netOf(g.key);
    if (Math.abs(net) > 1e-9) {
      var f = Math.min(1, Math.abs(net) / NETMAX);
      var k = 0.45 + 0.55 * Math.sqrt(f);
      wellPath(g, 0, k);
      if (net > 0) {
        ctx.fillStyle = C.gain; ctx.fill();
      } else {
        ctx.fillStyle = lossPattern || C.loss; ctx.fill();
        ctx.lineWidth = 1.2; ctx.strokeStyle = C.loss; ctx.stroke();
      }
      var minPx = Math.min(S(g.w), S(g.h)) * k;
      if (minPx >= 7) {
        var L = Math.min(minPx * 0.32, 5);
        ctx.lineWidth = 1.6; ctx.lineCap = 'round';
        ctx.strokeStyle = net > 0 ? C.ongain : C.loss;
        ctx.beginPath(); ctx.moveTo(cx - L, cy); ctx.lineTo(cx + L, cy);
        if (net > 0) { ctx.moveTo(cx, cy - L); ctx.lineTo(cx, cy + L); }
        ctx.stroke();
      }
    }
    ctx.restore();
  }

  function pill(text, x, y, border, filled) {
    ctx.save();
    var fs = clamp(S(3.9), 8.5, 11.5);
    ctx.font = '700 ' + fs + 'px ' + FONT;
    var tw = ctx.measureText(text).width;
    var h = fs + 6, w = tw + 22;
    rrPath(x, y, w, h, h / 2);
    ctx.fillStyle = C.surface; ctx.fill();
    ctx.lineWidth = 1.6; ctx.strokeStyle = border; ctx.stroke();
    return { ctx: ctx, w: w, h: h, fs: fs, restore: function () { ctx.restore(); } };
  }

  function drawModules() {
    (D.modules || []).forEach(function (m) {
      var rect = slotRect(m.slot);
      if (!rect) { return; }
      var st = disp.mods && disp.mods[m.slot] ? disp.mods[m.slot] : {};
      var text, on = false, square = false;
      if (m.type === 'temperature') {
        on = st.setpoint_c !== null && st.setpoint_c !== undefined;
        text = 'TEMP ' + (on ? fmtNum(st.setpoint_c, 1) + DEG + 'C set-point' : 'off');
      } else if (m.type === 'magnetic') {
        on = !!st.engaged; square = true;
        text = 'MAG ' + (on ? 'engaged' : 'disengaged');
      } else {
        text = (m.name || 'module');
      }
      var x = X(rect[0]) + 5, yb = Y(rect[1]) - 4;
      var p = pill(text, x, yb - (clamp(S(3.9), 8.5, 11.5) + 6), on ? C.accent : C.lwline, on);
      var fs = p.fs, cx = x + 10, cy = yb - p.h / 2;
      ctx.lineWidth = 1.5; ctx.strokeStyle = C.ink; ctx.fillStyle = C.ink;
      if (square) { if (on) { ctx.fillRect(cx - 3.5, cy - 3.5, 7, 7); } else { ctx.strokeRect(cx - 3.5, cy - 3.5, 7, 7); } }
      else { ctx.beginPath(); ctx.arc(cx, cy, 3.6, 0, TWO_PI); if (on) { ctx.fill(); } else { ctx.stroke(); } }
      ctx.fillStyle = C.ink; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
      ctx.fillText(text, x + 18, cy + 0.5);
      p.restore();
    });
  }

  function callout(text, cx, topY, botY, color, frac) {
    ctx.save();
    var fs = clamp(S(4.2), 9, 12.5);
    ctx.font = '700 ' + fs + 'px ' + FONT;
    var w = ctx.measureText(text).width + 14, h = fs + 10;
    var x = clamp(cx - w / 2, 2, Math.max(2, cssW - w - 2));
    var y = topY - h - 5;
    if (y < 2) { y = botY + 5; }
    rrPath(x, y, w, h, 6);
    ctx.fillStyle = C.surface; ctx.fill();
    ctx.lineWidth = 2; ctx.strokeStyle = color; ctx.stroke();
    ctx.fillStyle = C.ink; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(text, x + 7, y + (h - 3) / 2);
    if (frac !== null) { ctx.fillStyle = color; ctx.fillRect(x + 4, y + h - 5, (w - 8) * frac, 2.5); }
    ctx.restore();
  }

  var pendingCallouts = [];
  function drawCallouts() {
    for (var i = 0; i < pendingCallouts.length; i++) {
      var c = pendingCallouts[i];
      callout(c.txt, c.cx, c.top, c.bot, c.col, c.frac);
    }
    pendingCallouts = [];
  }

  function drawFocus() {
    var f = disp.f;
    pendingCallouts = [];
    if (!f || !f.focus || !f.focus.length) { return; }
    var preview = !!f.focus_preview;
    var boxes = {};
    f.focus.forEach(function (fc) {
      ctx.save();
      ctx.lineWidth = preview ? 1.5 : 2.6;
      if (fc.role === 'source') { ctx.strokeStyle = C.src; ctx.setLineDash([5, 3]); }
      else if (fc.role === 'dest') { ctx.strokeStyle = C.dst; }
      else if (fc.role === 'tip') { ctx.strokeStyle = C.ink; }
      else { ctx.strokeStyle = C.ink; ctx.setLineDash([1.5, 2.5]); }
      if (preview) { ctx.globalAlpha = 0.75; }
      var bx;
      if (fc.role === 'trash') {
        var tr = TRASH && TRASH.rect;
        if (tr) {
          rrPath(X(tr[0]) - 2, Y(tr[1] + tr[3]) - 2, S(tr[2]) + 4, S(tr[3]) + 4, S(3));
          ctx.stroke();
          bx = { x0: X(tr[0]), x1: X(tr[0] + tr[2]), y0: Y(tr[1] + tr[3]), y1: Y(tr[1]) };
        }
      } else {
        var g = WELL[fc.key];
        if (g) {
          wellPath(g, 2.5);
          ctx.stroke();
          var hw = (g.circle ? S(g.w / 2) : S(g.w) / 2) + 3, hh = (g.circle ? S(g.w / 2) : S(g.h) / 2) + 3;
          bx = { x0: X(g.x) - hw, x1: X(g.x) + hw, y0: Y(g.y) - hh, y1: Y(g.y) + hh };
        }
      }
      ctx.restore();
      if (bx) {
        var b = boxes[fc.role];
        if (!b) { boxes[fc.role] = bx; }
        else { b.x0 = Math.min(b.x0, bx.x0); b.x1 = Math.max(b.x1, bx.x1); b.y0 = Math.min(b.y0, bx.y0); b.y1 = Math.max(b.y1, bx.y1); }
      }
    });
    if (preview) { return; }
    var ch = (f.pipette && f.pipette.channels) || 1;
    var v = f.volume_ul;
    var frac = reduced ? null : (disp.applied && disp.ap < 1 ? disp.ap : (disp.ap >= 1 ? 1 : 0));
    ['source', 'dest', 'tip'].forEach(function (role) {
      var b = boxes[role];
      if (!b) { return; }
      var cx = (b.x0 + b.x1) / 2, txt, col;
      if (role === 'source') { txt = 'SOURCE ' + MINUS + fmtNum(v) + ' ' + MU + 'L' + (ch > 1 ? ' ' + TIMES + ' ' + ch : ''); col = C.src; }
      else if (role === 'dest') { txt = 'DEST +' + fmtNum(v) + ' ' + MU + 'L' + (ch > 1 ? ' ' + TIMES + ' ' + ch : ''); col = C.dst; }
      else { txt = ch > 1 ? 'TIPS ' + TIMES + ' ' + ch : 'TIP'; col = C.ink; }
      pendingCallouts.push({ txt: txt, cx: cx, top: b.y0, bot: b.y1, col: col, frac: role === 'tip' ? null : frac });
    });
  }

  function drawHead() {
    var d = disp;
    var mount = d.headMount;
    var info = mount && PIP[mount] ? PIP[mount] : null;
    var offs = info && info.channel_offsets_y && info.channel_offsets_y.length ? info.channel_offsets_y : [0];
    var ch = offs.length;
    var ps = (mount && d.pips && d.pips[mount]) ? d.pips[mount] : null;
    var hasTip = !!(ps && ps.tip);
    var hx = X(d.head[0]);
    var rc = Math.max(2.8, S(2.3));
    ctx.save();
    if (!reduced && d.mv > 0 && d.mp > 0 && d.mp < 1 && d.from) {
      ctx.setLineDash([3, 4]); ctx.lineWidth = 1.5; ctx.strokeStyle = C.muted; ctx.globalAlpha = 0.8;
      ctx.beginPath(); ctx.moveTo(X(d.from[0]), Y(d.from[1])); ctx.lineTo(hx, Y(d.head[1])); ctx.stroke();
      ctx.setLineDash([]); ctx.globalAlpha = 1;
    }
    var yTop = Y(d.head[1] + offs[0]), yBot = Y(d.head[1] + offs[ch - 1]);
    ctx.lineWidth = 1.6; ctx.strokeStyle = C.head; ctx.fillStyle = C.surface;
    if (ch > 1) { rrPath(hx - S(5.5), yTop - S(4.5), S(11), (yBot - yTop) + S(9), S(3)); }
    else { ctx.beginPath(); ctx.arc(hx, yTop, S(5), 0, TWO_PI); }
    ctx.globalAlpha = 0.55; ctx.fill(); ctx.globalAlpha = 0.9; ctx.stroke(); ctx.globalAlpha = 1;
    for (var k = 0; k < ch; k++) {
      var cy = Y(d.head[1] + offs[k]);
      ctx.beginPath(); ctx.arc(hx, cy, rc, 0, TWO_PI);
      if (hasTip) { ctx.fillStyle = C.head; ctx.fill(); ctx.lineWidth = 1; ctx.strokeStyle = C.surface; ctx.stroke(); }
      else { ctx.fillStyle = C.surface; ctx.fill(); ctx.lineWidth = 2; ctx.strokeStyle = C.head; ctx.stroke(); }
    }
    // mount tag beside the head (callouts sit above the target wells, so keep this out of their way)
    var lab = mount ? ((mount === 'left' ? 'L' : (mount === 'right' ? 'R' : mount)) + ' ' + ch + '-ch' + (hasTip ? ' + tip' : '')) : 'gantry at home';
    var fs = clamp(S(3.8), 8.5, 11);
    ctx.font = '700 ' + fs + 'px ' + FONT;
    var tw = ctx.measureText(lab).width + 8;
    var midY = (yTop + yBot) / 2;
    var lx = hx + S(7.5);
    if (lx + tw > cssW - 2) { lx = hx - S(7.5) - tw; }
    ctx.globalAlpha = 0.92;
    rrPath(lx, midY - fs / 2 - 3, tw, fs + 6, 4);
    ctx.fillStyle = C.surface; ctx.fill();
    ctx.lineWidth = 1; ctx.strokeStyle = C.head; ctx.stroke();
    ctx.globalAlpha = 1;
    ctx.fillStyle = C.ink; ctx.textAlign = 'left'; ctx.textBaseline = 'middle';
    ctx.fillText(lab, lx + 4, midY + 0.5);
    ctx.restore();
  }

  /* ------------------------------------------------------------------ text, banner, status, list */
  var capEl = $('caption'), metaEl = $('meta'), bannerEl = $('banner'), humanEl = $('human');
  var tNow = $('t-now'), tTotal = $('t-total'), scrubEl = $('scrub'), stepLab = $('steplab');
  var liveEl = $('live'), inspectEl = $('inspect'), listEl = $('steps');
  var btnPlay = $('btn-play'), btnBack = $('btn-back'), btnNext = $('btn-next'), btnRestart = $('btn-restart');
  var lastAnnounce = 0;

  function announce(text, force) {
    var now = Date.now();
    if (force || now - lastAnnounce > 1500) { liveEl.textContent = text || ''; lastAnnounce = now; }
  }
  function updateButtons() {
    var waitingHuman = !!(N && FRAMES[cur].human && prog < 1);
    var label = playing ? 'Pause' : (waitingHuman ? 'Resume' : (atEnd() ? 'Replay' : 'Play'));
    btnPlay.textContent = label;
    btnPlay.setAttribute('aria-label', label === 'Replay' ? 'Replay from the start' : (label === 'Resume' ? 'Resume after the human step' : label));
    var none = N === 0;
    btnPlay.disabled = none; btnBack.disabled = none; btnNext.disabled = none; btnRestart.disabled = none;
    scrubEl.disabled = none;
  }
  function badge(owner) {
    var b = mk('span', 'badge own-' + owner);
    b.appendChild(mk('i', 'shape'));
    b.lastChild.setAttribute('aria-hidden', 'true');
    b.appendChild(mk('span', null, owner));
    return b;
  }
  function updateBanner(f) {
    clear(bannerEl);
    var ph = (f && f.phase !== null && f.phase !== undefined) ? PHASES[f.phase] : null;
    if (ph) {
      bannerEl.appendChild(badge(ph.owner));
      bannerEl.appendChild(mk('span', 'bt', ph.step_id + ' ' + MID + ' ' + ph.title));
      if (ph.note) { bannerEl.appendChild(mk('span', 'bn', ph.note)); }
      bannerEl.appendChild(mk('span', 'muted', 'phase ' + (ph.index + 1) + ' of ' + PHASES.length));
    } else if (PHASES.length) {
      bannerEl.appendChild(mk('span', 'muted', 'Before the first annotated phase.'));
    } else {
      bannerEl.appendChild(mk('span', 'muted', 'No phase annotations ([step] title | OWNER | note) were found in this run log.'));
    }
    if (f && f.human && !(ph && ph.owner === 'HUMAN')) {
      bannerEl.appendChild(badge('HUMAN'));
      bannerEl.appendChild(mk('span', 'bt', 'human step in progress'));
    }
  }

  var TAG = { pick_up_tip: 'TIP+', drop_tip: 'TIP-', aspirate: 'ASP', dispense: 'DSP', blow_out: 'BLOW', touch_tip: 'TOUCH',
              mix: 'MIX', transfer: 'XFER', distribute: 'DIST', consolidate: 'CONS', temperature_set: 'TEMP',
              temperature_wait: 'TEMP', temperature_deactivate: 'TEMP', magnet_engage: 'MAG', magnet_disengage: 'MAG',
              delay: 'WAIT', pause: 'HUMAN', home: 'HOME', comment: 'NOTE', other: 'OTHER' };
  var stepBtns = [];
  function buildList() {
    clear(listEl);
    var lastPhase = null;
    FRAMES.forEach(function (f, i) {
      if (f.phase !== null && f.phase !== undefined && f.phase !== lastPhase && PHASES[f.phase] && PHASES[f.phase].first_frame === i) {
        var ph = PHASES[f.phase];
        var li0 = mk('li', 'ph');
        li0.appendChild(badge(ph.owner));
        li0.appendChild(mk('span', null, ph.step_id + ' ' + MID + ' ' + ph.title));
        listEl.appendChild(li0);
      }
      lastPhase = f.phase;
      var li = mk('li');
      var b = mk('button', 'stp lv' + Math.min(3, f.level || 0) + (f.is_group ? ' grp' : '') + (f.human ? ' hum' : ''));
      b.type = 'button';
      b.setAttribute('data-i', String(i));
      if (f.text) { b.title = f.text; }
      b.appendChild(mk('span', 'n', String(i + 1)));
      b.appendChild(mk('span', 'k', TAG[f.kind] || 'OTHER'));
      b.appendChild(mk('span', 'tx', f.caption));
      b.appendChild(mk('span', 'tm', f.human ? 'human' : fmtClock(f.start_s)));
      li.appendChild(b);
      listEl.appendChild(li);
      stepBtns[i] = b;
    });
  }
  listEl.addEventListener('click', function (e) {
    var t = e.target;
    while (t && t !== listEl && !(t.getAttribute && t.getAttribute('data-i') !== null)) { t = t.parentNode; }
    if (!t || t === listEl) { return; }
    playing = false;
    setPos(parseInt(t.getAttribute('data-i'), 10), 1);
    updateButtons();
    announce(FRAMES[cur].caption, true);
  });
  var curBtn = null;
  function highlightStep() {
    if (curBtn) { curBtn.removeAttribute('aria-current'); }
    curBtn = stepBtns[cur] || null;
    if (!curBtn) { return; }
    curBtn.setAttribute('aria-current', 'step');
    var top = curBtn.parentNode.offsetTop, h = curBtn.offsetHeight;
    if (top < listEl.scrollTop + 4 || top + h > listEl.scrollTop + listEl.clientHeight - 4) {
      listEl.scrollTop = Math.max(0, top - listEl.clientHeight / 2 + h / 2);
    }
  }

  function kv(dl, k, v) { dl.appendChild(mk('dt', null, k)); dl.appendChild(mk('dd', null, v)); }
  function buildStaticPanels() {
    var dl = $('st-totals'); clear(dl);
    kv(dl, 'Estimated total', fmtClock(TOT.estimated_total_s || 0) + ' (estimate)');
    kv(dl, 'Steps', String(N));
    kv(dl, 'Tips used', String(TOT.tips_used || 0) + ' (' + (TOT.tip_pickups || 0) + ' pick-ups)');
    kv(dl, 'Aspirations', String(TOT.aspirations || 0));
    kv(dl, 'Dispensed', fmtNum(TOT.volume_dispensed_ul || 0) + ' ' + MU + 'L (all channels)');
    kv(dl, 'Human steps', String(TOT.human_pauses || 0));
    var wb = $('warnings'); clear(wb);
    (T.warnings || []).forEach(function (w) { wb.appendChild(mk('p', 'warn', w)); });
    var tm = T.time_model || {}, body = $('tm-body'); clear(body);
    body.appendChild(mk('p', null, tm.headline || 'Estimated time - not measured on a robot'));
    var table = mk('table');
    var cons = tm.constants || {};
    Object.keys(cons).forEach(function (k) {
      var tr = mk('tr');
      tr.appendChild(mk('td', null, k));
      tr.appendChild(mk('td', null, Array.isArray(cons[k]) ? cons[k].join(', ') : String(cons[k])));
      table.appendChild(tr);
    });
    body.appendChild(table);
    var ul = mk('ul');
    (tm.rules || []).forEach(function (r) { ul.appendChild(mk('li', null, r)); });
    body.appendChild(ul);
    var src = T.source || {};
    var bits = [];
    if (src.protocol_file) { bits.push(src.protocol_file); }
    if (src.opentrons_version) { bits.push('Opentrons ' + src.opentrons_version); }
    if (src.api_level) { bits.push('API ' + src.api_level); }
    $('sub').textContent = bits.join(' ' + MID + ' ') || 'Simulated Opentrons OT-2 protocol';
    tTotal.textContent = fmtClock(TOT.estimated_total_s || 0);
  }

  function updateStatus() {
    var f = disp.f;
    var pipsEl = $('st-pips'); clear(pipsEl);
    (D.pipettes || []).forEach(function (p) {
      var ps = disp.pips && disp.pips[p.mount] ? disp.pips[p.mount] : { tip: false, held_ul: 0 };
      var active = disp.headMount === p.mount && f && f.pipette;
      var li = mk('li', active ? 'on' : '',
        p.mount.toUpperCase() + ' ' + MID + ' ' + (p.name || 'pipette') + ' (' + p.channels + '-ch) ' + MID + ' tip: ' +
        (ps.tip ? 'attached' : 'none') + (ps.tip ? ' ' + MID + ' holds ' + fmtNum(ps.held_ul) + ' ' + MU + 'L/ch (computed)' : '') +
        (active ? ' ' + MID + ' ACTIVE' : ''));
      pipsEl.appendChild(li);
    });
    if (!(D.pipettes || []).length) { pipsEl.appendChild(mk('li', null, 'No pipette listed.')); }
    var modsEl = $('st-mods'); clear(modsEl);
    (D.modules || []).forEach(function (m) {
      var st = disp.mods && disp.mods[m.slot] ? disp.mods[m.slot] : {};
      var s = 'slot ' + m.slot + ' ' + MID + ' ' + m.name + ': ';
      if (m.type === 'temperature') { s += (st.setpoint_c === null || st.setpoint_c === undefined) ? 'no set-point' : 'set-point ' + fmtNum(st.setpoint_c, 1) + ' ' + DEG + 'C (commanded, not measured)'; }
      else if (m.type === 'magnetic') { s += st.engaged ? 'engaged' : 'disengaged'; }
      else { s += 'state not tracked'; }
      modsEl.appendChild(mk('li', null, s));
    });
    if (!(D.modules || []).length) { modsEl.appendChild(mk('li', null, 'No modules on the deck.')); }
    var racksEl = $('st-racks'); clear(racksEl);
    (D.labware || []).forEach(function (lw) {
      if (lw.kind !== 'tiprack') { return; }
      var total = 0, used = 0;
      Object.keys(lw.wells || {}).forEach(function (n) {
        total += 1;
        var k = lw.slot + ':' + n;
        if (base.tips[k] || disp.newTips[k]) { used += 1; }
      });
      racksEl.appendChild(mk('li', null, 'Tip rack, slot ' + lw.slot + ': ' + used + ' of ' + total + ' tips used'));
    });
    if (!racksEl.firstChild) { racksEl.appendChild(mk('li', null, 'No tip rack on the deck.')); }
    var net = Object.assign({}, base.net);
    if (f && disp.applied && f.well_net_ul) { Object.assign(net, f.well_net_ul); }
    var keys = Object.keys(net).filter(function (k) { return Math.abs(net[k]) > 1e-9; });
    keys.sort(function (a, b) { return Math.abs(net[b]) - Math.abs(net[a]); });
    var netsEl = $('st-nets'); clear(netsEl);
    $('h-nets').textContent = 'Largest net changes so far' + (keys.length ? ' (' + keys.length + ' wells changed)' : '');
    keys.slice(0, 6).forEach(function (k) {
      var parts = k.split(':');
      netsEl.appendChild(mk('li', null, 'slot ' + parts[0] + ' ' + parts[1] + ': ' + fmtSigned(net[k]) + ' ' + MU + 'L net'));
    });
    if (!keys.length) { netsEl.appendChild(mk('li', null, 'No net volume change yet.')); }
  }

  function updateText() {
    var f = N ? FRAMES[cur] : null;
    if (!f) {
      capEl.textContent = 'No robot commands were logged for this protocol, so there is nothing to animate.';
      metaEl.textContent = '';
      updateBanner(null);
      humanEl.hidden = true;
      return;
    }
    var atStart = cur === 0 && prog === 0 && !playing;
    capEl.textContent = atStart ? 'Ready. Press Play (Space) to run ' + N + ' steps. First step: ' + f.caption : f.caption;
    var t = 'Step ' + (cur + 1) + ' of ' + N + ' ' + MID + ' est. ' + fmtClock(f.start_s) +
      (f.human ? ' (no duration: waits for a person)' : (f.duration_s ? ' to ' + fmtClock(f.end_s) : ''));
    if (f.detail) { t += ' ' + MID + ' ' + f.detail; }
    metaEl.textContent = t;
    updateBanner(f);
    var humanOn = !!(f.human && prog < 1);
    humanEl.hidden = !humanOn;
    if (!humanOn) { focusResume = false; }
    if (humanOn && focusResume) {
      focusResume = false;
      try { $('resume').focus(); } catch (err) { /* focus is a convenience only */ }
    }
    if (humanOn) {
      $('human-msg').textContent = (f.human_step && f.human_step.message) || 'The protocol asks for a human step here.';
      $('human-step').textContent = (f.human_step && f.human_step.step_id ? 'Protocol step ' + f.human_step.step_id + '. ' : '') +
        'The robot is waiting. No time is estimated for this step.';
    }
    stepLab.textContent = (cur + 1) + ' / ' + N;
    highlightStep();
  }

  var scrubbing = false;   // true only while the pointer is dragging the slider
  function updateClock() {
    var f = N ? FRAMES[cur] : null;
    var t = 0;
    if (f) { t = f.human ? f.start_s : lerp(f.start_s, f.end_s, prog); }
    tNow.textContent = fmtClock(t);
    if (N) {
      var v = cur + prog;
      scrubEl.max = String(N);
      if (!scrubbing) { scrubEl.value = String(v); }
      scrubEl.setAttribute('aria-valuetext', 'Step ' + (cur + 1) + ' of ' + N + ', ' + fmtClock(t) + ' estimated');
    }
  }

  function renderAll() {
    computeDisp();
    drawAll();
    var key = cur + '|' + (disp.applied ? 1 : 0) + '|' + (prog >= 1 ? 1 : 0) + '|' + (playing ? 1 : 0) + '|' + (N && FRAMES[cur].human ? 1 : 0);
    if (key !== shownKey) {
      shownKey = key;
      updateText();
      updateButtons();
    }
    var skey = cur + '|' + (disp.applied ? 1 : 0) + '|' + (disp.headMount || '');
    if (skey !== shownStatus) { shownStatus = skey; updateStatus(); }
    updateClock();
    if (playing && N) { announce(FRAMES[cur].caption, false); }
  }

  /* ------------------------------------------------------------------ hover / tap inspector */
  function inspectAt(ev) {
    if (!ctx) { return; }
    var rect = canvasEl.getBoundingClientRect();
    var px = ev.clientX - rect.left, py = ev.clientY - rect.top;
    var mx = px / scale - PAD_MM, my = EXT.h + PAD_MM - py / scale;
    var hit = null;
    for (var i = 0; i < WELL_LIST.length && !hit; i++) {
      var g = WELL_LIST[i];
      var dx = Math.abs(g.x - mx), dy = Math.abs(g.y - my);
      if (g.circle ? (dx * dx + dy * dy <= Math.pow(g.w / 2 + 0.7, 2)) : (dx <= g.w / 2 + 0.5 && dy <= g.h / 2 + 0.5)) { hit = g; }
    }
    if (hit) {
      var s = 'Slot ' + hit.slot + ', well ' + hit.name + ' (' + (hit.lw.label || hit.lw.load_name) + '): ';
      if (hit.tip) { s += (base.tips[hit.key] || disp.newTips[hit.key]) ? 'tip used' : 'tip available'; }
      else {
        var n = netOf(hit.key);
        s += 'net change ' + fmtSigned(n) + ' ' + MU + 'L since the start of the run (initial volume unknown)';
        if (hit.max) { s += '; capacity ' + fmtNum(hit.max) + ' ' + MU + 'L'; }
      }
      inspectEl.textContent = s;
      return;
    }
    for (var slot in LW_BY_SLOT) {
      if (!has(LW_BY_SLOT, slot)) { continue; }
      var r = LW_BY_SLOT[slot].rect;
      if (r && mx >= r[0] && mx <= r[0] + r[2] && my >= r[1] && my <= r[1] + r[3]) {
        inspectEl.textContent = 'Slot ' + slot + ': ' + (LW_BY_SLOT[slot].display_name || LW_BY_SLOT[slot].load_name) +
          (LW_BY_SLOT[slot].module ? ' on ' + LW_BY_SLOT[slot].module : '');
        return;
      }
    }
    inspectEl.textContent = 'Hover or tap a well to read its net change. Keys: Space = play/pause, Left/Right = step.';
  }
  if (canvasEl) {
    canvasEl.addEventListener('pointermove', inspectAt);
    canvasEl.addEventListener('pointerdown', inspectAt);
  }

  /* ------------------------------------------------------------------ wiring */
  btnPlay.addEventListener('click', togglePlay);
  btnBack.addEventListener('click', function () { stepBy(-1); });
  btnNext.addEventListener('click', function () { stepBy(1); });
  btnRestart.addEventListener('click', restart);
  $('resume').addEventListener('click', function () { resume(); });
  $('speed').addEventListener('change', function (e) { setSpeed(parseFloat(e.target.value) || 1); });
  $('skipdelays').addEventListener('change', function (e) { skipDelays = !!e.target.checked; markDirty(); });
  scrubEl.addEventListener('input', function () {
    if (N === 0) { return; }
    var v = parseFloat(scrubEl.value);
    if (!isFinite(v)) { return; }
    var i = Math.min(N - 1, Math.floor(v));
    setPos(i, v >= N ? 1 : v - i);
    if (v >= N) { prog = 1; }
    updateButtons();
  });
  scrubEl.addEventListener('change', function () { announce(N ? FRAMES[cur].caption : '', true); });
  scrubEl.addEventListener('pointerdown', function () { scrubbing = true; });
  window.addEventListener('pointerup', function () { scrubbing = false; });
  window.addEventListener('pointercancel', function () { scrubbing = false; });

  // Space = play/pause and Left/Right = step, everywhere except where the browser's own key meaning matters:
  // a select (arrows change the option), a checkbox (Space toggles it) and the step list (Space/Enter jump to a step).
  function keyInfo(t) {
    var tag = t && t.tagName ? t.tagName.toLowerCase() : '';
    var isInput = tag === 'input';
    return {
      typing: tag === 'select' || tag === 'textarea' || (isInput && t.type !== 'range' && t.type !== 'checkbox'),
      keepSpace: (isInput && t.type === 'checkbox') || !!(t && listEl.contains(t))
    };
  }
  document.addEventListener('keydown', function (e) {
    if (e.ctrlKey || e.metaKey || e.altKey) { return; }
    var k = keyInfo(e.target);
    if (k.typing) { return; }
    if (e.key === ' ' || e.key === 'Spacebar') {
      if (k.keepSpace) { return; }
      e.preventDefault();
      togglePlay();
    } else if (e.key === 'ArrowRight') { e.preventDefault(); stepBy(1); }
    else if (e.key === 'ArrowLeft') { e.preventDefault(); stepBy(-1); }
  });
  document.addEventListener('keyup', function (e) {
    if (e.key !== ' ' && e.key !== 'Spacebar') { return; }
    var k = keyInfo(e.target);
    if (!k.typing && !k.keepSpace) { e.preventDefault(); }   // stop a focused button from also "clicking" on key-up
  });

  // One display frame: advance the playhead by dt real seconds (when playing), then redraw if anything changed.
  function frameStep(dt) {
    if (playing) { advance(dt); dirty = true; }
    if (dirty) { dirty = false; renderAll(); }
  }
  function tick(ts) {
    window.requestAnimationFrame(tick);
    if (lastTs === null) { lastTs = ts; }
    var dt = Math.min(Math.max((ts - lastTs) / 1000, 0), 0.25);
    lastTs = ts;
    frameStep(dt);
  }

  if (window.ResizeObserver) {
    new ResizeObserver(function () { if (Math.abs((host.clientWidth || 0) - lastHostW) > 1) { layout(); } }).observe(host);
  }
  window.addEventListener('resize', layout);
  if (mqNarrow && mqNarrow.addEventListener) { mqNarrow.addEventListener('change', layout); }

  readColors();
  buildStaticPanels();
  buildList();
  layout();
  updateButtons();
  renderAll();
  window.requestAnimationFrame(tick);

  /* small hook for debugging and automated checks; the page does not need it */
  window.vaxforgePlayer = {
    frames: N,
    goto: function (i, p) { playing = false; setPos(i, p === undefined ? 1 : p); updateButtons(); markDirty(); },
    play: play, pause: pause, step: stepBy, restart: restart,
    tick: frameStep,
    state: function () {
      return { cur: cur, prog: prog, playing: playing, speed: speed, reduced: reduced,
               head: disp ? [Math.round(disp.head[0] * 100) / 100, Math.round(disp.head[1] * 100) / 100] : null,
               human: !!(N && FRAMES[cur].human && prog < 1) };
    }
  };
})();
"""


def _ascii_js(src: str) -> str:
    """Escape every non-ASCII character as a JS \\uXXXX escape (only occurs inside string literals)."""
    def esc(m: re.Match) -> str:
        cp = ord(m.group(0))
        if cp > 0xFFFF:
            cp -= 0x10000
            return "\\u%04x\\u%04x" % (0xD800 + (cp >> 10), 0xDC00 + (cp & 0x3FF))
        return "\\u%04x" % cp
    return re.sub(r"[^\x00-\x7f]", esc, src)


def _json_for_html(obj: Any) -> str:
    """JSON that is safe inside <script type="application/json"> and contains no 'http://'-like text."""
    s = json.dumps(obj, separators=(",", ":"), ensure_ascii=True, allow_nan=False, default=str)
    return (s.replace("<", "\\u003c").replace(">", "\\u003e").replace("&", "\\u0026")
             .replace("://", ":\\/\\/"))


def _failure_body(tl: dict[str, Any]) -> str:
    esc = _html.escape
    err = tl.get("error") if isinstance(tl.get("error"), dict) else {}
    etype = esc(_str(err.get("type")) or "Error")
    emsg = esc(_str(err.get("message")) or "The simulation did not complete and gave no further detail.")
    src = tl.get("source") if isinstance(tl.get("source"), dict) else {}
    bits = [b for b in (src.get("protocol_file"), f"Opentrons {src['opentrons_version']}" if src.get("opentrons_version") else None) if b]
    out = ['<div class="fail" role="alert">',
           '<h1><span class="badge own-HUMAN"><i class="shape" aria-hidden="true"></i><span>FAILED</span></span> '
           'Simulation failed - nothing to animate</h1>']
    if bits:
        out.append(f'<p class="sub">{esc(" - ".join(bits))}</p>')
    out.append("<p>The Opentrons simulator could not run this protocol, so no animation was produced. "
               "This page shows the error instead of an empty player. Fix the protocol and simulate again.</p>")
    out.append(f"<p><strong>{etype}</strong></p><pre>{emsg}</pre>")
    last = err.get("last_commands")
    n = err.get("events_logged")
    if isinstance(last, list) and last:
        out.append(f"<p>The simulator logged {esc(str(n))} command(s) before stopping. The last ones:</p><ul>")
        out.extend(f"<li>{esc(_str(x))}</li>" for x in last)
        out.append("</ul>")
    tb = err.get("traceback_tail")
    if isinstance(tb, list) and tb:
        out.append("<details><summary>Traceback (last lines)</summary><pre>"
                   + esc("\n".join(_str(x) for x in tb)) + "</pre></details>")
    out.append("</div>")
    return "\n".join(out)


def render_html(timeline: dict) -> str:
    """Render the self-contained player (inline CSS + JS, timeline embedded as JSON). Pure and offline."""
    tl = timeline if isinstance(timeline, dict) else {}
    ok = bool(tl.get("ok")) and isinstance(tl.get("frames"), list) and isinstance(tl.get("deck"), dict)
    if ok:
        body, script = _PLAYER_BODY, _ascii_js(_PLAYER_JS)
        src = tl.get("source") if isinstance(tl.get("source"), dict) else {}
        title = "Protocol animation" + (f" - {src['protocol_file']}" if src.get("protocol_file") else "")
    else:
        if not isinstance(tl.get("error"), dict):
            tl = dict(tl)
            tl["error"] = {"type": "InvalidTimeline", "message": "The timeline passed to render_html is not a valid one."}
        body, script = _failure_body(tl), ""
        title = "Simulation failed"
    parts = [
        '<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n',
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n',
        '<meta name="color-scheme" content="light dark">\n',
        "<meta http-equiv=\"Content-Security-Policy\" content=\"default-src 'none'; style-src 'unsafe-inline'; "
        "script-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'\">\n",
        f"<title>{_html.escape(title)}</title>\n<style>{_CSS}</style>\n</head>\n<body>\n",
        body,
        '\n<script type="application/json" id="vf-timeline">', _json_for_html(tl), "</script>\n",
    ]
    if script:
        parts += ["<script>", script, "</script>\n"]
    parts.append("</body>\n</html>\n")
    return "".join(parts)


# --------------------------------------------------------------------------------------
# Public: files and CLI
# --------------------------------------------------------------------------------------
def write_animation(sim_json_path: Any, out_dir: Any) -> dict:
    """Read a sim JSON, write ``timeline.json`` and ``protocol_animation.html`` into ``out_dir``.

    Never raises for a bad/missing/unparseable sim file: an honest "simulation failed" page is
    written instead and ``ok`` is False. Returns {"timeline": path, "html": path, "ok": bool}.
    """
    sim: Any
    try:
        sim = json.loads(Path(sim_json_path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        sim = {"schema": SIM_SCHEMA, "ok": False,
               "error": {"type": type(exc).__name__,
                         "message": f"could not read the simulation JSON '{_basename(sim_json_path)}': {exc}"}}
    timeline = build_timeline(sim)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    tl_path = out / "timeline.json"
    html_path = out / "protocol_animation.html"
    with open(tl_path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(timeline, fh, indent=1, ensure_ascii=False, allow_nan=False)
        fh.write("\n")
    with open(html_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(render_html(timeline))
    return {"timeline": str(tl_path), "html": str(html_path), "ok": bool(timeline.get("ok"))}


# --------------------------------------------------------------------------------------
# Public: stage entrypoint (artifact contract, docs/ARTIFACT_CONTRACT.md)
# --------------------------------------------------------------------------------------
TIMES_NOTE = "Times in the animations are estimates from a fixed time model (timeline['time_model']), not measurements."


def _short(text: str, limit: int = 160) -> str:
    """One line of at most `limit` characters, cut at a word: an upstream reason repeated in full by every later stage is noise."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 4].rsplit(" ", 1)[0] + " ..."


def _not_run(store: RunStore, reasons: list[str], inputs: list[str]) -> Artifact:
    """S9+ had nothing to animate: artifact `ok`, `payload.status == "not_run"`, `payload.reasons` and the same text as the first warning.

    `ok` because `vaxforge run` stops on any other artifact status and S9+ is optional; stream D's report reads `payload.status`.
    (The same convention as `streamc_adapter.write_not_run`, which this module cannot import: that one needs pydantic.)
    """
    payload = {"status": "not_run", "reasons": reasons, "sessions": [], "summary": {"n_sessions": 0, "n_animated": 0, "n_skipped": 0}}
    stale = store.run_dir / "data" / "s9plus"
    if stale.exists():  # this stage owns data/s9plus: an earlier run's files must not sit beside an artifact that says nothing was animated
        shutil.rmtree(stale)
    before = REQUIRED_PAYLOAD_KEYS.get(STAGE)
    register_payload_keys(STAGE, PAYLOAD_KEYS)
    try:
        return store.write_artifact(STAGE, payload, params={}, inputs=inputs, tools={}, status="ok",
                                    warnings=["S9+ not run: " + "; ".join(reasons), TIMES_NOTE])
    finally:
        if before is None:
            REQUIRED_PAYLOAD_KEYS.pop(STAGE, None)
        else:
            register_payload_keys(STAGE, before)


def run(store: RunStore, config) -> Artifact:
    """S9+ stage: animate every simulated S9 session. Reads ONLY the `s9` artifact (`require_ok()`: a `failed` S9 is an error).

    Writes `data/s9plus/<candidate>/<backend>/<session>/timeline.json` and `protocol_animation.html`, and a payload that
    holds a contract file reference for each (root/path/sha256/bytes). Status `ok` when every session that has a simulation was
    animated (sessions REFUSED by the code model have no code and are listed as skipped, not as failures), else `failed` with one
    warning per session that was not. Nothing to animate (no s9 artifact, an s9 that did not run, or no session with a simulation)
    -> `ok` with `payload.status == "not_run"` and `payload.reasons`. `config` is unused: the animation has no settings.
    """
    if not store.has("s9"):
        return _not_run(store, ["no s9 artifact in this run, so there are no simulated sessions to animate: run S9 first (vaxforge run --stages s9)"], [])
    s9 = store.read_artifact("s9").require_ok()
    if s9.payload.get("status") == "not_run":
        got = s9.payload.get("reasons")
        why = "; ".join(str(r) for r in got) if isinstance(got, list) and got else (s9.warnings or ["no details recorded"])[0]
        return _not_run(store, [f"s9 did not run ({_short(why)}); see payload.reasons of s9_robot.json"], ["s9"])
    data = store.run_dir / "data"
    root = data / "s9plus"
    if root.exists():  # this stage owns data/s9plus: a re-run must not leave an earlier run's files beside the new ones
        if root.resolve().parent != data.resolve():
            raise ValueError(f"refusing to clear {root}: it is not directly inside {data}")
        shutil.rmtree(root)
    sessions: list[dict[str, Any]] = []
    bad: list[str] = []
    skipped_by: Counter[str] = Counter()  # why a session was not animated -> how many
    for cand in s9.payload.get("candidates") or []:
        for backend in cand.get("backends") or []:
            for s in backend.get("sessions") or []:
                tag = f"{cand['candidate_id']} / {backend['label']} / {s['session_id']}"
                entry: dict[str, Any] = {"candidate_id": cand["candidate_id"], "backend": backend["label"], "session_id": s["session_id"],
                                         "s9_outcome": s["outcome"], "ok": False, "timeline": None, "html": None}
                sessions.append(entry)
                if s["outcome"] in ("REFUSED", "UNVERIFIED"):  # no code (refused) or no simulation (UNVERIFIED): nothing to animate, and no defect
                    entry["skipped"] = ("REFUSED by the code model: no code exists" if s["outcome"] == "REFUSED"
                                        else "not simulated (UNVERIFIED): no simulation file")
                    skipped_by[entry["skipped"]] += 1
                    continue
                sim_ref = (s.get("files") or {}).get("sim")
                if not sim_ref:
                    bad.append(f"{tag}: S9 wrote no simulation file, so there is nothing to animate")
                    continue
                problem = store.verify_ref(sim_ref)
                if problem:
                    bad.append(f"{tag}: simulation file unusable ({problem})")
                    continue
                res = write_animation(store.resolve(sim_ref), root / cand["dir_name"] / backend["dir_name"] / s["session_id"])
                entry.update(ok=bool(res["ok"]), timeline=store.run_ref(res["timeline"]), html=store.run_ref(res["html"]))
                if not res["ok"]:
                    bad.append(f"{tag}: the simulation had failed, so the HTML is an error page, not an animation")
    n_ok, n_skipped = sum(1 for x in sessions if x["ok"]), sum(skipped_by.values())
    skipped_note = "; ".join(f"{n} x {why}" for why, n in sorted(skipped_by.items()))
    if not bad and n_ok == 0:  # nothing was animated and nothing went wrong: there was nothing to animate
        why = ([f"every S9 session was skipped, so there is no simulation to animate ({skipped_note})"] if sessions
               else ["the s9 artifact lists no sessions"])
        return _not_run(store, why, ["s9"])
    payload = {"sessions": sessions, "summary": {"n_sessions": len(sessions), "n_animated": n_ok, "n_skipped": n_skipped}}
    # `inputs` is the default (s9) and is exactly what this stage reads.
    # The structural check is registered only for this write and restored after it: REQUIRED_PAYLOAD_KEYS is process-global state
    # (the same helper, payload_keys_applied, lives in streamc_adapter; this module stays free of pydantic, so it is not imported).
    before = REQUIRED_PAYLOAD_KEYS.get(STAGE)
    register_payload_keys(STAGE, PAYLOAD_KEYS)
    try:
        return store.write_artifact(STAGE, payload, params={}, inputs=["s9"], tools={}, status="failed" if bad else "ok",
                                    warnings=[*bad, *([f"{n_skipped} of {len(sessions)} session(s) were not animated: {skipped_note}"] if n_skipped else []),
                                              TIMES_NOTE])
    finally:
        if before is None:
            REQUIRED_PAYLOAD_KEYS.pop(STAGE, None)
        else:
            register_payload_keys(STAGE, before)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2 or args[0] in ("-h", "--help"):
        print("usage: python -m vaxforge.s9plus_animate <sim.json> <out_dir>\n"
              "  writes <out_dir>/timeline.json and <out_dir>/protocol_animation.html\n"
              "  exit code: 0 animation written, 1 simulation had failed (error page written), 2 usage")
        return 2
    res = write_animation(args[0], args[1])
    _say(f"timeline: {res['timeline']}\nhtml:     {res['html']}")
    if not res["ok"]:
        _say("the simulation had failed or could not be read; an error page was written instead of an animation",
             sys.stderr)
        return 1
    return 0


def _say(msg: str, stream: Any = None) -> None:
    """print() that cannot die on a console codepage that lacks a character in a path."""
    stream = stream or sys.stdout
    try:
        print(msg, file=stream)
    except UnicodeEncodeError:
        enc = getattr(stream, "encoding", None) or "ascii"
        print(msg.encode(enc, "replace").decode(enc), file=stream)


if __name__ == "__main__":
    raise SystemExit(main())
