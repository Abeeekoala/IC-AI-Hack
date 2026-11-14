# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    tubes = ctx.load_labware('opentrons_24_tuberack_nest_1.5ml_snapcap', '2')
    cells = ctx.load_labware('nest_96_wellplate_200ul_flat', '5')
    tips_p300_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '11')
    p300 = ctx.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_p300_1])

    # Liquids
    cocktail = ctx.define_liquid(
        name="staining antibody cocktail",
        description="staining antibody cocktail (robot_loaded)",
        display_color="#00AA55",
    )
    tubes["A1"].load_liquid(liquid=cocktail, volume=1230)

    conditions = [
        ("A", "vehicle / buffer only", "#B0B0B0"),
        ("B", "LPS positive control", "#CC3333"),
        ("C", "endotoxin-matched LPS control", "#CC8833"),
        ("D", "antigen low dose", "#99CCFF"),
        ("E", "antigen mid dose", "#3399FF"),
        ("F", "antigen high dose", "#0033CC"),
        ("G", "antigen mid dose + polymyxin B", "#9933CC"),
        ("H", "mock-purification eluate", "#996633"),
    ]
    dest_wells = []
    for row, name, color in conditions:
        liq = ctx.define_liquid(
            name="cells: " + name,
            description="cells: " + name + " (human_prepared)",
            display_color=color,
        )
        for col in ("1", "2", "3"):
            well = cells[row + col]
            well.load_liquid(liquid=liq, volume=100)
            dest_wells.append(well)

    ctx.comment("[s14] FACS readout | ROBOT-PARTIAL | Staining prep yes; acquisition no.")

    p300.transfer(
        50,
        tubes["A1"],
        [w.top(z=-2.0) for w in dest_wells],
        new_tip='once',
    )

    ctx.pause("HUMAN [s14]: Incubate protected from light, wash / centrifuge and acquire on the cytometer. None of this is automated.")
