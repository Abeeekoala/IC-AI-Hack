"""S9 static lint, imports: the aliased-import hole found by the independent audit of main (task H12).

`static_lint` used to judge only the MODULE of a `from ... import`: any `opentrons.*` submodule was allowed and the imported
names were never looked at, so `from opentrons.config import os as o` passed (`opentrons.config` re-exports `os`, `Path`,
`shutil` and `subprocess`). The lint is a filter for model-written code, not a sandbox; these tests claim nothing more.

Task H15 adds the attribute side (the sections "attribute chains" and "builtins and get_type_hints"): once the imports were judged,
`opentrons.config.Path(...)` and `protocol_api.labware.save_definition(...)` still passed, because the attributes of an allowed
module were never looked at. Those probes are strings handed to `static_lint`, like everything else here.

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


# --------------------------------------------------------------------------- attribute chains through allowed imports (task H15)

# PR #71 judged the imports. A module that may be imported still hands out attributes, and those reach what the imports no longer do:
# `opentrons.config.Path(...)`, and `protocol_api.labware.save_definition(...)` (it writes a file). The module objects `opentrons`,
# `protocol_api` and `types` are followed through every import alias to the real module: `opentrons.X` is for X in {protocol_api,
# types} only, `protocol_api.X` and `types.X` for a capitalised X only. A finding names the real module, never the alias. A module
# held under another name (`m = opentrons`) is not followed, and no test here says otherwise. Only strings are linted: the probes
# are parsed and never run.
CONFIG = ["line 2: attribute opentrons.config"]
LABWARE = ["line 2: attribute opentrons.protocol_api.labware"]
CHAINS_REFUSED = [
    # the two probes of PR #71's report, as written ...
    ("import opentrons\nopentrons.config.Path('x')\n", CONFIG),
    ("from opentrons import protocol_api\nprotocol_api.labware.save_definition({})\n", LABWARE),
    # ... and under every way of naming the module: an alias, or the package that `import opentrons.<submodule>` binds
    ("import opentrons as o\no.config.Path('x')\n", CONFIG),
    ("import opentrons.protocol_api\nopentrons.config.Path('x')\n", CONFIG),
    ("import opentrons.types as t, opentrons as o\no.config.Path('x')\n", CONFIG),
    ("from opentrons import protocol_api as p\np.labware.save_definition({})\n", LABWARE),
    ("import opentrons.protocol_api as p\np.labware.save_definition({})\n", LABWARE),
    ("import opentrons\nopentrons.protocol_api.labware.save_definition({})\n", LABWARE),
    ("import opentrons as o\no.protocol_api.labware.save_definition({})\n", LABWARE),
    ("import opentrons.protocol_api\nopentrons.protocol_api.labware.save_definition({})\n", LABWARE),
    # `types`, and the other lower-case names of `protocol_api` (submodules and helpers)
    ("from opentrons import types\ntypes.enum.Enum\n", ["line 2: attribute opentrons.types.enum"]),
    ("from opentrons import types as t\nt.sqrt(2)\n", ["line 2: attribute opentrons.types.sqrt"]),
    ("import opentrons.types as t\nt.enum\n", ["line 2: attribute opentrons.types.enum"]),
    ("import opentrons\nopentrons.types.enum\n", ["line 2: attribute opentrons.types.enum"]),
    ("from opentrons import protocol_api\nprotocol_api.create_protocol_context()\n",
     ["line 2: attribute opentrons.protocol_api.create_protocol_context"]),
    ("from opentrons import protocol_api\nprotocol_api.validation\n", ["line 2: attribute opentrons.protocol_api.validation"]),
    # `opentrons.X` for any X but protocol_api and types, a capitalised X too; only the first link of a chain is a finding
    ("import opentrons\nopentrons.simulate.get_protocol_api('2.19')\n", ["line 2: attribute opentrons.simulate"]),
    ("import opentrons\nopentrons.execute.get_protocol_api('2.19')\n", ["line 2: attribute opentrons.execute"]),
    ("import opentrons\nopentrons.Path\n", ["line 2: attribute opentrons.Path"]),
    ("import opentrons\nopentrons.ProtocolContext\n", ["line 2: attribute opentrons.ProtocolContext"]),
    ("import opentrons\nopentrons.protocols.api_support.util.APIVersionError\n", ["line 2: attribute opentrons.protocols"]),
    # an attribute that was refused before keeps its old finding (once, not twice)
    ("import opentrons\nopentrons.os\n", ["line 2: attribute .os"]),
    ("from opentrons import protocol_api\nprotocol_api._core\n", ["line 2: attribute ._core"]),
    ("import opentrons\nopentrons.__version__\n", ["line 2: attribute .__version__"]),
    # the alias is never looked at: an innocent one does not save the attribute
    ("import opentrons as math\nmath.config\n", CONFIG),
    ("from opentrons import protocol_api as Point\nPoint.labware\n", LABWARE),
    # a name that two imports bind to different modules is judged against both
    ("import opentrons as m\nfrom opentrons import types as m\nm.Point\n", ["line 3: attribute opentrons.Point"]),
    # wherever the attribute is written: function, annotation, decorator, default value, comprehension, class body, nested import
    ("import opentrons\ndef run(ctx):\n    return [opentrons.config for x in ctx]\n", ["line 3: attribute opentrons.config"]),
    ("import opentrons\ndef run(ctx: opentrons.config.Path): pass\n", CONFIG),
    ("import opentrons\n@opentrons.config.deco\ndef run(ctx): pass\n", CONFIG),
    ("import opentrons\ndef run(ctx, x=opentrons.config): pass\n", CONFIG),
    ("import opentrons\nclass A:\n    x = opentrons.config\n", ["line 3: attribute opentrons.config"]),
    ("def run(ctx):\n    import opentrons\n    opentrons.config.Path('x')\n", ["line 3: attribute opentrons.config"]),
    ("import opentrons.protocol_api as p\ndef run(ctx):\n    p.labware.save_definition({})\n",
     ["line 3: attribute opentrons.protocol_api.labware"]),
    # writing to an attribute of a module is refused twice over
    ("import opentrons\nopentrons.config = 1\n", ["line 2: assignment to an attribute", "line 2: attribute opentrons.config"]),
]


@pytest.mark.parametrize("src,findings", CHAINS_REFUSED)
def test_refused_attribute_chain(src, findings):
    assert static_lint(src) == findings
    assert all(re.fullmatch(r"line \d+: (attribute \S+|assignment to an attribute)", f) for f in findings)  # the style of the others


# Every way of naming a module object: (what is imported, how the module is written after it). `MODULE_SPELLINGS` adds the real module.
PACKAGE_SPELLINGS = [("import opentrons\n", "opentrons"), ("import opentrons.protocol_api\n", "opentrons"),
                     ("import opentrons.types\n", "opentrons"), ("import opentrons as o\n", "o")]
PROTOCOL_API_SPELLINGS = [("import opentrons\n", "opentrons.protocol_api"), ("import opentrons.protocol_api\n", "opentrons.protocol_api"),
                          ("import opentrons as o\n", "o.protocol_api"), ("from opentrons import protocol_api\n", "protocol_api"),
                          ("from opentrons import protocol_api as pa\n", "pa"), ("import opentrons.protocol_api as pa\n", "pa")]
TYPES_SPELLINGS = [("import opentrons\n", "opentrons.types"), ("import opentrons.types\n", "opentrons.types"),
                   ("import opentrons as o\n", "o.types"), ("from opentrons import types\n", "types"),
                   ("from opentrons import types as t\n", "t"), ("import opentrons.types as t\n", "t")]
MODULE_SPELLINGS = [(real, imports, module)
                    for real, spellings in (("opentrons.protocol_api", PROTOCOL_API_SPELLINGS), ("opentrons.types", TYPES_SPELLINGS))
                    for imports, module in spellings]
LOWER_CASE = ["labware", "config", "save_definition", "execute", "simulate", "validation", "create_protocol_context", "core", "deck",
              "enum", "sqrt", "types", "protocol_api"]
CAPITALISED = ["ProtocolContext", "InstrumentContext", "Labware", "Well", "COLUMN", "ALL", "OFF_DECK", "Point", "Location", "Mount"]


@pytest.mark.parametrize("real,imports,module", MODULE_SPELLINGS)
def test_a_lower_case_attribute_of_protocol_api_or_types_is_refused_under_every_spelling(real, imports, module):
    line = imports.count("\n") + 1
    for name in LOWER_CASE:
        assert static_lint(f"{imports}{module}.{name}\n") == [f"line {line}: attribute {real}.{name}"], name


@pytest.mark.parametrize("real,imports,module", MODULE_SPELLINGS)
def test_a_capitalised_attribute_of_protocol_api_or_types_is_accepted_under_every_spelling(real, imports, module):
    for name in CAPITALISED:
        assert static_lint(f"{imports}{module}.{name}\n") == [], (real, name)


@pytest.mark.parametrize("imports,module", PACKAGE_SPELLINGS)
def test_the_opentrons_package_gives_out_protocol_api_and_types_and_nothing_else(imports, module):
    line = imports.count("\n") + 1
    for name in ("protocol_api", "types"):
        assert static_lint(f"{imports}{module}.{name}\n") == [], name
    # a capitalised name is no exception here: `Point` and `ProtocolContext` live in the submodules, not in the package
    for name in ["config", "simulate", "execute", "protocols", "hardware_control", "util", "logging", "Path", "ProtocolContext", "Point",
                 "COLUMN"]:
        assert static_lint(f"{imports}{module}.{name}\n") == [f"line {line}: attribute opentrons.{name}"], name


CHAINS_ACCEPTED = [
    "from opentrons import protocol_api\ndef run(ctx: protocol_api.ProtocolContext): pass\n",  # the annotation every saved program has
    "import opentrons\ndef run(ctx: opentrons.protocol_api.ProtocolContext): pass\n",
    "import opentrons.protocol_api\ndef run(ctx: opentrons.protocol_api.ProtocolContext): pass\n",
    "import opentrons.protocol_api as p\ndef run(ctx: p.ProtocolContext): pass\n",
    "from opentrons import types\nstart = types.Point(0, 0, 10)\nwhere = types.Location(start, None)\n",
    "from opentrons import types as t\nleft = t.Mount.LEFT\n",  # an attribute of a class is not an attribute of the module
    "import opentrons.types as t\nstart = t.Point(x=1, y=2, z=3)\n",
    "import opentrons\nstart = opentrons.types.Point(1, 2, 3)\n",
    "from opentrons import protocol_api\nwhole = protocol_api.ALL\ncolumn = protocol_api.COLUMN\n",
    # a variable that is not an imported module keeps every attribute
    "from opentrons import protocol_api\n"
    "def run(ctx: protocol_api.ProtocolContext):\n"
    "    plate = ctx.load_labware('nest_96_wellplate_200ul_flat', '1')\n"
    "    pip = ctx.load_instrument('p300_single_gen2', 'right')\n"
    "    pip.transfer(10, plate['A1'], plate['B1'])\n"
    "    ctx.comment('done')\n",
    "def run(ctx):\n    ctx.load_labware('nest_96_wellplate_200ul_flat', '1')\n",
    "types = {'a': 1}\nfor key, value in types.items():\n    pass\n",  # a local called `types` is not `opentrons.types`
    "import math as p\nx = p.ceil(1.2)\n",  # an alias is followed to its real module: this one is not an opentrons module
]


@pytest.mark.parametrize("src", CHAINS_ACCEPTED)
def test_accepted_attribute_chain(src):
    assert static_lint(src) == []


# --------------------------------------------------------------------------- builtins and get_type_hints (task H15)

# `BANNED_NAMES` had `__import__` but not `__builtins__`, so a bare `__builtins__[...]` named no banned function. As attributes
# these names were refused already (a leading `_`, and `builtins` in `BANNED_ATTRS`); the tests pin both forms.
BUILTINS_NAMES = ["__builtins__", "builtins", "__import__", "__loader__", "__spec__", "__dict__"]


def test_the_ban_lists_hold_the_new_names():
    assert set(BUILTINS_NAMES) <= BANNED_NAMES
    assert {"builtins", "get_type_hints"} <= BANNED_ATTRS


@pytest.mark.parametrize("name", BUILTINS_NAMES)
def test_a_name_that_reaches_the_builtins_is_a_finding_as_a_name_and_as_an_attribute(name):
    assert static_lint(f"x = {name}\n") == [f"line 1: name {name}"]
    assert static_lint(f"{name} = 1\n") == [f"line 1: name {name}"]  # as an assignment target too
    assert static_lint(f"x = ctx.{name}\n") == [f"line 1: attribute .{name}"]


def test_a_bare_builtins_lookup_is_refused_in_each_of_its_forms():
    assert static_lint("x = __builtins__['open']\n") == ["line 1: name __builtins__"]
    assert static_lint("def run(ctx):\n    f = __builtins__['open']\n") == ["line 2: name __builtins__"]
    assert static_lint("x = __builtins__.__dict__\n") == ["line 1: attribute .__dict__", "line 1: name __builtins__"]
    assert static_lint("x = __loader__.name\n") == ["line 1: name __loader__"]
    assert static_lint("x = __spec__.loader\n") == ["line 1: name __spec__"]


# `typing.get_type_hints` evaluates string annotations, and `from __future__ import annotations` makes every annotation a string.
# No saved program uses it. It is refused as an imported name (under any alias: the real name is judged) and as an attribute.
GET_TYPE_HINTS_REFUSED = [
    ("from typing import get_type_hints\n", ["line 1: from typing import get_type_hints"]),
    ("from typing import get_type_hints as hints\n", ["line 1: from typing import get_type_hints"]),
    ("from typing import get_args, get_type_hints, get_origin\n", ["line 1: from typing import get_type_hints"]),
    ("from collections import get_type_hints\n", ["line 1: from collections import get_type_hints"]),  # whichever module hands it out
    ("import typing\nhints = typing.get_type_hints(run)\n", ["line 2: attribute .get_type_hints"]),
    ("import typing as t\nhints = t.get_type_hints(run)\n", ["line 2: attribute .get_type_hints"]),
    ("import typing\nhints = typing.get_type_hints\n", ["line 2: attribute .get_type_hints"]),  # a reference, not only a call
    ("import typing\ndef run(ctx, f=typing.get_type_hints): pass\n", ["line 2: attribute .get_type_hints"]),
]


@pytest.mark.parametrize("src,findings", GET_TYPE_HINTS_REFUSED)
def test_get_type_hints_is_refused(src, findings):
    assert static_lint(src) == findings


@pytest.mark.parametrize("src", [
    "from typing import get_args, get_origin\n",  # its neighbours in `typing` are not banned
    "import typing\nx = typing.get_origin(int)\n",
    # string annotations stay fine: what is refused is evaluating them
    "from __future__ import annotations\nfrom opentrons import protocol_api\ndef run(ctx: protocol_api.ProtocolContext): pass\n",
    "from opentrons import protocol_api\ndef run(ctx: 'protocol_api.ProtocolContext'): pass\n",
])
def test_the_neighbours_of_get_type_hints_and_string_annotations_are_accepted(src):
    assert static_lint(src) == []


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


def module_attributes_of(source):
    """(name, attribute) for each `<name>.<attribute>` in `source` where the name is `opentrons`, `protocol_api` or `types`."""
    return {(n.value.id, n.attr) for n in ast.walk(ast.parse(source))
            if isinstance(n, ast.Attribute) and isinstance(n.value, ast.Name) and n.value.id in {"opentrons", "protocol_api", "types"}}


@pytest.mark.skipif(not CORPUS.is_dir(), reason="docs/streamc_llm_eval is not in this checkout, so the saved LLM programs cannot be linted")
def test_the_saved_programs_read_only_attributes_that_the_attribute_rule_accepts():
    """The attributes of `opentrons` / `protocol_api` / `types` that the 56 saved programs read, as of task H15: only
    `protocol_api.ProtocolContext`, the annotation of `run`. None reads anything from `types` or from `opentrons` itself, and none
    a lower-case attribute of a module, so the attribute rule needed no allowed lower-case name. Each attribute is accepted on its
    own as well (the programs as a whole are linted in the test above).
    """
    used = set().union(*(module_attributes_of(p.read_text(encoding="utf-8")) for p in saved_programs()))
    assert ("protocol_api", "ProtocolContext") in used
    for name, attribute in sorted(used):
        imports = "import opentrons\n" if name == "opentrons" else f"from opentrons import {name}\n"
        assert static_lint(f"{imports}{name}.{attribute}\n") == [], (name, attribute)


@pytest.mark.skipif(not CORPUS.is_dir(), reason="docs/streamc_llm_eval is not in this checkout, so the saved LLM programs cannot be linted")
def test_the_two_probes_are_refused_when_appended_to_each_saved_program():
    """Lint only: a probe appended to the text of a saved program is parsed, never run, and is that program's one finding."""
    probes = [("\nprotocol_api.labware.save_definition({})\n", r"line \d+: attribute opentrons\.protocol_api\.labware"),
              ("\nimport opentrons as o\no.config.Path('x')\n", r"line \d+: attribute opentrons\.config")]
    for program in saved_programs():
        text = program.read_text(encoding="utf-8")
        for probe, finding in probes:
            found = static_lint(text + probe)
            assert len(found) == 1 and re.fullmatch(finding, found[0]), (program.relative_to(CORPUS).as_posix(), found)


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
