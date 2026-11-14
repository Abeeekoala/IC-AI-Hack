"""Our five-rule check against Text2WetLab's own grader: two independent implementations, compared rule by rule.

Ours:    vaxforge.s9_validate.simulate + check_physical_rules (stream C), given the initial well contents that the task's
         IR and deck imply (`derive_initial` below).
Theirs:  validation/t2wl_adapter.py `grade_protocol` (stream D), which runs Text2WetLab's lint, anti-hack traps, simulation and
         `spec_check.check` in a subprocess. It is loaded BY PATH, never imported as a package, and never edited.

Text2WetLab protocols are untrusted code. They are only ever run through `s9_validate.simulate` (our lint first, then a
subprocess) and through D's adapter (its own lint and traps first, then a subprocess). Nothing here execs them, and nothing is
written into the clone.

The reference solutions of the nine IR tasks and the mutations in tests/fixtures/t2wl/mutations.json (data: task, rule
targeted, description, edit operations) are graded by both. The tests assert, per rule, that the two verdicts agree, that the
counts agree (exactly where D's detail text shows every hit, i.e. fewer than three), and that the first flagged events (and, for
cross-contamination, wells) are the same. A mutation the simulator refuses must be refused by both. Known, explained disagreements
are `disagree` entries in the data and strict expected failures here; `both_wrong` marks the cases where the two agree on a verdict
that is physically wrong (one tip flag for two pipettes). Method, numbers and findings: docs/streamc_t2wl_agreement.md.

The tests against Text2WetLab are skipped, with the reason, unless a clone (T2WL_CLONE, default ../_t2wl next to the repo) at the
commit the adapter was verified against and an interpreter with opentrons 7.5.0 (VAXFORGE_OT_PYTHON) are available. The helpers
(`expand_wells`, `derive_initial`, `apply_ops`, `compare`) and the mutation data are tested without them. The whole matrix takes
about two minutes at six workers (T2WL_WORKERS).

Windows: D's adapter is POSIX only (documented in `grade_protocol`). Its child processes get an environment of PATH=/usr/bin:/bin
and HOME only, which a Windows Python cannot start in (no SYSTEMROOT), and Text2WetLab's data-hash trap fails on a CRLF checkout
and on the `\\` in a relative path. On Windows (os.name == "nt") only, `load_adapter` therefore replaces two of the adapter's
module-level functions on the loaded copy: `_run_scrubbed` (same contract, an allow-listed environment, the process tree killed
on a timeout) and `_task_layout` (hands the adapter an LF copy of a task's /data, and only when that copy hashes exactly as the
task ships it). No verdict logic is touched; on POSIX nothing is replaced. The adapter file in validation/ is not modified.

Run `python tests/wetlab/test_t2wl_agreement.py --help` for the command line: `all --table` prints the tables of
docs/streamc_t2wl_agreement.md, `--only ID` runs the cases whose id contains ID, `rna` tries the two RNA tasks.
"""

from __future__ import annotations

import argparse
import ast
import atexit
import collections
import concurrent.futures
import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
from pathlib import Path

import pytest

from vaxforge import s9_validate as v

REPO = Path(__file__).resolve().parents[2]
ADAPTER_FILE = REPO / "validation" / "t2wl_adapter.py"
MUTATIONS_FILE = REPO / "tests" / "fixtures" / "t2wl" / "mutations.json"
RULES = v.RULES
AMPLE_UL = 1e9  # "a stock of unlimited volume", as check_physical_rules can only be told for a non-reservoir well
ROWS = "ABCDEFGH"
IR_TASKS = (
    "a1-a12-100ul", "ampure-bead-cleanup", "colony-pcr-screening", "colony-pcr-screening-hard",
    "ecoli-heat-shock-transformation", "ecoli-heat-shock-transformation-hard", "golden-gate-assembly",
    "golden-gate-assembly-hard", "split-200ul-two-wells",
)
RNA_TASKS = ("opentrons-rna-extraction", "opentrons-rna-extraction-hard")


# ---- locating the clone, the interpreter and the adapter ---------------------------------------------------------------


def find_clone() -> Path | None:
    env = os.environ.get("T2WL_CLONE")
    for cand in ([Path(env)] if env else [REPO.parent / "_t2wl"]):
        if (cand / "tasks").is_dir():
            return cand.resolve()
    return None


def _scrubbed_env_nt(home: Path) -> dict[str, str]:
    root = os.environ.get("SYSTEMROOT") or os.environ.get("SystemRoot") or r"C:\Windows"
    scratch = str(home)
    # an allow-list, like the adapter's own: nothing of the caller's environment (so no key) reaches the grader
    return {"SYSTEMROOT": root, "WINDIR": root, "PATH": root + r"\System32;" + root, "HOME": scratch, "USERPROFILE": scratch,
            "APPDATA": scratch, "LOCALAPPDATA": scratch, "TEMP": scratch, "TMP": scratch, "PYTHONUTF8": "1"}


def _run_scrubbed_nt(cmd, home, cwd, timeout):
    """The contract of the adapter's `_run_scrubbed` on Windows: (returncode or None on timeout, stdout, stderr)."""
    proc = subprocess.Popen(cmd, cwd=str(cwd), env=_scrubbed_env_nt(Path(home)), stdin=subprocess.DEVNULL,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8", errors="replace",
                            creationflags=subprocess.CREATE_NEW_PROCESS_GROUP)
    try:
        out, err = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(proc.pid)], capture_output=True)
        proc.communicate()
        return None, "", ""
    return proc.returncode, out, err


_LF_COPIES: dict[str, Path] = {}
_LF_LOCK = threading.Lock()
_LF_ROOT: list[Path] = []


def _lf_data(tests: Path, data: Path) -> Path:
    """`data` itself, or a copy of it with CRLF -> LF when that makes every file the task ships hash as shipped."""
    shipped = Path(tests) / "data_hashes.json"
    data = Path(data)
    expected = json.loads(shipped.read_text(encoding="utf-8")) if shipped.is_file() else {}
    files = [p for p in data.rglob("*") if p.is_file()] if data.is_dir() else []
    if not expected or not any(b"\r\n" in p.read_bytes() for p in files):
        return data
    key = str(data)
    with _LF_LOCK:
        if key not in _LF_COPIES:
            if not _LF_ROOT:
                _LF_ROOT.append(Path(tempfile.mkdtemp(prefix="t2wl-lf-")))
                atexit.register(shutil.rmtree, _LF_ROOT[0], True)
            copy = _LF_ROOT[0] / hashlib.sha256(key.encode()).hexdigest()[:12]
            for p in files:
                dst = copy / p.relative_to(data)
                dst.parent.mkdir(parents=True, exist_ok=True)
                dst.write_bytes(p.read_bytes().replace(b"\r\n", b"\n"))
            ok = all(hashlib.sha256((copy / rel).read_bytes()).hexdigest() == want
                     for rel, want in expected.items() if (copy / rel).is_file())
            _LF_COPIES[key] = copy if ok else data
        return _LF_COPIES[key]


_ADAPTER: list = []


def load_adapter():
    """validation/t2wl_adapter.py loaded by path (once). On Windows two functions of the loaded copy are replaced, see above."""
    if not _ADAPTER:
        spec = importlib.util.spec_from_file_location("t2wl_adapter_under_test", ADAPTER_FILE)
        mod = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = mod
        spec.loader.exec_module(mod)
        if os.name == "nt":
            original = mod._task_layout

            def layout(clone, task_id):
                kind, tests, lint, data, missing = original(clone, task_id)
                return kind, tests, lint, _lf_data(tests, data), missing

            mod._run_scrubbed = _run_scrubbed_nt
            mod._task_layout = layout
        _ADAPTER.append(mod)
    return _ADAPTER[0]


def skip_reason() -> str | None:
    """Why this comparison cannot run here, or None."""
    clone = find_clone()
    if clone is None:
        return "no Text2WetLab clone: set T2WL_CLONE (or put one at ../_t2wl next to the repo)"
    if not ADAPTER_FILE.is_file():
        return "validation/t2wl_adapter.py is missing"
    if not (os.environ.get("VAXFORGE_OT_PYTHON") or v.find_ot_python()):
        return "no Python 3.10 interpreter with opentrons 7.5.0: set VAXFORGE_OT_PYTHON"
    ad = load_adapter()
    commit = ad.clone_commit(clone)
    if commit != ad.T2WL_COMMIT:
        return f"the clone is at {commit}; the adapter was verified at {ad.T2WL_COMMIT}"
    return None


def ot_python() -> str:
    return os.environ.get("VAXFORGE_OT_PYTHON") or v.find_ot_python()


# ---- item 1: the initial contents of a task, from its IR and deck ------------------------------------------------------


def _rc(well: str) -> tuple[int, int]:
    m = re.fullmatch(r"([A-H])(\d{1,2})", well)
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError(f"bad 96-well position {well!r}")
    return ROWS.index(m.group(1)), int(m.group(2))


def expand_wells(wells: list[str], kind: str) -> list[str]:
    """['A1:A3', 'B1'] -> ['A1', 'A2', 'A3', 'B1']. An empty list (a tube, a one-well reservoir) -> [''].

    The rule of Text2WetLab's paper2protocol.check.expand_wells: a range is the rectangle between two corners."""
    if not wells:
        return [""]
    out: list[str] = []
    for w in wells:
        w = w.strip().upper().replace("-", ":").replace("\u2013", ":")
        if ":" in w:
            a, b = w.split(":", 1)
            (ra, ca), (rb, cb) = _rc(a), _rc(b)
            out += [f"{ROWS[r]}{c}" for r in range(min(ra, rb), max(ra, rb) + 1) for c in range(min(ca, cb), max(ca, cb) + 1)]
        else:
            if kind.startswith("plate_96"):
                _rc(w)
            out.append(w)
    return out


def _is_reservoir(load_name: str) -> bool:
    return any(word in load_name.lower() for word in ("reservoir", "trough"))


def derive_initial(ir: dict, deck: dict, sim: dict) -> tuple[dict, set, list]:
    """What a task's IR says is in the wells before the run, in the form `check_physical_rules` takes.

    Follows `spec_check._deck_mapping` and `spec_check.check` (container -> deck label -> slot; the IR's initial contents):
      * only containers some step uses are mapped, and only when the run really loaded ONE labware with the deck's label,
        in the deck's slot, with the deck's load name (otherwise spec_check reports `deck_labware` and gives that container
        no contents; so do we, and the container is listed in `unmapped`). A slot written "<module> on <slot>" is the
        module's slot, which is how our simulator names a labware sitting on a module;
      * the IR's wells are expanded, volumes of one well from several entries are added, and `null` stays null;
      * a tube or one-well reservoir has the empty well name in the IR and the deck's "well" (A1, B1, ...) in the run;
      * a reservoir is a stock whatever the IR says: it gets no entry (check_physical_rules treats an undeclared reservoir
        well as ample, as spec_check does);
      * any other well with volume null is an ample stock (a source plate, a reagent tube): it goes in `ample`;
      * any other well with a volume is tracked exactly (a plate, a tube): it goes in `initial`.
    Returns (initial {(slot, well): uL}, ample {(slot, well)}, unmapped [container]). `ample` wells are handed to
    `check_physical_rules` at AMPLE_UL, because that function has no notion of an ample stock outside reservoirs.
    """
    kinds = {c["name"]: c["kind"] for c in ir["containers"]}
    used = {s["source"] for s in ir["steps"] if s.get("source")} | {s["dest"] for s in ir["steps"] if s.get("dest")}
    run_labware = sim["deck"]["labware"]
    mapping: dict[str, tuple[str, str, str]] = {}
    unmapped: list[str] = []
    for cname, spec in deck["containers"].items():
        if cname not in used:
            continue
        slot = str(spec["slot"]).rsplit(" on ", 1)[-1]
        hits = [lw for lw in run_labware if lw["display_name"] == spec["label"]]
        if len(hits) == 1 and hits[0]["slot"] == slot and hits[0]["load_name"] == spec["load_name"]:
            mapping[cname] = (slot, spec.get("well", ""), spec["load_name"])
        else:
            unmapped.append(cname)
    state: dict[tuple[str, str], float | None] = {}
    for c in ir["initial_contents"]:
        if c["container"] not in kinds:
            continue
        for w in expand_wells(c["wells"], kinds[c["container"]]):
            prev = state.get((c["container"], w), 0.0)
            state[(c["container"], w)] = None if c["volume_ul"] is None or prev is None else prev + c["volume_ul"]
    initial: dict[tuple[str, str], float] = {}
    ample: set[tuple[str, str]] = set()
    for (cname, w), volume in state.items():
        if cname not in mapping:
            continue
        slot, tube_well, load_name = mapping[cname]
        well = tube_well if w == "" and tube_well else w
        if _is_reservoir(load_name):
            continue
        if volume is None:
            ample.add((slot, well))
        else:
            initial[(slot, well)] = volume
    return initial, ample, unmapped


def task_files(clone: Path, task: str) -> tuple[dict, dict]:
    tests = clone / "tasks" / task / "tests"
    return (json.loads((tests / "ir.json").read_text(encoding="utf-8")),
            json.loads((tests / "deck.json").read_text(encoding="utf-8")))


def reference_code(clone: Path, task: str) -> str:
    return (clone / "tasks" / task / "solution" / "protocol.py").read_text(encoding="utf-8")


# ---- the two graders, one protocol -------------------------------------------------------------------------------------


REPLAY_RULES = RULES[:3]  # the rules whose detail text in spec_check lists (event number, code) for the first three hits
NOT_PLAIN = frozenset({"delay", "pause", "other", "magnet_engage", "magnet_disengage", "temperature_set", "temperature_wait",
                       "temperature_deactivate"})  # kinds that Text2WetLab's runlog may number too (its delay, engage, temp, thermocycler)


def run_ours(code: str, ir: dict, deck: dict) -> dict:
    sim = v.simulate(code, ot_python=ot_python(), timeout=300)
    if not sim.get("ok"):
        err = sim.get("error") or {}
        return {"simulated": False, "error": err.get("type"), "message": err.get("message")}
    initial, ample, unmapped = derive_initial(ir, deck, sim)
    rep = v.check_physical_rules(sim, {**initial, **{k: AMPLE_UL for k in ample}})
    # Text2WetLab numbers its events (event_idx in its detail text) over the kinds its runlog parses. On a run that has only
    # pipetting events that is the position among our atomic events, so the flagged events can be compared, not only counted.
    place = {e["idx"]: n for n, e in enumerate(v.leaf_events(sim))}
    plain = not any(e["kind"] in NOT_PLAIN for e in sim["events"])
    positions = {r: sorted(place[x.event_idx] for x in rep.violations[r] if x.event_idx in place) for r in REPLAY_RULES} if plain else None
    return {"simulated": True, "counts": rep.counts, "other": len(rep.other), "unmapped": unmapped,
            "initial_wells": len(initial), "ample_wells": len(ample), "aspirations": rep.aspirations,
            "positions": positions,
            "cc_wells": [x.detail.removeprefix("well ") for x in rep.violations["no_cross_contamination"]]}


def _their_hits(detail: str, passed: bool, rule: str) -> tuple[int, bool, list]:
    """(violations, exact, wells) from one rule's detail text: spec_check keeps the first three hits as a list, nothing for
    tip_dropped_at_end (one or none by definition)."""
    if rule == "tip_dropped_at_end":
        return (0 if passed else 1), True, []
    try:
        hits = ast.literal_eval(detail) if detail.strip().startswith("[") else []
    except (ValueError, SyntaxError):
        return (0 if passed else 1), False, []
    wells = [h[1] for h in hits if isinstance(h, tuple) and len(h) == 2 and rule == "no_cross_contamination"]
    return len(hits), len(hits) < 3, wells


def _their_idxs(detail: str, rule: str) -> list[int] | None:
    """The event numbers in the first hits of a replay rule's detail text; None for a rule whose text has none."""
    if rule not in REPLAY_RULES:
        return None
    try:
        hits = ast.literal_eval(detail) if detail.strip().startswith("[") else []
    except (ValueError, SyntaxError):
        return None
    return [h[0] for h in hits if isinstance(h, tuple) and len(h) == 2]


def run_theirs(task: str, code: str, clone: Path) -> dict:
    ad = load_adapter()
    try:
        g = ad.grade_protocol(task, code, clone=clone, grader_python=ot_python())
    except ad.AdapterError as exc:
        return {"simulated": False, "adapter_error": str(exc)[:600]}
    out = {"simulated": bool(g["simulate_pass"]), "stage": g["stage"], "failed_checks": g["failed_checks"],
           "sim_error": g["sim_error"], "commit": g["t2wl_commit"], "rules": {}}
    for rule, r in g["rule_results"].items():
        n, exact, wells = _their_hits(r["detail"], r["pass"] is not False, rule) if r["pass"] is not None else (None, False, [])
        out["rules"][rule] = {"pass": r["pass"], "count": n, "exact": exact, "wells": wells,
                              "idxs": _their_idxs(r["detail"], rule) if r["pass"] is not None else None}
    return out


def compare(ours: dict, theirs: dict) -> dict:
    """Per rule: verdict agreement, and count agreement where D's text makes the count exact (>= 3 hits are cut off)."""
    if "adapter_error" in theirs:
        return {"status": "adapter_error", "rules": {}}
    if not (ours["simulated"] and theirs["simulated"]):
        status = "unsimulated_in_both" if not (ours["simulated"] or theirs["simulated"]) else "simulated_in_one"
        return {"status": status, "rules": {}}
    rules = {}
    for rule in RULES:
        n_ours, t = ours["counts"][rule], theirs["rules"][rule]
        verdict = (n_ours == 0) == bool(t["pass"])
        count = n_ours == t["count"] if t["exact"] else n_ours >= (t["count"] or 0)
        if rule == "no_cross_contamination" and count and t["wells"]:
            count = ours["cc_wells"][: len(t["wells"])] == t["wells"]  # the first hits are the same wells, in the same order
        mine, theirs_idxs = (ours.get("positions") or {}).get(rule), t.get("idxs")
        events = mine is not None and theirs_idxs is not None  # both sides can say which events they flagged
        rules[rule] = {"ours": n_ours, "their_pass": t["pass"], "their_count": t["count"], "exact": t["exact"],
                       "verdict_agrees": verdict, "count_agrees": count, "events_compared": events,
                       "events_agree": mine[: len(theirs_idxs)] == theirs_idxs if events else True}
    return {"status": "compared", "rules": rules}


# ---- mutations as data -------------------------------------------------------------------------------------------------


def load_mutations() -> list[dict]:
    return json.loads(MUTATIONS_FILE.read_text(encoding="utf-8"))


def apply_ops(code: str, ops: list[list]) -> str:
    """The edit operations of one mutation. Each must hit exactly where it says, so that no mutation is a silent no-op.
      ["replace", old, new]          old occurs once (or the 4th item's number of times)
      ["line", prefix, old, new]     the one line that starts with prefix (after its indentation) holds old; replace it there
      ["set_line", prefix, [stmt..]] the one line that starts with prefix is replaced by these statements, at its indentation
                                     (a statement may carry extra leading spaces of its own, for a loop body)
      ["append", [stmt..]]           these statements at the end of run(), indented by four spaces
    """
    for op in ops:
        kind = op[0]
        if kind == "replace":
            old, new, times = op[1], op[2], (op[3] if len(op) > 3 else 1)
            if code.count(old) != times:
                raise ValueError(f"{old[:60]!r} occurs {code.count(old)} times, expected {times}")
            code = code.replace(old, new)
        elif kind in ("line", "set_line"):
            prefix = op[1]
            lines = code.split("\n")
            hit = [i for i, line in enumerate(lines) if line.lstrip().startswith(prefix)]
            if len(hit) != 1:
                raise ValueError(f"line starting {prefix!r}: {len(hit)} candidates, expected 1")
            if kind == "line":
                old, new = op[2], op[3]
                if old not in lines[hit[0]]:
                    raise ValueError(f"line starting {prefix!r} does not hold {old!r}")
                lines[hit[0]] = lines[hit[0]].replace(old, new)
            else:
                indent = lines[hit[0]][: len(lines[hit[0]]) - len(lines[hit[0]].lstrip())]
                lines[hit[0]] = "\n".join(indent + stmt for stmt in op[2])
            code = "\n".join(lines)
        elif kind == "append":
            code = code.rstrip("\n") + "\n" + "\n".join("    " + stmt for stmt in op[1]) + "\n"
        else:
            raise ValueError(f"unknown operation {kind!r}")
    return code


# ---- running everything ------------------------------------------------------------------------------------------------


def cases(clone: Path) -> list[dict]:
    """Every protocol both graders see: the nine references, then the mutations. Each has id, task, kind, rule, description, code."""
    out = [{"id": f"ref:{t}", "task": t, "kind": "reference", "rule": None, "targets": [], "simulates": True, "disagree": {},
            "description": "the reference solution", "code": reference_code(clone, t)} for t in IR_TASKS]
    for m in load_mutations():
        out.append({"id": m["id"], "task": m["task"], "kind": "mutation", "rule": m["rule"], "targets": m.get("targets", []),
                    "simulates": m.get("simulates", True), "disagree": m.get("disagree", {}), "description": m["description"],
                    "code": apply_ops(reference_code(clone, m["task"]), m["ops"])})
    return out


def run_case(case: dict, clone: Path, cache: dict | None = None) -> dict:
    key = hashlib.sha256((case["task"] + "\0" + case["code"]).encode()).hexdigest()
    if cache is not None and key in cache:
        graded = cache[key]
    else:
        ir, deck = task_files(clone, case["task"])
        ours = run_ours(case["code"], ir, deck)
        theirs = run_theirs(case["task"], case["code"], clone)
        graded = {"ours": ours, "theirs": theirs, "compare": compare(ours, theirs)}
        if cache is not None:
            cache[key] = graded
    return {k: case[k] for k in ("id", "task", "kind", "rule", "targets", "simulates", "disagree", "description")} | graded


def run_cases(selected: list[dict], clone: Path, workers: int = 3, cache: dict | None = None,
              on_result=None) -> dict[str, dict]:
    results: dict[str, dict] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(run_case, c, clone, cache): c["id"] for c in selected}
        for fut in concurrent.futures.as_completed(futures):
            results[futures[fut]] = fut.result()
            if on_result:
                on_result()
            print(f"  done {futures[fut]} ({len(results)}/{len(selected)})", file=sys.stderr, flush=True)
    return results


def disagreements(res: dict) -> list[str]:
    """The rules of one compared case on which the two graders differ (in verdict, or in the count where it is exact)."""
    rules = res["compare"]["rules"]
    return [r for r in RULES if r in rules and not (rules[r]["verdict_agrees"] and rules[r]["count_agrees"] and rules[r]["events_agree"])]


def _brief(res: dict) -> str:
    o, t, c = res["ours"], res["theirs"], res["compare"]
    if "adapter_error" in t:
        return "THEIRS ADAPTER ERROR: " + t["adapter_error"][:200]
    if c["status"] != "compared":
        return (f"{c['status']}: ours {'ok' if o['simulated'] else 'FAILED ' + str(o.get('error'))}, "
                f"theirs {'ok' if t['simulated'] else 'FAILED at ' + t['stage'] + ' ' + str(t['sim_error'] or t['failed_checks'])}")
    flagged = {r: x["ours"] for r, x in c["rules"].items() if x["ours"]}
    their = [r for r, x in c["rules"].items() if x["their_pass"] is False]
    bad = disagreements(res)
    return f"ours {flagged or '-'} | theirs {their or '-'} | " + ("AGREE" if not bad else "DISAGREE on " + ", ".join(bad))


# ---- the numbers for docs/streamc_t2wl_agreement.md ----------------------------------------------------------------------


def summarize(results: dict[str, dict]) -> dict:
    """Counts over the results. A mutation is `compared` when both graders simulated it; every rule of it is then one
    comparison (five per mutation). A comparison agrees when the verdicts agree and the counts do (exactly where D's text shows
    them all, i.e. fewer than three hits; ours >= theirs with the same first wells where it shows only three)."""
    refs = {rid: r for rid, r in results.items() if r["kind"] == "reference"}
    muts = {rid: r for rid, r in results.items() if r["kind"] == "mutation"}
    out: dict = {"references": {}, "by_rule": {r: dict.fromkeys(
        ("compared", "both_flag", "both_clean", "only_ours", "only_theirs", "count_exact", "count_exact_agree", "count_cut", "count_cut_agree",
         "events_compared", "events_agree"), 0) for r in RULES}, "by_task": {}, "by_family": {}, "status": {}}
    out["matrix"] = {}  # task -> rule -> [agreements, comparisons], over the reference and its mutations

    def tally(r: dict) -> None:
        bad = disagreements(r)
        for rule in r["compare"]["rules"]:
            cell = out["matrix"].setdefault(r["task"], {}).setdefault(rule, [0, 0])
            cell[0] += rule not in bad
            cell[1] += 1

    for r in refs.values():
        c = r["compare"]
        tally(r)
        out["references"][r["task"]] = {"status": c["status"], "ours": sum(x["ours"] for x in c["rules"].values()),
                                        "theirs": sum(1 for x in c["rules"].values() if x["their_pass"] is False),
                                        "disagree": len(disagreements(r))}
    for r in muts.values():
        c = r["compare"]
        out["status"][c["status"]] = out["status"].get(c["status"], 0) + 1
        fam = out["by_family"].setdefault(r["rule"], {"built": 0, "compared": 0, "unsimulated_in_both": 0, "simulated_in_one": 0,
                                                       "adapter_error": 0, "comparisons": 0, "disagreements": 0})
        task = out["by_task"].setdefault(r["task"], {"built": 0, "compared": 0, "comparisons": 0, "disagreements": 0})
        fam["built"] += 1
        task["built"] += 1
        if c["status"] != "compared":
            fam[c["status"]] += 1
            continue
        fam["compared"] += 1
        task["compared"] += 1
        tally(r)
        bad = disagreements(r)
        for rule, x in c["rules"].items():
            row = out["by_rule"][rule]
            ours_flags, theirs_flags = x["ours"] > 0, x["their_pass"] is False
            row["compared"] += 1
            row["both_flag" if ours_flags and theirs_flags else "both_clean" if not (ours_flags or theirs_flags)
                else "only_ours" if ours_flags else "only_theirs"] += 1
            if ours_flags and theirs_flags:
                row["count_exact" if x["exact"] else "count_cut"] += 1
                row["count_exact_agree" if x["exact"] else "count_cut_agree"] += bool(x["count_agrees"])
                if x["events_compared"]:
                    row["events_compared"] += 1
                    row["events_agree"] += bool(x["events_agree"])
            for part in (fam, task):
                part["comparisons"] += 1
                part["disagreements"] += rule in bad
    return out


def markdown(summary: dict) -> str:
    L = ["**References** (ours = violations over the five rules, theirs = rules failed)", "",
         "| task | compared | ours | theirs | disagreements |", "|---|---|---|---|---|"]
    for task in IR_TASKS:
        x = summary["references"].get(task)
        if x:
            L.append(f"| {task} | {x['status']} | {x['ours']} | {x['theirs']} | {x['disagree']} |")
    L += ["", "**By task and rule** (agreements / comparisons, the reference and the mutations both graders simulated)", "",
          "| task | " + " | ".join(RULES) + " |", "|---|" + "---|" * len(RULES)]
    for task in IR_TASKS:
        row = summary["matrix"].get(task, {})
        L.append(f"| {task} | " + " | ".join(f"{row[r][0]}/{row[r][1]}" if r in row else "-" for r in RULES) + " |")
    L += ["", "**Mutations, by rule** (a comparison is one rule of one mutation both graders simulated)", "",
          "| rule | comparisons | both flag | both clean | only ours flags | only theirs flags | exact counts agree | cut-off counts agree | same first events |",
          "|---|---|---|---|---|---|---|---|---|"]
    for rule, x in summary["by_rule"].items():
        L.append(f"| {rule} | {x['compared']} | {x['both_flag']} | {x['both_clean']} | {x['only_ours']} | {x['only_theirs']} | "
                 f"{x['count_exact_agree']}/{x['count_exact']} | {x['count_cut_agree']}/{x['count_cut']} | {x['events_agree']}/{x['events_compared']} |")
    tot = {k: sum(x[k] for x in summary["by_rule"].values()) for k in next(iter(summary["by_rule"].values()))}
    L.append(f"| **all five** | {tot['compared']} | {tot['both_flag']} | {tot['both_clean']} | {tot['only_ours']} | {tot['only_theirs']} | "
             f"{tot['count_exact_agree']}/{tot['count_exact']} | {tot['count_cut_agree']}/{tot['count_cut']} | {tot['events_agree']}/{tot['events_compared']} |")
    L += ["", "**Mutations, by task**", "", "| task | built | compared | comparisons | disagreements |", "|---|---|---|---|---|"]
    for task in IR_TASKS:
        x = summary["by_task"].get(task)
        if x:
            L.append(f"| {task} | {x['built']} | {x['compared']} | {x['comparisons']} | {x['disagreements']} |")
    L += ["", "**Mutations, by family**", "",
          "| family | built | compared | not simulated by either | simulated by one only | comparisons | disagreements |", "|---|---|---|---|---|---|---|"]
    for fam, x in summary["by_family"].items():
        L.append(f"| {fam} | {x['built']} | {x['compared']} | {x['unsimulated_in_both']} | {x['simulated_in_one']} | {x['comparisons']} | {x['disagreements']} |")
    return "\n".join(L)


# ---- the two RNA tasks: no IR, no spec_check ---------------------------------------------------------------------------


def run_rna(task: str, clone: Path) -> dict:
    """Text2WetLab runs no spec_check on the RNA tasks, so the five rules cannot be compared one for one. What can be put side
    by side: ours on the reference (each reservoir well declared full, nothing else declared) against the two RNA checks the
    adapter maps (reservoir_columns_within_15ml -> no_aspirate_from_empty_well, fresh_tip_per_sample_no_cross_contact ->
    no_cross_contamination). They are different rules (a capacity limit, and one tip per sample), so no agreement is expected."""
    code = reference_code(clone, task)
    sim = v.simulate(code, ot_python=ot_python(), timeout=900)
    ours: dict = {"simulated": bool(sim.get("ok"))}
    if sim.get("ok"):
        labware = {lw["slot"]: lw for lw in sim["deck"]["labware"]}
        stock = {slot for slot, lw in labware.items() if v._is_stock(lw["load_name"])}
        full = {(slot, w): float(info["max_volume_ul"]) for slot in stock for w, info in labware[slot]["wells"].items()}
        rep = v.check_physical_rules(sim, full)
        on_stock = [x for x in rep.violations["no_aspirate_from_empty_well"]
                    if (m := re.search(r"on slot (\S+) holds", x.detail)) and m.group(1) in stock]
        ours.update(counts=rep.counts, aspirations=rep.aspirations, overdrawn_reservoir_asks=len(on_stock), other=len(rep.other),
                    cc_wells=[x.detail.removeprefix("well ") for x in rep.violations["no_cross_contamination"]][:5])
    else:
        ours.update(error=(sim.get("error") or {}).get("type"), message=((sim.get("error") or {}).get("message") or "")[:300])
    ad = load_adapter()
    try:
        g = ad.grade_protocol(task, code, clone=clone, grader_python=ot_python())
        theirs = {"stage": g["stage"], "simulate_pass": g["simulate_pass"], "failed_checks": g["failed_checks"],
                  "rules": {r: {"pass": x["pass"], "check": x["t2wl_check"], "detail": x["detail"][:100]} for r, x in g["rule_results"].items()}}
    except ad.AdapterError as exc:
        theirs = {"adapter_error": str(exc)[:600]}
    return {"task": task, "ours": ours, "theirs": theirs}


# ---- tests: helpers (always run) ---------------------------------------------------------------------------------------


def test_expand_wells_follows_text2wetlabs_rule():
    assert expand_wells(["A1:A3", "B1"], "plate_96") == ["A1", "A2", "A3", "B1"]
    assert expand_wells(["A1:B2"], "plate_96") == ["A1", "A2", "B1", "B2"]  # a rectangle between two corners
    assert expand_wells(["H12:G11"], "plate_96") == ["G11", "G12", "H11", "H12"]  # corners in any order
    assert expand_wells([], "tube_1.5ml") == [""]  # a tube or a one-well reservoir has no well name in the IR
    with pytest.raises(ValueError):
        expand_wells(["I1"], "plate_96")


def _synthetic_task():
    ir = {"containers": [{"name": n, "kind": k} for n, k in (("res", "reservoir"), ("plate", "plate_96"), ("tube", "tube_1.5ml"),
                                                             ("src", "plate_96"), ("unused", "plate_96"), ("mod", "plate_96"))],
          "initial_contents": [
              {"container": "res", "wells": [], "volume_ul": 10000}, {"container": "tube", "wells": [], "volume_ul": 50},
              {"container": "src", "wells": ["A1:A2"], "volume_ul": None}, {"container": "plate", "wells": ["A1"], "volume_ul": 10},
              {"container": "plate", "wells": ["A1", "B1"], "volume_ul": 5}, {"container": "unused", "wells": ["A1"], "volume_ul": 3},
              {"container": "mod", "wells": ["A1"], "volume_ul": 7}],
          "steps": [{"source": "res", "dest": "plate"}, {"source": "tube", "dest": "src"}, {"source": "src", "dest": "mod"}]}
    plate96, rack = "corning_96_wellplate_360ul_flat", "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap"
    deck = {"containers": {
        "res": {"slot": 1, "label": "res", "load_name": "nest_1_reservoir_195ml", "well": "A1"},
        "plate": {"slot": 2, "label": "plate", "load_name": plate96},
        "tube": {"slot": 3, "label": "tubes", "load_name": rack, "well": "B1"},
        "src": {"slot": 4, "label": "src", "load_name": plate96}, "unused": {"slot": 5, "label": "unused", "load_name": plate96},
        "mod": {"slot": "Thermocycler Module GEN1 on 7", "label": "tc plate", "load_name": plate96}}}
    labware = [{"display_name": "res", "slot": "1", "load_name": "nest_1_reservoir_195ml"}, {"display_name": "plate", "slot": "2", "load_name": plate96},
               {"display_name": "tubes", "slot": "3", "load_name": rack}, {"display_name": "src", "slot": "4", "load_name": plate96},
               {"display_name": "tc plate", "slot": "7", "load_name": plate96}]
    return ir, deck, {"deck": {"labware": labware}}


def test_derive_initial_maps_containers_the_way_spec_check_does():
    ir, deck, sim = _synthetic_task()
    initial, ample, unmapped = derive_initial(ir, deck, sim)
    # a reservoir gets no entry (it is a stock); volumes of one well add up; a tube sits at the deck's well; a module's labware is
    # in the module's slot; a null volume is an ample stock; a container no step uses is not mapped
    assert initial == {("2", "A1"): 15.0, ("2", "B1"): 5.0, ("3", "B1"): 50.0, ("7", "A1"): 7.0}
    assert ample == {("4", "A1"), ("4", "A2")} and unmapped == []


def test_derive_initial_gives_a_container_whose_labware_does_not_match_no_contents():
    ir, deck, sim = _synthetic_task()
    sim["deck"]["labware"][3]["slot"] = "9"  # the run puts `src` in the wrong slot: spec_check reports deck_labware and maps nothing
    initial, ample, unmapped = derive_initial(ir, deck, sim)
    assert unmapped == ["src"] and ample == set() and ("3", "B1") in initial


def test_apply_ops_edits_exactly_where_it_says_and_never_silently():
    code = "def run(p):\n    a.transfer(1)\n    b.go()\n"
    assert apply_ops(code, [["replace", "a.transfer(1)", "a.transfer(2)"]]) == "def run(p):\n    a.transfer(2)\n    b.go()\n"
    assert apply_ops(code, [["line", "b.go", "go()", "stop()"]]).endswith("    b.stop()\n")
    assert apply_ops(code, [["set_line", "a.transfer(", ["x()", "for i in r:", "    y(i)"]]]) == (
        "def run(p):\n    x()\n    for i in r:\n        y(i)\n    b.go()\n")
    assert apply_ops(code, [["append", ["z()", "w()"]]]) == code + "    z()\n    w()\n"
    for bad in ([["replace", "nope", "x"]], [["replace", ".", "x"]], [["line", "c.", "a", "b"]], [["line", "b.go", "stop", "x"]],
                [["set_line", "zzz", ["x"]]], [["frobnicate"]]):
        with pytest.raises(ValueError):
            apply_ops(code, bad)


def test_their_hits_reads_spec_checks_detail_text():
    assert _their_hits("[(3, 'NoTipError')]", False, "tip_before_aspirate") == (1, True, [])
    assert _their_hits("[(5, 'A1'), (7, 'B1'), (9, 'C1')]", False, "no_cross_contamination") == (3, False, ["A1", "B1", "C1"])
    assert _their_hits("[]", True, "no_overdispense") == (0, True, [])
    assert _their_hits("", False, "tip_dropped_at_end") == (1, True, [])  # one or none by definition


def _grader_pair(ours_counts: dict, theirs: dict, cc_wells=()):
    o = {"simulated": True, "counts": dict.fromkeys(RULES, 0) | ours_counts, "cc_wells": list(cc_wells)}
    t = {"simulated": True, "rules": {r: {"pass": True, "count": 0, "exact": True, "wells": []} for r in RULES} | theirs}
    return o, t


def test_compare_checks_verdicts_exact_counts_and_the_first_wells():
    def rule(o, t, name="no_overdispense", cc=()):
        return compare(*_grader_pair(o, t, cc))["rules"][name]
    fail = {"pass": False, "count": 2, "exact": True, "wells": []}
    assert rule({}, {})["verdict_agrees"] and rule({}, {})["count_agrees"]
    assert not rule({"no_overdispense": 1}, {})["verdict_agrees"]  # ours flags, theirs does not
    assert not rule({}, {"no_overdispense": fail})["verdict_agrees"]  # theirs flags, ours does not
    assert rule({"no_overdispense": 2}, {"no_overdispense": fail})["count_agrees"]
    assert not rule({"no_overdispense": 3}, {"no_overdispense": fail})["count_agrees"]  # fewer than three hits: the count is exact
    cut = {"pass": False, "count": 3, "exact": False, "wells": []}  # three shown: the real count is not known
    assert rule({"no_overdispense": 40}, {"no_overdispense": cut})["count_agrees"]
    assert not rule({"no_overdispense": 2}, {"no_overdispense": cut})["count_agrees"]
    cc = {"pass": False, "count": 3, "exact": False, "wells": ["A1", "B1", "C1"]}
    assert rule({"no_cross_contamination": 9}, {"no_cross_contamination": cc}, "no_cross_contamination", ["A1", "B1", "C1", "D1"])["count_agrees"]
    assert not rule({"no_cross_contamination": 9}, {"no_cross_contamination": cc}, "no_cross_contamination", ["A1", "B1", "D1"])["count_agrees"]
    def events(positions, idxs):
        o, t = _grader_pair({"no_overdispense": 2}, {"no_overdispense": {**fail, "idxs": idxs}})
        return compare({**o, "positions": {"no_overdispense": positions}}, t)["rules"]["no_overdispense"]
    assert events([4, 9], [4, 9])["events_compared"] and events([4, 9], [4, 9])["events_agree"]
    assert not events([4, 8], [4, 9])["events_agree"]  # the same count, but not the same events
    assert events([4, 9, 12, 20], [4, 9, 12])["events_agree"]  # three shown: the first three are compared
    assert not events(None, [4, 9])["events_compared"]  # a run with delays or module steps is not numbered like ours
    o, t = _grader_pair({}, {})
    assert compare({**o, "simulated": False}, {**t, "simulated": False})["status"] == "unsimulated_in_both"
    assert compare(o, {**t, "simulated": False})["status"] == "simulated_in_one"
    assert compare(o, {"adapter_error": "x"})["status"] == "adapter_error"


FAMILIES = (*RULES, "combination", "probe", "control")


def test_the_mutation_data_is_well_formed_and_big_enough():
    ms = load_mutations()
    assert len({m["id"] for m in ms}) == len(ms)
    per_family = collections.Counter(m["rule"] for m in ms)
    assert len(ms) >= 30 and len({m["task"] for m in ms}) >= 4
    assert all(per_family[r] >= 6 for r in RULES), per_family
    allowed = {"id", "task", "rule", "targets", "description", "ops", "simulates", "disagree", "both_wrong"}
    for m in ms:
        assert set(m) <= allowed and {"id", "task", "rule", "targets", "description", "ops"} <= set(m), m["id"]
        assert m["task"] in IR_TASKS and m["rule"] in FAMILIES and set(m["targets"]) <= set(RULES), m["id"]
        assert m["ops"] and all(op[0] in ("replace", "line", "set_line", "append") for op in m["ops"]), m["id"]
        assert set(m.get("disagree", {})) <= set(RULES) and all(m.get("disagree", {}).values()), m["id"]  # a disagreement has a reason
        assert (m["rule"] in RULES) <= (bool(m["targets"]) or bool(m.get("both_wrong"))), m["id"]  # it says which rule it breaks


# ---- tests: against Text2WetLab's grader (need the clone and the Opentrons interpreter) --------------------------------------

_WHY = skip_reason()
needs_t2wl = pytest.mark.skipif(_WHY is not None, reason=f"Text2WetLab comparison not run: {_WHY}")
needs_ot = pytest.mark.skipif(not (os.environ.get("VAXFORGE_OT_PYTHON") or v.find_ot_python()),
                              reason="no Python 3.10 interpreter with opentrons 7.5.0: set VAXFORGE_OT_PYTHON")
MUTATIONS = {m["id"]: m for m in load_mutations()}
# Mutations whose two verdicts are expected to differ, on which rules, and why (the data holds the reasons).
KNOWN = [(m["id"], rule, reason) for m in MUTATIONS.values() for rule, reason in m.get("disagree", {}).items()]


@pytest.fixture(scope="module")
def results():
    clone = find_clone()
    return run_cases(cases(clone), clone, workers=int(os.environ.get("T2WL_WORKERS", "6")))


@needs_t2wl
def test_every_mutation_applies_to_its_reference():
    clone = find_clone()
    assert len(cases(clone)) == len(IR_TASKS) + len(MUTATIONS)


@needs_t2wl
@pytest.mark.parametrize("task", IR_TASKS)
def test_the_reference_solution_has_no_violation_in_either_and_they_agree(results, task):
    r = results[f"ref:{task}"]
    assert r["compare"]["status"] == "compared", _brief(r)
    assert disagreements(r) == [], _brief(r)
    assert r["ours"]["aspirations"] > 0 and r["ours"]["other"] == 0 and r["ours"]["unmapped"] == []
    assert sum(r["ours"]["counts"].values()) == 0
    assert all(x["their_pass"] is True for x in r["compare"]["rules"].values())


@needs_t2wl
@pytest.mark.parametrize("mid", [m for m in MUTATIONS if MUTATIONS[m].get("simulates", True) and not MUTATIONS[m].get("disagree")])
def test_both_graders_agree_on_every_rule_of_a_mutation(results, mid):
    r = results[mid]
    assert r["compare"]["status"] == "compared", _brief(r)
    assert disagreements(r) == [], _brief(r)


@needs_t2wl
@pytest.mark.parametrize("mid", [m for m in MUTATIONS if not MUTATIONS[m].get("simulates", True)])
def test_a_mutation_the_simulator_refuses_is_refused_by_both(results, mid):
    # the simulator itself enforces a tip before every aspiration, so these never reach the rules; counted apart
    assert results[mid]["compare"]["status"] == "unsimulated_in_both", _brief(results[mid])


@needs_t2wl
@pytest.mark.parametrize("mid", [m for m in MUTATIONS if MUTATIONS[m].get("simulates", True) and not MUTATIONS[m].get("disagree")])
def test_a_mutation_breaks_what_its_record_says_it_breaks(results, mid):
    # so that no mutation is a silent no-op, and a control stays clean. `both_wrong`: the two agree on a verdict that is physically wrong.
    m, rules = MUTATIONS[mid], results[mid]["compare"]["rules"]
    flagged = {r for r, x in rules.items() if x["ours"] > 0 and x["their_pass"] is False}
    assert flagged == {r for r, x in rules.items() if x["ours"] > 0 or x["their_pass"] is False}  # no verdict is one-sided here
    if m.get("both_wrong"):
        assert flagged != set(m["targets"])
    else:
        assert flagged == set(m["targets"]), (flagged, m["targets"])


@needs_t2wl
@pytest.mark.parametrize("mid", [m for m in MUTATIONS if MUTATIONS[m].get("disagree")])
def test_a_known_disagreement_is_confined_to_the_rules_listed(results, mid):
    assert results[mid]["compare"]["status"] == "compared", _brief(results[mid])
    assert disagreements(results[mid]) == sorted(MUTATIONS[mid]["disagree"], key=RULES.index), _brief(results[mid])


@needs_t2wl
@pytest.mark.parametrize("mid, rule, reason", [pytest.param(*k, marks=pytest.mark.xfail(strict=True, reason=k[2]), id=f"{k[0]}::{k[1]}")
                                                 for k in KNOWN])
def test_known_disagreement_between_text2wetlab_and_us(results, mid, rule, reason):
    # strict: if Text2WetLab (or the adapter) ever agrees with us on this, this XPASSes and fails, and the entry goes
    rules = results[mid]["compare"]["rules"][rule]
    assert rules["verdict_agrees"] and rules["count_agrees"]


MODULE_AT_2_13 = "metadata = {'apiLevel': '2.13'}\n\n\ndef run(protocol):\n    protocol.load_module('thermocycler')\n"


@needs_ot
def test_our_simulator_runs_a_protocol_that_loads_a_module_below_api_2_14():
    # Was a strict xfail: _ot_sim_runner._deck_snapshot read getattr(module, 'model', ''), which raises APIVersionError below
    # apiLevel 2.14. Fixed on 2026-11-14 (_ot_sim_runner._module_model). Opentrons and Text2WetLab's grader run such a protocol.
    sim = v.simulate(MODULE_AT_2_13, ot_python=ot_python(), timeout=300)
    assert sim["ok"], sim["error"]


# ---- command line ------------------------------------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("what", choices=("refs", "mutations", "all", "rna"), nargs="?", default="all")
    ap.add_argument("--only", help="run only the cases whose id contains this text")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--cache", help="JSON file that keeps results keyed by task+code (a development aid, never used by pytest)")
    ap.add_argument("--json", help="write every result to this file")
    ap.add_argument("--from-json", help="do not run anything: read the results a previous --json run wrote")
    ap.add_argument("--table", action="store_true", help="print the markdown tables of docs/streamc_t2wl_agreement.md")
    args = ap.parse_args(argv)
    if args.from_json:
        results = json.loads(Path(args.from_json).read_text(encoding="utf-8"))
        chosen = [{"id": rid} for rid in results if not args.only or args.only in rid]
    else:
        why = skip_reason()
        if why:
            print("cannot run:", why, file=sys.stderr)
            return 2
        clone = find_clone()
        if args.what == "rna":
            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                rna = list(pool.map(lambda t: run_rna(t, clone), RNA_TASKS))
            if args.json:
                Path(args.json).write_text(json.dumps(rna, indent=1), encoding="utf-8")
            for r in rna:
                print(json.dumps(r, indent=1))
            return 0
        chosen = [c for c in cases(clone) if (args.what == "all" or (c["kind"] == "reference") == (args.what == "refs"))
                  and (not args.only or args.only in c["id"])]
        cache_path = Path(args.cache) if args.cache else None
        cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path and cache_path.is_file() else ({} if cache_path else None)

        def save() -> None:
            if cache_path:
                cache_path.write_text(json.dumps(cache), encoding="utf-8")

        results = run_cases(chosen, clone, args.workers, cache, on_result=save)
        if args.json:
            Path(args.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
    for c in chosen:
        print(f"{c['id']:52s} {_brief(results[c['id']])}")
    if args.table:
        print()
        print(markdown(summarize(results)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
