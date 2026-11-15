from opentrons import protocol_api

# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    cult = ctx.load_labware('nest_96_wellplate_2ml_deep', '5')
    tubes = ctx.load_labware('opentrons_24_tuberack_nest_1.5ml_snapcap', '2')
    tips_p20_1 = ctx.load_labware('opentrons_96_tiprack_20ul', '11')
    tips_p300_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '10')
    p20 = ctx.load_instrument('p20_single_gen2', 'left', tip_racks=[tips_p20_1])
    p300 = ctx.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_p300_1])

    # Liquids
    induce_culture = ctx.define_liquid(
        name="expression culture (to be induced)",
        description="expression culture (to be induced), human prepared",
        display_color="#2E8B57")
    control_culture = ctx.define_liquid(
        name="expression culture (uninduced control)",
        description="expression culture (uninduced control), human prepared",
        display_color="#8FBC8F")
    inducer = ctx.define_liquid(
        name="inducer stock",
        description="inducer stock, robot loaded",
        display_color="#FF8C00")
    vehicle = ctx.define_liquid(
        name="vehicle",
        description="vehicle (same solvent, no inducer), robot loaded",
        display_color="#87CEEB")

    for w in ["A1", "B1", "C1"]:
        cult[w].load_liquid(liquid=induce_culture, volume=1000)
    for w in ["D1", "E1", "F1"]:
        cult[w].load_liquid(liquid=control_culture, volume=1000)
    tubes["A1"].load_liquid(liquid=inducer, volume=45)
    tubes["A2"].load_liquid(liquid=vehicle, volume=45)

    ctx.comment("[s05] Small-scale expression and induction | ROBOT-PARTIAL | Inducer addition yes; shaking incubation no.")

    # 1. Inducer addition
    p20.transfer(5, tubes["A1"], [cult["A1"], cult["B1"], cult["C1"]], new_tip='always')

    # 2. Vehicle addition
    p20.transfer(5, tubes["A2"], [cult["D1"], cult["E1"], cult["F1"]], new_tip='always')

    # 3-8. Mixing with fresh tip per well
    for w in ["A1", "B1", "C1", "D1", "E1", "F1"]:
        p300.pick_up_tip()
        p300.mix(5, 100, cult[w])
        p300.drop_tip()

    # 9. Human hand-off
    ctx.pause("HUMAN [s05]: Move the plate to the shaking incubator for expression. Shaking incubation is not automated.")
