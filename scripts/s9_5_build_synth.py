#!/usr/bin/env python3
"""Write the synthetic S9.5 candidate rows and simulate each one (README S9.5).

    python3 scripts/s9_5_build_synth.py --seed SEED --count N --simulator PATH --out candidates_synth.jsonl [--workers 3]

Rows come from src/vaxforge/s9_5_synth.py (deterministic for a seed). Each is run through opentrons_simulate 7.5.0 via
``--simulator`` and its ``simulate_pass`` is the real result; a row that fails stays in the file as failed, so the
manifest can count and exclude it. Standard library only.
"""
import argparse
import json
import sys
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from vaxforge import s9_5_synth  # noqa: E402
from vaxforge.s9_5_corpus import simulate_file  # noqa: E402


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--seed", required=True)
    ap.add_argument("--count", type=int, required=True)
    ap.add_argument("--simulator", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--workers", type=int, default=3)
    args = ap.parse_args(argv)
    rows, rejected = s9_5_synth.generate(args.seed, args.count)
    with tempfile.TemporaryDirectory() as tmp:
        def simulate(i_row):
            i, row = i_row
            path = Path(tmp) / f"row{i}.py"
            path.write_text(row["code"], encoding="utf-8")
            row["simulate_pass"] = bool(simulate_file(path, simulator=args.simulator)["passed"])
            return row
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            rows = list(pool.map(simulate, enumerate(rows)))
    with open(args.out, "w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    passed = sum(r["simulate_pass"] for r in rows)
    print(f"{len(rows)} rows written ({rejected} indices skipped by the family labeller or as duplicates); "
          f"{passed} pass opentrons_simulate, {len(rows) - passed} fail")
    return 0


if __name__ == "__main__":
    sys.exit(main())
