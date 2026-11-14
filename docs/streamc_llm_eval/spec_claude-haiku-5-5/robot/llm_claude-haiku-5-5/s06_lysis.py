# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck
    pel = ctx.load_labware("nest_96_wellplate_2ml_deep", "5")  # Cell pellets (supernatant removed by the human)
    res = ctx.load_labware("nest_12_reservoir_15ml", "2")  # Lysis buffer
    tips_p300_1 = ctx.load_labware("opentrons_96_tiprack_300ul", "11")  # p300 tips
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips_p300_1])

    # Initial contents
    lysis = ctx.define_liquid(
        name="lysis buffer",
        description="Lysis buffer",
        display_color="#4DA6FF",
    )
    res["A1"].load_liquid(liquid=lysis, volume=2300)

    # [s06] Lyse cells; clarify lysate
    ctx.comment(
        "[s06] Lyse cells; clarify lysate | ROBOT-PARTIAL | "
        "Reagent addition yes; sonication/centrifugation no."
    )

    # Action 1: add lysis buffer to pellets (fresh tip per well, mix after)
    p300.transfer(
        300,
        res["A1"],
        [
            pel["A1"],
            pel["B1"],
            pel["C1"],
            pel["D1"],
            pel["E1"],
            pel["F1"],
        ],
        new_tip="always",
        mix_after=(5, 200),
    )

    # Safety: ensure no tip remains on the pipette
    if p300.has_tip:
        p300.drop_tip()

    # Action 2: human hand-off
    ctx.pause(
        "HUMAN [s06]: Incubate / sonicate as the lysis method requires, then centrifuge to clarify. "
        "Sonication and centrifugation are not automated. "
        "Transfer clarified lysate to the purification plate (next session)."
    )
