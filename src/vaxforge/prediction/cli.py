"""Tiny shared CLI for the five stage modules (``python -m vaxforge.s3_rank --run-dir runs/x``)."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Callable, Sequence

from .config import load_config
from .errors import VaxForgeError


def _target(run_dir: Path, cfg: dict):
    """A run directory created by Stream A's pipeline (it has ``manifest.json``) must be written through their ``RunStore``
    (envelope, digest, manifest, ``vaxforge verify``); a plain directory gets stream B's own envelope."""
    if (Path(run_dir) / "manifest.json").exists():
        try:
            from vaxforge.artifacts import RunStore              # Stream A's module; absent when stream B runs standalone
        except ImportError:
            return run_dir
        return RunStore(run_dir, cache_dir=cfg["refdata"]["cache_dir"])
    return run_dir


def stage_main(stage: str, run_fn: Callable[..., Path], argv: Sequence[str] | None = None,
               extra_args: Callable[[argparse.ArgumentParser], None] | None = None) -> int:
    ap = argparse.ArgumentParser(prog=f"python -m vaxforge.{stage}", description=f"Run stage {stage}")
    ap.add_argument("--run-dir", required=True, type=Path, help="runs/<run_id> directory (artifacts are read/written here)")
    ap.add_argument("--config", type=Path, default=None, help="config.yaml (defaults apply if omitted)")
    ap.add_argument("--set", action="append", default=[], metavar="KEY=JSON",
                    help="override a config value, e.g. --set s3.backend='\"scores_file\"' (JSON-encoded value)")
    ap.add_argument("--force", action="store_true", help="recompute even if an identical artifact exists")
    if extra_args:
        extra_args(ap)
    args = ap.parse_args(argv)
    overrides: dict = {}
    for item in args.set:
        key, _, raw = item.partition("=")
        node = overrides
        parts = key.split(".")
        for p in parts[:-1]:
            node = node.setdefault(p, {})
        node[parts[-1]] = json.loads(raw)
    try:
        cfg = load_config(args.config, overrides)
        kwargs = {k: v for k, v in vars(args).items() if k not in ("run_dir", "config", "set", "force")}
        out = run_fn(_target(args.run_dir, cfg), cfg, force=args.force, **kwargs)
    except VaxForgeError as e:
        print(f"{stage}: error: {e}", file=sys.stderr)
        return 2
    print(out)
    return 0
