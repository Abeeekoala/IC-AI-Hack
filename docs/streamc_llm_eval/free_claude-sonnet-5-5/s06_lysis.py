from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck layout
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "1")
    reservoir = ctx.load_labware("nest_12_well_reservoir_15ml", "2")
    sample_plate = ctx.load_labware("nest_96_wellplate_2ml_deepwell", "3")
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # Liquids
    lysis_buffer = ctx.define_liquid(
        name="Lysis buffer",
        description="Placeholder lysis buffer",
        display_color="#00AA55",
    )
    cells = ctx.define_liquid(
        name="Cell suspension",
        description="Placeholder resuspended cell pellet",
        display_color="#CC8844",
    )
    reservoir["A1"].load_liquid(liquid=lysis_buffer, volume=5000)
    sample_wells = ["A1", "B1", "C1"]
    for w in sample_wells:
        sample_plate[w].load_liquid(liquid=cells, volume=100)

    # Placeholder volumes
    lysis_vol = 200  # uL, placeholder
    mix_vol = 150    # uL, placeholder
    mix_reps = 3

    ctx.comment("[s06] Lyse cells; clarify lysate | ROBOT | Volumes used here "
                "(200 uL lysis buffer per sample, 150 uL x3 mixing) are placeholder-sized "
                "and must be replaced with protocol values")

    ctx.comment("[s06] Lyse cells; clarify lysate | ROBOT | Reagent addition: dispense "
                "lysis buffer into each cell sample and mix, fresh tip per sample")
    for w in sample_wells:
        p300.pick_up_tip()
        p300.aspirate(lysis_vol, reservoir["A1"])
        p300.dispense(lysis_vol, sample_plate[w])
        p300.mix(mix_reps, mix_vol, sample_plate[w])
        p300.blow_out(sample_plate[w].top())
        p300.drop_tip()

    ctx.comment("[s06] Lyse cells; clarify lysate | HUMAN | Sonication and centrifugation "
                "are not automated; operator performs them")
    ctx.pause("HUMAN [s06]: Remove the sample plate from slot 3, sonicate the lysates as "
              "required, then centrifuge to clarify the lysate. Return the plate to slot 3 "
              "and resume.")

    ctx.comment("[s06] Lyse cells; clarify lysate | ROBOT | Robot-owned part complete; "
                "clarified lysate remains in the sample plate")
