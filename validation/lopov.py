"""Retrospective validation metrics and leave-one-pathogen-out (LOPOV) splits (README §3, tier 1).

What this computes: ranking metrics over a ranked list and a set of positives, LOPOV train/test id splits, and a
leakage assertion over id sets. What it does not do: run BLAST (the homologue list at blastp E <= 1e-3 is taken from the
caller), train or run any model (per-fold scores are taken from the caller), choose or filter negatives (README tier 1's
"<30% identity" rule is the caller's job: the grouping you pass is the universe), or download anything.

Metric conventions (all of them, so nothing is left to a reader's guess):
  * A ranking is a list of unique ids, best first. Position is rank; ties in the underlying score must be resolved by the
    caller, and the numbers depend on how. (`lopov_report` resolves them pessimistically, see below.)
  * Positives must all be in the ranked list (the list is the closed universe being evaluated); otherwise ValueError.
  * No positives, or k larger than the list: the value is None with a reason, never 0. k < 1 is a ValueError.
  * precision@k   = positives in the top k / k.
  * recall@k      = positives in the top k / all positives.
  * fold enrichment@k = precision@k / (positives / list length), i.e. recall@k / (k / list length). This is the
    "fraction of positives found / fraction of proteome selected" ratio the README quotes, taken at a top-k cut.
  * PR-AUC        = average precision: sum over each positive of precision at its rank, divided by the number of
    positives. Step-wise, no interpolation. It is NOT the trapezoid area under the PR curve, which is optimistic.
  * rank-of-truth = {positive id: 1-based rank}.

Ids are compared as exact strings, except in the leakage assertion, which also treats UniProt headers `sp|ACC|NAME` and
`tr|ACC|NAME` as their accession and ignores case. It cannot map between namespaces (Protegen's RefSeq ids are not UniProt
accessions): ids from two namespaces never collide, so a pass proves nothing unless both sets use one namespace.

Usage: python validation/lopov.py INPUT.json --k K [K ...] [--out REPORT.json]
INPUT.json: {"proteins": {pathogen: [ids]}, "positives": [ids],
             "homologues": {pathogen: [ids]} or [ids]      (optional: removed from train for that/every fold),
             "scores": {pathogen: {id: score}}              (optional: from a model trained on that fold's train split),
             "train_ids_used": {pathogen: [ids]}}           (optional: what the model was really trained on, asserted)
Scores that tie are resolved against the model: positives rank after negatives with the same score.
Choose k before you look at results (README S11).
Exit status: 0 ok, 1 bad input, 2 leakage assertion violated (the report is still written).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import NamedTuple

_UNIPROT = re.compile(r"^(?:sp|tr)\|([^|]+)\|")


# ----------------------------------------------------------------------------------------------- metrics
def _metric(value, reason=None):
    return {"value": value, "reason": reason}


def _check(ranked, positives, k=None):
    ranked, pos = list(ranked), set(positives)
    if len(set(ranked)) != len(ranked):
        raise ValueError("the ranked list has duplicate ids")
    missing = sorted(pos - set(ranked))
    if missing:
        raise ValueError(f"{len(missing)} positive(s) are not in the ranked list, e.g. {missing[:3]}")
    if k is not None and k < 1:
        raise ValueError(f"k must be at least 1, got {k}")
    return ranked, pos


def _undefined(ranked, pos, k=None):
    if not pos:
        return "no positives in this set"
    if k is not None and k > len(ranked):
        return f"k={k} exceeds the ranked list length {len(ranked)}"
    return None


def precision_at_k(ranked, positives, k):
    ranked, pos = _check(ranked, positives, k)
    why = _undefined(ranked, pos, k)
    return _metric(None, why) if why else _metric(sum(x in pos for x in ranked[:k]) / k)


def recall_at_k(ranked, positives, k):
    ranked, pos = _check(ranked, positives, k)
    why = _undefined(ranked, pos, k)
    return _metric(None, why) if why else _metric(sum(x in pos for x in ranked[:k]) / len(pos))


def fold_enrichment_at_k(ranked, positives, k):
    ranked, pos = _check(ranked, positives, k)
    why = _undefined(ranked, pos, k)
    if why:
        return _metric(None, why)
    return _metric((sum(x in pos for x in ranked[:k]) / k) / (len(pos) / len(ranked)))


def pr_auc(ranked, positives):
    """Average precision (see the module docstring); the estimator is reported as "average_precision"."""
    ranked, pos = _check(ranked, positives)
    why = _undefined(ranked, pos)
    if why:
        return _metric(None, why)
    hits, total = 0, 0.0
    for i, x in enumerate(ranked, 1):
        if x in pos:
            hits += 1
            total += hits / i
    return _metric(total / len(pos))


def rank_of_truth(ranked, positives):
    ranked, pos = _check(ranked, positives)
    why = _undefined(ranked, pos)
    return _metric(None, why) if why else _metric({x: i for i, x in enumerate(ranked, 1) if x in pos})


def metrics(ranked, positives, ks):
    ranked = list(ranked)
    pos = set(positives)
    per_k = {"precision_at_k": precision_at_k, "recall_at_k": recall_at_k, "fold_enrichment_at_k": fold_enrichment_at_k}
    out = {"n_ranked": len(ranked), "n_positives": len(pos)}
    for name, fn in per_k.items():
        out[name] = {str(k): fn(ranked, pos, k) for k in ks}
    out["pr_auc"] = pr_auc(ranked, pos)
    out["rank_of_truth"] = rank_of_truth(ranked, pos)
    return out


# ----------------------------------------------------------------------------------------------- leakage
class LeakageError(ValueError):
    """Ids that must be absent from the reference/training set are in it."""

    def __init__(self, pathogen_ids, homologue_ids):
        self.pathogen_ids, self.homologue_ids = list(pathogen_ids), list(homologue_ids)
        parts = []
        if self.pathogen_ids:
            parts.append(f"{len(self.pathogen_ids)} id(s) of the pathogen under test are in the reference set, "
                         f"e.g. {self.pathogen_ids[:10]}")
        if self.homologue_ids:
            parts.append(f"{len(self.homologue_ids)} listed homologue(s) are in the reference set, "
                         f"e.g. {self.homologue_ids[:10]}")
        super().__init__("; ".join(parts))


def id_key(i):
    m = _UNIPROT.match(i)
    return (m.group(1) if m else i).casefold()


def assert_absent(pathogen_ids, reference_ids, homologue_ids=()):
    """Raise LeakageError if any id of the pathogen under test, or any listed homologue, is in the reference set.

    Refuses (ValueError) to pass on an empty reference set or an empty pathogen set: nothing was compared.
    Returns the sizes it compared on a pass. See the module docstring for how ids are compared.
    """
    ref = {id_key(i) for i in reference_ids}
    path, homs = sorted(set(pathogen_ids)), sorted(set(homologue_ids))
    if not ref:
        raise ValueError("the reference set is empty: nothing to check, refusing to report a pass")
    if not path:
        raise ValueError("the pathogen under test has no ids: nothing to check, refusing to report a pass")
    bad_p = [i for i in path if id_key(i) in ref]
    bad_h = [i for i in homs if id_key(i) in ref]
    if bad_p or bad_h:
        raise LeakageError(bad_p, bad_h)
    return {"n_pathogen_ids": len(path), "n_homologue_ids": len(homs), "n_reference_ids": len(ref)}


def leakage_assertion(pathogen_ids, reference_ids=None, homologue_ids=()):
    """The assertion as a report field. `reference_ids=None` is "not checked", never a pass."""
    if reference_ids is None:
        return {"run": False, "passed": None, "status": "not checked",
                "reason": "no reference/training set was supplied, so nothing shows the pathogen was held out"}
    try:
        sizes = assert_absent(pathogen_ids, reference_ids, homologue_ids)
    except LeakageError as e:
        return {"run": True, "passed": False, "status": "violated", "error": str(e),
                "pathogen_ids_in_reference": e.pathogen_ids, "homologue_ids_in_reference": e.homologue_ids}
    return {"run": True, "passed": True, "status": "passed", **sizes}


# ----------------------------------------------------------------------------------------------- LOPOV
class Fold(NamedTuple):
    pathogen: str
    train: set
    test: set
    homologues: set          # ids the caller asked to keep out of train for this fold
    homologues_removed: int  # how many of them were actually in another pathogen's ids


def lopov_splits(by_pathogen, excluded=None):
    """Yield a Fold per pathogen, in sorted pathogen order: the held-out pathogen entirely absent from train.

    train = every other pathogen's ids, minus the held-out pathogen's ids (an id shared with another pathogen is removed
    from train too), minus the ids in `excluded`: a mapping pathogen -> ids (the homologues of that pathogen's proteins
    at blastp E <= 1e-3, computed by the caller) or one collection applied to every fold. Before yielding, the fold is
    asserted (assert_absent), not assumed. Needs at least two pathogens.
    """
    sets = {p: set(ids) for p, ids in by_pathogen.items()}
    if len(sets) < 2:
        raise ValueError("LOPOV needs at least two pathogens: with one, train would be empty")
    if not isinstance(excluded, Mapping):
        excluded = {p: excluded or () for p in sets}
    for p in sorted(sets):
        test, homs = sets[p], set(excluded.get(p, ()))
        if not test:
            raise ValueError(f"pathogen {p!r} has no proteins")
        test_keys, hom_keys = {id_key(i) for i in test}, {id_key(i) for i in homs}
        others = set().union(*(s for q, s in sets.items() if q != p))
        train = {i for i in others if id_key(i) not in test_keys | hom_keys}
        removed = sum(1 for i in others if id_key(i) in hom_keys and id_key(i) not in test_keys)
        assert_absent(test, train, homs)
        yield Fold(p, train, test, homs, removed)


def _rank_by_score(test, scores, pos):
    missing = [i for i in test if i not in scores]
    if missing:
        raise ValueError(f"{len(missing)} held-out id(s) have no score, e.g. {sorted(missing)[:3]}")
    s = {i: float(scores[i]) for i in test}
    if not all(math.isfinite(v) for v in s.values()):
        raise ValueError("scores must be finite numbers")
    return sorted(test, key=lambda i: (-s[i], i in pos, i))   # a tie never helps the model


def _macro(ms, ks):
    def avg(values):
        v = [x for x in values if x is not None]
        return {"mean": sum(v) / len(v) if v else None, "n": len(v)}
    out = {name: {str(k): avg(m[name][str(k)]["value"] for m in ms) for k in ks}
           for name in ("precision_at_k", "recall_at_k", "fold_enrichment_at_k")}
    out["pr_auc"] = avg(m["pr_auc"]["value"] for m in ms)
    return out


def lopov_report(by_pathogen, positives, ks, scores=None, excluded=None, train_used=None):
    """Per-pathogen splits, leakage assertions and metrics, and a summary.

    `positives` is one flat collection of ids; each pathogen's positives are those of its ids it contains.
    `scores[pathogen]` maps every held-out id to a score from a model trained on that fold's train split (without it
    the pathogen has splits but metrics are None). `train_used[pathogen]` is the id set that model was really trained
    on, asserted against the held-out pathogen and its homologues; without it that assertion is "not checked", and the
    only assertion run is on the split built here. The summary averages a metric only over pathogens where it is
    defined and reports how many that was; pathogens with no positives are listed, not averaged in as zero.
    """
    positives, scores, train_used = set(positives), scores or {}, train_used or {}
    per, with_metrics = {}, []
    for f in lopov_splits(by_pathogen, excluded):
        pos = {i for i in f.test if i in positives}
        leak = leakage_assertion(f.test, train_used.get(f.pathogen), f.homologues)
        entry = {"n_train": len(f.train), "n_test": len(f.test), "n_positives": len(pos),
                 "homologues_listed": len(f.homologues), "homologues_removed_from_train": f.homologues_removed,
                 "split_assertion": "passed", "training_set_assertion": leak, "metrics": None}
        if leak["status"] == "violated":
            entry["metrics_reason"] = "withheld: the training set contains the held-out pathogen or its homologues"
        elif f.pathogen not in scores:
            entry["metrics_reason"] = "no scores supplied for this pathogen"
        else:
            entry["metrics"] = metrics(_rank_by_score(f.test, scores[f.pathogen], pos), pos, ks)
            with_metrics.append(entry["metrics"])
        per[f.pathogen] = entry
    summary = {
        "n_pathogens": len(per),
        "pathogens_without_positives": sorted(p for p, e in per.items() if e["n_positives"] == 0),
        "pathogens_without_metrics": sorted(p for p, e in per.items() if e["metrics"] is None),
        "pathogens_violating_leakage": sorted(p for p, e in per.items() if e["training_set_assertion"]["status"] == "violated"),
        "positives_in_no_pathogen": len(positives - set().union(*(set(v) for v in by_pathogen.values()))),
        "macro_average": _macro(with_metrics, ks),
        "macro_average_note": "over pathogens with positives and defined values only; n is how many contributed",
    }
    return {"ks": sorted(ks), "pr_auc_estimator": "average_precision",
            "score_ties": "positives ranked after negatives with the same score", "per_pathogen": per, "summary": summary}


# ----------------------------------------------------------------------------------------------- CLI
def read_ids(path):
    """Ids from a .json list of strings, or a text file with one id per line (blank lines and # comments skipped)."""
    text = Path(path).read_text(encoding="utf-8")
    if Path(path).suffix.lower() == ".json":
        ids = json.loads(text)
        if not isinstance(ids, list) or not all(isinstance(i, str) for i in ids):
            raise ValueError(f"{path}: expected a JSON list of id strings")
        return ids
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.lstrip().startswith("#")]


def main(argv=None):
    ap = argparse.ArgumentParser(description="Leave-one-pathogen-out splits, leakage assertion and metrics")
    ap.add_argument("input", help="JSON: proteins by pathogen, positives, and optionally homologues, scores, train_ids_used")
    ap.add_argument("--k", type=int, nargs="+", required=True, help="cutoffs for precision/recall/fold enrichment")
    ap.add_argument("--out", help="also write the report to this file")
    args = ap.parse_args(argv)
    try:
        d = json.loads(Path(args.input).read_text(encoding="utf-8"))
        report = lopov_report(d["proteins"], d["positives"], args.k, d.get("scores"), d.get("homologues"),
                              d.get("train_ids_used"))
    except KeyError as e:
        print(f"lopov: the input has no key {e}", file=sys.stderr)
        return 1
    except (ValueError, TypeError, OSError) as e:
        print(f"lopov: {e}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    s = report["summary"]
    print(f"{s['n_pathogens']} pathogens; {len(s['pathogens_without_positives'])} without positives, "
          f"{len(s['pathogens_without_metrics'])} without metrics, "
          f"{len(s['pathogens_violating_leakage'])} violating the leakage assertion")
    for p in s["pathogens_violating_leakage"]:
        print(f"  {p}: {report['per_pathogen'][p]['training_set_assertion']['error']}")
    return 2 if s["pathogens_violating_leakage"] else 0


if __name__ == "__main__":
    sys.exit(main())
