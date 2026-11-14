from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck
    tips = ctx.load_labware("opentrons_96_tiprack_20ul", "1")
    rxn_plate = ctx.load_labware("nest_96_wellplate_100ul_pcr_full_skirt", "2")
    reagents = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "4")
    templates = ctx.load_labware("nest_96_wellplate_100ul_pcr_full_skirt", "5")
    p20 = ctx.load_instrument("p20_single_gen2", "right", tip_racks=[tips])

    # Liquids
    mm_liq = ctx.define_liquid("PCR master mix", "Colony PCR master mix with primers", "#1f77b4")
    water_liq = ctx.define_liquid("Nuclease-free water", "No-template control water", "#aaddff")
    colony_liq = ctx.define_liquid("Colony resuspension", "Candidate colony picked into water", "#2ca02c")
    ev_liq = ctx.define_liquid("Empty-vector colony resuspension", "Empty-vector control colony", "#d62728")

    mm = reagents["A1"]
    water = reagents["B1"]
    mm.load_liquid(liquid=mm_liq, volume=200)
    water.load_liquid(liquid=water_liq, volume=200)

    cand_wells = ["A1", "A2", "A3"]
    for w in cand_wells:
        templates[w].load_liquid(liquid=colony_liq, volume=10)
    templates["A4"].load_liquid(liquid=ev_liq, volume=10)

    # Reaction layout
    rxn_cand = ["A1", "A2", "A3"]   # candidate colonies
    rxn_ev = "A4"                   # empty-vector colony control
    rxn_ntc = "A5"                  # no-template control
    all_rxn = rxn_cand + [rxn_ev, rxn_ntc]

    # Placeholder volumes
    mm_vol = 18
    tmpl_vol = 2

    ctx.comment("[s04] Colony PCR to confirm insert | ROBOT | Reaction assembly only. "
                "Volumes are placeholder-sized (18 uL master mix + 2 uL template per reaction).")

    ctx.pause("HUMAN [s04]: Load master mix (A1) and water (B1) in the tube rack in slot 4, "
              "and colony resuspensions in slot 5 wells A1-A3 (candidates) and A4 (empty-vector colony control). "
              "Resume when ready.")

    # Master mix to all reactions (one tip, source is never dispensed into)
    ctx.comment("[s04] Colony PCR to confirm insert | ROBOT | Dispense master mix to all reaction wells")
    p20.pick_up_tip()
    for w in all_rxn:
        p20.aspirate(mm_vol, mm)
        p20.dispense(mm_vol, rxn_plate[w])
    p20.drop_tip()

    # No-template control: water instead of template
    ctx.comment("[s04] Colony PCR to confirm insert | ROBOT | No-template control: add water in place of template")
    p20.pick_up_tip()
    p20.aspirate(tmpl_vol, water)
    p20.dispense(tmpl_vol, rxn_plate[rxn_ntc])
    p20.drop_tip()

    # Templates, fresh tip each
    ctx.comment("[s04] Colony PCR to confirm insert | ROBOT | Add candidate colony templates")
    for src, dst in zip(cand_wells, rxn_cand):
        p20.pick_up_tip()
        p20.aspirate(tmpl_vol, templates[src])
        p20.dispense(tmpl_vol, rxn_plate[dst])
        p20.drop_tip()

    ctx.comment("[s04] Colony PCR to confirm insert | ROBOT | Add empty-vector colony control template")
    p20.pick_up_tip()
    p20.aspirate(tmpl_vol, templates["A4"])
    p20.dispense(tmpl_vol, rxn_plate[rxn_ev])
    p20.drop_tip()

    ctx.pause("HUMAN [s04]: Seal the reaction plate in slot 2 and move it to the thermocycler. "
              "Resume to finish the protocol.")
