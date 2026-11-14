from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck: tips 1, reagent reservoir 2, template source plate 4, reaction plate 5.
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", 1)
    reagent = ctx.load_labware("nest_12_reservoir_15ml", 2)
    src = ctx.load_labware("nest_96_wellplate_200ul_flat", 4)
    dest = ctx.load_labware("nest_96_wellplate_100ul_pcr_full_skirt", 5)
    pip = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # Liquids. All volumes are PLACEHOLDER values, not validated against the real protocol.
    mm = ctx.define_liquid(
        name="RT-qPCR master mix",
        description="Placeholder master mix",
        display_color="#00AAFF",
    )
    tpl = ctx.define_liquid(
        name="Template RNA",
        description="Placeholder template",
        display_color="#FF8800",
    )
    n_samples = 8
    mm_per_rxn = 15   # placeholder uL per reaction
    tpl_per_rxn = 5   # placeholder uL per reaction

    reagent["A1"].load_liquid(liquid=mm, volume=150)  # placeholder reservoir load
    sources = src.wells()[:n_samples]
    targets = dest.wells()[:n_samples]
    for w in sources:
        w.load_liquid(liquid=tpl, volume=20)  # placeholder template load

    ctx.comment(
        "[s13] RT QPCR readout | ROBOT | Reaction setup only. "
        "Placeholder volumes: 15 uL master mix + 5 uL template per well, 8 samples."
    )

    # Master mix distribution: one tip, touches only the reservoir, never the reaction wells.
    ctx.comment(
        "[s13] RT QPCR readout | ROBOT | Dispense master mix into reaction wells "
        "(single tip, reservoir source only) | placeholder 15 uL per well"
    )
    pip.pick_up_tip()
    pip.aspirate(mm_per_rxn * n_samples, reagent["A1"])
    for w in targets:
        pip.dispense(mm_per_rxn, w)
    pip.drop_tip()

    # Template addition: fresh tip per sample so no tip carries template into another source
    # or returns to a well it has drawn from.
    for i in range(n_samples):
        ctx.comment(
            f"[s13] RT QPCR readout | ROBOT | Add template to reaction well {i + 1} "
            f"(fresh tip) | placeholder 5 uL"
        )
        pip.pick_up_tip()
        pip.aspirate(tpl_per_rxn, sources[i])
        pip.dispense(tpl_per_rxn, targets[i])
        pip.drop_tip()

    ctx.comment(
        "[s13] RT QPCR readout | ROBOT | Reaction setup complete; no tip left on pipette | "
        "Thermocycling and reading are manual"
    )

    ctx.pause(
        "HUMAN [s13]: Seal the reaction plate in slot 5, move it to the thermocycler, "
        "run the RT-qPCR program, then read the plate. Thermocycling and reading are manual."
    )
