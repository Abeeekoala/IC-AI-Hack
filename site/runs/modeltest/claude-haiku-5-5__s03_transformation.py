# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # ---- Deck ----
    temp = ctx.load_module('temperature module gen2', '3')
    blk = temp.load_labware('opentrons_96_aluminumblock_nest_wellplate_100ul')  # Competent cells
    tubes = ctx.load_labware('opentrons_24_tuberack_nest_1.5ml_snapcap', '2')   # Recovery medium
    dna = ctx.load_labware('biorad_96_wellplate_200ul_pcr', '5')                # Assembly products
    tips_p20_1 = ctx.load_labware('opentrons_96_tiprack_20ul', '11')
    tips_p300_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '10')
    p20 = ctx.load_instrument('p20_single_gen2', 'left', tip_racks=[tips_p20_1])
    p300 = ctx.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_p300_1])

    # ---- Liquids ----
    competent = ctx.define_liquid(
        name='competent cells',
        description='Competent expression host cells',
        display_color='#F0C000',
    )
    candidate = ctx.define_liquid(
        name='assembled plasmid (candidate)',
        description='Assembled plasmid, candidate construct',
        display_color='#00A0FF',
    )
    control = ctx.define_liquid(
        name='assembled plasmid (no-insert control)',
        description='Assembled plasmid, no-insert control',
        display_color='#FF6000',
    )
    soc = ctx.define_liquid(
        name='SOC / recovery medium',
        description='SOC recovery medium',
        display_color='#80FF80',
    )

    blk['A1'].load_liquid(liquid=competent, volume=25)
    blk['A2'].load_liquid(liquid=competent, volume=25)
    blk['A3'].load_liquid(liquid=competent, volume=25)
    dna['A1'].load_liquid(liquid=candidate, volume=4)
    dna['A2'].load_liquid(liquid=control, volume=4)
    tubes['A1'].load_liquid(liquid=soc, volume=180)

    # ---- s03 ----
    ctx.comment("[s03] Transform expression host | ROBOT-PARTIAL | Temperature module; plating is manual.")

    # Step 1: cool block to 4 C
    ctx.comment("[s03] step 1: set temperature module to 4 C and wait")
    temp.set_temperature(4)

    # Step 2: human hand-off
    ctx.comment("[s03] step 2: human check of competent cells on cold block")
    ctx.pause("HUMAN [s03]: Confirm 25 uL competent cells are in A1-A3 of the block, held at 4 C, then resume.")

    # Steps 3-4: add plasmid to competent cells
    ctx.comment("[s03] step 3: add 1 uL candidate plasmid to blk A1")
    p20.transfer(1, dna['A1'], blk['A1'], new_tip='always')
    ctx.comment("[s03] step 4: add 1 uL no-insert control to blk A2")
    p20.transfer(1, dna['A2'], blk['A2'], new_tip='always')

    # Step 5: incubation on cold block
    ctx.comment("[s03] step 5: incubation on cold block for 1800 s")
    ctx.delay(seconds=1800)

    # Step 6: heat shock temperature
    ctx.comment("[s03] step 6: set temperature module to 42 C and wait")
    temp.set_temperature(42)

    # Step 7: heat shock hold
    ctx.comment("[s03] step 7: heat shock hold 45 s (block ramp time dominates; validate on your hardware)")
    ctx.delay(seconds=45)

    # Step 8: return to cold
    ctx.comment("[s03] step 8: set temperature module to 4 C and wait")
    temp.set_temperature(4)

    # Step 9: cold rest
    ctx.comment("[s03] step 9: return to cold, 120 s")
    ctx.delay(seconds=120)

    # Step 10: add recovery medium to each transformation well
    ctx.comment("[s03] step 10: add 50 uL SOC from tubes A1 to blk A1, A2, A3")
    p300.transfer(
        50,
        tubes['A1'],
        [blk['A1'], blk['A2'], blk['A3']],
        new_tip='always',
    )

    # Ensure no tip is left on the pipettes
    if p300.has_tip:
        p300.drop_tip()
    if p20.has_tip:
        p20.drop_tip()

    # Step 11: human hand-off for recovery and plating
    ctx.comment("[s03] step 11: human recovery and plating hand-off")
    ctx.pause("HUMAN [s03]: Recover the cells (typically shaking at 37 C), then plate on selective agar BY HAND. Plating is not automated.")

    # Step 12: deactivate module
    ctx.comment("[s03] step 12: deactivate temperature module")
    temp.deactivate()
