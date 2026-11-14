from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck:
    #   Slot 1: temperature module GEN2 with 96-well aluminum block (reaction wells)
    #   Slot 2: 20 uL tip rack
    #   Slot 5: 24-tube rack (A1 = expression vector, A2 = assembly mix)
    #   Right mount: P20 single-channel GEN2
    # All volumes and the incubation temperature/time are PLACEHOLDERS pending validation.

    temp_mod = ctx.load_module("temperature module gen2", 1)
    rxn_plate = temp_mod.load_labware("opentrons_96_well_aluminum_block")
    tips = ctx.load_labware("opentrons_96_tiprack_20ul", 2)
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", 5)
    pipette = ctx.load_instrument("p20_single_gen2", "right", tip_racks=[tips])

    vector_src = tubes["A1"]
    mix_src = tubes["A2"]

    expression_vector = ctx.define_liquid(
        name="expression vector",
        description="His-tagged expression vector (placeholder source)",
        display_color="#D62728",
    )
    assembly_mix = ctx.define_liquid(
        name="assembly mix",
        description="Assembly mix (placeholder source)",
        display_color="#1F77B4",
    )
    vector_src.load_liquid(liquid=expression_vector, volume=50)  # placeholder volume
    mix_src.load_liquid(liquid=assembly_mix, volume=50)          # placeholder volume

    sample_well = rxn_plate["A1"]   # clone reaction (insert added by person)
    control_well = rxn_plate["A2"]  # no-insert assembly control

    ctx.comment(
        "[s02] Clone into His-tagged expression vector | ROBOT | "
        "Full automation of liquid handling and temperature control. "
        "PLACEHOLDER values: mix 5 uL/well, vector 2 uL/well, 50 C, 15 min."
    )

    # Step 1: assembly mix to sample and control wells (one tip, source = mix tube only)
    ctx.comment(
        "[s02] Dispense assembly mix | ROBOT | "
        "Placeholder 5 uL per well from tube A2 into A1 (sample) and A2 (no-insert control)."
    )
    pipette.pick_up_tip()
    pipette.aspirate(10, mix_src)              # placeholder: 5 uL x 2 wells
    pipette.dispense(5, sample_well)
    pipette.dispense(5, control_well)
    pipette.drop_tip()

    # Step 2: expression vector to sample and control wells (fresh tip, source = vector tube)
    ctx.comment(
        "[s02] Dispense expression vector | ROBOT | "
        "Placeholder 2 uL per well from tube A1 into A1 and A2; fresh tip, never reused."
    )
    pipette.pick_up_tip()
    pipette.aspirate(4, vector_src)            # placeholder: 2 uL x 2 wells
    pipette.dispense(2, sample_well)
    pipette.dispense(2, control_well)
    pipette.drop_tip()

    # Human step: insert addition and sealing
    ctx.pause(
        "HUMAN [s02]: Add insert DNA to sample well A1 only (control well A2 gets no insert), "
        "seal the aluminum block plate, then press resume."
    )

    # Step 3: incubation on temperature module (placeholder conditions)
    ctx.comment(
        "[s02] Incubate clone reactions | ROBOT | "
        "Hold at PLACEHOLDER 50 C for PLACEHOLDER 15 min, then deactivate."
    )
    temp_mod.set_temperature(50)               # placeholder
    ctx.delay(minutes=15)                      # placeholder
    temp_mod.deactivate()

    ctx.comment(
        "[s02] Clone reactions complete | ROBOT | "
        "No tips remain on the pipette; reactions in A1 (sample) and A2 (no-insert control)."
    )
