from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Placeholder volumes throughout; replace with validated values before use.
    ctx.comment(
        "[s06] Lyse cells; clarify lysate | ROBOT | Placeholder volumes used "
        "(lysis buffer 50 uL per well, sample 100 uL per well). Robot adds lysis "
        "reagent only; sonication and centrifugation are manual."
    )

    # Liquid definitions
    lysis_buffer = ctx.define_liquid(
        name="Lysis buffer",
        description="Placeholder lysis reagent",
        display_color="#00AAFF",
    )
    cell_suspension = ctx.define_liquid(
        name="Cell suspension",
        description="Placeholder cell sample",
        display_color="#FFAA00",
    )

    # Deck layout
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", "2")
    sample_plate = ctx.load_labware("corning_96_wellplate_360ul_flat", "3")
    pipette = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # Initial contents
    reservoir["A1"].load_liquid(liquid=lysis_buffer, volume=12000)
    sample_wells = ["A1", "B1", "C1", "D1"]
    for well in sample_wells:
        sample_plate[well].load_liquid(liquid=cell_suspension, volume=100)

    # Reagent addition (robot)
    ctx.comment(
        "[s06] Lyse cells; clarify lysate | ROBOT | Dispense placeholder 50 uL "
        "lysis buffer from reservoir A1 into sample wells A1, B1, C1, D1 "
        "using one tip (200 uL total, within tip capacity)."
    )
    pipette.pick_up_tip()
    pipette.aspirate(200, reservoir["A1"])
    for well in sample_wells:
        pipette.dispense(50, sample_plate[well])
    pipette.drop_tip()

    # Manual lysis step
    ctx.comment(
        "[s06] Lyse cells; clarify lysate | HUMAN | Sonication of lysate "
        "must be performed manually."
    )
    ctx.pause(
        "HUMAN [s06]: Remove the sample plate from slot 3, sonicate the lysate "
        "in wells A1, B1, C1, D1 per the approved method, then return the plate "
        "to slot 3 and resume."
    )

    # Manual clarification step
    ctx.comment(
        "[s06] Lyse cells; clarify lysate | HUMAN | Centrifugation to clarify "
        "lysate must be performed manually."
    )
    ctx.pause(
        "HUMAN [s06]: Centrifuge the sample plate to clarify the lysate per the "
        "approved method, then return the plate to slot 3 and resume."
    )

    ctx.comment(
        "[s06] Lyse cells; clarify lysate | ROBOT | Robot-owned steps complete. "
        "No tips remain on the pipette."
    )
