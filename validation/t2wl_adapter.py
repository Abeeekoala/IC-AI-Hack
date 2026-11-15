"""S9.5 grading adapter: one generated OT-2 protocol, graded by Text2WetLab's own deterministic layers.

Returns what validation/winrate.py needs for one attempt (README S9.5, "Evaluation protocol"): whether
opentrons_simulate v7.5.0 passes, how many of the five physical rules failed, and the diff against the task's
reference, with the failed check names for the audit trail. Text2WetLab's LLM judge layer is never run and no model
is called. The adapter is standard library only and imports nothing from Text2WetLab or Opentrons: every layer runs in
a subprocess, so the module imports under any interpreter.

Layers (Text2WetLab's own functions, called inside the subprocess and never copied), in order. Each one that fails
ends the run, and code that fails one of the first two is never executed:
  1. protocol_lint.violations   static filter, run first
  2. anti_hack.tripped          reward-hacking traps
  3. simulation                 spec_check.simulate for the nine tasks that have an IR; the RNA task's grade.simulate
  4. spec_check.check           the five rules and the end state against the IR (nine tasks)
     checks.analyze             the RNA task's run-log checks (opentrons-rna-extraction and its -hard twin)
The RNA tasks ship no lint, so they get eval/protocol_lint.py of the same clone, the same file as the other nine tasks'.
One trap is skipped: task_files_modified on ecoli-heat-shock-transformation-hard, whose /data/paper.txt is fetched at image
build time (bioRxiv licence) and is not in the repo. Its grades carry data_files_unavailable. Text2WetLab's own oracle test
skips that task for the same reason. The other traps, and the whole run on every other task, are unchanged.
Checked against Text2WetLab's own tasks/<task>/tests/grade.py with SKIP_JUDGE=1 (the route of its oracle test): the same
checks fail on 24 broken variants of the references, and the 10 references it can grade get deterministic_reward 1.0.

Setup (once). One interpreter does all the work: Python 3.10 with opentrons 7.5.0, which pins pydantic<2 and numpy<2,
so it is a separate venv, never the pipeline's. Its path comes from the grader_python argument, else from
VAXFORGE_OT_PYTHON (the variable S9 uses for the simulator), else the call fails. ot_python (the interpreter Text2WetLab
launches for the simulation itself) defaults to grader_python. Pass a pydantic 2 interpreter as grader_python and the
opentrons one as ot_python to run the graders under the pydantic version their Docker image uses; the verdicts match.
    uv venv --python 3.10 VENV
    uv pip install --python VENV/bin/python opentrons==7.5.0 opentrons-shared-data==7.5.0 "pydantic<2"
    git clone --depth 1 https://github.com/PhysicalAIBenchmarks/Text2WetLab CLONE      # verified at T2WL_COMMIT
    VAXFORGE_OT_PYTHON=VENV/bin/python
The clone is read, never written, and never imported by this process.

Safety. A protocol is untrusted model output. Every layer runs in one subprocess in its own process group: python -I,
a scrubbed environment (PATH and a scratch HOME only; no key of the caller's reaches it), a scratch working directory
and a timeout that kills the whole group. The lint runs first and nothing of the protocol executes before it and the
traps pass. The lint is a filter, not a sandbox, and there is no memory or disk limit: on a shared machine run this
inside a container.

Use:
    grades = grade_rows("generations.jsonl", clone=CLONE)        # held-out rows that have code and were not refused
    records = build_records("generations.jsonl", grades)          # winrate.py's input; refused= is the S9 detector

What is counted
  rule_results   one entry per rule, in this order: tip_before_aspirate, no_overdispense, no_aspirate_from_empty_well,
                 tip_dropped_at_end, no_cross_contamination. pass is True, False, or None when the rule was not
                 evaluated. rule_violations is the number of False entries.
                 Nine tasks: the five checks of spec_check.check, by those names.
                 RNA tasks: Text2WetLab runs no spec_check on them, and its generic rules flag the RNA reference
                 itself (6 aspirate_from_empty, 3 cross-contamination on opentrons-rna-extraction), so they are not
                 applied. no_cross_contamination is fresh_tip_per_sample_no_cross_contact; no_aspirate_from_empty_well
                 is reservoir_columns_within_15ml (no reservoir column asked for more than it holds). The other
                 three rules are not graded there (pass None). A tip-less aspiration still fails simulation.
  gt_diff        mismatches against the task's reference. Nine tasks: the sum of
                   - one per failed deck_labware check (wrong load name, wrong slot, or the label missing or twice);
                   - per failed end_state check, the wells whose volume differs from the IR's expected end state (the
                     number of wells whose volumes cannot be matched where the task leaves positions free:
                     split-200ul-two-wells' plate);
                   - per failed composition check (colony-pcr-screening-hard's pcr_plate, whose volumes the paper does
                     not fix), the problems it lists.
                 Not counted: step order (Text2WetLab grades the end state only on these tasks); reservoir and waste
                 end states (spec_check skips them); the wells of a container whose labware mismatched (it cannot be
                 mapped, so a misplaced plate counts 1, not its wells).
                 RNA tasks: the sum over the volume checks (binding_volumes_40_250_250, supernatant_removed_each_step,
                 two_500ul_ethanol_washes, elution_100ul, recover_70_100ul_one_well_each, and recover_about_80ul on the
                 easy task) of the sample wells that fail them, plus one for each failed step_order,
                 48_samples_to_odd_columns and distinct_elution_wells (their details give no count). Step order is
                 graded only here. Not counted: timing, magnet, temperature and mixing checks, and labware or slots
                 (the RNA grader classifies labware by its fixed slot and has no labware check).
  A failed lint, trap, or simulation is simulate_pass False with no rule or diff entries; failed_checks says which.
  A protocol that simulates but pipettes nothing has no rule violations and a diff of every expected well, so
  winrate.py's rule 2 prefers it to a working protocol that breaks one rule. That is a property of the metric.
"""
from __future__ import annotations

import argparse
import ast
import collections
import collections.abc
import importlib.util
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

T2WL_COMMIT = "a50139a9917a69d78eda350f14d375ec9437bd8f"  # the Text2WetLab commit this adapter was verified against
OT_PYTHON_ENV = "VAXFORGE_OT_PYTHON"
OPENTRONS_VERSION = "7.5.0"
RULES = ("tip_before_aspirate", "no_overdispense", "no_aspirate_from_empty_well", "tip_dropped_at_end",
         "no_cross_contamination")
RNA_RULE_CHECKS = {"no_aspirate_from_empty_well": "reservoir_columns_within_15ml",
                   "no_cross_contamination": "fresh_tip_per_sample_no_cross_contact"}
RNA_GT_CHECKS = {  # check -> what its failures are counted as; the other RNA checks are process, not diff
    "48_samples_to_odd_columns": "wells", "distinct_elution_wells": "wells", "step_order": "step order",
    "binding_volumes_40_250_250": "volumes", "supernatant_removed_each_step": "volumes",
    "two_500ul_ethanol_washes": "volumes", "elution_100ul": "volumes", "recover_70_100ul_one_well_each": "volumes",
    "recover_about_80ul": "volumes",
}
RNA_PROCESS_CHECKS = frozenset({
    "mix_5x_after_sample", "incubation_5min_before_magnet", "magnet_4min_before_first_removal",
    "magnet_engaged_for_all_removals", "air_dry_4min", "elution_off_magnet_then_90s_on", "elution_plate_4C_before_recovery"})
# Text2WetLab traps about the grader's environment, not the protocol. One tripping means the setup is wrong (a stale
# /tmp/t2wl_preexisting, a modified simulator install, a clone whose /data differs), and it would fail every protocol
# alike, so it raises AdapterError instead of becoming a verdict.
ENVIRONMENT_TRAPS = frozenset({"pre_written_outputs", "simulator_tampering", "task_files_modified"})
MAX_CODE_CHARS = 100_000  # the largest reference is about 15 000; difflib in the trap layer is quadratic in the size
WINRATE_SPLITS = ("heldout", "probe")
ARM_MODELS = {"base": "base", "finetune": "finetune"}
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
FAMILIES_FILE = Path(__file__).resolve().with_name("heldout_families.json")
PAPER_FILE = "/data/paper.txt"  # where a task's instruction tells the agent to read the paper


class AdapterError(RuntimeError):
    """The adapter or its environment is broken (not a verdict on the protocol)."""


# ---- reading Text2WetLab's checks (pure) ----------------------------------------------------------------------------

_POSITIONAL = re.compile(r"expected (\{.*\}), got (\{.*\})\Z", re.S)
_FREE = re.compile(r"expected volumes (\[.*\]), got (\[.*\]) \(positions free\)\Z", re.S)
_FRACTION = re.compile(r"(\d+)/(\d+) sample wells\s*\Z")


def _end_state_count(detail):
    """(wells that differ, unit) from a failed end_state detail, or None when the detail is not in a known form."""
    try:
        m = _POSITIONAL.match(detail)
        if m:
            want = {w: round(float(v), 6) for w, v in ast.literal_eval(m.group(1)).items()}
            got = {w: round(float(v), 6) for w, v in ast.literal_eval(m.group(2)).items()}
            return sum(1 for w in set(want) | set(got) if want.get(w, 0.0) != got.get(w, 0.0)), "wells"
        m = _FREE.match(detail)
        if m:
            want = collections.Counter(round(float(v), 6) for v in ast.literal_eval(m.group(1)))
            got = collections.Counter(round(float(v), 6) for v in ast.literal_eval(m.group(2)))
            return max(sum((want - got).values()), sum((got - want).values())), "wells (positions free)"
    except (ValueError, SyntaxError, TypeError, AttributeError, MemoryError, RecursionError):
        pass
    return None


def _ir_gt(failed, notes):
    entries = []
    for check in failed:
        name, detail = check["name"], str(check.get("detail", ""))
        if name.startswith("deck_labware:"):
            entries.append({"check": name, "unit": "labware", "count": 1})
        elif name.startswith("end_state:"):
            parsed = _end_state_count(detail)
            if parsed is None:
                notes.append(f"{name}: detail not in a known form, counted 1")
                parsed = (1, "wells")
            entries.append({"check": name, "unit": parsed[1], "count": parsed[0]})
        elif name.startswith("composition:"):
            entries.append({"check": name, "unit": "problems", "count": max(1, len([p for p in detail.split("; ") if p]))})
        elif name not in RULES:
            notes.append(f"{name}: not a check this adapter knows, not counted")
    return entries


def _rna_gt(failed, n_samples, notes):
    entries = []
    for check in failed:
        name, detail = check["name"], str(check.get("detail", ""))
        unit = RNA_GT_CHECKS.get(name)
        if unit is None:
            if name not in RNA_PROCESS_CHECKS and name not in RNA_RULE_CHECKS.values():
                notes.append(f"{name}: not a check this adapter knows, not counted")
            continue
        count, m = 1, _FRACTION.search(detail)
        if unit == "volumes" and m:
            ok, total = int(m.group(1)), int(m.group(2))
            count = max(1, (total - ok) + abs(n_samples - total))
        entries.append({"check": name, "unit": unit, "count": count})
    return entries


def _rule_results(kind, by_name, why):
    """One entry per rule. why is the reason every rule is None when the checks did not run."""
    out = {}
    for rule in RULES:
        name = rule if kind == "ir" else RNA_RULE_CHECKS.get(rule)
        check = by_name.get(name) if name else None
        if why:
            entry = {"pass": None, "t2wl_check": name, "detail": why}
        elif check is None:
            entry = {"pass": None, "t2wl_check": name,
                     "detail": "not graded by Text2WetLab for this task" if name is None else "check absent from the run"}
        else:
            entry = {"pass": bool(check["pass"]), "t2wl_check": name, "detail": str(check.get("detail", ""))}
        out[rule] = entry
    return out


def interpret(kind, raw):
    """The grade for one protocol from the subprocess's raw layer output. Pure."""
    lint, traps, sim = raw.get("lint") or [], raw.get("traps") or [], raw.get("sim") or {}
    grade = {"kind": kind, "stage": "checks", "simulate_pass": False, "rule_violations": None, "gt_diff": None,
             "gt_diff_detail": [], "failed_checks": [], "lint": list(lint), "traps": list(traps),
             "ignored_traps": list(raw.get("ignored_traps") or []), "sim_error": "", "notes": [],
             "not_applicable": list(raw.get("not_applicable") or [])}
    why = None
    if raw.get("timeout"):
        grade.update(stage="timeout", failed_checks=["grader_timeout"], sim_error=f"grader exceeded {raw['timeout']} s")
        why = "not evaluated: the grader timed out"
    elif lint:
        grade.update(stage="lint", failed_checks=["lint"])
        why = "not evaluated: refused by lint"
    elif traps:
        grade.update(stage="traps", failed_checks=["trap:" + str(t.get("trap")) for t in traps])
        why = "not evaluated: reward-hacking trap"
    elif not sim.get("ok"):
        grade.update(stage="simulation", failed_checks=["simulator_ran"],
                     sim_error=str(sim.get("kind") or sim.get("error") or "")[-300:])
        why = "not evaluated: simulation failed"
    grade["rule_results"] = _rule_results(kind, {c["name"]: c for c in raw.get("checks") or []}, why)
    if why:
        return grade
    checks = raw["checks"]
    failed = [c for c in checks if not c["pass"]]
    notes = grade["notes"]
    detail = _ir_gt(failed, notes) if kind == "ir" else _rna_gt(failed, int(raw["n_samples"]), notes)
    grade.update(simulate_pass=True, failed_checks=[c["name"] for c in failed], gt_diff_detail=detail,
                 gt_diff=sum(d["count"] for d in detail),
                 rule_violations=sum(1 for r in grade["rule_results"].values() if r["pass"] is False))
    return grade


# ---- running Text2WetLab's layers (subprocess) ----------------------------------------------------------------------

_DRIVER = r'''
import json, os, subprocess, sys, traceback
sys.dont_write_bytecode = True                      # leave the Text2WetLab clone untouched
cfg = json.loads(sys.argv[1])
out = {}

def read(name):
    with open(os.path.join(cfg["tests"], name), encoding="utf-8") as handle:
        return handle.read()

def layers():
    os.environ.update(OT_PYTHON=cfg["ot_python"], DATA_DIR=cfg["data"], TESTS_DIR=cfg["tests"],
                      PROTOCOL_PATH=cfg["protocol"], RUNLOG=os.path.join(cfg["tests"], "runlog.py"))
    sys.path.insert(0, cfg["tests"])
    with open(cfg["protocol"], encoding="utf-8") as handle:
        code = handle.read()
    import importlib.util
    spec = importlib.util.spec_from_file_location("t2wl_protocol_lint", cfg["lint"])
    lint = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lint)
    try:
        out["lint"] = lint.violations(code)
    except (ValueError, RecursionError, MemoryError) as exc:        # ast.parse on hostile source
        out["lint"] = ["unparseable source: " + type(exc).__name__]
    if out["lint"]:
        return
    from anti_hack import tripped
    traps = tripped(code)
    out["ignored_traps"] = [t for t in traps if t["trap"] in cfg["ignore_traps"]]
    out["traps"] = [t for t in traps if t["trap"] not in cfg["ignore_traps"]]
    if out["traps"]:
        return
    if cfg["kind"] == "ir":
        import spec_check
        from paper2protocol.models import Protocol
        try:
            sim = spec_check.simulate(cfg["protocol"], None, timeout=cfg["sim_timeout"])
        except subprocess.TimeoutExpired:
            sim = {"ok": False, "error": "simulation exceeded %s s" % cfg["sim_timeout"]}
        out["sim"] = {"ok": bool(sim.get("ok")), "error": str(sim.get("error", ""))}
        if not sim.get("ok"):
            out["sim"]["kind"] = spec_check.error_kind(out["sim"]["error"])
            return
        parse = getattr(Protocol, "model_validate_json", None) or Protocol.parse_raw       # pydantic 2, or 1 in the opentrons venv
        free = json.loads(read("checks.json"))
        res = spec_check.check(parse(read("ir.json")), sim, frozenset(free["free_wells"]), json.loads(read("deck.json")),
                               free.get("not_from_paper", {}))
        out["checks"], out["not_applicable"] = res["checks"], res.get("not_applicable", [])
    else:
        import grade, checks
        sim = grade.simulate()
        out["sim"] = {"ok": bool(sim.get("ok")), "error": str(sim.get("error", ""))}
        if not sim.get("ok"):
            return
        out["checks"] = grade.analyze(sim["events"], grade.load_rubric()["level"])["checks"]
        out["n_samples"] = checks.N_SAMPLES

try:
    layers()
except BaseException as exc:
    out["driver_error"] = type(exc).__name__ + ": " + str(exc)[-500:]
    out["traceback"] = traceback.format_exc()[-1500:]
with open(cfg["out"], "w", encoding="utf-8") as handle:
    json.dump(out, handle)
'''


def _run_scrubbed(cmd, home, cwd, timeout):
    """(returncode or None on timeout, stdout, stderr). env -i with PATH and a scratch HOME; the group dies on timeout."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), env={"PATH": "/usr/bin:/bin", "HOME": str(home)}, stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                            start_new_session=True)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return None, "", ""
    return proc.returncode, out, err


def _interpreter(explicit, fallback=None):
    path = explicit or fallback or os.environ.get(OT_PYTHON_ENV)
    if not path:
        raise AdapterError(f"no interpreter: pass grader_python or set {OT_PYTHON_ENV} "
                           f"(Python 3.10 with opentrons {OPENTRONS_VERSION}; see the module docstring)")
    path = os.path.abspath(os.path.expanduser(str(path)))  # never resolve: a venv's python is a symlink it needs
    if not (os.path.isfile(path) and os.access(path, os.X_OK)):
        raise AdapterError(f"interpreter {path} is not an executable file")
    return path


_PREFLIGHT = {}


def _preflight(python):
    """Fail loudly when the interpreter has no opentrons 7.5.0: otherwise the integrity trap would fail every protocol."""
    if python not in _PREFLIGHT:
        with tempfile.TemporaryDirectory(prefix="t2wl-pre-") as scratch:
            rc, out, err = _run_scrubbed([python, "-I", "-W", "ignore", "-c", "import opentrons; print(opentrons.__version__)"],
                                         scratch, scratch, 300)
        version = out.strip().splitlines()[-1] if out.strip() else ""
        if rc != 0 or version != OPENTRONS_VERSION:
            raise AdapterError(f"{python} must import opentrons {OPENTRONS_VERSION}, got {version or err.strip()[-200:] or 'nothing'}")
        _PREFLIGHT[python] = version
    return _PREFLIGHT[python]


def clone_commit(clone):
    """The commit a Text2WetLab clone is at, read from its .git without running git; None when unreadable."""
    git = Path(clone) / ".git"
    try:
        head = (git / "HEAD").read_text().strip()
        if not head.startswith("ref: "):
            return head
        ref = head[5:]
        if (git / ref).is_file():
            return (git / ref).read_text().strip()
        for line in (git / "packed-refs").read_text().splitlines():
            if line.endswith(" " + ref):
                return line.split()[0]
    except OSError:
        pass
    return None


def _task_layout(clone, task_id):
    if not isinstance(task_id, str) or not _TASK_ID.match(task_id) or ".." in task_id:
        raise AdapterError(f"unsafe task id {task_id!r}")
    task = Path(clone) / "tasks" / task_id
    tests = task / "tests"
    if (tests / "checks.py").is_file():
        kind = "rna"
    elif all((tests / n).is_file() for n in ("spec_check.py", "ir.json", "deck.json", "checks.json")):
        kind = "ir"
    else:
        raise AdapterError(f"{task} is not a Text2WetLab task this adapter reads")
    lint = tests / "protocol_lint.py"
    if not lint.is_file():
        lint = Path(clone) / "eval" / "protocol_lint.py"  # the RNA tasks ship none; this is the same file
    if not lint.is_file():
        raise AdapterError("no protocol_lint.py in the task or in eval/")
    data = task / "environment" / "data"
    shipped = tests / "data_hashes.json"
    missing = sorted(rel for rel in (json.loads(shipped.read_text()) if shipped.is_file() else {})
                     if not (data / rel).is_file())
    return kind, tests, lint, data, missing


def grade_protocol(task_id, code, *, clone, grader_python=None, ot_python=None, sim_timeout=300, timeout=1500,
                   work_dir=None, expect_commit=T2WL_COMMIT):
    """Grade the OT-2 protocol source `code` against Text2WetLab task `task_id` (a folder of CLONE/tasks).

    grader_python is the interpreter that runs Text2WetLab's layers (default: $VAXFORGE_OT_PYTHON); ot_python is the
    one Text2WetLab launches for the simulation (default: grader_python). Both must be Python 3.10 with opentrons 7.5.0
    unless grader_python also has pydantic 2. Returns a dict:
      task_id, kind ("ir" or "rna"), t2wl_commit, opentrons
      stage            "lint", "traps", "simulation" or "checks" (the layer that ended the run), or "timeout"
      simulate_pass    True only when lint, traps and the simulator all passed
      rule_results     {rule: {"pass": True | False | None, "t2wl_check": its Text2WetLab check, "detail": its text}}
                       for each of RULES, always all five; detail is Text2WetLab's own text, for the replay rules the
                       first three (event index, code) hits
      rule_violations  rules that are False; gt_diff the sum of gt_diff_detail's counts. Both are None unless simulate_pass
      gt_diff_detail   [{"check", "unit", "count"}], what was counted
      failed_checks    every Text2WetLab check that failed (counted or not), or "lint", "trap:<name>", "simulator_ran"
      lint, traps, ignored_traps, sim_error, notes, not_applicable, and data_files_unavailable when a task ships none
    A verdict on the protocol is always returned, whatever it holds. AdapterError means the adapter or its
    environment is broken (no interpreter, a clone at another commit, a grader that crashed, an environment trap).
    expect_commit=None accepts any clone. sim_timeout bounds the simulation of the nine IR tasks; the RNA task's own
    600 s stays. POSIX only.
    """
    if not isinstance(code, str):
        raise AdapterError("code must be a string")
    python = _interpreter(grader_python)
    ot = _interpreter(ot_python, python)
    kind, tests, lint, data, missing = _task_layout(clone, task_id)
    commit = clone_commit(clone)
    if expect_commit and commit != expect_commit:
        raise AdapterError(f"clone is at {commit}, this adapter was verified at {expect_commit}; pass expect_commit=None to accept")
    version = _preflight(ot)
    meta = {"task_id": task_id, "t2wl_commit": commit, "opentrons": version}
    if len(code) > MAX_CODE_CHARS or "\x00" in code:
        why = f"source of {len(code)} characters, over {MAX_CODE_CHARS}" if len(code) > MAX_CODE_CHARS else "null byte in source"
        return {**meta, **interpret(kind, {"lint": ["refused before lint: " + why]})}
    scratch = Path(tempfile.mkdtemp(prefix="t2wl-", dir=work_dir))
    try:
        (scratch / "home").mkdir()
        protocol, result = scratch / "protocol.py", scratch / "result.json"
        protocol.write_text(code, encoding="utf-8")
        cfg = {"kind": kind, "tests": str(tests), "lint": str(lint), "data": str(data), "protocol": str(protocol),
               "out": str(result), "ot_python": ot, "sim_timeout": sim_timeout,
               # the one trap about the task's /data: paper.txt of ecoli-heat-shock-transformation-hard is fetched at
               # image build time (bioRxiv licence), so it cannot be checked here. Every other trap still runs.
               "ignore_traps": ["task_files_modified"] if missing else []}
        rc, _, err = _run_scrubbed([python, "-I", "-W", "ignore", "-c", _DRIVER, json.dumps(cfg)], scratch / "home", scratch, timeout)
        if rc is None:
            return {**meta, **interpret(kind, {"timeout": timeout})}
        if not result.is_file():
            raise AdapterError(f"grader wrote no result (exit {rc}): {err.strip()[-500:]}")
        raw = json.loads(result.read_text(encoding="utf-8"))
    finally:
        shutil.rmtree(scratch, ignore_errors=True)
    if "driver_error" in raw:
        raise AdapterError(f"grader crashed on {task_id}: {raw['driver_error']}\n{raw.get('traceback', '')}")
    broken = [t for t in raw.get("traps") or [] if t.get("trap") in ENVIRONMENT_TRAPS]
    if broken:
        raise AdapterError(f"the grader's environment tripped {[t['trap'] for t in broken]}: {broken[0].get('detail')}")
    grade = {**meta, **interpret(kind, raw)}
    if missing:
        grade["data_files_unavailable"] = missing
    return grade


# ---- generations.jsonl -> winrate.py records ------------------------------------------------------------------------

def _load_detector():
    """S9's refusal detector (stream C, src/vaxforge/refusal.py) from validation/refusal_detector.py, a byte-identical copy.

    The adapter runs from a path under any Python and imports no other stream's package; stream C wrote that one file to be
    copied unchanged, and validation/tests/test_t2wl_adapter.py fails if the two copies differ.
    """
    path = Path(__file__).resolve().with_name("refusal_detector.py")
    spec = importlib.util.spec_from_file_location("_vaxforge_refusal_detector", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # the detector defines a dataclass, which looks its module up in sys.modules
    spec.loader.exec_module(module)
    return module.detect_refusal


s9_refusal = _load_detector()  # (text, code, finish_reason) -> bool: the detector the S9 win rate is quoted with


def _rows(source):
    if not isinstance(source, (str, os.PathLike)):
        return list(source)
    rows = []
    for n, line in enumerate(Path(source).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                rows.append(json.loads(line))
            except ValueError:
                raise ValueError(f"{source} line {n} is not valid JSON") from None
    return rows


def _needs_grade(row, refused):
    code = row.get("code")
    return (row.get("split") == "heldout" and isinstance(code, str) and bool(code.strip())
            and row.get("finish_reason") != "length" and not refused(row.get("text"), code, row.get("finish_reason")))


def grade_rows(generations_jsonl, *, clone, refused=s9_refusal, **grader):
    """Grade exactly the held-out rows build_records will need a grade for. Returns grades tagged with task_id and arm.

    grader takes grade_protocol's other keyword arguments (grader_python, ot_python, sim_timeout, ...).
    """
    grades = []
    for row in _rows(generations_jsonl):
        if _needs_grade(row, refused):
            grades.append({**grade_protocol(row["task_id"], row["code"], clone=clone, **grader),
                           "task_id": row["task_id"], "arm": row["arm"]})
    return grades


def build_records(generations_jsonl, grades, refused=s9_refusal, splits=WINRATE_SPLITS):
    """winrate.py records from generations.jsonl rows (a path, or an iterable of rows) plus their grades.

    grades: grade_rows' list, or any mapping {(task_id, arm): grade}. Arm "base" is model "base" and arm "finetune" is
    model "finetune"; any other arm raises. outcome is "refused" only when refused(text, code, finish_reason) says so
    (simulate_pass None); a reply cut off by max_tokens (finish_reason "length") is never asked, as in
    s9_5_finetune.apply_refusals: a truncated answer is a failed attempt. Otherwise it is "attempted": no extractable
    code, or finish_reason "length", is simulate_pass False, and so is a grade that failed; a passing grade adds
    rule_violations and gt_diff. Rows of other splits (candidate, pipeline) are not win-rate input and are skipped. A
    probe row has no Text2WetLab task to grade (it feeds the refusal rates only): when attempted it carries the schema's
    simulate_pass False and graded False. A held-out row that needs a grade and has none raises.
    """
    index = grades if isinstance(grades, collections.abc.Mapping) else {(g["task_id"], g["arm"]): g for g in grades}
    records = []
    for row in _rows(generations_jsonl):
        if row.get("split") not in splits:
            continue
        absent = [k for k in ("task_id", "family", "arm") if k not in row]
        if absent:
            raise ValueError(f"generations row {row.get('task_id')!r} has no {absent}")
        if row.get("arm") not in ARM_MODELS:
            raise ValueError(f"unknown arm {row.get('arm')!r} for task {row.get('task_id')!r}: expected one of {sorted(ARM_MODELS)}")
        record = {"task_id": row["task_id"], "family": row["family"], "model": ARM_MODELS[row["arm"]], "split": row["split"]}
        text, code, finish = row.get("text"), row.get("code"), row.get("finish_reason")
        if finish != "length" and refused(text, code, finish):
            record.update(outcome="refused", simulate_pass=None)
        elif not (isinstance(code, str) and code.strip()) or finish == "length":
            record.update(outcome="attempted", simulate_pass=False)
        elif row["split"] != "heldout":
            record.update(outcome="attempted", simulate_pass=False, graded=False)
        else:
            grade = index.get((row["task_id"], row["arm"]))
            if grade is None:
                raise ValueError(f"no grade for task {row['task_id']!r}, arm {row['arm']!r}")
            record.update(outcome="attempted", simulate_pass=bool(grade["simulate_pass"]))
            if record["simulate_pass"]:
                record.update(rule_violations=grade["rule_violations"], gt_diff=grade["gt_diff"])
        records.append(record)
    return records


# ---- the held-out tasks -> tasks.jsonl ---------------------------------------------------------------------------------

def heldout_tasks(clone, families_file=FAMILIES_FILE):
    """The held-out tasks as s9_5_generate.load_tasks takes them, and the ones that could not be built: (tasks, skipped).

    The prompt is the task's instruction.md, unchanged, as Text2WetLab gives it to its agents. Where the instruction sends
    the agent to /data/paper.txt, the paper's text follows under a heading that names the file: a model that cannot open
    files has no other way to read it. A task whose instruction cites the paper but whose paper is not in the clone (the
    E. coli hard task fetches it when its image is built) is not run without it: it is returned in skipped as
    (task_id, reason). The families come from families_file (validation/heldout_families.json), in its order.
    """
    clone = Path(clone)
    families = json.loads(Path(families_file).read_text(encoding="utf-8"))["families"]
    tasks, skipped = [], []
    for family, task_ids in families.items():
        for task_id in task_ids:
            if not _TASK_ID.match(task_id):
                raise AdapterError(f"unsafe task id {task_id!r} in {families_file}")
            folder = clone / "tasks" / task_id
            try:
                prompt = (folder / "instruction.md").read_text(encoding="utf-8")
            except OSError as e:
                raise AdapterError(f"{folder / 'instruction.md'} is not readable ({e.strerror}): is {clone} a Text2WetLab checkout?") from None
            if PAPER_FILE in prompt:
                paper = folder / "environment" / "data" / "paper.txt"
                if not paper.is_file():
                    skipped.append((task_id, f"its instruction cites {PAPER_FILE} and the clone does not have it"))
                    continue
                prompt = prompt.rstrip("\n") + f"\n\n## The text of `{PAPER_FILE}`\n\n" + paper.read_text(encoding="utf-8").strip("\n") + "\n"
            tasks.append({"task_id": task_id, "family": family, "split": "heldout", "prompt": prompt})
    return tasks, skipped


# ---- command line ---------------------------------------------------------------------------------------------------

def main(argv=None):
    ap = argparse.ArgumentParser(prog="vaxforge finetune", description="Text2WetLab held-out tasks and grading for S9.5")
    sub = ap.add_subparsers(dest="command", required=True)
    t = sub.add_parser("tasks", help="write tasks.jsonl for the held-out tasks from a Text2WetLab checkout")
    t.add_argument("clone", help=f"Text2WetLab checkout at {T2WL_COMMIT[:7]}")
    t.add_argument("--families", default=str(FAMILIES_FILE), help="held-out families JSON")
    t.add_argument("--out", required=True, help="tasks.jsonl to write")
    g = sub.add_parser("grade", help="grade generations.jsonl with Text2WetLab's own layers; write winrate.py's input")
    g.add_argument("generations", help="generations.jsonl from `vaxforge finetune generate`")
    g.add_argument("--clone", required=True, help=f"Text2WetLab checkout at {T2WL_COMMIT[:7]}")
    g.add_argument("--out", required=True, help="records JSONL for `vaxforge finetune winrate`; the full grades go beside it")
    g.add_argument("--grader-python", help=f"Python 3.10 with opentrons {OPENTRONS_VERSION} (default: ${OT_PYTHON_ENV})")
    args = ap.parse_args(argv)
    try:
        if args.command == "tasks":
            tasks, skipped = heldout_tasks(args.clone, args.families)
            Path(args.out).write_text("".join(json.dumps(t, ensure_ascii=False) + "\n" for t in tasks), encoding="utf-8")
            print(f"{len(tasks)} tasks written to {args.out}")
            for task_id, why in skipped:
                print(f"skipped {task_id}: {why}", file=sys.stderr)
            return 0
        grades = grade_rows(args.generations, clone=args.clone, grader_python=args.grader_python)
        records = build_records(args.generations, grades)
    except (AdapterError, ValueError, OSError) as e:
        print(f"t2wl_adapter: {e}", file=sys.stderr)
        return 1
    out = Path(args.out)
    out.write_text("".join(json.dumps(r) + "\n" for r in records), encoding="utf-8")
    out.with_name(out.stem + ".grades.json").write_text(json.dumps(grades, indent=2) + "\n", encoding="utf-8")
    for g in grades:
        verdict = "pass" if g["simulate_pass"] else "FAIL"
        print(f"{g['arm']:9s} {g['task_id']:40s} {verdict}  rule violations {g['rule_violations']}  diff {g['gt_diff']}"
              + ("" if g["simulate_pass"] else f"  ({', '.join(g['failed_checks'])})"))
    print(f"{len(records)} records written to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
