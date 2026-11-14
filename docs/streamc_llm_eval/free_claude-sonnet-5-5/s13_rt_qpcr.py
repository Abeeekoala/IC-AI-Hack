from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # ---------------- Deck ----------------
    tips = ctx.load_labware("opentrons_96_tiprack_20ul", "1")
    reagents = ctx.load_labware("opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap", "2")
    qpcr_plate = ctx.load_labware("nest_96_wellplate_100ul_pcr_full_skirt", "3")
    template_plate = ctx.load_labware("nest_96_wellplate_100ul_pcr_full_skirt", "5")
    p20 = ctx.load_instrument("p20_single_gen2", "left", tip_racks=[tips])

    # ---------------- Liquids ----------------
    mm_liq = ctx.define_liquid("RT-qPCR master mix", "RT-qPCR master mix with primers/probe (placeholder)", "#E4572E")
    water_liq = ctx.define_liquid("Nuclease-free water", "No-template control water", "#76B7EA")
    tmpl_liq = ctx.define_liquid("RNA template", "RNA template samples (placeholder)", "#59A14F")

    mm_well = reagents["A1"]
    water_well = reagents["B1"]
    mm_well.load_liquid(liquid=mm_liq, volume=200)
    water_well.load_liquid(liquid=water_liq, volume=100)

    sample_wells = ["A1", "B1", "C1"]
    for w in sample_wells:
        template_plate[w].load_liquid(liquid=tmpl_liq, volume=20)

    # Placeholder-sized volumes (uL)
    MM_VOL = 15
    TEMPLATE_VOL = 5
    NTC_VOL = 5
    dest_wells = ["A1", "B1", "C1", "D1"]  # 3 samples + 1 NTC

    ctx.comment("[s13] RT QPCR readout | ROBOT | Reaction setup only. NOTE: all volumes "
                "(15 uL master mix, 5 uL template/NTC water) are placeholder-sized, "
                "not validated assay values.")

    # ---------------- Master mix distribution ----------------
    ctx.comment("[s13] RT QPCR readout | ROBOT | Dispense master mix into qPCR plate wells A1-D1 (one tip, source only)")
    p20.pick_up_tip()
    for w in dest_wells:
        p20.aspirate(MM_VOL, mm_well)
        p20.dispense(MM_VOL, qpcr_plate[w])
        p20.blow_out()
    p20.drop_tip()

    # ---------------- Template addition ----------------
    ctx.comment("[s13] RT QPCR readout | ROBOT | Add template to qPCR wells A1-C1 (fresh tip per sample)")
    for src, dst in zip(sample_wells, dest_wells[:3]):
        p20.pick_up_tip()
        p20.aspirate(TEMPLATE_VOL, template_plate[src])
        p20.dispense(TEMPLATE_VOL, qpcr_plate[dst])
        p20.mix(3, 10, qpcr_plate[dst])
        p20.blow_out()
        p20.drop_tip()

    ctx.comment("[s13] RT QPCR readout | ROBOT | Add water no-template control to qPCR well D1")
    p20.pick_up_tip()
    p20.aspirate(NTC_VOL, water_well)
    p20.dispense(NTC_VOL, qpcr_plate["D1"])
    p20.mix(3, 10, qpcr_plate["D1"])
    p20.blow_out()
    p20.drop_tip()

    # ---------------- Human steps ----------------
    ctx.comment("[s13] RT QPCR readout | HUMAN | Seal plate, thermocycle and read fluorescence")
    ctx.pause("HUMAN [s13]: Seal the qPCR plate in slot 3, spin it down, and move it to the real-time PCR instrument. "
              "Run the RT-qPCR thermocycling program and read the fluorescence data. "
              "Thermocycling and reading are not performed by the robot.")

    ctx.comment("[s13] RT QPCR readout | ROBOT | Robot-owned reaction setup complete")
