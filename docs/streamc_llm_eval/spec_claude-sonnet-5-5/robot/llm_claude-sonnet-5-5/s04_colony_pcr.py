# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.
from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    temp = ctx.load_module("temperature module gen2", "3")
    rxn = temp.load_labware("opentrons_96_aluminumblock_nest_wellplate_100ul")
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")
    tmpl = ctx.load_labware("biorad_96_wellplate_200ul_pcr", "5")
    tips_p20_1 = ctx.load_labware("opentrons_96_tiprack_20ul", "11")
    tips_p300_1 = ctx.load_labware("opentrons_96_tiprack_300ul", "10")
    p20 = ctx.load_instrument("p20_single_gen2", "left", tip_racks=[tips_p20_1])
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips_p300_1])

    # Liquids
    c1 = ctx.define_liquid(name="candidate colony 1 resuspension", description="human_prepared", display_color="#FF6666")
    c2 = ctx.define_liquid(name="candidate colony 2 resuspension", description="human_prepared", display_color="#FF9966")
    c3 = ctx.define_liquid(name="candidate colony 3 resuspension", description="human_prepared", display_color="#FFCC66")
    c4 = ctx.define_liquid(name="candidate colony 4 resuspension", description="human_prepared", display_color="#CCFF66")
    c5 = ctx.define_liquid(name="empty-vector colony control resuspension", description="human_prepared", display_color="#66FFCC")
    water = ctx.define_liquid(name="nuclease-free water", description="robot_loaded", display_color="#66CCFF")
    mm = ctx.define_liquid(name="2x PCR master mix", description="robot_loaded", display_color="#6666FF")
    fwd = ctx.define_liquid(name="forward primer", description="robot_loaded", display_color="#CC66FF")
    rev = ctx.define_liquid(name="reverse primer", description="robot_loaded", display_color="#FF66CC")

    tmpl["A1"].load_liquid(liquid=c1, volume=10)
    tmpl["B1"].load_liquid(liquid=c2, volume=10)
    tmpl["C1"].load_liquid(liquid=c3, volume=10)
    tmpl["D1"].load_liquid(liquid=c4, volume=10)
    tmpl["E1"].load_liquid(liquid=c5, volume=10)
    tubes["A1"].load_liquid(liquid=water, volume=87)
    tubes["A2"].load_liquid(liquid=mm, volume=110)
    tubes["A3"].load_liquid(liquid=fwd, volume=38)
    tubes["A4"].load_liquid(liquid=rev, volume=38)

    ctx.comment("[s04] Colony PCR to confirm insert | ROBOT | Reaction assembly only.")

    # 1
    temp.set_temperature(4)

    # 2
    ctx.comment("assemble the master mix in tube B1")

    # 3-6
    p300.transfer(56, tubes["A1"], [tubes["B1"]], new_tip="always")
    p300.transfer(80, tubes["A2"], [tubes["B1"]], new_tip="always")
    p20.transfer(8, tubes["A3"], [tubes["B1"]], new_tip="always")
    p20.transfer(8, tubes["A4"], [tubes["B1"]], new_tip="always")

    # 7
    p300.pick_up_tip()
    p300.mix(5, 50, tubes["B1"])
    p300.drop_tip()

    # 8
    ctx.comment("distribute master mix, then add template")

    # 9
    p20.distribute(
        19,
        tubes["B1"],
        [rxn["A1"], rxn["B1"], rxn["C1"], rxn["D1"], rxn["E1"], rxn["F1"]],
        disposal_volume=1,
        new_tip="once",
    )

    # 10-15
    p20.transfer(1, tmpl["A1"], [rxn["A1"]], new_tip="always", mix_after=(3, 10))
    p20.transfer(1, tmpl["B1"], [rxn["B1"]], new_tip="always", mix_after=(3, 10))
    p20.transfer(1, tmpl["C1"], [rxn["C1"]], new_tip="always", mix_after=(3, 10))
    p20.transfer(1, tmpl["D1"], [rxn["D1"]], new_tip="always", mix_after=(3, 10))
    p20.transfer(1, tmpl["E1"], [rxn["E1"]], new_tip="always", mix_after=(3, 10))
    p20.transfer(1, tubes["A1"], [rxn["F1"]], new_tip="always", mix_after=(3, 10))

    # 16
    ctx.pause("HUMAN [s04]: Seal the plate and run the PCR programme and gel/readout OFF the robot (thermocycling and reading are not automated).")

    # 17
    temp.deactivate()
