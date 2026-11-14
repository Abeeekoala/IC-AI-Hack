"""S9.5: the audited training manifest and the run guard (README §S9.5).

Pure functions over JSON artifacts: no network calls and no imports from other streams.

Input is a JSONL of candidate pairs, one per protocol:

  {"id": "...", "source": "opentrons-apache", "prompt": "...", "code": "...",
   "heldout_family": "colony-pcr" | "none",  # assigned by hand or by the corpus labeller; absent = unassigned
   "family_doubt": false,                    # true when that assignment is uncertain
   "assigned_by": "human" | "agent-review" | "rule",  # who set heldout_family; absent = "rule"
   "refused_by_base": true | false | null,   # S9's refusal detector on the base model; null = not run
   "simulate_pass": true | false | null}     # opentrons_simulate v7.5.0 on the code; null = not run

build_manifest() keeps a pair only if every rule passes, and records each rejection with all of its reasons so a
rejected pair can be recovered and inspected (README §4 does the same for S4). assert_run_allowed() is the gate
in front of any AutoScientist run.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import sys
from pathlib import Path

FAMILIES_FILE = Path(__file__).resolve().parents[2] / "validation" / "heldout_families.json"
MIN_SFT_ROWS = 1000  # Adaption's documented SFT minimum ("effective rows"; what that counts is unverified)
EXCLUDED_SOURCES = {
    "protocols.io": "site policy (/llms.txt) forbids model training and fine-tuning",
    "text2wetlab": "evaluation only: its tasks, reference solutions and tests are never training data",
}


class RunNotAllowed(Exception):
    pass


def _sha(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def load_candidates(path):
    out = []
    for i, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            c = json.loads(line)
        except json.JSONDecodeError as e:
            raise ValueError(f"{path}:{i}: not valid JSON ({e})") from None
        missing = [k for k in ("id", "source", "prompt", "code") if not isinstance(c.get(k), str) or not c[k]]
        if missing:
            raise ValueError(f"{path}:{i}: missing or empty {missing}")
        out.append(c)
    return out


def reasons_for(c, heldout_families):
    """Every rule the candidate fails; an empty list means it may be trained on."""
    why = []
    if c.get("source") in EXCLUDED_SOURCES:
        why.append(f"source-excluded: {c['source']}: {EXCLUDED_SOURCES[c['source']]}")
    family = c.get("heldout_family")
    if family is None:
        why.append("family-unassigned")
    elif family in heldout_families:
        why.append(f"heldout-family: {family}")
    elif family != "none":
        why.append(f"unknown-family-label: {family}")
    if c.get("family_doubt", False):
        why.append("family-in-doubt")
    if c.get("refused_by_base") is None:
        why.append("refusal-filter-not-run")
    elif c["refused_by_base"]:
        why.append("base-model-refused")
    if c.get("simulate_pass") is None:
        why.append("simulate-not-run")
    elif not c["simulate_pass"]:
        why.append("fails-opentrons_simulate")
    return why


def build_manifest(candidates, heldout_families, min_rows=MIN_SFT_ROWS):
    ids = [c["id"] for c in candidates]
    if len(ids) != len(set(ids)):
        raise ValueError("candidate ids are not unique")
    included, excluded, refused = [], [], []
    for c in candidates:
        why = reasons_for(c, heldout_families)
        if not why:
            included.append({"id": c["id"], "source": c["source"], "heldout_family": c["heldout_family"],
                             "assigned_by": c.get("assigned_by", "rule"),
                             "prompt_sha256": _sha(c["prompt"]), "code_sha256": _sha(c["code"])})
            continue
        excluded.append({"id": c["id"], "source": c["source"], "reasons": why})
        # prompts the base model refused become the refusal probe, unless their source is off limits
        if "base-model-refused" in why and not any(r.startswith("source-excluded") for r in why):
            refused.append(c["id"])
    return {
        "schema_version": 1,
        "training_method": "instruction",  # SFT only; the alignment (DPO) method is never used
        "augmentation": "none",            # no platform-generated rows; Adaptive Data improve/invent not run
        "heldout_families": sorted(heldout_families),
        "min_rows": min_rows,
        "counts": {"candidates": len(candidates), "included": len(included), "excluded": len(excluded)},
        "meets_min_rows": len(included) >= min_rows,
        "included": included,
        "excluded": excluded,
        "dropped_for_refusal": refused,
    }


def apply_refusals(candidates, generations, refused):
    """Copies of the candidates with refused_by_base filled in from the base arm's generations.

    generations are rows of generations.jsonl; only split "candidate" and arm "base" count. refused(text, code,
    finish_reason) is S9's refusal detector. A reply cut off by max_tokens is a failed attempt, not a refusal. A
    candidate with no generation keeps refused_by_base as it was (null excludes it from the manifest). A generation
    made for a different prompt is an error: the flag has to describe the prompt that would be trained on."""
    rows = {g["task_id"]: g for g in generations if g.get("split") == "candidate" and g.get("arm") == "base"}
    out = []
    for c in candidates:
        c = dict(c)
        g = rows.get(c["id"])
        if g is not None:
            if g["prompt_sha256"] != _sha(c["prompt"]):
                raise ValueError(f"candidate {c['id']!r}: the base-model reply was generated for a different prompt")
            cut_off = g.get("finish_reason") == "length"
            c["refused_by_base"] = False if cut_off else bool(refused(g.get("text") or "", g.get("code"), g.get("finish_reason")))
        out.append(c)
    return out


def training_rows(candidates, manifest):
    """The prompt/completion rows to upload: exactly the manifest's included pairs, checked against their hashes."""
    by_id = {c["id"]: c for c in candidates}
    rows = []
    for e in manifest["included"]:
        c = by_id.get(e["id"])
        if c is None or _sha(c["prompt"]) != e["prompt_sha256"] or _sha(c["code"]) != e["code_sha256"]:
            raise ValueError(f"candidate {e['id']!r} is missing or changed since the manifest was built")
        rows.append({"prompt": c["prompt"], "completion": c["code"]})
    return rows


def assert_run_allowed(manifest, base_model_id, cleared_sources, leakage_violations):
    """Raise RunNotAllowed unless every README precondition holds.

    cleared_sources maps each source in the manifest to the written licence or permission that clears it.
    leakage_violations is the list validation/leakage_check.py returned; None means it was not run.
    """
    problems = []
    if not base_model_id:
        problems.append("finetune.base_model_id is not pinned")
    n = manifest["counts"]["included"]
    if n < manifest["min_rows"]:
        problems.append(f"{n} rows is under the {manifest['min_rows']}-row minimum; the run does not happen")
    if manifest.get("training_method") != "instruction" or manifest.get("augmentation") != "none":
        problems.append("the manifest is not SFT-only with no augmentation")
    for source in sorted({e["source"] for e in manifest["included"]}):
        if source in EXCLUDED_SOURCES:
            problems.append(f"source {source!r} is excluded: {EXCLUDED_SOURCES[source]}")
        elif not str(cleared_sources.get(source, "")).strip():
            problems.append(f"source {source!r} has no recorded licence or permission")
    not_by_a_person = [e for e in manifest["included"] if e.get("assigned_by", "rule") != "human"]
    if not_by_a_person:
        kinds = ", ".join(sorted({e.get("assigned_by", "rule") for e in not_by_a_person}))
        problems.append(f"{len(not_by_a_person)} included family label(s) were not assigned by a person ({kinds}); "
                        "README S9.5 requires them confirmed by hand")
    if leakage_violations is None:
        problems.append("the leakage check has not been run")
    elif leakage_violations:
        problems.append(f"the leakage check found {len(leakage_violations)} violation(s)")
    if problems:
        raise RunNotAllowed("; ".join(problems))


STAGE = "s9_5"

def _not_run_reasons(store, config):
    reasons = []
    missing = [s for s in ("s8", "s9") if not store.has(s)]
    if missing:
        reasons.append("upstream artifact not present: " + ", ".join(missing))
    for key in ("base_model_id", "model_id"):
        if not config.get(f"finetune.{key}"):
            reasons.append(f"finetune.{key} is not set")
    reasons.append("this stage does not generate yet: the fine-tune is evaluated offline with "
                   "s9_5_generate.py and validation/winrate.py")
    return reasons


def run(store, config):
    """The `vaxforge run` entrypoint for stage s9_5 (README S9.5).

    S9.5 is additive and `vaxforge run` stops on any status but "ok", so this stage writes "ok" and says in the payload
    that the fine-tune was not run, and why. It never records a result it did not compute."""
    reasons = _not_run_reasons(store, config)
    payload = {"fine_tune": {"status": "not_run", "reasons": reasons},
               "base_model_id": config.get("finetune.base_model_id"), "model_id": config.get("finetune.model_id")}
    return store.write_artifact(
        STAGE, payload, params={"eval_tasks": config.get("finetune.eval_tasks"), "seed": config.get("finetune.seed")},
        inputs=[s for s in ("s8", "s9") if store.has(s)], status="ok", warnings=["S9.5 not run: " + "; ".join(reasons)])


def _validation_module(name):
    path = Path(__file__).resolve().parents[2] / "validation" / f"{name}.py"
    if not path.is_file():
        raise ValueError(f"{path} not found: this command needs a source checkout of the repository")
    spec = importlib.util.spec_from_file_location(f"_vaxforge_validation_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _command(name):
    def go(args):
        if name == "manifest":
            return main(args.rest)
        try:
            module = _validation_module({"check": "leakage_check", "winrate": "winrate"}[name])
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return module.main(args.rest)
    return go


def register_cli(subparsers, common):
    """`vaxforge finetune manifest|check|winrate ...`: each passes its arguments to that tool's own CLI.
    Exit codes follow the tool: check returns 2 on violations, winrate 3 on a refusal red flag."""
    p = subparsers.add_parser("finetune", parents=[common], help="S9.5 tools: training manifest, leakage check, win rate")
    sub = p.add_subparsers(dest="finetune_command", required=True)
    for name, text in (("manifest", "build the audited training manifest"),
                       ("check", "adversarial leakage check of a manifest (validation/leakage_check.py)"),
                       ("winrate", "pairwise win rate, fine-tune vs base (validation/winrate.py)")):
        q = sub.add_parser(name, help=text, add_help=False)  # --help reaches the tool's own parser
        q.add_argument("rest", nargs=argparse.REMAINDER)
        q.set_defaults(func=_command(name))


def main(argv=None):
    ap = argparse.ArgumentParser(prog="vaxforge finetune manifest", description="Build the audited S9.5 training manifest")
    ap.add_argument("candidates", help="JSONL of candidate pairs")
    ap.add_argument("--families", default=str(FAMILIES_FILE), help="held-out families JSON")
    ap.add_argument("--out", required=True, help="manifest JSON to write")
    ap.add_argument("--training-out", help="also write the included pairs as prompt/completion JSONL")
    ap.add_argument("--min-rows", type=int, default=MIN_SFT_ROWS)
    args = ap.parse_args(argv)
    try:
        candidates = load_candidates(args.candidates)
        families = set(json.loads(Path(args.families).read_text(encoding="utf-8"))["families"])
        manifest = build_manifest(candidates, families, args.min_rows)
        rows = training_rows(candidates, manifest) if args.training_out else []
    except (ValueError, KeyError, OSError) as e:
        print(f"s9_5_finetune: {e}", file=sys.stderr)
        return 1
    Path(args.out).write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if args.training_out:
        Path(args.training_out).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    c = manifest["counts"]
    print(f"{c['included']} of {c['candidates']} candidates included; {len(manifest['dropped_for_refusal'])} dropped for refusal")
    if not manifest["meets_min_rows"]:
        print(f"s9_5_finetune: under the {args.min_rows}-row minimum, so the run does not happen", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
