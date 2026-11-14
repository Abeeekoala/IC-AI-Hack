from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck:
    #   Slot 1: Temperature module (GEN2) with 96-well aluminum block (control destination)
    #   Slot 2: 24-tube rack (untransformed host source in A1)
    #   Slot 5: 300 uL tip rack
    ctx.comment(
        "[s03] Transform expression host | ROBOT | Partial automation: robot dispenses the "
        "untransformed host control only. Volumes are PLACEHOLDERS (50 uL aspirate/dispense, "
        "1000 uL loaded in source tube); replace with real values before use. No temperature "
        "setpoint was specified, so none is set on the Temperature module."
    )

    temp_mod = ctx.load_module("temperature module gen2", "1")
    plate = temp_mod.load_labware("opentrons_96_aluminumblock_nest_wellplate_100ul")
    source_rack = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")
    tip_rack = ctx.load_labware("opentrons_96_tiprack_300ul", "5")

    pipette = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tip_rack])

    host = ctx.define_liquid(
        name="untransformed host",
        description="Untransformed expression host cells (control)",
        display_color="#70AD47",
    )
    source_well = source_rack["A1"]
    source_well.load_liquid(liquid=host, volume=1000)  # placeholder volume

    control_well = plate["A1"]
    placeholder_volume = 50  # uL, placeholder; well holds 100 uL and tip holds 300 uL

    # Single source (untransformed host), single control destination: one fresh tip.
    ctx.comment(
        "[s03] Untransformed host control dispense | ROBOT | Transfer 50 uL (placeholder) of "
        "untransformed host from A1 of the source rack into A1 of the Temperature module plate."
    )
    pipette.pick_up_tip()
    pipette.aspirate(placeholder_volume, source_well)
    pipette.dispense(placeholder_volume, control_well)
    pipette.drop_tip()

    ctx.comment(
        "[s03] Plate control on selection plate | HUMAN | Manual plating of the untransformed "
        "host control onto the selection plate; robot does not plate."
    )
    ctx.pause(
        "HUMAN [s03]: Plate the untransformed host control from the Temperature module plate "
        "(well A1) onto the selection plate, then continue the manual transformation workflow."
    )
