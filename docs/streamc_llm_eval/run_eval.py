#!/usr/bin/env python
"""Live evaluation of the S9 LLM code path (task G3). Driver, kept with its data so the run can be audited.

What it does (nothing here edits a source or test file of the project):

    template   offline baseline: the S8 protocol compiled by TemplateBackend (no model call)
    spec       spec mode: compile_protocol(protocol, backends=[LLMBackend(model_id=M)], animate=False), 13 sessions
    free       free mode: CodegenRequest(plan=None, protocol_steps=[one S8 step]) -> LLMBackend.generate -> grade_source(code)
    records    offline: stream D attempt records (make_record, split "probe"), results tables, consistency checks
    selftest   offline: the whole chain with a scripted fake client in a scratch directory (no network, no key)
    leakcheck  offline: scan the written files for API-key-shaped strings (prints counts and paths only)

Every model call goes through `LLMBackend.generate`, i.e. through the backend's own `client.messages.create(...)` with its
default max_tokens (12000) and the SDK's default retries. The only addition is `CountingLLMBackend`, a subclass whose
`_client()` wraps the backend's own client in a recorder. The recorder (a) counts a call BEFORE it is sent, (b) refuses a
second call for the same (model, mode, session) and any call beyond the budget, (c) saves the raw reply and the usage,
(d) never touches the API key (the SDK reads it itself; nothing here reads, prints or stores it).

Resume (added after the first run was cut off with calls in flight; the ledger spans both runs). When the ledger is opened it
is read once, and for every (model, mode, session) it says which of these holds:
    answered     a raw reply is on disk            -> the reply is REPLAYED to the backend, nothing is sent (event "replay")
    failed       a call_end with ok=false is on disk -> the recorded error is replayed, nothing is sent
    reissuable   a call_start with no call_end (cut off in flight) or a call the API rejected before any model saw it
                 (401/402/403, unpaid key)          -> ONE re-issue is allowed (call_start carries reissue_of=<old seq>)
    otherwise    already attempted                  -> blocked (pass@1)
A replay is a pure function of the recorded reply, so a rebuilt batch has the same code and the same grade as the original one.
`spec --resume` removes the previous output directory of that model and rebuilds it this way. Replays and re-issues are logged.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib.util
import json
import logging
import math
import re
import shutil
import sys
import threading
import time
import traceback
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vaxforge.refusal import _text_of  # noqa: E402  (the helper LLMBackend itself uses to read a reply)
from vaxforge.s8_protocol import Candidate, ProtocolConfig, StepKind, build_protocol  # noqa: E402
from vaxforge.s9_attempt import append_jsonl, make_record  # noqa: E402
from vaxforge.s9_llm import CodegenRequest, LLMBackend, TemplateBackend  # noqa: E402
from vaxforge.s9_plan import render_python  # noqa: E402  (selftest only)
from vaxforge.s9_robot import OWNER, S12_NOTE, compile_protocol  # noqa: E402
from vaxforge.s9_sessions import HUMAN_ONLY, PLACEHOLDER_BANNER, plan_sessions  # noqa: E402
from vaxforge.s9_validate import grade_source, leaf_events  # noqa: E402

MODELS = ("claude-sonnet-5-5", "claude-haiku-5-5")
HARD_CAP = 80  # the task's budget of model calls, enforced before every call
WORKERS = 4  # compile_protocol's own default; the free-mode runner uses the same
FREE_MARK = "Design the deck and liquid handling yourself"  # only the free-mode prompt contains this
RULES = ("tip_before_aspirate", "no_overdispense", "no_aspirate_from_empty_well", "tip_dropped_at_end", "no_cross_contamination")


# --------------------------------------------------------------------------- small helpers


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


_KEYLIKE = re.compile(r"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{8,}")


def scrub(text: object) -> str:
    """Error text as stored: anything shaped like an API key is masked, and the local checkout path is shortened."""
    return _KEYLIKE.sub("[redacted]", str(text)).replace(str(ROOT), "<repo>")


def read_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float] | None:
    """95% Wilson score interval for k successes in n trials."""
    if n <= 0:
        return None
    p = k / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def make_protocol():
    """The S8 protocol of the task: demo candidate c1 (fHbp), readouts ELISA, RT-qPCR and FACS."""
    return build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1),
                          ProtocolConfig(readouts=[StepKind.ELISA, StepKind.RT_QPCR, StepKind.FACS]))


def free_steps(proto):
    """The robot-assisted S8 steps (not gene synthesis, not THP-1 culture): 12 of the 14."""
    return [s for s in proto.steps if s.kind not in HUMAN_ONLY]


def free_session_id(step) -> str:
    return f"{step.step_id}_{step.kind.value}"


def parse_prompt(prompt: str) -> tuple[str, str]:
    """(mode, session id) from the user prompt `build_prompt` made."""
    first = prompt.splitlines()[0] if prompt else ""
    m = re.match(r"Session:\s*(\S+)", first)
    return ("free" if FREE_MARK in prompt else "spec"), (m.group(1) if m else "unknown")


def classify_error(error: str | None) -> str | None:
    """Kind of a GenerationResult.error: no_code (the model replied without a protocol: a failed attempt) or a harness-side kind."""
    if not error:
        return None
    if error.startswith("the reply contained no Python protocol"):
        return "no_code"
    if error.startswith("GuardError"):
        return "guard_blocked"
    if error.startswith("the `anthropic` package") or error.startswith("could not create the model client"):
        return "client_setup"
    if error.startswith("harness crash"):
        return "harness_crash"
    return "api_error"


# --------------------------------------------------------------------------- ledger, recorder, counting backend


class GuardError(RuntimeError):
    """The evaluation guard refused a model call (pass@1 or the call budget). Nothing was sent."""


def rejected_before_model(end: dict) -> bool:
    """A recorded failed call that the API refused before any model saw the prompt (bad, missing or unpaid key). It is not a
    result of the model, so it may be re-issued once; any other API error (rate limit, overload, timeout) stays the attempt's result."""
    text = f"{end.get('error_type')} {end.get('error')}".lower()
    return end.get("status_code") in (401, 402, 403) or "credit balance" in text or "authentication" in text


def replay_response(raw: dict, model: str):
    """A response in the SDK's shape for a recorded reply: the backend reads only the text blocks and the stop reason."""
    text = raw.get("text") or ""
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)] if text else [], stop_reason=raw.get("stop_reason"),
                           id=None, model=model, usage=None)


class Ledger:
    """calls.jsonl (one line per event: call_start, call_end, blocked, sdk_retry) and raw_replies.jsonl, under one directory."""

    def __init__(self, data_dir: Path, cap: int = HARD_CAP):
        self.data_dir, self.cap = data_dir, cap
        data_dir.mkdir(parents=True, exist_ok=True)
        self.calls, self.raw = data_dir / "calls.jsonl", data_dir / "raw_replies.jsonl"
        self.lock = threading.RLock()
        self.n_started, self.retries = 0, 0
        self.keys: set[tuple[str, str, str]] = set()
        # the resume snapshot (module docstring): taken once here and not updated while the process runs
        self.answered: dict[tuple[str, str, str], dict] = {}
        self.failed: dict[tuple[str, str, str], dict] = {}
        self.reissuable: dict[tuple[str, str, str], int] = {}
        starts: dict[tuple[str, str, str], list[int]] = {}
        ends: dict[int, dict] = {}
        for e in read_jsonl(self.calls):
            if e.get("event") == "call_start":
                self.n_started += 1
                key = (e["model"], e["mode"], e["session"])
                self.keys.add(key)
                starts.setdefault(key, []).append(e["seq"])
            elif e.get("event") == "call_end":
                ends[e["seq"]] = e
            elif e.get("event") == "sdk_retry":
                self.retries += 1
        for r in read_jsonl(self.raw):
            self.answered[(r["model"], r["mode"], r["session"])] = r
        for key, seqs in starts.items():
            if key in self.answered:
                continue
            end = ends.get(seqs[-1])
            if end is None:
                if len(seqs) == 1:  # cut off in flight once: one re-issue; a second loss is final
                    self.reissuable[key] = seqs[-1]
            elif end.get("ok") is False:
                if len(seqs) == 1 and rejected_before_model(end):
                    self.reissuable[key] = seqs[-1]
                else:
                    self.failed[key] = end

    def write(self, path: Path, obj: dict) -> None:
        line = json.dumps(obj, ensure_ascii=True) + "\n"  # ASCII only: no U+2028 can split a line
        with self.lock, open(path, "a", encoding="utf-8", newline="") as fh:
            fh.write(line)


class _RetryCounter(logging.Handler):
    """Counts the SDK's own retries ("Retrying request to /v1/messages in N seconds", logged at INFO). Prints nothing."""

    def __init__(self, ledger: Ledger):
        super().__init__(level=logging.INFO)
        self.ledger = ledger

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = record.getMessage()
        except Exception:  # noqa: BLE001
            return
        if msg.startswith("Retrying request to"):
            with self.ledger.lock:
                self.ledger.retries += 1
                self.ledger.write(self.ledger.calls, dict(event="sdk_retry", ts=utcnow(), message=scrub(msg)[:200]))


def install_retry_counter(ledger: Ledger) -> None:
    lg = logging.getLogger("anthropic")
    lg.setLevel(logging.INFO)
    lg.addHandler(_RetryCounter(ledger))
    lg.propagate = False  # the counter is the only handler: nothing is printed


class _RecordingMessages:
    def __init__(self, real, ledger: Ledger):
        self._real, self._ledger = real, ledger

    def create(self, **kw):
        L = self._ledger
        messages = kw.get("messages") or []
        prompt = messages[0]["content"] if messages else ""
        mode, session = parse_prompt(prompt)
        model = kw.get("model")
        key = (model, mode, session)
        with L.lock:
            if key in L.answered:  # a reply was recorded by an earlier run: reuse it, send nothing, count nothing
                raw = L.answered[key]
                L.write(L.calls, dict(event="replay", ts=utcnow(), of_seq=raw.get("seq"), model=model, mode=mode, session=session,
                                      note="recorded reply reused; no model call"))
                return replay_response(raw, model)
            if key in L.failed:  # the recorded API error is the attempt's result: replay it, send nothing
                end = L.failed[key]
                L.write(L.calls, dict(event="replay", ts=utcnow(), of_seq=end.get("seq"), model=model, mode=mode, session=session,
                                      note="recorded API error reused; no model call"))
                raise type(str(end.get("error_type") or "RecordedApiError"), (Exception,), {})(end.get("error") or "")
            reissue_of = L.reissuable.get(key)
            reason = None
            if key in L.keys and reissue_of is None:
                reason = "this (model, mode, session) was already attempted: pass@1 allows one attempt"
            elif L.n_started >= L.cap:
                reason = f"the call budget ({L.cap}) is used up"
            if reason:
                L.write(L.calls, dict(event="blocked", ts=utcnow(), model=model, mode=mode, session=session, reason=reason))
                raise GuardError(reason)
            L.reissuable.pop(key, None)
            L.n_started += 1
            L.keys.add(key)
            seq = L.n_started
            L.write(L.calls, dict(event="call_start", seq=seq, ts=utcnow(), model=model, mode=mode, session=session,
                                  prompt_sha256=sha256(prompt), prompt_chars=len(prompt), system_sha256=sha256(kw.get("system") or ""),
                                  max_tokens=kw.get("max_tokens"), **({"reissue_of": reissue_of} if reissue_of else {})))
        t0 = time.monotonic()
        try:
            resp = self._real.messages.create(**kw)
        except Exception as exc:
            L.write(L.calls, dict(event="call_end", seq=seq, ts=utcnow(), model=model, mode=mode, session=session, ok=False,
                                  latency_s=round(time.monotonic() - t0, 2), error_type=type(exc).__name__,
                                  status_code=getattr(exc, "status_code", None), error=scrub(exc)[:400]))
            raise
        text = _text_of(resp)
        usage = getattr(resp, "usage", None)
        stop = getattr(resp, "stop_reason", None)
        L.write(L.raw, dict(seq=seq, model=model, mode=mode, session=session, stop_reason=stop, text=text))
        L.write(L.calls, dict(event="call_end", seq=seq, ts=utcnow(), model=model, mode=mode, session=session, ok=True,
                              latency_s=round(time.monotonic() - t0, 2), stop_reason=stop,
                              input_tokens=getattr(usage, "input_tokens", None), output_tokens=getattr(usage, "output_tokens", None),
                              resp_model=getattr(resp, "model", None), resp_id=getattr(resp, "id", None), reply_chars=len(text),
                              block_types=[getattr(b, "type", None) for b in (getattr(resp, "content", None) or [])]))
        return resp


class _RecordingClient:
    def __init__(self, real, ledger: Ledger):
        self.messages = _RecordingMessages(real, ledger)


class CountingLLMBackend(LLMBackend):
    """`LLMBackend` unchanged except that its client is wrapped by the recorder. `generate`, the prompts, max_tokens, the
    refusal handling and the SDK's default retries are the backend's own."""

    def __init__(self, model_id: str, ledger: Ledger, client=None):
        super().__init__(model_id=model_id, client=client)
        self.ledger = ledger

    def _client(self):
        return _RecordingClient(super()._client(), self.ledger)


# --------------------------------------------------------------------------- liquid-movement statistics (is a PASS vacuous?)


def liquid_stats(sim: dict | None) -> dict | None:
    if not sim:
        return None
    ev = leaf_events(sim)
    asp = [e for e in ev if e["kind"] == "aspirate"]
    allev = sim.get("events", [])
    comments = [e for e in allev if e["kind"] == "comment"]
    deck = sim.get("deck", {})
    return dict(
        n_events=len(allev), n_aspirate=len(asp), n_dispense=sum(1 for e in ev if e["kind"] == "dispense"),
        n_pick_up_tip=sum(1 for e in ev if e["kind"] == "pick_up_tip"),
        ul_aspirated=round(sum(float(e.get("volume_ul") or 0.0) for e in asp), 3),
        n_pause=sum(1 for e in allev if e["kind"] == "pause"), n_comment=len(comments),
        n_banner_comments=sum(1 for e in comments if re.match(r"\[s\d+\]", e.get("message") or "")),
        n_liquids_declared=len(sim.get("liquids", [])),
        labware=[x["load_name"] for x in deck.get("labware", [])], modules=[x["name"] for x in deck.get("modules", [])],
        pipettes=[x["name"] for x in deck.get("pipettes", [])], opentrons_version=sim.get("opentrons_version"))


# --------------------------------------------------------------------------- phases that call the model (or the template)


def run_template(data_dir: Path) -> None:
    out = data_dir / "template_baseline"
    rep = compile_protocol(make_protocol(), out, backends=[TemplateBackend()], animate=False)
    for run in rep.runs:
        print(f"[{run.label}] {run.tally}")
        for s in run.sessions:
            print(f"  {s.session_id}: {s.outcome}")


def run_spec(model: str, ledger: Ledger, client=None, resume: bool = False) -> None:
    out = ledger.data_dir / f"spec_{model}"
    if out.exists() and any(out.iterdir()):
        if not resume:
            raise SystemExit(f"{out.name} already holds output: pass@1 allows one run per (model, task); use --resume to rebuild it from the ledger")
        if out.parent != ledger.data_dir or not out.name.startswith("spec_"):
            raise SystemExit(f"refusing to remove {out}")
        shutil.rmtree(out)  # the partial output of an interrupted batch; everything in it is rebuilt from the recorded replies
        print(f"resume: removed the previous output of {out.name}")
    if resume:
        replay = sorted(s for (m, mo, s) in ledger.answered if m == model and mo == "spec")
        failed = sorted(s for (m, mo, s) in ledger.failed if m == model and mo == "spec")
        reissue = sorted(s for (m, mo, s) in ledger.reissuable if m == model and mo == "spec")
        ledger.write(ledger.calls, dict(event="resume", ts=utcnow(), model=model, mode="spec", replay=replay, replay_error=failed, reissue=reissue))
        print(f"resume {model}: replay {len(replay)} recorded replies, {len(failed)} recorded errors; re-issue {len(reissue)} call(s) that had no recorded end: {reissue}")
    backend = CountingLLMBackend(model, ledger, client=client)
    t0 = time.monotonic()
    try:
        rep = compile_protocol(make_protocol(), out, backends=[backend], animate=False)
    except Exception:  # noqa: BLE001 - keep the evidence: the raw replies are already in raw_replies.jsonl
        out.mkdir(parents=True, exist_ok=True)
        (out / "CRASH.txt").write_text(scrub(traceback.format_exc()), encoding="utf-8")
        print(f"compile_protocol RAISED ({scrub(sys.exc_info()[1])[:200]}); traceback in {out.name}/CRASH.txt")
        return
    for run in rep.runs:
        print(f"[{run.label}] {run.tally}  ({time.monotonic() - t0:.0f} s wall)")
        for s in run.sessions:
            why = s.refusal_kind or s.error or ""
            nviol = s.grade.n_rule_violations if s.grade else "-"
            print(f"  {s.session_id}: {s.outcome}  rule-violations={nviol}  {scrub(why)[:100]}")


def run_free(model: str, ledger: Ledger, only: list[str] | None = None, client=None) -> None:
    proto = free_steps(make_protocol())
    steps = [s for s in proto if (not only or s.step_id in only)]
    out = ledger.data_dir / f"free_{model}"
    out.mkdir(parents=True, exist_ok=True)
    backend = CountingLLMBackend(model, ledger, client=client)

    def one(step) -> dict:
        sid = free_session_id(step)
        att = dict(mode="free", model=model, session=sid, step_id=step.step_id, kind=step.kind.value, outcome="ERROR", refused=False,
                   refusal_kind=None, refusal_detail=None, error=None, error_class=None, stop_reason=None, code_file=None,
                   code_sha256=None, grade=None, liquid=None)
        try:
            gen = backend.generate(CodegenRequest(session_id=sid, plan=None, protocol_steps=[step.model_dump(mode="json")]))
            att.update(refused=gen.refused, refusal_kind=gen.refusal_kind, refusal_detail=gen.refusal_detail, error=gen.error,
                       stop_reason=gen.stop_reason, error_class=classify_error(gen.error))
            if gen.refused:
                att["outcome"] = "REFUSED"
            elif gen.code is not None:
                (out / f"{sid}.py").write_bytes(gen.code.encode("utf-8"))
                att.update(code_file=f"free_{model}/{sid}.py", code_sha256=sha256(gen.code))
                grade, sim = grade_source(gen.code)  # no plan: lint + simulation + the five rules (+ structure checks)
                att.update(grade=grade.model_dump(mode="json"), liquid=liquid_stats(sim), outcome=grade.verdict)
        except Exception:  # noqa: BLE001 - a crash in OUR code is recorded as such, not as a model failure
            att.update(outcome="ERROR", error_class="harness_crash", error="harness crash: " + scrub(traceback.format_exc())[-600:])
        (out / f"{sid}.attempt.json").write_text(json.dumps(att, indent=1, ensure_ascii=True), encoding="utf-8")
        return att

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        results = list(pool.map(one, steps))
    print(f"[free {model}] {dict(Counter(r['outcome'] for r in results))}  ({time.monotonic() - t0:.0f} s wall)")
    for r in results:
        g = r["grade"] or {}
        print(f"  {r['session']}: {r['outcome']}  kind={g.get('sim_error_kind')}  rules={g.get('n_rule_violations')}  "
              f"{scrub(r['refusal_kind'] or r['error'] or '')[:100]}")


# --------------------------------------------------------------------------- attempts -> rows


def _grade_fields(g: dict | None) -> dict:
    if not g:
        return dict(verdict=None, sim_ok=None, sim_error_kind=None, sim_error=None, lint=[], rule_counts=dict.fromkeys(RULES, 0), tip_state=0,
                    error_findings=[], warning_findings=[], n_events=None)
    rc = g.get("rule_counts") or dict.fromkeys(RULES, 0)
    fnd = g.get("findings") or []
    return dict(verdict=g.get("verdict"), sim_ok=g.get("sim_ok"), sim_error_kind=g.get("sim_error_kind"), sim_error=g.get("sim_error"),
                lint=list(g.get("lint") or []), rule_counts=rc,
                tip_state=max(0, int(g.get("n_rule_violations") or 0) - sum(rc.values())) if g.get("sim_ok") else 0,
                error_findings=[f"{f['rule']}: {f['message']}" for f in fnd if f["severity"] == "error"],
                warning_findings=[f"{f['rule']}: {f['message']}" for f in fnd if f["severity"] == "warning"],
                n_events=g.get("n_events"))


def _row(mode, model, session, covers, kinds, outcome, ends, raws, **extra) -> dict:
    key = (model, mode, session)
    end, raw = ends.get(key) or {}, raws.get(key) or {}
    row = dict(mode=mode, model=model, session=session, task_id=f"{mode}:{session}", covers=list(covers), family=kinds[covers[0]], outcome=outcome,
               stop_reason=raw.get("stop_reason") or end.get("stop_reason"), input_tokens=end.get("input_tokens"),
               output_tokens=end.get("output_tokens"), latency_s=end.get("latency_s"), call_ok=end.get("ok"),
               call_error=(f"{end.get('error_type')}: {end.get('error')}" if end.get("ok") is False else None),
               reply_chars=end.get("reply_chars"), resp_model=end.get("resp_model"))
    row.update(extra)
    return row


def load_rows(data_dir: Path) -> list[dict]:
    proto = make_protocol()
    kinds = {s.step_id: s.kind.value for s in proto.steps}
    calls = read_jsonl(data_dir / "calls.jsonl")
    ends = {(e["model"], e["mode"], e["session"]): e for e in calls if e.get("event") == "call_end"}
    raws = {(r["model"], r["mode"], r["session"]): r for r in read_jsonl(data_dir / "raw_replies.jsonl")}
    present = {p.name.split("_", 1)[1] for p in data_dir.iterdir() if p.is_dir() and re.match(r"(spec|free)_.+", p.name)}
    models = [m for m in MODELS if m in present] + sorted(present - set(MODELS))
    rows: list[dict] = []
    for model in models:
        rp = data_dir / f"spec_{model}" / "robot_report.json"
        if rp.exists():
            rep = json.loads(rp.read_text(encoding="utf-8"))
            for s in rep["runs"][0]["sessions"]:
                g = s.get("grade")
                stats = None
                sim_path = data_dir / f"spec_{model}" / (s["sim_file"] or "") if s.get("sim_file") else None
                if sim_path and sim_path.exists():
                    stats = liquid_stats(json.loads(sim_path.read_text(encoding="utf-8")))
                rows.append(_row("spec", model, s["session_id"], s["covers"], kinds, s["outcome"], ends, raws,
                                 refusal_kind=s.get("refusal_kind"), refusal_detail=s.get("refusal_detail"), error=s.get("error"),
                                 error_class=classify_error(s.get("error")),
                                 code_file=(f"spec_{model}/" + s["code_file"]) if s.get("code_file") else None,
                                 code_sha256=s.get("code_sha256"), liquid=stats, **_grade_fields(g)))
        for f in sorted((data_dir / f"free_{model}").glob("*.attempt.json")) if (data_dir / f"free_{model}").exists() else []:
            a = json.loads(f.read_text(encoding="utf-8"))
            rows.append(_row("free", model, a["session"], [a["step_id"]], kinds, a["outcome"], ends, raws,
                             refusal_kind=a.get("refusal_kind"), refusal_detail=a.get("refusal_detail"), error=a.get("error"),
                             error_class=a.get("error_class"), code_file=a.get("code_file"), code_sha256=a.get("code_sha256"),
                             liquid=a.get("liquid"), **_grade_fields(a.get("grade"))))
    order = {m: i for i, m in enumerate(models)}
    step_order = {s.step_id: i for i, s in enumerate(proto.steps)}
    rows.sort(key=lambda r: (r["mode"] != "spec", order[r["model"]], step_order[r["covers"][0]], r["session"]))
    return rows


# --------------------------------------------------------------------------- stream D records


def record_for(row: dict, raws: dict, ends: dict, data_dir: Path) -> dict | None:
    """The stream D record of one attempt, or None when the attempt is not a model result (the harness, not the model, failed)."""
    key = (row["model"], row["mode"], row["session"])
    raw = raws.get(key)
    common = dict(task_id=row["task_id"], family=row["family"], model="base", split="probe", finish_reason=row.get("stop_reason"))
    if row["outcome"] == "REFUSED":
        text = raw["text"] if raw else ((ends.get(key) or {}).get("error") or row.get("refusal_detail") or "")
        return make_record(reply=text, **common)
    if row["outcome"] == "ERROR":
        if row.get("error_class") != "no_code" or not raw:
            return None
        return make_record(reply=raw["text"], **common)
    if raw:
        return make_record(reply=raw["text"], **common)
    return make_record(code=(data_dir / row["code_file"]).read_text(encoding="utf-8"), **common)


def _load_winrate():
    spec = importlib.util.spec_from_file_location("vf_winrate", ROOT / "validation" / "winrate.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def build_records(data_dir: Path, rows: list[dict]) -> dict:
    calls = read_jsonl(data_dir / "calls.jsonl")
    ends = {(e["model"], e["mode"], e["session"]): e for e in calls if e.get("event") == "call_end"}
    raws = {(r["model"], r["mode"], r["session"]): r for r in read_jsonl(data_dir / "raw_replies.jsonl")}
    winrate = _load_winrate()
    report: dict = {}
    for mode in ("spec", "free"):
        for model in dict.fromkeys(r["model"] for r in rows):
            group = [r for r in rows if r["mode"] == mode and r["model"] == model]
            if not group:
                continue
            path = data_dir / f"attempts_{mode}_{model}.jsonl"
            if path.exists():
                path.unlink()
            skipped, mismatches = [], []
            for row in group:
                rec = record_for(row, raws, ends, data_dir)
                if rec is None:
                    skipped.append(row["session"])
                    continue
                append_jsonl(path, rec)
                # the record is graded by make_record WITHOUT the plan; compare with the S9 grade of the same code
                if row["outcome"] in ("PASS", "FAIL") and row.get("verdict") is not None:
                    if rec["simulate_pass"] != bool(row["sim_ok"]):
                        mismatches.append((row["session"], "simulate_pass", rec["simulate_pass"], row["sim_ok"]))
                    elif row["sim_ok"]:
                        want = sum(1 for v in row["rule_counts"].values() if v >= 1)
                        if rec["rule_violations"] != want:
                            mismatches.append((row["session"], "rule_violations", rec["rule_violations"], want))
            lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
            parsed = [json.loads(x) for x in lines]  # every line must be valid JSON
            loaded = winrate.load_records(path) if path.exists() else []  # and must load with stream D's own reader
            report[(mode, model)] = dict(
                file=path.name, attempts=len(group), records=len(parsed), loaded_by_winrate_load_records=len(loaded),
                outcomes=dict(Counter(p["outcome"] for p in parsed)), simulate_pass_true=sum(1 for p in parsed if p["simulate_pass"] is True),
                zero_rule_violations_among_passing_sims=sum(1 for p in parsed if p["simulate_pass"] is True and p["rule_violations"] == 0),
                not_recorded_harness_errors=skipped, grader_disagreements=mismatches,
                splits=sorted({p["split"] for p in parsed}), model_labels=sorted({p["model"] for p in parsed}),
                counts_add_up=(len(parsed) + len(skipped) == len(group)))
    return report


# --------------------------------------------------------------------------- tables


def _fmt_rules(r: dict) -> str:
    parts = [f"{k}={v}" for k, v in r["rule_counts"].items() if v]
    if r["tip_state"]:
        parts.append(f"tip_state={r['tip_state']}")
    return ", ".join(parts) or "-"


def headline(rows: list[dict]) -> list[dict]:
    out = []
    models = list(dict.fromkeys(r["model"] for r in rows))
    for mode in ("spec", "free"):
        for model in models:
            g = [r for r in rows if r["mode"] == mode and r["model"] == model]
            if not g:
                continue
            c = Counter(r["outcome"] for r in g)
            n, k = len(g), c["PASS"]
            ci = wilson(k, n)
            out.append(dict(mode=mode, model=model, n=n, PASS=k, FAIL=c["FAIL"], REFUSED=c["REFUSED"], ERROR=c["ERROR"], UNVERIFIED=c["UNVERIFIED"],
                            pass_at_1=f"{k}/{n}", wilson95=f"{ci[0]:.2f}-{ci[1]:.2f}" if ci else None))
    return out


def call_summary(data_dir: Path) -> dict:
    ev = read_jsonl(data_dir / "calls.jsonl")
    starts = [e for e in ev if e.get("event") == "call_start"]
    ends = [e for e in ev if e.get("event") == "call_end"]
    ok = [e for e in ends if e.get("ok")]
    per = Counter((e["model"], e["mode"]) for e in starts)
    return dict(calls_started=len(starts), calls_ended=len(ends), calls_ok=len(ok), calls_error=len(ends) - len(ok),
                calls_without_end=len(starts) - len(ends), reissued=sum(1 for e in starts if e.get("reissue_of")),
                replays=sum(1 for e in ev if e.get("event") == "replay"), blocked=sum(1 for e in ev if e.get("event") == "blocked"),
                sdk_retries=sum(1 for e in ev if e.get("event") == "sdk_retry"),
                per_model_mode={f"{m}/{mo}": n for (m, mo), n in sorted(per.items())},
                stop_reasons=dict(Counter(e.get("stop_reason") for e in ok)),
                input_tokens=sum(e.get("input_tokens") or 0 for e in ok), output_tokens=sum(e.get("output_tokens") or 0 for e in ok),
                resp_models=sorted({e.get("resp_model") for e in ok if e.get("resp_model")}),
                first_call_utc=min((e["ts"] for e in starts), default=None), last_call_utc=max((e["ts"] for e in ends), default=None),
                errors=[f"{e['model']}/{e['mode']}/{e['session']}: {e.get('error_type')} {e.get('status_code')}" for e in ends if not e.get("ok")])


def write_tables(data_dir: Path, rows: list[dict], rec_report: dict) -> None:
    hl, cs = headline(rows), call_summary(data_dir)
    flat_keys = ["mode", "model", "session", "family", "outcome", "error_class", "sim_error_kind", "n_rules_failed", "rule_counts", "tip_state", "plan_findings",
                 "other_error_findings", "lint", "refusal_kind", "stop_reason", "input_tokens", "output_tokens", "latency_s", "n_events", "n_aspirate",
                 "n_dispense", "ul_aspirated", "n_pause", "n_banner_comments", "n_warnings", "code_file", "code_sha256"]
    flat = []
    for r in rows:
        liq = (r.get("liquid") or {}) if r["sim_ok"] else {}  # no liquid statistics for a run that did not simulate
        plan_f =[x for x in r["error_findings"] if x.split(":")[0].startswith("plan_")]
        other_f = [x for x in r["error_findings"] if x not in plan_f and x.split(":")[0] not in RULES]
        flat.append(dict(mode=r["mode"], model=r["model"], session=r["session"], family=r["family"], outcome=r["outcome"], error_class=r.get("error_class"),
                         sim_error_kind=r["sim_error_kind"], n_rules_failed=sum(1 for v in r["rule_counts"].values() if v), rule_counts=_fmt_rules(r),
                         tip_state=r["tip_state"], plan_findings=" | ".join(plan_f), other_error_findings=" | ".join(other_f), lint=" | ".join(r["lint"]),
                         refusal_kind=r.get("refusal_kind"), stop_reason=r.get("stop_reason"), input_tokens=r.get("input_tokens"),
                         output_tokens=r.get("output_tokens"), latency_s=r.get("latency_s"), n_events=liq.get("n_events"), n_aspirate=liq.get("n_aspirate"),
                         n_dispense=liq.get("n_dispense"), ul_aspirated=liq.get("ul_aspirated"), n_pause=liq.get("n_pause"),
                         n_banner_comments=liq.get("n_banner_comments"), n_warnings=len(r["warning_findings"]), code_file=r.get("code_file"),
                         code_sha256=r.get("code_sha256")))
    with open(data_dir / "results.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=flat_keys, lineterminator="\n")
        w.writeheader()
        w.writerows(flat)
    (data_dir / "results.json").write_text(json.dumps(dict(headline=hl, calls=cs, records={f"{k[0]}/{k[1]}": v for k, v in rec_report.items()}, attempts=rows),
                                                      indent=1, ensure_ascii=True), encoding="utf-8")
    L = ["# Results (generated by run_eval.py records; the report quotes these numbers)", "", "## pass@1", "",
         "| mode | model | n | PASS | FAIL | REFUSED | ERROR | UNVERIFIED | pass@1 | Wilson 95% |", "|---|---|---|---|---|---|---|---|---|---|"]
    for h in hl:
        L.append(f"| {h['mode']} | {h['model']} | {h['n']} | {h['PASS']} | {h['FAIL']} | {h['REFUSED']} | {h['ERROR']} | {h['UNVERIFIED']} | {h['pass_at_1']} | {h['wilson95']} |")
    L += ["", "## per attempt", "", "| mode | model | session | outcome | sim error kind | rule violations | plan / other findings | stop | out tokens |", "|---|---|---|---|---|---|---|---|---|"]
    for r, f in zip(rows, flat):
        finds = " ; ".join(x for x in (f["plan_findings"], f["other_error_findings"], f["lint"], (r.get("refusal_kind") or ""), (r.get("error") or "")[:80]) if x)
        L.append(f"| {r['mode']} | {r['model']} | {r['session']} | {r['outcome']} | {r['sim_error_kind'] or ''} | {f['rule_counts']} | {finds[:200]} | {r.get('stop_reason') or ''} | {r.get('output_tokens') or ''} |")
    L += ["", "## model calls", "", "```", json.dumps(cs, indent=1), "```", "", "## stream D records", "", "```", json.dumps({f"{k[0]}/{k[1]}": v for k, v in rec_report.items()}, indent=1, default=list), "```", ""]
    (data_dir / "results.md").write_text("\n".join(L), encoding="utf-8")


def cmd_records(data_dir: Path) -> None:
    rows = load_rows(data_dir)
    rec = build_records(data_dir, rows)
    write_tables(data_dir, rows, rec)
    print(json.dumps(headline(rows), indent=1))
    for k, v in rec.items():
        print(k, json.dumps(v, default=list))
    print(json.dumps(call_summary(data_dir), indent=1))


# --------------------------------------------------------------------------- leak check


def cmd_leakcheck(paths: list[Path]) -> None:
    pats = [re.compile(rb"(?<![A-Za-z0-9])sk-[A-Za-z0-9_\-]{16,}"), re.compile(rb"x-api-key\s*[:=]\s*\S+", re.I), re.compile(rb"Authorization\s*:\s*Bearer\s+\S+", re.I)]
    n_files = hits = 0
    for root in paths:
        for p in ([root] if root.is_file() else [x for x in root.rglob("*") if x.is_file()]):
            n_files += 1
            data = p.read_bytes()
            if any(pt.search(data) for pt in pats):
                hits += 1
                print("KEY-LIKE STRING IN", p.relative_to(ROOT))  # path only, never the match
    print(f"leakcheck: scanned {n_files} files, {hits} with a key-like string")


# --------------------------------------------------------------------------- selftest with a scripted fake client (no network)

FREE_OK = '''```python
from opentrons import protocol_api
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}
def run(ctx: protocol_api.ProtocolContext):
    # placeholder volumes
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    plate = ctx.load_labware("nest_96_wellplate_200ul_flat", "2")
    pip = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])
    liq = ctx.define_liquid(name="reagent", description="x", display_color="#33AAFF")
    plate["A1"].load_liquid(liquid=liq, volume=100)
    pip.pick_up_tip()
    pip.aspirate(50, plate["A1"])
    pip.dispense(50, plate["B1"])
    pip.drop_tip()
```'''


class FakeClient:
    """Scripted replies in the SDK's shape. Used only by `selftest`; no network."""

    def __init__(self, scripts: dict[tuple[str, str], str]):
        self.messages = self
        self.scripts = scripts
        self.n = 0
        self.seen: list[tuple[str, str]] = []  # (mode, session) of every request that reached the client
        proto = make_protocol()
        info = {s.step_id: (s.title, OWNER[s.automation], s.automation_note) for s in proto.steps}
        header = dict(Candidate=f"{proto.candidate_id} ({proto.protein_name})", Parameters=PLACEHOLDER_BANNER, Biosecurity=S12_NOTE["pending"])
        self.template = {p.session_id: render_python(p, step_info={sid: info[sid] for sid in p.covers}, header=header) for p in plan_sessions(proto)}

    def create(self, **kw):
        self.n += 1
        mode, session = parse_prompt(kw["messages"][0]["content"])
        self.seen.append((mode, session))
        how = self.scripts.get((mode, session), "ok")
        stop, text = "end_turn", ""
        if how == "raise":
            raise RuntimeError("simulated provider failure")
        if how == "refusal":
            stop = "refusal"
        elif how == "text_refusal":
            text = "I can't help with writing a protocol for that."
        elif how == "prose":
            text = "Sure! Let me think about the deck layout first."
        elif how == "truncated":
            stop, text = "max_tokens", "```python\nfrom opentrons import protocol_api\nrequirements = {}\ndef run(ctx):\n    ctx.comment('x')\n"
        else:
            code = self.template[session] if mode == "spec" else FREE_OK.strip("`").removeprefix("python\n")
            if how == "fixed_trash":
                code = code.replace(".drop_tip()", '.drop_tip(ctx.fixed_trash["A1"])')
            elif how == "no_drop":
                code = code.replace("pip.drop_tip()", "pass")
            elif how == "undeclared":
                code = code.replace('    plate["A1"].load_liquid(liquid=liq, volume=100)\n', "")
            elif how == "no_pause" and mode == "spec":
                code = code.replace("ctx.pause(", "ctx.comment(")
            text = "```python\n" + code.strip() + "\n```"
        return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)] if text else [], stop_reason=stop, id=f"msg_selftest_{self.n}",
                               model=kw["model"], usage=SimpleNamespace(input_tokens=len(kw["messages"][0]["content"]) // 4, output_tokens=len(text) // 4))


def cmd_selftest(clean: bool) -> None:
    d = HERE / "_selftest"
    if clean:
        for x in (d, HERE / "_selftest_resume"):
            if x.name.startswith("_selftest") and x.exists():
                shutil.rmtree(x)
        print("selftest directories removed")
        return
    if d.exists():
        shutil.rmtree(d)
    ledger = Ledger(d)
    scripts = {("spec", "s03_transformation"): "refusal", ("spec", "s04_colony_pcr"): "prose", ("spec", "s05_expression_induction"): "raise",
               ("spec", "s06_lysis"): "fixed_trash", ("spec", "s07_ni_nta_purification"): "no_pause", ("spec", "s08_endotoxin_removal"): "text_refusal",
               ("spec", "s09_lal_assay"): "truncated",
               ("free", "s03_transformation"): "refusal", ("free", "s04_colony_pcr"): "prose", ("free", "s05_expression_induction"): "raise",
               ("free", "s06_lysis"): "fixed_trash", ("free", "s07_ni_nta_purification"): "no_drop", ("free", "s08_endotoxin_removal"): "undeclared"}
    client = FakeClient(scripts)
    run_spec("selftest-model", ledger, client=client)
    run_free("selftest-model", ledger, client=client)
    # the guard: a second attempt of the same task must be refused before anything is sent to the client
    n_before = client.n
    again = CodegenRequest(session_id="s06_lysis", plan=None, protocol_steps=[free_steps(make_protocol())[4].model_dump(mode="json")])
    res = CountingLLMBackend("selftest-model", ledger, client=client).generate(again)
    blocked = (res.error or "").startswith("GuardError") and client.n == n_before
    print("guard blocks a duplicate attempt before it reaches the client:", blocked, "|", (res.error or "")[:100])
    # the budget guard: a ledger whose cap is reached refuses a NEW task too
    tiny = Ledger(d / "_cap_probe", cap=1)
    cap_backend = CountingLLMBackend("selftest-model", tiny, client=client)
    first = cap_backend.generate(CodegenRequest(session_id="s02_plasmid_assembly", plan=None, protocol_steps=[free_steps(make_protocol())[0].model_dump(mode="json")]))
    second = cap_backend.generate(CodegenRequest(session_id="s03_transformation", plan=None, protocol_steps=[free_steps(make_protocol())[1].model_dump(mode="json")]))
    print("budget guard: first call ok =", first.code is not None, "| second call blocked =", (second.error or "").startswith("GuardError"))
    shutil.rmtree(d / "_cap_probe")
    cmd_records(d)
    cmd_leakcheck([d])


class _CutOff(BaseException):
    """Stands for a killed process. Raised inside a model call it leaves a call_start with no call_end, because a BaseException is
    not caught by the recorder's `except Exception` (a real kill leaves the same trace)."""


class CuttingClient(FakeClient):
    def __init__(self, cut: set[str]):
        super().__init__({})
        self.cut = cut

    def create(self, **kw):
        mode, session = parse_prompt(kw["messages"][0]["content"])
        if session in self.cut:
            self.seen.append((mode, session))
            raise _CutOff()
        return super().create(**kw)


def cmd_selftest_resume() -> int:
    """Offline check (scripted client, no network, no key) of the resume path: cut a spec batch off with calls in flight, reopen
    the ledger as a fresh process would, resume, and check the invariants the real resume relies on."""
    d = HERE / "_selftest_resume"
    if d.exists():
        shutil.rmtree(d)
    model = "selftest-model"
    all_sessions = {p.session_id for p in plan_sessions(make_protocol())}
    try:
        run_spec(model, Ledger(d), client=CuttingClient({"s04_colony_pcr", "s06_lysis"}))
        print("selftest-resume: the simulated cut-off did not happen (unexpected)")
        return 1
    except _CutOff:
        pass
    snap = Ledger(d)  # what a new process sees after the cut-off
    answered = {s for (_m, mo, s) in snap.answered if mo == "spec"}
    lost = {s for (_m, mo, s) in snap.reissuable if mo == "spec"}
    before = snap.n_started
    client2 = FakeClient({})
    run_spec(model, snap, client=client2, resume=True)
    called = {s for (_mo, s) in client2.seen}
    after = Ledger(d)
    events = read_jsonl(d / "calls.jsonl")
    reissues = [e["session"] for e in events if e.get("event") == "call_start" and e.get("reissue_of")]
    rp = d / f"spec_{model}" / "robot_report.json"
    rep = json.loads(rp.read_text(encoding="utf-8"))["runs"][0]["sessions"]
    codes = {s["session_id"]: s["code_sha256"] for s in rep}
    client3 = FakeClient({})
    run_spec(model, after, client=client3, resume=True)  # now everything is answered: nothing may reach the client
    codes3 = {s["session_id"]: s["code_sha256"] for s in json.loads(rp.read_text(encoding="utf-8"))["runs"][0]["sessions"]}
    try:
        run_spec(model, Ledger(d), client=FakeClient({}))
        refused_plain = False
    except SystemExit:
        refused_plain = True
    checks = {
        "the cut-off left answered sessions and lost calls": bool(answered) and bool(lost),
        "resume sent only the sessions that had no recorded reply": called == all_sessions - answered,
        "no answered session was sent again": not (called & answered),
        "each lost call was re-issued exactly once": sorted(reissues) == sorted(lost),
        "calls started = before + calls sent": after.n_started == before + len(called),
        "the rebuilt report has all sessions, all PASS": len(rep) == len(all_sessions) and all(s["outcome"] == "PASS" for s in rep),
        "a second resume sent nothing": client3.n == 0,
        "the second rebuild is byte-identical in code": codes3 == codes,
        "a rerun without --resume is refused (pass@1)": refused_plain,
    }
    for name, ok in checks.items():
        print(f"  {'ok  ' if ok else 'FAIL'} {name}")
    print(f"selftest-resume: answered={sorted(answered)} lost={sorted(lost)} calls before={before} sent on resume={len(called)}")
    if all(checks.values()):
        shutil.rmtree(d)
        print("selftest-resume: all checks passed (scratch directory removed)")
        return 0
    print(f"selftest-resume: FAILED; scratch directory kept at {d.name}")
    return 1


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # model text can hold non-ASCII; a Windows console would raise
        except (AttributeError, ValueError):
            pass
    ap = argparse.ArgumentParser(prog="run_eval.py", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("template")
    sp = sub.add_parser("spec")
    sp.add_argument("--model", required=True, choices=MODELS)
    sp.add_argument("--resume", action="store_true", help="rebuild an interrupted batch from the ledger: replay recorded replies, call only the rest")
    fp = sub.add_parser("free")
    fp.add_argument("--model", required=True, choices=MODELS)
    fp.add_argument("--steps", default="all", help="comma-separated S8 step ids, or all")
    sub.add_parser("records")
    st = sub.add_parser("selftest")
    st.add_argument("--clean", action="store_true")
    sub.add_parser("selftest-resume")
    sub.add_parser("leakcheck")
    a = ap.parse_args(argv)
    if a.cmd == "template":
        run_template(HERE)
    elif a.cmd in ("spec", "free"):
        ledger = Ledger(HERE)
        install_retry_counter(ledger)
        print(f"calls already started in this data directory: {ledger.n_started} (budget {ledger.cap})")
        if a.cmd == "spec":
            run_spec(a.model, ledger, resume=a.resume)
        else:
            run_free(a.model, ledger, None if a.steps == "all" else [x.strip() for x in a.steps.split(",") if x.strip()])
        print(f"calls started in this data directory so far: {ledger.n_started}; SDK retries logged: {ledger.retries}")
    elif a.cmd == "records":
        cmd_records(HERE)
    elif a.cmd == "selftest":
        cmd_selftest(a.clean)
    elif a.cmd == "selftest-resume":
        return cmd_selftest_resume()
    elif a.cmd == "leakcheck":
        cmd_leakcheck([HERE, ROOT / "docs" / "streamc_llm_eval.md"] if (ROOT / "docs" / "streamc_llm_eval.md").exists() else [HERE])
    return 0


if __name__ == "__main__":
    sys.exit(main())
