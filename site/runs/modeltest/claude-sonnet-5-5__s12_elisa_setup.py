from opentrons import protocol_api

# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    waste = ctx.load_labware('nest_1_reservoir_195ml', '1')
    tubes = ctx.load_labware('opentrons_24_tuberack_nest_1.5ml_snapcap', '2')
    assay = ctx.load_labware('nest_96_wellplate_200ul_flat', '3')
    sup = ctx.load_labware('nest_96_wellplate_200ul_flat', '5')
    tips_p300_1 = ctx.load_labware('opentrons_96_tiprack_300ul', '11')
    p300 = ctx.load_instrument('p300_single_gen2', 'right', tip_racks=[tips_p300_1])

    # Liquid definitions
    sup_types = {
        "A": "supernatant: vehicle / buffer only",
        "B": "supernatant: LPS positive control",
        "C": "supernatant: endotoxin-matched LPS control",
        "D": "supernatant: antigen low dose",
        "E": "supernatant: antigen mid dose",
        "F": "supernatant: antigen high dose",
        "G": "supernatant: antigen mid dose + polymyxin B",
        "H": "supernatant: mock-purification eluate",
    }
    for row, name in sup_types.items():
        liq = ctx.define_liquid(name=name, description=name + " (human prepared)", display_color="#" + {
            "A": "8888FF", "B": "FF4444", "C": "FF9944", "D": "44CC44",
            "E": "22AA88", "F": "118855", "G": "AA44CC", "H": "999999"}[row])
        for col in ("1", "2", "3"):
            sup[row + col].load_liquid(liquid=liq, volume=100)

    diluent = ctx.define_liquid(name="assay diluent", description="assay diluent", display_color="#00AAFF")
    standard = ctx.define_liquid(name="TNF-alpha top standard", description="TNF-alpha top standard", display_color="#FFCC00")
    tubes["A1"].load_liquid(liquid=diluent, volume=380)
    tubes["A2"].load_liquid(liquid=standard, volume=130)

    ctx.comment("[s12] ELISA readout | ROBOT-PARTIAL | Plate setup and washes yes; reading no.")

    # Standard curve
    ctx.comment("standard curve in column 1: 2-fold serial dilution (top standard in A1)")
    p300.transfer(100, tubes["A2"], [assay["A1"]], new_tip='always')
    p300.transfer(50, tubes["A1"],
                  [assay["B1"], assay["C1"], assay["D1"], assay["E1"], assay["F1"], assay["G1"]],
                  new_tip='always')
    rows = ["A", "B", "C", "D", "E", "F", "G"]
    for i in range(len(rows) - 1):
        p300.transfer(50, assay[rows[i] + "1"], [assay[rows[i + 1] + "1"]],
                      new_tip='always', mix_after=(3, 60))
    p300.transfer(50, assay["G1"], [waste["A1"]], new_tip='always')
    p300.transfer(50, tubes["A1"], [assay["H1"]], new_tip='always')

    # Samples
    ctx.comment("samples")
    for row in "ABCDEFGH":
        for col in (1, 2, 3):
            p300.transfer(50, sup[row + str(col)], [assay[row + str(col + 1)]],
                          new_tip='always')

    ctx.pause("HUMAN [s12]: Seal the plate and incubate 120 min (room temperature, shaking if the kit says so). Then put the plate back on the deck for the develop session. Sealing and incubation are not automated.")
