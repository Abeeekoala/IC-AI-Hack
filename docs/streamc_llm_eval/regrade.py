#!/usr/bin/env python
"""Offline regrade of the saved LLM-eval protocols with the corrected five-rule check (task H7). No model call, no network.

`docs/streamc_llm_eval.md` (sections 1-9) was graded with `vaxforge.s9_validate` as it was BEFORE the comparison with
Text2WetLab (`docs/streamc_t2wl_agreement.md`) fixed four defects in `leaf_events` / `check_physical_rules`:

    air_volume   the aspirate under an `Air gap` command was drawn from the well it hovers over (it is air, held in the tip)
    air_visit    the same aspirate counted as a visit of that well by the tip (no_cross_contamination)
    reservoir    dispensing into an undeclared reservoir well (a mix) made it a tracked well
    tolerance    the two volume rules used 1e-6 uL of slack; Text2WetLab's spec_check uses 1e-9

What this script does
    1. reads the original attempt records (`attempts_{spec,free}_<model>.jsonl`: task id, attempted/refused, simulate_pass,
       rule_violations, per-rule counts in `detail`) and the original `results.json` (the PASS / FAIL / REFUSED verdict the report quotes);
    2. grades every saved protocol again with the two calls the original run made (see run_eval.py):
           spec  grade_source(code, plan=plan)   plan = the saved <session>.plan.json, loaded with the S9 plan model
           free  grade_source(code)              no plan
       The simulator logs (*.sim.json) were not kept, so every protocol is simulated again. The 7 refused attempts (spec / Haiku)
       produced no code: there is nothing to regrade, they are carried over as REFUSED and counted as such;
    3. compares outcome and per-rule violation counts, before against after, and lists every attempt that changed, with the reason;
    4. writes `regrade_results.json` and `regrade_results.md` next to this file (the originals are only read).

Safety. Generated code is untrusted: it only reaches `grade_source`, which lints it first and simulates it in a subprocess with a
scrubbed environment. This script never executes it, never imports `LLMBackend`, `vaxforge.s9_llm` or `anthropic`, opens no socket and
reads no API key (run_eval.py is not imported either, because it imports the LLM backend).

Attribution aid (not a grade). To say WHICH fix explains a change, `variant()` replays a simulated run with each of the four fixes
switched on or off (a copy of the pre-fix lines of `check_physical_rules`, from `git diff` of s9_validate.py). `--selftest` checks
it on hand-built logs, and every run checks that (a) with all fixes on it equals `check_physical_rules` and (b) with none on it
reproduces the ORIGINAL per-rule counts. If (b) fails for a program, the script says so instead of attributing.

If a tracked source file (the simulator runner, the grader, the plan model) changes on disk while a pass runs, the pass is repeated
once. A changed simulator runner is picked up by the repeat, so the numbers come from one version of it. The grader and the plan
model are imported once: a change to them needs a new process, and the integrity check says so. The output records the sha256 of
each file at the start and at the end of every pass.

Usage
    python docs/streamc_llm_eval/regrade.py              regrade, print the table, write regrade_results.json / .md
    python docs/streamc_llm_eval/regrade.py --selftest   offline check of the grader's four fixes and of the attribution aid
    python docs/streamc_llm_eval/regrade.py --workers 2  simulations in parallel (default 4, the original run's setting)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SRC = ROOT / "src" / "vaxforge"
sys.path.insert(0, str(ROOT / "src"))


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.exists() else None


GRADER_SHA256_AT_IMPORT = sha256_file(SRC / "s9_validate.py")  # hashed just before the import: the version this run grades with

from vaxforge import s9_validate as V  # noqa: E402  the grader under test, as it is in the working tree
from vaxforge.s9_plan import SessionPlan  # noqa: E402  the S9 plan model: loads the saved <session>.plan.json

MODES = ("spec", "free")
MODELS = ("claude-sonnet-5-5", "claude-haiku-5-5")
SHORT = {"claude-sonnet-5-5": "Sonnet", "claude-haiku-5-5": "Haiku"}
GROUP_N = {("spec", "claude-sonnet-5-5"): 13, ("spec", "claude-haiku-5-5"): 13, ("free", "claude-sonnet-5-5"): 12, ("free", "claude-haiku-5-5"): 12}
RULES = V.RULES
OUTCOMES = ("PASS", "FAIL", "REFUSED", "ERROR", "UNVERIFIED")
OLD_TOL, NEW_TOL = 1e-6, 1e-9
FIXES = ("air_volume", "air_visit", "reservoir", "tolerance")
FIX_TEXT = {
    "air_volume": "an air gap is air held in the tip, not liquid drawn from the well it hovers over",
    "air_visit": "an air gap is not a visit of that well by the tip (no_cross_contamination)",
    "reservoir": "a reservoir well is tracked only if its contents are declared; dispensing into it (a mix) no longer declares it",
    "tolerance": "the two volume rules use 1e-9 uL of slack (was 1e-6)",
}
# sim_error_kind values that say the harness failed, not the protocol: never a result of the model's code
HARNESS_KINDS = {"RunnerError", "BadSimulatorOutput", "SimulatorUnavailable", "Timeout"}
TRACKED = {"runner": SRC / "_ot_sim_runner.py", "grader": SRC / "s9_validate.py", "plan_model": SRC / "s9_plan.py"}

_KEYLIKE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}")


# --------------------------------------------------------------------------- small helpers


def scrub(text: object) -> str:
    """Text as stored: anything shaped like an API key is masked, the local checkout path is shortened (same as run_eval.scrub)."""
    return _KEYLIKE.sub("[redacted]", str(text)).replace(str(ROOT), "<repo>")


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def snapshot_hashes() -> dict[str, str | None]:
    return {name: sha256_file(p) for name, p in TRACKED.items()}


def ot_versions(python: str) -> dict[str, str]:
    """Python and Opentrons version of the simulator's interpreter (one quick subprocess, scrubbed environment)."""
    try:
        p = subprocess.run([python, "-c", "import sys, opentrons; print(sys.version.split()[0], opentrons.__version__)"],
                           capture_output=True, text=True, timeout=120, env=V._scrubbed_env())
        py, ot = (p.stdout.split() + ["?", "?"])[:2]
        return {"python": py, "opentrons": ot}
    except (OSError, subprocess.SubprocessError):
        return {"python": "?", "opentrons": "?"}


# --------------------------------------------------------------------------- the replay with the four fixes as switches


def replay(events: list[dict], initial: dict, *, air_volume: bool, reservoir: bool, tol: float) -> tuple[dict[str, int], int, list[float]]:
    """`check_physical_rules` line by line, minus the cross-contamination rule, with the air-gap / reservoir / tolerance fixes as switches.

    `events` is the CURRENT `leaf_events` (air gaps flagged as kind "air_gap"). With `air_volume` off an air gap is replayed as the
    ordinary aspirate it used to be. Returns (counts of four rules, number of "picked up a tip while one is attached", the slack of
    every volume check: uL available minus uL asked)."""
    wells = dict(initial)
    declared = frozenset(initial)
    counts = {r: 0 for r in RULES}
    other = 0
    tip, held = False, 0.0
    margins: list[float] = []
    for e in events:
        kind = e["kind"]
        if kind == "air_gap" and not air_volume:
            kind = "aspirate"
        if kind == "pick_up_tip":
            if tip:
                other += 1
            tip, held = True, 0.0
        elif kind == "drop_tip":
            tip, held = False, 0.0
        elif kind == "air_gap":
            if tip:
                held += float(e.get("volume_ul") or 0.0)
        else:
            vol = float(e.get("volume_ul") or 0.0)
            if not tip:
                counts["tip_before_aspirate"] += 1
                continue
            t = e.get("target") or {}
            if kind == "aspirate":
                for key, mult in V._cells(e):
                    tracked = (not V._is_stock(t.get("labware"))) or (key in declared if reservoir else key in wells)
                    if not tracked:
                        continue
                    margins.append(wells.get(key, 0.0) - vol * mult)
                    if wells.get(key, 0.0) < vol * mult - tol:
                        counts["no_aspirate_from_empty_well"] += 1
                        break
                    wells[key] = wells.get(key, 0.0) - vol * mult
                held += vol
            else:
                margins.append(held - vol)
                if vol > held + tol:
                    counts["no_overdispense"] += 1
                for key, mult in V._cells(e):
                    wells[key] = wells.get(key, 0.0) + vol * mult
                held = max(held - vol, 0.0)
    if tip:
        counts["tip_dropped_at_end"] += 1
    return counts, other, margins


def variant(sim: dict, initial: dict, fixes: set[str]) -> tuple[dict[str, int], int, list[float]]:
    """The five rule counts of one simulated run with exactly the fixes in `fixes` switched on (all four = the current check)."""
    events = V.leaf_events(sim)
    old_events = [{**e, "kind": "aspirate"} if e["kind"] == "air_gap" else e for e in events]  # what leaf_events used to return
    counts, other, margins = replay(events, initial, air_volume="air_volume" in fixes, reservoir="reservoir" in fixes,
                                    tol=NEW_TOL if "tolerance" in fixes else OLD_TOL)
    counts["no_cross_contamination"] = len(V.cross_contamination(events if "air_visit" in fixes else old_events))
    return counts, other, margins


# --------------------------------------------------------------------------- self-test on hand-built logs (the repo tests' event shapes)

PLATE = "nest_96_wellplate_200ul_flat"
TUBES = "opentrons_24_tuberack_nest_1.5ml_snapcap"
RES = "nest_12_reservoir_15ml"


def _ev(idx, kind, slot=None, well=None, vol=None, labware=PLATE, level=0, text=None):
    return {"idx": idx, "level": level, "kind": kind, "is_group": False, "text": text or f"{kind} {well}",
            "pipette": {"mount": "right", "name": "p300_single_gen2", "channels": 1}, "volume_ul": vol, "flow_rate_ul_s": None,
            "target": {"slot": slot, "well": well, "labware": labware, "point_mm": None} if slot else None,
            "source": None, "dest": None, "celsius": None, "seconds": None, "repetitions": None, "message": None}


def _liq(slot, well, ul, labware=TUBES):
    return {"slot": slot, "well": well, "labware": labware, "liquid": "x", "volume_ul": ul}


def _sim(events, liquids=()):
    return {"ok": True, "events": events, "liquids": list(liquids), "deck": {"labware": [], "modules": [], "pipettes": [], "trash": {"slot": "12"}}}


def _air_gap(idx, slot, well, vol, labware=PLATE):
    """The two log entries of pipette.air_gap(vol): the 'Air gap' command and the aspirate that is its sub-step."""
    return [_ev(idx, "comment", text="Air gap"), _ev(idx + 1, "aspirate", slot, well, vol, labware, level=1)]


def _cases() -> list[tuple[str, dict, dict, dict, list[str]]]:
    """(name, sim, nonzero counts BEFORE the fixes, nonzero counts NOW, the fixes that change the pre-fix counts)."""
    tip, drop = (lambda i: _ev(i, "pick_up_tip", labware="tiprack")), (lambda i: _ev(i, "drop_tip"))
    nae, cc, od = "no_aspirate_from_empty_well", "no_cross_contamination", "no_overdispense"
    five_ul = 5e-7
    return [
        ("air gap over a nearly empty tube",
         _sim([tip(0), _ev(1, "aspirate", "2", "A1", 50, TUBES), *_air_gap(2, "2", "A1", 20, TUBES), _ev(4, "dispense", "3", "A1", 70), drop(5)],
              [_liq("2", "A1", 60)]), {nae: 1}, {}, ["air_volume"]),
        ("air gap above a destination well",
         _sim([tip(0), _ev(1, "aspirate", "2", "A1", 50, TUBES), _ev(2, "dispense", "3", "A1", 50), *_air_gap(3, "3", "A1", 10),
               _ev(5, "aspirate", "2", "A1", 50, TUBES), _ev(6, "dispense", "3", "B1", 50), drop(7)], [_liq("2", "A1", 500)]),
         {cc: 2}, {}, ["air_visit"]),
        ("mix in an undeclared reservoir, then draw 200 uL from it",
         _sim([tip(0), _ev(1, "aspirate", "5", "A1", 50, RES), _ev(2, "dispense", "5", "A1", 50, RES), _ev(3, "aspirate", "5", "A1", 200, RES),
               _ev(4, "dispense", "3", "A1", 200), drop(5)]), {nae: 1}, {}, ["reservoir"]),
        ("the same, but the reservoir well is declared with 100 uL (still tracked)",
         _sim([tip(0), _ev(1, "aspirate", "5", "A1", 50, RES), _ev(2, "dispense", "5", "A1", 50, RES), _ev(3, "aspirate", "5", "A1", 200, RES),
               _ev(4, "dispense", "3", "A1", 200), drop(5)], [_liq("5", "A1", 100, RES)]), {nae: 1}, {nae: 1}, []),
        ("dispense 5e-7 uL more than the tip holds",
         _sim([tip(0), _ev(1, "aspirate", "2", "A1", 100, TUBES), _ev(2, "dispense", "3", "A1", 100 + five_ul), drop(3)], [_liq("2", "A1", 1000)]),
         {}, {od: 1}, ["tolerance"]),
        ("aspirate 5e-7 uL more than the well holds",
         _sim([tip(0), _ev(1, "aspirate", "2", "A1", 100 + five_ul, TUBES), _ev(2, "dispense", "3", "A1", 100 + five_ul), drop(3)], [_liq("2", "A1", 100)]),
         {}, {nae: 1}, ["tolerance"]),
        ("clean run",
         _sim([tip(0), _ev(1, "aspirate", "2", "A1", 50, TUBES), _ev(2, "dispense", "3", "A1", 50), drop(3)], [_liq("2", "A1", 100)]), {}, {}, []),
    ]


def selftest(verbose: bool = False) -> dict[str, Any]:
    """`grader_is_corrected`: check_physical_rules gives the corrected counts on the cases; `replica_ok`: the attribution aid matches."""
    def nonzero(c: dict[str, int]) -> dict[str, int]:
        return {k: v for k, v in c.items() if v}

    grader_ok = replica_ok = True
    lines = []
    for name, sim, want_before, want_now, want_fixes in _cases():
        initial = V.initial_from_liquids(sim)
        prod = V.check_physical_rules(sim)
        now_c, now_o, _ = variant(sim, initial, set(FIXES))
        old_c, old_o, _ = variant(sim, initial, set())
        explained = [fx for fx in FIXES if variant(sim, initial, {fx})[:2] != (old_c, old_o)]
        checks = {
            "check_physical_rules gives the corrected counts": nonzero(prod.counts) == want_now,
            "replica with all four fixes == check_physical_rules": (now_c, now_o) == (prod.counts, len(prod.other)),
            "replica with no fix gives the pre-fix counts": nonzero(old_c) == want_before and old_o == 0,
            "fixes that change the pre-fix counts": explained == want_fixes,
        }
        grader_ok &= checks["check_physical_rules gives the corrected counts"]
        replica_ok &= all(v for k, v in checks.items() if not k.startswith("check_physical_rules"))
        lines.append((name, checks, nonzero(old_c), nonzero(now_c), explained))
    if verbose:
        for name, checks, before, now, explained in lines:
            print(f"  {'ok  ' if all(checks.values()) else 'FAIL'} {name}: before {before or 'clean'} -> now {now or 'clean'}; explained by {explained or 'none'}")
            for k, v in checks.items():
                if not v:
                    print(f"       failed: {k}")
        print(f"selftest: grader_is_corrected={grader_ok} replica_ok={replica_ok}")
    return {"grader_is_corrected": grader_ok, "replica_ok": replica_ok, "cases": len(lines)}


# --------------------------------------------------------------------------- the ORIGINAL side


def outcome_from_record(rec: dict) -> str:
    """The verdict an attempt record implies: refused -> REFUSED; not simulated OK or any rule broken -> FAIL; else PASS."""
    if rec["outcome"] == "refused":
        return "REFUSED"
    if rec.get("simulate_pass") is None:
        return "UNVERIFIED"
    if rec["simulate_pass"] is not True or (rec.get("rule_violations") or 0) > 0:
        return "FAIL"
    return "PASS"


def load_originals() -> list[dict]:
    """One dict per attempt, in the order of the original results.json: spec Sonnet, spec Haiku, free Sonnet, free Haiku."""
    results = json.loads((HERE / "results.json").read_text(encoding="utf-8"))
    records: dict[tuple[str, str, str], dict] = {}
    for mode in MODES:
        for model in MODELS:
            path = HERE / f"attempts_{mode}_{model}.jsonl"
            for n, rec in enumerate(read_jsonl(path), 1):
                prefix, _, session = rec["task_id"].partition(":")
                key = (mode, model, session)
                if prefix != mode or key in records:
                    raise SystemExit(f"{path.name} line {n}: unexpected or duplicate task id {rec['task_id']!r}")
                records[key] = dict(rec, _file=path.name, _line=n)
    plan_sha: dict[tuple[str, str], str | None] = {}
    for model in MODELS:
        rep = json.loads((HERE / f"spec_{model}" / "robot_report.json").read_text(encoding="utf-8"))
        for s in rep["runs"][0]["sessions"]:
            plan_sha[(model, s["session_id"])] = s.get("plan_sha256")
    out = []
    for row in results["attempts"]:
        key = (row["mode"], row["model"], row["session"])
        if key not in records:
            raise SystemExit(f"results.json has {row['task_id']} ({row['model']}) but no attempt record has it")
        out.append({"key": key, "row": row, "rec": records[key], "plan_sha256": plan_sha.get((row["model"], row["session"]))})
    extra = set(records) - {o["key"] for o in out}
    if extra:
        raise SystemExit(f"attempt records without a row in results.json: {sorted(extra)}")
    return out


def original_view(o: dict) -> dict:
    row, rec = o["row"], o["rec"]
    det = rec.get("detail") or {}
    rc = det.get("rule_counts") or {}
    ok = rec.get("simulate_pass") is True
    return {
        "outcome": row["outcome"], "record_outcome": rec["outcome"], "outcome_from_record": outcome_from_record(rec),
        "sim_ok": row.get("sim_ok"), "simulate_pass": rec.get("simulate_pass"), "rule_violations": rec.get("rule_violations"),
        "rules_evaluated": ok, "rule_counts": {r: int(rc.get(r) or 0) for r in RULES}, "tip_state": int(row.get("tip_state") or 0),
        "sim_error_kind": row.get("sim_error_kind"), "sim_error": row.get("sim_error"), "lint": list(row.get("lint") or []),
        "n_events": row.get("n_events"), "error_findings": list(row.get("error_findings") or []),
        "warning_findings": list(row.get("warning_findings") or []), "code_sha256": row.get("code_sha256"),
        "stop_reason": row.get("stop_reason"), "refusal_kind": row.get("refusal_kind"), "record": f"{rec['_file']}:{rec['_line']}",
    }


def original_consistency(o: dict, old: dict) -> list[str]:
    """Where the attempt record and results.json (the two original sources) disagree. Expected: nothing."""
    row, rec = o["row"], o["rec"]
    det = rec.get("detail") or {}
    bad = []
    if old["outcome_from_record"] != row["outcome"]:
        bad.append(f"verdict implied by the record is {old['outcome_from_record']}, results.json says {row['outcome']}")
    if rec.get("simulate_pass") != row.get("sim_ok"):
        bad.append(f"simulate_pass {rec.get('simulate_pass')} != sim_ok {row.get('sim_ok')}")
    if old["rules_evaluated"]:
        if old["rule_counts"] != {r: int(row["rule_counts"].get(r) or 0) for r in RULES}:
            bad.append("per-rule counts differ")
        if rec.get("rule_violations") != sum(1 for v in old["rule_counts"].values() if v):
            bad.append("rule_violations is not the number of rules with a count")
    if det.get("error_kind") != row.get("sim_error_kind"):
        bad.append(f"error kind {det.get('error_kind')} != {row.get('sim_error_kind')}")
    if det.get("code_sha256") != row.get("code_sha256"):
        bad.append("code_sha256 differs")
    return bad


# --------------------------------------------------------------------------- the regrade


def regrade_one(o: dict, python: str) -> dict:
    """grade_source exactly as the original run called it: spec with the saved plan, free without."""
    row = o["row"]
    code_path = HERE / row["code_file"]
    raw = code_path.read_bytes()  # the original wrote gen.code as UTF-8 bytes
    code = raw.decode("utf-8")
    out: dict[str, Any] = {"code_sha256": sha256_bytes(raw), "source_air_gap_mentions": code.count("air_gap")}
    plan = None
    if row["mode"] == "spec":
        plan = SessionPlan.model_validate_json(code_path.with_suffix(".plan.json").read_text(encoding="utf-8"))
        out["plan_sha256"] = plan.plan_hash()
    t0 = time.monotonic()
    grade, sim = V.grade_source(code, plan=plan, ot_python=python)
    out.update(seconds=round(time.monotonic() - t0, 1), grade=grade, sim=sim)
    return out


def safe_regrade(o: dict, python: str) -> dict:
    try:
        return regrade_one(o, python)
    except Exception:  # noqa: BLE001 - a crash in OUR code is recorded as such, not as a model failure
        return {"harness_error": scrub(traceback.format_exc())[-800:]}


def new_view(res: dict) -> dict:
    g = res["grade"]
    rc = {r: int(g.rule_counts.get(r, 0)) for r in RULES}
    ok = g.sim_ok is True
    return {
        "outcome": g.verdict, "sim_ok": g.sim_ok, "sim_error_kind": g.sim_error_kind, "sim_error": g.sim_error, "lint": list(g.lint),
        "rules_evaluated": ok, "rule_counts": rc, "rule_violations": sum(1 for v in rc.values() if v) if ok else None,
        "tip_state": max(0, g.n_rule_violations - sum(rc.values())) if ok else 0, "n_events": g.n_events,
        "error_findings": [f"{f.rule}: {f.message}" for f in g.findings if f.severity == "error"],
        "warning_findings": [f"{f.rule}: {f.message}" for f in g.findings if f.severity == "warning"],
        "code_sha256": res["code_sha256"],
    }


def diff_views(old: dict, new: dict) -> dict:
    d = {"outcome": old["outcome"] != new["outcome"], "rule_counts": old["rule_counts"] != new["rule_counts"],
         "tip_state": old["tip_state"] != new["tip_state"]}
    detail = ("sim_error_kind", "sim_error", "lint", "n_events", "error_findings", "warning_findings")
    for k in detail:
        d[k] = old[k] != new[k]
    d["main"] = d["outcome"] or d["rule_counts"] or d["tip_state"]
    d["detail_only"] = (not d["main"]) and any(d[k] for k in detail)
    return d


def exposure(sim: dict) -> dict:
    """How much of each fix's territory the program touches: if it touches none, the fix cannot have changed the grade."""
    events = V.leaf_events(sim)
    initial = V.initial_from_liquids(sim)
    declared = set(initial)
    air = [e for e in events if e["kind"] == "air_gap"]
    touched: set[tuple[str, str]] = set()
    n_disp = n_asp_after = 0
    for e in events:
        if e["kind"] not in ("aspirate", "dispense") or not V._is_stock((e.get("target") or {}).get("labware")):
            continue
        for key, _mult in V._cells(e):
            if key in declared:
                continue
            if e["kind"] == "dispense":
                n_disp += 1
                touched.add(key)
            elif key in touched:
                n_asp_after += 1
    _, _, margins = variant(sim, initial, set(FIXES))
    return {
        "n_leaf_events": len(events), "n_air_gap_events": len(air), "air_gap_ul": round(sum(float(e.get("volume_ul") or 0.0) for e in air), 6),
        "n_dispense_into_undeclared_reservoir": n_disp, "n_aspirate_from_undeclared_reservoir_after_a_dispense_into_it": n_asp_after,
        "n_volume_checks": len(margins),
        "n_checks_in_tolerance_flip_band": sum(1 for m in margins if -OLD_TOL <= m < -NEW_TOL),  # fine under 1e-6, a violation under 1e-9
    }


def replica_report(sim: dict, old: dict, new: dict) -> dict:
    initial = V.initial_from_liquids(sim)
    old_c, old_o, _ = variant(sim, initial, set())
    now_c, now_o, _ = variant(sim, initial, set(FIXES))
    alone = {fx: variant(sim, initial, {fx})[:2] for fx in FIXES}
    explained = [fx for fx in FIXES if alone[fx] != (old_c, old_o)]
    rep: dict[str, Any] = {
        "legacy_replica_matches_original": old_c == old["rule_counts"] and old_o == old["tip_state"],
        "current_replica_matches_grade": now_c == new["rule_counts"] and now_o == new["tip_state"],
        "fixes_that_change_the_pre_fix_counts": explained,
    }
    if explained or not rep["legacy_replica_matches_original"] or not rep["current_replica_matches_grade"]:
        rep.update(legacy_counts=old_c, legacy_tip_state=old_o, counts_with_each_fix_alone={fx: {"counts": c, "tip_state": o} for fx, (c, o) in alone.items()})
    return rep


def explain(old: dict, new: dict, rep: dict | None) -> str:
    parts = []
    if old["outcome"] != new["outcome"]:
        parts.append(f"outcome {old['outcome']} -> {new['outcome']}")
    parts += [f"{r} {old['rule_counts'][r]} -> {new['rule_counts'][r]}" for r in RULES if old["rule_counts"][r] != new["rule_counts"][r]]
    if old["tip_state"] != new["tip_state"]:
        parts.append(f"tip_state {old['tip_state']} -> {new['tip_state']}")
    if rep is None:
        why = "the program did not simulate now, so no attribution to the four fixes is possible"
    elif not rep["legacy_replica_matches_original"]:
        why = "the pre-fix replica does not reproduce the original counts, so this is NOT shown to be due to the four fixes"
    elif rep["fixes_that_change_the_pre_fix_counts"]:
        why = "explained by: " + "; ".join(f"{fx} ({FIX_TEXT[fx]})" for fx in rep["fixes_that_change_the_pre_fix_counts"])
    else:
        why = "none of the four fixes changes this program's counts: NOT explained by them"
    return "; ".join(parts) + ". " + why


def build_row(o: dict, res: dict | None) -> dict:
    row = o["row"]
    old = original_view(o)
    base: dict[str, Any] = {"task_id": row["task_id"], "mode": row["mode"], "model": row["model"], "session": row["session"], "family": row["family"],
                            "original": old, "original_sources_disagree": original_consistency(o, old)}
    if res is None:  # refused: the provider ended the turn with a refusal, no code exists
        expected = HERE / f"spec_{row['model']}" / "robot" / f"llm_{row['model']}" / f"{row['session']}.py"
        base.update(regraded=False, new=None, outcome_before=old["outcome"], outcome_after=old["outcome"],
                    changed={"main": False, "detail_only": False, "outcome": False, "rule_counts": False},
                    note="refused by the provider: no code was generated, so there is nothing to regrade; carried over as REFUSED",
                    unexpected_code_file=(row["mode"] == "spec" and expected.exists()))
        return base
    if "harness_error" in res:
        base.update(regraded=False, new=None, outcome_before=old["outcome"], outcome_after="ERROR", harness_error=res["harness_error"],
                    changed={"main": True, "detail_only": False, "outcome": True, "rule_counts": False},
                    explanation="the regrade itself crashed (a bug in this script or the grader), not a grade of the protocol")
        return base
    new = new_view(res)
    d = diff_views(old, new)
    base.update(regraded=True, outcome_before=old["outcome"], outcome_after=new["outcome"], new=new, changed=d, seconds=res["seconds"],
                code_matches_original=(res["code_sha256"] == old["code_sha256"]),
                plan_matches_original=((res["plan_sha256"] == o["plan_sha256"]) if "plan_sha256" in res else None),
                harness_failure=(new["sim_error_kind"] in HARNESS_KINDS), source_air_gap_mentions=res["source_air_gap_mentions"],
                opentrons_version=(res["sim"] or {}).get("opentrons_version"))
    if new["sim_ok"]:
        base["exposure"] = exposure(res["sim"])
        base["replica"] = replica_report(res["sim"], old, new)
    if d["main"]:
        base["explanation"] = explain(old, new, base.get("replica"))
    return base


def run_pass(originals: list[dict], python: str, workers: int) -> tuple[list[dict], dict]:
    h0 = snapshot_hashes()
    t0 = time.monotonic()
    todo = [o for o in originals if o["row"]["outcome"] != "REFUSED"]
    with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        graded = list(pool.map(lambda o: safe_regrade(o, python), todo))
    by_key = {o["key"]: g for o, g in zip(todo, graded)}
    h1 = snapshot_hashes()
    rows = [build_row(o, by_key.get(o["key"])) for o in originals]
    meta = {"hashes_at_start": h0, "hashes_at_end": h1, "changed_during_pass": {k: h0[k] != h1[k] for k in h0},
            "grader_changed_since_import": h1["grader"] != GRADER_SHA256_AT_IMPORT, "seconds": round(time.monotonic() - t0, 1)}
    return rows, meta


# --------------------------------------------------------------------------- summaries


def headline(rows: list[dict]) -> list[dict]:
    out = []
    for mode in MODES:
        for model in MODELS:
            g = [r for r in rows if r["mode"] == mode and r["model"] == model]
            before, after = Counter(r["outcome_before"] for r in g), Counter(r["outcome_after"] for r in g)
            n = len(g)
            out.append({"mode": mode, "model": model, "n": n, "regraded": sum(1 for r in g if r["regraded"]),
                        "carried_over_refused": sum(1 for r in g if not r["regraded"] and r["outcome_after"] == "REFUSED"),
                        "before": {k: before.get(k, 0) for k in OUTCOMES}, "after": {k: after.get(k, 0) for k in OUTCOMES},
                        "pass_at_1_before": f"{before['PASS']}/{n}", "pass_at_1_after": f"{after['PASS']}/{n}"})
    return out


def rule_totals(rows: list[dict]) -> dict:
    sim_before = [r["original"] for r in rows if r["original"]["rules_evaluated"]]
    sim_after = [r["new"] for r in rows if r["new"] and r["new"]["rules_evaluated"]]
    out: dict[str, Any] = {"programs_with_rules_evaluated_before": len(sim_before), "programs_with_rules_evaluated_after": len(sim_after),
                           "rules": {}}
    for rule in RULES:
        b, a = [v["rule_counts"][rule] for v in sim_before], [v["rule_counts"][rule] for v in sim_after]
        out["rules"][rule] = {"violations_before": sum(b), "violations_after": sum(a),
                              "programs_with_violation_before": sum(1 for x in b if x), "programs_with_violation_after": sum(1 for x in a if x)}
    out["tip_state_before"], out["tip_state_after"] = sum(v["tip_state"] for v in sim_before), sum(v["tip_state"] for v in sim_after)
    return out


def exposure_totals(rows: list[dict]) -> dict:
    ex = [r["exposure"] for r in rows if r.get("exposure")]
    n_asp = "n_aspirate_from_undeclared_reservoir_after_a_dispense_into_it"
    return {
        "programs_simulated": len(ex),
        "air_gap": {"programs_with_an_air_gap_substep": sum(1 for e in ex if e["n_air_gap_events"]), "events": sum(e["n_air_gap_events"] for e in ex),
                    "programs_whose_source_text_contains_air_gap": sum(1 for r in rows if r.get("source_air_gap_mentions"))},
        "reservoir": {"programs_dispensing_into_an_undeclared_reservoir_well": sum(1 for e in ex if e["n_dispense_into_undeclared_reservoir"]),
                      "programs_that_then_aspirate_from_such_a_well": sum(1 for e in ex if e[n_asp]), "such_aspirations": sum(e[n_asp] for e in ex)},
        "tolerance": {"volume_checks": sum(e["n_volume_checks"] for e in ex),
                      "programs_with_a_check_in_the_flip_band": sum(1 for e in ex if e["n_checks_in_tolerance_flip_band"]),
                      "checks_in_the_flip_band": sum(e["n_checks_in_tolerance_flip_band"] for e in ex)},
    }


def integrity(originals: list[dict], rows: list[dict], head: list[dict], passes: list[dict], st: dict) -> dict[str, Any]:
    regraded = [r for r in rows if r["regraded"]]
    spec_regraded = [r for r in regraded if r["mode"] == "spec"]
    rep = [r["replica"] for r in rows if r.get("replica")]
    last = passes[-1]
    checks = {
        "50 attempts": len(rows) == 50,
        "group sizes are 13, 13, 12, 12": all(h["n"] == GROUP_N[(h["mode"], h["model"])] for h in head),
        "outcomes before add up to n in every group": all(sum(h["before"].values()) == h["n"] for h in head),
        "outcomes after add up to n in every group": all(sum(h["after"].values()) == h["n"] for h in head),
        "totals add up to 50 (before and after)": sum(sum(h["before"].values()) for h in head) == 50 and sum(sum(h["after"].values()) for h in head) == 50,
        "regraded + carried-over refused == 50": len(regraded) + sum(1 for r in rows if not r["regraded"] and r["outcome_after"] == "REFUSED") == 50,
        "attempt records and results.json agree for all 50": not any(r["original_sources_disagree"] for r in rows),
        "saved code is byte-identical to what was graded (sha256) for every regraded protocol": all(r["code_matches_original"] for r in regraded),
        "reloaded plan hash equals the recorded plan hash for every regraded spec protocol": all(r["plan_matches_original"] for r in spec_regraded),
        "no refused attempt has a code file": not any(r.get("unexpected_code_file") for r in rows),
        "no harness failure (crash, runner error, timeout, simulator missing)": not any(("harness_error" in r) or r.get("harness_failure") for r in rows),
        "pre-fix replica reproduces the ORIGINAL counts of every simulated program": all(x["legacy_replica_matches_original"] for x in rep),
        "all-fixes replica equals check_physical_rules on every simulated program": all(x["current_replica_matches_grade"] for x in rep),
        "grader is the corrected check (self-test on hand-built logs)": st["grader_is_corrected"],
        "attribution aid passes its self-test": st["replica_ok"],
        "simulator runner, grader and plan model unchanged during the final pass": not any(last["changed_during_pass"].values()) and not last["grader_changed_since_import"],
    }
    return {"all_ok": all(checks.values()), "checks": checks, "failed": [k for k, v in checks.items() if not v]}


# --------------------------------------------------------------------------- output


def fmt_vec(view: dict | None) -> str:
    if view is None or not view["rules_evaluated"]:
        return "-"
    return "/".join(str(view["rule_counts"][r]) for r in RULES) + (f" +tip_state {view['tip_state']}" if view["tip_state"] else "")


def fmt_outcome(view: dict | None, fallback: str) -> str:
    if view is None:
        return fallback
    if view["outcome"] == "FAIL":
        if view["lint"]:
            return "FAIL: lint"
        if view["sim_ok"] is False:
            return f"FAIL: {view['sim_error_kind']}"
        broken = [f"{r} x{n}" for r, n in view["rule_counts"].items() if n]
        if view["tip_state"]:
            broken.append(f"tip_state x{view['tip_state']}")
        return "FAIL: " + (", ".join(broken) or "plan or structure finding")
    return view["outcome"]


def build_result(originals: list[dict], rows: list[dict], passes: list[dict], st: dict, python: str, started: str) -> dict:
    head = headline(rows)
    changed = [r for r in rows if r["changed"]["main"]]
    detail = [r for r in rows if r["changed"].get("detail_only")]
    versions = {r.get("opentrons_version") for r in rows if r.get("opentrons_version")}
    last = passes[-1]
    return {
        "schema": "vaxforge.streamc_llm_eval.regrade/1", "task": "H7", "started_utc": started,
        "finished_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "offline": {"model_calls": 0, "network": "none", "llm_backend_or_anthropic_imported": any(m == "vaxforge.s9_llm" or m.split(".")[0] == "anthropic" for m in sys.modules)},
        "grader": {"module": "vaxforge.s9_validate", "file_sha256": last["hashes_at_end"]["grader"], "file_sha256_at_import": GRADER_SHA256_AT_IMPORT,
                   "vol_tol_ul": V._VOL_TOL, "state": "as in the working tree when this ran (identified by its sha256)"},
        "simulator": {"runner_sha256_at_start_of_final_pass": last["hashes_at_start"]["runner"], "runner_sha256_at_end_of_final_pass": last["hashes_at_end"]["runner"],
                      "opentrons_reported_by_the_simulations": sorted(versions), "interpreter": ot_versions(python)},
        "passes": passes, "fixes": FIX_TEXT, "rule_order": list(RULES),
        "n_attempts": len(rows), "n_regraded": sum(1 for r in rows if r["regraded"]),
        "n_carried_over_refused": sum(1 for r in rows if not r["regraded"] and r["outcome_after"] == "REFUSED"),
        "headline": head, "rule_totals": rule_totals(rows), "exposure_totals": exposure_totals(rows),
        "no_outcome_changed": not any(r["changed"]["outcome"] for r in rows),
        "no_rule_count_changed": not any(r["changed"].get("rule_counts") or r["changed"].get("tip_state") for r in rows),
        "changed_attempts": [{"task_id": r["task_id"], "model": r["model"], "mode": r["mode"], "before": fmt_outcome(r["original"], r["outcome_before"]),
                              "after": fmt_outcome(r["new"], r["outcome_after"]), "reason": r.get("explanation")} for r in changed],
        "detail_changed_attempts": [{"task_id": r["task_id"], "model": r["model"], "fields": [k for k, v in r["changed"].items() if v and k not in ("main", "detail_only")]} for r in detail],
        "integrity": integrity(originals, rows, head, passes, st), "attempts": rows,
    }


def render_md(res: dict) -> str:
    L: list[str] = []
    A = L.append
    A("# Regrade of the saved LLM-eval protocols with the corrected five-rule check (task H7)")
    A("")
    A(f"Generated by `docs/streamc_llm_eval/regrade.py` ({res['finished_utc']}). Offline: 0 model calls, no network. The saved code only went through "
      "`grade_source` (lint first, then the Opentrons simulation in a subprocess); the original records and `results.json` were only read.")
    A("")
    A("- **before** = the original run: attempt records `attempts_{spec,free}_<model>.jsonl` (task id, simulate_pass, per-rule counts) and `results.json` (the PASS / FAIL / REFUSED verdict).")
    A("- **after** = `grade_source` as it is in the working tree now, on the saved code (spec: with the saved `<session>.plan.json`; free: no plan). "
      "The simulator logs were not kept, so every protocol was simulated again.")
    A(f"- {res['n_regraded']} protocols were regraded. The {res['n_carried_over_refused']} refused attempts (spec, Haiku) produced no code: they are carried over as REFUSED.")
    A("- The four fixes: " + "; ".join(f"**{k}**: {v}" for k, v in res["fixes"].items()) + ".")
    A("")
    sim, g = res["simulator"], res["grader"]
    last = res["passes"][-1]
    A("## Versions")
    A("")
    A("| item | value |")
    A("|---|---|")
    A(f"| grader | `vaxforge.s9_validate`, {g['state']}; sha256 `{g['file_sha256']}`; `_VOL_TOL` = {g['vol_tol_ul']:g} uL |")
    A(f"| simulator runner `_ot_sim_runner.py` | sha256 `{sim['runner_sha256_at_start_of_final_pass']}` at the start of the final pass, `{sim['runner_sha256_at_end_of_final_pass']}` at its end |")
    A(f"| passes | {len(res['passes'])} (a pass is repeated once if a tracked file changes while it runs); final pass {last['seconds']} s |")
    A(f"| interpreter / Opentrons | Python {sim['interpreter']['python']}, opentrons {sim['interpreter']['opentrons']}; the simulations report {', '.join(sim['opentrons_reported_by_the_simulations'])} |")
    A("")
    A("## Counts before -> after")
    A("")
    A("| mode | model | n | PASS | FAIL | REFUSED | ERROR | UNVERIFIED | pass@1 |")
    A("|---|---|---|---|---|---|---|---|---|")
    for h in res["headline"]:
        cells = " | ".join(f"{h['before'][k]} -> {h['after'][k]}" for k in OUTCOMES)
        A(f"| {h['mode']} | {SHORT[h['model']]} | {h['n']} | {cells} | {h['pass_at_1_before']} -> {h['pass_at_1_after']} |")
    tot_b, tot_a = Counter(), Counter()
    for h in res["headline"]:
        tot_b.update(h["before"])
        tot_a.update(h["after"])
    A(f"| all | both | {res['n_attempts']} | " + " | ".join(f"{tot_b[k]} -> {tot_a[k]}" for k in OUTCOMES) + " | |")
    A("")
    A("## What changed")
    A("")
    if not res["changed_attempts"]:
        A(f"**No outcome changed and no per-rule violation count changed**, in any of the {res['n_regraded']} regraded protocols.")
    else:
        A(f"**{len(res['changed_attempts'])} attempt(s) changed outcome or violation counts:**")
        A("")
        A("| task id | model | before | after | reason |")
        A("|---|---|---|---|---|")
        for c in res["changed_attempts"]:
            A(f"| {c['task_id']} | {SHORT[c['model']]} | {c['before']} | {c['after']} | {str(c['reason']).replace('|', '/')} |")
    A("")
    if res["detail_changed_attempts"]:
        A("Changed in detail only (outcome and rule counts equal): " + "; ".join(f"{d['task_id']} ({SHORT[d['model']]}): {', '.join(d['fields'])}" for d in res["detail_changed_attempts"]) + ".")
    else:
        A(f"No other field of the grade changed either: simulator error kind and text, number of simulator events, lint, error findings and warning findings are equal in all {res['n_regraded']}.")
    A("")
    A("## Rule violations over the programs that simulated")
    A("")
    rt = res["rule_totals"]
    A(f"Programs with the rules evaluated: {rt['programs_with_rules_evaluated_before']} before, {rt['programs_with_rules_evaluated_after']} after.")
    A("")
    A("| rule | violations before -> after | programs with a violation before -> after |")
    A("|---|---|---|")
    for rule, v in rt["rules"].items():
        A(f"| {rule} | {v['violations_before']} -> {v['violations_after']} | {v['programs_with_violation_before']} -> {v['programs_with_violation_after']} |")
    A(f"| tip_state (picked up a tip while one is attached) | {rt['tip_state_before']} -> {rt['tip_state_after']} | |")
    A("")
    A("## Did the programs touch what the fixes changed?")
    A("")
    ex = res["exposure_totals"]
    A(f"Over the {ex['programs_simulated']} programs that simulated (current `leaf_events`, the declared contents the program made with `load_liquid`):")
    A("")
    A("| fix | what it can change | programs | events or checks |")
    A("|---|---|---|---|")
    A(f"| air_volume, air_visit | an `Air gap` sub-step in the run log | {ex['air_gap']['programs_with_an_air_gap_substep']} | {ex['air_gap']['events']} |")
    A(f"| air_volume, air_visit | the text `air_gap` in the source code | {ex['air_gap']['programs_whose_source_text_contains_air_gap']} of {res['n_regraded']} regraded | |")
    A(f"| reservoir | a dispense into an undeclared reservoir well | {ex['reservoir']['programs_dispensing_into_an_undeclared_reservoir_well']} | |")
    A(f"| reservoir | ... and a later aspiration from that same well (the case that was wrongly tracked) | {ex['reservoir']['programs_that_then_aspirate_from_such_a_well']} | {ex['reservoir']['such_aspirations']} |")
    A(f"| tolerance | a volume check with -1e-6 <= slack < -1e-9 uL (passed under 1e-6, fails under 1e-9) | {ex['tolerance']['programs_with_a_check_in_the_flip_band']} | {ex['tolerance']['checks_in_the_flip_band']} of {ex['tolerance']['volume_checks']} |")
    A("")
    A("## Per attempt")
    A("")
    A("Rule counts are in this order: " + " / ".join(RULES) + ". `-` = the rules were not evaluated (the program did not simulate, or was refused).")
    A("")
    A("| # | mode | model | task id | outcome before | outcome after | rule counts before | rule counts after | changed |")
    A("|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(res["attempts"], 1):
        ch = r["changed"]
        flag = "OUTCOME" if ch["outcome"] else ("COUNTS" if ch["main"] else ("detail" if ch.get("detail_only") else "no"))
        after = fmt_outcome(r["new"], r["outcome_after"]) + ("" if r["regraded"] else " (not regraded: no code)")
        A(f"| {i} | {r['mode']} | {SHORT[r['model']]} | {r['task_id']} | {fmt_outcome(r['original'], r['outcome_before'])} | {after} | "
          f"{fmt_vec(r['original'])} | {fmt_vec(r['new'])} | {flag} |")
    A("")
    A("## Checks")
    A("")
    A("| check | result |")
    A("|---|---|")
    for k, v in res["integrity"]["checks"].items():
        A(f"| {k} | {'ok' if v else '**FAILED**'} |")
    A("")
    return "\n".join(L)


def print_table(rows: list[dict]) -> None:
    for r in rows:
        mark = "  <-- CHANGED" if r["changed"]["main"] else ""
        print(f"{r['mode']:4} {SHORT[r['model']]:6} {r['session']:26} {fmt_outcome(r['original'], r['outcome_before']):42.42} -> "
              f"{fmt_outcome(r['new'], r['outcome_after']):42.42} rules {fmt_vec(r['original']):15} -> {fmt_vec(r['new'])}{mark}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="regrade.py", description=__doc__.split("\n")[0])
    ap.add_argument("--selftest", action="store_true", help="check the grader's four fixes and the attribution aid on hand-built logs, then stop")
    ap.add_argument("--workers", type=int, default=4, help="simulations in parallel (default 4, as in the original run)")
    a = ap.parse_args(argv)
    st = selftest(verbose=a.selftest)
    if a.selftest:
        return 0 if st["grader_is_corrected"] and st["replica_ok"] else 1
    try:
        grader_file = Path(V.__file__).resolve()
        grader_file.relative_to(SRC.resolve())
    except ValueError:
        print(f"refusing to regrade: vaxforge.s9_validate was imported from {grader_file}, not from this checkout's src/")
        return 2
    if not st["grader_is_corrected"]:
        print("refusing to regrade: check_physical_rules does not show the corrected behaviour on the hand-built logs (run --selftest)")
        return 2
    python = V.find_ot_python()
    if python is None:
        print("refusing to regrade: no interpreter with opentrons installed (set VAXFORGE_OT_PYTHON)")
        return 2
    started = datetime.now(timezone.utc).isoformat(timespec="seconds")
    originals = load_originals()
    print(f"regrading {sum(1 for o in originals if o['row']['outcome'] != 'REFUSED')} saved protocols "
          f"({sum(1 for o in originals if o['row']['outcome'] == 'REFUSED')} refused attempts have no code); workers={a.workers}")
    passes: list[dict] = []
    rows: list[dict] = []
    for n in (1, 2):
        rows, meta = run_pass(originals, python, a.workers)
        passes.append(dict(pass_number=n, **meta))
        print(f"pass {n}: {meta['seconds']} s; tracked files changed during the pass: {[k for k, v in meta['changed_during_pass'].items() if v] or 'none'}")
        if not any(meta["changed_during_pass"].values()) and not meta["grader_changed_since_import"]:
            break
        print("a tracked source file changed while the pass ran: repeating the pass once so the numbers come from one version")
    res = build_result(originals, rows, passes, st, python, started)
    (HERE / "regrade_results.json").write_bytes((json.dumps(res, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))
    (HERE / "regrade_results.md").write_bytes(render_md(res).encode("utf-8"))
    print_table(rows)
    print()
    for h in res["headline"]:
        print(f"{h['mode']:4} {SHORT[h['model']]:6} n={h['n']:2}  before {h['before']}  after {h['after']}")
    print("no outcome changed:", res["no_outcome_changed"], "| no rule count changed:", res["no_rule_count_changed"])
    for c in res["changed_attempts"]:
        print("CHANGED:", c["task_id"], c["model"], c["before"], "->", c["after"], "|", c["reason"])
    print("exposure:", json.dumps(res["exposure_totals"]))
    print("integrity:", "all ok" if res["integrity"]["all_ok"] else f"FAILED: {res['integrity']['failed']}")
    print("runner sha256 at start / end of the final pass:", passes[-1]["hashes_at_start"]["runner"], "/", passes[-1]["hashes_at_end"]["runner"])
    print("model calls: 0; LLM backend / anthropic imported:", res["offline"]["llm_backend_or_anthropic_imported"])
    print("wrote", (HERE / "regrade_results.json").relative_to(ROOT).as_posix(), "and", (HERE / "regrade_results.md").relative_to(ROOT).as_posix())
    return 0 if res["integrity"]["all_ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
