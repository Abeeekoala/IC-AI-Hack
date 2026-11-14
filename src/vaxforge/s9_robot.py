"""S9 - Robot code generation and validation (stream C).

    S8 protocol  ->  robot sessions (one OT-2 run each)  ->  Python  ->  validate  ->  report

Input:  an S8 `Protocol` (see s8_protocol.py).
Output: under `<out>/`
    robot/<backend>/<session>.py          the OT-2 protocol (API 2.19), annotated per step
    robot/<backend>/<session>.setup.md    operator sheet: deck, what to load, human hand-offs
    robot/<backend>/<session>.plan.json   the plan the code was generated from
    robot/<backend>/<session>.sim.json    structured simulation (input to S9+ animation)
    robot/<backend>/<session>.grade.json  lint / simulation / five physical rules / plan check
    robot_report.json, robot_report.md    outcome per session and the automatability coverage
    workflow.md                           the whole workflow, robot and human steps in order

Public grader API (importable by S9.5 / validation/winrate.py):
    simulate, check_physical_rules, reference_diff, detect_refusal, grade_source, RULES

What this stage does NOT claim: a PASS means the code simulated and broke none of the checked
rules. It does not mean the protocol is right for the experiment, and every volume and time
is a PLACEHOLDER (see s9_sessions.RobotParams).

Stage entrypoint (artifact contract): `run(store, config)` reads ONLY the `s8` artifact (`require_ok()`),
compiles every candidate's protocol and writes `s9_robot.json`. Sidecars go under the run folder, one folder
per candidate (unique names, see `streamc_models.candidate_dir_names`):

    data/s9/<candidate>/<backend>/<session>.py|.setup.md|.plan.json|.sim.json|.grade.json
    data/s9/<candidate>/robot_report.json|.md, workflow.md

and the payload holds a contract file reference (root/path/sha256/bytes) for each. Status (artifact `status`, then `payload.status`):
    ok / (absent)     every session of every backend is PASS: a normal result
    ok / "not_run"    nothing to work on (no s8 artifact, or an s8 that did not run), or code generated but NOT simulated (no simulator, or
                      streamc.simulate_runs: false; sessions are UNVERIFIED): `payload.reasons`, and the first warning "S9 not run: ...", say how to
                      fix it (set VAXFORGE_OT_PYTHON). The sidecars and the per-session summary are kept.
    ok / "refused"    at least one session was REFUSED by the code model and none failed: no code exists for those sessions;
                      `payload.summary.outcomes` counts every outcome
    failed            a real defect: any session FAIL / ERROR, or a protocol S9 refuses (one warning each)
`needs_user_input` and `blocked` are never written (`vaxforge run` stops on any artifact status but `ok`; `blocked` is for a future S12 verdict).
Config (all optional, `streamc.<key>`): backend (template), model, simulate_runs (true), s12_status (pending),
robot_params ({} = RobotParams defaults), ot_python (None: found by find_ot_python).
"""

from __future__ import annotations

import argparse
import functools
import hashlib
import json
import re
import shutil
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from vaxforge.artifacts import Artifact, RunStore
from vaxforge.config import ConfigError
from vaxforge.s8_protocol import Automation, Protocol, validate_protocol
from vaxforge.streamc_adapter import STATUS_NOT_RUN, STATUS_REFUSED, payload_keys_applied, setting, write_not_run
from vaxforge.streamc_models import candidate_dir_names
from vaxforge.s9_llm import CodegenBackend, CodegenRequest, LLMBackend, TemplateBackend, detect_refusal, refusal_verdict
from vaxforge.s9_plan import SessionPlan, setup_markdown
from vaxforge.s9_sessions import BUILDERS, HUMAN_ONLY, PLACEHOLDER_BANNER, RobotParams, plan_sessions
from vaxforge.s9_validate import (
    RULES,
    Grade,
    check_physical_rules,
    find_ot_python,
    grade_source,
    reference_diff,
    simulate,
)

__all__ = [
    "RULES", "RobotParams", "compile_protocol", "grade_source", "simulate", "check_physical_rules", "reference_diff",
    "detect_refusal", "refusal_verdict", "TemplateBackend", "LLMBackend", "RobotReport", "ProtocolInvalid",
]

SCHEMA = "s9.v1"
STAGE = "s9"
PAYLOAD_KEYS = ("candidates", "summary", "simulator")  # A's structural check for status "ok", applied while this stage writes
OWNER = {Automation.FULL: "ROBOT", Automation.PARTIAL: "ROBOT-PARTIAL", Automation.MANUAL: "HUMAN"}

S12_NOTE = {
    "cleared": "S12 gate reported CLEARED by the caller.",
    "pending": "S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.",
}


class ProtocolInvalid(ValueError):
    """The S8 protocol broke its own rules; S9 refuses to generate robot code from it."""


# --------------------------------------------------------------------------- report models


class SessionReport(BaseModel):
    session_id: str
    title: str
    covers: list[str]
    outcome: Literal["PASS", "FAIL", "REFUSED", "UNVERIFIED", "ERROR"]
    code_file: str | None = None
    setup_file: str | None = None
    plan_file: str | None = None
    sim_file: str | None = None
    grade_file: str | None = None
    animation_file: str | None = None
    animation_error: str | None = None
    code_sha256: str | None = None
    plan_sha256: str | None = None
    tips_needed: dict[str, int] = Field(default_factory=dict)
    n_human_pauses: int = 0
    grade: Grade | None = None
    refusal_kind: str | None = None
    refusal_detail: str | None = None
    error: str | None = None


class BackendRun(BaseModel):
    label: str
    model_id: str | None = None
    sessions: list[SessionReport]

    @property
    def all_pass(self) -> bool:
        return bool(self.sessions) and all(s.outcome == "PASS" for s in self.sessions)

    @property
    def tally(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for s in self.sessions:
            out[s.outcome] = out.get(s.outcome, 0) + 1
        return out


class StepCoverage(BaseModel):
    step_id: str
    kind: str
    title: str
    owner: Literal["ROBOT", "ROBOT-PARTIAL", "HUMAN"]
    sessions: list[str]
    human_remainder: str


class RobotReport(BaseModel):
    schema_version: str = SCHEMA
    candidate_id: str
    protein_name: str
    opentrons_version: str | None
    simulated: bool
    parameters_status: str = PLACEHOLDER_BANNER
    biosecurity: str
    runs: list[BackendRun]
    coverage: list[StepCoverage]
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------------------- helpers


def _safe(label: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", label).strip("_") or "backend"


def _write(path: Path, text: str) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    # bytes, not write_text: on Windows write_text turns every "\n" into "\r\n", which would make a file's hash (the contract's
    # file reference) differ between operating systems and from `code_sha256`, the hash of the generated text
    path.write_bytes(text.encode("utf-8"))
    return str(path)


def _step_info(protocol: Protocol) -> dict[str, tuple[str, str, str]]:
    return {s.step_id: (s.title, OWNER[s.automation], s.automation_note) for s in protocol.steps}


def coverage(protocol: Protocol, plans: list[SessionPlan]) -> list[StepCoverage]:
    """Per S8 step: who owns it, which robot sessions cover it, and what a human still does."""
    out: list[StepCoverage] = []
    for s in protocol.steps:
        sess = [p.session_id for p in plans if s.step_id in p.covers]
        if s.kind in HUMAN_ONLY:
            remainder = HUMAN_ONLY[s.kind]
        elif s.automation == Automation.FULL:
            remainder = "Nothing beyond loading the deck."
        elif s.automation == Automation.MANUAL:
            remainder = s.automation_note
        else:
            remainder = f"Robot does the liquid-handling portion only. Not automated: {s.automation_note}"
        if s.kind not in BUILDERS and s.automation != Automation.MANUAL:
            remainder = f"No S9 session implemented for {s.kind.value}; treat as manual. " + remainder
        out.append(StepCoverage(step_id=s.step_id, kind=s.kind.value, title=s.title, owner=OWNER[s.automation],
                                sessions=sess, human_remainder=remainder))
    return out


def _animate(sim_path: Path, out_dir: Path) -> tuple[str | None, str | None]:
    try:
        from vaxforge.s9plus_animate import write_animation
    except ImportError:
        return None, "S9+ animation module not available"
    try:
        res = write_animation(sim_path, out_dir)
    except Exception as exc:  # noqa: BLE001 - the animation is optional; never fail S9 over it
        return None, f"{type(exc).__name__}: {str(exc)[:200]}"
    return (res.get("html") if res.get("ok") else None), (None if res.get("ok") else "animation reported ok=false")


# --------------------------------------------------------------------------- compile


def compile_protocol(
    protocol: Protocol,
    out_dir: str | Path,
    *,
    params: RobotParams | None = None,
    backends: list[CodegenBackend] | None = None,
    simulate_runs: bool = True,
    ot_python: str | None = None,
    s12_status: Literal["cleared", "pending"] = "pending",
    animate: bool = True,
    sessions: list[str] | None = None,
    workers: int = 4,
    robot_subdir: str = "robot",
) -> RobotReport:
    """Generate, validate and write robot code for every robot-assisted step of `protocol`.

    `sessions` limits the run to those session ids (e.g. to regenerate one); `workers` is how many
    sessions are generated and simulated at once (simulations are separate processes).
    `robot_subdir` is the folder under `out_dir` that holds one folder per backend ("" = directly in `out_dir`).
    """
    errors = validate_protocol(protocol)
    if errors:
        raise ProtocolInvalid("S8 protocol is invalid: " + "; ".join(errors))
    out = Path(out_dir)
    params = params or RobotParams()
    backends = backends or [TemplateBackend()]
    plans = plan_sessions(protocol, params)
    if sessions is not None:
        unknown = set(sessions) - {p.session_id for p in plans}
        if unknown:
            raise ValueError(f"unknown session id(s): {sorted(unknown)}; available: {[p.session_id for p in plans]}")
        plans = [p for p in plans if p.session_id in set(sessions)]
    info = _step_info(protocol)
    python = ot_python or (find_ot_python() if simulate_runs else None)
    runs: list[BackendRun] = []

    header = {
        "Candidate": f"{protocol.candidate_id} ({protocol.protein_name})",
        "Parameters": PLACEHOLDER_BANNER,
        "Biosecurity": S12_NOTE[s12_status],
    }

    def rel(path: str | Path) -> str:
        """Paths in the report are relative to `out_dir` (portable; no local usernames or drives)."""
        return Path(path).resolve().relative_to(out.resolve()).as_posix()

    def process(backend: CodegenBackend, plan: SessionPlan) -> tuple[SessionReport, str | None]:
        plan_info = {sid: info[sid] for sid in plan.covers}
        bdir = out / robot_subdir / _safe(backend.label)
        rep = SessionReport(session_id=plan.session_id, title=plan.title, covers=plan.covers, outcome="ERROR",
                            tips_needed=plan.tips_needed, n_human_pauses=sum(1 for a in plan.actions if a.op == "pause"),
                            plan_sha256=plan.plan_hash())
        base = bdir / plan.session_id
        rep.plan_file = rel(_write(base.with_suffix(".plan.json"), json.dumps(plan.model_dump(mode="json"), indent=1)))
        rep.setup_file = rel(_write(base.with_suffix(".setup.md"), setup_markdown(plan, plan_info)))
        gen = backend.generate(CodegenRequest(session_id=plan.session_id, plan=plan, step_info=plan_info, header=header))
        if gen.refused:
            rep.outcome, rep.refusal_kind, rep.refusal_detail = "REFUSED", gen.refusal_kind, gen.refusal_detail
            return rep, None
        if gen.code is None:
            rep.outcome, rep.error = "ERROR", gen.error or "no code produced"
            return rep, None
        rep.code_file = rel(_write(base.with_suffix(".py"), gen.code))
        rep.code_sha256 = hashlib.sha256(gen.code.encode()).hexdigest()
        grade, sim = grade_source(gen.code, plan=plan, ot_python=python, run_simulation=simulate_runs)
        rep.grade = grade
        rep.outcome = grade.verdict
        rep.grade_file = rel(_write(base.with_suffix(".grade.json"), grade.model_dump_json(indent=1)))
        version = None
        if sim is not None:
            version = sim.get("opentrons_version")
            sim_path = Path(_write(base.with_suffix(".sim.json"), json.dumps(sim, indent=1, ensure_ascii=False)))
            rep.sim_file = rel(sim_path)
            if animate and sim.get("ok"):
                html, rep.animation_error = _animate(sim_path, out / "animation" / _safe(backend.label) / plan.session_id)
                rep.animation_file = rel(html) if html else None
        return rep, version

    sim_version: str | None = None
    for backend in backends:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            results = list(pool.map(functools.partial(process, backend), plans))  # keeps plan order
        sim_version = sim_version or next((v for _r, v in results if v), None)
        runs.append(BackendRun(label=backend.label, model_id=backend.model_id, sessions=[r for r, _v in results]))

    cov = coverage(protocol, plans)
    notes = [
        "A PASS means the code simulated in Opentrons 7.5.0 and broke none of the five physical rules or the plan; it does NOT mean the protocol is right for the experiment.",
        "Reading plates, sealing, incubating, centrifuging, sonicating, plating and cell culture are human steps; each appears as a `HUMAN [step]` pause in the code.",
    ]
    if python is None and simulate_runs:
        notes.append("No Opentrons interpreter was found, so nothing was simulated (outcome UNVERIFIED). Set VAXFORGE_OT_PYTHON to a Python 3.10 environment with opentrons==7.5.0.")
    report = RobotReport(candidate_id=protocol.candidate_id, protein_name=protocol.protein_name, opentrons_version=sim_version,
                         simulated=bool(python and simulate_runs), biosecurity=S12_NOTE[s12_status], runs=runs, coverage=cov, notes=notes)
    _write(out / "robot_report.json", report.model_dump_json(indent=1))
    _write(out / "robot_report.md", render_report_markdown(report))
    _write(out / "workflow.md", workflow_markdown(report, protocol, plans))
    return report


# --------------------------------------------------------------------------- markdown


def render_report_markdown(r: RobotReport) -> str:
    L = [f"# S9 robot report - {r.protein_name} ({r.candidate_id})", "",
         f"> {r.parameters_status}", "", f"> {r.biosecurity}", "",
         f"Simulator: Opentrons {r.opentrons_version or 'NOT RUN'}. Rules checked: " + ", ".join(RULES) + ".", ""]
    for run in r.runs:
        L += [f"## Backend `{run.label}`" + (f" (model `{run.model_id}`)" if run.model_id else ""), "",
              f"Outcome tally: {run.tally}", "", "| Session | Outcome | Tips | Rule violations | Findings |", "|---|---|---|---|---|"]
        for s in run.sessions:
            g = s.grade
            viol = g.n_rule_violations if g else "-"
            finds = "; ".join(f"{f.severity}: {f.rule}: {f.message[:70]}" for f in (g.findings if g else [])[:3]) or (s.refusal_detail or s.error or "")
            tips = sum(s.tips_needed.values())
            L.append(f"| `{s.session_id}` | **{s.outcome}** | {tips} | {viol} | {finds} |")
        L.append("")
    L += ["## Automatability coverage (README matrix)", "", "| Step | Owner | Robot session(s) | What a human still does |", "|---|---|---|---|"]
    for c in r.coverage:
        L.append(f"| {c.step_id} {c.title} | {c.owner} | {', '.join(f'`{x}`' for x in c.sessions) or '-'} | {c.human_remainder} |")
    L += ["", "## Notes", ""] + [f"- {n}" for n in r.notes]
    return "\n".join(L) + "\n"


def workflow_markdown(r: RobotReport, protocol: Protocol, plans: list[SessionPlan]) -> str:
    by_step: dict[str, list[SessionPlan]] = {}
    for p in plans:
        for sid in p.covers:
            by_step.setdefault(sid, []).append(p)
    L = [f"# Workflow - {protocol.protein_name} ({protocol.candidate_id})", "",
         "Robot and human steps in order. `ROBOT` = the OT-2 owns it; `ROBOT-PARTIAL` = the robot does the liquid handling and a person does the rest; `HUMAN` = a person or other instrument.", "",
         f"> {r.parameters_status}", "", f"> {r.biosecurity}", ""]
    for s in protocol.steps:
        L.append(f"## {s.step_id}. {s.title}  `{OWNER[s.automation]}`")
        for c in s.controls:
            L.append(f"- control: {c}")
        if s.gate:
            L.append(f"- gate: {s.gate}")
        for p in by_step.get(s.step_id, []):
            L.append(f"- robot session `{p.session_id}` ({sum(p.tips_needed.values())} tip pick-ups)")
            for a in p.actions:
                if a.op == "pause":
                    L.append(f"    - hand-off: {a.text}")
        cov = next(c for c in r.coverage if c.step_id == s.step_id)
        L.append(f"- human part: {cov.human_remainder}")
        L.append("")
    L += ["---", f"S8 falsification condition: {protocol.falsifier}", "", "What the in-vitro cascade does NOT measure: " + "; ".join(protocol.does_not_measure) + "."]
    return "\n".join(L) + "\n"


# --------------------------------------------------------------------------- stage entrypoint


def backends_from_choice(choice: str, model: str | None = None) -> list[CodegenBackend]:
    """`template`, `llm` or `both` -> backend instances (constructing the LLM backend makes no network call)."""
    if choice not in ("template", "llm", "both"):
        raise ConfigError(f"streamc.backend must be template, llm or both, got {choice!r}")
    out: list[CodegenBackend] = []
    if choice in ("template", "both"):
        out.append(TemplateBackend())
    if choice in ("llm", "both"):
        out.append(LLMBackend(model_id=model))
    return out


def _reset_dir(path: Path, parent: Path) -> None:
    """Remove `path` (a folder this stage owns, directly inside `parent`) so a re-run leaves no stale sidecar beside the new ones."""
    if path.resolve().parent != parent.resolve():
        raise ValueError(f"refusing to clear {path}: it is not directly inside {parent}")
    if path.exists():
        shutil.rmtree(path)


def _reason(s: SessionReport) -> str:
    """Why a session did not PASS, in one line."""
    g = s.grade
    if s.refusal_kind:
        why = f"refused ({s.refusal_kind}): {s.refusal_detail or 'no detail'}"
    elif s.error:
        why = s.error
    elif g is not None and g.lint:
        why = "static lint: " + "; ".join(g.lint[:3])
    elif g is not None and g.sim_error:
        why = g.sim_error
    elif g is not None and any(f.severity == "error" for f in g.findings):
        why = "; ".join(f"{f.rule}: {f.message}" for f in [x for x in g.findings if x.severity == "error"][:3])
    else:
        why = "no detail recorded"
    return " ".join(why.split())[:300]


def _session_entry(store: RunStore, cdir: Path, s: SessionReport) -> dict[str, Any]:
    g = s.grade

    def ref(rel: str | None) -> dict | None:
        return store.run_ref(cdir / rel) if rel else None

    grade = None
    if g is not None:
        grade = {
            "verdict": g.verdict, "sim_ok": g.sim_ok, "sim_error_kind": g.sim_error_kind, "sim_error": g.sim_error,
            "lint": list(g.lint), "rule_counts": dict(g.rule_counts), "n_rule_violations": g.n_rule_violations,
            "n_events": g.n_events, "findings": [f.model_dump(mode="json") for f in g.findings],
        }
    return {
        "session_id": s.session_id, "title": s.title, "covers": list(s.covers), "outcome": s.outcome,
        "tips_needed": dict(s.tips_needed), "tips_total": sum(s.tips_needed.values()), "n_human_pauses": s.n_human_pauses,
        "code_sha256": s.code_sha256, "plan_sha256": s.plan_sha256, "grade": grade,
        "refusal": {"kind": s.refusal_kind, "detail": s.refusal_detail} if s.refusal_kind else None,
        "error": s.error,
        "files": {"code": ref(s.code_file), "setup": ref(s.setup_file), "plan": ref(s.plan_file),
                  "sim": ref(s.sim_file), "grade": ref(s.grade_file)},
    }


def _short(text: str, limit: int = 160) -> str:
    """One line of at most `limit` characters, cut at a word: an upstream reason repeated in full by every later stage is noise."""
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 4].rsplit(" ", 1)[0] + " ..."


def _reason_lines(s8: Artifact) -> str:
    """Why the s8 artifact did not run, in its own words (payload.reasons, else its first warning), shortened."""
    got = s8.payload.get("reasons")
    return _short("; ".join(str(r) for r in got) if isinstance(got, list) and got else (s8.warnings or ["no details recorded"])[0])


def run(store: RunStore, config) -> Artifact:
    """S9 stage: compile and validate robot code for every S8 candidate. Returns the written artifact (see the module docstring)."""
    backends = backends_from_choice(str(setting(config, "backend", "template")), setting(config, "model"))
    try:
        robot_params = RobotParams(**(setting(config, "robot_params") or {}))
    except (ValidationError, TypeError) as exc:
        raise ConfigError(f"streamc.robot_params is not valid RobotParams: {' '.join(str(exc).split())[:300]}") from None
    simulate_runs = bool(setting(config, "simulate_runs", True))
    s12 = str(setting(config, "s12_status", "pending"))
    if s12 not in S12_NOTE:
        raise ConfigError(f"streamc.s12_status must be one of {sorted(S12_NOTE)}, got {s12!r}")
    ot_python = setting(config, "ot_python")  # how, not what: a local path stays out of params and payload
    params = {
        "backends": [{"label": b.label, "model_id": b.model_id} for b in backends],
        "simulate_runs": simulate_runs,
        "s12_status": s12,
        "robot_params": robot_params.model_dump(mode="json"),
    }
    notes = [PLACEHOLDER_BANNER, S12_NOTE[s12]]

    def nothing_to_do(reasons: list[str], inputs: list[str]) -> Artifact:
        empty = {"parameters_status": PLACEHOLDER_BANNER, "biosecurity": S12_NOTE[s12], "simulator": {"simulated": False, "opentrons_version": None},
                 "candidates": [], "summary": {"n_candidates": 0, "n_sessions": 0, "outcomes": {}, "all_pass": False}}
        return write_not_run(store, STAGE, PAYLOAD_KEYS, empty, reasons, params=params, inputs=inputs, tools={"opentrons": "not_run"}, extra_warnings=notes)

    if not store.has("s8"):
        return nothing_to_do(["no s8 artifact in this run, so there is no protocol to compile: run S8 first (vaxforge run --stages s8, or "
                              "python -m vaxforge.streamc_cli run <run folder> --proteins ... for a plain tree)"], [])
    s8 = store.read_artifact("s8").require_ok()
    entries = list(s8.payload.get("candidates") or [])
    if s8.payload.get("status") == STATUS_NOT_RUN:
        return nothing_to_do([f"s8 did not run ({_reason_lines(s8)}); see payload.reasons of s8_protocol.json"], ["s8"])
    if not entries:
        return nothing_to_do(["the s8 artifact lists no candidates, so there is no protocol to compile"], ["s8"])

    data = store.run_dir / "data"
    root = data / "s9"
    if root.exists():
        _reset_dir(root, data)  # this stage owns data/s9: a re-run must not leave an earlier run's files beside the new ones
    dir_names = candidate_dir_names([e["candidate_id"] for e in entries])
    out_candidates: list[dict[str, Any]] = []
    bad: list[str] = []
    refused_lines: list[str] = []
    tally: Counter[str] = Counter()
    versions: list[str] = []
    simulated: list[bool] = []
    for e, dirname in zip(entries, dir_names):
        cid, cdir = e["candidate_id"], root / dirname
        item: dict[str, Any] = {"candidate_id": cid, "protein_name": e["protein_name"], "rank": e.get("rank"),
                                "dir_name": dirname, "refused_protocol": None, "report": None, "report_md": None,
                                "workflow": None, "coverage": [], "notes": [], "backends": []}
        out_candidates.append(item)
        try:
            rep = compile_protocol(Protocol.model_validate(e["protocol"]), cdir, params=robot_params, backends=backends,
                                   simulate_runs=simulate_runs, ot_python=ot_python, s12_status=s12,  # type: ignore[arg-type]
                                   animate=False, robot_subdir="")  # S9+ makes the animations from the sim files, as its own stage
        except (ProtocolInvalid, ValidationError) as exc:
            item["refused_protocol"] = " ".join(str(exc).split())[:300]
            bad.append(f"{cid}: S9 refuses this S8 protocol: {item['refused_protocol']}")
            continue
        simulated.append(rep.simulated)
        if rep.opentrons_version:
            versions.append(rep.opentrons_version)
        item.update(report=store.run_ref(cdir / "robot_report.json"), report_md=store.run_ref(cdir / "robot_report.md"),
                    workflow=store.run_ref(cdir / "workflow.md"), notes=list(rep.notes),
                    coverage=[c.model_dump(mode="json") for c in rep.coverage])
        for run_ in rep.runs:
            item["backends"].append({
                "label": run_.label, "model_id": run_.model_id, "dir_name": _safe(run_.label), "tally": run_.tally,
                "all_pass": run_.all_pass, "sessions": [_session_entry(store, cdir, s) for s in run_.sessions]})
            if not run_.sessions:
                bad.append(f"{cid} / {run_.label}: no robot session was produced")
            for s in run_.sessions:
                tally[s.outcome] += 1
                if s.outcome in ("FAIL", "ERROR"):
                    bad.append(f"{cid} / {run_.label} / {s.session_id}: {s.outcome} - {_reason(s)}")
                elif s.outcome == "REFUSED":  # the model declined: a distinct outcome, reported, not a defect of ours (README S9)
                    refused_lines.append(f"{cid} / {run_.label} / {s.session_id}: REFUSED - {_reason(s)}")

    version = versions[0] if versions else None
    n_sessions, refused, unverified = sum(tally.values()), tally.get("REFUSED", 0), tally.get("UNVERIFIED", 0)
    payload_status: str | None = None
    reasons: list[str] = []
    if bad:  # a real defect: FAIL / ERROR sessions, a protocol S9 refuses, a backend with no session
        status, warnings = "failed", [*bad, *refused_lines, *notes]
    else:
        status = "ok"
        if refused:
            payload_status = STATUS_REFUSED
            reasons.append(f"{refused} of {n_sessions} session(s) were REFUSED by the code model, so no code exists for them (a refusal is reported, "
                           "never engineered around: README S9); payload.summary.outcomes counts every outcome and each session carries its refusal record")
        if unverified:
            payload_status = payload_status or STATUS_NOT_RUN
            how = "simulation is switched off (streamc.simulate_runs: false)" if not simulate_runs else "no Opentrons simulator was found"
            reasons.append(f"code generated, not simulated: {how}, so {unverified} session(s) were not simulated and none can pass (UNVERIFIED is not a "
                           "pass). Set VAXFORGE_OT_PYTHON to a Python 3.10 environment with opentrons==7.5.0 (bash tools/setup-ot-venv.sh creates one)"
                           + ("" if simulate_runs else ", set streamc.simulate_runs to true") + ", then re-run S9")
        head = ("S9 refused: " if payload_status == STATUS_REFUSED else "S9 not run: ") + "; ".join(reasons)
        warnings = [head, *refused_lines, *notes] if reasons else list(notes)
    payload = {
        "parameters_status": PLACEHOLDER_BANNER,
        "biosecurity": S12_NOTE[s12],
        "simulator": {"simulated": bool(simulated) and all(simulated), "opentrons_version": version},
        "candidates": out_candidates,
        "summary": {"n_candidates": len(out_candidates), "n_sessions": n_sessions, "outcomes": dict(sorted(tally.items())),
                    "all_pass": status == "ok" and payload_status is None and n_sessions > 0},
    }
    if payload_status:
        payload = {"status": payload_status, "reasons": reasons, **payload}
    # `inputs` is the default (s8) and is exactly what this stage reads. An unrun simulator is recorded as "not_run", never a version.
    with payload_keys_applied(STAGE, PAYLOAD_KEYS):
        return store.write_artifact(STAGE, payload, params=params, inputs=["s8"], tools={"opentrons": version or "not_run"},
                                    status=status, warnings=warnings)


# --------------------------------------------------------------------------- CLI


def register_cli(subparsers, common) -> None:
    """Add `compile-protocol` to the `vaxforge` command (called by cli.build_parser; see docs/ARTIFACT_CONTRACT.md)."""
    p = subparsers.add_parser(
        "compile-protocol", parents=[common],
        help="S9: compile S8 protocols to OT-2 code and validate them in the Opentrons simulator")
    p.add_argument("source", help="a run folder or its s8_protocol.json (writes the s9 artifact), or one bare protocol.json (needs --out)")
    p.add_argument("--target", choices=["opentrons-ot2"], default="opentrons-ot2", help="robot platform (only the OT-2 exists)")
    p.add_argument("--out", help="output folder for a bare protocol.json; a run folder keeps its files under data/s9")
    p.add_argument("--backend", choices=["template", "llm", "both"], default="template")
    p.add_argument("--model", help="model id for the llm backend")
    p.add_argument("--no-simulate", action="store_true", help="generate and lint only; every session is UNVERIFIED")
    p.add_argument("--ot-python", help="interpreter that has opentrons (default: VAXFORGE_OT_PYTHON or .venv-ot)")
    p.add_argument("--s12", choices=["pending", "cleared"], default="pending")
    p.set_defaults(func=_cmd_compile_protocol)


def _cmd_compile_protocol(args: argparse.Namespace) -> int:
    from vaxforge import streamc_cli  # imported here: streamc_cli imports this module

    return streamc_cli.cmd_compile_protocol(args)


def _cli(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="vaxforge.s9_robot", description="S9: compile an S8 protocol to OT-2 code and validate it.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("compile", help="compile an S8 protocol.json")
    c.add_argument("protocol_json")
    c.add_argument("--out", required=True)
    c.add_argument("--backend", choices=["template", "llm", "both"], default="template")
    c.add_argument("--model", default=None, help="model id for the llm backend (default: $VAXFORGE_CODEGEN_MODEL or claude-sonnet-5-5)")
    c.add_argument("--no-simulate", action="store_true")
    c.add_argument("--ot-python", default=None)
    c.add_argument("--params", default=None, help="JSON file overriding RobotParams")
    c.add_argument("--s12", choices=["pending", "cleared"], default="pending")
    g = sub.add_parser("grade", help="grade any OT-2 protocol file with the five physical rules")
    g.add_argument("protocol_py")
    g.add_argument("--reference", default=None, help="reference protocol to diff against")
    g.add_argument("--ot-python", default=None)
    a = ap.parse_args(argv)

    if a.cmd == "grade":
        src = Path(a.protocol_py).read_text(encoding="utf-8")
        ref_sim = simulate(Path(a.reference), ot_python=a.ot_python) if a.reference else None
        grade, _ = grade_source(src, reference_sim=ref_sim, ot_python=a.ot_python)
        print(grade.model_dump_json(indent=1))
        return 0 if grade.verdict == "PASS" else 1

    proto = Protocol.model_validate_json(Path(a.protocol_json).read_text(encoding="utf-8"))
    params = RobotParams.model_validate_json(Path(a.params).read_text(encoding="utf-8")) if a.params else None
    backends: list[CodegenBackend] = []
    if a.backend in ("template", "both"):
        backends.append(TemplateBackend())
    if a.backend in ("llm", "both"):
        backends.append(LLMBackend(model_id=a.model))
    try:
        rep = compile_protocol(proto, a.out, params=params, backends=backends, simulate_runs=not a.no_simulate,
                               ot_python=a.ot_python, s12_status=a.s12)
    except ProtocolInvalid as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    for run in rep.runs:
        print(f"[{run.label}] {run.tally}")
    print(f"wrote {Path(a.out) / 'robot_report.md'}")
    return 0 if all(r.all_pass for r in rep.runs) else 1


if __name__ == "__main__":
    sys.exit(_cli())
