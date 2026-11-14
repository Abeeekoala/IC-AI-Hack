"""S9.5: the audited training manifest and the run guard (README §S9.5).

Pure functions over JSON artifacts: no network calls and no imports from other streams.

Input is a JSONL of candidate pairs, one per protocol:

  {"id": "...", "source": "opentrons-apache", "prompt": "...", "code": "...",
   "heldout_family": "colony-pcr" | "none",  # assigned by hand or by the corpus labeller; absent = unassigned
   "family_doubt": false,                    # true when that assignment is uncertain
   "refused_by_base": true | false | null,   # S9's refusal detector on the base model; null = not run
   "simulate_pass": true | false | null}     # opentrons_simulate v7.5.0 on the code; null = not run

build_manifest() keeps a pair only if every rule passes, and records each rejection with all of its reasons so a
rejected pair can be recovered and inspected (README §4 does the same for S4). assert_run_allowed() is the gate
in front of any AutoScientist run.
"""
from __future__ import annotations

import argparse
import hashlib
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
    if leakage_violations is None:
        problems.append("the leakage check has not been run")
    elif leakage_violations:
        problems.append(f"the leakage check found {len(leakage_violations)} violation(s)")
    if problems:
        raise RunNotAllowed("; ".join(problems))


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the audited S9.5 training manifest")
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
