from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # ---------------- Deck ----------------
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    rack = ctx.load_labware("opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap", "2")
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", "3")
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", "5")
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # ---------------- Liquids ----------------
    water = ctx.define_liquid("LAL reagent water (buffer blank)", "LAL reagent water", "#A0C4FF")
    lal = ctx.define_liquid("LAL reagent", "LAL reagent", "#FFD6A5")
    std = ctx.define_liquid("Endotoxin standard dilutions", "Pre-diluted endotoxin standards", "#FFADAD")
    smp = ctx.define_liquid("Test samples", "Samples for residual endotoxin", "#CAFFBF")

    reservoir["A1"].load_liquid(liquid=water, volume=5000)
    reservoir["A2"].load_liquid(liquid=lal, volume=5000)
    std_tubes = ["A1", "A2", "A3", "A4"]
    smp_tubes = ["B1", "B2", "B3"]
    for t in std_tubes:
        rack[t].load_liquid(liquid=std, volume=500)
    for t in smp_tubes:
        rack[t].load_liquid(liquid=smp, volume=500)

    # Placeholder-sized volumes (not final assay values)
    SAMPLE_VOL = 50   # uL per well, placeholder
    REAGENT_VOL = 50  # uL per well, placeholder

    ctx.comment("[s09] Quantify residual endotoxin (LAL) | ROBOT | Plate setup only. "
                "All volumes (50 uL sample/standard/blank, 50 uL LAL reagent) are PLACEHOLDER-sized.")

    ctx.pause("HUMAN [s09]: Confirm the residual endotoxin release limit has been set "
              "(NOT YET SET - decide before running). Load pre-diluted LAL standards in rack A1-A4, "
              "samples in rack B1-B3, reagent water in reservoir A1, LAL reagent in reservoir A2, "
              "and an empty 96-well plate in slot 5.")

    # ---------------- Standard curve (duplicates, rows A-D, cols 1-2) ----------------
    ctx.comment("[s09] LAL standard curve | ROBOT | Standards in duplicate, placeholder volumes")
    for row, t in zip("ABCD", std_tubes):
        p300.pick_up_tip()
        p300.aspirate(2 * SAMPLE_VOL, rack[t])
        p300.dispense(SAMPLE_VOL, plate[row + "1"])
        p300.dispense(SAMPLE_VOL, plate[row + "2"])
        p300.drop_tip()

    # ---------------- Buffer blank (row E, cols 1-2) ----------------
    ctx.comment("[s09] Buffer blank | ROBOT | Reagent water blank in duplicate, placeholder volumes")
    p300.pick_up_tip()
    p300.aspirate(2 * SAMPLE_VOL, reservoir["A1"])
    p300.dispense(SAMPLE_VOL, plate["E1"])
    p300.dispense(SAMPLE_VOL, plate["E2"])
    p300.drop_tip()

    # ---------------- Samples (rows F-H, cols 1-2) ----------------
    ctx.comment("[s09] Sample loading | ROBOT | Samples in duplicate, placeholder volumes")
    for row, t in zip("FGH", smp_tubes):
        p300.pick_up_tip()
        p300.aspirate(2 * SAMPLE_VOL, rack[t])
        p300.dispense(SAMPLE_VOL, plate[row + "1"])
        p300.dispense(SAMPLE_VOL, plate[row + "2"])
        p300.drop_tip()

    # ---------------- LAL reagent addition ----------------
    ctx.comment("[s09] LAL reagent addition | ROBOT | Placeholder volume added to all used wells")
    p300.pick_up_tip()
    for row in "ABCDEFGH":
        for col in ("1", "2"):
            p300.aspirate(REAGENT_VOL, reservoir["A2"])
            p300.dispense(REAGENT_VOL, plate[row + col].top(z=-1))
    p300.drop_tip()

    ctx.comment("[s09] Plate setup complete | ROBOT | Reading requires a plate reader")
    ctx.pause("HUMAN [s09]: Move the plate to the plate reader, incubate and read per the LAL kit, "
              "then compare residual endotoxin against the release limit. Do NOT start any THP-1 work "
              "until the gate is passed.")
