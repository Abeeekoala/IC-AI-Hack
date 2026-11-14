"""Bexsero retrieval case (README §3, tier 2): recall@k and rank-of-truth for the licensed vaccine's antigens.

The licensed vaccine (Bexsero/4CMenB) contains three recombinant antigens: fHbp, NadA and NHBA. Given the S3 artifact of a
run on N. meningitidis serogroup B (docs/CONTRACT_S3_S7.md, `payload.ranking`: every protein, in rank order), this reports
the rank of each antigen and recall@k over the antigens in the truth set. Nothing is run or downloaded here.

What it computes: ranks and recall on the S3 ranking only. It does not evaluate S4 survivors; CONTRACT_S3_S7.md §4 warns that
S4 with the README defaults rejects all three antigens, so recall on the survivors is a separate question. It does not check
that the run itself was done honestly: the report passes through S3's own `leakage_audit`.

The truth set is an input: a JSON file `{"fHbp": ["term", ...], ...}` (or a list of strings, each its own antigen). A term
matches a ranked protein if it equals (ignoring case) its protein id, its accession, its gene, or occurs as whole words in its
name. Without a file, DEFAULT_TRUTH is used: these are NAME patterns written from the README and the fixture's UniProt
descriptions, not database accessions, and a person must check each against the proteome before quoting a number. Every
antigen is reported as matched (exactly one protein), not_found (zero) or ambiguous (several: all listed with their ranks).
Only matched antigens count as hits; not_found and ambiguous ones stay in the denominator and are listed, never dropped.

Ties: S3 breaks equal scores by an arbitrary order (the rule-based fallback gives many proteins identical scores), so a rank
inside a run of equal (composite_score, protegenicity) is not informative. Each antigen reports its rank in S3's order and the
best and worst rank its tie group allows, and recall@k is reported for S3's order and for both extremes.

Leakage (README §3): with `--reference`, asserts that no protein of the ranked proteome, and with `--homologues` no listed
homologue, is in the reference/training id set (lopov.assert_absent; same id-namespace caveat). Without `--reference` the
report says "not checked". If the assertion fails the report is written without any recall, and the exit status is 2.

Usage: python validation/bexsero_case.py S3_ARTIFACT [--truth FILE] [--k 3] [--reference IDS] [--homologues IDS] [--out FILE]
IDS files: a .json list of strings, or one id per line.
Exit status: 0 report written, 1 bad input, 2 leakage assertion violated.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Mapping
from pathlib import Path

from lopov import id_key, leakage_assertion, read_ids

DEFAULT_TRUTH = {
    "fHbp": ["fHbp", "Factor H binding protein", "Factor H-binding protein"],
    "NadA": ["NadA", "Neisseria adhesin A"],
    "NHBA": ["NHBA", "Neisserial heparin binding antigen"],
}
DEFAULT_SOURCE = "DEFAULT_TRUTH name patterns: not verified against the proteome, a person must check each match"


def load_truth(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, list):
        data = {t: [t] for t in data}
    if not isinstance(data, Mapping) or not data:
        raise ValueError(f"{path}: expected a non-empty JSON object {{antigen: [terms]}} or list of strings")
    truth = {}
    for label, terms in data.items():
        terms = [terms] if isinstance(terms, str) else terms
        if not isinstance(terms, list) or not terms or not all(isinstance(t, str) and t.strip() for t in terms):
            raise ValueError(f"{path}: {label!r} needs a non-empty list of non-empty strings")
        truth[label] = terms
    return truth


def _matches(entry, term):
    t = term.casefold()
    pid = entry["protein_id"]
    own = {pid.casefold(), id_key(pid), str(entry.get("gene") or "").casefold(), str(entry.get("accession") or "").casefold()}
    return t in own or re.search(rf"(?<!\w){re.escape(t)}(?!\w)", str(entry.get("name") or "").casefold()) is not None


def _tie_ranges(ranking):
    """(best, worst) 1-based rank of each entry within its run of equal (composite_score, protegenicity)."""
    keys = [(e.get("composite_score"), e.get("protegenicity")) for e in ranking]
    out, i = [], 0
    while i < len(keys):
        j = i
        while j + 1 < len(keys) and keys[j + 1] == keys[i]:
            j += 1
        out += [(i + 1, j + 1)] * (j - i + 1)
        i = j + 1
    return out


def bexsero_report(s3_doc, truth=None, k=3, reference_ids=None, homologue_ids=(), truth_source=None):
    truth = DEFAULT_TRUTH if truth is None else truth
    truth_source = truth_source or DEFAULT_SOURCE
    payload = s3_doc.get("payload") if isinstance(s3_doc, Mapping) else None
    ranking = (payload or {}).get("ranking")
    if not ranking:
        raise ValueError("not an S3 artifact: no payload.ranking")
    ids = [e["protein_id"] for e in ranking]
    if len(set(ids)) != len(ids):
        raise ValueError("payload.ranking has duplicate protein ids")
    if any("rank" in e for e in ranking) and [e.get("rank") for e in ranking] != list(range(1, len(ranking) + 1)):
        raise ValueError("payload.ranking is not in rank order (its rank fields are not 1..N in list order)")
    if k < 1:
        raise ValueError(f"k must be at least 1, got {k}")

    leak = leakage_assertion(ids, reference_ids, homologue_ids)
    report = {"case": "bexsero", "k": k, "truth_source": truth_source,
              "s3": {"taxid": (payload.get("context") or {}).get("taxid"),
                     "ranker_backend": (payload.get("ranker") or {}).get("backend"), "n_ranked": len(ranking),
                     "leakage_audit": payload.get("leakage_audit")},
              "scope": "S3 ranking only; S4 survivors are not evaluated",
              "leakage_assertion": leak}
    if leak["status"] == "violated":
        report["recall_at_k"] = {"value": None, "reason": "withheld: the leakage assertion failed, so this is not a retrieval result"}
        return report

    ranges = _tie_ranges(ranking)
    antigens = []
    for label, terms in truth.items():
        hits = [i for i, e in enumerate(ranking) if any(_matches(e, t) for t in terms)]
        matches = [{"protein_id": ranking[i]["protein_id"], "gene": ranking[i].get("gene"), "name": ranking[i].get("name"),
                    "rank": i + 1, "rank_best_case": ranges[i][0], "rank_worst_case": ranges[i][1],
                    "composite_score": ranking[i].get("composite_score"), "protegenicity": ranking[i].get("protegenicity")}
                   for i in hits]
        status = {0: "not_found", 1: "matched"}.get(len(hits), "ambiguous")
        antigens.append({"antigen": label, "terms": terms, "status": status, "matches": matches,
                         "rank": matches[0]["rank"] if status == "matched" else None})
    report["antigens"] = antigens
    report["unresolved_antigens"] = [{"antigen": a["antigen"], "status": a["status"]} for a in antigens if a["status"] != "matched"]

    if k > len(ranking):
        report["recall_at_k"] = {"value": None, "reason": f"k={k} exceeds the ranked list length {len(ranking)}"}
        return report
    resolved = [a["matches"][0] for a in antigens if a["status"] == "matched"]
    n = len(antigens)

    def count(field):
        return sum(1 for m in resolved if m[field] <= k)

    report["recall_at_k"] = {"value": count("rank") / n, "reason": None, "n_hit": count("rank"), "n_truth": n,
                             "if_ties_go_best_case": count("rank_best_case") / n,
                             "if_ties_go_worst_case": count("rank_worst_case") / n}
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bexsero case: recall@k and rank-of-truth on an S3 ranking")
    ap.add_argument("s3", help="S3 artifact JSON (payload.ranking)")
    ap.add_argument("--truth", help="JSON truth set; default: DEFAULT_TRUTH name patterns, unverified")
    ap.add_argument("--k", type=int, default=3)
    ap.add_argument("--reference", help="ids the predictor was trained on; enables the leakage assertion")
    ap.add_argument("--homologues", help="ids of listed homologues (blastp E <= 1e-3) that must also be absent from it")
    ap.add_argument("--out", help="also write the report to this file")
    args = ap.parse_args(argv)
    try:
        s3 = json.loads(Path(args.s3).read_text(encoding="utf-8"))
        report = bexsero_report(
            s3, load_truth(args.truth) if args.truth else None, args.k,
            read_ids(args.reference) if args.reference else None,
            read_ids(args.homologues) if args.homologues else (), args.truth)
    except KeyError as e:
        print(f"bexsero_case: the input has no key {e}", file=sys.stderr)
        return 1
    except (ValueError, TypeError, OSError) as e:
        print(f"bexsero_case: {e}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    leak, r = report["leakage_assertion"], report["recall_at_k"]
    print(f"leakage assertion: {leak['status']}")
    if leak["status"] == "violated":
        print(f"  {leak['error']}")
        return 2
    print(f"recall@{args.k}: {r['value']}" + (f" ({r['reason']})" if r["reason"] else
          f" ({r['n_hit']}/{r['n_truth']}; ties best {r['if_ties_go_best_case']:.2f}, worst {r['if_ties_go_worst_case']:.2f})"))
    for a in report["antigens"]:
        print(f"  {a['antigen']}: {a['status']}" + (f", rank {a['rank']}" if a["rank"] else
              "".join(f"; {m['protein_id']} rank {m['rank']}" for m in a["matches"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
