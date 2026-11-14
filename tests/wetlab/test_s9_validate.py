"""S9 grader: static lint, Text2WetLab's five physical rules, reference diff, and real simulations.

The rule tests use hand-built event logs, so they run anywhere. The `needs_ot` tests run real
`opentrons_simulate` runs and are skipped when no Opentrons 7.5.0 interpreter is available.
A grader that only ever says PASS proves nothing, so most tests here are NEGATIVE controls.
"""

import importlib
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import MappingProxyType

import pytest

from vaxforge import _ot_sim_runner
from vaxforge._ot_sim_runner import summarize_exception
from vaxforge.s9_validate import (
    RULES,
    _scrubbed_env,
    actual_dispensed,
    check_physical_rules,
    cross_contamination,
    find_ot_python,
    final_volumes,
    grade_source,
    initial_from_liquids,
    leaf_events,
    reference_diff,
    simulate,
    static_lint,
    structure_findings,
)

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "ot_sim"
needs_ot = pytest.mark.skipif(find_ot_python() is None, reason="no Opentrons 7.5.0 interpreter (set VAXFORGE_OT_PYTHON)")

PLATE = "nest_96_wellplate_200ul_flat"
TUBES = "opentrons_24_tuberack_nest_1.5ml_snapcap"
RES = "nest_12_reservoir_15ml"


# --------------------------------------------------------------------------- synthetic logs


def E(idx, kind, slot=None, well=None, vol=None, labware=PLATE, ch=1, level=0):
    return {
        "idx": idx, "level": level, "kind": kind, "is_group": False, "text": f"{kind} {well}",
        "pipette": {"mount": "right", "name": "p300_multi_gen2" if ch == 8 else "p300_single_gen2", "channels": ch},
        "volume_ul": vol, "flow_rate_ul_s": None,
        "target": {"slot": slot, "well": well, "labware": labware, "point_mm": None} if slot else None,
        "source": None, "dest": None, "celsius": None, "seconds": None, "repetitions": None, "message": None,
    }


def sim(events, liquids=()):
    return {"ok": True, "events": events, "liquids": list(liquids),
            "deck": {"labware": [], "modules": [], "pipettes": [], "trash": {"slot": "12"}}}


def liq(slot, well, ul, labware=TUBES):
    return {"slot": slot, "well": well, "labware": labware, "liquid": "x", "volume_ul": ul}


def rules(events, liquids=()):
    return check_physical_rules(sim(events, liquids)).counts


GOOD = [E(0, "pick_up_tip", "1", "A1", labware="tiprack"), E(1, "aspirate", "2", "A1", 50, TUBES),
        E(2, "dispense", "3", "A1", 50), E(3, "drop_tip", "12", None, labware="trash")]


def test_clean_run_has_no_violations():
    assert not any(rules(GOOD, [liq("2", "A1", 100)]).values())


def test_rule_tip_before_aspirate():
    c = rules([E(0, "aspirate", "2", "A1", 50, TUBES)], [liq("2", "A1", 100)])
    assert c["tip_before_aspirate"] == 1


def test_rule_no_overdispense():
    ev = [E(0, "pick_up_tip", "1", "A1", labware="tiprack"), E(1, "aspirate", "2", "A1", 20, TUBES),
          E(2, "dispense", "3", "A1", 50), E(3, "drop_tip")]
    assert rules(ev, [liq("2", "A1", 100)])["no_overdispense"] == 1


def test_rule_no_aspirate_from_empty_well():
    # nothing declared in the tube
    assert rules(GOOD)["no_aspirate_from_empty_well"] == 1
    # declared, but asked for more than it holds
    assert rules(GOOD, [liq("2", "A1", 30)])["no_aspirate_from_empty_well"] == 1


def test_reservoirs_are_ample_unless_declared():
    ev = [E(0, "pick_up_tip", labware="tiprack"), E(1, "aspirate", "5", "A1", 200, RES), E(2, "dispense", "3", "A1", 200), E(3, "drop_tip")]
    assert not any(rules(ev).values())  # undeclared reservoir = ample stock
    assert rules(ev, [liq("5", "A1", 100, RES)])["no_aspirate_from_empty_well"] == 1  # declared and tracked


def test_rule_tip_dropped_at_end():
    ev = [E(0, "pick_up_tip", labware="tiprack"), E(1, "aspirate", "2", "A1", 10, TUBES), E(2, "dispense", "3", "A1", 10)]
    assert rules(ev, [liq("2", "A1", 100)])["tip_dropped_at_end"] == 1


def test_picking_up_a_second_tip_is_reported():
    ev = [E(0, "pick_up_tip", labware="tiprack"), E(1, "pick_up_tip", labware="tiprack"), E(2, "drop_tip")]
    rep = check_physical_rules(sim(ev), {})
    assert any(v.rule == "tip_state" for v in rep.other)


@pytest.mark.parametrize(
    "name,events,bad",
    [
        ("multi-dispense from one source is fine",
         [E(0, "pick_up_tip"), E(1, "aspirate", "2", "A1", 30, TUBES), E(2, "dispense", "3", "A1", 10),
          E(3, "dispense", "3", "B1", 10), E(4, "dispense", "3", "C1", 10), E(5, "drop_tip")], False),
        ("source changes without a fresh tip",
         [E(0, "pick_up_tip"), E(1, "aspirate", "2", "A1", 10, TUBES), E(2, "dispense", "3", "A1", 10),
          E(3, "aspirate", "2", "A2", 10, TUBES), E(4, "dispense", "3", "B1", 10), E(5, "drop_tip")], True),
        ("tip that has drawn from a destination then visits another well",
         [E(0, "pick_up_tip"), E(1, "aspirate", "2", "A1", 20, TUBES), E(2, "dispense", "3", "A1", 20),
          E(3, "aspirate", "3", "A1", 10), E(4, "dispense", "3", "B1", 10), E(5, "drop_tip")], True),
        ("mixing in the source before drawing carries nothing",
         [E(0, "pick_up_tip"), E(1, "aspirate", "2", "A1", 10, TUBES), E(2, "dispense", "2", "A1", 10, TUBES),
          E(3, "aspirate", "2", "A1", 10, TUBES), E(4, "dispense", "3", "A1", 10), E(5, "drop_tip")], False),
        ("a fresh tip resets everything",
         [E(0, "pick_up_tip"), E(1, "aspirate", "2", "A1", 10, TUBES), E(2, "dispense", "3", "A1", 10), E(3, "drop_tip"),
          E(4, "pick_up_tip"), E(5, "aspirate", "2", "A2", 10, TUBES), E(6, "dispense", "3", "B1", 10), E(7, "drop_tip")], False),
    ],
)
def test_cross_contamination_cases(name, events, bad):
    assert bool(cross_contamination(events)) is bad, name


def test_multichannel_aspiration_empties_the_whole_column():
    ev = [E(0, "pick_up_tip", ch=8), E(1, "aspirate", "3", "A1", 60, ch=8), E(2, "dispense", "3", "A2", 60, ch=8), E(3, "drop_tip", ch=8),
          E(4, "pick_up_tip", ch=8), E(5, "aspirate", "3", "A1", 60, ch=8), E(6, "dispense", "3", "A2", 60, ch=8), E(7, "drop_tip", ch=8)]
    initial = [liq("3", f"{r}1", 100, PLATE) for r in "ABCDEFGH"]
    assert rules(ev, initial)["no_aspirate_from_empty_well"] == 1  # 100 uL cannot give 60 twice


# Found by comparing with Text2WetLab's own grader on its reference solutions and broken variants of them
# (docs/streamc_t2wl_agreement.md, tests/wetlab/test_t2wl_agreement.py).


def air_gap(idx, slot, well, vol, labware=PLATE, level=0):
    """The two log entries of pipette.air_gap(vol): the 'Air gap' command, and the aspirate that is its sub-step."""
    return [{**E(idx, "comment", level=level), "text": "Air gap"}, E(idx + 1, "aspirate", slot, well, vol, labware, level=level + 1)]


def test_an_air_gap_is_air_not_drawn_from_the_well_and_it_can_be_dispensed():
    ev = [E(0, "pick_up_tip", labware="tiprack"), E(1, "aspirate", "2", "A1", 50, TUBES), *air_gap(2, "2", "A1", 20, TUBES),
          E(4, "dispense", "3", "A1", 70), E(5, "drop_tip")]
    rep = check_physical_rules(sim(ev), {("2", "A1"): 60.0})
    # 10 uL are left in the tube and the 20 uL air gap did not come from it; 70 are in the tip and 70 are dispensed
    assert rep.counts == dict.fromkeys(RULES, 0)
    assert rep.aspirations == 1


def test_an_air_gap_above_a_destination_does_not_make_the_tip_carry_that_well():
    ev = [E(0, "pick_up_tip", labware="tiprack"), E(1, "aspirate", "2", "A1", 50, TUBES), E(2, "dispense", "3", "A1", 50),
          *air_gap(3, "3", "A1", 10), E(5, "aspirate", "2", "A1", 50, TUBES), E(6, "dispense", "3", "B1", 50), E(7, "drop_tip")]
    assert check_physical_rules(sim(ev), {("2", "A1"): 500.0}).counts["no_cross_contamination"] == 0


def test_a_comment_that_says_air_gap_does_not_hide_the_next_aspirate():
    note = {**E(1, "comment"), "text": "Air gap"}  # a user comment: no sub-step, so no air gap
    ev = [E(0, "pick_up_tip", labware="tiprack"), note, E(2, "aspirate", "2", "A1", 50, TUBES), E(3, "dispense", "3", "A1", 50), E(4, "drop_tip")]
    rep = check_physical_rules(sim(ev), {})
    assert rep.aspirations == 1 and rep.counts["no_aspirate_from_empty_well"] == 1


def test_leaf_events_marks_the_air_gap_aspirate_on_a_copy_and_final_volumes_ignore_it():
    ev = [E(0, "pick_up_tip", labware="tiprack"), E(1, "aspirate", "2", "A1", 50, TUBES), *air_gap(2, "2", "A1", 20, TUBES),
          E(4, "dispense", "3", "A1", 70), E(5, "drop_tip")]
    s = sim(ev)
    assert [e["kind"] for e in leaf_events(s)] == ["pick_up_tip", "aspirate", "air_gap", "dispense", "drop_tip"]
    assert [e["kind"] for e in s["events"]] == ["pick_up_tip", "aspirate", "comment", "aspirate", "dispense", "drop_tip"]
    assert final_volumes(s, {("2", "A1"): 60.0}) == {("2", "A1"): 10.0, ("3", "A1"): 70.0}


def test_dispensing_into_an_undeclared_reservoir_does_not_start_tracking_it():
    # mixing a reagent reservoir before drawing from it: 50 uL go in and out, then 200 are drawn from an ample stock
    ev = [E(0, "pick_up_tip", labware="tiprack"), E(1, "aspirate", "5", "A1", 50, RES), E(2, "dispense", "5", "A1", 50, RES),
          E(3, "aspirate", "5", "A1", 200, RES), E(4, "dispense", "3", "A1", 200), E(5, "drop_tip")]
    assert not any(rules(ev).values())
    assert rules(ev, [liq("5", "A1", 100, RES)])["no_aspirate_from_empty_well"] == 1  # declared: tracked, and 200 > 100


@pytest.mark.parametrize("extra,flagged", [(5e-7, 1), (5e-10, 0)])
def test_the_volume_rules_have_spec_checks_tolerance_of_1e_9_ul(extra, flagged):
    over = [E(0, "pick_up_tip", labware="tiprack"), E(1, "aspirate", "2", "A1", 100, TUBES), E(2, "dispense", "3", "A1", 100 + extra), E(3, "drop_tip")]
    dry = [E(0, "pick_up_tip", labware="tiprack"), E(1, "aspirate", "2", "A1", 100 + extra, TUBES), E(2, "dispense", "3", "A1", 100 + extra), E(3, "drop_tip")]
    assert check_physical_rules(sim(over), {("2", "A1"): 1000.0}).counts["no_overdispense"] == flagged
    assert check_physical_rules(sim(dry), {("2", "A1"): 100.0}).counts["no_aspirate_from_empty_well"] == flagged


def test_initial_from_liquids_reads_declared_contents():
    s = json.loads((FIXTURES / "sample_with_liquids.json").read_text(encoding="utf-8"))
    assert initial_from_liquids(s) == {("2", "A1"): 500.0}


# --------------------------------------------------------------------------- lint / structure


@pytest.mark.parametrize(
    "src",
    [
        "import os\n", "import subprocess\n", "from os import path\n", "from . import x\n",
        "x = open('f')\n", "x = __import__('os')\n", "exec('1')\n", "eval('1')\n", "y = getattr(p, 'a')\n",
        "p._core\n", "ctx.foo = 1\n", "p.system\n", "type(1)\n",
    ],
)
def test_lint_refuses_dangerous_code(src):
    assert static_lint(src), src


def test_lint_allows_ordinary_protocol_code():
    ok = (
        "from opentrons import protocol_api\nimport math\n"
        "requirements = {'robotType': 'OT-2', 'apiLevel': '2.19'}\n"
        "def run(ctx):\n    p = ctx.load_instrument('p300_single_gen2', 'right')\n"
        "    p.flow_rate.aspirate = 50\n    p.well_bottom_clearance.dispense = 2\n    ctx.comment(str(math.ceil(2.1)))\n"
    )
    assert static_lint(ok) == []
    assert static_lint("def run(:\n") and "syntax error" in static_lint("def run(:\n")[0]


def test_structure_findings():
    assert not structure_findings('requirements = {"apiLevel": "2.19"}\ndef run(ctx):\n    pass\n')
    msgs = " ".join(f.message for f in structure_findings('requirements = {"apiLevel": "2.20"}\ndef go():\n    pass\n'))
    assert "run(ctx)" in msgs and "2.20" in msgs
    assert "apiLevel" in " ".join(f.message for f in structure_findings("def run(ctx):\n    pass\n"))


# --------------------------------------------------------------------------- reference diff


def test_reference_diff_identical_and_different():
    a = json.loads((FIXTURES / "sample_all_kinds.json").read_text(encoding="utf-8"))
    d = reference_diff(a, a)
    assert d["labware_mismatches"] == 0 and d["volume_abs_diff_ul"] == 0 and d["step_order_edit_distance"] == 0
    b = json.loads(json.dumps(a))
    for e in b["events"]:
        if e["kind"] == "dispense" and e["volume_ul"] == 5.0:
            e["volume_ul"] = 7.0
            break
    b["deck"]["labware"] = b["deck"]["labware"][:-1]
    d2 = reference_diff(b, a)
    assert d2["volume_abs_diff_ul"] > 0 and d2["wells_with_different_volume"] >= 1
    assert d2["labware_mismatches"] == 1 and d2["step_order_edit_distance"] >= 1 and 0 < d2["step_order_normalised"] <= 1


def test_actual_dispensed_ignores_mix_children_and_expands_multichannel():
    a = json.loads((FIXTURES / "sample_all_kinds.json").read_text(encoding="utf-8"))
    got = actual_dispensed(a)
    assert got[("3", "A1")] == 5.0  # the 3x10 uL mix that follows is NOT counted
    assert got[("3", "A2")] == 5.0 and got[("3", "A3")] == 5.0  # distribute
    assert all(got[("6", f"{r}1")] == 100.0 for r in "ABCDEFGH")  # 8-channel transfer, mix_after not counted
    assert final_volumes(a)[("6", "H1")] == 100.0


# --------------------------------------------------------------------------- simulation error summaries
# `summarize_exception` condenses an Opentrons engine error (whose repr embeds a UUID, a timestamp and
# local paths) into (kind, one clean line). The strings below have the shape the real engine produces.

BS = chr(92)  # a backslash, built here so that no string literal below needs escaping
UUID_OUTER, UUID_INNER = "21d189b3-a0fc-4048-8cda-f021d5496c99", "c36c8545-bb49-4c5a-92f3-b11eb30b7d88"
STAMP = "datetime.datetime(2026, 11, 14, 16, 19, 33, 378575, tzinfo=datetime.timezone.utc)"
WIN_TRACEBACK = '  File "C:' + BS + "Users" + BS + "Someone" + BS + "proj" + BS + 'protocol.py", line 48, in run ...'


def occurrence(error_type, detail=None, *, info="{}", inner=None, uuid=UUID_OUTER):
    parts = [f"id='{uuid}'", f"createdAt={STAMP}", "errorCode='4000'", f"errorType='{error_type}'"]
    if detail is not None:
        parts.append(f"detail={detail!r}")  # repr, like the engine: double quotes when the text has an apostrophe
    parts += [f"errorInfo={info}", f"wrappedErrors=[{inner or ''}]"]
    return "ErrorOccurrence(" + ", ".join(parts) + ")"


def engine_error(*occurrences):
    return Exception("[" + ", ".join(occurrences) + "]")


def assert_clean(msg):
    """No UUID, timestamp, local path or line break survives in a summarised message."""
    assert "\n" not in msg and msg == msg.strip()
    assert not re.search(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}", msg)
    assert "datetime" not in msg and not re.search(r"\d{4}-\d{2}-\d{2}", msg)
    assert not any(bad in msg for bad in ("C:", "Users", "Someone", "/home/", "/tmp/"))


def test_python_error_in_the_protocol_is_reported_as_its_own_class():
    inner = occurrence("PythonException", "TypeError: 'TrashBin' object is not subscriptable",
                       info="{'traceback': " + repr(WIN_TRACEBACK) + "}", uuid=UUID_INNER)
    outer = occurrence("ExceptionInProtocolError", "TypeError [line 48]: 'TrashBin' object is not subscriptable", inner=inner)
    kind, msg = summarize_exception(engine_error(outer))
    assert kind == "TypeError"  # not a generic wrapper such as PythonException / ExceptionInProtocolError
    assert msg == "TypeError [line 48]: 'TrashBin' object is not subscriptable"


def test_a_specific_engine_error_is_reported_by_name():
    err = engine_error(occurrence("TipNotAttachedError", "Pipette should have a tip attached"))
    assert summarize_exception(err) == ("TipNotAttachedError", "Pipette should have a tip attached")


def test_the_innermost_engine_error_wins_over_its_wrappers():
    # What the real engine reports for `pipette.aspirate(...)` with no tip: three nested occurrences.
    tip = occurrence("TipNotAttachedError", "Pipette should have a tip attached, but does not.", uuid=UUID_INNER)
    failed = occurrence("ProtocolCommandFailedError", "TipNotAttachedError: Pipette should have a tip attached, but does not.", inner=tip)
    outer = occurrence("ExceptionInProtocolError", "ProtocolCommandFailedError [line 8]: Error 4000 GENERAL_ERROR "
                       "(ProtocolCommandFailedError): TipNotAttachedError: Pipette should have a tip attached, but does not.", inner=failed)
    kind, msg = summarize_exception(engine_error(outer))
    assert kind == "TipNotAttachedError"
    assert "Pipette should have a tip attached" in msg and "[line 8]" in msg
    assert_clean(msg)


def test_a_plain_exception_keeps_its_class_and_message():
    kind, msg = summarize_exception(KeyError("Z99"))
    assert kind == "KeyError" and "Z99" in msg
    assert summarize_exception(KeyError()) == ("KeyError", "KeyError")  # no message: fall back to the class name


def test_uuids_timestamps_and_local_paths_are_scrubbed_from_a_detail():
    dirty = f"failed at 2026-11-14T16:19:33.378575+00:00 and {STAMP} for run {UUID_OUTER} while reading C:" + BS + "Users" + BS + "Someone" + BS + "data.json"
    kind, msg = summarize_exception(engine_error(occurrence("UnexpectedProtocolError", "ValueError: " + dirty)))
    assert kind == "ValueError"
    assert msg == "ValueError: failed at <time> and <time> for run <id> while reading <path>"
    assert_clean(msg)


def test_the_raw_repr_fallback_is_scrubbed_too():
    # No `detail` anywhere: the message falls back to the raw repr, which holds a UUID, a datetime and a traceback path.
    kind, msg = summarize_exception(engine_error(occurrence("PythonException", info="{'traceback': " + repr(WIN_TRACEBACK) + "}")))
    assert kind == "PythonException"  # nothing more specific was reported
    assert "<id>" in msg and "<time>" in msg and "<path>" in msg
    assert_clean(msg)


def test_posix_paths_are_scrubbed_but_labware_uris_are_left_alone():
    assert summarize_exception(RuntimeError("cannot read '/home/ci/work/data.csv' or /tmp/vaxforge_sim_1/protocol.py")) == (
        "RuntimeError", "cannot read '<path>' or <path>")
    uri = "opentrons/nest_96_wellplate_200ul_flat/3 is not loaded in slot A1/B1"
    assert summarize_exception(RuntimeError(uri)) == ("RuntimeError", uri)


def test_a_multi_line_detail_becomes_one_line():
    raw = "ErrorOccurrence(errorType='UnexpectedProtocolError', detail=" + '"ValueError: two' + chr(10) + "   lines" + '")'
    assert summarize_exception(Exception(raw)) == ("ValueError", "ValueError: two lines")


# --------------------------------------------------------------------------- real simulations

HEAD = '''
from opentrons import protocol_api
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}
def run(ctx: protocol_api.ProtocolContext):
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")
    plate = ctx.load_labware("nest_96_wellplate_200ul_flat", "3")
    p = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])
    water = ctx.define_liquid(name="water", description="d", display_color="#33AAFF")
    tubes["A1"].load_liquid(liquid=water, volume=500)
    tubes["A2"].load_liquid(liquid=water, volume=500)
'''


def body(*lines):
    return HEAD + "".join(f"    {l}\n" for l in lines)


@needs_ot
def test_a_correct_protocol_passes():
    g, s = grade_source(body('p.transfer(50, tubes["A1"], plate["A1"])'))
    assert g.verdict == "PASS" and g.sim_ok and g.n_rule_violations == 0 and s["opentrons_version"] == "7.5.0"


@needs_ot
def test_tip_left_on_fails():
    g, _ = grade_source(body("p.pick_up_tip()", 'p.aspirate(50, tubes["A1"])', 'p.dispense(50, plate["A1"])'))
    assert g.verdict == "FAIL" and g.rule_counts["tip_dropped_at_end"] == 1


@needs_ot
def test_aspirating_from_an_undeclared_well_fails():
    g, _ = grade_source(body('p.transfer(50, tubes["B1"], plate["A1"])'))
    assert g.verdict == "FAIL" and g.rule_counts["no_aspirate_from_empty_well"] >= 1


@needs_ot
def test_reusing_a_tip_across_sources_fails_cross_contamination():
    g, _ = grade_source(body("p.pick_up_tip()", 'p.aspirate(20, tubes["A1"])', 'p.dispense(20, plate["A1"])',
                             'p.aspirate(20, tubes["A2"])', 'p.dispense(20, plate["B1"])', "p.drop_tip()"))
    assert g.verdict == "FAIL" and g.rule_counts["no_cross_contamination"] >= 1


@needs_ot
def test_aspirating_without_a_tip_fails_in_the_simulator():
    g, _ = grade_source(body('p.aspirate(50, tubes["A1"])'))
    assert g.verdict == "FAIL" and g.sim_ok is False and g.sim_error
    assert g.sim_error_kind == "TipNotAttachedError"  # the engine wraps it twice; the grade names the real error
    assert_clean(g.sim_error)


@needs_ot
def test_lint_failure_never_reaches_the_simulator():
    g, s = grade_source("import os\n" + body('p.transfer(50, tubes["A1"], plate["A1"])'))
    assert g.verdict == "FAIL" and g.lint and g.sim_ok is None and s is None
    r = simulate("import os\n")
    assert r["ok"] is False and r["error"]["type"] == "LintError"


@needs_ot
def test_a_syntactically_fine_protocol_that_crashes_is_reported_not_raised():
    g, _ = grade_source(body('plate["Z99"]'))
    assert g.verdict == "FAIL" and g.sim_ok is False
    assert g.sim_error_kind == "KeyError" and "Z99" in g.sim_error


@needs_ot
def test_a_python_error_inside_run_is_graded_by_its_class_with_a_clean_message():
    # The mistake a model really made: the OT-2 trash is implicit at apiLevel >= 2.16, and `ctx.fixed_trash`
    # is not subscriptable. The engine reports it as ExceptionInProtocolError > PythonException.
    g, s = grade_source(body("p.pick_up_tip()", 'p.drop_tip(ctx.fixed_trash["A1"])'))
    assert g.verdict == "FAIL" and g.sim_ok is False
    assert g.sim_error_kind == "TypeError" and s["error"]["type"] == "TypeError"
    assert g.sim_error.startswith("TypeError: ") and "TrashBin" in g.sim_error
    assert_clean(g.sim_error)
    assert_clean(s["error"]["message"])


# Three modules, loaded by the gen-1 names that every apiLevel from 2.0 on accepts (the apiLevel is put in front by `modules_protocol`).
MODULES_BODY = '''
def run(ctx):
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", 1)
    src = ctx.load_labware("nest_12_reservoir_15ml", 2)
    p = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])
    temp = ctx.load_module("tempdeck", 3)
    plate = temp.load_labware("nest_96_wellplate_100ul_pcr_full_skirt")
    mag = ctx.load_module("magdeck", 6)
    mag.load_labware("nest_96_wellplate_2ml_deep")
    tc = ctx.load_module("thermocycler")
    temp.set_temperature(4)
    p.transfer(20, src["A1"], plate["A1"])
    temp.deactivate()
    mag.engage()
    mag.disengage()
    tc.open_lid()
'''


def modules_protocol(api_level):
    return f'metadata = {{"apiLevel": "{api_level}"}}\n' + MODULES_BODY


@needs_ot
@pytest.mark.parametrize("api_level", ["2.0", "2.13"])
def test_a_protocol_that_loads_modules_simulates_below_api_2_14(api_level):
    # ModuleContext.model exists only from apiLevel 2.14. Below that Opentrons raises APIVersionError, which is not an
    # AttributeError, so `getattr(module, "model", "")` did not return its default and the runner reported ok=false for every
    # protocol that loads a module - one that Opentrons itself simulates without complaint.
    sim = simulate(modules_protocol(api_level))
    assert sim["ok"] is True, sim["error"]
    assert sim["api_level"] == api_level
    # all three modules are on the deck, in their slots; the model is absent at this level, it is not made up
    assert sorted((m["slot"], m["model"]) for m in sim["deck"]["modules"]) == [("3", ""), ("6", ""), ("7", "")]
    assert {lw["slot"] for lw in sim["deck"]["labware"] if lw["module"]} == {"3", "6"}  # the two labware that sit on a module
    # their events are reported ...
    assert {"temperature_set", "temperature_deactivate", "magnet_engage", "magnet_disengage"} <= {e["kind"] for e in sim["events"]}
    assert "Opening Thermocycler lid" in [e["text"] for e in sim["events"]]
    # ... and pipetting into the plate on the temperature module is placed in that module's slot
    moves = [(e["kind"], e["target"]["slot"], e["target"]["well"]) for e in sim["events"] if e["kind"] in ("aspirate", "dispense")]
    assert moves == [("aspirate", "2", "A1"), ("dispense", "3", "A1")]


@needs_ot
def test_modules_keep_their_names_and_models_from_api_2_14():
    # The other side of the fix above: from apiLevel 2.14 nothing changed, the model is read and names the module.
    sim = simulate(modules_protocol("2.14"))
    assert sim["ok"] is True, sim["error"]
    assert sorted((m["slot"], m["name"], m["model"]) for m in sim["deck"]["modules"]) == [
        ("3", "temperature module", "temperatureModuleV1"),
        ("6", "magnetic module", "magneticModuleV1"),
        ("7", "thermocycler module", "thermocyclerModuleV1"),
    ]


@needs_ot
def test_the_runner_is_not_shadowed_by_modules_that_sit_next_to_it(tmp_path):
    # The runner is started by path, so its own directory is first on sys.path. Stream A added src/vaxforge/http.py, which
    # hid the standard library's `http`: every simulation died on `import http.client` inside opentrons ("'http' is not a package").
    runner = tmp_path / "_ot_sim_runner.py"
    shutil.copy(_ot_sim_runner.__file__, runner)
    (tmp_path / "http.py").write_text("raise ImportError('shadowed the standard library')\n", encoding="utf-8")
    protocol, result = tmp_path / "protocol.py", tmp_path / "result.json"
    protocol.write_text(body('p.transfer(50, tubes["A1"], plate["A1"])'), encoding="utf-8")
    proc = subprocess.run([find_ot_python(), str(runner), str(protocol), str(result)], capture_output=True, text=True, timeout=300, cwd=tmp_path)
    assert result.exists(), proc.stderr[-400:]
    sim = json.loads(result.read_text(encoding="utf-8"))
    assert sim["ok"] is True, sim["error"]


def test_importing_the_runner_does_not_touch_sys_path():
    before = list(sys.path)
    importlib.reload(_ot_sim_runner)
    assert sys.path == before  # the clean-up runs only when the file is executed as a script


def test_missing_simulator_is_unverified_not_a_crash():
    r = simulate(body('p.transfer(1, tubes["A1"], plate["A1"])'), ot_python="definitely-not-a-python")
    assert r["ok"] is False and r["error"]["type"] == "SimulatorUnavailable"
    g, s = grade_source(body('p.transfer(5, tubes["A1"], plate["A1"])'), run_simulation=False)
    assert g.verdict == "UNVERIFIED" and s is None


def test_rule_names_are_the_five_text2wetlab_rules():
    assert RULES == ("tip_before_aspirate", "no_overdispense", "no_aspirate_from_empty_well", "tip_dropped_at_end", "no_cross_contamination")


# --------------------------------------------------------------------------- simulator environment scrub
# The simulator subprocess EXECUTES model-written code, so the parent's secrets must not be in its environment.
# Every value below is a dummy: nothing here reads, prints or stores a real credential.

SECRET_NAMES = [  # one realistic name per marker (KEY TOKEN SECRET PASSWORD PASSWD CREDENTIAL ANTHROPIC OPENAI AWS_ AZURE_ GITHUB GH_)
    "ANTHROPIC_API_KEY", "ANTHROPIC_BASE_URL", "OPENAI_ORG_ID", "AWS_REGION", "AZURE_TENANT_ID", "GITHUB_ACTOR", "GH_HOST",
    "MY_SERVICE_KEY", "SOME_TOKEN", "DB_SECRET", "DB_PASSWORD", "DB_PASSWD", "GOOGLE_APPLICATION_CREDENTIALS",
    "hf_token", "Api_Key",  # matching ignores case
]
ESSENTIAL_NAMES = [
    "PATH", "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "COMSPEC", "PATHEXT", "TEMP", "TMP", "HOME", "USERPROFILE", "APPDATA",
    "LOCALAPPDATA", "ProgramFiles(x86)", "LANG", "PYTHONPATH", "VAXFORGE_OT_PYTHON",
]


@pytest.mark.parametrize("name", SECRET_NAMES)
def test_scrubbed_env_drops_every_kind_of_secret(name):
    env = _scrubbed_env({name: "dummy-not-a-real-secret", "PATH": "a-path"})
    assert name not in env and "dummy-not-a-real-secret" not in env.values()
    assert env["PATH"] == "a-path"


def test_scrubbed_env_keeps_what_python_and_opentrons_need():
    base = {name: f"value-of-{name}" for name in ESSENTIAL_NAMES}
    env = _scrubbed_env({**base, "ANTHROPIC_API_KEY": "dummy", "GITHUB_TOKEN": "dummy"})
    assert env == {**base, "PYTHONIOENCODING": "utf-8"}  # nothing else dropped, nothing invented


def test_scrubbed_env_forces_utf8_stdio():
    assert _scrubbed_env({})["PYTHONIOENCODING"] == "utf-8"
    assert _scrubbed_env({"PYTHONIOENCODING": "latin-1"})["PYTHONIOENCODING"] == "utf-8"


def test_scrubbed_env_does_not_modify_its_input():
    base = {"PATH": "a-path", "GITHUB_TOKEN": "dummy", "PYTHONIOENCODING": "latin-1"}
    snapshot = dict(base)
    env = _scrubbed_env(base)
    assert base == snapshot  # nothing dropped from it, nothing overwritten in it
    env["PATH"] = "changed"
    assert base["PATH"] == "a-path"  # the result is a copy
    assert _scrubbed_env(MappingProxyType({"PATH": "p", "GH_TOKEN": "dummy"})) == {"PATH": "p", "PYTHONIOENCODING": "utf-8"}


def test_scrubbed_env_defaults_to_the_process_environment_and_leaves_it_alone(monkeypatch):
    monkeypatch.setenv("VAXFORGE_TEST_API_KEY", "dummy-secret-value")
    monkeypatch.setenv("VAXFORGE_TEST_PLAIN", "plain")
    env = _scrubbed_env()
    assert "VAXFORGE_TEST_API_KEY" not in env and env["VAXFORGE_TEST_PLAIN"] == "plain"
    assert os.environ["VAXFORGE_TEST_API_KEY"] == "dummy-secret-value"  # the parent keeps its own variables


def test_simulate_starts_the_runner_with_the_scrubbed_environment(monkeypatch):
    monkeypatch.setenv("VAXFORGE_TEST_API_KEY", "dummy-secret-value")
    monkeypatch.setenv("VAXFORGE_TEST_PLAIN", "plain")
    seen = {}

    def fake_run(cmd, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(cmd, 1, "", "")  # no result file, as if the runner had died

    monkeypatch.setattr(subprocess, "run", fake_run)
    result = simulate(body('p.transfer(1, tubes["A1"], plate["A1"])'), ot_python="python-for-this-test")
    assert result["error"]["type"] == "RunnerError"  # the fake wrote nothing
    env = seen["env"]
    assert "VAXFORGE_TEST_API_KEY" not in env and env["VAXFORGE_TEST_PLAIN"] == "plain" and env["PYTHONIOENCODING"] == "utf-8"


CANARY = '''
import os
from opentrons import protocol_api
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}
def run(ctx: protocol_api.ProtocolContext):
    ctx.comment("secret=" + os.environ.get("VAXFORGE_TEST_API_KEY", "ABSENT"))
    ctx.comment("plain=" + os.environ.get("VAXFORGE_TEST_PLAIN", "ABSENT"))
'''


@needs_ot
def test_code_run_by_the_simulator_cannot_see_the_parents_secrets(monkeypatch):
    monkeypatch.setenv("VAXFORGE_TEST_API_KEY", "dummy-secret-value")
    monkeypatch.setenv("VAXFORGE_TEST_PLAIN", "plain")
    s = simulate(CANARY, lint=False)  # lint=False only so that this probe may import os; real attempts are linted
    assert s["ok"], s["error"]
    comments = [e["message"] for e in s["events"] if e["kind"] == "comment"]
    assert "secret=ABSENT" in comments  # a leak would read secret=dummy-secret-value
    assert "plain=plain" in comments  # and the rest of the environment does arrive, so the probe can see variables at all
