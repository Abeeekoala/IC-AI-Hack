from opentrons import protocol_api
# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    rea = ctx.load_labware('nest_12_reservoir_15ml', '2')
    assay = ctx.load_labware('nest_96_wellplate_200ul_flat', '3')
    waste = ctx.load_labware('nest_1_reservoir_195ml', '4')
    wash = ctx.load_labware('nest_1_reservoir_195ml', '5')
    tips_p300m_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '11')
    tips_p300m_2 = ctx.load_labware('opentrons_96_tiprack_300ul', '10')
    tips_p300m_3 = ctx.load_labware('opentrons_96_tiprack_300ul', '9')
    tips_p300m_4 = ctx.load_labware('opentrons_96_tiprack_300ul', '8')
    tips_p300m_5 = ctx.load_labware('opentrons_96_tiprack_300ul', '7')
    tips_p300m_6 = ctx.load_labware('opentrons_96_tiprack_300ul', '6')
    p300m = ctx.load_instrument(
        'p300_multi_gen2', 'right',
        tip_racks=[tips_p300m_1, tips_p300m_2, tips_p300m_3,
                   tips_p300m_4, tips_p300m_5, tips_p300m_6])

    # Liquids
    sample_liq = ctx.define_liquid(
        name="standard / sample", description="standard / sample (from the setup session)",
        display_color="#C0392B")
    det_ab = ctx.define_liquid(
        name="detection antibody", description="detection antibody", display_color="#2E86C1")
    strep = ctx.define_liquid(
        name="streptavidin-HRP", description="streptavidin-HRP", display_color="#28B463")
    substrate = ctx.define_liquid(
        name="substrate", description="substrate", display_color="#F1C40F")
    stop = ctx.define_liquid(
        name="stop solution", description="stop solution", display_color="#8E44AD")
    wash_liq = ctx.define_liquid(
        name="wash buffer", description="wash buffer", display_color="#AED6F1")

    for r in "ABCDEFGH":
        for c in range(1, 5):
            assay[r + str(c)].load_liquid(liquid=sample_liq, volume=50)
    rea["A1"].load_liquid(liquid=det_ab, volume=3700)
    rea["A2"].load_liquid(liquid=strep, volume=3700)
    rea["A3"].load_liquid(liquid=substrate, volume=3700)
    rea["A4"].load_liquid(liquid=stop, volume=2100)
    wash["A1"].load_liquid(liquid=wash_liq, volume=53840)

    cols = ["A1", "A2", "A3", "A4"]

    def remove(vol):
        for c in cols:
            p300m.transfer(vol, assay[c].bottom(z=1.0), waste["A1"], new_tip='always')

    def dispense_to_cols(vol, source):
        p300m.transfer(vol, source, [assay[c].top(z=-2.0) for c in cols], new_tip='once')

    def wash_cycle():
        dispense_to_cols(180, wash["A1"])
        remove(180)

    ctx.comment("[s12] ELISA readout | ROBOT-PARTIAL | Plate setup and washes yes; reading no.")

    ctx.comment("remove sample, then wash")
    remove(50)
    wash_cycle()
    wash_cycle()
    wash_cycle()

    ctx.comment("detection antibody")
    dispense_to_cols(100, rea["A1"])
    ctx.pause("HUMAN [s12]: Seal and incubate 60 min, then unseal and resume.")
    remove(100)
    wash_cycle()
    wash_cycle()
    wash_cycle()

    ctx.comment("streptavidin-HRP")
    dispense_to_cols(100, rea["A2"])
    ctx.pause("HUMAN [s12]: Seal and incubate 30 min, then unseal and resume.")
    remove(100)
    wash_cycle()
    wash_cycle()
    wash_cycle()

    ctx.comment("substrate")
    dispense_to_cols(100, rea["A3"])
    ctx.pause("HUMAN [s12]: Protect from light and incubate 20 min, then resume.")
    dispense_to_cols(50, rea["A4"])
    ctx.pause("HUMAN [s12]: Read the plate in a plate reader at the kit's wavelength. Reading is not automated.")
