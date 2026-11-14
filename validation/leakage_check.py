"""Adversarial leakage check for the S9.5 training manifest (README §3 and §S9.5).

It asserts that the holdout actually held out, and it does not trust the labels it is given:

  1. Manifest rules. No included pair comes from an excluded source or carries a held-out family label, none is
     also in the refusal probe, and every included pair's text still matches the hash the manifest recorded.
  2. Content. No included prompt or code is a near-duplicate of any held-out text (task instructions and
     reference solutions), measured by word 5-gram shingle overlap against the smaller of the two texts.

Usage: python validation/leakage_check.py MANIFEST CANDIDATES --heldout-dir DIR [--threshold 0.5]
Exit status: 0 clean, 1 bad input, 2 violations found.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

K = 5               # words per shingle
MIN_SHINGLES = 10   # shorter texts are skipped and listed: their overlap ratios say nothing
THRESHOLD = 0.5
TEXT_SUFFIXES = {".md", ".py", ".txt", ".json", ".toml", ".yaml", ".yml"}
# Kept apart from src/vaxforge/s9_5_finetune.py on purpose: this check must still catch that module's mistakes.
EXCLUDED_SOURCES = {"protocols.io", "text2wetlab"}


def shingles(text):
    words = re.findall(r"\w+", text.lower())
    if len(words) < K:
        return {tuple(words)} if words else set()
    return {tuple(words[i:i + K]) for i in range(len(words) - K + 1)}


def similarity(a, b):
    """How much of the smaller text sits inside the other: the larger of the two containments."""
    return len(a & b) / min(len(a), len(b))


def load_heldout_texts(directory):
    root = Path(directory)
    out = {}
    for p in sorted(root.rglob("*")):
        if p.is_file() and not p.is_symlink() and p.suffix.lower() in TEXT_SUFFIXES:
            out[str(p.relative_to(root))] = p.read_text(encoding="utf-8", errors="replace")
    return out


def check(manifest, candidates, heldout_texts, threshold=THRESHOLD):
    held, skipped = {}, []
    for path, text in sorted(heldout_texts.items()):
        s = shingles(text)
        if len(s) < MIN_SHINGLES:
            skipped.append(path)
        else:
            held[path] = s
    if not held:
        raise ValueError("no held-out text is long enough to compare against; refusing to report a clean result")

    by_id = {c["id"]: c for c in candidates}
    probe = set(manifest.get("dropped_for_refusal", []))
    violations = []
    for e in manifest["included"]:
        i = e["id"]
        if e["source"] in EXCLUDED_SOURCES:
            violations.append(f"{i}: source {e['source']!r} is excluded from training")
        if e.get("heldout_family") != "none":
            violations.append(f"{i}: family label is {e.get('heldout_family')!r}, not 'none'")
        if i in probe:
            violations.append(f"{i}: is in the training set and in the refusal probe")
        c = by_id.get(i)
        if c is None:
            violations.append(f"{i}: not in the candidates file")
            continue
        for field, key in (("prompt", "prompt_sha256"), ("code", "code_sha256")):
            if hashlib.sha256(c[field].encode("utf-8")).hexdigest() != e[key]:
                violations.append(f"{i}: {field} changed since the manifest was built")
            cs = shingles(c[field])
            if len(cs) < MIN_SHINGLES:
                continue
            for path, hs in held.items():
                sim = similarity(cs, hs)
                if sim >= threshold:
                    violations.append(f"{i}: {field} overlaps held-out {path} ({sim:.0%})")
    return {"checked": len(manifest["included"]), "heldout_texts": len(held), "threshold": threshold,
            "skipped_short_heldout": skipped, "violations": violations}


def main(argv=None):
    ap = argparse.ArgumentParser(description="Adversarial leakage check for the S9.5 training manifest")
    ap.add_argument("manifest", help="manifest JSON from s9_5_finetune.py")
    ap.add_argument("candidates", help="candidate pairs JSONL the manifest was built from")
    ap.add_argument("--heldout-dir", required=True, help="directory of held-out task texts and reference solutions")
    ap.add_argument("--threshold", type=float, default=THRESHOLD)
    ap.add_argument("--out", help="also write the report to this file")
    args = ap.parse_args(argv)
    try:
        manifest = json.loads(Path(args.manifest).read_text(encoding="utf-8"))
        candidates = [json.loads(line) for line in Path(args.candidates).read_text(encoding="utf-8").splitlines()
                      if line.strip()]
        report = check(manifest, candidates, load_heldout_texts(args.heldout_dir), args.threshold)
    except (ValueError, KeyError, OSError) as e:
        print(f"leakage_check: {e}", file=sys.stderr)
        return 1
    if args.out:
        Path(args.out).write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    v = report["violations"]
    print(f"checked {report['checked']} pairs against {report['heldout_texts']} held-out texts: "
          f"{len(v)} violation(s); {len(report['skipped_short_heldout'])} short held-out text(s) skipped")
    for line in v[:50]:
        print("  " + line)
    return 2 if v else 0


if __name__ == "__main__":
    sys.exit(main())
