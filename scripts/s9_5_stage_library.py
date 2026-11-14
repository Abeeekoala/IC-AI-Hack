#!/usr/bin/env python3
"""Stage the Opentrons Protocols library as self-contained protocol files (README S9.5, Training data provenance).

    python3 scripts/s9_5_stage_library.py PROTOCOLS_CLONE STAGE_DIR

Most protocols in github.com/Opentrons/Protocols call ``get_values('a', 'b')``, which the library site injects when a
protocol is downloaded; as stored they do not run. Each protocol directory has a ``fields.json``; every field has a default, or is a drop-down whose first
option the form preselects, so this replaces each ``get_values(...)`` call with the tuple of those defaults and writes the result to
STAGE_DIR/<protocol dir>/<file>.py. Nothing else in the code changes. A protocol with a text-file field (it needs an input file we do not have), whose fields.json is missing or unreadable, or which does not parse, is skipped and counted. The staged tree
is then the input of src/vaxforge/s9_5_corpus.py, which simulates, labels and de-duplicates it like any other source.

The library carries no licence file and Opentrons states its copyright (README S9.5): this step records no licence. Using
it is the project owner's decision for non-commercial personal research, and the manifest must say so.
Standard library only.
"""
import argparse
import ast
import json
import sys
from collections import Counter
from pathlib import Path


def defaults(fields_path):
    """{field name: default} from a fields.json, or None when any field lacks a usable default."""
    fields = json.loads(Path(fields_path).read_text(encoding="utf-8"))
    out = {}
    for f in fields:
        if f.get("type") == "dropDown" and f.get("options"):  # the form's first option is what a download preselects
            out[f["name"]] = f["options"][0]["value"]
        elif f.get("type") in ("int", "float", "str", "string") and f.get("default") is not None:
            out[f["name"]] = f["default"]
        else:  # a textFile field has no default: the protocol needs an input file we do not have
            return None
    return out


def patch(text, values):
    """(source with every get_values(...) call replaced by the tuple of the defaults, None), or (None, the reason)."""
    tree = ast.parse(text)
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name) and n.func.id == "get_values"]
    lines = text.splitlines(keepends=True)
    starts = [0]
    for line in lines:
        starts.append(starts[-1] + len(line))
    edits = []
    for call in calls:
        names = [a.value for a in call.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if len(names) != len(call.args) or call.keywords or not names:
            return None, "get_values with arguments that are not field names"
        missing = [n for n in names if n not in values]
        if missing:
            return None, "a get_values field has no default"
        begin = starts[call.lineno - 1] + len(lines[call.lineno - 1].encode()[: call.col_offset].decode())
        end = starts[call.end_lineno - 1] + len(lines[call.end_lineno - 1].encode()[: call.end_col_offset].decode())
        edits.append((begin, end, "(" + "".join(repr(values[n]) + ", " for n in names) + ")"))
    for begin, end, new in sorted(edits, reverse=True):
        text = text[:begin] + new + text[end:]
    ast.parse(text)
    return text, None


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("clone")
    ap.add_argument("stage")
    args = ap.parse_args(argv)
    clone, stage = Path(args.clone), Path(args.stage)
    counts = Counter()
    for src in sorted((clone / "protocols").glob("*/*.ot2.apiv2.py")):
        try:
            text = src.read_text(encoding="utf-8")
            if "get_values" in text:
                fields = src.parent / "fields.json"
                values = defaults(fields) if fields.is_file() else None
                if values is None:
                    counts["skipped: no usable fields.json defaults"] += 1
                    continue
                text, why = patch(text, values)
                if text is None:
                    counts["skipped: " + why] += 1
                    continue
            else:
                ast.parse(text)
        except (SyntaxError, ValueError, OSError, UnicodeDecodeError) as e:  # JSONDecodeError is a ValueError
            counts[f"skipped: {type(e).__name__}"] += 1
            continue
        dest = stage / src.parent.name / src.name.replace(".ot2.apiv2.py", ".py")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(text, encoding="utf-8")
        counts["staged"] += 1
    for k, v in sorted(counts.items()):
        print(f"{v:5d}  {k}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
