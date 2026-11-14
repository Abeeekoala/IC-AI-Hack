from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", "3")
    src = ctx.load_labware("nest_96_wellplate_200ul_flat", "1", label="Sample source plate")
    dst = ctx.load_labware("nest_96_wellplate_200ul_flat", "2", label="Endotoxin removal plate")
    pip = ctx.load_instrument("p300_single_gen2", "left", tip_racks=[tips])

    sample = ctx.define_liquid(
        name="Sample",
        description="Sample to be depyrogenated (placeholder)",
        display_color="#33AA55",
    )
    sample_wells = ["A1", "B1", "C1"]
    for w in sample_wells:
        src[w].load_liquid(liquid=sample, volume=200)

    ctx.comment("[s08] Endotoxin removal | ROBOT | Plate setup. Volumes are placeholder-sized (50 uL per well), not validated values.")

    transfer_vol = 50  # placeholder volume in uL
    for w in sample_wells:
        pip.pick_up_tip()
        pip.aspirate(transfer_vol, src[w])
        pip.dispense(transfer_vol, dst[w])
        pip.blow_out()
        pip.drop_tip()

    ctx.comment("[s08] Endotoxin removal | ROBOT | Plate setup complete; handing over to operator")
    ctx.pause("HUMAN [s08]: Seal the plate, perform the endotoxin removal step (incubation/separation) off-deck, then resume.")
