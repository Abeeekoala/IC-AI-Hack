#!/usr/bin/env python3
"""Assemble the candidate file for the S9.5 manifest: every real row, plus just enough synthetic rows (README S9.5, Size).

    python3 scripts/s9_5_assemble.py --real REAL.jsonl [REAL.jsonl ...] --synthetic SYNTH.jsonl --out CANDIDATES.jsonl
                                     [--rows 1000] [--seed S]

REAL files hold the real candidate pairs after the refusal filter. The synthetic rows are chosen by kind (the id prefix before
"-s", for example ``vaxforge-synthetic:serial_dilution``) in round-robin order from a seeded shuffle of each kind, so the
subset is as even across the generator's kinds as the supply allows, and only as many are taken as are needed for
``--rows`` rows in the manifest. Only synthetic rows that would pass every manifest rule are drawn. Prints the counts and the
real share: that ratio goes into the README with the manifest.

A real file may hold the same id twice: the plain simulate run that failed (the file needs its repository's own labware
definition) and the later re-run that passed. ``merge_duplicates`` keeps one row per id, the passing copy, and never loosens a
label: a held-out family on either copy sticks, and a family in doubt on either copy sticks, so a merge can only exclude more.
Two copies of an id with different code or prompt are an error, not a merge.
"""
import argparse
import json
import random
import re
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vaxforge import s9_5_finetune as ft  # noqa: E402


def kind_of(row_id):
    m = re.match(r"(.+?)-s\d", row_id)
    return m.group(1) if m else row_id


def merge_duplicates(rows):
    """-> (rows with unique ids in first-seen order, number merged, number whose label was tightened)."""
    groups, order = {}, []
    for c in rows:
        if c["id"] not in groups:
            order.append(c["id"])
        groups.setdefault(c["id"], []).append(c)
    merged, n_merged, n_tightened = [], 0, 0
    for i in order:
        copies = groups[i]
        if len(copies) == 1:
            merged.append(copies[0])
            continue
        if len({(c["code"], c["prompt"]) for c in copies}) != 1:
            raise ValueError(f"{i}: two copies with different code or prompt")
        n_merged += 1
        passing = [c for c in copies if c.get("simulate_pass") is True]
        keep = dict((passing or copies)[-1])
        held = [c for c in copies if c.get("heldout_family") not in ("none", None)]
        if keep.get("heldout_family") in ("none", None) and held:
            keep["heldout_family"], keep["assigned_by"] = held[-1]["heldout_family"], held[-1].get("assigned_by", "rule")
            n_tightened += 1
        if any(c.get("family_doubt") for c in copies) and not keep.get("family_doubt"):
            keep["family_doubt"] = True
            n_tightened += 1
        merged.append(keep)
    return merged, n_merged, n_tightened


def assemble(real, synthetic, rows, heldout_families, seed):
    """-> (candidates, report). candidates = every real row + the synthetic rows drawn."""
    real, n_merged, n_tightened = merge_duplicates(real)
    n_real = len(ft.build_manifest(real, heldout_families, 1)["included"])
    need = max(0, rows - n_real)
    eligible = [c for c in synthetic if not ft.reasons_for(c, heldout_families)]
    by_kind = defaultdict(list)
    for c in sorted(eligible, key=lambda c: c["id"]):
        by_kind[kind_of(c["id"])].append(c)
    rng = random.Random(seed)
    for kind in by_kind:
        rng.shuffle(by_kind[kind])
    chosen, kinds = [], sorted(by_kind)
    while len(chosen) < need and any(by_kind[k] for k in kinds):
        for k in kinds:
            if by_kind[k] and len(chosen) < need:
                chosen.append(by_kind[k].pop())
    total = n_real + len(chosen)
    report = {"rows_target": rows, "duplicate_ids_merged": n_merged, "labels_tightened_by_merge": n_tightened, "real_included": n_real, "synthetic_available": len(eligible), "synthetic_chosen": len(chosen),
              "total": total, "real_share": round(n_real / total, 4) if total else None,
              "synthetic_per_kind": {k: sum(1 for c in chosen if kind_of(c["id"]) == k) for k in kinds},
              "meets_rows": total >= rows}
    return list(real) + chosen, report


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--real", nargs="+", required=True)
    ap.add_argument("--synthetic", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--rows", type=int, default=ft.MIN_SFT_ROWS)
    ap.add_argument("--seed", default="s9_5-assemble")
    ap.add_argument("--families", default=str(ft.FAMILIES_FILE))
    args = ap.parse_args(argv)
    real = [c for p in args.real for c in ft.load_candidates(p)]
    synthetic = ft.load_candidates(args.synthetic)
    families = set(json.loads(Path(args.families).read_text(encoding="utf-8"))["families"])
    candidates, report = assemble(real, synthetic, args.rows, families, args.seed)
    Path(args.out).write_text("".join(json.dumps(c, ensure_ascii=False) + "\n" for c in candidates), encoding="utf-8")
    print(json.dumps(report, indent=1))
    return 0 if report["meets_rows"] else 1


if __name__ == "__main__":
    sys.exit(main())
