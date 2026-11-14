#!/usr/bin/env python3
"""S9.5 training corpus builder: protocol files -> prompt/code candidate records.

Walks a directory of Opentrons Python protocols and emits one candidate record per
independent file, in the shape the S9.5 manifest builder reads:

    {"id", "source", "prompt", "code", "heldout_family", "family_doubt",
     "refused_by_base", "simulate_pass"}

What it does, in order, per file:
  1. parse with ``ast`` (files are never imported or executed here);
  2. keep OT-2 protocols only (a ``robotType`` of Flex/OT-3 is skipped);
  3. drop exact/normalised duplicates and files with no liquid handling;
  4. build a deterministic, model-free prompt from the AST (deck, pipettes, modules,
     ordered steps) plus the human description when the file has one;
  5. label held-out families conservatively: anything ambiguous sets ``family_doubt``;
  6. optionally run ``opentrons_simulate`` in a scrubbed-environment subprocess.

``refused_by_base`` is always null: the refusal filter runs later, in the manifest
builder. Nothing here calls a model or a network. Standard library only.

    python src/vaxforge/s9_5_corpus.py DIR --source owner/repo@sha --out candidates.jsonl \\
        [--report report.json] [--simulator PATH|auto|none] [--exclude GLOB ...] [--min-steps N] \\
        [--avoid DIR ...]

Run it only on directories whose licence you have read. The simulator step executes the
protocol files, so point ``--simulator`` at a sandboxed ``opentrons_simulate`` for downloads.

Licence and family labels are inputs to a person's decision, not the decision: S9.5
assigns families by hand and removes anything in doubt (README, S9.5).
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import dataclasses
import fnmatch
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import tokenize
import warnings
from pathlib import Path
from typing import Callable, Iterable, Iterator, Optional, Sequence

RECORD_KEYS = ("id", "source", "prompt", "code", "heldout_family", "family_doubt", "refused_by_base", "simulate_pass")

# Text2WetLab's 7 task families, named by the directory of the easy task (11 tasks, 7 families).
HELDOUT_FAMILIES = (
    "colony-pcr-screening",
    "ecoli-heat-shock-transformation",
    "golden-gate-assembly",
    "opentrons-rna-extraction",
    "ampure-bead-cleanup",
    "a1-a12-100ul",
    "split-200ul-two-wells",
)
NO_FAMILY = "none"

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "site-packages"}
MAX_FILE_BYTES = 1_000_000

# metadata["source"] values that mark code as derived from material that is not licence-cleared
# (the Opentrons protocol library, Opentrons custom-protocol work) or as model output.
PROVENANCE_BLOCKLIST = ("protocol library", "custom protocol request", "opentronsai")

_LIQUID = ("transfer", "distribute", "consolidate", "aspirate", "dispense", "mix")
_MODULE_CALLS = {
    "set_temperature", "set_block_temperature", "set_lid_temperature", "start_set_temperature",
    "set_and_wait_for_temperature", "set_and_wait_for_shake_speed", "deactivate", "deactivate_block",
    "deactivate_lid", "open_lid", "close_lid", "execute_profile", "engage", "disengage",
    "open_labware_latch", "close_labware_latch",
}
_TEMP_CALLS = {"set_temperature", "set_block_temperature", "set_lid_temperature", "start_set_temperature", "set_and_wait_for_temperature"}
_LOADERS = {"load_labware", "load_module", "load_instrument", "load_adapter"}
_UNRESOLVED = object()


# ---------------------------------------------------------------------------
# small AST helpers
# ---------------------------------------------------------------------------

def _short(text: str, n: int = 60) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= n else text[: n - 3] + "..."


def _fmt(value) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _const(node, consts: dict):
    """Evaluate literals, known constants and plain arithmetic; _UNRESOLVED otherwise."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return consts.get(node.id, _UNRESOLVED)
    if isinstance(node, (ast.List, ast.Tuple)):
        vals = [_const(e, consts) for e in node.elts]
        return _UNRESOLVED if any(v is _UNRESOLVED for v in vals) else vals
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.USub, ast.UAdd)):
        v = _const(node.operand, consts)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return -v if isinstance(node.op, ast.USub) else v
        return _UNRESOLVED
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
        a, b = _const(node.left, consts), _const(node.right, consts)
        if all(isinstance(x, (int, float)) and not isinstance(x, bool) for x in (a, b)):
            if isinstance(node.op, ast.Add):
                return a + b
            if isinstance(node.op, ast.Sub):
                return a - b
            if isinstance(node.op, ast.Mult):
                return a * b
            return a / b if b else _UNRESOLVED
    return _UNRESOLVED


def _dict_literal(node) -> Optional[dict]:
    """Keys of a metadata/requirements dict with constant keys; non-constant values map to None."""
    if not isinstance(node, ast.Dict):
        return None
    out = {}
    for k, v in zip(node.keys, node.values):
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            out[k.value] = v.value if isinstance(v, ast.Constant) else None
    return out


def _root_name(node) -> Optional[str]:
    while isinstance(node, (ast.Attribute, ast.Subscript, ast.Call)):
        node = node.value if not isinstance(node, ast.Call) else node.func
    return node.id if isinstance(node, ast.Name) else None


def _calls_in_order(node) -> list:
    calls = [n for n in ast.walk(node) if isinstance(n, ast.Call)]
    return sorted(calls, key=lambda n: (n.lineno, n.col_offset))


def _parse(text: str) -> ast.Module:
    """ast.parse without the SyntaxWarnings that untrusted sources trigger (invalid escapes)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        return ast.parse(text)


# ---------------------------------------------------------------------------
# AST -> structured spec
# ---------------------------------------------------------------------------

class _Walker:
    """Collects deck setup and ordered steps from a ``run`` body without executing it."""

    def __init__(self, consts: dict):
        self.consts = dict(consts)
        self.labware: list = []
        self.modules: list = []
        self.pipettes: list = []
        self.steps: list = []
        self.methods: set = set()
        self.module_vars: dict = {}
        self.temps: list = []

    # -- value rendering ---------------------------------------------------
    def say(self, node) -> Optional[str]:
        if node is None:
            return None
        v = _const(node, self.consts)
        if isinstance(v, (int, float, str)) and not isinstance(v, bool):
            return _fmt(v)
        if isinstance(v, list):
            return ", ".join(_fmt(x) for x in v[:8]) + (", ..." if len(v) > 8 else "")
        return _short(ast.unparse(node))

    def loc(self, node) -> Optional[str]:
        if node is None:
            return None
        v = _const(node, self.consts)
        if isinstance(v, (int, float, str)) and not isinstance(v, bool):
            return _fmt(v)
        if isinstance(node, ast.Subscript):
            key = _const(node.slice, self.consts)
            base = node.value
            if isinstance(base, ast.Name) and isinstance(key, str):
                return f"{key} of {base.id}"
            if isinstance(base, ast.Call) and isinstance(base.func, ast.Attribute):
                owner, m = _root_name(base.func.value) or "labware", base.func.attr
                if isinstance(node.slice, ast.Slice):
                    return f"{m} [{_short(ast.unparse(node.slice), 20)}] of {owner}"
                if isinstance(key, int) and m == "rows" and 0 <= key < 26:
                    return f"row {chr(65 + key)} of {owner}"
                if isinstance(key, int) and m == "columns":
                    return f"column {key + 1} of {owner}"
                if isinstance(key, int) and m == "wells":
                    return f"well number {key + 1} of {owner}"
                if isinstance(key, str) and m.endswith("_by_name"):
                    return f"{m.split('_')[0][:-1]} {key} of {owner}"
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            m, owner = node.func.attr, _root_name(node.func.value) or "labware"
            if m in ("top", "bottom", "center"):
                return f"{m} of {self.loc(node.func.value)}"
            if m in ("wells", "rows", "columns") and not node.args:
                return f"all {m} of {owner}"
            if m == "wells" and node.args:
                return f"wells {self.say(ast.List(elts=node.args, ctx=ast.Load()))} of {owner}"
        if isinstance(node, (ast.List, ast.Tuple)):
            parts = [self.loc(e) for e in node.elts[:6]]
            return "[" + "; ".join(parts) + ("; ..." if len(node.elts) > 6 else "") + "]"
        return _short(ast.unparse(node))

    # -- traversal -----------------------------------------------------------
    def body(self, stmts, loops: tuple) -> None:
        for s in stmts:
            self.stmt(s, loops)

    def stmt(self, s, loops: tuple) -> None:
        if isinstance(s, (ast.For, ast.AsyncFor)):
            ctx = _short(f"for {ast.unparse(s.target)} in {ast.unparse(s.iter)}", 70)
            self.body(s.body, loops + (ctx,))
            self.body(s.orelse, loops)
        elif isinstance(s, ast.While):
            self.body(s.body, loops + (_short("while " + ast.unparse(s.test), 70),))
        elif isinstance(s, ast.If):
            self.body(s.body, loops + (_short("if " + ast.unparse(s.test), 70),))
            self.body(s.orelse, loops + ("otherwise",))
        elif isinstance(s, (ast.With, ast.AsyncWith)):
            self.body(s.body, loops)
        elif isinstance(s, ast.Try):
            for part in (s.body, s.orelse, s.finalbody, *[h.body for h in s.handlers]):
                self.body(part, loops)
        elif isinstance(s, (ast.FunctionDef, ast.AsyncFunctionDef)):
            self.body(s.body, loops)
        elif isinstance(s, ast.Assign):
            self.assign(s, loops)
        elif isinstance(s, (ast.Expr, ast.AugAssign, ast.Return, ast.AnnAssign)):
            for c in _calls_in_order(s):
                self.call(c, None, loops)

    def assign(self, s: ast.Assign, loops: tuple) -> None:
        target = s.targets[0].id if len(s.targets) == 1 and isinstance(s.targets[0], ast.Name) else None
        value = s.value
        comp_calls = [c for c in _calls_in_order(value) if isinstance(c.func, ast.Attribute) and c.func.attr in _LOADERS]
        if target and not comp_calls:
            v = _const(value, self.consts)
            if v is not _UNRESOLVED:
                self.consts[target] = v
        for c in _calls_in_order(value):
            self.call(c, target, loops)

    def call(self, c: ast.Call, target: Optional[str], loops: tuple) -> None:
        if not isinstance(c.func, ast.Attribute):
            return
        m, owner = c.func.attr, _root_name(c.func.value)
        self.methods.add(m)
        args = c.args
        kw = {k.arg: k.value for k in c.keywords if k.arg}

        def a(i, name):
            return args[i] if len(args) > i else kw.get(name)

        loop = "; ".join(loops) or None
        if m in _LOADERS:
            self.load(m, owner, target, a, kw, loops)
        elif m in _LIQUID:
            self.liquid(m, owner, a, kw, loop)
        elif m == "delay":
            parts = [f"{self.say(n)} {unit}" for n, unit in ((a(1, "minutes"), "minutes"), (a(0, "seconds"), "seconds")) if n is not None and _const(n, self.consts) != 0]
            self.add("delay", "Wait " + (" and ".join(parts) or "for a while"), loop)
        elif m == "pause":
            self.add("pause", "Pause the protocol" + (f" with the message: {self.say(a(0, 'msg'))}" if a(0, "msg") is not None else ""), loop)
        elif m == "comment":
            self.add("comment", f"Show the comment: {self.say(a(0, 'msg'))}", loop)
        elif m in _MODULE_CALLS:
            self.module_call(m, owner, args, kw, loop)

    def add(self, kind: str, text: str, loop: Optional[str]) -> None:
        self.steps.append({"kind": kind, "text": text, "loop": loop})

    def load(self, m, owner, target, a, kw, loops) -> None:
        loop = "; ".join(loops) or None
        if m == "load_instrument":
            racks = kw.get("tip_racks")
            rack_txt = [self.say(e) for e in racks.elts] if isinstance(racks, (ast.List, ast.Tuple)) else ([self.say(racks)] if racks is not None else [])
            mount = self.say(a(1, "mount")) or ""
            mount = "left" if "LEFT" in mount.upper() else "right" if "RIGHT" in mount.upper() else mount
            self.pipettes.append({"var": target, "name": self.say(a(0, "instrument_name")), "mount": mount, "tip_racks": rack_txt})
        elif m == "load_module":
            name, slot = self.say(a(0, "module_name")), self.say(a(1, "location"))
            self.modules.append({"var": target, "name": name, "slot": slot})
            if target:
                self.module_vars[target] = name or ""
        else:  # load_labware / load_adapter: ctx.load_labware(name, slot, label) or module_or_adapter.load_labware(name, label)
            on_other = owner in self.module_vars or owner in {lw["var"] for lw in self.labware if lw["var"]}
            slot = None if on_other else self.say(a(1, "location"))
            label = self.say(a(1, "label")) if on_other else self.say(a(2, "label"))
            self.labware.append({"var": target, "name": self.say(a(0, "load_name")), "slot": slot, "label": label, "on": owner if on_other else None, "loop": loop})

    def liquid(self, m, owner, a, kw, loop) -> None:
        who = f" (pipette {owner})" if owner and len(self.pipettes) > 1 else ""
        if m in ("transfer", "distribute", "consolidate"):
            vol, src, dst = self.say(a(0, "volume")), self.loc(a(1, "source")), self.loc(a(2, "dest"))
            opts = []
            nt = self.say(kw.get("new_tip"))
            if nt:
                opts.append(f"new tip: {nt}")
            for key in ("mix_before", "mix_after"):
                if key in kw:
                    opts.append(f"{key.replace('_', ' ')} {self.say(kw[key])}")
            if "disposal_volume" in kw:
                opts.append(f"disposal volume {self.say(kw['disposal_volume'])} µL")
            tail = f" ({'; '.join(opts)})" if opts else ""
            self.add(m, f"{m.capitalize()} {vol} µL from {src} to {dst}{tail}{who}", loop)
            self.steps[-1].update(src=src, vol=vol, dst=dst)
        elif m == "aspirate":
            vol, src = self.say(a(0, "volume")), self.loc(a(1, "location"))
            self.add(m, f"Aspirate {vol or 'a volume'} µL from {src or 'the current position'}{who}", loop)
            self.steps[-1].update(src=src, vol=vol)
        elif m == "dispense":
            self.add(m, f"Dispense {self.say(a(0, 'volume')) or 'the held volume'} µL into {self.loc(a(1, 'location')) or 'the current position'}{who}", loop)
        else:
            self.add(m, f"Mix {self.say(a(0, 'repetitions')) or '1'} times with {self.say(a(1, 'volume')) or 'the default'} µL at {self.loc(a(2, 'location')) or 'the current position'}{who}", loop)

    def module_call(self, m, owner, args, kw, loop) -> None:
        mod = self.module_vars.get(owner or "", "") or owner or "module"
        first = args[0] if args else next(iter(kw.values()), None)  # the first argument, whatever its name
        arg = self.say(first) if first is not None else None
        if m in _TEMP_CALLS and arg:
            self.temps.append(_const(first, self.consts))
            self.add(m, f"Set the {'lid of the ' if 'lid' in m else ''}{mod} to {arg} C", loop)
        elif m == "execute_profile":
            self.add(m, f"Run the thermocycler profile {self.say(kw.get('steps') or first)}", loop)
        else:
            self.add(m, f"Module command: {m.replace('_', ' ')} ({mod}{', ' + arg if arg else ''})", loop)


def _module_consts(tree: ast.Module) -> dict:
    consts: dict = {}
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            v = _const(n.value, consts)
            if v is not _UNRESOLVED:
                consts[n.targets[0].id] = v
    return consts


# Lines that are licence, author or contact boilerplate rather than a description of the protocol.
_BOILER = re.compile(r"licen[sc]e|copyright|all rights reserved|permission is hereby|warrant|applicable law|spdx|\S+@\S+\.\S+|^(?:authors?|created by|updated|date|contact|e-?mail)\b", re.I)
_CODEISH = re.compile(r"^(?:import|from|def|class|print)\b|^!|coding[:=]", re.I)  # commented-out code, shebang, encoding line


def _human_text(text: str) -> str:
    return " ".join(l.strip() for l in text.splitlines() if l.strip() and not _BOILER.search(l.strip()))


def _leading_comments(text: str) -> str:
    lines = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                lines.append(tok.string.lstrip("#").strip())
            elif tok.type not in (tokenize.NL, tokenize.NEWLINE, tokenize.ENCODING):
                break
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return " ".join(l for l in lines if l and not _BOILER.search(l) and not _CODEISH.search(l))


def _inline_comment_count(text: str) -> int:
    n = 0
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT and len(tok.string.lstrip("# ")) > 12 and "noqa" not in tok.string and "type:" not in tok.string:
                n += 1
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    return n


def _norm_api(value) -> Optional[str]:
    return str(value) if value is not None else None


def _robot(value) -> str:
    v = re.sub(r"[^a-z0-9]", "", str(value).lower()) if value is not None else "ot2"
    return "OT-2" if v in ("ot2", "") else "Flex"


def analyse_protocol(text: str, relpath: str = "") -> Optional[dict]:
    """Parse one protocol file into a structured spec; None if it is not an Opentrons protocol.

    A protocol has a module-level ``metadata`` or ``requirements`` dict with an ``apiLevel`` key
    and a module-level ``run`` function. The file is parsed, never executed.
    """
    try:
        tree = _parse(text)
    except (SyntaxError, ValueError, RecursionError):
        return None
    meta: dict = {}
    req: dict = {}
    run_fn = None
    for n in tree.body:
        if isinstance(n, ast.Assign) and len(n.targets) == 1 and isinstance(n.targets[0], ast.Name):
            d = _dict_literal(n.value)
            if d is not None and n.targets[0].id == "metadata":
                meta.update(d)
            elif d is not None and n.targets[0].id == "requirements":
                req.update(d)
        elif isinstance(n, ast.FunctionDef) and n.name == "run" and n.args.args:
            run_fn = n
    if run_fn is None or ("apiLevel" not in meta and "apiLevel" not in req):
        return None
    api = _norm_api(req.get("apiLevel", meta.get("apiLevel")))
    robot = _robot(req.get("robotType", meta.get("robotType")))
    walker = _Walker(_module_consts(tree))
    walker.body(run_fn.body, ())
    if not any(s["kind"] in _LIQUID for s in walker.steps):  # helpers outside run() often hold the liquid handling
        for n in tree.body:
            if isinstance(n, ast.FunctionDef) and n is not run_fn:
                walker.body(n.body, ())
    doc = _human_text(ast.get_docstring(tree) or "")
    desc = _human_text(str(meta.get("description") or ""))
    lead = _leading_comments(text)
    description = re.sub(r"\s+", " ", " ".join(x for x in (desc, doc, lead) if x)).strip()
    title = str(meta.get("protocolName") or "").strip() or None
    steps = walker.steps
    step_text = "\n".join(s["text"] + " " + (s["loop"] or "") for s in steps)
    constants = {n: _short(repr(v), 90) for n, v in walker.consts.items() if re.search(rf"\b{re.escape(n)}\b", step_text)}  # named values the steps refer to
    spec = {
        "api_level": api,
        "robot_type": robot,
        "title": title,
        "description": description[:1500] or None,
        "has_description": len(desc) >= 20 or len(doc) >= 20 or len(lead) >= 20,
        "inline_comments": _inline_comment_count(text),
        "labware": walker.labware,
        "modules": walker.modules,
        "pipettes": walker.pipettes,
        "constants": constants,
        "steps": steps,
        "liquid_steps": sum(1 for s in steps if s["kind"] in _LIQUID),
        "methods": sorted(walker.methods),
        "temps": [t for t in walker.temps if isinstance(t, (int, float))],
        "uses_get_values": "get_values" in text,
        "uses_runtime_parameters": "add_parameters" in text,
        "metadata_source": str(meta.get("source") or ""),
        "signals": _signal_text(tree, text, relpath),
    }
    return spec


# ---------------------------------------------------------------------------
# prompt
# ---------------------------------------------------------------------------

def build_prompt(spec: dict, max_steps: int = 60) -> str:
    """Deterministic prompt from the spec: same spec, same text. No model, no randomness."""
    blocks = [[f"Write an Opentrons OT-2 Python protocol using Python Protocol API version {spec['api_level'] or 'as appropriate'}."]]
    head = ([f"Protocol name: {spec['title']}"] if spec["title"] else []) + ([f"Description: {spec['description']}"] if spec["description"] else [])
    if head:
        blocks.append(head)
    deck = [lw for lw in spec["labware"] if lw["name"]]
    if deck:
        blocks.append(["Labware:"] + [
            f"- {lw['name']} {'on ' + lw['on'] if lw['on'] else 'in slot ' + str(lw['slot'])}"
            + (f", labelled '{lw['label']}'" if lw["label"] else "") + (f" (variable {lw['var']})" if lw["var"] else "")
            for lw in deck])
    if spec["modules"]:
        blocks.append(["Modules:"] + [f"- {md['name']} in slot {md['slot']}" + (f" (variable {md['var']})" if md["var"] else "") for md in spec["modules"]])
    if spec["pipettes"]:
        blocks.append(["Pipettes:"] + [
            f"- {p['name']} on the {p['mount']} mount" + (f", tip racks: {', '.join(r for r in p['tip_racks'] if r)}" if p["tip_racks"] else "")
            + (f" (variable {p['var']})" if p["var"] else "") for p in spec["pipettes"]])
    if spec["constants"]:
        blocks.append(["Values the steps refer to:"] + [f"- {name} = {value}" for name, value in list(spec["constants"].items())[:15]])
    steps = spec["steps"]
    lines = ["Steps, in order:" if steps else "Steps: none could be read from the code."]
    i = n = 0
    while i < len(steps) and n < max_steps:
        s, j = steps[i], i
        while j + 1 < len(steps) and steps[j + 1]["text"] == s["text"] and steps[j + 1]["loop"] == s["loop"]:
            j += 1
        n += 1
        lines.append(f"{n}. {s['text']}" + (f" (x{j - i + 1})" if j > i else "") + (f" [inside: {s['loop']}]" if s["loop"] else ""))
        i = j + 1
    if i < len(steps):
        lines.append(f"... and {len(steps) - i} more steps.")
    blocks.append(lines)
    return "\n\n".join("\n".join(b) for b in blocks) + "\n"


# ---------------------------------------------------------------------------
# held-out family labelling
# ---------------------------------------------------------------------------

def _signal_text(tree: ast.Module, text: str, relpath: str) -> str:
    """Human-readable text of a file: path, comments, strings and names (not labware load names)."""
    skip = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute) and n.func.attr in _LOADERS and n.args:
            skip.add(id(n.args[0]))
    parts = [relpath]
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type == tokenize.COMMENT:
                parts.append(tok.string.lstrip("# "))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        pass
    for n in ast.walk(tree):
        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in skip:
            parts.append(n.value[:400])
        elif isinstance(n, ast.Name):
            parts.append(n.id)
        elif isinstance(n, (ast.FunctionDef, ast.arg)):
            parts.append(n.name if isinstance(n, ast.FunctionDef) else n.arg)
    return "\n".join(parts)


def _views(signals: str) -> tuple:
    raw = signals.lower()
    split = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", signals).lower()
    norm = re.sub(r"[\s_\-./\\]+", " ", split)
    return raw, norm


# (family, strong patterns, weak patterns), matched on the normalised text; a1-a12 uses the raw text.
_RULES = (
    ("colony-pcr-screening",
     (r"\bcolony pcr\b", r"\bcolony screen", r"\bscreen\w* (?:of )?colon", r"\bcolony template"),
     (r"\bq?pcr\b", r"\brt pcr\b", r"\bmaster ?mix", r"\bmmix\b", r"\bprimers?\b", r"\bamplif", r"\bcolon(?:y|ies)\b")),
    ("ecoli-heat-shock-transformation",
     (r"\bheat ?shock", r"\bcompetent\b", r"\btransformations?\b", r"\bsoc\b"),
     (r"\btransform", r"\bplasmids?\b", r"\be ?coli\b", r"\bbacteri", r"\bplating\b", r"\brecovery\b")),
    ("golden-gate-assembly",
     (r"\bgolden ?gate", r"\bbsa ?i\b", r"\bbsmb ?i\b", r"\besp3i\b", r"\btype iis\b", r"\bmoclo\b", r"\bbasic assembly\b", r"\bdna ?bot\b", r"\bloop assembly\b"),
     (r"\bassembl", r"\bligas", r"\bdigest", r"\brestriction\b", r"\bgibson\b", r"\bclon(?:e|ing)\b", r"\bfragments?\b")),
    ("opentrons-rna-extraction",
     (r"\b(?:rna|dna|nucleic acids?|viral|genomic)\b.{0,20}\b(?:extraction|isolation|purification|prep)\b", r"\bextraction\b", r"\bmagmax\b", r"\bmagbind\b", r"\blysis\b", r"\blysate\b"),
     (r"\bextract", r"\bisolat", r"\blyse\b", r"\belut", r"\bisopropanol\b", r"\bethanol\b", r"\betoh\b", r"\bsupernatant\b", r"\bwash buffer\b", r"\brna\b")),
    ("ampure-bead-cleanup",
     (r"\bampure\b", r"\bspri\b", r"\bbead (?:clean|purif|wash)", r"\b(?:clean ?up|purif\w*) .{0,20}\bbeads?\b", r"\bbeads? .{0,20}(?:clean ?up|purif)"),
     (r"\bbeads?\b", r"\bmagnet", r"\bclean ?up\b", r"\bpurif", r"\belut", r"\bethanol\b", r"\bsupernatant\b")),
)
_A1_A12 = re.compile(r"\ba1\s*(?:-+>?|=>|to|through|thru|:)\s*a12\b")
_TWO_WELLS = re.compile(r"\bsplit\w* .{0,40}\b(?:two|2) wells?\b")


@dataclasses.dataclass
class FamilyLabel:
    family: str
    doubt: bool
    reasons: list


def classify_family(spec: dict) -> FamilyLabel:
    """Conservative held-out family label. A strong match names one family; anything weaker,
    or a strong match across several families, is flagged as doubt.

    Signals: strong and weak keyword patterns over the file path, comments, strings and names
    (labware load names excluded); modules (magnetic, thermocycler, a 42 C step); and two shape
    rules for the plate-distribution families (three liquid steps or fewer with no module, or
    plain transfers/aspirate-dispense pairs that all move one volume). A doubt always carries a
    best-guess family."""
    raw, norm = _views(spec["signals"])
    strong = {f: [p for p in sp if re.search(p, norm)] for f, sp, _ in _RULES}
    weak = {f: [p for p in wp if re.search(p, norm)] for f, _, wp in _RULES}
    if _A1_A12.search(raw):
        strong["a1-a12-100ul"] = ["a1 to a12"]
    if _TWO_WELLS.search(norm):
        strong["split-200ul-two-wells"] = ["split two wells"]
    mod_names = " ".join(m["name"] or "" for m in spec["modules"]).lower()
    methods = set(spec["methods"])
    structure = {f: [] for f in HELDOUT_FAMILIES}
    if "magnetic" in mod_names or "magdeck" in mod_names or "engage" in methods:
        structure["ampure-bead-cleanup"].append("magnetic module")
        structure["opentrons-rna-extraction"].append("magnetic module")
    if "thermocycler" in mod_names:
        structure["colony-pcr-screening"].append("thermocycler")
        structure["golden-gate-assembly"].append("thermocycler")
    if any(abs(t - 42) < 0.5 for t in spec["temps"]):
        structure["ecoli-heat-shock-transformation"].append("42 C step")
    kinds = {s["kind"] for s in spec["steps"] if s["kind"] in _LIQUID}
    moves = [s for s in spec["steps"] if s["kind"] in ("transfer", "distribute", "aspirate")]
    if not spec["modules"] and 1 <= spec["liquid_steps"] <= 3:
        for f in ("a1-a12-100ul", "split-200ul-two-wells"):
            structure[f].append("tiny liquid-handling-only protocol")
    if not spec["modules"] and moves and kinds <= {"transfer", "distribute", "aspirate", "dispense"} and len({s["vol"] for s in moves}) == 1:
        for f in ("a1-a12-100ul", "split-200ul-two-wells"):
            structure[f].append("one volume throughout, plain moves only")
    hits = {f: len(strong.get(f, [])) for f in HELDOUT_FAMILIES}
    soft = {f: len(weak.get(f, [])) + len(structure[f]) for f in HELDOUT_FAMILIES}
    reasons = [f"{f}: strong {strong[f]}" for f in HELDOUT_FAMILIES if strong.get(f)]
    reasons += [f"{f}: weak {weak.get(f, []) + structure[f]}" for f in HELDOUT_FAMILIES if soft[f] and not strong.get(f)]
    named = [f for f in HELDOUT_FAMILIES if hits[f]]
    if len(named) == 1:
        return FamilyLabel(named[0], False, reasons)
    if named:
        return FamilyLabel(max(named, key=lambda f: (hits[f], soft[f])), True, reasons)
    guess = [f for f in HELDOUT_FAMILIES if soft[f]]
    if guess:
        return FamilyLabel(max(guess, key=lambda f: soft[f]), True, reasons)
    return FamilyLabel(NO_FAMILY, False, [])


# ---------------------------------------------------------------------------
# fingerprints (independence)
# ---------------------------------------------------------------------------

def _code_lines(text: str) -> list:
    """Code lines with comments and extra whitespace removed (blank lines dropped)."""
    return [l for l in (re.sub(r"\s+", " ", x.split("#", 1)[0]).strip() for x in text.splitlines()) if l]


def fingerprints(text: str, skeleton: bool = True) -> dict:
    """exact: raw bytes; normalised: comments and whitespace stripped; skeleton: code structure
    with constants blanked and metadata removed (same procedure, different numbers)."""
    out = {"exact": hashlib.sha256(text.encode()).hexdigest(), "normalised": hashlib.sha256("\n".join(_code_lines(text)).encode()).hexdigest()}
    if not skeleton:
        return out
    try:
        tree = _parse(text)
        tree.body = [n for n in tree.body if not (isinstance(n, ast.Assign) and any(isinstance(t, ast.Name) and t.id in ("metadata", "requirements") for t in n.targets))]
        for n in ast.walk(tree):
            if isinstance(n, ast.Constant):
                n.value = None
        out["skeleton"] = hashlib.sha256(ast.dump(tree).encode()).hexdigest()
    except (SyntaxError, ValueError, RecursionError):
        out["skeleton"] = out["normalised"]
    return out


NEAR_COPY = 0.6  # share of code lines two files must have in common to count as the same file


class Reference:
    """Python files that must never reach the corpus: an evaluation set, or a library whose licence
    is not cleared. A candidate matches if it is byte-identical, identical once comments and
    whitespace are removed, or shares NEAR_COPY of its code lines (Jaccard) with one of them."""

    def __init__(self, dirs: Iterable):
        self.exact: dict = {}
        self.normalised: dict = {}
        self.lines: dict = {}
        self.index: dict = {}
        for d in dirs:
            d = Path(d).resolve()
            for p in _iter_py(d):
                try:
                    text = p.read_bytes().decode("utf-8")
                except (OSError, UnicodeDecodeError):
                    continue
                name = f"{d.name}/{p.relative_to(d).as_posix()}"
                fp = fingerprints(text, skeleton=False)
                self.exact[fp["exact"]] = self.normalised[fp["normalised"]] = name
                self.lines[name] = {l for l in _code_lines(text) if len(l) >= 6}  # short lines (")", "else:") are in every file
                for line in self.lines[name]:
                    self.index.setdefault(line, set()).add(name)

    def match(self, text: str, fp: dict) -> Optional[str]:
        """Name of the reference file this text copies, or None."""
        if fp["exact"] in self.exact or fp["normalised"] in self.normalised:
            return self.exact.get(fp["exact"]) or self.normalised[fp["normalised"]]
        mine = {l for l in _code_lines(text) if len(l) >= 6}
        votes: dict = {}
        for line in mine:
            names = self.index.get(line, ())
            if len(names) <= 30:  # boilerplate lines shared by many files say nothing about who copied whom
                for n in names:
                    votes[n] = votes.get(n, 0) + 1
        for name in sorted(votes, key=votes.get, reverse=True)[:8]:
            theirs = self.lines[name]
            if len(mine & theirs) / len(mine | theirs) >= NEAR_COPY:
                return name
        return None


# ---------------------------------------------------------------------------
# walking a directory
# ---------------------------------------------------------------------------

@dataclasses.dataclass
class Candidate:
    record: dict
    path: Path
    info: dict


def _iter_py(root: Path) -> Iterator[Path]:
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = sorted(d for d in dirnames if d not in SKIP_DIRS and not os.path.islink(os.path.join(dirpath, d)))
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if fn.endswith(".py") and not p.is_symlink():
                yield p


def walk_candidates(root, source: str, *, exclude: Sequence[str] = (), min_steps: int = 1, skipped: Optional[list] = None, seen: Optional[dict] = None, avoid: Optional[Reference] = None) -> Iterator[Candidate]:
    """Yield one candidate per independent OT-2 protocol file under ``root``.

    Files that are skipped (not a protocol, Flex, unparseable, excluded, duplicate, no liquid
    handling, provenance-blocked, copied from ``avoid``) are appended to ``skipped`` as
    {"path", "reason"} rows. Pass the same ``seen`` dict to several calls to drop duplicates
    across sources too.
    """
    root = Path(root).resolve()  # the simulator runs from another directory, so paths must be absolute
    seen = seen if seen is not None else {}
    seen_exact, seen_norm = seen.setdefault("exact", {}), seen.setdefault("normalised", {})
    skips = skipped if skipped is not None else []
    for p in _iter_py(root):
        rel = p.relative_to(root).as_posix()
        if any(fnmatch.fnmatch(rel, g) for g in exclude):
            skips.append({"path": rel, "reason": "excluded-by-glob"})
            continue
        try:
            if p.stat().st_size > MAX_FILE_BYTES:
                skips.append({"path": rel, "reason": "too-large"})
                continue
            text = p.read_bytes().decode("utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if "apiLevel" not in text or "def run" not in text:
            continue
        try:
            spec = analyse_protocol(text, rel)
            if spec is None:
                try:
                    _parse(text)
                    skips.append({"path": rel, "reason": "not-a-protocol"})
                except (SyntaxError, ValueError, RecursionError):
                    skips.append({"path": rel, "reason": "unparseable"})
                continue
        except Exception as exc:  # one pathological untrusted file must not stop the build; the reason is recorded
            skips.append({"path": rel, "reason": f"analysis-error:{type(exc).__name__}"})
            continue
        if spec["robot_type"] != "OT-2":
            skips.append({"path": rel, "reason": "flex"})
            continue
        prov = [b for b in PROVENANCE_BLOCKLIST if b in spec["metadata_source"].lower()]
        if prov:
            skips.append({"path": rel, "reason": f"provenance:{prov[0]}"})
            continue
        fp = fingerprints(text)
        copied = avoid.match(text, fp) if avoid is not None else None
        if copied:
            skips.append({"path": rel, "reason": f"matches-reference:{copied}"})
            continue
        if fp["exact"] in seen_exact or fp["normalised"] in seen_norm:
            skips.append({"path": rel, "reason": "duplicate-of:" + (seen_exact.get(fp["exact"]) or seen_norm[fp["normalised"]])})
            continue
        seen_exact[fp["exact"]] = seen_norm[fp["normalised"]] = f"{source}:{rel}"
        if spec["liquid_steps"] < min_steps:
            skips.append({"path": rel, "reason": "no-liquid-handling"})
            continue
        label = classify_family(spec)
        record = {
            "id": f"{source}:{rel}",
            "source": source,
            "prompt": build_prompt(spec),
            "code": text,
            "heldout_family": label.family,
            "family_doubt": label.doubt,
            "refused_by_base": None,
            "simulate_pass": None,
        }
        info = {
            "path": rel, "sha256": fp["exact"], "normalised": fp["normalised"], "skeleton": fp["skeleton"], "api_level": spec["api_level"],
            "uses_get_values": spec["uses_get_values"],
            "uses_runtime_parameters": spec["uses_runtime_parameters"], "has_description": spec["has_description"],
            "inline_comments": spec["inline_comments"], "liquid_steps": spec["liquid_steps"], "family_reasons": label.reasons, "simulate": None,
        }
        yield Candidate(record, p, info)


# ---------------------------------------------------------------------------
# simulator gate
# ---------------------------------------------------------------------------

Runner = Callable[..., "tuple[int, str]"]


def scrubbed_env(simulator: str, home: str) -> dict:
    """Minimal environment for untrusted protocol code: no tokens, keys or proxies."""
    return {"PATH": os.pathsep.join([os.path.dirname(os.path.abspath(simulator)), "/usr/bin", "/bin"]), "HOME": home, "PYTHONIOENCODING": "utf-8", "PYTHONDONTWRITEBYTECODE": "1"}


def subprocess_runner(argv: list, *, env: dict, cwd: str, timeout: float) -> "tuple[int, str]":
    """Run argv in its own process group with a timeout; return (returncode, output tail)."""
    proc = subprocess.Popen(argv, env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    try:
        out, _ = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        raise TimeoutError(f"no result after {timeout} s")
    return proc.returncode, out.decode("utf-8", "replace")[-2000:]


def simulate_file(path, *, simulator: str, timeout: float = 120.0, runner: Optional[Runner] = None) -> dict:
    """Run one protocol through opentrons_simulate. Returns {"passed", "status", "detail"}."""
    run = runner or subprocess_runner
    with tempfile.TemporaryDirectory(prefix="s95sim-") as tmp:
        argv = [simulator, "-o", "nothing", str(path)]
        try:
            rc, out = run(argv, env=scrubbed_env(simulator, tmp), cwd=tmp, timeout=timeout)
        except TimeoutError:
            return {"passed": False, "status": "timeout", "detail": f"no result after {timeout} s"}
    if rc == 0:
        return {"passed": True, "status": "pass", "detail": ""}
    lines = [l for l in out.strip().splitlines() if l.strip() and not l.startswith("Caught exception")]  # asyncio clean-up noise follows the real error
    errors = [l for l in lines if re.search(r"Error|Exception", l)]
    return {"passed": False, "status": "fail", "detail": _short((errors or lines or [f"exit code {rc}"])[-1], 300)}


def simulate_candidates(cands: Iterable[Candidate], *, simulator: str, timeout: float = 120.0, workers: int = 6, runner: Optional[Runner] = None) -> None:
    """Fill ``simulate_pass`` on every candidate record (in place), ``workers`` at a time."""
    cands = list(cands)
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
        results = list(pool.map(lambda c: simulate_file(c.path, simulator=simulator, timeout=timeout, runner=runner), cands))
    for c, r in zip(cands, results):
        c.record["simulate_pass"] = r["passed"]
        c.info["simulate"] = r


# ---------------------------------------------------------------------------
# funnel and CLI
# ---------------------------------------------------------------------------

def funnel(cands: Sequence[Candidate], skipped: Sequence[dict]) -> dict:
    """Counts at each stage. ``independent`` counts distinct code skeletons among survivors."""
    reasons: dict = {}
    for s in skipped:
        k = s["reason"].split(":")[0]
        reasons[k] = reasons.get(k, 0) + 1
    rec = [c.record for c in cands]
    unflagged = [c for c in cands if c.record["heldout_family"] == NO_FAMILY and not c.record["family_doubt"]]
    survivors = [c for c in unflagged if c.record["simulate_pass"] is True]
    return {
        "candidates": len(cands),
        "skipped_by_reason": dict(sorted(reasons.items())),
        "simulate_pass": sum(1 for r in rec if r["simulate_pass"] is True),
        "simulate_fail": sum(1 for r in rec if r["simulate_pass"] is False),
        "simulate_not_run": sum(1 for r in rec if r["simulate_pass"] is None),
        "heldout_flagged": sum(1 for r in rec if r["heldout_family"] != NO_FAMILY and not r["family_doubt"]),
        "family_doubt": sum(1 for r in rec if r["family_doubt"]),
        "no_family_flag": len(unflagged),
        "survivors": len(survivors),
        "independent_survivors": len({c.info["skeleton"] for c in survivors}),
        "survivors_with_description": sum(1 for c in survivors if c.info["has_description"]),
    }


def main(argv: Optional[Sequence[str]] = None, runner: Optional[Runner] = None) -> int:
    ap = argparse.ArgumentParser(description="Build S9.5 training-corpus candidate records from a directory of OT-2 protocols.")
    ap.add_argument("root", help="directory of protocol files (treated as untrusted)")
    ap.add_argument("--source", required=True, help="source name written into every record, e.g. owner/repo@commit")
    ap.add_argument("--out", required=True, help="output JSONL path")
    ap.add_argument("--report", help="optional JSON report: funnel, per-file info, skipped files")
    ap.add_argument("--simulator", default="auto", help="path to opentrons_simulate, 'auto' (use PATH), or 'none' (leave simulate_pass null)")
    ap.add_argument("--timeout", type=float, default=120.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--exclude", action="append", default=[], metavar="GLOB", help="relative-path glob to skip (repeatable)")
    ap.add_argument("--min-steps", type=int, default=1, help="skip files with fewer liquid-handling steps (default 1)")
    ap.add_argument("--avoid", action="append", default=[], metavar="DIR", help="directory of files that must not reach the corpus (the Text2WetLab checkout, an uncleared library); copies and near-copies are skipped (repeatable)")
    args = ap.parse_args(argv)

    sim = None if args.simulator == "none" else (shutil.which("opentrons_simulate") if args.simulator == "auto" else args.simulator)
    if args.simulator not in ("auto", "none") and not (sim and os.access(sim, os.X_OK)):
        print(f"error: simulator not executable: {args.simulator}", file=sys.stderr)
        return 2
    skipped: list = []
    avoid = Reference(args.avoid) if args.avoid else None
    cands = list(walk_candidates(args.root, args.source, exclude=args.exclude, min_steps=args.min_steps, skipped=skipped, avoid=avoid))
    if sim:
        simulate_candidates(cands, simulator=sim, timeout=args.timeout, workers=args.workers, runner=runner)
    else:
        print("note: no simulator, simulate_pass left null", file=sys.stderr)
    with open(args.out, "w", encoding="utf-8") as fh:
        for c in cands:
            fh.write(json.dumps({k: c.record[k] for k in RECORD_KEYS}, ensure_ascii=False) + "\n")
    summary = funnel(cands, skipped)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump({"source": args.source, "funnel": summary, "files": [c.info for c in cands], "skipped": skipped}, fh, indent=1, ensure_ascii=False)
    print(json.dumps(summary, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
