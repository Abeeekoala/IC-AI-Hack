from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck layout (session s12_elisa):
    #   Slot 1,4,5,6,7,8,9 : 300 uL tip racks (84 tip positions, one per 8-channel pick)
    #   Slot 2             : 12-well reservoir, wash buffer in A1
    #   Slot 3             : 96-well flat plate (ELISA plate)
    #   Trash              : implicit OT-2 fixed trash
    tip_slots = [1, 4, 5, 6, 7, 8, 9]
    tip_racks = [ctx.load_labware("opentrons_96_tiprack_300ul", s) for s in tip_slots]
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", 2)
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", 3)
    pip = ctx.load_instrument("p300_multi_gen2", "right", tip_racks=tip_racks)

    # Liquid declarations. All volumes are PLACEHOLDER values, not protocol-specified.
    wash = ctx.define_liquid(
        name="Wash buffer",
        description="PLACEHOLDER wash buffer (volume is a placeholder)",
        display_color="#3366FF",
    )
    reservoir["A1"].load_liquid(liquid=wash, volume=12000)  # placeholder volume

    WASH_UL = 200  # PLACEHOLDER per-well wash volume
    WASH_CYCLES = 3  # PLACEHOLDER wash cycle count

    ctx.comment(
        "[s12] ELISA readout | ROBOT | Plate setup and washes; all volumes are "
        "PLACEHOLDER sizes. Fresh tip per column pick; no tip reused across sources or wells."
    )

    # Plate setup: fill every column with wash buffer from the reservoir.
    ctx.comment("[s12] Plate setup | ROBOT | Dispense PLACEHOLDER 200 uL wash buffer into all 12 columns, fresh tip per column")
    for col in range(1, 13):
        pip.pick_up_tip()
        pip.aspirate(WASH_UL, reservoir["A1"])
        pip.dispense(WASH_UL, plate[f"A{col}"])
        pip.drop_tip()

    # Washes: remove liquid from each column with a fresh tip (tip discarded), then refill.
    for cycle in range(1, WASH_CYCLES + 1):
        ctx.comment(f"[s12] Washes cycle {cycle} of {WASH_CYCLES} | ROBOT | Remove liquid then refill each column with PLACEHOLDER 200 uL wash")
        for col in range(1, 13):
            # Removal: fresh tip aspirates from the plate column, then tip is discarded.
            pip.pick_up_tip()
            pip.aspirate(WASH_UL, plate[f"A{col}"])
            pip.drop_tip()
            # Refill: fresh tip draws from the reservoir and dispenses into the column.
            pip.pick_up_tip()
            pip.aspirate(WASH_UL, reservoir["A1"])
            pip.dispense(WASH_UL, plate[f"A{col}"])
            pip.drop_tip()

    ctx.comment("[s12] ELISA readout | ROBOT | Washes complete; no tip left on pipette")

    # Reading is NOT automated. A person must take the plate to the reader.
    ctx.pause("HUMAN [s12]: Remove the ELISA plate from slot 3, check the wash state, and read absorbance on the plate reader (reading is not automated).")
