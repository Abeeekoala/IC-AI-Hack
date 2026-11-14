"""Tests for S9+ (protocol animation): sim JSON -> timeline.json -> self-contained HTML player.

The timeline's numbers are re-derived here straight from the simulator events (tip counts, per-well net
volumes, module state) without reusing the module's own footprint code, so a bug in either side shows up.
"""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from vaxforge import s9plus_animate as anim

HERE = Path(__file__).resolve().parent
SAMPLE = HERE.parent / "fixtures" / "ot_sim" / "sample_all_kinds.json"
DEMO_DIR = HERE.parent / "fixtures" / "animation_demo"
ROWS = "ABCDEFGH"


# --------------------------------------------------------------------------------------
# fixtures and helpers
# --------------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def sim() -> dict:
    return json.loads(SAMPLE.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def tl(sim) -> dict:
    return anim.build_timeline(sim)


@pytest.fixture(scope="module")
def html(tl) -> str:
    return anim.render_html(tl)


def embedded_json(page: str) -> dict:
    m = re.search(r'<script type="application/json" id="vf-timeline">(.*?)</script>', page, re.S)
    assert m, "timeline JSON block not found"
    return json.loads(m.group(1))


def player_script(page: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 1
    return scripts[0]


def labware_of(sim: dict, slot: str) -> dict:
    return next(lw for lw in sim["deck"]["labware"] if lw["slot"] == slot)


def footprint(sim: dict, slot: str, well: str, channels: int) -> dict[str, int]:
    """Wells under the nozzles, derived independently of the module under test."""
    if channels == 1:
        return {well: 1}
    lw = labware_of(sim, slot)
    if len({name[0] for name in lw["wells"]}) == 1:  # single-row labware (reservoir): all channels share the well
        return {well: channels}
    col, start = well[1:], ROWS.index(well[0])
    return {f"{r}{col}": 1 for r in ROWS[start:start + channels] if f"{r}{col}" in lw["wells"]}


def expected_net_after_each_event(sim: dict) -> list[dict[str, float]]:
    net: dict[str, float] = {}
    out = []
    for ev in sim["events"]:
        if ev["kind"] in ("aspirate", "dispense") and ev["target"] and ev["target"]["well"]:
            sign = -1.0 if ev["kind"] == "aspirate" else 1.0
            ch = ev["pipette"]["channels"]
            for w, cnt in footprint(sim, ev["target"]["slot"], ev["target"]["well"], ch).items():
                key = f"{ev['target']['slot']}:{w}"
                net[key] = net.get(key, 0.0) + sign * cnt * ev["volume_ul"]
        out.append(dict(net))
    return out


def plate_wells(x0: float, y0: float) -> dict:
    """96 round wells, A1 at (x0, y0), 9 mm pitch, rows A..H towards the front (decreasing y)."""
    return {
        f"{ROWS[r]}{c + 1}": {"x": x0 + 9 * c, "y": y0 - 9 * r, "z_top": 10.0, "depth": 10.0, "shape": "circular",
                               "diameter": 6.0, "length": None, "width": None, "max_volume_ul": 360.0}
        for r in range(8) for c in range(12)
    }


def blank_event(idx: int, kind: str, text: str = "", **kw) -> dict:
    ev = {"idx": idx, "level": 0, "kind": kind, "is_group": kind in ("transfer", "distribute", "consolidate", "mix"),
          "text": text, "pipette": None, "volume_ul": None, "flow_rate_ul_s": None, "target": None, "source": None,
          "dest": None, "celsius": None, "seconds": None, "repetitions": None, "message": None}
    ev.update(kw)
    return ev


def tiny_sim(events: list) -> dict:
    """The small synthetic sim for edge cases: one labware (96-well plate, slot 2), one 1-channel pipette."""
    return {
        "schema": "vaxforge.ot_sim/1", "ok": True, "error": None, "opentrons_version": "7.5.0",
        "protocol_file": "tiny_protocol.py", "api_level": "2.19",
        "deck": {
            "labware": [{
                "slot": "2", "load_name": "corning_96_wellplate_360ul_flat",
                "display_name": "Corning 96 Well Plate 360 uL Flat", "uri": "opentrons/corning_96_wellplate_360ul_flat/2",
                "module": None, "kind": "plate", "origin_mm": [146.88, 74.24, 14.2],
                "wells": plate_wells(146.88, 74.24),
            }],
            "modules": [],
            "pipettes": [{"mount": "left", "name": "p300_single_gen2", "channels": 1,
                          "min_volume_ul": 20.0, "max_volume_ul": 300.0}],
            "trash": {"slot": "12"},
        },
        "events": events,
    }


def target(slot: str, well: str, x: float, y: float) -> dict:
    return {"slot": slot, "well": well, "labware": "corning_96_wellplate_360ul_flat", "point_mm": [x, y, 14.2]}


PIP1 = {"mount": "left", "name": "p300_single_gen2", "channels": 1}


def annotated_sim(sample: dict) -> dict:
    """The sample protocol with the S9 annotation convention hand-inserted (used by tests and the demo)."""
    out = copy.deepcopy(sample)
    new: list[dict] = []
    for ev in out["events"]:
        if ev["idx"] == 0:
            new.append(blank_event(0, "comment", message="[s03] Reagent prep | ROBOT | robot adds buffer to the plate"))
            new.append(blank_event(0, "comment", message="[s04] Bogus owner | MAYBE | stays a plain caption"))
            continue
        if ev["idx"] == 14:
            new.append(blank_event(0, "comment",
                                   message="[s07] Temperature step | ROBOT-PARTIAL | heating is robot, checks are manual"))
        if ev["idx"] == 21:
            new.append(blank_event(0, "comment", message="[s09] LAL assay plate setup | ROBOT-PARTIAL | reading needs a plate reader"))
        if ev["kind"] == "pause":
            new.append(blank_event(0, "pause", "Pausing robot operation: HUMAN [s09]: centrifuge the plate, then resume",
                                   message="HUMAN [s09]: centrifuge the plate, then resume"))
            new.append(blank_event(0, "comment", message="[s11] THP-1 culture | HUMAN | CO2 incubator and sterile handling are manual"))
            new.append(blank_event(0, "pause", "Pausing robot operation: HUMAN [s11]: seed THP-1 cells and return the plate",
                                   message="HUMAN [s11]: seed THP-1 cells and return the plate"))
            continue
        new.append(ev)
    for i, ev in enumerate(new):
        ev["idx"] = i
    out["events"] = new
    return out


# --------------------------------------------------------------------------------------
# deck geometry
# --------------------------------------------------------------------------------------
def test_deck_geometry_matches_the_sample(tl):
    deck = tl["deck"]
    assert [s["slot"] for s in deck["slots"]] == [str(i) for i in range(1, 13)]
    origins = {s["slot"]: (s["x"], s["y"]) for s in deck["slots"]}
    assert origins["1"] == (0.0, 0.0) and origins["3"] == (265.0, 0.0)
    assert origins["10"] == (0.0, 271.5) and origins["12"] == (265.0, 271.5)
    assert all(s["w"] == 127.76 and s["h"] == 85.48 for s in deck["slots"])
    assert deck["trash"]["slot"] == "12" and next(s for s in deck["slots"] if s["slot"] == "12")["kind"] == "trash"
    rack = next(lw for lw in deck["labware"] if lw["slot"] == "1")
    assert (rack["wells"]["A1"]["x"], rack["wells"]["A1"]["y"]) == (14.38, 74.24)
    assert rack["rect"] == [0.0, 0.0, 127.76, 85.48]
    res = next(lw for lw in deck["labware"] if lw["kind"] == "reservoir")
    assert res["wells"]["A1"]["shape"] == "rect" and (res["wells"]["A1"]["w"], res["wells"]["A1"]["h"]) == (8.2, 71.2)
    assert {m["type"] for m in deck["modules"]} == {"temperature", "magnetic"}
    eight = next(p for p in deck["pipettes"] if p["channels"] == 8)
    assert eight["channel_offsets_y"] == [31.5, 22.5, 13.5, 4.5, -4.5, -13.5, -22.5, -31.5]


def test_one_frame_per_event_in_order(sim, tl):
    assert len(tl["frames"]) == len(sim["events"])
    for ev, f in zip(sim["events"], tl["frames"]):
        assert f["event_idx"] == ev["idx"] and f["kind"] == ev["kind"] and f["level"] == ev["level"]
        assert f["is_group"] == ev["is_group"]
    assert tl["schema"] == "vaxforge.timeline/1" and tl["ok"] is True and tl["error"] is None
    assert set(tl) == {"schema", "ok", "error", "source", "time_model", "deck", "initial_state", "frames",
                       "phases", "totals", "warnings"}
    # group children point at their header
    by_idx = {f["event_idx"]: f for f in tl["frames"]}
    assert by_idx[5]["parent"] == by_idx[4]["index"]          # mix strokes under the mix header
    assert by_idx[36]["parent"] == by_idx[34]["index"]        # primitives under the nested transfer
    assert by_idx[34]["parent"] == by_idx[33]["index"]        # transfer under distribute
    assert by_idx[33]["parent"] is None


# --------------------------------------------------------------------------------------
# time model
# --------------------------------------------------------------------------------------
def test_times_are_non_decreasing_and_total_positive(tl):
    frames = tl["frames"]
    assert tl["totals"]["estimated_total_s"] > 0
    prev_end = 0.0
    for f in frames:
        assert f["start_s"] == prev_end                      # no gaps, no overlaps
        assert f["end_s"] >= f["start_s"]
        prev_end = f["end_s"]
    assert tl["totals"]["estimated_total_s"] == frames[-1]["end_s"]
    for f in frames:
        if not f["human"]:
            assert f["duration_s"] == pytest.approx(f["end_s"] - f["start_s"], abs=0.002)
            assert f["duration_s"] == pytest.approx(f["move_s"] + f["action_s"], abs=0.002)


def test_pause_frames_have_no_duration(sim, tl):
    pauses = [f for f in tl["frames"] if f["kind"] == "pause"]
    assert len(pauses) == sum(1 for e in sim["events"] if e["kind"] == "pause") >= 1
    for f in pauses:
        assert f["human"] is True and f["duration_s"] is None and f["start_s"] == f["end_s"]
        assert f["move_s"] == 0 and f["action_s"] == 0
        assert "HUMAN STEP" in f["caption"]
    assert tl["totals"]["human_pauses"] == len(pauses)
    assert all(not f["human"] for f in tl["frames"] if f["kind"] != "pause")


def test_time_model_is_declared_and_used(sim, tl):
    tm = tl["time_model"]
    assert tm["estimate_only"] is True and "not measured on a robot" in tm["headline"]
    assert tm["constants"] == anim.TIME_MODEL and tm["rules"]
    c = anim.TIME_MODEL
    by_idx = {f["event_idx"]: f for f in tl["frames"]}
    ev = {e["idx"]: e for e in sim["events"]}
    # fixed seconds
    assert by_idx[1]["action_s"] == c["pick_up_tip_s"] and by_idx[13]["action_s"] == c["drop_tip_s"]
    assert by_idx[11]["action_s"] == c["blow_out_s"] and by_idx[12]["action_s"] == c["touch_tip_s"]
    assert by_idx[14]["action_s"] == c["temperature_set_s"]
    # delays are exact; aspirate = volume / reported flow rate
    assert by_idx[16]["duration_s"] == 45.0 and by_idx[19]["duration_s"] == 120.0
    assert by_idx[2]["action_s"] == pytest.approx(ev[2]["volume_ul"] / ev[2]["flow_rate_ul_s"], abs=0.001)
    # gantry travel = straight-line distance / constant speed (first move starts at home)
    hx, hy = c["home_xy_mm"]
    x, y = ev[1]["target"]["point_mm"][:2]
    assert by_idx[1]["move_s"] == pytest.approx(((x - hx) ** 2 + (y - hy) ** 2) ** 0.5 / c["gantry_speed_mm_s"], abs=0.002)
    # nothing is counted twice: group headers carry no time, no volume
    for f in tl["frames"]:
        if f["is_group"]:
            assert f["duration_s"] == 0 and f["volume_ul"] is None and f["well_net_ul"] == {}


def test_default_flow_rate_used_only_when_the_sim_reports_none():
    ev = [
        blank_event(0, "aspirate", pipette=PIP1, volume_ul=50.0, flow_rate_ul_s=None, target=target("2", "A1", 146.88, 74.24)),
        blank_event(1, "dispense", pipette=PIP1, volume_ul=50.0, flow_rate_ul_s=100.0, target=target("2", "B1", 146.88, 65.24)),
    ]
    t = anim.build_timeline(tiny_sim(ev))
    assert t["frames"][0]["action_s"] == pytest.approx(50.0 / anim.TIME_MODEL["default_flow_rate_ul_s"], abs=0.001)
    assert t["frames"][1]["action_s"] == pytest.approx(0.5, abs=0.001)
    assert "default" in t["frames"][0]["detail"] and "default" not in t["frames"][1]["detail"]


# --------------------------------------------------------------------------------------
# tips, volumes, modules (independent recomputation)
# --------------------------------------------------------------------------------------
def test_tip_consumption_matches_pick_up_events(sim, tl):
    picks = [e for e in sim["events"] if e["kind"] == "pick_up_tip"]
    assert len(picks) == 3
    expected_total = sum(e["pipette"]["channels"] for e in picks)
    assert expected_total == 1 + 8 + 1
    assert tl["totals"]["tip_pickups"] == 3 and tl["totals"]["tips_used"] == expected_total
    frames = {f["event_idx"]: f for f in tl["frames"]}
    for e in picks:
        got = frames[e["idx"]]["tips_consumed"]
        col = e["target"]["well"][1:]
        want = [f"{e['target']['slot']}:{r}{col}" for r in ROWS[ROWS.index(e["target"]["well"][0]):][:e["pipette"]["channels"]]]
        assert got == want
    assert len(frames[22]["tips_consumed"]) == 8                       # the multi-channel pick-up takes a whole column
    assert tl["totals"]["tips_consumed_by_rack"] == {"1": ["A1", "B1"], "2": [f"{r}1" for r in ROWS]}
    running = [f["tips_used_total"] for f in tl["frames"]]
    assert running == sorted(running) and running[-1] == expected_total
    assert sum(len(f["tips_consumed"]) for f in tl["frames"]) == expected_total
    # tip attached follows pick-up / drop
    attached = {f["event_idx"]: f["pipettes"] for f in tl["frames"]}
    assert attached[1]["left"]["tip"] is True and attached[13]["left"]["tip"] is False
    assert attached[22]["right"]["tip"] is True and attached[32]["right"]["tip"] is False


def test_per_well_net_change_matches_the_events(sim, tl):
    expected = expected_net_after_each_event(sim)
    cumulative: dict[str, float] = {}
    for f, want in zip(tl["frames"], expected):
        cumulative.update(f["well_net_ul"])                           # frames hold sparse deltas
        assert set(cumulative) == set(want)
        for k, v in want.items():
            assert cumulative[k] == pytest.approx(v, abs=1e-6), (f["index"], k)
    final = {k: v for k, v in expected[-1].items() if abs(v) > 1e-9}
    assert tl["totals"]["final_net_ul"] == pytest.approx(final)
    assert anim.net_volumes_after(tl, len(tl["frames"]) - 1) == pytest.approx(expected[-1])
    # spot checks that the 8-channel arithmetic really happened
    assert final["5:A1"] == -800.0                                    # reservoir well feeds all 8 channels
    assert all(final[f"6:{r}1"] == 100.0 for r in ROWS)               # one column of the deep plate gets 100 uL each
    assert final["4:A2"] == -12.0 and final["3:A2"] == 5.0 and final["3:A3"] == 5.0
    assert tl["totals"]["max_abs_net_ul"] == 800.0


def test_totals_match_the_events_and_headers_are_not_double_counted(sim, tl):
    asp = [e for e in sim["events"] if e["kind"] == "aspirate"]
    dsp = [e for e in sim["events"] if e["kind"] == "dispense"]
    t = tl["totals"]
    assert t["aspirations"] == len(asp) and t["dispenses"] == len(dsp)
    assert t["volume_aspirated_ul"] == pytest.approx(sum(e["volume_ul"] * e["pipette"]["channels"] for e in asp))
    assert t["volume_dispensed_ul"] == pytest.approx(sum(e["volume_ul"] * e["pipette"]["channels"] for e in dsp))
    assert t["volume_moved_ul"] == t["volume_dispensed_ul"]
    assert sum(f["volume_total_ul"] or 0 for f in tl["frames"] if f["kind"] == "dispense") == pytest.approx(t["volume_dispensed_ul"])
    assert sum(t["time_by_kind_s"].values()) == pytest.approx(t["estimated_total_s"], abs=0.01)
    assert t["time_by_kind_s"]["transfer"] == t["time_by_kind_s"]["mix"] == t["time_by_kind_s"]["distribute"] == 0


def test_module_state_follows_the_events(sim, tl):
    mods = {m["type"]: m["slot"] for m in tl["deck"]["modules"]}
    temp_slot, mag_slot = mods["temperature"], mods["magnetic"]
    assert tl["initial_state"]["modules"][temp_slot]["setpoint_c"] is None
    assert tl["initial_state"]["modules"][mag_slot]["engaged"] is False
    temp, mag = None, False
    seen_temp, seen_mag = [], []
    for ev, f in zip(sim["events"], tl["frames"]):
        if ev["kind"] == "temperature_set":
            temp = ev["celsius"]
        elif ev["kind"] == "temperature_deactivate":
            temp = None
        elif ev["kind"] == "magnet_engage":
            mag = True
        elif ev["kind"] == "magnet_disengage":
            mag = False
        assert f["modules"][temp_slot]["setpoint_c"] == temp
        assert f["modules"][mag_slot]["engaged"] is mag
        seen_temp.append(temp)
        seen_mag.append(mag)
    assert {4.0, 42.0, None} <= set(seen_temp) and {True, False} <= set(seen_mag)


def test_head_positions_follow_the_targets(sim, tl):
    by_idx = {f["event_idx"]: f for f in tl["frames"]}
    ev = {e["idx"]: e for e in sim["events"]}
    # single channel: the head is exactly over the named well
    assert by_idx[1]["head_after"] == [14.38, 74.24]
    assert by_idx[3]["head_after"] == pytest.approx(ev[3]["target"]["point_mm"][:2], abs=0.01)
    # 8-channel over a plate column: rear nozzle on row A, head centre 31.5 mm towards the front
    assert by_idx[24]["head_after"] == pytest.approx([ev[24]["target"]["point_mm"][0], ev[24]["target"]["point_mm"][1] - 31.5], abs=0.01)
    # 8-channel over a single-row reservoir: head centred on the well
    assert by_idx[23]["head_after"] == pytest.approx(ev[23]["target"]["point_mm"][:2], abs=0.01)
    # trash and consecutive frames chain: each frame starts where the previous one ended
    assert by_idx[13]["head_after"] == tl["deck"]["trash"]["point"]
    for a, b in zip(tl["frames"], tl["frames"][1:]):
        assert b["head_before"] == a["head_after"]
    assert tl["frames"][0]["head_before"] == tl["deck"]["home"]
    assert tl["totals"]["gantry_distance_mm"] == pytest.approx(sum(f["move_mm"] for f in tl["frames"]), abs=1.0)


def test_focus_roles_mark_source_and_destination(tl):
    by_idx = {f["event_idx"]: f for f in tl["frames"]}
    assert by_idx[2]["focus"] == [{"key": "4:A1", "role": "source"}]
    assert by_idx[3]["focus"] == [{"key": "3:A1", "role": "dest"}]
    assert {(x["key"], x["role"]) for x in by_idx[24]["focus"]} == {(f"6:{r}1", "dest") for r in ROWS}
    assert by_idx[13]["focus"] == [{"key": "trash:12", "role": "trash"}]
    assert by_idx[21]["focus_preview"] is True and by_idx[2]["focus_preview"] is False


# --------------------------------------------------------------------------------------
# annotation convention
# --------------------------------------------------------------------------------------
@pytest.mark.parametrize("owner", ["ROBOT", "ROBOT-PARTIAL", "HUMAN"])
def test_phase_comment_parser_accepts_each_owner(owner):
    got = anim.parse_phase_comment(f"[s09] LAL assay plate setup | {owner} | reading needs a plate reader")
    assert got == {"step_id": "s09", "title": "LAL assay plate setup", "owner": owner,
                   "note": "reading needs a plate reader"}


def test_phase_comment_parser_edge_cases():
    assert anim.parse_phase_comment("[s1] Title | ROBOT")["note"] == ""                          # note is optional
    assert anim.parse_phase_comment("[s1] Title | human | a | b")["note"] == "a | b"            # owner case-insensitive
    assert anim.parse_phase_comment("  [step-2.a]  Spaced title  |  ROBOT  |  note  ") == {
        "step_id": "step-2.a", "title": "Spaced title", "owner": "ROBOT", "note": "note"}
    for bad in ["[s09] Title | MAYBE | note", "[s09] Title only", "[s09] | ROBOT | no title", "s09 Title | ROBOT | n",
                "[] Title | ROBOT | n", "[s09 Title | ROBOT | n", "Title | ROBOT | [s09]", "hello", "", None, 42, ["x"]]:
        assert anim.parse_phase_comment(bad) is None, bad


def test_human_pause_parser():
    assert anim.parse_human_pause("HUMAN [s09]: centrifuge the plate, then resume") == {
        "step_id": "s09", "message": "centrifuge the plate, then resume"}
    assert anim.parse_human_pause("HUMAN [s09]:")["message"] == ""
    for bad in ["human step", "HUMAN: no step id", "HUMAN [s09] no colon", "ROBOT [s09]: x", "", None, 3]:
        assert anim.parse_human_pause(bad) is None, bad


def test_phases_are_built_from_comments_and_pauses(sim):
    t = anim.build_timeline(annotated_sim(sim))
    assert [(p["step_id"], p["owner"]) for p in t["phases"]] == [
        ("s03", "ROBOT"), ("s07", "ROBOT-PARTIAL"), ("s09", "ROBOT-PARTIAL"), ("s11", "HUMAN")]
    p = t["phases"][2]
    assert (p["title"], p["note"]) == ("LAL assay plate setup", "reading needs a plate reader")
    n = len(t["frames"])
    firsts = [p["first_frame"] for p in t["phases"]]
    lasts = [p["last_frame"] for p in t["phases"]]
    assert firsts[0] == 0 and lasts[-1] == n - 1
    assert all(lasts[i] == firsts[i + 1] - 1 for i in range(3))
    for i, ph in enumerate(t["phases"]):
        assert ph["index"] == i and ph["start_s"] == t["frames"][ph["first_frame"]]["start_s"]
        assert ph["end_s"] == t["frames"][ph["last_frame"]]["end_s"]
        assert all(t["frames"][j]["phase"] == i for j in range(ph["first_frame"], ph["last_frame"] + 1))
    # the malformed comment stays a plain caption inside the s03 phase
    bogus = next(f for f in t["frames"] if "Bogus owner" in f["caption"])
    assert bogus["kind"] == "comment" and bogus["phase"] == 0 and bogus["caption"].startswith("[s04]")
    # the s09 pause belongs to the s09 phase, with its instructions parsed out
    pauses = [f for f in t["frames"] if f["human"]]
    assert [f["human_step"] for f in pauses] == [
        {"step_id": "s09", "message": "centrifuge the plate, then resume"},
        {"step_id": "s11", "message": "seed THP-1 cells and return the plate"}]
    assert [f["phase"] for f in pauses] == [2, 3]
    assert t["totals"]["estimated_total_s"] == anim.build_timeline(sim)["totals"]["estimated_total_s"]  # comments cost no time


def test_a_conforming_pause_without_a_preceding_phase_opens_a_human_phase():
    ev = [blank_event(0, "pause", "Pausing robot operation: HUMAN [s05]: seed the cells",
                      message="HUMAN [s05]: seed the cells")]
    t = anim.build_timeline(tiny_sim(ev))
    assert [(p["step_id"], p["owner"], p["note"]) for p in t["phases"]] == [("s05", "HUMAN", "seed the cells")]
    assert t["frames"][0]["phase"] == 0
    # a plain pause is only a caption and creates no phase
    t2 = anim.build_timeline(tiny_sim([blank_event(0, "pause", "Pausing robot operation: check", message="check")]))
    assert t2["phases"] == [] and t2["frames"][0]["human_step"] == {"step_id": None, "message": "check"}


# --------------------------------------------------------------------------------------
# robustness: bad, odd and empty input
# --------------------------------------------------------------------------------------
def test_failed_sim_renders_an_honest_error_page():
    bad = {"schema": "vaxforge.ot_sim/1", "ok": False,
           "error": {"type": "OutOfTipsError", "message": "no tips left <b>& more</b>", "traceback_tail": ["line 1", "line 2"]},
           "protocol_file": "C:/Users/someone/private dir/bad_protocol.py", "opentrons_version": "7.5.0",
           "events": [blank_event(0, "comment", "starting", message="starting")],
           "deck": {"labware": [], "modules": [], "pipettes": [], "trash": {"slot": "12"}}}
    t = anim.build_timeline(bad)
    assert t["ok"] is False and t["frames"] == [] and t["phases"] == []
    assert t["error"]["type"] == "OutOfTipsError" and t["error"]["events_logged"] == 1
    assert t["source"]["protocol_file"] == "bad_protocol.py"                     # no local directories in artifacts
    page = anim.render_html(t)
    assert "Simulation failed" in page and "OutOfTipsError" in page and "no tips left &lt;b&gt;&amp; more&lt;/b&gt;" in page
    assert "<canvas" not in page and "someone" not in page and page.isascii()
    assert embedded_json(page) == t
    assert page.count("<script") == 1


@pytest.mark.parametrize("weird", [None, [], "text", 12, {"ok": False}, {"ok": "false", "error": "boom"}, {"ok": False, "error": None}])
def test_build_timeline_never_raises_on_garbage(weird):
    t = anim.build_timeline(weird)
    assert t["schema"] == "vaxforge.timeline/1"
    assert t["ok"] is False and t["error"]["type"] and t["error"]["message"]
    page = anim.render_html(t)
    assert "Simulation failed" in page and embedded_json(page) == t


def test_render_html_tolerates_a_non_timeline():
    for junk in ({}, None, {"ok": True}, {"ok": True, "frames": "x", "deck": 1}):
        page = anim.render_html(junk)  # type: ignore[arg-type]
        assert "Simulation failed" in page and "http://" not in page


def test_empty_events_do_not_raise():
    t = anim.build_timeline(tiny_sim([]))
    assert t["ok"] is True and t["frames"] == [] and t["phases"] == []
    assert t["totals"]["estimated_total_s"] == 0 and t["totals"]["tips_used"] == 0
    assert any("no commands" in w for w in t["warnings"])
    page = anim.render_html(t)
    assert embedded_json(page) == t and "<canvas" in page
    # a sim with no deck at all is still renderable
    t2 = anim.build_timeline({"schema": "vaxforge.ot_sim/1", "ok": True, "events": []})
    assert t2["ok"] is True and len(t2["deck"]["slots"]) == 12
    assert embedded_json(anim.render_html(t2)) == t2


def test_pause_only_protocol():
    t = anim.build_timeline(tiny_sim([blank_event(0, "pause", "Pausing robot operation: swap the plate", message="swap the plate")]))
    assert len(t["frames"]) == 1
    f = t["frames"][0]
    assert f["human"] and f["duration_s"] is None and f["start_s"] == f["end_s"] == 0
    assert t["totals"]["estimated_total_s"] == 0 and t["totals"]["human_pauses"] == 1 == len(t["totals"]["human_frames"])
    assert f["head_before"] == f["head_after"] == t["deck"]["home"]
    page = anim.render_html(t)
    assert embedded_json(page) == t and "HUMAN STEP - robot paused" in page


def test_unknown_event_kinds_become_captions_and_never_crash():
    events = [
        blank_event(0, "air_gap", "Air gap 5 uL", pipette=PIP1, volume_ul=5.0, target=target("2", "A1", 146.88, 74.24)),
        {"idx": 1, "level": 0, "text": "no kind at all"},
        {"idx": 2, "level": "1", "kind": 123, "text": "numeric kind"},
        "not even an object",
        blank_event(4, "comment", "plain note", message="plain note"),
        blank_event(5, "aspirate", pipette=PIP1, volume_ul=None, target=None),
        blank_event(6, "dispense", pipette={"mount": "right", "name": "p20_single_gen2", "channels": 1}, volume_ul=-3.0,
                    target={"slot": "9", "well": "Z99", "labware": None, "point_mm": None}),
        blank_event(7, "pick_up_tip", pipette=PIP1, target=None),
        blank_event(8, "temperature_set", celsius=37.0),             # no temperature module on this deck
        blank_event(9, "touch_tip"),                                  # nothing to touch yet
    ]
    t = anim.build_timeline(tiny_sim(events))
    assert t["ok"] is True and len(t["frames"]) == len(events)
    f0, f1, f2, f3 = t["frames"][:4]
    assert f0["kind"] == "other" and f0["raw_kind"] == "air_gap" and f0["caption"] == "Air gap 5 uL"
    assert f0["duration_s"] == 0 and f0["well_net_ul"] == {} and f0["head_after"] == f0["head_before"]
    assert f1["kind"] == "other" and f1["caption"] == "no kind at all"
    assert f2["kind"] == "other" and f2["raw_kind"] == "123" and f2["level"] == 1
    assert f3["kind"] == "other" and f3["caption"] == "not even an object"
    assert any("not an object" in w for w in t["warnings"])
    assert t["totals"]["tips_used"] == 1                              # pick-up counted even though the rack is unknown
    assert t["frames"][8]["modules"] == {}
    page = anim.render_html(t)
    assert embedded_json(page) == t


def test_multi_channel_head_on_a_single_pipette_deck_and_unlisted_pipettes():
    # an event whose pipette is not in the deck listing is added on the fly (8-channel over the plate column)
    p8 = {"mount": "right", "name": "p300_multi_gen2", "channels": 8}
    ev = [blank_event(0, "dispense", pipette=p8, volume_ul=10.0, flow_rate_ul_s=94.0, target=target("2", "A3", 164.88, 74.24))]
    t = anim.build_timeline(tiny_sim(ev))
    assert {p["mount"] for p in t["deck"]["pipettes"]} == {"left", "right"}
    assert t["frames"][0]["well_net_ul"] == {f"2:{r}3": 10.0 for r in ROWS}
    assert t["totals"]["volume_dispensed_ul"] == 80.0
    assert set(t["frames"][0]["pipettes"]) == {"left", "right"}


def test_timeline_is_deterministic_and_json_roundtrips(sim, tl):
    again = anim.build_timeline(sim)
    assert json.dumps(again, sort_keys=False) == json.dumps(tl, sort_keys=False)
    assert json.loads(json.dumps(tl, allow_nan=False)) == tl
    assert anim.render_html(again) == anim.render_html(tl)


def test_source_does_not_leak_local_paths(sim):
    s = copy.deepcopy(sim)
    s["protocol_file"] = "C:" + chr(92) + "Users" + chr(92) + "somebody" + chr(92) + "proj" + chr(92) + "p.py"
    assert anim.build_timeline(s)["source"]["protocol_file"] == "p.py"
    s["protocol_file"] = "/home/somebody/proj/p.py"
    assert anim.build_timeline(s)["source"]["protocol_file"] == "p.py"


# --------------------------------------------------------------------------------------
# the HTML player
# --------------------------------------------------------------------------------------
def test_html_has_no_external_resources(html):
    assert "http://" not in html and "https://" not in html
    markup = re.sub(r"<(script|style)\b.*?</\1>", "", html, flags=re.S)                        # HTML outside script/style
    assert not re.search(r"""\s(?:src|href|action|poster|srcset|data)\s*=""", markup)          # no src=/href= attributes at all
    assert "@import" not in html and "url(" not in html and "//cdn" not in html
    assert "XMLHttpRequest" not in html and "fetch(" not in html and "WebSocket" not in html and "import(" not in html
    assert html.isascii()                                                                      # survives any file encoding
    assert "Content-Security-Policy" in html and "default-src 'none'" in html
    assert html.count("<script") == 2 and html.count("</script>") == 2


def test_embedded_json_equals_the_timeline(tl, html):
    assert embedded_json(html) == tl


def test_html_is_safe_with_hostile_text_in_the_run_log():
    nasty = "see https://example.org/x </script><script>alert(1)</script> <!-- & " + chr(0x2028) + " end"
    t = anim.build_timeline(tiny_sim([blank_event(0, "comment", nasty, message=nasty)]))
    page = anim.render_html(t)
    assert "http://" not in page and "https://" not in page
    assert "<script>alert" not in page and page.count("<script") == 2
    assert embedded_json(page) == t
    assert embedded_json(page)["frames"][0]["caption"] == nasty


def test_html_declares_the_required_ui(html):
    for needle in [
        "Estimated time - not measured on a robot", "HUMAN STEP - robot paused", ">Resume<", 'aria-live="polite"',
        "prefers-color-scheme: dark", "prefers-reduced-motion", 'name="viewport"', "<canvas",
        'id="btn-play"', 'id="btn-back"', 'id="btn-next"', 'id="btn-restart"', 'id="scrub"', 'id="speed"', 'id="steps"',
        'id="banner"', 'id="human"', 'value="0.5"', 'value="64"', "Fast-forward waits", "ArrowRight", "ArrowLeft",
        "own-ROBOT-PARTIAL", "own-HUMAN", "own-ROBOT", "net change",
    ]:
        assert needle in html, needle
    speeds = re.findall(r'<option value="([0-9.]+)"', html)
    assert [float(s) for s in speeds] == [0.5, 1, 2, 4, 8, 16, 32, 64]
    assert "overflow-x:hidden" not in html.replace(" ", "")                                      # no masking of overflow bugs


NODE = shutil.which("node")


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_player_script_passes_node_syntax_check(html, tmp_path):
    js = tmp_path / "player.js"
    js.write_text(player_script(html), encoding="utf-8")
    r = subprocess.run([NODE, "--check", str(js)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr


_NODE_DRIVER = r"""
// Runs the real player script against a stub DOM and drives it with synthetic animation frames.
const fs = require('fs');
const vm = require('vm');
const html = fs.readFileSync(process.argv[2], 'utf8');
const dataM = /<script type="application\/json" id="vf-timeline">([\s\S]*?)<\/script>/.exec(html);
const jsM = /<script>([\s\S]*?)<\/script>/.exec(html);
const idTags = Array.from(html.matchAll(/<(\w+)\b[^>]*?\bid="([^"]+)"/g)).map(m => [m[1], m[2]]);

const ctxStub = new Proxy({}, {
  get(t, k) { if (k === 'measureText') return () => ({ width: 40 }); if (k === 'createPattern') return () => ({}); if (k in t) return t[k]; return () => {}; },
  set(t, k, v) { t[k] = v; return true; },
});
function makeEl(tag, id) {
  return {
    tagName: String(tag).toUpperCase(), id: id || '', className: '', style: {}, hidden: false, disabled: false,
    value: '', checked: false, type: '', title: '', children: [], parentNode: null, listeners: {}, attrs: {}, _text: '',
    offsetTop: 0, offsetHeight: 20, clientHeight: 300, clientWidth: 700, scrollTop: 0, width: 0, height: 0,
    get firstChild() { return this.children[0] || null; },
    get lastChild() { return this.children[this.children.length - 1] || null; },
    get textContent() { return this._text + this.children.map(c => c.textContent).join(''); },
    set textContent(v) { this._text = String(v); this.children = []; },
    appendChild(c) { c.parentNode = this; this.children.push(c); return c; },
    removeChild(c) { const i = this.children.indexOf(c); if (i >= 0) this.children.splice(i, 1); c.parentNode = null; return c; },
    setAttribute(k, v) { this.attrs[k] = String(v); }, getAttribute(k) { return k in this.attrs ? this.attrs[k] : null; },
    removeAttribute(k) { delete this.attrs[k]; },
    addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
    dispatch(t, ev) { const e = Object.assign({ target: this, defaultPrevented: false, preventDefault() { this.defaultPrevented = true; } }, ev || {}); (this.listeners[t] || []).forEach(fn => fn(e)); return e; },
    contains(o) { for (let n = o; n; n = n.parentNode) if (n === this) return true; return false; },
    getBoundingClientRect() { return { left: 0, top: 100, right: 700, bottom: 600, width: 700, height: 500 }; },
    focus() { doc.activeElement = this; }, click() { this.dispatch('click'); },
    getContext() { return ctxStub; },
  };
}
const doc = { activeElement: null, listeners: {}, els: {}, documentElement: makeEl('html'), body: makeEl('body'),
  getElementById(id) { return this.els[id] || null; }, createElement(t) { return makeEl(t); },
  addEventListener(t, fn) { (this.listeners[t] = this.listeners[t] || []).push(fn); },
  key(k, target) { const e = { key: k, target: target || doc.body, defaultPrevented: false, ctrlKey: false, metaKey: false, altKey: false, preventDefault() { this.defaultPrevented = true; } };
    (this.listeners['keydown'] || []).forEach(fn => fn(e)); return e.defaultPrevented; } };
idTags.forEach(([tag, id]) => { doc.els[id] = makeEl(tag, id); });
doc.els['vf-timeline'].textContent = dataM[1];
doc.els['scrub'].type = 'range'; doc.els['skipdelays'].type = 'checkbox'; doc.els['skipdelays'].checked = true;
doc.els['speed'].value = '4';

const win = { innerWidth: 1280, innerHeight: 800, devicePixelRatio: 1, pageYOffset: 0,
  matchMedia: q => ({ matches: false, media: q, addEventListener() {}, removeEventListener() {} }),
  requestAnimationFrame: () => 0, addEventListener() {}, ResizeObserver: class { observe() {} } };
const context = vm.createContext({ window: win, document: doc, getComputedStyle: () => ({ getPropertyValue: () => '#808080' }),
  ResizeObserver: win.ResizeObserver, console });
vm.runInContext(jsM[1], context, { filename: 'player.js' });

const P = win.vaxforgePlayer;
const E = id => doc.els[id];
const R = { frames: P.frames, init: P.state(), initCaption: E('caption').textContent, errors: [] };
function setSpeed(v) { E('speed').value = String(v); E('speed').dispatch('change'); }
function runUntil(pred, max) { let n = 0; while (n < max && !pred()) { P.tick(1 / 60); n++; } return n; }

// 1. play at 64x until the first human pause
setSpeed(64); P.play();
R.ticksToHuman = runUntil(() => P.state().human, 200000);
R.atHuman = P.state(); R.overlayHidden = E('human').hidden; R.humanMsg = E('human-msg').textContent;
R.captionAtHuman = E('caption').textContent; R.playLabelAtHuman = E('btn-play').textContent; R.focusAtHuman = doc.activeElement && doc.activeElement.id;
R.banner = E('banner').textContent;
const frozen = JSON.stringify(P.state()); for (let i = 0; i < 200; i++) P.tick(1 / 60);
R.waitingIsStable = frozen === JSON.stringify(P.state());
R.clockAtHuman = E('t-now').textContent; R.clockTotal = E('t-total').textContent;
// 2. keep resuming until the run ends (counts human stops)
let stops = 1; E('resume').click(); P.tick(0);
for (let guard = 0; guard < 50; guard++) {
  runUntil(() => P.state().human || !P.state().playing, 200000);
  if (P.state().human) { stops++; E('resume').click(); P.tick(0); } else break;
}
R.humanStops = stops; R.end = P.state(); R.endLabel = E('btn-play').textContent; R.overlayHiddenAtEnd = E('human').hidden;
// 3. keyboard: space toggles, arrows step, native Space is kept on a checkbox and on the step list
P.restart(); P.tick(0);
R.spacePrevented = doc.key(' '); R.afterSpace = P.state().playing; doc.key(' '); R.afterSpace2 = P.state().playing;
R.checkboxSpacePrevented = doc.key(' ', E('skipdelays'));
R.selectArrowPrevented = doc.key('ArrowRight', Object.assign(makeEl('select'), {}));
P.tick(0); doc.key('ArrowRight'); P.tick(0); R.afterRight = P.state(); doc.key('ArrowRight'); P.tick(0); R.afterRight2 = P.state();
doc.key('ArrowLeft'); P.tick(0); R.afterLeft = P.state();
// 4. scrub slider and list clicks
E('scrub').value = '30.5'; E('scrub').dispatch('input'); P.tick(0); R.scrub = P.state();
const btn = E('steps').children.map(li => li.children[0]).filter(b => b && b.attrs && b.attrs['data-i'] === '5')[0];
E('steps').dispatch('click', { target: btn }); P.tick(0); R.clicked = P.state();
R.currentRows = E('steps').children.filter(li => li.children[0] && li.children[0].attrs['aria-current'] === 'step').length;
R.listRows = E('steps').children.length;
// 5. every frame can be displayed (exercises every drawing and status branch), forward and backward
for (let i = 0; i < P.frames; i++) { P.goto(i, 0.5); P.tick(0); P.goto(i, 1); P.tick(0); P.goto(i, 0); P.tick(0); }
R.visitedAll = true;
console.log(JSON.stringify(R));
"""


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_player_runs_end_to_end_against_a_stub_dom(sim, tmp_path):
    page = tmp_path / "annotated.html"
    t = anim.build_timeline(annotated_sim(sim))
    page.write_text(anim.render_html(t), encoding="utf-8")
    driver = tmp_path / "driver.js"
    driver.write_text(_NODE_DRIVER, encoding="utf-8")
    r = subprocess.run([NODE, str(driver), str(page)], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-2000:] + r.stdout[-500:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    n = len(t["frames"])
    assert out["frames"] == n and out["init"]["cur"] == 0 and out["init"]["playing"] is False
    assert "Ready" in out["initCaption"]
    # playback stops at the first human step, shows the overlay and waits there
    first_human = next(f["index"] for f in t["frames"] if f["human"])
    assert out["atHuman"]["cur"] == first_human and out["atHuman"]["human"] is True and out["atHuman"]["playing"] is False
    assert out["overlayHidden"] is False and "centrifuge the plate" in out["humanMsg"]
    assert "HUMAN STEP - robot paused" in out["captionAtHuman"] and out["playLabelAtHuman"] == "Resume"
    assert out["focusAtHuman"] == "resume" and out["waitingIsStable"] is True
    assert "ROBOT-PARTIAL" in out["banner"] and "LAL assay plate setup" in out["banner"]
    assert out["clockAtHuman"] == out["clockTotal"]          # every robot step precedes the first pause in this protocol
    # resuming twice reaches the end; the end state offers Replay
    assert out["humanStops"] == 2 and out["end"]["cur"] == n - 1 and out["end"]["prog"] == 1
    assert out["endLabel"] == "Replay" and out["overlayHiddenAtEnd"] is True
    # keyboard
    assert out["spacePrevented"] is True and out["afterSpace"] is True and out["afterSpace2"] is False
    assert out["checkboxSpacePrevented"] is False and out["selectArrowPrevented"] is False
    assert (out["afterRight"]["cur"], out["afterRight"]["prog"]) == (0, 1)       # first step completes the first frame
    assert (out["afterRight2"]["cur"], out["afterRight2"]["prog"]) == (1, 1)
    assert (out["afterLeft"]["cur"], out["afterLeft"]["prog"]) == (0, 1)
    assert (out["scrub"]["cur"], round(out["scrub"]["prog"], 3)) == (30, 0.5)
    assert out["clicked"]["cur"] == 5 and out["clicked"]["prog"] == 1
    assert out["currentRows"] == 1 and out["listRows"] == n + len(t["phases"])
    assert out["visitedAll"] is True


@pytest.mark.skipif(NODE is None, reason="node is not on PATH")
def test_player_survives_an_empty_run_and_a_big_run(tmp_path):
    driver = tmp_path / "driver.js"
    driver.write_text(_NODE_DRIVER.split("// 1. play")[0] + "console.log(JSON.stringify(R));", encoding="utf-8")
    empty = anim.build_timeline(tiny_sim([]))
    p = tmp_path / "empty.html"
    p.write_text(anim.render_html(empty), encoding="utf-8")
    r = subprocess.run([NODE, str(driver), str(p)], capture_output=True, text=True, timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    out = json.loads(r.stdout.strip().splitlines()[-1])
    assert out["frames"] == 0 and "nothing to animate" in out["initCaption"]
    # 1500 pipetting steps
    events = []
    for i in range(500):
        events.append(blank_event(3 * i, "aspirate", pipette=PIP1, volume_ul=20.0, flow_rate_ul_s=7.56, target=target("2", "A1", 146.88, 74.24)))
        events.append(blank_event(3 * i + 1, "dispense", pipette=PIP1, volume_ul=20.0, flow_rate_ul_s=7.56,
                                  target=target("2", f"{ROWS[i % 8]}{(i // 8) % 12 + 1}", 146.88 + 9 * ((i // 8) % 12), 74.24 - 9 * (i % 8))))
        events.append(blank_event(3 * i + 2, "touch_tip"))
    big = anim.build_timeline(tiny_sim(events))
    assert len(big["frames"]) == 1500 and big["totals"]["dispenses"] == 500
    p2 = tmp_path / "big.html"
    p2.write_text(anim.render_html(big), encoding="utf-8")
    driver2 = tmp_path / "driver2.js"
    driver2.write_text(_NODE_DRIVER.split("// 1. play")[0] + """
setSpeed(64); P.play(); const n = runUntil(() => !P.state().playing, 400000);
R.finished = P.state(); R.ticks = n; for (let i = 0; i < P.frames; i += 97) { P.goto(i, 0.3); P.tick(0); }
console.log(JSON.stringify(R));""", encoding="utf-8")
    r2 = subprocess.run([NODE, str(driver2), str(p2)], capture_output=True, text=True, timeout=120)
    assert r2.returncode == 0, r2.stderr[-1500:]
    out2 = json.loads(r2.stdout.strip().splitlines()[-1])
    assert out2["finished"]["cur"] == 1499 and out2["finished"]["prog"] == 1 and out2["finished"]["playing"] is False


# --------------------------------------------------------------------------------------
# files and CLI
# --------------------------------------------------------------------------------------
def test_write_animation_writes_both_files(tmp_path, tl, html):
    res = anim.write_animation(SAMPLE, tmp_path / "nested" / "out")
    assert res["ok"] is True
    tp, hp = Path(res["timeline"]), Path(res["html"])
    assert tp.name == "timeline.json" and hp.name == "protocol_animation.html" and tp.parent == hp.parent
    assert json.loads(tp.read_text(encoding="utf-8")) == tl
    assert hp.read_text(encoding="utf-8") == html
    assert b"\r\n" not in tp.read_bytes() and b"\r\n" not in hp.read_bytes()            # same bytes on every OS


def test_write_animation_reports_failures_without_raising(tmp_path):
    missing = anim.write_animation(tmp_path / "nope.json", tmp_path / "o1")
    assert missing["ok"] is False and Path(missing["html"]).exists()
    assert "could not read" in embedded_json(Path(missing["html"]).read_text(encoding="utf-8"))["error"]["message"]
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    res = anim.write_animation(broken, tmp_path / "o2")
    assert res["ok"] is False and "Simulation failed" in Path(res["html"]).read_text(encoding="utf-8")
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps({"schema": "vaxforge.ot_sim/1", "ok": False, "error": {"type": "X", "message": "y"}}), encoding="utf-8")
    assert anim.write_animation(failed, tmp_path / "o3")["ok"] is False
    bom = tmp_path / "bom.json"
    bom.write_text(SAMPLE.read_text(encoding="utf-8"), encoding="utf-8-sig")             # a BOM must not break reading
    assert anim.write_animation(bom, tmp_path / "o4")["ok"] is True


def test_cli(tmp_path, capsys):
    assert anim.main([str(SAMPLE), str(tmp_path / "cli")]) == 0
    assert (tmp_path / "cli" / "timeline.json").is_file() and (tmp_path / "cli" / "protocol_animation.html").is_file()
    assert "protocol_animation.html" in capsys.readouterr().out
    assert anim.main(["only-one-arg"]) == 2 and anim.main(["-h"]) == 2
    failed = tmp_path / "failed.json"
    failed.write_text(json.dumps({"ok": False, "error": {"type": "X", "message": "y"}}), encoding="utf-8")
    assert anim.main([str(failed), str(tmp_path / "cli2")]) == 1


def test_committed_demo_is_self_consistent():
    """The demo files generated from the sample (tests/fixtures/animation_demo) must agree with each other."""
    for sub in ("", "annotated"):
        d = DEMO_DIR / sub if sub else DEMO_DIR
        tl_path, html_path = d / "timeline.json", d / "protocol_animation.html"
        assert tl_path.is_file() and html_path.is_file(), f"regenerate the demo into {d} (python -m vaxforge.s9plus_animate)"
        timeline = json.loads(tl_path.read_text(encoding="utf-8"))
        page = html_path.read_text(encoding="utf-8")
        assert timeline["schema"] == "vaxforge.timeline/1" and timeline["ok"] is True
        assert embedded_json(page) == timeline
        assert "http://" not in page and "https://" not in page
