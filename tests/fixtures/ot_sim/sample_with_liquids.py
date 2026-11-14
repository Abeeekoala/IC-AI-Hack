from opentrons import protocol_api
requirements = {"robotType": "OT-2", "apiLevel": "2.19"}
metadata = {"protocolName": "t3"}
def run(ctx: protocol_api.ProtocolContext):
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")
    plate = ctx.load_labware("nest_96_wellplate_200ul_flat", "3")
    p = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])
    water = ctx.define_liquid(name="water", description="diluent", display_color="#33AAFF")
    tubes["A1"].load_liquid(liquid=water, volume=500)
    p.transfer(50, tubes["A1"], plate["A1"], new_tip="always")
