from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck layout
    tips = ctx.load_labware("opentrons_96_tiprack_20ul", "1")
    culture_plate = ctx.load_labware("nest_96_wellplate_2ml_deep", "2")
    tube_rack = ctx.load_labware("opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap", "5")
    p20 = ctx.load_instrument("p20_single_gen2", "right", tip_racks=[tips])

    # Liquids
    inducer_liq = ctx.define_liquid(
        name="Inducer", description="Inducer stock (placeholder volume)", display_color="#00A651"
    )
    culture_liq = ctx.define_liquid(
        name="Culture", description="Starter culture (placeholder volume)", display_color="#C4A000"
    )
    control_liq = ctx.define_liquid(
        name="Uninduced control culture",
        description="Control culture, receives no inducer (placeholder volume)",
        display_color="#8E44AD",
    )

    inducer_tube = tube_rack["A1"]
    inducer_tube.load_liquid(liquid=inducer_liq, volume=500)

    induced_wells = ["A1", "A2", "A3"]
    control_wells = ["B1"]
    for name in induced_wells:
        culture_plate[name].load_liquid(liquid=culture_liq, volume=500)
    for name in control_wells:
        culture_plate[name].load_liquid(liquid=control_liq, volume=500)

    inducer_volume = 10  # placeholder volume (uL)

    ctx.comment(
        "[s05] Small-scale expression and induction | ROBOT | "
        "All volumes are placeholder-sized and must be replaced with real values."
    )
    ctx.comment(
        "[s05] Small-scale expression and induction | ROBOT | "
        "Inducer addition to wells A1-A3; uninduced control well B1 receives no inducer."
    )

    for name in induced_wells:
        p20.pick_up_tip()
        p20.aspirate(inducer_volume, inducer_tube)
        p20.dispense(inducer_volume, culture_plate[name])
        p20.drop_tip()

    ctx.comment(
        "[s05] Small-scale expression and induction | ROBOT | "
        "Inducer addition complete; uninduced control B1 left untouched."
    )

    ctx.pause(
        "HUMAN [s05]: Move the culture plate to the shaking incubator for the expression/induction "
        "incubation, then return it to the deck when finished."
    )
