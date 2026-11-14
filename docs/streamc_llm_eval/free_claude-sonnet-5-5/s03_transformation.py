from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck layout
    tips = ctx.load_labware("opentrons_96_tiprack_20ul", 1)
    reagents = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", 2)
    temp = ctx.load_module("temperature module gen2", 4)
    block = temp.load_labware("opentrons_96_aluminumblock_nest_wellplate_100ul")
    p20 = ctx.load_instrument("p20_single_gen2", "left", tip_racks=[tips])

    # Liquids
    plasmid_liq = ctx.define_liquid(name="Plasmid DNA", description="Expression plasmid", display_color="#1f77b4")
    soc_liq = ctx.define_liquid(name="SOC recovery medium", description="Recovery medium", display_color="#ff7f0e")
    cells_liq = ctx.define_liquid(name="Competent expression host", description="Chemically competent cells", display_color="#2ca02c")

    plasmid = reagents["A1"]
    soc = reagents["B1"]
    plasmid.load_liquid(liquid=plasmid_liq, volume=50)
    soc.load_liquid(liquid=soc_liq, volume=1000)

    transform_well = block["A1"]
    control_well = block["A2"]
    transform_well.load_liquid(liquid=cells_liq, volume=20)
    control_well.load_liquid(liquid=cells_liq, volume=20)

    # Placeholder volumes and times
    dna_vol = 2      # uL placeholder
    soc_vol = 20     # uL placeholder
    heat_shock_s = 30   # placeholder
    ice_min = 2         # placeholder

    ctx.comment("[s03] Transform expression host | ROBOT | Volumes and times in this protocol are placeholder-sized "
                "(DNA 2 uL, SOC 20 uL, heat shock 30 s, ice 2 min) and must be set to real values before use. "
                "Well A1 = transformation, well A2 = untransformed host control.")

    ctx.comment("[s03] Transform expression host | ROBOT | Chill Temperature module to 4 C")
    temp.set_temperature(4)

    ctx.pause("HUMAN [s03]: Confirm competent cells are in wells A1 and A2 of the aluminum block on the Temperature module "
              "(slot 4), plasmid DNA is in tube A1 and SOC in tube B1 of the tube rack (slot 2), then resume.")

    ctx.comment("[s03] Transform expression host | ROBOT | Add plasmid DNA to cells in A1 only; A2 receives no DNA (control)")
    p20.pick_up_tip()
    p20.aspirate(dna_vol, plasmid)
    p20.dispense(dna_vol, transform_well)
    p20.blow_out()
    p20.drop_tip()

    ctx.comment("[s03] Transform expression host | ROBOT | Ice incubation at 4 C (placeholder time)")
    ctx.delay(minutes=ice_min)

    ctx.comment("[s03] Transform expression host | ROBOT | Heat shock at 42 C (placeholder time)")
    temp.set_temperature(42)
    ctx.delay(seconds=heat_shock_s)

    ctx.comment("[s03] Transform expression host | ROBOT | Return to 4 C")
    temp.set_temperature(4)

    ctx.comment("[s03] Transform expression host | ROBOT | Add SOC recovery medium to A1 and A2 with a fresh tip for each well")
    for well in (transform_well, control_well):
        p20.pick_up_tip()
        p20.aspirate(soc_vol, soc)
        p20.dispense(soc_vol, well)
        p20.blow_out()
        p20.drop_tip()

    ctx.pause("HUMAN [s03]: Remove the plate from the Temperature module and plate the A1 transformation onto the selection plate. "
              "Also plate the untransformed host control (A2) on a selection plate. Incubate plates as required, then resume.")

    temp.deactivate()
    ctx.comment("[s03] Transform expression host | ROBOT | Temperature module deactivated; robot steps complete")
