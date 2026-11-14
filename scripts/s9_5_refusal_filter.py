#!/usr/bin/env python3
"""The base-model refusal filter for S9.5 training candidates (README S9.5, "Filter before training").

    python3 scripts/s9_5_refusal_filter.py tasks CANDIDATES.jsonl --out tasks.jsonl
    vaxforge finetune generate tasks.jsonl --arm base=MODEL --endpoint URL --max-tokens N --out DIR   # the base model, once per prompt
    python3 scripts/s9_5_refusal_filter.py apply CANDIDATES.jsonl DIR/generations.jsonl --out CHECKED.jsonl

``tasks`` writes the prompts of every candidate that could otherwise be trained on (it simulates and its family label is
"none" without doubt); the rest are excluded already and are not sent to a model. ``apply`` fills ``refused_by_base`` from
the replies with the stop-gap detector in validation/t2wl_adapter.py (to be replaced by stream C's detector, README S9).
A reply cut off by max_tokens counts as a failed attempt, not a refusal, so keep max_tokens high enough for a refusal to
finish (a few dozen tokens). A candidate with no reply keeps ``refused_by_base: null``, which the manifest excludes.
Standard library only.
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vaxforge import s9_5_finetune as ft  # noqa: E402


def eligible(c):
    return c.get("simulate_pass") is True and c.get("heldout_family") == "none" and not c.get("family_doubt")


def read_jsonl(path):
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines() if line.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="command", required=True)
    t = sub.add_parser("tasks")
    t.add_argument("candidates")
    t.add_argument("--out", required=True)
    a = sub.add_parser("apply")
    a.add_argument("candidates")
    a.add_argument("generations")
    a.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    candidates = read_jsonl(args.candidates)
    if args.command == "tasks":
        tasks = ft.candidate_tasks([c for c in candidates if eligible(c)])
        Path(args.out).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in tasks), encoding="utf-8")
        print(f"{len(tasks)} prompts to send to the base model, of {len(candidates)} candidates")
        return 0
    refused = ft._validation_module("t2wl_adapter").stopgap_refusal
    checked = ft.apply_refusals(candidates, read_jsonl(args.generations), refused)
    Path(args.out).write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in checked), encoding="utf-8")
    done = [c for c in checked if c["refused_by_base"] is not None]
    print(f"{len(done)} candidates checked, {sum(1 for c in done if c['refused_by_base'])} refused by the base model, "
          f"{sum(1 for c in checked if eligible(c) and c['refused_by_base'] is None)} eligible ones still unchecked")
    return 0


if __name__ == "__main__":
    sys.exit(main())
