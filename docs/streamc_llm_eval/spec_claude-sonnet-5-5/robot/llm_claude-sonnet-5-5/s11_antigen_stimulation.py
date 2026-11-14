# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    tubes = ctx.load_labware('opentrons_24_tuberack_nest_1.5ml_snapcap', '2')
    cells = ctx.load_labware('corning_96_wellplate_360ul_flat', '5')
    tips_p20_1 = ctx.load_labware('opentrons_96_tiprack_20ul', '11')
    p20 = ctx.load_instrument('p20_single_gen2', 'left', tip_racks=[tips_p20_1])

    # Liquids: cells
    cell_liquid = ctx.define_liquid(
        name="THP-1 cells in medium",
        description="Seeded, differentiated THP-1 cells in medium (human prepared)",
        display_color="#C99A6B",
    )
    for row in "ABCDEFGH":
        for col in "123":
            cells[row + col].load_liquid(liquid=cell_liquid, volume=180)

    # Liquids: stimulus tubes
    tube_contents = {
        "A1": ("vehicle / buffer only", "#9EC5E8"),
        "A2": ("antigen mid dose", "#E8A33D"),
        "B1": ("LPS positive control", "#D9534F"),
        "B2": ("antigen high dose", "#B5651D"),
        "C1": ("endotoxin-matched LPS control", "#8E44AD"),
        "C2": ("antigen mid dose + polymyxin B", "#2E8B57"),
        "D1": ("antigen low dose", "#F5D76E"),
        "D2": ("mock-purification eluate", "#7F8C8D"),
    }
    for well_name, (liq_name, color) in tube_contents.items():
        liq = ctx.define_liquid(
            name=liq_name,
            description=liq_name,
            display_color=color,
        )
        tubes[well_name].load_liquid(liquid=liq, volume=90)

    ctx.comment("[s11] Stimulate differentiated THP-1 with purified antigen | ROBOT | Addition to seeded plate.")

    p20.transfer(20, tubes["A1"], [cells["A1"], cells["A2"], cells["A3"]], new_tip='always')
    p20.transfer(20, tubes["B1"], [cells["B1"], cells["B2"], cells["B3"]], new_tip='always')
    p20.transfer(20, tubes["C1"], [cells["C1"], cells["C2"], cells["C3"]], new_tip='always')
    p20.transfer(20, tubes["D1"], [cells["D1"], cells["D2"], cells["D3"]], new_tip='always')
    p20.transfer(20, tubes["A2"], [cells["E1"], cells["E2"], cells["E3"]], new_tip='always')
    p20.transfer(20, tubes["B2"], [cells["F1"], cells["F2"], cells["F3"]], new_tip='always')
    p20.transfer(20, tubes["C2"], [cells["G1"], cells["G2"], cells["G3"]], new_tip='always')
    p20.transfer(20, tubes["D2"], [cells["H1"], cells["H2"], cells["H3"]], new_tip='always')

    ctx.pause("HUMAN [s11]: Return the plate to the 37 C / 5% CO2 incubator for the stimulation period, then collect supernatants (ELISA) and lyse cells (RT-qPCR). The incubator is not automated.")
