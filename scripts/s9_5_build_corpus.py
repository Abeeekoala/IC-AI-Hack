#!/usr/bin/env python3
"""Rebuild the S9.5 candidate corpus from validation/s9_5_corpus_sources.json (README S9.5, Training data provenance).

For each source it fetches exactly the recorded commit (shallow, nothing else) into WORK/src/<owner>__<repo>, runs
src/vaxforge/s9_5_corpus.py on it, and concatenates the records into WORK/candidates.jsonl. The corpus builder parses
files with ast and never imports them; its simulate step does execute them, so give --simulator a sandboxed
opentrons_simulate (a wrapper that denies network and writes outside a scratch folder) for anything downloaded.

    python3 scripts/s9_5_build_corpus.py --work DIR --simulator PATH --avoid DIR [--avoid DIR ...] [--workers N]

--avoid takes the directories whose files must not reach the corpus: the Text2WetLab checkout's tasks/ folder and the
uncleared Opentrons/Protocols library. The family labels and the refusal flag are not set here: heldout_family comes
from the builder's conservative labeller, and every label still needs a person (README S9.5).
Standard library only.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCES = ROOT / "validation" / "s9_5_corpus_sources.json"
BUILDER = ROOT / "src" / "vaxforge" / "s9_5_corpus.py"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)


def fetch(repo, commit, dest):
    """Check out exactly commit of repo into dest (skipped when it is already there)."""
    if dest.is_dir():
        have = subprocess.run(["git", "rev-parse", "HEAD"], cwd=dest, capture_output=True, text=True).stdout.strip()
        if have == commit:
            return
        raise SystemExit(f"{dest} exists at {have or 'no commit'}, not {commit}: remove it and rerun")
    dest.mkdir(parents=True)
    git("init", "-q", cwd=dest)
    git("remote", "add", "origin", f"https://github.com/{repo}.git", cwd=dest)
    git("fetch", "-q", "--depth", "1", "origin", commit, cwd=dest)
    git("checkout", "-q", "FETCH_HEAD", cwd=dest)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--work", required=True, help="empty or previously used working directory (clones go in WORK/src)")
    ap.add_argument("--simulator", required=True, help="path to a sandboxed opentrons_simulate (7.5.0)")
    ap.add_argument("--avoid", action="append", default=[], metavar="DIR")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--sources", default=str(SOURCES))
    args = ap.parse_args(argv)
    work = Path(args.work)
    (work / "out").mkdir(parents=True, exist_ok=True)
    sources = json.loads(Path(args.sources).read_text(encoding="utf-8"))["sources"]
    total = 0
    for s in sources:
        name = s["repo"].replace("/", "__")
        out = work / "out" / f"{name}.jsonl"
        if out.exists():
            continue
        dest = work / "src" / name
        try:
            fetch(s["repo"], s["commit"], dest)
        except subprocess.CalledProcessError as e:
            print(f"{s['repo']}: fetch failed: {e.stderr.strip()[:200]}", file=sys.stderr)
            return 1
        cmd = [sys.executable, "-I", str(BUILDER), str(dest), "--source", f"{s['repo']}@{s['commit'][:7]}", "--out", str(out),
               "--report", str(work / "out" / f"{name}.report.json"), "--simulator", args.simulator,
               "--workers", str(args.workers)]
        for a in args.avoid:
            cmd += ["--avoid", a]
        done = subprocess.run(cmd, cwd=work, capture_output=True, text=True)
        if done.returncode:
            print(f"{s['repo']}: builder failed: {done.stderr.strip()[-300:]}", file=sys.stderr)
            return 1
        n = sum(1 for _ in out.open(encoding="utf-8"))
        total += n
        print(f"{s['repo']}@{s['commit'][:7]}: {n} candidates")
    with (work / "candidates.jsonl").open("w", encoding="utf-8") as fh:
        for p in sorted((work / "out").glob("*.jsonl")):
            fh.write(p.read_text(encoding="utf-8"))
    print(f"{sum(1 for _ in (work / 'candidates.jsonl').open(encoding='utf-8'))} candidates in {work / 'candidates.jsonl'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
