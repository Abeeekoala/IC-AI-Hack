from opentrons import protocol_api
# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    tubes = ctx.load_labware('opentrons_24_tuberack_nest_1.5ml_snapcap', '2')
    assay = ctx.load_labware('nest_96_wellplate_200ul_flat', '3')
    tips_p20_1 = ctx.load_labware('opentrons_96_tiprack_20ul', '11')
    tips_p300_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '10')
    p20 = ctx.load_instrument('p20_single_gen2', 'left', tip_racks=[tips_p20_1])
    p300 = ctx.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_p300_1])

    water = ctx.define_liquid(name="LAL reagent water (diluent)", description="LAL reagent water (diluent)", display_color="#4A90D9")
    std = ctx.define_liquid(name="Endotoxin standard (top concentration)", description="endotoxin standard (top concentration)", display_color="#D0021B")
    cand = ctx.define_liquid(name="Candidate sample (endotoxin-reduced)", description="candidate sample (endotoxin-reduced)", display_color="#7ED321")
    mock = ctx.define_liquid(name="Mock eluate sample (endotoxin-reduced)", description="mock eluate sample (endotoxin-reduced)", display_color="#F5A623")
    lal = ctx.define_liquid(name="LAL reagent", description="LAL reagent", display_color="#9013FE")

    tubes["A1"].load_liquid(liquid=water, volume=415)
    tubes["A2"].load_liquid(liquid=std, volume=96)
    tubes["A3"].load_liquid(liquid=cand, volume=130)
    tubes["A4"].load_liquid(liquid=mock, volume=130)
    tubes["A5"].load_liquid(liquid=lal, volume=680)

    ctx.comment("[s09] Quantify residual endotoxin (LAL) | ROBOT-PARTIAL | Plate setup yes; reading needs a plate reader.")

    # Standard curve
    ctx.comment("standard curve: serial dilution down column 1")
    p300.transfer(55.56, tubes["A2"], [assay["A1"]], new_tip='always')
    p300.transfer(50, tubes["A1"], [assay["B1"]], new_tip='always')
    p300.transfer(50, tubes["A1"], [assay["C1"]], new_tip='always')
    p300.transfer(50, tubes["A1"], [assay["D1"]], new_tip='always')
    p300.transfer(50, tubes["A1"], [assay["E1"]], new_tip='always')
    p300.transfer(44.44, tubes["A1"], [assay["F1"]], new_tip='always')
    p20.transfer(5.56, assay["A1"], [assay["B1"]], new_tip='always', mix_after=(3, 20))
    p20.transfer(5.56, assay["B1"], [assay["C1"]], new_tip='always', mix_after=(3, 20))
    p20.transfer(5.56, assay["C1"], [assay["D1"]], new_tip='always', mix_after=(3, 20))
    p20.transfer(5.56, assay["D1"], [assay["E1"]], new_tip='always', mix_after=(3, 20))
    p20.transfer(5.56, assay["E1"], [assay["F1"]], new_tip='always', mix_after=(3, 20))
    p300.transfer(50, tubes["A1"], [assay["G1"]], new_tip='always')

    # Samples
    ctx.comment("samples in column 2: neat, diluted, and spiked (positive product control)")
    p300.transfer(50, tubes["A3"], [assay["A2"]], new_tip='always')
    p300.transfer(45, tubes["A1"], [assay["B2"]], new_tip='always')
    p20.transfer(5, tubes["A3"], [assay["B2"]], new_tip='always', mix_after=(3, 20))
    p300.transfer(45, tubes["A3"], [assay["C2"]], new_tip='always')
    p20.transfer(5, tubes["A2"], [assay["C2"]], new_tip='always', mix_after=(3, 20))
    p300.transfer(50, tubes["A4"], [assay["D2"]], new_tip='always')
    p300.transfer(45, tubes["A1"], [assay["E2"]], new_tip='always')
    p20.transfer(5, tubes["A4"], [assay["E2"]], new_tip='always', mix_after=(3, 20))
    p300.transfer(45, tubes["A4"], [assay["F2"]], new_tip='always')
    p20.transfer(5, tubes["A2"], [assay["F2"]], new_tip='always', mix_after=(3, 20))

    # LAL reagent
    ctx.comment("add LAL reagent to every used well (one tip, no contact with the wells)")
    used = ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "A2", "B2", "C2", "D2", "E2", "F2"]
    p300.transfer(50, tubes["A5"], [assay[w].top(z=-2.0) for w in used], new_tip='once')

    ctx.pause("HUMAN [s09]: Move the plate to the incubating plate reader and read per the kit. Reading is not automated.")
