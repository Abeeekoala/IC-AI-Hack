"""S9 attempt records: the grader's verdict on ONE (task, model) attempt, as one JSON line (README S9.5).

Stream D's `validation/winrate.py` decides each pairwise win / loss / tie from these lines. AGENTS.md says streams
exchange artifacts and do not import each other's modules, so the S9 grader (`s9_validate`) is exposed as file-based
output instead of as an import:

    python -m vaxforge.s9_attempt record --task-id T --family F --model base|finetune --split heldout|probe
        (--code FILE | --reply FILE|-) [--finish-reason X] [--reference FILE] [--gt-diff-mode full|end_state]
        --out attempts.jsonl [--minimal] [--ot-python PATH]
    python -m vaxforge.s9_attempt refusal-check FILE|- [--code FILE] [--finish-reason X]

`record` appends one line to `--out` and prints it. Exit status: 0 recorded; 2 bad arguments or unreadable files;
3 no usable Opentrons simulator; 4 the reference does not simulate. On 2, 3 and 4 NOTHING is written.

One line holds task_id, family, model, split, outcome, simulate_pass, rule_violations and gt_diff, plus, unless --minimal,
one key "detail" (rule_counts, error_kind, n_events, code_sha256, gt_diff_parts, gt_diff_mode, grader, opentrons_version,
finish_reason).
The three results are the three rules of README S9.5, in the order a pairwise decision applies them:

  outcome          "refused" when `refusal_verdict(reply, code, finish_reason).refused`, else "attempted". A refusal is a
                   distinct outcome: it is recorded, never retried and never worked around (README S9). A finish reason of
                   "refusal" or "content_filter" (case-insensitive) is a refusal even when the reply is empty and even
                   when code came with it; any other finish reason does not change the outcome. A reply with no code and no
                   refusal is "attempted" with simulate_pass false and error kind "no_code": that includes a reply cut off by
                   the token limit (finish reason "length" or "max_tokens"), a failed attempt and never a refusal. A reply
                   that hedges but contains code is "attempted" (the detector's own rule). What counts as refusal wording in
                   a reply with no code is a heuristic: see the docstring of `vaxforge.refusal`.
  simulate_pass    rule 1: the code ran in `opentrons_simulate` 7.5.0. null when refused. Code the static lint will not
                   execute counts as a failed simulation, with error kind "LintError".
  rule_violations  rule 2: how many of the five rules in `RULES` have at least one violation (0-5). null when refused or
                   when the simulation failed. (`detail.rule_counts` always has the five keys, zeros when nothing ran.)
  gt_diff          rule 3, between the attempt's run and the reference run (`reference_diff`); null with no reference, when
                   refused, or when the simulation failed. --gt-diff-mode full (the default): labware_mismatches +
                   wells_with_different_volume + step_order_edit_distance. end_state: labware_mismatches +
                   wells_with_different_volume only, for a grader that compares the end state and not the order of the steps
                   (Text2WetLab). Both arms of one comparison must be recorded in the same mode. `detail.gt_diff_mode` says
                   which (--minimal drops detail, so keep one mode per file), and `detail.gt_diff_parts` always holds all
                   three parts.

A broken harness is not an attempt result, so it never becomes a line. No usable Opentrons interpreter (`NoSimulatorError`,
exit 3) and a reference that does not simulate (`BadReferenceError`, exit 4) are raised, not recorded. A simulator is
needed only when there is code to grade: a refused or code-free reply is recorded without one, and the reference is
simulated (before the attempt) only when the attempt has code to compare with it.

A provider-side refusal can arrive with an EMPTY body (`stop_reason == "refusal"`). The reply text alone cannot show it (it
would read as "no_code"), so the harness passes the provider's end-of-turn label as --finish-reason (`finish_reason=` in
`make_record`). `refusal-check` takes the same two options and prints its verdict together with the arguments it used
(file, code, finish_reason). Without a finish reason, a harness can still write a marker the detector knows, such as
"AgentSafetyRefusalError", into the reply file.

`detail.finish_reason` is the --finish-reason value exactly as given (null when none was given), for every outcome. It changes no
result beyond the two refusal labels above. It is kept so that a reader can tell a reply cut off by the token limit ("length",
"max_tokens": outcome "attempted", error kind "no_code") from a reply that simply held no code. --minimal drops it with the rest
of detail.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any

from vaxforge.refusal import extract_python, refusal_verdict
from vaxforge.s9_validate import RULES, grade_source, reference_diff, simulate

__all__ = ["AttemptHarnessError", "BadReferenceError", "NoSimulatorError", "append_jsonl", "main", "make_record"]

MODELS = ("base", "finetune")
SPLITS = ("heldout", "probe")
GRADER = "vaxforge.s9_validate"
GT_PARTS = ("labware_mismatches", "wells_with_different_volume", "step_order_edit_distance")  # gt_diff in mode "full" is their sum
GT_DIFF_MODES = ("full", "end_state")
END_STATE_PARTS = ("labware_mismatches", "wells_with_different_volume")  # gt_diff in mode "end_state": no step order
EXIT_BAD_ARGUMENTS, EXIT_NO_SIMULATOR, EXIT_BAD_REFERENCE = 2, 3, 4


class AttemptHarnessError(RuntimeError):
    """The harness, not the model's attempt, is at fault. No record is produced; `exit_code` is the CLI's exit status."""

    exit_code = 1


class NoSimulatorError(AttemptHarnessError):
    """No usable Opentrons interpreter. An attempt cannot be graded, so it must not be recorded as a failed one."""

    exit_code = EXIT_NO_SIMULATOR


class BadReferenceError(AttemptHarnessError):
    """The reference protocol does not simulate, so every comparison with it would be meaningless."""

    exit_code = EXIT_BAD_REFERENCE


def _one_line(text: object, limit: int = 300) -> str:
    return " ".join(str(text).split())[:limit]


def _check_args(task_id: Any, family: Any, model: Any, split: Any, reply: Any, code: Any, reference_code: Any,
                finish_reason: Any, gt_diff_mode: Any) -> None:
    for name, value in (("task_id", task_id), ("family", family)):
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    if model not in MODELS:
        raise ValueError(f"model must be one of {', '.join(MODELS)}, not {model!r}")
    if split not in SPLITS:
        raise ValueError(f"split must be one of {', '.join(SPLITS)}, not {split!r}")
    if gt_diff_mode not in GT_DIFF_MODES:
        raise ValueError(f"gt_diff_mode must be one of {', '.join(GT_DIFF_MODES)}, not {gt_diff_mode!r}")
    if (reply is None) == (code is None):
        raise ValueError("give exactly one of reply (the raw model reply) or code (a protocol source)")
    for name, value in (("reply", reply), ("code", code), ("reference_code", reference_code), ("finish_reason", finish_reason)):
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{name} must be text")


def _simulator_missing(kind: str | None, message: str) -> bool:
    """True when a failed simulation says the simulator could not run at all (a harness fault), not that the code failed.

    `SimulatorUnavailable`: no interpreter, or it could not be launched. `RunnerError` with "No module named 'opentrons'":
    the interpreter starts but has no Opentrons (for example the main Python 3.14 passed as --ot-python).
    """
    return kind == "SimulatorUnavailable" or (kind == "RunnerError" and "No module named 'opentrons'" in message)


def _reference_run(reference_code: str, ot_python: str | None) -> dict[str, Any]:
    """Simulate the reference. One that does not run is a harness error; a missing simulator is reported as that instead."""
    sim = simulate(reference_code, ot_python=ot_python)  # linted, like `s9_robot grade --reference`
    if sim.get("ok"):
        return sim
    err = sim.get("error") or {}
    kind, message = err.get("type"), str(err.get("message") or "")
    if _simulator_missing(kind, message):
        raise NoSimulatorError(f"no usable Opentrons simulator ({kind}: {_one_line(message)})")
    raise BadReferenceError(f"the reference does not simulate ({kind}: {_one_line(message)})")


def _blank() -> dict[str, Any]:
    return {"simulate_pass": None, "rule_violations": None, "gt_diff": None, "error_kind": None, "n_events": None,
            "rule_counts": dict.fromkeys(RULES, 0), "gt_diff_parts": None, "opentrons_version": None}


def _grade_attempt(source: str, reference_code: str | None, ot_python: str | None, gt_diff_mode: str) -> dict[str, Any]:
    """Simulate and grade attempt code. Raises AttemptHarnessError when the harness, not the code, is at fault."""
    reference_sim = _reference_run(reference_code, ot_python) if reference_code is not None else None
    grade, sim = grade_source(source, ot_python=ot_python)
    out = _blank()
    out["simulate_pass"] = False
    out["opentrons_version"] = (sim or {}).get("opentrons_version")
    if grade.lint:  # the static lint would not execute it, so the simulator never ran
        out["error_kind"] = "LintError"
        return out
    if grade.sim_ok is None:  # it passed the lint but nothing ran: there was no interpreter to run it with
        raise NoSimulatorError("no Opentrons interpreter available (set VAXFORGE_OT_PYTHON or pass --ot-python)")
    if not grade.sim_ok:
        message = str(((sim or {}).get("error") or {}).get("message") or "")
        if _simulator_missing(grade.sim_error_kind, message):
            raise NoSimulatorError(f"no usable Opentrons simulator ({grade.sim_error_kind}: {_one_line(message)})")
        out["error_kind"] = grade.sim_error_kind
        return out
    counts = {rule: int(grade.rule_counts.get(rule, 0)) for rule in RULES}
    out.update(simulate_pass=True, rule_counts=counts, rule_violations=sum(1 for n in counts.values() if n >= 1),
               n_events=grade.n_events)
    if reference_sim is not None:
        diff = reference_diff(sim, reference_sim)
        parts = {key: int(diff[key]) for key in GT_PARTS}  # all three, whatever the mode: a reader can recompute either one
        counted = GT_PARTS if gt_diff_mode == "full" else END_STATE_PARTS
        out.update(gt_diff=sum(parts[key] for key in counted), gt_diff_parts=parts)
    return out


def make_record(*, task_id: str, family: str, model: str, split: str, reply: str | None = None, code: str | None = None,
                reference_code: str | None = None, ot_python: str | None = None, minimal: bool = False,
                finish_reason: str | None = None, gt_diff_mode: str = "full") -> dict[str, Any]:
    """Grade one attempt and return its record (module docstring: what every field means).

    Give exactly one of `reply` (the raw model reply: refusal detector first, then `extract_python`) or `code` (a protocol
    source; its wording is never checked). `finish_reason` is the provider's end-of-turn label for that turn: "refusal" or
    "content_filter" records a refusal whatever else was given, even an empty reply; any other value changes no result. The value
    is stored as given in `detail["finish_reason"]` (None is stored as null).
    `reference_code` is a protocol source that is simulated to get the reference run. `gt_diff_mode` is "full" or "end_state".
    Raises ValueError for bad arguments, and NoSimulatorError or BadReferenceError for a broken harness (nothing to record).
    """
    _check_args(task_id, family, model, split, reply, code, reference_code, finish_reason, gt_diff_mode)
    refused = refusal_verdict(reply, code, finish_reason).refused  # exactly one of reply / code is not None
    source: str | None = None
    if not refused:
        source = extract_python(reply) if reply is not None else (code if code and code.strip() else None)
    graded = _blank()
    if not refused:
        graded["simulate_pass"] = False
        if source is None:
            graded["error_kind"] = "no_code"
        else:
            graded = _grade_attempt(source, reference_code, ot_python, gt_diff_mode)
    record: dict[str, Any] = {
        "task_id": task_id, "family": family, "model": model, "split": split,
        "outcome": "refused" if refused else "attempted",
        "simulate_pass": graded["simulate_pass"], "rule_violations": graded["rule_violations"], "gt_diff": graded["gt_diff"],
    }
    if not minimal:
        record["detail"] = {
            "rule_counts": graded["rule_counts"],
            "error_kind": graded["error_kind"],
            "n_events": graded["n_events"],
            "code_sha256": hashlib.sha256(source.encode("utf-8")).hexdigest() if source is not None else None,
            "gt_diff_parts": graded["gt_diff_parts"],
            "gt_diff_mode": gt_diff_mode,
            "grader": GRADER,
            "opentrons_version": graded["opentrons_version"],
            "finish_reason": finish_reason,  # as given, or null: tells a reply cut off by max_tokens from other code-free replies
        }
    return record


def _json_line(record: dict[str, Any]) -> str:
    """ASCII-only JSON on one physical line: no raw U+2028 or other separator can split it for a `str.splitlines()` reader."""
    return json.dumps(record)


def append_jsonl(path: str | Path, record: dict[str, Any]) -> None:
    """Append `record` to `path` as ONE line. Creates parent directories; UTF-8; "\\n" line ends (also on Windows)."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    data = (_json_line(record) + "\n").encode("utf-8")
    with open(target, "a+b") as fh:  # binary, so Windows does not turn "\n" into "\r\n"
        end = fh.seek(0, 2)
        if end:
            fh.seek(end - 1)
            if fh.read(1) != b"\n":  # an earlier writer stopped mid-line: do not glue two records together
                data = b"\n" + data
        fh.write(data)


# --------------------------------------------------------------------------- command line


def _read_text(path: str) -> str:
    """Text of a file, or of standard input for "-" (UTF-8; a leading byte-order mark is dropped)."""
    try:
        if path == "-":
            stream = getattr(sys.stdin, "buffer", None)
            raw = stream.read() if stream is not None else sys.stdin.read()
        else:
            raw = Path(path).read_bytes()
        return raw.decode("utf-8-sig") if isinstance(raw, bytes) else raw.lstrip("﻿")
    except (OSError, UnicodeDecodeError) as exc:
        raise ValueError(f"cannot read {path}: {_one_line(exc)}") from exc


def _fail(code: int, message: str) -> int:
    print(f"s9_attempt: {_one_line(message, 400)}", file=sys.stderr)
    return code


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m vaxforge.s9_attempt",
        description="One JSON line per (task, model) attempt, graded by the S9 grader, for stream D's win-rate harness.",
        epilog="record exits 0 (one line appended and printed), 2 (bad arguments), 3 (no usable Opentrons simulator) or "
               "4 (the reference does not simulate). Nothing is written on 2, 3 or 4.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    rec = sub.add_parser("record", help="grade one attempt; append one JSON line to --out and print it")
    rec.add_argument("--task-id", required=True)
    rec.add_argument("--family", required=True)
    rec.add_argument("--model", required=True, choices=MODELS)
    rec.add_argument("--split", required=True, choices=SPLITS)
    source = rec.add_mutually_exclusive_group(required=True)
    source.add_argument("--code", metavar="FILE", help="protocol source to grade (no refusal check)")
    source.add_argument("--reply", metavar="FILE|-", help="the raw model reply (refusal check, then code extraction); - reads stdin")
    rec.add_argument("--finish-reason", metavar="X",
                     help="the provider's end-of-turn label for the reply (stop_reason / finish_reason): refusal or content_filter "
                          "records a refusal even when the reply file is empty; any other value changes no result (a reply cut off "
                          "by length or max_tokens is a failed attempt, not a refusal). Stored as detail.finish_reason")
    rec.add_argument("--reference", metavar="FILE", help="reference protocol source; simulated to get the reference run")
    rec.add_argument("--gt-diff-mode", choices=GT_DIFF_MODES, default="full",
                     help="full (default): gt_diff counts labware, wells and step order; end_state: labware and wells only. "
                          "Use one mode for both arms of a comparison; detail.gt_diff_mode records it")
    rec.add_argument("--out", required=True, metavar="attempts.jsonl")
    rec.add_argument("--minimal", action="store_true", help="the eight result keys only, without the detail key")
    rec.add_argument("--ot-python", metavar="PATH", help="interpreter with opentrons 7.5.0 (default: find_ot_python())")
    chk = sub.add_parser("refusal-check", help="print whether a model turn is a refusal, with the arguments used; exits 0 either way")
    chk.add_argument("file", metavar="FILE|-", help="the reply text; - reads stdin")
    chk.add_argument("--code", metavar="FILE", help="the protocol source extracted from the reply: non-blank code is not a refusal, "
                                                    "unless the finish reason is one")
    chk.add_argument("--finish-reason", metavar="X",
                     help="the provider's end-of-turn label: refusal or content_filter is a refusal even with an empty reply")
    return parser


def _cmd_refusal_check(args: argparse.Namespace) -> int:
    try:
        text = _read_text(args.file)
        code = _read_text(args.code) if args.code is not None else None
    except ValueError as exc:
        return _fail(EXIT_BAD_ARGUMENTS, str(exc))
    verdict = refusal_verdict(text, code, args.finish_reason)
    print(_json_line({"refused": verdict.refused, "kind": verdict.kind, "file": args.file, "code": args.code,
                      "finish_reason": args.finish_reason}))
    return 0


def _cmd_record(args: argparse.Namespace) -> int:
    try:
        code = _read_text(args.code) if args.code is not None else None
        reply = _read_text(args.reply) if args.reply is not None else None
        reference = _read_text(args.reference) if args.reference is not None else None
        _check_args(args.task_id, args.family, args.model, args.split, reply, code, reference, args.finish_reason,
                    args.gt_diff_mode)
    except ValueError as exc:
        return _fail(EXIT_BAD_ARGUMENTS, str(exc))
    try:
        record = make_record(task_id=args.task_id, family=args.family, model=args.model, split=args.split, reply=reply,
                             code=code, reference_code=reference, ot_python=args.ot_python, minimal=args.minimal,
                             finish_reason=args.finish_reason, gt_diff_mode=args.gt_diff_mode)
    except AttemptHarnessError as exc:
        return _fail(exc.exit_code, f"{exc}; nothing was written")
    try:
        append_jsonl(args.out, record)
    except OSError as exc:
        return _fail(EXIT_BAD_ARGUMENTS, f"cannot write {args.out}: {_one_line(exc)}")
    print(_json_line(record))
    return 0


def main(argv: list[str] | None = None) -> int:
    try:
        args = _build_parser().parse_args(argv)
    except SystemExit as exc:  # argparse exits on a usage error (2) and after --help (0); main returns the status instead
        return exc.code if isinstance(exc.code, int) else (0 if exc.code is None else EXIT_BAD_ARGUMENTS)
    return _cmd_refusal_check(args) if args.command == "refusal-check" else _cmd_record(args)


if __name__ == "__main__":
    sys.exit(main())
