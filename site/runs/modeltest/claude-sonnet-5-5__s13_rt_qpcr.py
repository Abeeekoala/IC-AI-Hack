from opentrons import protocol_api

# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    temp = ctx.load_module("temperature module gen2", "3")
    q = temp.load_labware("opentrons_96_aluminumblock_nest_wellplate_100ul")
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")
    cd = ctx.load_labware("biorad_96_wellplate_200ul_pcr", "5")
    tips_p20_1 = ctx.load_labware("opentrons_96_tiprack_20ul", "11")
    tips_p300_1 = ctx.load_labware("opentrons_96_tiprack_300ul", "10")
    p20 = ctx.load_instrument("p20_single_gen2", "left", tip_racks=[tips_p20_1])
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips_p300_1])

    # ---- liquids ----
    cdna_defs = {
        "A1": "cDNA: vehicle / buffer only",
        "A2": "cDNA: vehicle / buffer only",
        "A3": "cDNA: vehicle / buffer only",
        "A4": "no-RT control (vehicle RNA, reverse transcriptase omitted)",
        "B1": "cDNA: LPS positive control",
        "B2": "cDNA: LPS positive control",
        "B3": "cDNA: LPS positive control",
        "C1": "cDNA: endotoxin-matched LPS control",
        "C2": "cDNA: endotoxin-matched LPS control",
        "C3": "cDNA: endotoxin-matched LPS control",
        "D1": "cDNA: antigen low dose",
        "D2": "cDNA: antigen low dose",
        "D3": "cDNA: antigen low dose",
        "E1": "cDNA: antigen mid dose",
        "E2": "cDNA: antigen mid dose",
        "E3": "cDNA: antigen mid dose",
        "F1": "cDNA: antigen high dose",
        "F2": "cDNA: antigen high dose",
        "F3": "cDNA: antigen high dose",
        "G1": "cDNA: antigen mid dose + polymyxin B",
        "G2": "cDNA: antigen mid dose + polymyxin B",
        "G3": "cDNA: antigen mid dose + polymyxin B",
        "H1": "cDNA: mock-purification eluate",
        "H2": "cDNA: mock-purification eluate",
        "H3": "cDNA: mock-purification eluate",
    }
    liq_cache = {}
    for well_name, desc in cdna_defs.items():
        if desc not in liq_cache:
            liq_cache[desc] = ctx.define_liquid(
                name=desc, description=desc + " (human prepared)", display_color="#C0504D"
            )
        cd[well_name].load_liquid(liquid=liq_cache[desc], volume=20)

    water = ctx.define_liquid(name="nuclease-free water", description="nuclease-free water", display_color="#9DC3E6")
    mm = ctx.define_liquid(name="2x qPCR master mix", description="2x qPCR master mix", display_color="#F4B183")
    gapdh = ctx.define_liquid(name="GAPDH (reference) primer pair", description="GAPDH primer pair", display_color="#70AD47")
    tnf = ctx.define_liquid(name="TNF primer pair", description="TNF primer pair", display_color="#FFC000")
    il1b = ctx.define_liquid(name="IL1B primer pair", description="IL1B primer pair", display_color="#7030A0")
    tubes["A1"].load_liquid(liquid=water, volume=462)
    tubes["A2"].load_liquid(liquid=mm, volume=870)
    tubes["A3"].load_liquid(liquid=gapdh, volume=58)
    tubes["A4"].load_liquid(liquid=tnf, volume=58)
    tubes["A5"].load_liquid(liquid=il1b, volume=58)

    ctx.comment("[s13] RT QPCR readout | ROBOT-PARTIAL | Reaction setup yes; thermocycling and reading no.")

    # 1
    temp.set_temperature(4)

    rows = "ABCDEFGH"
    sample_wells = [r + str(c) for r in rows for c in (1, 2, 3)]

    def run_gene(label, mm_tube, primer_tube, offset):
        ctx.comment(label + ": assemble master mix in tube " + mm_tube)
        p300.transfer(140, tubes["A1"], [tubes[mm_tube]], new_tip="always")
        p300.transfer(280, tubes["A2"], [tubes[mm_tube]], new_tip="always")
        p300.transfer(28, tubes[primer_tube], [tubes[mm_tube]], new_tip="always")
        p300.pick_up_tip()
        p300.mix(5, 50, tubes[mm_tube])
        p300.drop_tip()

        dest_names = [r + str(c + offset) for r in rows for c in (1, 2, 3)]
        dest_names += ["A" + str(4 + offset), "B" + str(4 + offset)]
        p20.distribute(
            16,
            tubes[mm_tube],
            [q[w] for w in dest_names],
            disposal_volume=1,
            new_tip="once",
        )

        for w in sample_wells:
            r = w[0]
            c = int(w[1:])
            p20.transfer(
                4,
                cd[w],
                [q[r + str(c + offset)]],
                new_tip="always",
                mix_after=(3, 10),
            )
        p20.transfer(
            4,
            tubes["A1"],
            [q["A" + str(4 + offset)]],
            new_tip="always",
            mix_after=(3, 10),
        )
        p20.transfer(
            4,
            cd["A4"],
            [q["B" + str(4 + offset)]],
            new_tip="always",
            mix_after=(3, 10),
        )

    run_gene("GAPDH (reference)", "B1", "A3", 0)
    run_gene("TNF", "B2", "A4", 4)
    run_gene("IL1B", "B3", "A5", 8)

    ctx.pause("HUMAN [s13]: Seal the plate, spin it down and load the qPCR instrument. Thermocycling and reading are not automated.")
    temp.deactivate()
