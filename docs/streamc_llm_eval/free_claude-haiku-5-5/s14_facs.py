from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Placeholder volumes throughout (50 uL per well, 5000 uL reagent). Replace with validated values before use.
    ctx.comment(
        "[s14] FACS readout | ROBOT | Staining prep only. Placeholder volumes "
        "(50 uL per well, 5000 uL reagent) - replace with validated values before use."
    )

    # Deck layout
    tiprack = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    reagent_rack = ctx.load_labware("nest_12_reservoir_15ml", "2")
    sample_plate = ctx.load_labware("corning_96_wellplate_360ul_flat", "5")
    pipette = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tiprack])

    # Liquid definition and initial contents (placeholder)
    stain_mix = ctx.define_liquid(
        name="Staining antibody mix (placeholder)",
        description="FACS staining mix, placeholder volume",
        display_color="#E53935",
    )
    reagent_rack["A1"].load_liquid(liquid=stain_mix, volume=5000)

    target_wells = ["A1", "A2", "A3", "A4", "A5", "A6"]
    transfer_volume = 50  # uL, placeholder

    ctx.comment(
        "[s14] FACS readout | ROBOT | Dispense placeholder 50 uL staining mix into "
        "sample wells A1-A6, one fresh tip per well."
    )
    for well_name in target_wells:
        pipette.pick_up_tip()
        pipette.aspirate(transfer_volume, reagent_rack["A1"])
        pipette.dispense(transfer_volume, sample_plate[well_name])
        pipette.drop_tip()

    ctx.comment(
        "[s14] FACS readout | ROBOT | Staining prep complete; no tip left on pipette."
    )
    ctx.pause(
        "HUMAN [s14]: Staining prep is complete. Cover the plate, incubate per the "
        "FACS SOP, then run acquisition on the flow cytometer."
    )
