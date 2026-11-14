metadata = {"protocolName": "bad: volume too large", "apiLevel": "2.15"}
def run(ctx):
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", 1)
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", 2)
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])
    p300.pick_up_tip(); p300.aspirate(500, plate["A1"])
