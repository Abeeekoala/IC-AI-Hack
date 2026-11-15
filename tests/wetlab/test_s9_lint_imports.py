"""S9 static lint, imports: the aliased-import hole found by the independent audit of main (task H12).

`static_lint` used to judge only the MODULE of a `from ... import`: any `opentrons.*` submodule was allowed and the imported
names were never looked at, so `from opentrons.config import os as o` passed (`opentrons.config` re-exports `os`, `Path`,
`shutil` and `subprocess`). The lint is a filter for model-written code, not a sandbox; these tests claim nothing more.

Every test here calls `static_lint` on a string, or on the text of a file. Nothing executes protocol code or starts the
simulator, and the audit's proof of concept is only ever parsed. Files are written under pytest's `tmp_path` and nowhere else.
"""

import ast
import re
from pathlib import Path

import pytest

from vaxforge.s8_protocol import Candidate, ProtocolConfig, StepKind, build_protocol
from vaxforge.s9_llm import TemplateBackend
from vaxforge.s9_robot import compile_protocol
from vaxforge.s9_validate import ALLOWED_IMPORTS, BANNED_ATTRS, BANNED_NAMES, static_lint

CORPUS = Path(__file__).resolve().parents[2] / "docs" / "streamc_llm_eval"
NOT_PROGRAMS = {"run_eval.py", "regrade.py", "report_tables.py"}  # the harness around the saved programs (and any `*_probe*` file)
N_SAVED = 43 + 13  # live LLM attempts (spec_*/robot/llm_*/ and free_*/) + the template backend's 13 sessions

TAIL = "metadata = {'apiLevel': '2.19'}\ndef run(ctx): pass\n"
# The audit's proof of concept. It is only ever handed to `static_lint`, which parses it and runs nothing.
POC = "from opentrons import protocol_api\nfrom opentrons.config import os as o\nfrom opentrons.config import Path as P\n" + TAIL

# The module-like names the design lists as escape hatches, and every module a name may be imported from.
ESCAPE_NAMES = ["os", "sys", "pathlib", "Path", "subprocess", "shutil", "importlib", "io", "builtins", "socket", "ctypes", "pickle",
                "marshal", "tempfile", "glob", "inspect"]
# `__future__` is left out of the loops below: on a current Python, `from __future__ import <not a feature>` is already a syntax error
# when parsed, so its finding reads differently. It has a test of its own.
STDLIB_MODULES = sorted(ALLOWED_IMPORTS - {"opentrons", "__future__"})
OPENTRONS_MODULES = ["opentrons", "opentrons.protocol_api", "opentrons.types"]


# --------------------------------------------------------------------------- the audit's hole


def test_the_audits_proof_of_concept_is_refused_line_by_line():
    assert static_lint(POC) == ["line 2: from opentrons.config import ...", "line 3: from opentrons.config import ..."]
    assert static_lint("from opentrons import protocol_api\n" + TAIL) == []  # without the two imports the rest of it is clean


def test_the_stdlib_allow_list_is_unchanged():
    assert ALLOWED_IMPORTS == {"opentrons", "math", "typing", "itertools", "string", "collections", "functools", "dataclasses", "__future__"}


# --------------------------------------------------------------------------- refused imports

# (source, findings). The findings pin the wording too: `line N: ...`, with `...` when the whole statement is refused because of its
# module, and the REAL imported name (never the alias) when one name is refused.
REFUSED = [
    # another `opentrons` module, whatever is imported from it and under whatever alias
    ("from opentrons.config import os\n", ["line 1: from opentrons.config import ..."]),
    ("from opentrons.config import os as o\n", ["line 1: from opentrons.config import ..."]),
    ("from opentrons.config import Path as P, shutil\n", ["line 1: from opentrons.config import ..."]),
    ("import opentrons.config\n", ["line 1: import opentrons.config"]),
    ("import opentrons.config as c\n", ["line 1: import opentrons.config"]),
    ("import opentrons.simulate\n", ["line 1: import opentrons.simulate"]),
    ("from opentrons.simulate import get_protocol_api\n", ["line 1: from opentrons.simulate import ..."]),
    ("from opentrons.execute import get_protocol_api\n", ["line 1: from opentrons.execute import ..."]),
    ("from opentrons.protocols.api_support.util import APIVersionError\n", ["line 1: from opentrons.protocols.api_support.util import ..."]),
    ("from opentrons.hardware_control import API\n", ["line 1: from opentrons.hardware_control import ..."]),
    ("from opentrons.util import logging_config\n", ["line 1: from opentrons.util import ..."]),
    # submodules of the two allowed modules: no saved program needs them
    ("import opentrons.protocol_api.labware\n", ["line 1: import opentrons.protocol_api.labware"]),
    ("from opentrons.protocol_api.labware import Well\n", ["line 1: from opentrons.protocol_api.labware import ..."]),
    # `from opentrons import <name>` takes only protocol_api and types (the package itself imports os and Path)
    ("from opentrons import config\n", ["line 1: from opentrons import config"]),
    ("from opentrons import config as c\n", ["line 1: from opentrons import config"]),
    ("from opentrons import os\n", ["line 1: from opentrons import os"]),
    ("from opentrons import Path as P\n", ["line 1: from opentrons import Path"]),
    ("from opentrons import simulate, execute\n", ["line 1: from opentrons import execute", "line 1: from opentrons import simulate"]),
    ("from opentrons import protocol_api, config, types\n", ["line 1: from opentrons import config"]),  # one bad name among good ones
    # private names and star imports
    ("from opentrons.protocol_api import _private\n", ["line 1: from opentrons.protocol_api import _private"]),
    ("from opentrons.protocol_api import _private as visible\n", ["line 1: from opentrons.protocol_api import _private"]),
    ("from opentrons.protocol_api import *\n", ["line 1: from opentrons.protocol_api import *"]),
    ("from opentrons.types import *\n", ["line 1: from opentrons.types import *"]),
    ("from opentrons import *\n", ["line 1: from opentrons import *"]),
    ("from math import *\n", ["line 1: from math import *"]),
    # lower-case names in the two opentrons modules are submodules and helpers, not API classes (`labware` re-exports save_definition)
    ("from opentrons.protocol_api import labware\n", ["line 1: from opentrons.protocol_api import labware"]),
    ("from opentrons.protocol_api import validation as v\n", ["line 1: from opentrons.protocol_api import validation"]),
    ("from opentrons.types import enum\n", ["line 1: from opentrons.types import enum"]),
    # a banned name under an innocent alias: the finding names the real one
    ("from typing import exec as run_it\n", ["line 1: from typing import exec"]),
    ("from collections import environ as env\n", ["line 1: from collections import environ"]),
    # a name from an allowed non-opentrons module that starts with an underscore
    ("from collections import _collections_abc\n", ["line 1: from collections import _collections_abc"]),
    ("from typing import _GenericAlias as g\n", ["line 1: from typing import _GenericAlias"]),
    ("from functools import _lru_cache_wrapper\n", ["line 1: from functools import _lru_cache_wrapper"]),
    ("from dataclasses import _MISSING_TYPE\n", ["line 1: from dataclasses import _MISSING_TYPE"]),
    # modules that an allowed module re-exports
    ("from typing import sys\n", ["line 1: from typing import sys"]),
    ("from typing import types\n", ["line 1: from typing import types"]),
    ("from typing import operator\n", ["line 1: from typing import operator"]),
    ("from dataclasses import inspect as i\n", ["line 1: from dataclasses import inspect"]),
    # relative imports (the finding used to read `from None import ...`)
    ("from . import x\n", ["line 1: from . import ..."]),
    ("from .x import y\n", ["line 1: from .x import ..."]),
    ("from .. import z\n", ["line 1: from .. import ..."]),
    # what was refused before still is
    ("import os\n", ["line 1: import os"]),
    ("import os as math\n", ["line 1: import os"]),  # the module is judged, not the alias
    ("from os import path\n", ["line 1: from os import ..."]),
]


@pytest.mark.parametrize("src,findings", REFUSED)
def test_refused_import(src, findings):
    assert static_lint(src) == findings
    assert all(re.fullmatch(r"line \d+: (import|from) \S.*", f) for f in findings)  # the style of the other findings


def test_every_banned_name_is_refused_as_an_imported_name_under_any_alias():
    for module in [*STDLIB_MODULES, *OPENTRONS_MODULES]:
        for name in sorted(BANNED_NAMES | BANNED_ATTRS):
            assert static_lint(f"from {module} import {name} as harmless\n") == [f"line 1: from {module} import {name}"], (module, name)


def test_every_escape_hatch_name_is_refused_from_every_allowed_module():
    for module in [*STDLIB_MODULES, *OPENTRONS_MODULES]:
        for name in ESCAPE_NAMES:
            assert static_lint(f"from {module} import {name} as ok\n") == [f"line 1: from {module} import {name}"], (module, name)


def test_a_private_name_is_refused_from_every_allowed_module():
    for module in [*STDLIB_MODULES, *OPENTRONS_MODULES]:
        for name in ("_hidden", "__dunder__"):
            assert static_lint(f"from {module} import {name} as visible\n") == [f"line 1: from {module} import {name}"], (module, name)


def test_a_star_import_is_a_finding_from_every_allowed_module():
    for module in [*STDLIB_MODULES, *OPENTRONS_MODULES]:
        assert static_lint(f"from {module} import *\n") == [f"line 1: from {module} import *"], module


@pytest.mark.parametrize("name", ["os", "Path", "exec", "_hidden", "*"])
def test_from_future_takes_nothing_but_a_feature(name):
    assert static_lint(f"from __future__ import {name}\n")  # a syntax error on a current Python, this lint's own finding otherwise


@pytest.mark.parametrize("module", ["opentrons.protocol_api", "opentrons.types"])
@pytest.mark.parametrize("name", ["labware", "core", "deck", "validation", "create_protocol_context", "types", "enum", "sqrt"])
def test_lower_case_names_from_the_two_opentrons_modules_are_refused(module, name):
    assert static_lint(f"from {module} import {name}\n") == [f"line 1: from {module} import {name}"]


@pytest.mark.parametrize("name", ["config", "simulate", "execute", "protocols", "hardware_control", "util", "os", "Path", "logging", "HERE"])
def test_from_opentrons_takes_only_protocol_api_and_types(name):
    assert static_lint(f"from opentrons import {name}\n") == [f"line 1: from opentrons import {name}"]


def test_types_is_refused_from_outside_opentrons_only():
    assert static_lint("from opentrons import types\n") == []
    for module in ("typing", "collections", "dataclasses", "functools"):
        assert static_lint(f"from {module} import types\n") == [f"line 1: from {module} import types"], module


def test_imports_inside_functions_and_blocks_are_checked_too():
    assert static_lint("def run(ctx):\n    if ctx:\n        from opentrons.config import os as o\n") == ["line 3: from opentrons.config import ..."]
    assert static_lint("try:\n    import os\nexcept ImportError:\n    pass\n") == ["line 2: import os"]


# --------------------------------------------------------------------------- accepted imports

ACCEPTED = [
    "from opentrons import protocol_api\n",  # the one import every saved program uses
    "from opentrons import protocol_api, types\n",
    "from opentrons import protocol_api as pa, types as t\n",
    "import opentrons\n",
    "import opentrons as ot\n",
    "import opentrons.protocol_api\n",
    "import opentrons.types as t\n",
    "from opentrons.types import Point\n",
    "from opentrons.types import Point, Location, Mount\n",
    "from opentrons.protocol_api import ProtocolContext\n",
    "from opentrons.protocol_api import ProtocolContext as Context, Labware, Well, InstrumentContext, COLUMN, ALL\n",
    "import math\n",
    "from math import ceil, floor, sqrt as root\n",
    "import typing\n",
    "from typing import Any, Optional, TYPE_CHECKING\n",
    "import itertools\n",
    "from itertools import product\n",
    "import string\n",
    "from string import ascii_uppercase\n",
    "import collections\n",
    "from collections import OrderedDict, defaultdict\n",
    "from collections import abc\n",
    "from collections.abc import Mapping\n",
    "import functools\n",
    "from functools import partial\n",
    "import dataclasses\n",
    "from dataclasses import dataclass, field\n",
    "from __future__ import annotations\n",
    "from math import ceil as os\n",  # the real name is judged, so an alarming alias on an innocent name is not a finding
]


@pytest.mark.parametrize("src", ACCEPTED)
def test_accepted_import(src):
    assert static_lint(src) == []


# The public names of Opentrons 7.5.0 (the pinned simulator): `opentrons.protocol_api.__all__` without the two names it marks "for
# internal Opentrons use only", and what `opentrons/types.py` defines. Written out here so that the rule "a name taken from these two
# modules is capitalised" can be seen not to refuse any of them. Nothing is imported from Opentrons.
PROTOCOL_API_NAMES = ["ProtocolContext", "InstrumentContext", "Labware", "Well", "Deck", "ModuleContext", "TemperatureModuleContext",
                      "MagneticModuleContext", "ThermocyclerContext", "HeaterShakerContext", "MagneticBlockContext", "ParameterContext",
                      "Parameters", "Liquid", "TrashBin", "WasteChute", "COLUMN", "ALL", "OFF_DECK", "MAX_SUPPORTED_VERSION",
                      "MIN_SUPPORTED_VERSION", "MIN_SUPPORTED_VERSION_FOR_FLEX"]
TYPES_NAMES = ["Point", "Location", "Mount", "MountType", "PipetteMountType", "OT3MountType", "DeckSlotName", "StagingSlotName",
               "TransferTipPolicy", "PipetteNotAttachedError", "LocationLabware", "DeckLocation", "ALLOWED_PRIMARY_NOZZLES"]


def test_every_public_name_of_the_two_opentrons_modules_is_accepted():
    for module, names in (("opentrons.protocol_api", PROTOCOL_API_NAMES), ("opentrons.types", TYPES_NAMES)):
        for name in names:
            assert static_lint(f"from {module} import {name}\n") == [], (module, name)
            assert static_lint(f"from {module} import {name} as alias\n") == [], (module, name)


# --------------------------------------------------------------------------- the saved programs must still pass


def saved_programs():
    return sorted(p for p in CORPUS.rglob("*.py") if p.name not in NOT_PROGRAMS and "_probe" not in p.name)


def imports_of(source):
    return {ast.unparse(n) for n in ast.walk(ast.parse(source)) if isinstance(n, (ast.Import, ast.ImportFrom))}


@pytest.mark.skipif(not CORPUS.is_dir(), reason="docs/streamc_llm_eval is not in this checkout, so the saved LLM programs cannot be linted")
def test_every_saved_program_still_passes_the_lint():
    """All 43 LLM programs and the 13 template programs passed `static_lint` before the import rules were tightened.

    So any finding here is new. The test also says which imports they use, and that each is accepted on its own.
    """
    programs = saved_programs()
    assert len(programs) >= N_SAVED, f"expected the 43 LLM and 13 template programs, found {len(programs)}"
    texts = {p.relative_to(CORPUS).as_posix(): p.read_text(encoding="utf-8") for p in programs}
    new_findings = {name: found for name, text in texts.items() if (found := static_lint(text))}
    assert not new_findings, new_findings
    used = set().union(*(imports_of(text) for text in texts.values()))
    assert "from opentrons import protocol_api" in used
    for statement in sorted(used):  # every import the saved programs use is accepted on its own as well
        assert static_lint(statement + "\n") == [], statement


def test_the_template_backends_13_programs_still_pass_the_lint(tmp_path):
    """Generated here, not read from docs/: `simulate_runs=False` writes and lints the code and never runs it."""
    proto = build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1),
                           ProtocolConfig(readouts=[StepKind.ELISA, StepKind.RT_QPCR, StepKind.FACS]))
    report = compile_protocol(proto, tmp_path, backends=[TemplateBackend()], simulate_runs=False, animate=False)
    (run,) = report.runs
    assert len(run.sessions) == 13
    for s in run.sessions:
        code = (tmp_path / s.code_file).read_text(encoding="utf-8")
        assert static_lint(code) == [], s.session_id
        assert s.grade is not None and s.grade.lint == [], s.session_id
