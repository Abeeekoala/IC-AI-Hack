#!/usr/bin/env python3
"""Resolve the family doubts left by the corpus labeller and write an audit file (README S9.5, family labels).

    python3 scripts/s9_5_label_candidates.py CANDIDATES.jsonl REPORT_DIR --out-labels validation/s9_5_labels.json \
        --out-candidates LABELLED.jsonl

CANDIDATES.jsonl and REPORT_DIR are the output of scripts/s9_5_build_corpus.py (WORK/candidates.jsonl, WORK/out). Every
candidate that simulates and is still in doubt gets a family and a reason; the rest keep the labeller's own label.
The family a candidate is given is the held-out Text2WetLab family whose procedure it performs, or "none".

These are judgements by an agent reading each protocol, not by a person: every record carries
``assigned_by: "agent-review"`` and the run guard (s9_5_finetune.assert_run_allowed) refuses a run until a person has
confirmed them. validation/s9_5_labels.json is what that person reads.

The rules, in order of use (over the candidates the labeller flagged ``family_doubt`` that pass opentrons_simulate):
  * performs the procedure of a held-out family -> that family: magnetic-bead nucleic-acid extraction or bead clean-up
    (opentrons-rna-extraction, ampure-bead-cleanup); heat-shock transformation; assembling PCR or qPCR reactions
    (master mix, primer or probe plating) as in the colony-pcr-screening task.
  * only keyword or module overlap (a module test, an enzyme assay, a chemistry dosing run, a dilution) -> none.
  * a plain transfer of up to three liquid steps with one volume, or 100 uL from one reservoir well to plate wells, has the
    shape of the a1-a12-100ul / split-200ul-two-wells tasks -> that family (kept out, because it is the same task).
When in doubt the candidate is kept out, because a wrong "none" leaks a held-out task and a wrong family loses one row.
Standard library only.
"""
import argparse
import glob
import json
import re
import sys

LIQUID = re.compile(r"(Transfer|Aspirate|Dispense|Distribute|Consolidate)")

# index into the list of doubted, simulating candidates that carry any signal besides the two plain-transfer families
# (in corpus order, the order scripts/s9_5_build_corpus.py wrote them) -> (family, reason)
EXCLUDE_MODULE_CASES = {
    36: ("ecoli-heat-shock-transformation", "heat-shock protocol (42 C step with cells and plasmid)"),
    **{i: ("opentrons-rna-extraction", "magnetic-bead nucleic-acid extraction station (bind, wash, elute)") for i in (37, 38, 39, 40, 41, 42, 43, 54, 55)},
    **{i: ("ampure-bead-cleanup", "library-prep kit protocol with magnetic-bead clean-up and PCR") for i in (50, 51, 57, 81)},
    **{i: ("colony-pcr-screening", "qPCR reaction setup: master mix and sample added to a reaction plate") for i in (44, 45, 46, 47, 48)},
    **{i: ("colony-pcr-screening", "primer or probe plating, or master-mix plating, for PCR reactions") for i in (59, 60, 61, 62, 63, 64, 65, 66, 67, 68, 70)},
    75: ("colony-pcr-screening", "master mix dispensed into PCR strip tubes"),
}
NONE_REASONS = (  # (first index, last index, reason) for the remaining module cases
    (0, 1, "generic transfers or a magnetic-module test; no held-out procedure"),
    (2, 24, "enzyme-assay additions and buffer mixes (CoaB characterisation); not PCR"),
    (25, 34, "high-throughput chemistry dosing and work-up with a heater-shaker; not a held-out procedure"),
    (35, 35, "cell-culture dilution with inducers (part characterisation); not a transformation"),
    (49, 49, "magnetic-module test; no clean-up"),
    (52, 53, "module test protocol"),
    (56, 56, "smoke test: one transfer and a wait"),
    (58, 58, "thermocycler pipetting test; no PCR"),
    (69, 69, "RNA aliquoting into a plate; no extraction"),
    (71, 74, "template, serial dilution or luminescence plate; not a held-out procedure"),
    (76, 80, "primer quantification, dilution or rehydration; no reaction assembly"),
    (82, 82, "mix step with modules loaded; no held-out procedure"),
)
# plain-transfer candidates with more than three liquid steps or several volumes that are NOT the a1-a12/split shape
TINY_KEEP_NONE = {4, 5, 11, 12, 13, 14, 15, 17, 18}  # 1-based positions in the list printed for review (see below)
TINY_SPLIT = {16}


def load(candidates_path, report_dir):
    cands = [json.loads(line) for line in open(candidates_path, encoding="utf-8") if line.strip()]
    info = {}
    for path in glob.glob(f"{report_dir}/*.report.json"):
        doc = json.load(open(path, encoding="utf-8"))
        for f in doc["files"]:
            info[doc["source"] + ":" + f["path"]] = f
    return cands, info


def liquid_shape(prompt):
    steps = re.findall(r"^\d+\. (.*)$", prompt, re.M)
    liquid = [s for s in steps if LIQUID.match(s)]
    vols = set(re.findall(r"(\d+(?:\.\d+)?) µL", " ".join(liquid)))
    return len(liquid), vols


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("candidates")
    ap.add_argument("report_dir")
    ap.add_argument("--out-labels", required=True)
    ap.add_argument("--out-candidates", required=True)
    args = ap.parse_args(argv)
    cands, info = load(args.candidates, args.report_dir)
    plain = {"a1-a12-100ul", "split-200ul-two-wells"}
    module_cases, tiny_cases = [], []
    for c in cands:
        if c["simulate_pass"] is not True or not c["family_doubt"]:
            continue
        fams = {r.split(":")[0] for r in info[c["id"]]["family_reasons"]}
        (tiny_cases if fams <= plain else module_cases).append(c)
    if len(module_cases) != 83 or len(tiny_cases) != 77:
        print(f"expected 83 module cases and 77 plain-transfer cases, found {len(module_cases)} and {len(tiny_cases)}: "
              "the rules below were written for the corpus of 2026-11-14; review them before applying", file=sys.stderr)
        return 1
    labels = {}
    for i, c in enumerate(module_cases):
        if i in EXCLUDE_MODULE_CASES:
            fam, why = EXCLUDE_MODULE_CASES[i]
        else:
            fam, why = "none", next(r for a, b, r in NONE_REASONS if a <= i <= b)
        labels[c["id"]] = (fam, why)
    reviewed = []  # the plain-transfer candidates that need a look: more than three liquid steps or several volumes
    for c in tiny_cases:
        n, vols = liquid_shape(c["prompt"])
        if n >= 4 or len(vols) >= 2:
            reviewed.append(c)
    for pos, c in enumerate(reviewed, 1):
        if pos in TINY_KEEP_NONE:
            labels[c["id"]] = ("none", "plain transfers but not the a1-a12-100ul or split-200ul shape (plate or column work, several volumes)")
        elif pos in TINY_SPLIT:
            labels[c["id"]] = ("split-200ul-two-wells", "splits one volume into several wells")
        else:
            labels[c["id"]] = ("a1-a12-100ul", "100 uL from one reservoir well to plate wells: the shape of the a1-a12-100ul task")
    for c in tiny_cases:
        labels.setdefault(c["id"], ("a1-a12-100ul", "up to three plain liquid steps with one volume: the shape of the a1-a12-100ul / split-200ul tasks"))
    out = []
    for c in cands:
        if c["id"] in labels:
            fam, why = labels[c["id"]]
            c = {**c, "heldout_family": fam, "family_doubt": False, "assigned_by": "agent-review"}
        out.append(c)
    doc = {"about": "Family labels set by an agent reading each protocol (assigned_by agent-review). A person must confirm them before a run: s9_5_finetune.assert_run_allowed refuses any included label not assigned by a person. See scripts/s9_5_label_candidates.py for the rules.",
           "labels": {k: {"heldout_family": f, "reason": r} for k, (f, r) in labels.items()}}
    json.dump(doc, open(args.out_labels, "w", encoding="utf-8"), indent=1, sort_keys=True)
    with open(args.out_candidates, "w", encoding="utf-8") as fh:
        for c in out:
            fh.write(json.dumps(c, ensure_ascii=False) + "\n")
    kept = sum(1 for k, (f, r) in labels.items() if f == "none")
    clean = sum(1 for c in out if c["simulate_pass"] is True and not c["family_doubt"] and c["heldout_family"] == "none")
    print(f"{len(labels)} doubts resolved: {kept} -> none, {len(labels) - kept} -> a held-out family; "
          f"{clean} simulating candidates are now unflagged and unassigned")
    return 0


if __name__ == "__main__":
    sys.exit(main())
