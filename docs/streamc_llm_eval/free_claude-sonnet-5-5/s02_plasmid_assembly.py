from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck: tips slot 1, reagent tube rack slot 2, temperature module slot 3 (reaction strip block)
    tips = ctx.load_labware("opentrons_96_tiprack_20ul", 1)
    rack = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", 2)
    temp = ctx.load_module("temperature module gen2", 3)
    rxn = temp.load_labware("opentrons_96_aluminumblock_generic_pcr_strip_200ul")
    p20 = ctx.load_instrument("p20_single_gen2", "left", tip_racks=[tips])

    vec_liq = ctx.define_liquid(name="Expression vector", description="His-tag expression vector", display_color="#1E90FF")
    mix_liq = ctx.define_liquid(name="Assembly mix", description="Assembly master mix", display_color="#32CD32")
    rack["A1"].load_liquid(liquid=vec_liq, volume=50)
    rack["A2"].load_liquid(liquid=mix_liq, volume=100)

    vec_vol = 2    # placeholder
    mix_vol = 5    # placeholder
    mix_cycles = 3
    mix_rep_vol = 5
    incubation_c = 50       # placeholder
    incubation_min = 15     # placeholder

    sample = rxn["A1"]
    control = rxn["B1"]

    ctx.comment("[s02] Clone into His-tagged expression vector | ROBOT | Liquid handling + temperature module. "
                "NOTE: all volumes, temperatures and times are placeholder-sized.")

    temp.set_temperature(4)

    ctx.comment("[s02] Clone into His-tagged expression vector | ROBOT | Dispense expression vector into sample (A1) and no-insert control (B1)")
    p20.pick_up_tip()
    for dest in (sample, control):
        p20.aspirate(vec_vol, rack["A1"])
        p20.dispense(vec_vol, dest)
    p20.drop_tip()

    ctx.comment("[s02] Clone into His-tagged expression vector | ROBOT | Dispense assembly mix into sample (A1) and no-insert control (B1)")
    p20.pick_up_tip()
    for dest in (sample, control):
        p20.aspirate(mix_vol, rack["A2"])
        p20.dispense(mix_vol, dest)
    p20.drop_tip()

    ctx.pause("HUMAN [s02]: Add the insert to the sample well A1 of the reaction block on the temperature module. "
              "Leave the no-insert control well B1 without insert. Then resume.")

    ctx.comment("[s02] Clone into His-tagged expression vector | ROBOT | Mix sample and no-insert control with fresh tips")
    for well in (sample, control):
        p20.pick_up_tip()
        p20.mix(mix_cycles, mix_rep_vol, well)
        p20.drop_tip()

    ctx.comment("[s02] Clone into His-tagged expression vector | ROBOT | Incubate assembly on temperature module (placeholder conditions)")
    temp.set_temperature(incubation_c)
    ctx.delay(minutes=incubation_min)
    temp.set_temperature(4)
