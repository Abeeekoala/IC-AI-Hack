"""S9 plan layer: constructive physical correctness (no simulator needed)."""

import pytest

from vaxforge.s9_plan import (
    CATALOG,
    PlanBuilder,
    PlanError,
    expand_multi,
    expected_dispensed,
    render_python,
    valid_well,
    well_names,
)

TUBES = "opentrons_24_tuberack_nest_1.5ml_snapcap"
PLATE = "nest_96_wellplate_200ul_flat"
RES = "nest_12_reservoir_15ml"


def mk():
    b = PlanBuilder("t", "t", ["s01"])
    tubes = b.labware("tubes", TUBES, slot="2")
    plate = b.labware("plate", PLATE, slot="3")
    return b, tubes, plate


def test_wells():
    assert valid_well(PLATE, "H12") and not valid_well(PLATE, "I1") and not valid_well(PLATE, "A13")
    assert valid_well(RES, "A12") and not valid_well(RES, "B1")
    assert len(well_names(PLATE)) == 96 and well_names(TUBES)[:5] == ["A1", "B1", "C1", "D1", "A2"]
    assert len(expand_multi(PLATE, "A3", 8)) == 8 and expand_multi(RES, "A1", 8) == [("A1", 8)]


def test_aspirating_from_an_empty_well_is_rejected():
    b, tubes, plate = mk()
    with pytest.raises(PlanError, match="only 0 uL"):
        b.transfer(5, tubes["A1"], plate["A1"])


def test_overfilling_a_well_is_rejected():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    b.transfer(150, tubes["A1"], plate["A1"])
    with pytest.raises(PlanError, match="holds 200"):
        b.transfer(100, tubes["A1"], plate["A1"])


def test_mixing_more_liquid_than_present_is_rejected():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    b.transfer(10, tubes["A1"], plate["A1"])
    with pytest.raises(PlanError, match="mixing"):
        b.mix(3, 20, plate["A1"])
    with pytest.raises(PlanError, match="mixing"):
        b.transfer(5, tubes["A1"], plate["A2"], mix_after=(3, 15))


def test_pipette_volume_limits():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    with pytest.raises(PlanError, match="below the p20 minimum"):
        b.transfer(0.5, tubes["A1"], plate["A1"])
    with pytest.raises(PlanError, match="exceeds the p20 maximum"):
        b.transfer(25, tubes["A1"], plate["A1"], pipette="p20")
    b.transfer(25, tubes["A1"], plate["A1"])  # auto-selects the p300
    assert "p300" in {p.key for p in b.build().pipettes}


def test_deck_conflicts():
    b = PlanBuilder("t", "t", ["s01"])
    b.labware("a", PLATE, slot="3")
    with pytest.raises(PlanError, match="already used"):
        b.labware("b", PLATE, slot="3")
    with pytest.raises(PlanError, match="fixed trash"):
        b.labware("c", PLATE, slot="12")
    with pytest.raises(PlanError, match="cannot sit"):
        b.module("m", "temperature module gen2", "2")
    with pytest.raises(PlanError, match="not in the S9 catalogue"):
        b.labware("d", "some_unknown_plate", slot="4")


def test_p300_single_and_multichannel_cannot_share_the_mount():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    b.transfer(50, tubes["A1"], plate["A1"], pipette="p300")
    with pytest.raises(PlanError, match="share the right mount"):
        b.transfer(50, tubes["A1"], plate["A2"], pipette="p300m")


def test_mix_after_with_one_tip_over_many_destinations_is_forbidden():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    with pytest.raises(PlanError, match="new_tip='always'"):
        b.transfer(5, tubes["A1"], [plate["A1"], plate["A2"]], new_tip="once", mix_after=(2, 2))


def test_stock_volume_is_draw_plus_dead_volume():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    for w in ("A1", "A2", "A3"):
        b.transfer(10, tubes["A1"], plate[w])
    plan = b.build()
    (liq,) = [l for l in plan.liquids if l.ref.well == "A1" and l.ref.labware == "tubes"]
    assert liq.volume_ul == 30 + 30 and liq.source == "robot_loaded"


def test_distribute_draws_one_disposal_volume_per_trip():
    # verified against the simulator: 3 wells x 19 uL with a p20 needs 3 trips of 19+1 uL
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "mm")
    b.distribute(19, tubes["A1"], [plate["A1"], plate["B1"], plate["C1"]])
    (liq,) = b.build().liquids
    assert liq.volume_ul == 19 * 3 + 3 * 1 + 30


def test_cannot_dispense_into_a_robot_loaded_stock():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    b.reagent(tubes["A2"], "buffer")
    with pytest.raises(PlanError, match="robot-loaded stock"):
        b.transfer(5, tubes["A1"], tubes["A2"])


def test_tip_racks_scale_with_pickups():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    wells = well_names(PLATE)
    for i in range(100):  # 100 pickups; 5 uL into 96 wells, a few wells get a second dose (well under capacity)
        b.transfer(5, tubes["A1"], plate[wells[i % 96]])
    plan = b.build()  # 100 single-channel pickups need 2 racks (96 per rack)
    assert plan.tips_needed["p20"] == 100
    assert len(plan.pipette_by_key("p20").tipracks) == 2


def test_multichannel_expectations_expand_to_the_whole_column():
    b = PlanBuilder("t", "t", ["s01"])
    res = b.labware("res", RES, slot="2")
    plate = b.labware("plate", PLATE, slot="3")
    b.reagent(res["A1"], "buffer")
    b.transfer(100, res["A1"], plate["A3"], pipette="p300m")
    plan = b.build()
    exp = expected_dispensed(plan)
    assert {w for (_s, w) in exp} == {f"{r}3" for r in "ABCDEFGH"}
    assert all(v == 100 for v in exp.values())
    (liq,) = plan.liquids
    assert liq.volume_ul == 8 * 100 + 500  # eight channels draw from the one reservoir well


def test_render_is_deterministic_and_annotated():
    b, tubes, plate = mk()
    b.reagent(tubes["A1"], "water")
    b.step("s01")
    b.transfer(10, tubes["A1"], plate["A1"])
    b.pause("HUMAN [s01]: do the thing")
    plan = b.build()
    info = {"s01": ("Make a thing", "ROBOT-PARTIAL", "liquid handling only")}
    code = render_python(plan, step_info=info)
    assert code == render_python(plan, step_info=info)
    assert "[s01] Make a thing | ROBOT-PARTIAL | liquid handling only" in code
    assert 'ctx.pause("HUMAN [s01]: do the thing")' in code
    assert 'requirements = {"robotType": "OT-2", "apiLevel": "2.19"}' in code
    assert "load_liquid(liquid=liq_1, volume=40)" in code
    assert plan.plan_hash() in code


def test_plan_hash_tracks_content():
    def build(v):
        b, tubes, plate = mk()
        b.reagent(tubes["A1"], "water")
        b.transfer(v, tubes["A1"], plate["A1"])
        return b.build().plan_hash()

    assert build(10) == build(10) and build(10) != build(11)


def test_catalog_has_sane_capacities():
    for name, info in CATALOG.items():
        assert info.rows >= 1 and info.cols >= 1 and info.well_max_ul > 0, name
