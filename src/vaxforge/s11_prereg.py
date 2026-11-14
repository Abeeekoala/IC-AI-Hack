"""S11: hypothesis pre-registration (README S11).

Before any validation is run, this stage freezes what a later metric can be checked against: the digest and tool
versions of every upstream artifact, the seeds and thresholds from the config, and the ranked prediction lists of S3 and
S4. It writes them to ``s11_prereg.json`` with a timestamp and a digest (``payload.frozen_digest``) over the canonical
JSON of everything frozen, the timestamp included.

What this computes: a snapshot, and a digest of it. It computes no metric and no validation result. A stage that has not
run is recorded as ``{"status": "not_run"}``; a stage that ran without status "ok" is recorded with that status and no
list; a config key that is not set is frozen as null (and warned about). Nothing is defaulted to something that looks
like a result.

What this does NOT do: the timestamp is this machine's clock, so it is only evidence once ``frozen_digest`` is
published somewhere the team does not control (a git tag, a public post). This module posts nothing and uses no
network. Re-running the stage before validation replaces the manifest and its timestamp, so publish the digest of the
manifest you mean to keep and do not re-run the stage afterwards.

Convention: every validation output (LOPOV, Bexsero recall@3, win rate, ...) of a run goes under
``runs/<run_id>/validation/``. The stage refuses to write while any file is there, because a pre-registration written
after validation results exist is not one. The refusal returns a ``failed`` artifact in memory and leaves an existing
manifest untouched.

``vaxforge prereg verify RUN_DIR`` recomputes the digest from the files and reports whether the manifest was edited
and whether any upstream artifact changed since. It cannot know what was published: compare the digest it prints with
the one you posted. (cli.py must list this module in EXTENSION_MODULES for the command to exist.)
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path

from .artifacts import STAGE_ORDER, Artifact, ContractError, RunStore, canonical_json, compute_digest
from .cache import utcnow_iso

STAGE = "s11"
VALIDATION_DIR = "validation"
UPSTREAM = STAGE_ORDER[:STAGE_ORDER.index(STAGE)]
# Seeds and thresholds a reader should see in plain text. config.digest() covers the rest of the science-relevant
# config, and each stage's own params are covered by its artifact digest.
FROZEN_CONFIG = ("reproducibility.seed", "reproducibility.pin_versions", "finetune.seed", "pipeline.top_k",
                 "thresholds", "epitopes", "structure")
NOTE = ("registered_at is this machine's clock. It is evidence of when this was frozen only once frozen_digest is "
        "published somewhere the team does not control (a git tag, a public post). Nothing here publishes it.")


def frozen_digest(frozen):
    return "sha256:" + hashlib.sha256(canonical_json(frozen).encode("utf-8")).hexdigest()


def validation_outputs(run_dir):
    """Every file under runs/<run_id>/validation/ (relative paths); empty if there is none."""
    root = Path(run_dir) / VALIDATION_DIR
    return sorted(p.relative_to(run_dir).as_posix() for p in root.rglob("*") if p.is_file())


def _upstream(store, stage):
    """What is frozen about one upstream stage. Reading verifies the artifact's own digest."""
    if not store.has(stage):
        return {"status": "not_run"}
    art = store.read_artifact(stage)
    return {"status": art.status, "digest": art.digest, "vaxforge_version": art.producer.get("vaxforge_version"),
            "tools": art.producer.get("tools", {})}


def _ranking(payload):
    """S3: every protein in rank order (rank-of-truth needs all of them, not only the selected set)."""
    return {"ranking": [{"protein_id": c["protein_id"], "rank": c["rank"], "composite_score": c["composite_score"]}
                        for c in payload["ranking"]]}


def _survivors(payload):
    """S4: survivors in S3 order, rejections with the gate that rejected, and whether screening was complete."""
    return {"survivors": [c["protein_id"] for c in payload["candidates"]],
            "rejected": [{"protein_id": c["protein_id"], "rejected_by": c["rejected_by"]} for c in payload["rejected"]],
            "incomplete_screening": payload["incomplete_screening"], "unavailable_gates": payload["unavailable_gates"]}


PREDICTIONS = {"s3": _ranking, "s4": _survivors}


def _predictions(store):
    out = {}
    for stage, extract in PREDICTIONS.items():
        if not store.has(stage):
            out[stage] = {"status": "not_run"}
            continue
        art = store.read_artifact(stage)
        out[stage] = {"status": art.status, **(extract(art.payload) if art.status == "ok" else {})}
    return out


def run(store, config, clock=utcnow_iso):
    """The `vaxforge run` entrypoint for stage s11. ``clock()`` returns the ISO-8601 UTC timestamp to freeze."""
    present = validation_outputs(store.run_dir)
    if present:
        why = (f"refusing to pre-register: {len(present)} validation output(s) already exist under {VALIDATION_DIR}/ "
               f"(first: {present[0]}). A pre-registration written after validation results exist is not one.")
        print(f"s11_prereg: {why}", file=sys.stderr)
        return Artifact(STAGE, store.artifact_path(STAGE),
                        {"stage": STAGE, "status": "failed", "warnings": [why], "payload": {}, "digest": None})
    upstream = {s: _upstream(store, s) for s in UPSTREAM}
    predictions = _predictions(store)
    settings = {key: config.get(key) for key in FROZEN_CONFIG}
    frozen = {"registered_at": clock(), "config": settings, "config_digest": config.digest(), "upstream": upstream,
              "predictions": predictions, "validation_dir": VALIDATION_DIR}
    warnings = []
    unset = [key for key, value in settings.items() if value is None]
    if unset:
        warnings.append("not set in config, frozen as null: " + ", ".join(unset))
    not_frozen = [f"{s} ({p['status']})" for s, p in predictions.items() if p["status"] != "ok"]
    if not_frozen:
        warnings.append("no prediction list frozen for: " + ", ".join(not_frozen))
    return store.write_artifact(
        STAGE, {"frozen": frozen, "frozen_digest": frozen_digest(frozen), "note": NOTE},
        params={"validation_dir": VALIDATION_DIR}, inputs=[s for s, u in upstream.items() if u["status"] != "not_run"],
        status="ok", warnings=warnings)


def verify(store):
    """Problems found, empty if the manifest still matches its digest and no upstream artifact changed since."""
    if not store.has(STAGE):
        return [f"{store.artifact_name(STAGE)} not found: this run was never pre-registered"]
    art = store.read_artifact(STAGE, verify=False)
    problems = []
    if compute_digest(art.data) != art.digest:
        problems.append(f"{store.artifact_name(STAGE)}: the envelope digest does not match its content")
    frozen = art.payload["frozen"]
    if frozen_digest(frozen) != art.payload["frozen_digest"]:
        problems.append("the frozen manifest does not match frozen_digest: it was edited after the fact")
    short = lambda u: (u.get("digest") or u["status"])[:19]  # noqa: E731
    for stage, was in frozen["upstream"].items():
        try:
            now = _upstream(store, stage)
        except ContractError as exc:  # the artifact no longer matches its own digest
            problems.append(f"{stage}: {exc}")
            continue
        if now != was:
            problems.append(f"{stage}: changed since registration (was {short(was)}, now {short(now)})")
    return problems


def _verify_command(args):
    run_dir = Path(args.run_dir)
    if not run_dir.is_dir():  # RunStore would create it
        print(f"s11_prereg: {run_dir} is not a folder", file=sys.stderr)
        return 1
    store = RunStore(run_dir)
    problems = verify(store)
    if store.has(STAGE):
        payload = store.read_artifact(STAGE, verify=False).payload
        print(f"{run_dir}: registered {payload['frozen']['registered_at']}")
        print(f"frozen digest: {payload['frozen_digest']}")
        print("compare it with the digest you published; this command cannot know what was published")
    for p in problems:
        print(f"  PROBLEM: {p}")
    print("OK - the manifest matches its digest and no upstream artifact has changed since" if not problems
          else f"{len(problems)} problem(s)")
    return 0 if not problems else 1


def register_cli(subparsers, common):
    """`vaxforge prereg verify RUN_DIR`. Exit code 0 if intact, 1 on any problem."""
    p = subparsers.add_parser("prereg", help="S11 tools: check a pre-registration manifest")
    sub = p.add_subparsers(dest="prereg_command", required=True)
    v = sub.add_parser("verify", help="recompute the manifest digest and check the upstream artifacts against it")
    v.add_argument("run_dir")
    v.set_defaults(func=_verify_command)
