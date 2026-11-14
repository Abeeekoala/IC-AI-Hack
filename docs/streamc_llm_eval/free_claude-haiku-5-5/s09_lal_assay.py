from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # ---- Labware / deck --------------------------------------------------
    # Slot 1: 96-well flat assay plate (standard curve + blanks + LAL reagent)
    # Slot 2: 300 uL tip rack
    # Slot 3: 12-well reservoir (A1 buffer, A2 LAL reagent)
    # Slot 4: 24-tube rack (A1 endotoxin standard stock)
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", 1)
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", 2)
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", 3)
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", 4)
    pip = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # ---- Liquids (placeholder volumes throughout) ------------------------
    buffer = ctx.define_liquid(
        name="LAL assay buffer (diluent/blank)",
        description="Endotoxin-free assay buffer",
        display_color="#4F86F7",
    )
    lal = ctx.define_liquid(
        name="LAL reagent",
        description="Limulus amebocyte lysate reagent",
        display_color="#F7C44F",
    )
    std = ctx.define_liquid(
        name="Endotoxin standard stock",
        description="Endotoxin standard for LAL curve",
        display_color="#D9534F",
    )

    # PLACEHOLDER volumes: replace with validated kit values before running.
    reservoir["A1"].load_liquid(liquid=buffer, volume=6000)
    reservoir["A2"].load_liquid(liquid=lal, volume=3000)
    tubes["A1"].load_liquid(liquid=std, volume=500)

    std_wells = ["A1", "A2", "A3", "A4", "A5", "A6", "A7", "A8"]
    blank_wells = ["B1", "B2"]

    # ---- Gate ------------------------------------------------------------
    ctx.comment(
        "[s09] Quantify residual endotoxin (LAL) | ROBOT | "
        "PLACEHOLDER VOLUMES: all volumes in this protocol are placeholders "
        "pending validated LAL kit values. Plate reading is HUMAN. "
        "Controls: LAL standard curve (A1-A8), buffer blank (B1-B2)."
    )
    ctx.pause(
        "HUMAN [s09]: Residual endotoxin release limit is NOT YET SET. "
        "Set and record the limit before ANY THP-1 work; do not start THP-1 work until it is recorded."
    )
    ctx.pause(
        "HUMAN [s09]: Confirm deck: plate in slot 1 (empty), tips in slot 2, "
        "buffer and LAL reagent in reservoir A1/A2, endotoxin stock in tube rack slot 4 A1. Resume when ready."
    )

    # ---- s09.1 Standard stock into top standard well ---------------------
    ctx.comment(
        "[s09.1] Load endotoxin standard into curve top | ROBOT | "
        "PLACEHOLDER 200 uL stock into A1; fresh tip, single source."
    )
    pip.pick_up_tip()
    pip.aspirate(200, tubes["A1"])
    pip.dispense(200, plate["A1"])
    pip.drop_tip()

    # ---- s09.2 Buffer diluent to curve wells and blanks ------------------
    ctx.comment(
        "[s09.2] Dispense assay buffer to curve and blanks | ROBOT | "
        "PLACEHOLDER 100 uL per well; new tip per well from buffer source."
    )
    for well in std_wells[1:] + blank_wells:
        pip.pick_up_tip()
        pip.aspirate(100, reservoir["A1"])
        pip.dispense(100, plate[well])
        pip.drop_tip()

    # ---- s09.3 Two-fold serial dilution across the standard curve --------
    ctx.comment(
        "[s09.3] Serial dilution of standard curve | ROBOT | "
        "PLACEHOLDER 100 uL transfers, 2-fold series A1->A8; fresh tip every transfer, mix after dispense."
    )
    for src, dst in zip(std_wells[:-1], std_wells[1:]):
        pip.pick_up_tip()
        pip.aspirate(100, plate[src])
        pip.dispense(100, plate[dst])
        pip.mix(3, 80, plate[dst])
        pip.drop_tip()

    # ---- s09.4 LAL reagent to all curve and blank wells ------------------
    ctx.comment(
        "[s09.4] Add LAL reagent to all wells | ROBOT | "
        "PLACEHOLDER 100 uL per well; fresh tip per well from reagent source."
    )
    for well in std_wells + blank_wells:
        pip.pick_up_tip()
        pip.aspirate(100, reservoir["A2"])
        pip.dispense(100, plate[well])
        pip.drop_tip()

    if pip.has_tip:
        pip.drop_tip()

    # ---- Hand-off to human for incubation and reading --------------------
    ctx.comment(
        "[s09] Quantify residual endotoxin (LAL) | HUMAN | "
        "Seal plate, incubate per LAL kit insert (time PLACEHOLDER), read on plate reader, record results against the release limit."
    )
    ctx.pause(
        "HUMAN [s09]: Seal the plate, incubate per the LAL kit insert (incubation time is a placeholder), "
        "read absorbance on the plate reader, and record results against the release limit."
    )
