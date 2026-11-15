"""`vaxforge validate lopov|bexsero ...`: the retrospective validation tools (README §3, tiers 1 and 2). Stream D.

Each sub-command hands its arguments to that tool's own command line (a leading `--help` does not get through argparse's
"rest of the line" argument, as with `vaxforge finetune`; run `python validation/lopov.py --help` for the options):

    vaxforge validate lopov INPUT.json --k 3 5 [--out REPORT.json]        # validation/lopov.py
    vaxforge validate bexsero S3_ARTIFACT [--truth FILE] [--k 3] ...       # validation/bexsero_case.py

The tools live in validation/ of a source checkout (they are not part of the installed package), and bexsero_case imports
lopov by name, so validation/ is on the import path while a tool is loaded. cli.py registers this module through
EXTENSION_MODULES.
"""
from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

VALIDATION = Path(__file__).resolve().parents[2] / "validation"
TOOLS = {"lopov": ("lopov", "leave-one-pathogen-out splits and ranking metrics (validation/lopov.py)"),
         "bexsero": ("bexsero_case", "recall@k of the licensed Bexsero antigens in an S3 ranking (validation/bexsero_case.py)")}


def _load(module):
    if not (VALIDATION / f"{module}.py").is_file():
        raise ValueError(f"{VALIDATION / (module + '.py')} not found: this command needs a source checkout of the repository")
    sys.path.insert(0, str(VALIDATION))
    try:
        return importlib.import_module(module)
    finally:
        sys.path.remove(str(VALIDATION))


def _command(name):
    def go(args):
        try:
            tool = _load(TOOLS[name][0])
        except ValueError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
        return tool.main(args.rest) or 0
    return go


def register_cli(subparsers, common):
    p = subparsers.add_parser("validate", parents=[common], help="retrospective validation: LOPOV metrics and the Bexsero recall@k case")
    sub = p.add_subparsers(dest="validate_command", required=True)
    for name, (_, text) in TOOLS.items():
        q = sub.add_parser(name, help=text, add_help=False)
        q.add_argument("rest", nargs=argparse.REMAINDER)
        q.set_defaults(func=_command(name))
