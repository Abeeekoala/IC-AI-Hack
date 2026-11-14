# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    ctx.comment("[s04] Colony PCR to confirm insert | ROBOT | Reaction assembly only.")

    # Deck
    temp = ctx.load_module("temperature module gen2", "3")
    rxn = temp.load_labware("opentrons_96_aluminumblock_nest_wellplate_100ul")
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")
    tmpl = ctx.load_labware("biorad_96_wellplate_200ul_pcr", "5")
    tips_p20_1 = ctx.load_labware("opentrons_96_tiprack_20ul", "11")
    tips_p300_1 = ctx.load_labware("opentrons_96_tiprack_300ul", "10")
    p20 = ctx.load_instrument("p20_single_gen2", "left", tip_racks=[tips_p20_1])
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips_p300_1])

    # Liquids
    c1 = ctx.define_liquid(name="candidate colony 1 resuspension", description="human_prepared", display_color="#FF0000")
    c2 = ctx.define_liquid(name="candidate colony 2 resuspension", description="human_prepared", display_color="#FF3333")
    c3 = ctx.define_liquid(name="candidate colony 3 resuspension", description="human_prepared", display_color="#FF6666")
    c4 = ctx.define_liquid(name="candidate colony 4 resuspension", description="human_prepared", display_color="#FF9999")
    ev = ctx.define_liquid(name="empty-vector colony control resuspension", description="human_prepared", display_color="#FFCC00")
    water = ctx.define_liquid(name="nuclease-free water", description="robot_loaded", display_color="#0000FF")
    mmix = ctx.define_liquid(name="2x PCR master mix", description="robot_loaded", display_color="#00FF00")
    fwd = ctx.define_liquid(name="forward primer", description="robot_loaded", display_color="#FF00FF")
    rev = ctx.define_liquid(name="reverse primer", description="robot_loaded", display_color="#00FFFF")

    tmpl["A1"].load_liquid(liquid=c1, volume=10)
    tmpl["B1"].load_liquid(liquid=c2, volume=10)
    tmpl["C1"].load_liquid(liquid=c3, volume=10)
    tmpl["D1"].load_liquid(liquid=c4, volume=10)
    tmpl["E1"].load_liquid(liquid=ev, volume=10)
    tubes["A1"].load_liquid(liquid=water, volume=87)
    tubes["A2"].load_liquid(liquid=mmix, volume=110)
    tubes["A3"].load_liquid(liquid=fwd, volume=38)
    tubes["A4"].load_liquid(liquid=rev, volume=38)

    # 1. Temperature
    ctx.comment("[s04.1] Chill reaction block | ROBOT | Set temperature module to 4 C and wait.")
    temp.set_temperature(4)

    # 2. Master mix assembly
    ctx.comment("[s04.2] Assemble master mix | ROBOT | Assemble the master mix in tube B1.")

    # 3
    ctx.comment("[s04.3] Add nuclease-free water | ROBOT | 56 uL water to B1.")
    p300.transfer(56, tubes["A1"], tubes["B1"], new_tip="always")

    # 4
    ctx.comment("[s04.4] Add 2x master mix | ROBOT | 80 uL 2x PCR master mix to B1.")
    p300.transfer(80, tubes["A2"], tubes["B1"], new_tip="always")

    # 5
    ctx.comment("[s04.5] Add forward primer | ROBOT | 8 uL forward primer to B1.")
    p20.transfer(8, tubes["A3"], tubes["B1"], new_tip="always")

    # 6
    ctx.comment("[s04.6] Add reverse primer | ROBOT | 8 uL reverse primer to B1.")
    p20.transfer(8, tubes["A4"], tubes["B1"], new_tip="always")

    # 7
    ctx.comment("[s04.7] Mix master mix | ROBOT | 5x 50 uL mix at B1 with fresh p300 tip.")
    p300.pick_up_tip()
    p300.mix(5, 50, tubes["B1"])
    p300.drop_tip()

    # 8
    ctx.comment("[s04.8] Distribute master mix, then add template | ROBOT | Distribute 19 uL per well, then 1 uL template.")

    # 9
    ctx.comment("[s04.9] Distribute master mix | ROBOT | 19 uL to A1-F1 of reaction block.")
    p20.distribute(
        19,
        tubes["B1"],
        [rxn["A1"], rxn["B1"], rxn["C1"], rxn["D1"], rxn["E1"], rxn["F1"]],
        disposal_volume=1,
        new_tip="once",
    )
    p20.drop_tip()

    # 10-14 template additions
    ctx.comment("[s04.10] Add colony 1 template | ROBOT | 1 uL tmpl:A1 to rxn:A1.")
    p20.transfer(1, tmpl["A1"], rxn["A1"], new_tip="always", mix_after=(3, 10))

    ctx.comment("[s04.11] Add colony 2 template | ROBOT | 1 uL tmpl:B1 to rxn:B1.")
    p20.transfer(1, tmpl["B1"], rxn["B1"], new_tip="always", mix_after=(3, 10))

    ctx.comment("[s04.12] Add colony 3 template | ROBOT | 1 uL tmpl:C1 to rxn:C1.")
    p20.transfer(1, tmpl["C1"], rxn["C1"], new_tip="always", mix_after=(3, 10))

    ctx.comment("[s04.13] Add colony 4 template | ROBOT | 1 uL tmpl:D1 to rxn:D1.")
    p20.transfer(1, tmpl["D1"], rxn["D1"], new_tip="always", mix_after=(3, 10))

    ctx.comment("[s04.14] Add empty-vector control | ROBOT | 1 uL tmpl:E1 to rxn:E1.")
    p20.transfer(1, tmpl["E1"], rxn["E1"], new_tip="always", mix_after=(3, 10))

    # 15 no-template control
    ctx.comment("[s04.15] Add no-template control | ROBOT | 1 uL water from tubes:A1 to rxn:F1.")
    p20.transfer(1, tubes["A1"], rxn["F1"], new_tip="always", mix_after=(3, 10))

    # 16 human hand-off
    ctx.comment("[s04.16] Hand-off to operator | HUMAN | Seal plate and run PCR and readout off-robot.")
    ctx.pause("HUMAN [s04]: Seal the plate and run the PCR programme and gel/readout OFF the robot (thermocycling and reading are not automated).")

    # 17
    ctx.comment("[s04.17] Deactivate block | ROBOT | Turn off temperature module.")
    temp.deactivate()
