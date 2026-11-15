# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.
from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    pel = ctx.load_labware('nest_96_wellplate_2ml_deep', '5')
    res = ctx.load_labware('nest_12_reservoir_15ml', '2')
    tips_p300_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '11')
    p300 = ctx.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_p300_1])

    lysis = ctx.define_liquid(
        name="lysis buffer",
        description="Lysis buffer",
        display_color="#33AA55",
    )
    res["A1"].load_liquid(liquid=lysis, volume=2300)

    ctx.comment("[s06] Lyse cells; clarify lysate | ROBOT-PARTIAL | Reagent addition yes; sonication/centrifugation no.")

    targets = [pel[w] for w in ["A1", "B1", "C1", "D1", "E1", "F1"]]
    p300.transfer(
        300,
        res["A1"],
        targets,
        new_tip='always',
        mix_after=(5, 200),
    )

    ctx.pause("HUMAN [s06]: Incubate / sonicate as the lysis method requires, then centrifuge to clarify. Sonication and centrifugation are not automated. Transfer clarified lysate to the purification plate (next session).")
