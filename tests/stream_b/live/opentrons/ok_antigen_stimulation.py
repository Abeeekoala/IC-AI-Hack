from opentrons import protocol_api
metadata = {"protocolName": "antigen stimulation smoke test", "apiLevel": "2.15"}
def run(ctx: protocol_api.ProtocolContext):
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", 1)
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", 2)
    res = ctx.load_labware("opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap", 3)
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])
    p300.transfer(50, res["A1"], [plate.wells_by_name()[w] for w in ("A1", "A2", "A3")], new_tip="always")   # antigen into wells
    p300.transfer(50, res["A2"], plate["B1"])                                                              # LPS control
