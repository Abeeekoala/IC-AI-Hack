"""Standalone Opentrons simulation runner (private helper for S9 / S9+).

Run it with an interpreter that has `opentrons` installed (Opentrons 7.5.0 needs
Python 3.10; it does not import on 3.11+):

    python _ot_sim_runner.py <protocol.py> <out.json>

It deliberately imports nothing from `vaxforge` so it can run in a separate
virtualenv from the rest of the pipeline. It writes ONE JSON document and always
exits 0 when it managed to write that document - a protocol that fails to
simulate is reported inside the JSON (`ok: false`), not as a crash.

SECURITY: this EXECUTES the protocol file (that is what simulation is). Callers
must run `s9_robot.static_safety_check` on any code they did not write
themselves (e.g. LLM output) BEFORE invoking this runner.

Output schema (version 1)
-------------------------
{
  "schema": "vaxforge.ot_sim/1",
  "ok": bool,
  "error": null | {"type": str, "message": str, "wrapper": str},
                                 # type = most specific error name (TipNotAttachedError, TypeError, ...);
                                 # message = one clean line, no UUIDs / timestamps / local paths
  "opentrons_version": str,
  "protocol_file": str,
  "api_level": str,
  "deck": {
    "labware": [{
      "slot": "1".."11",
      "load_name": str, "display_name": str, "uri": str,
      "module": null | "temperature module gen2" | "magnetic module gen2" | ...,
      "kind": "tiprack" | "plate" | "reservoir" | "tubes" | "other",
      "origin_mm": [x, y, z],                 # deck-frame position of well A1 top
      "wells": {"A1": {"x": mm, "y": mm, "z_top": mm, "depth": mm,
                       "shape": "circular"|"rectangular",
                       "diameter": mm|null, "length": mm|null, "width": mm|null,
                       "max_volume_ul": float}, ...}
    }],
    "modules":   [{"slot": str, "name": str, "model": str}],
    "pipettes":  [{"mount": "left"|"right", "name": str, "channels": int,
                   "min_volume_ul": float, "max_volume_ul": float}],
    "trash":     {"slot": "12"}          # fixed trash; not listed in `labware`
  },
  "liquids": [{"slot": str, "well": str, "labware": str, "liquid": str|null, "volume_ul": float|null}],
                                 # every `well.load_liquid(...)` the protocol made (initial contents)
  "events": [{
    "idx": int,                  # position in the run log
    "level": int,                # 0 = top-level command; >0 = sub-step of the nearest lower level
    "kind": "comment"|"pick_up_tip"|"drop_tip"|"aspirate"|"dispense"|"blow_out"|"touch_tip"
            |"mix"|"transfer"|"distribute"|"consolidate"|"temperature_set"|"temperature_wait"
            |"temperature_deactivate"|"magnet_engage"|"magnet_disengage"|"delay"|"pause"
            |"home"|"other",
    "is_group": bool,            # true for transfer/distribute/consolidate/mix headers whose
                                 # primitive steps follow at a deeper level
    "text": str,                 # the robot's own human-readable line
    "pipette": null | {"mount": str, "name": str, "channels": int},
    "volume_ul": null | float,
    "flow_rate_ul_s": null | float,   # parsed from the text when the robot reports it
    "target": null | {"slot": str|null, "well": str|null, "labware": str|null,
                      "point_mm": [x, y, z]|null},   # for trash: slot "12", well null
    "source": null | {...same as target...},         # group headers only
    "dest":   null | {...same as target...},         # group headers only
    "celsius": null | float,
    "seconds": null | float,          # delay duration (minutes folded in)
    "repetitions": null | int,        # mix
    "message": null | str             # pause / comment text
  }]
}
"""

from __future__ import annotations

import os
import sys

if __name__ == "__main__":
    # Run by path, Python puts this file's directory first on sys.path. In the repo that directory holds the vaxforge
    # modules (e.g. `http.py`), which would shadow the standard library and the simulator's own dependencies: stream A's
    # `http.py` made `import http.client` fail inside opentrons ("'http' is not a package"). Drop it before any other import.
    _HERE = os.path.normcase(os.path.dirname(os.path.realpath(__file__)))
    sys.path[:] = [p for p in sys.path if os.path.normcase(os.path.realpath(p or os.curdir)) != _HERE]

import json  # noqa: E402
import re  # noqa: E402
import traceback  # noqa: E402
from typing import Any  # noqa: E402

SCHEMA = "vaxforge.ot_sim/1"

# (text prefix, kind). Order matters (longest/most specific first).
_PREFIXES: list[tuple[str, str]] = [
    ("Picking up tip", "pick_up_tip"),
    ("Dropping tip", "drop_tip"),
    ("Returning tip", "drop_tip"),
    ("Aspirating", "aspirate"),
    ("Dispensing", "dispense"),
    ("Blowing out", "blow_out"),
    ("Touching tip", "touch_tip"),
    ("Mixing", "mix"),
    ("Transferring", "transfer"),
    ("Distributing", "distribute"),
    ("Consolidating", "consolidate"),
    ("Setting Temperature Module", "temperature_set"),
    ("Waiting for Temperature Module", "temperature_wait"),
    ("Deactivating Temperature Module", "temperature_deactivate"),
    ("Engaging Magnetic Module", "magnet_engage"),
    ("Disengaging Magnetic Module", "magnet_disengage"),
    ("Delaying", "delay"),
    ("Pausing robot operation", "pause"),
    ("Homing", "home"),
]
_GROUPS = {"transfer", "distribute", "consolidate", "mix"}
_MODULE_NAMES = {
    "temperatureModuleV1": "temperature module",
    "temperatureModuleV2": "temperature module gen2",
    "magneticModuleV1": "magnetic module",
    "magneticModuleV2": "magnetic module gen2",
    "thermocyclerModuleV1": "thermocycler module",
    "thermocyclerModuleV2": "thermocycler module gen2",
    "heaterShakerModuleV1": "heaterShakerModuleV1",
}
_TRASH_RE = re.compile(r"Trash Bin on slot (\d+)", re.IGNORECASE)
_RATE_RE = re.compile(r"at\s+([0-9.]+)\s+(?:uL|µL|ul)/sec", re.IGNORECASE)


_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_PATH_RE = re.compile(r"[A-Za-z]:[\\/][^\r\n'\"]*")
# Absolute POSIX paths under the usual local roots (a bare "/" in a message is left alone).
_POSIX_PATH_RE = re.compile(r"""(?<![\w.:/-])/(?:home|Users|tmp|var|private|root|opt|usr|mnt|Volumes)/[^\r\n'"]*""")
# `datetime.datetime(2026, 11, 14, ..., tzinfo=...)` as an ErrorOccurrence repr prints it, and ISO / log timestamps.
_TIME_RE = re.compile(
    r"datetime\.datetime\((?:[^()]|\((?:[^()]|\([^()]*\))*\))*\)"
    r"|\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}(?::\d{2}(?:[.,]\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?"
)
# Engine wrappers around the real error. The reported kind is never one of these (engine errors nest
# outermost first, e.g. ExceptionInProtocolError > ProtocolCommandFailedError > TipNotAttachedError).
_GENERIC_ERRORS = {"ExceptionInProtocolError", "PythonException", "UnexpectedProtocolError", "ProtocolCommandFailedError"}


def _clean(text: str) -> str:
    """One line, with no UUIDs, timestamps or local paths."""
    text = _TIME_RE.sub("<time>", _UUID_RE.sub("<id>", text))
    return " ".join(_POSIX_PATH_RE.sub("<path>", _PATH_RE.sub("<path>", text)).split())


def summarize_exception(exc: BaseException) -> tuple[str, str]:
    """(specific kind, clean one-line message) for a simulation failure.

    The raw engine error embeds a fresh UUID, a timestamp and local file paths, so it must
    never be stored or compared. The kind is the most specific error name, e.g.
    `TipNotAttachedError`, or for a Python error inside the protocol its class (`TypeError`).
    """
    raw = str(exc)
    types = re.findall(r"errorType='(\w+)'", raw)
    details = [a or b for a, b in re.findall(r"detail=(?:\"([^\"]*)\"|'([^']*)')", raw)]
    first = next((d for d in details if d), "")
    specific = [t for t in types if t not in _GENERIC_ERRORS]
    kind = specific[-1] if specific else None  # the last one is the innermost wrapped error
    if kind is None:
        m = re.match(r"\s*(\w+(?:Error|Exception|Warning))\b", first)
        kind = m.group(1) if m else (types[-1] if types else type(exc).__name__)
    text = first or (raw.splitlines()[0] if raw else type(exc).__name__)
    text = _clean(text)
    return kind, text[:400]


def _num(x: Any) -> float | None:
    try:
        return None if x is None else float(x)
    except (TypeError, ValueError):
        return None


def _slot_of_labware(labware: Any) -> str | None:
    """Slot a labware sits in, whether directly on the deck or on a module."""
    parent = getattr(labware, "parent", None)
    if isinstance(parent, str):
        return parent
    inner = getattr(parent, "parent", None)  # ModuleContext.parent -> slot name
    return inner if isinstance(inner, str) else None


def _point(p: Any) -> list[float] | None:
    try:
        return [round(float(p.x), 3), round(float(p.y), 3), round(float(p.z), 3)]
    except Exception:
        return None


def _target(obj: Any) -> dict[str, Any] | None:
    """Normalise a Well / Location / TrashBin-ish object to a plain dict."""
    if obj is None:
        return None
    try:
        # Well
        if hasattr(obj, "well_name") and hasattr(obj, "parent"):
            lw = obj.parent
            return {
                "slot": _slot_of_labware(lw),
                "well": obj.well_name,
                "labware": getattr(lw, "load_name", None),
                "point_mm": _point(obj.top().point),
            }
        # Location (point + labware-or-well)
        if hasattr(obj, "point") and hasattr(obj, "labware"):
            lab = obj.labware
            point = _point(obj.point)
            if getattr(lab, "is_well", False):
                w = lab.as_well()
                lw = w.parent
                return {
                    "slot": _slot_of_labware(lw),
                    "well": w.well_name,
                    "labware": getattr(lw, "load_name", None),
                    "point_mm": point,
                }
            if getattr(lab, "is_labware", False):
                lw = lab.as_labware()
                return {"slot": _slot_of_labware(lw), "well": None,
                        "labware": getattr(lw, "load_name", None), "point_mm": point}
            return {"slot": None, "well": None, "labware": None, "point_mm": point}
    except Exception:
        pass
    text = str(obj)
    m = re.search(r"slot (\d+)", text)
    return {"slot": m.group(1) if m else None, "well": None, "labware": None, "point_mm": None}


def _pipette(instr: Any) -> dict[str, Any] | None:
    if instr is None:
        return None
    try:
        return {
            "mount": str(getattr(instr, "mount", None)),
            "name": getattr(instr, "name", None),
            "channels": int(getattr(instr, "channels", 1) or 1),
        }
    except Exception:
        return {"mount": None, "name": str(instr)[:60], "channels": 1}


def _classify(text: str, payload: dict[str, Any]) -> str:
    # A user comment (ctx.comment) is a payload with nothing but `text`.
    if set(payload) <= {"text"}:
        for prefix, kind in _PREFIXES:
            if text.startswith(prefix) and kind in ("temperature_deactivate", "magnet_engage",
                                                    "magnet_disengage", "home", "touch_tip"):
                return kind  # these carry no typed fields
        return "comment"
    for prefix, kind in _PREFIXES:
        if text.startswith(prefix):
            return kind
    return "other"


def _normalise_event(idx: int, entry: dict[str, Any]) -> dict[str, Any]:
    payload = dict(entry.get("payload", {}))
    text = str(payload.get("text", ""))
    kind = _classify(text, payload)
    seconds = None
    if kind == "delay":
        seconds = (_num(payload.get("minutes")) or 0.0) * 60.0 + (_num(payload.get("seconds")) or 0.0)
    rate = _RATE_RE.search(text)
    ev: dict[str, Any] = {
        "idx": idx,
        "level": int(entry.get("level", 0)),
        "kind": kind,
        "is_group": kind in _GROUPS,
        "text": text,
        "pipette": _pipette(payload.get("instrument")),
        "volume_ul": _num(payload.get("volume")),
        "flow_rate_ul_s": float(rate.group(1)) if rate else None,
        "target": _target(payload.get("location")) if kind not in _GROUPS else None,
        "source": _target(payload.get("source")) if kind in _GROUPS else None,
        "dest": None,
        "celsius": _num(payload.get("celsius")),
        "seconds": seconds,
        "repetitions": int(payload["repetitions"]) if payload.get("repetitions") is not None else None,
        "message": None,
    }
    if kind in _GROUPS:
        dest = payload.get("dest")
        if isinstance(dest, (list, tuple)):  # distribute: several destinations
            ev["dest"] = [_target(d) for d in dest]
        else:
            ev["dest"] = _target(dest)
        if kind == "mix":
            ev["target"] = _target(payload.get("location"))
    trash = _TRASH_RE.search(text)
    if trash and ev["target"] is not None and ev["target"].get("slot") is None:
        # Fixed trash is not in `loaded_labwares`; name it explicitly so consumers can draw it.
        ev["target"] = {"slot": trash.group(1), "well": None, "labware": "trash", "point_mm": ev["target"].get("point_mm")}
    if kind == "pause":
        ev["message"] = str(payload.get("userMessage", text.split(":", 1)[-1].strip()))
    elif kind == "comment":
        ev["message"] = text
    return ev


def _labware_kind(load_name: str, definition_params: dict[str, Any] | None) -> str:
    n = load_name.lower()
    if "tiprack" in n:
        return "tiprack"
    if "reservoir" in n:
        return "reservoir"
    if "tuberack" in n or "tube" in n:
        return "tubes"
    if "plate" in n or "block" in n:
        return "plate"
    return "other"


def _deck_snapshot(protocol_path: str, api_level: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute run() once more against a live context to read deck geometry."""
    from opentrons import simulate

    from opentrons.protocol_api.labware import Well

    ns: dict[str, Any] = {"__name__": "vaxforge_protocol"}
    with open(protocol_path, encoding="utf-8") as fh:
        exec(compile(fh.read(), protocol_path, "exec"), ns)  # noqa: S102 - see SECURITY note
    ctx = simulate.get_protocol_api(api_level, robot_type="OT-2")

    # Record `well.load_liquid(...)` declarations: a protocol that declares what is on the
    # deck is self-describing, so graders can read initial volumes straight from it.
    liquids: list[dict[str, Any]] = []
    original = Well.load_liquid

    def recording(self: Any, liquid: Any, volume: Any) -> Any:
        liquids.append({
            "slot": _slot_of_labware(self.parent),
            "well": self.well_name,
            "labware": getattr(self.parent, "load_name", None),
            "liquid": getattr(liquid, "name", None),
            "volume_ul": _num(volume),
        })
        return original(self, liquid, volume)

    Well.load_liquid = recording
    try:
        ns["run"](ctx)
    finally:
        Well.load_liquid = original

    module_by_slot = {}
    modules = []
    for slot, mod in ctx.loaded_modules.items():
        model = str(getattr(mod, "model", ""))
        name = _MODULE_NAMES.get(model, model or type(mod).__name__)
        modules.append({"slot": str(slot), "name": name, "model": model})
        module_by_slot[str(slot)] = name

    labware = []
    for slot, lw in ctx.loaded_labwares.items():
        wells: dict[str, Any] = {}
        for w in lw.wells():
            top = w.top().point
            diameter = _num(getattr(w, "diameter", None))
            length = _num(getattr(w, "length", None))
            width = _num(getattr(w, "width", None))
            wells[w.well_name] = {
                "x": round(float(top.x), 3),
                "y": round(float(top.y), 3),
                "z_top": round(float(top.z), 3),
                "depth": _num(getattr(w, "depth", None)),
                "shape": "circular" if diameter else "rectangular",
                "diameter": diameter,
                "length": length,
                "width": width,
                "max_volume_ul": _num(getattr(w, "max_volume", None)),
            }
        first = lw.wells()[0].top().point
        labware.append({
            "slot": str(slot),
            "load_name": lw.load_name,
            "display_name": getattr(lw, "name", lw.load_name),
            "uri": lw.uri,
            "module": module_by_slot.get(str(slot)),
            "kind": _labware_kind(lw.load_name, None),
            "origin_mm": [round(float(first.x), 3), round(float(first.y), 3), round(float(first.z), 3)],
            "wells": wells,
        })

    pipettes = []
    for mount, p in ctx.loaded_instruments.items():
        pipettes.append({
            "mount": str(mount),
            "name": p.name,
            "channels": int(getattr(p.channels, "value", p.channels)),
            "min_volume_ul": _num(p.min_volume),
            "max_volume_ul": _num(p.max_volume),
        })
    # OT-2 fixed trash: slot 12, back-right. Not part of `loaded_labwares`.
    return {"labware": labware, "modules": modules, "pipettes": pipettes, "trash": {"slot": "12"}}, liquids


def run(protocol_path: str) -> dict[str, Any]:
    import opentrons
    from opentrons import simulate

    result: dict[str, Any] = {
        "schema": SCHEMA,
        "ok": False,
        "error": None,
        "opentrons_version": getattr(opentrons, "__version__", "unknown"),
        "protocol_file": os.path.basename(protocol_path),
        "api_level": None,
        "deck": {"labware": [], "modules": [], "pipettes": [], "trash": {"slot": "12"}},
        "liquids": [],
        "events": [],
    }
    try:
        with open(protocol_path, encoding="utf-8") as fh:
            runlog, _bundle = simulate.simulate(fh)
        result["events"] = [_normalise_event(i, e) for i, e in enumerate(runlog)]
        ns: dict[str, Any] = {}
        with open(protocol_path, encoding="utf-8") as fh:
            exec(compile(fh.read(), protocol_path, "exec"), ns)  # noqa: S102
        req = ns.get("requirements") or {}
        meta = ns.get("metadata") or {}
        api_level = str(req.get("apiLevel") or meta.get("apiLevel") or "2.19")
        result["api_level"] = api_level
        result["deck"], result["liquids"] = _deck_snapshot(protocol_path, api_level)
        result["ok"] = True
    except BaseException as exc:  # noqa: BLE001 - report every failure as data
        kind, text = summarize_exception(exc)
        result["error"] = {"type": kind, "message": text, "wrapper": type(exc).__name__}
        if os.environ.get("VAXFORGE_DEBUG"):  # tracebacks embed absolute local paths; opt-in only
            result["error"]["traceback_tail"] = traceback.format_exc().splitlines()[-6:]
    return result


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__)
        return 2
    out = run(argv[1])
    with open(argv[2], "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=1, ensure_ascii=False)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
