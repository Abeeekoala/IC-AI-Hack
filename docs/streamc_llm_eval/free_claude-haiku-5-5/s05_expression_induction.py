from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    ctx.comment(
        "[s05] Small-scale expression and induction | ROBOT | "
        "Partial automation: robot adds inducer; shaking incubation is manual. "
        "All volumes are PLACEHOLDER values and must be verified before use."
    )

    # Deck layout
    # Slot 1: deep-well culture plate (A1-A3 induced, A4 uninduced control)
    # Slot 2: reservoir holding inducer stock
    # Slot 3: 300 uL tip rack
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "3")
    culture = ctx.load_labware("nest_96_wellplate_2ml_deep", "1")
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", "2")
    pipette = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # Liquid definitions
    culture_liquid = ctx.define_liquid(
        name="uninduced_culture",
        description="Expression culture before induction (placeholder).",
        display_color="#A0C4FF",
    )
    inducer_liquid = ctx.define_liquid(
        name="inducer",
        description="Inducer stock solution (placeholder).",
        display_color="#FFB703",
    )

    # Initial contents (placeholder volumes)
    for well in ["A1", "A2", "A3", "A4"]:
        culture[well].load_liquid(liquid=culture_liquid, volume=1000)
    reservoir["A1"].load_liquid(liquid=inducer_liquid, volume=5000)

    # Step s05.1: inducer addition to induced wells
    ctx.comment(
        "[s05.1] Inducer addition to induced wells | ROBOT | "
        "PLACEHOLDER: 30 uL inducer aspirated once from reservoir A1 and "
        "dispensed 10 uL into each of A1, A2, A3. A4 is the uninduced control "
        "and receives no inducer."
    )
    pipette.pick_up_tip()
    pipette.aspirate(30, reservoir["A1"])
    for well in ["A1", "A2", "A3"]:
        pipette.dispense(10, culture[well])
    pipette.drop_tip()

    # Step s05.2: manual incubation with shaking
    ctx.comment(
        "[s05.2] Shaking incubation | HUMAN | "
        "Shaking incubation is not automated; the operator handles the plate."
    )
    ctx.pause(
        "HUMAN [s05.2]: Seal the culture plate and place it on the shaker for "
        "incubation. Resume the protocol when incubation is complete."
    )
