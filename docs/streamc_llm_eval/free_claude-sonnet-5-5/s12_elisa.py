from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck layout
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    plate = ctx.load_labware("nest_96_wellplate_200ul_flat", "2")
    res = ctx.load_labware("nest_12_reservoir_15ml", "5")
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # Liquids
    wash_liq = ctx.define_liquid(name="Wash buffer", description="ELISA wash buffer (placeholder)", display_color="#1E90FF")
    sample_liq = ctx.define_liquid(name="Sample", description="ELISA sample (placeholder)", display_color="#FFA500")
    res["A1"].load_liquid(liquid=wash_liq, volume=10000)
    res["A2"].load_liquid(liquid=sample_liq, volume=2000)
    waste = res["A12"]  # empty well used as liquid waste

    wells = [plate[w] for w in ["A1", "B1", "C1", "A2", "B2", "C2"]]

    SAMPLE_VOL = 50   # placeholder
    WASH_VOL = 100    # placeholder
    WASH_CYCLES = 2   # placeholder

    ctx.comment("[s12] ELISA readout | ROBOT | Volumes in this protocol (50 uL sample, 100 uL wash, 2 wash cycles) are placeholder-sized values, not validated assay values. Robot does plate setup and washes; plate reading is done by a person.")

    # Plate setup
    ctx.comment("[s12] ELISA readout - plate setup | ROBOT | Dispense placeholder 50 uL sample into 6 wells with one tip")
    p300.pick_up_tip()
    for w in wells:
        p300.aspirate(SAMPLE_VOL, res["A2"])
        p300.dispense(SAMPLE_VOL, w)
    p300.drop_tip()

    # Washes
    current = SAMPLE_VOL
    for cycle in range(WASH_CYCLES):
        ctx.comment("[s12] ELISA readout - wash cycle %d | ROBOT | Add placeholder 100 uL wash buffer, then remove liquid with a fresh tip per well" % (cycle + 1))
        p300.pick_up_tip()
        for w in wells:
            p300.aspirate(WASH_VOL, res["A1"])
            p300.dispense(WASH_VOL, w)
        p300.drop_tip()
        current += WASH_VOL
        for w in wells:
            p300.pick_up_tip()
            p300.aspirate(current - 10, w)
            p300.dispense(current - 10, waste)
            p300.drop_tip()
        current = 10

    # Reading is manual
    ctx.comment("[s12] ELISA readout - reading | HUMAN | Plate reading is not automated")
    ctx.pause("HUMAN [s12]: Remove the plate from deck slot 2 and read it on the plate reader.")
