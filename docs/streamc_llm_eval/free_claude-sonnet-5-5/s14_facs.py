from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck layout (designed here):
    #   slot 1: 300 uL tip rack
    #   slot 2: 96-well flat plate holding the cell samples (A1-H1)
    #   slot 3: 12-well reservoir, well A1 = antibody staining master mix
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    sample_plate = ctx.load_labware("corning_96_wellplate_360ul_flat", "2", "FACS sample plate")
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", "3", "Staining reagents")
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # Liquids (placeholder volumes)
    cells = ctx.define_liquid(name="Cell suspension", description="Cells to be stained (placeholder volume)", display_color="#C8A2C8")
    mix = ctx.define_liquid(name="Antibody staining mix", description="Fluorescent antibody master mix (placeholder volume)", display_color="#2E86DE")

    sample_wells = sample_plate.columns()[0]  # A1-H1
    for w in sample_wells:
        w.load_liquid(liquid=cells, volume=100)
    mix_well = reservoir["A1"]
    mix_well.load_liquid(liquid=mix, volume=1000)

    STAIN_VOL = 20  # uL per sample, placeholder-sized

    ctx.comment("[s14] FACS readout | ROBOT | Staining prep is automated; acquisition is manual. "
                "All volumes are placeholder-sized (100 uL cells/well, 20 uL staining mix/well, 1000 uL mix in reservoir).")

    ctx.pause("HUMAN [s14]: Confirm the antibody staining master mix is in reservoir well A1 (slot 3) and the cell samples are in column 1 (A1-H1) of the plate in slot 2, then resume.")

    ctx.comment("[s14] Staining prep: add staining mix to each sample | ROBOT | 20 uL per well (placeholder), fresh tip per sample")
    for well in sample_wells:
        p300.pick_up_tip()
        p300.aspirate(STAIN_VOL, mix_well)
        p300.dispense(STAIN_VOL, well)
        p300.blow_out()
        p300.drop_tip()

    ctx.comment("[s14] Acquisition | HUMAN | Flow cytometer acquisition is not automated")
    ctx.pause("HUMAN [s14]: Incubate the stained plate as required by the staining protocol (protected from light), then remove the plate and run acquisition on the flow cytometer.")
