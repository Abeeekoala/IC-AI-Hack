"""S9 grader: everything that judges a generated OT-2 protocol, usable on ANY protocol file.

This is the objective grader that README section S9.5 ("fine-tuned codegen") reuses offline:
`validation/winrate.py` can import it instead of re-implementing it. Layers, in the order
a win/loss decision uses them:

  1. static_lint       refuse to execute code that could touch the host (defence in depth,
                       NOT a sandbox - the simulator runs in a separate interpreter with a timeout)
  2. simulate          run the protocol in `opentrons_simulate` 7.5.0 (a subprocess; Opentrons
                       pins pydantic<2 and needs Python 3.10, so it cannot live in this process)
  3. check_physical_rules   Text2WetLab's five physical-safety rules, replayed over the run:
        tip_before_aspirate, no_overdispense, no_aspirate_from_empty_well,
        tip_dropped_at_end, no_cross_contamination
  4. plan_findings     (optional) compare the run with the S9 plan it was generated from
  5. reference_diff    (optional) compare the run with a reference run: labware, final well
                       volumes, and step order

Rule semantics follow Text2WetLab `eval/spec_check.py` (MIT) so a number from this module
means the same thing as the benchmark's. The implementation here is our own.

`simulation passing is not correctness` (README S9): a PASS verdict means the code ran and
broke none of the rules above. It does not mean the protocol is right for the experiment.
"""

from __future__ import annotations

import ast
import functools
import json
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field

from vaxforge.s9_plan import SessionPlan, expected_dispensed

RULES = (
    "tip_before_aspirate",
    "no_overdispense",
    "no_aspirate_from_empty_well",
    "tip_dropped_at_end",
    "no_cross_contamination",
)

SIM_SCHEMA = "vaxforge.ot_sim/1"
_RUNNER = Path(__file__).with_name("_ot_sim_runner.py")
_EPS = 1e-9
_VOL_TOL = 1e-9  # uL of slack in the two volume rules: spec_check's own (`vol > held + 1e-9`, `wells < vol - 1e-9`)


# --------------------------------------------------------------------------- 1. static lint

ALLOWED_IMPORTS = {"opentrons", "math", "typing", "itertools", "string", "collections", "functools", "dataclasses", "__future__"}
BANNED_NAMES = {
    "open", "exec", "eval", "compile", "globals", "locals", "vars", "getattr", "setattr", "delattr", "hasattr", "__import__",
    "SystemExit", "KeyboardInterrupt", "BaseException", "input", "breakpoint", "exit", "quit", "type", "memoryview", "help", "dir",
}
BANNED_ATTRS = {
    "os", "sys", "subprocess", "builtins", "importlib", "system", "popen", "modules", "environ", "stdout", "stderr", "stdin",
    "write", "writelines", "exit", "getenv", "open", "read_text", "write_text", "unlink", "remove",
}
# Documented pipette settings that are ordinary protocol code, not patching.
_PIPETTE_SETTINGS = {"tip_racks", "starting_tip", "default_speed"}


def _is_pipette_setting(t: ast.Attribute) -> bool:
    if isinstance(t.value, ast.Name):
        return t.attr in _PIPETTE_SETTINGS
    return (
        isinstance(t.value, ast.Attribute)
        and isinstance(t.value.value, ast.Name)
        and (
            (t.value.attr == "flow_rate" and t.attr in {"aspirate", "dispense", "blow_out"})
            or (t.value.attr == "well_bottom_clearance" and t.attr in {"aspirate", "dispense"})
        )
    )


def static_lint(source: str) -> list[str]:
    """Reasons NOT to execute `source`. Empty list = may be simulated."""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"syntax error: {exc.msg} (line {exc.lineno})"]
    out: set[str] = set()
    for node in ast.walk(tree):
        line = getattr(node, "lineno", 0)
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name.split(".")[0] not in ALLOWED_IMPORTS:
                    out.add(f"line {line}: import {a.name}")
        elif isinstance(node, ast.ImportFrom):
            if (node.module or "").split(".")[0] not in ALLOWED_IMPORTS or node.level:
                out.add(f"line {line}: from {node.module} import ...")
        elif isinstance(node, ast.Name) and node.id in BANNED_NAMES:
            out.add(f"line {line}: name {node.id}")
        elif isinstance(node, ast.Attribute):
            if node.attr.startswith("_") or node.attr in BANNED_ATTRS:
                out.add(f"line {line}: attribute .{node.attr}")
        elif isinstance(node, (ast.Assign, ast.AugAssign, ast.AnnAssign, ast.Delete, ast.For, ast.withitem)):
            if isinstance(node, (ast.Assign, ast.Delete)):
                targets = node.targets
            elif isinstance(node, ast.withitem):
                targets = [node.optional_vars]
            else:
                targets = [node.target]
            attrs = [a for t in targets if t is not None for a in ast.walk(t)
                     if isinstance(a, ast.Attribute) and not isinstance(a.ctx, ast.Load)]
            if any(not (isinstance(node, ast.Assign) and _is_pipette_setting(a)) for a in attrs):
                out.add(f"line {line}: assignment to an attribute")
    return sorted(out)


_API_RE = re.compile(r"""["']apiLevel["']\s*:\s*["'](\d+)\.(\d+)["']""")


def structure_findings(source: str) -> list["Finding"]:
    """Cheap structural checks a human reviewer would do first."""
    f: list[Finding] = []
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return f
    runs = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "run"]
    if not runs:
        f.append(Finding(severity="error", rule="structure", message="no `def run(ctx)` entry point"))
    elif not runs[0].args.args:
        f.append(Finding(severity="error", rule="structure", message="`run` takes no protocol-context argument"))
    m = _API_RE.search(source)
    if not m:
        f.append(Finding(severity="error", rule="structure", message="no apiLevel declared in `requirements`/`metadata`"))
    elif (int(m.group(1)), int(m.group(2))) > (2, 19):
        f.append(Finding(severity="error", rule="structure", message=f"apiLevel {m.group(1)}.{m.group(2)} is above 2.19, the maximum Opentrons 7.5.0 supports"))
    return f


# --------------------------------------------------------------------------- 2. simulate


class Finding(BaseModel):
    severity: Literal["error", "warning"]
    rule: str
    message: str
    event_idx: int | None = None


def _probe(python: str) -> bool:
    try:
        r = subprocess.run([python, "-c", "import opentrons"], capture_output=True, timeout=120)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


@functools.lru_cache(maxsize=1)
def find_ot_python() -> str | None:
    """Interpreter with `opentrons` 7.x installed, or None.

    Order: $VAXFORGE_OT_PYTHON, this interpreter, then conventional venv locations near the repo.
    """
    env = os.environ.get("VAXFORGE_OT_PYTHON")
    if env:
        return env if _probe(env) else None
    candidates = [sys.executable]
    here = Path(__file__).resolve()
    for root in (Path.cwd(), *here.parents[:4]):
        for venv in (".venv-ot", ".venv-wetlab"):
            candidates += [str(root / venv / "Scripts" / "python.exe"), str(root / venv / "bin" / "python")]
    seen: set[str] = set()
    for c in candidates:
        if c in seen or not Path(c).exists():
            continue
        seen.add(c)
        if _probe(c):
            return c
    return None


# A variable whose upper-cased name contains one of these never reaches the process that runs model-written code.
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "CREDENTIAL", "ANTHROPIC", "OPENAI", "AWS_", "AZURE_", "GITHUB", "GH_")


def _scrubbed_env(base: Mapping[str, str] | None = None) -> dict[str, str]:
    """The environment for the simulator subprocess, which EXECUTES model-written code.

    A copy of `base` (default: this process's environment) without every variable whose upper-cased name contains one of
    `_SECRET_MARKERS`: API keys and tokens in the parent are not visible to that code. Everything else is kept, because
    Python and Opentrons need PATH, SYSTEMROOT, TEMP, HOME, USERPROFILE and the like (on Windows above all), and
    PYTHONIOENCODING is set to utf-8. `base` is not modified. Defence in depth, like `static_lint`, not a sandbox.
    """
    source = os.environ if base is None else base
    env = {name: value for name, value in source.items() if not any(marker in name.upper() for marker in _SECRET_MARKERS)}
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def simulate(code: str | Path, *, ot_python: str | None = None, timeout: int = 300, lint: bool = True) -> dict[str, Any]:
    """Simulate a protocol (source text or path). Always returns the sim-JSON dict
    (schema `vaxforge.ot_sim/1`); failures are `ok: false` with an `error`, never exceptions.

    The subprocess that executes the protocol gets `_scrubbed_env()`, not the full environment of this process.
    """
    source = Path(code).read_text(encoding="utf-8") if isinstance(code, Path) else code
    if lint:
        problems = static_lint(source)
        if problems:
            return _failed("LintError", "; ".join(problems[:5]))
    python = ot_python or find_ot_python()
    if python is None:
        return _failed("SimulatorUnavailable", "no interpreter with opentrons installed; set VAXFORGE_OT_PYTHON (Opentrons 7.5.0 needs Python 3.10)")
    with tempfile.TemporaryDirectory(prefix="vaxforge_sim_") as tmp:
        proto = Path(tmp, "protocol.py")
        proto.write_text(source, encoding="utf-8")
        out = Path(tmp, "result.json")  # a file, not stdout: a protocol can print a forged result
        try:
            proc = subprocess.run([python, str(_RUNNER), str(proto), str(out)], capture_output=True, text=True,
                                  timeout=timeout, env=_scrubbed_env(), cwd=tmp)
        except subprocess.TimeoutExpired:
            return _failed("Timeout", f"simulation exceeded {timeout}s")
        except OSError as exc:
            return _failed("SimulatorUnavailable", str(exc))
        if out.exists():
            try:
                return json.loads(out.read_text(encoding="utf-8"))
            except json.JSONDecodeError as exc:
                return _failed("BadSimulatorOutput", str(exc))
        tail = (proc.stderr.strip().splitlines() or ["runner produced no result"])[-1]
        return _failed("RunnerError", tail[:500])


def _failed(kind: str, message: str) -> dict[str, Any]:
    return {"schema": SIM_SCHEMA, "ok": False, "error": {"type": kind, "message": message}, "events": [],
            "deck": {"labware": [], "modules": [], "pipettes": [], "trash": {"slot": "12"}}, "liquids": []}


# --------------------------------------------------------------------------- 3. five rules


def _is_stock(load_name: str | None) -> bool:
    n = (load_name or "").lower()
    return "reservoir" in n or "trough" in n


def _key(t: dict[str, Any] | None) -> tuple[str, str] | None:
    if not t or t.get("well") is None:
        return None
    return (str(t.get("slot")), str(t["well"]))


def _cells(event: dict[str, Any]) -> list[tuple[tuple[str, str], int]]:
    """Wells one aspirate/dispense touches, with multiplicity (8-channel heads cover a plate column)."""
    t = event.get("target") or {}
    key = _key(t)
    if key is None:
        return []
    channels = int((event.get("pipette") or {}).get("channels") or 1)
    if channels > 1 and not _is_stock(t.get("labware")) and key[1][0] == "A":
        return [((key[0], f"{r}{key[1][1:]}"), 1) for r in "ABCDEFGH"[:channels]]
    return [(key, channels if channels > 1 else 1)]


def leaf_events(sim: dict[str, Any]) -> list[dict[str, Any]]:
    """The atomic pipetting events, in order (group headers removed).

    The aspirate that is the sub-step of an `Air gap` command draws air, not liquid. It comes back as a copy with kind
    "air_gap" (`sim` is not modified): the tip holds that volume afterwards, but no well loses it and the tip has not
    visited the well it hovers over. Text2WetLab's runlog sets these aside the same way (kind "air").
    """
    out: list[dict[str, Any]] = []
    latest: dict[int, dict[str, Any]] = {}  # level -> the latest event at that level, which is the parent of a deeper next one
    for e in sim.get("events", []):
        level = int(e.get("level") or 0)
        parent = latest.get(level - 1) if level > 0 else None
        for deeper in [k for k in latest if k >= level]:
            del latest[deeper]
        latest[level] = e
        if e["kind"] == "aspirate" and parent is not None and str(parent.get("text") or "").startswith("Air gap"):
            out.append({**e, "kind": "air_gap"})
        elif e["kind"] in ("pick_up_tip", "drop_tip", "aspirate", "dispense"):
            out.append(e)
    return out


class Violation(BaseModel):
    rule: str
    event_idx: int
    detail: str = ""


class RuleReport(BaseModel):
    violations: dict[str, list[Violation]] = Field(default_factory=lambda: {r: [] for r in RULES})
    other: list[Violation] = Field(default_factory=list)  # e.g. picking up a tip while holding one
    aspirations: int = 0

    @property
    def counts(self) -> dict[str, int]:
        return {r: len(v) for r, v in self.violations.items()}

    @property
    def total(self) -> int:
        return sum(self.counts.values()) + len(self.other)


def initial_from_liquids(sim: dict[str, Any]) -> dict[tuple[str, str], float]:
    """Initial well contents a protocol declared with `well.load_liquid(...)`."""
    out: dict[tuple[str, str], float] = {}
    for liq in sim.get("liquids", []):
        if liq.get("slot") is not None and liq.get("volume_ul") is not None:
            out[(str(liq["slot"]), str(liq["well"]))] = float(liq["volume_ul"])
    return out


def cross_contamination(events: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """(event idx, well) where one tip carries liquid between wells it should not.

    A tip may return to wells it dispensed into (mixing) and to the one well it started from
    (multi-dispense). Once it has drawn from a destination it dispensed into, it carries that
    well's contents: dispensing elsewhere or drawing from any other well is contamination until
    a fresh tip. Mixing in a SOURCE before drawing from it carries nothing the transfer does
    not mean to move. (Semantics: Text2WetLab `cross_contamination`.)
    """
    bad: list[tuple[int, str]] = []
    sources: set[tuple[str, str]] = set()
    own: set[tuple[str, str]] = set()
    carried: set[tuple[str, str]] = set()
    for e in events:
        kind = e["kind"]
        if kind in ("pick_up_tip", "drop_tip"):
            sources, own, carried = set(), set(), set()
            continue
        key = _key(e.get("target"))
        if key is None:
            continue
        if kind == "dispense":
            if carried - {key}:
                bad.append((e["idx"], key[1]))
            own.add(key)
        elif kind == "aspirate":
            if (sources and key not in sources and key not in own) or carried - {key}:
                bad.append((e["idx"], key[1]))
            if key in own and key not in sources:
                carried.add(key)
            elif key not in own:
                sources.add(key)
    return bad


def check_physical_rules(sim: dict[str, Any], initial: dict[tuple[str, str], float] | None = None) -> RuleReport:
    """Replay a simulated run against the five physical rules.

    `initial` maps (slot, well) -> uL for wells that hold liquid at the start. When None it is
    read from the protocol's own `load_liquid` declarations. Reservoir wells are treated as
    ample stocks UNLESS they appear in `initial` (then they are tracked like any other well).
    """
    events = leaf_events(sim)
    initial = initial if initial is not None else initial_from_liquids(sim)
    wells: dict[tuple[str, str], float] = dict(initial)
    declared = frozenset(initial)  # a reservoir well is tracked only if declared; dispensing into one (a mix) does not declare it
    rep = RuleReport()
    tip, held = False, 0.0
    for e in events:
        kind, idx = e["kind"], e["idx"]
        if kind == "pick_up_tip":
            if tip:
                rep.other.append(Violation(rule="tip_state", event_idx=idx, detail="picked up a tip while one is attached"))
            tip, held = True, 0.0
        elif kind == "drop_tip":
            tip, held = False, 0.0
        elif kind == "air_gap":
            if tip:
                held += float(e.get("volume_ul") or 0.0)  # air in the tip: it may be dispensed, and no well loses it
        else:
            vol = float(e.get("volume_ul") or 0.0)
            if not tip:
                rep.violations["tip_before_aspirate"].append(Violation(rule="tip_before_aspirate", event_idx=idx, detail=e["text"][:80]))
                continue
            t = e.get("target") or {}
            if kind == "aspirate":
                rep.aspirations += 1
                for key, mult in _cells(e):
                    tracked = (not _is_stock(t.get("labware"))) or key in declared
                    if not tracked:
                        continue
                    if wells.get(key, 0.0) < vol * mult - _VOL_TOL:
                        rep.violations["no_aspirate_from_empty_well"].append(
                            Violation(rule="no_aspirate_from_empty_well", event_idx=idx,
                                      detail=f"{key[1]} on slot {key[0]} holds {wells.get(key, 0.0):g} uL, asked for {vol * mult:g}"))
                        break
                    wells[key] = wells.get(key, 0.0) - vol * mult
                held += vol
            else:
                if vol > held + _VOL_TOL:
                    rep.violations["no_overdispense"].append(
                        Violation(rule="no_overdispense", event_idx=idx, detail=f"dispensing {vol:g} uL with {held:g} uL in the tip"))
                for key, mult in _cells(e):
                    wells[key] = wells.get(key, 0.0) + vol * mult
                held = max(held - vol, 0.0)
    if tip:
        rep.violations["tip_dropped_at_end"].append(Violation(rule="tip_dropped_at_end", event_idx=events[-1]["idx"] if events else -1,
                                                              detail="a tip is still attached when the protocol ends"))
    for idx, well in cross_contamination(events):
        rep.violations["no_cross_contamination"].append(Violation(rule="no_cross_contamination", event_idx=idx, detail=f"well {well}"))
    return rep


# --------------------------------------------------------------------------- 4. plan comparison


def actual_dispensed(sim: dict[str, Any]) -> dict[tuple[str, str], float]:
    """Net volume each (slot, well) received from NON-mix dispenses, in uL."""
    out: dict[tuple[str, str], float] = {}
    mix_level: int | None = None
    for e in sim.get("events", []):
        lvl = e["level"]
        if mix_level is not None and lvl <= mix_level:
            mix_level = None
        if e["kind"] == "mix" and mix_level is None:
            mix_level = lvl
            continue
        if mix_level is not None or e["kind"] != "dispense":
            continue
        vol = float(e.get("volume_ul") or 0.0)
        for key, mult in _cells(e):
            out[key] = out.get(key, 0.0) + vol * mult
    return {k: round(v, 6) for k, v in out.items()}


def plan_findings(plan: SessionPlan, sim: dict[str, Any], tol_ul: float = 0.01) -> list[Finding]:
    """Compare a simulated run with the plan it was meant to implement."""
    f: list[Finding] = []
    deck = sim.get("deck", {})
    have = {(l["slot"], l["load_name"]) for l in deck.get("labware", [])}
    for lw in plan.labware:
        if (lw.slot, lw.load_name) not in have:
            f.append(Finding(severity="error", rule="plan_deck", message=f"plan expects {lw.load_name} in slot {lw.slot}; the run does not load it there"))
    have_mod = {(m["slot"], m["name"]) for m in deck.get("modules", [])}
    for m in plan.modules:
        if (m.slot, m.name) not in have_mod:
            f.append(Finding(severity="error", rule="plan_deck", message=f"plan expects a {m.name} in slot {m.slot}"))
    want, got = expected_dispensed(plan), actual_dispensed(sim)
    for key in sorted(set(want) | set(got)):
        w, g = want.get(key, 0.0), got.get(key, 0.0)
        if abs(w - g) > tol_ul:
            f.append(Finding(severity="error", rule="plan_volume",
                             message=f"slot {key[0]} well {key[1]}: plan dispenses {w:g} uL into it, the run dispenses {g:g} uL"))
    declared = initial_from_liquids(sim)
    for liq in plan.liquids:
        key = (plan.slot_of(liq.ref.labware), liq.ref.well)
        if abs(declared.get(key, -1.0) - liq.volume_ul) > tol_ul:
            f.append(Finding(severity="error", rule="plan_liquids",
                             message=f"initial content of slot {key[0]} well {key[1]} ({liq.reagent}): plan {liq.volume_ul:g} uL, protocol declares {declared.get(key, 'nothing')}"))
    pauses = [e for e in sim.get("events", []) if e["kind"] == "pause"]
    want_pauses = [a for a in plan.actions if a.op == "pause"]
    if len(pauses) != len(want_pauses):
        f.append(Finding(severity="error", rule="plan_handoffs",
                         message=f"plan has {len(want_pauses)} human hand-off pause(s); the run has {len(pauses)}"))
    comments = [e.get("message") or "" for e in sim.get("events", []) if e["kind"] == "comment"]
    for sid in plan.covers:
        if not any(c.startswith(f"[{sid}]") for c in comments):
            f.append(Finding(severity="warning", rule="annotation", message=f"no `[{sid}] title | OWNER | note` annotation comment in the run"))
    return f


def pipette_range_findings(sim: dict[str, Any]) -> list[Finding]:
    """Aspirate/dispense volumes outside the loaded pipette's range (a warning; the simulator may accept them)."""
    f: list[Finding] = []
    rng = {p["mount"]: (p["min_volume_ul"], p["max_volume_ul"]) for p in sim.get("deck", {}).get("pipettes", [])}
    for e in leaf_events(sim):
        if e["kind"] not in ("aspirate", "dispense") or not e.get("pipette"):
            continue
        lo, hi = rng.get(e["pipette"]["mount"], (None, None))
        v = e.get("volume_ul")
        if v is not None and lo is not None and (v < lo - _EPS or v > hi + _EPS):
            f.append(Finding(severity="warning", rule="pipette_range", message=f"{e['kind']} {v:g} uL outside {lo:g}-{hi:g} uL", event_idx=e["idx"]))
    return f


def temperature_findings(sim: dict[str, Any]) -> list[Finding]:
    """A temperature module left hot/cold at the end is a warning."""
    last: float | None = None
    for e in sim.get("events", []):
        if e["kind"] == "temperature_set" and e.get("celsius") is not None:
            last = float(e["celsius"])
        elif e["kind"] == "temperature_deactivate":
            last = None
    if last is not None and last > 40:
        return [Finding(severity="warning", rule="temperature", message=f"temperature module left at {last:g} C at the end of the run")]
    return []


# --------------------------------------------------------------------------- 5. reference diff


def _signature(sim: dict[str, Any]) -> list[tuple[str, str, str, float]]:
    seq: list[tuple[str, str, str, float]] = []
    for e in leaf_events(sim):
        if e["kind"] in ("aspirate", "dispense"):
            k = _key(e.get("target"))
            if k:
                seq.append((e["kind"], k[0], k[1], float(e.get("volume_ul") or 0.0)))
    return seq


def _edit_distance(a: list, b: list) -> int:
    prev = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        cur = [i]
        for j, y in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (x != y)))
        prev = cur
    return prev[-1]


def final_volumes(sim: dict[str, Any], initial: dict[tuple[str, str], float] | None = None) -> dict[tuple[str, str], float]:
    """Net well volumes after the run, replayed from `initial` (default: the protocol's declared contents)."""
    wells = dict(initial if initial is not None else initial_from_liquids(sim))
    for e in leaf_events(sim):
        if e["kind"] not in ("aspirate", "dispense"):
            continue
        vol = float(e.get("volume_ul") or 0.0)
        sign = -1.0 if e["kind"] == "aspirate" else 1.0
        for key, mult in _cells(e):
            wells[key] = wells.get(key, 0.0) + sign * vol * mult
    return {k: round(v, 6) for k, v in wells.items() if abs(v) > 1e-9}


def reference_diff(sim: dict[str, Any], reference: dict[str, Any]) -> dict[str, Any]:
    """How far a run is from a reference run, on the three things the S9.5 rule 3 names.

    labware       entries in one deck and not the other, matched on (slot, load_name)
    volumes       sum over wells of |final volume difference|, in uL, and the wells that differ
    step_order    edit distance between the (kind, slot, well, volume) event sequences,
                  and that distance divided by the longer sequence
    Smaller is closer. Identical runs give zeros.
    """
    a = {(l["slot"], l["load_name"]) for l in sim.get("deck", {}).get("labware", [])}
    b = {(l["slot"], l["load_name"]) for l in reference.get("deck", {}).get("labware", [])}
    fa, fb = final_volumes(sim), final_volumes(reference)
    wells = set(fa) | set(fb)
    vol_delta = {k: round(abs(fa.get(k, 0.0) - fb.get(k, 0.0)), 6) for k in wells if abs(fa.get(k, 0.0) - fb.get(k, 0.0)) > 1e-6}
    sa, sb = _signature(sim), _signature(reference)
    dist = _edit_distance(sa, sb)
    return {
        "labware_mismatches": len(a ^ b),
        "labware_only_in_run": sorted(map(list, a - b)),
        "labware_only_in_reference": sorted(map(list, b - a)),
        "volume_abs_diff_ul": round(sum(vol_delta.values()), 6),
        "wells_with_different_volume": len(vol_delta),
        "step_order_edit_distance": dist,
        "step_order_normalised": round(dist / max(len(sa), len(sb), 1), 6),
    }


# --------------------------------------------------------------------------- one-stop grade


class Grade(BaseModel):
    verdict: Literal["PASS", "FAIL", "UNVERIFIED"]
    lint: list[str] = Field(default_factory=list)
    sim_ok: bool | None = None  # None: the simulation was not run
    sim_error: str | None = None
    sim_error_kind: str | None = None  # e.g. TipNotAttachedError, TypeError, LintError
    rule_counts: dict[str, int] = Field(default_factory=lambda: {r: 0 for r in RULES})
    n_rule_violations: int = 0
    findings: list[Finding] = Field(default_factory=list)
    reference_diff: dict[str, Any] | None = None
    n_events: int = 0
    note: str = "A PASS means the code ran and broke none of the checked rules; it does not mean the protocol is right for the experiment."


def grade_source(
    source: str,
    *,
    plan: SessionPlan | None = None,
    initial: dict[tuple[str, str], float] | None = None,
    reference_sim: dict[str, Any] | None = None,
    ot_python: str | None = None,
    run_simulation: bool = True,
    timeout: int = 300,
) -> tuple[Grade, dict[str, Any] | None]:
    """Grade protocol source end to end. Returns (Grade, sim JSON or None)."""
    lint = static_lint(source)
    findings = structure_findings(source)
    if lint:
        return Grade(verdict="FAIL", lint=lint, sim_ok=None, findings=findings), None
    python = ot_python or find_ot_python()
    if not run_simulation or python is None:
        why = "simulation skipped" if not run_simulation else "no Opentrons interpreter available (set VAXFORGE_OT_PYTHON)"
        findings.append(Finding(severity="warning", rule="simulation", message=why))
        verdict = "FAIL" if any(f.severity == "error" for f in findings) else "UNVERIFIED"
        return Grade(verdict=verdict, sim_ok=None, findings=findings), None
    sim = simulate(source, ot_python=python, timeout=timeout, lint=False)
    if not sim.get("ok"):
        err = sim.get("error") or {}
        return Grade(verdict="FAIL", sim_ok=False, sim_error=f"{err.get('type')}: {err.get('message')}"[:600],
                     sim_error_kind=err.get("type"), findings=findings), sim
    rep = check_physical_rules(sim, initial)
    for rule, vs in rep.violations.items():
        for v in vs[:5]:
            findings.append(Finding(severity="error", rule=rule, message=v.detail, event_idx=v.event_idx))
    for v in rep.other:
        findings.append(Finding(severity="error", rule=v.rule, message=v.detail, event_idx=v.event_idx))
    findings += pipette_range_findings(sim) + temperature_findings(sim)
    if plan is not None:
        findings += plan_findings(plan, sim)
    diff = reference_diff(sim, reference_sim) if reference_sim is not None else None
    verdict = "FAIL" if any(f.severity == "error" for f in findings) else "PASS"
    return Grade(verdict=verdict, sim_ok=True, rule_counts=rep.counts, n_rule_violations=rep.total, findings=findings,
                 reference_diff=diff, n_events=len(sim.get("events", []))), sim
