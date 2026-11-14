# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Labware
    el = ctx.load_labware('nest_96_wellplate_200ul_flat', '3')  # Eluates from purification
    rem = ctx.load_labware('nest_96_wellplate_2ml_deep', '4')  # Endotoxin-removal plate
    res = ctx.load_labware('nest_12_reservoir_15ml', '5')  # Removal reagent
    tips_p300_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '11')  # p300 tips
    p300 = ctx.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_p300_1])

    # Liquid definitions
    candidate = ctx.define_liquid(
        name="candidate_eluate",
        description="Candidate eluate from purification",
        display_color="#1E90FF",
    )
    mock = ctx.define_liquid(
        name="empty_vector_mock_eluate",
        description="Empty-vector mock eluate",
        display_color="#32CD32",
    )
    reagent = ctx.define_liquid(
        name="endotoxin_removal_reagent",
        description="Endotoxin-removal reagent",
        display_color="#FF8C00",
    )

    # Initial contents
    el["A1"].load_liquid(liquid=candidate, volume=100)
    el["B1"].load_liquid(liquid=mock, volume=100)
    res["A1"].load_liquid(liquid=reagent, volume=600)

    # [s08] Endotoxin removal
    ctx.comment("[s08] Endotoxin removal | ROBOT-PARTIAL | Plate setup yes.")

    # 1. Candidate eluate into removal plate A1
    p300.transfer(80, el["A1"], rem["A1"], new_tip='always')

    # 2. Mock eluate into removal plate B1
    p300.transfer(80, el["B1"], rem["B1"], new_tip='always')

    # 3. Removal reagent into A1 and B1, mix after dispense 5x80 uL
    p300.transfer(
        50,
        res["A1"],
        [rem["A1"], rem["B1"]],
        new_tip='always',
        mix_after=(5, 80),
    )

    # 4. Human hand-off
    ctx.pause("HUMAN [s08]: Incubate / separate per the chosen endotoxin-removal method (phase separation, resin or column), then recover the endotoxin-reduced protein into endotoxin-free tubes. These steps are not automated.")
