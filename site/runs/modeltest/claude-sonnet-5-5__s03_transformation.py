# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.
from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    temp = ctx.load_module('temperature module gen2', '3')
    blk = temp.load_labware('opentrons_96_aluminumblock_nest_wellplate_100ul')
    tubes = ctx.load_labware('opentrons_24_tuberack_nest_1.5ml_snapcap', '2')
    dna = ctx.load_labware('biorad_96_wellplate_200ul_pcr', '5')
    tips_p20_1 = ctx.load_labware('opentrons_96_tiprack_20ul', '11')
    tips_p300_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '10')
    p20 = ctx.load_instrument('p20_single_gen2', 'left', tip_racks=[tips_p20_1])
    p300 = ctx.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_p300_1])

    cells = ctx.define_liquid(
        name="competent cells",
        description="Competent cells (human_prepared)",
        display_color="#F5A623",
    )
    cand = ctx.define_liquid(
        name="assembled plasmid (candidate)",
        description="Assembled plasmid, candidate (robot_loaded)",
        display_color="#4A90E2",
    )
    ctrl = ctx.define_liquid(
        name="assembled plasmid (no-insert control)",
        description="Assembled plasmid, no-insert control (robot_loaded)",
        display_color="#7ED321",
    )
    soc = ctx.define_liquid(
        name="SOC / recovery medium",
        description="SOC / recovery medium (robot_loaded)",
        display_color="#D0021B",
    )

    blk["A1"].load_liquid(liquid=cells, volume=25)
    blk["A2"].load_liquid(liquid=cells, volume=25)
    blk["A3"].load_liquid(liquid=cells, volume=25)
    dna["A1"].load_liquid(liquid=cand, volume=4)
    dna["A2"].load_liquid(liquid=ctrl, volume=4)
    tubes["A1"].load_liquid(liquid=soc, volume=180)

    ctx.comment("[s03] Transform expression host | ROBOT-PARTIAL | Temperature module; plating is manual.")

    temp.set_temperature(4)

    ctx.pause("HUMAN [s03]: Confirm 25 uL competent cells are in A1-A3 of the block, held at 4 C, then resume.")

    p20.transfer(1, dna["A1"], [blk["A1"]], new_tip='always')
    p20.transfer(1, dna["A2"], [blk["A2"]], new_tip='always')

    ctx.delay(seconds=1800)

    temp.set_temperature(42)
    ctx.delay(seconds=45)

    temp.set_temperature(4)
    ctx.delay(seconds=120)

    p300.transfer(50, tubes["A1"], [blk["A1"], blk["A2"], blk["A3"]], new_tip='always')

    ctx.pause("HUMAN [s03]: Recover the cells (typically shaking at 37 C), then plate on selective agar BY HAND. Plating is not automated.")

    temp.deactivate()
