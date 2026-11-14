from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # ---- Deck layout (placeholder design) ----
    # Slot 1: 300 uL tip rack
    # Slot 2: 96-well flat plate (sample/destination plate, plate setup by robot)
    # Slot 3: 12-well reservoir holding placeholder equilibration buffer
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", "2")
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", "3")
    pipette = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # ---- Liquids (placeholder volumes, not validated values) ----
    buffer = ctx.define_liquid(
        name="Equilibration buffer (placeholder)",
        description="Placeholder liquid for endotoxin removal plate setup",
        display_color="#4A90D9",
    )
    # Placeholder source volume; reservoir wells are not limited by the non-reservoir rule.
    reservoir["A1"].load_liquid(liquid=buffer, volume=10000)

    # ---- Step s08: plate setup ----
    ctx.comment(
        "[s08] Endotoxin removal (automation: partial; Plate setup yes.) | ROBOT | "
        "PLACEHOLDER VOLUMES: 100 uL per well is a stand-in value, not a validated volume. "
        "Plate setup only; downstream endotoxin removal is performed off-robot."
    )

    ctx.pause(
        "HUMAN [s08]: Confirm the 96-well plate in slot 2 and the buffer reservoir in slot 3 "
        "are in place, then resume."
    )

    transfer_volume = 100  # placeholder; always <= 300 uL tip capacity
    target_wells = ["A1", "A2", "A3", "A4"]  # placeholder destination wells

    pipette.pick_up_tip()
    for well in target_wells:
        # Aspirate and dispense the same placeholder volume; never exceeds tip capacity.
        pipette.aspirate(transfer_volume, reservoir["A1"])
        pipette.dispense(transfer_volume, plate[well])
    pipette.drop_tip()

    # Final check: no tip remains on the pipette.
    assert not pipette.has_tip, "Tip still attached at end of protocol"
