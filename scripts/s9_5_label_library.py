#!/usr/bin/env python3
"""Resolve the family doubts of the Opentrons Protocols library candidates (README S9.5, family labels).

    python3 scripts/s9_5_label_library.py CANDIDATES.jsonl --out-labels validation/s9_5_labels_library.json --out-candidates LABELLED.jsonl

CANDIDATES.jsonl is the output of src/vaxforge/s9_5_corpus.py on the staged library (scripts/s9_5_stage_library.py). The
rules are those of scripts/s9_5_label_candidates.py: a candidate that performs a held-out procedure gets that family (and
is kept out), one that does not gets "none", and a plain transfer of at most four steps has the shape of the a1-a12 / split
tasks. The index of a doubted, simulating candidate below is its position in the file; the decisions were read from each
protocol's name, modules and steps on 2026-11-14 and are an agent's judgement (``assigned_by: "agent-review"``): a person
must confirm them before a run. Standard library only.
"""
import argparse
import json
import sys

GROUPS = (
    ("opentrons-rna-extraction", "magnetic-bead nucleic-acid extraction or purification kit (bind, wash, elute)",
     (0, 20, 31, 33, 36, 42, 46, 67, 68, 74, 75, 83, 84, 85, 86, 87, 88, 91, 92, 93, 94)),
    ("ampure-bead-cleanup", "magnetic-bead clean-up, size selection or bead capture (NGS clean-up, bead immunoprecipitation)",
     (7, 8, 9, 16, 22, 27, 29, 32, 43, 44, 47, 51, 53, 55, 56, 57, 58, 59, 60, 61, 72, 76, 77, 78, 79, 80, 89, 90)),
    ("colony-pcr-screening", "assembles PCR, cDNA or library-prep reactions (master mix, primers or indexes, template) for thermocycling",
     (2, 3, 4, 5, 6, 10, 11, 13, 14, 15, 17, 18, 19, 21, 23, 24, 25, 26, 28, 30, 41, 45, 49, 50, 52, 54)),
    ("ecoli-heat-shock-transformation", "bacterial plating with a temperature step; treated as the transformation procedure when in doubt", (66,)),
    ("a1-a12-100ul", "at most four plain steps: the shape of the a1-a12-100ul / split-200ul tasks", (1, 12, 48, 62, 63, 64, 65, 69, 70, 71, 81)),
    ("none", "does not perform a held-out procedure (an extension assay, culture, toxicity assay, encapsulation, a module test, protein digestion)",
     (34, 35, 37, 38, 39, 40, 73, 82, 95)),
)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("candidates")
    ap.add_argument("--out-labels", required=True)
    ap.add_argument("--out-candidates", required=True)
    args = ap.parse_args(argv)
    cands = [json.loads(line) for line in open(args.candidates, encoding="utf-8") if line.strip()]
    doubted = [c for c in cands if c["simulate_pass"] is True and c["family_doubt"]]
    decided = [i for _, _, idx in GROUPS for i in idx]
    if len(doubted) != 96 or sorted(decided) != list(range(96)):
        print(f"expected 96 doubted simulating candidates, each decided once; found {len(doubted)} and {len(decided)} decisions: "
              "these rules were written for the library corpus of 2026-11-14", file=sys.stderr)
        return 1
    labels = {}
    for family, reason, idx in GROUPS:
        for i in idx:
            labels[doubted[i]["id"]] = {"heldout_family": family, "reason": reason}
    out = [{**c, "heldout_family": labels[c["id"]]["heldout_family"], "family_doubt": False, "assigned_by": "agent-review"}
           if c["id"] in labels else c for c in cands]
    json.dump({"about": "Family labels set by an agent reading each library protocol (assigned_by agent-review); a person must confirm them. Rules: scripts/s9_5_label_library.py.",
               "labels": labels}, open(args.out_labels, "w", encoding="utf-8"), indent=1, sort_keys=True)
    with open(args.out_candidates, "w", encoding="utf-8") as fh:
        for c in out:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    clean = sum(1 for c in out if c["simulate_pass"] is True and not c["family_doubt"] and c["heldout_family"] == "none")
    print(f"{len(labels)} doubts resolved: {sum(1 for v in labels.values() if v['heldout_family'] == 'none')} -> none; {clean} simulating library candidates are now unflagged")
    return 0


if __name__ == "__main__":
    sys.exit(main())
