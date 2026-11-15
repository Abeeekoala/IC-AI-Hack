# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.
from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    temp = ctx.load_module('temperature module gen2', '3')
    rxn = temp.load_labware('opentrons_96_aluminumblock_nest_wellplate_100ul')
    tubes = ctx.load_labware('opentrons_24_tuberack_nest_1.5ml_snapcap', '2')
    tips_p20_1 = ctx.load_labware('opentrons_96_tiprack_20ul', '11')
    p20 = ctx.load_instrument('p20_single_gen2', 'left', tip_racks=[tips_p20_1])

    water = ctx.define_liquid(name="nuclease-free water", description="nuclease-free water", display_color="#9EC9F0")
    buffer = ctx.define_liquid(name="assembly buffer (10x)", description="assembly buffer (10x)", display_color="#F0C987")
    vector = ctx.define_liquid(name="linearised expression vector", description="linearised expression vector", display_color="#8BC98B")
    insert = ctx.define_liquid(name="insert (synthesised gene fragment)", description="insert (synthesised gene fragment)", display_color="#C98BC9")
    enzyme = ctx.define_liquid(name="assembly enzyme mix", description="assembly enzyme mix", display_color="#E08B8B")

    tubes["A1"].load_liquid(liquid=water, volume=56)
    tubes["A2"].load_liquid(liquid=buffer, volume=34)
    tubes["A3"].load_liquid(liquid=vector, volume=34)
    tubes["A4"].load_liquid(liquid=insert, volume=32)
    tubes["A5"].load_liquid(liquid=enzyme, volume=34)

    ctx.comment("[s02] Clone into His-tagged expression vector | ROBOT | Liquid handling + temperature module.")

    # 1
    temp.set_temperature(4)

    # 2
    p20.transfer(12, tubes["A1"], [rxn["A1"]], new_tip='always')
    # 3
    p20.transfer(14, tubes["A1"], [rxn["A2"]], new_tip='always')
    # 4
    p20.transfer(2, tubes["A2"], [rxn["A1"], rxn["A2"]], new_tip='always')
    # 5
    p20.transfer(2, tubes["A3"], [rxn["A1"], rxn["A2"]], new_tip='always')
    # 6
    p20.transfer(2, tubes["A4"], [rxn["A1"]], new_tip='always')
    # 7
    p20.transfer(2, tubes["A5"], [rxn["A1"], rxn["A2"]], new_tip='always', mix_after=(5, 10))

    # 8
    temp.set_temperature(37)
    # 9
    ctx.delay(seconds=3600)
    # 10
    temp.set_temperature(4)
    # 11
    ctx.pause("HUMAN [s02]: If the supplier programme needs thermal cycling, run it in a thermocycler (not part of this robot run). Keep the reactions cold, then resume to finish the session.")
    # 12
    temp.deactivate()
