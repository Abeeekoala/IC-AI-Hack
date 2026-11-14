from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    ctx.comment(
        "[s11] Stimulate differentiated THP-1 with purified antigen | ROBOT | "
        "Addition to seeded plate. All volumes are PLACEHOLDER values (50 uL per well) "
        "pending the real S8 volumes."
    )

    # Deck: tips slot 1, reagent source plate slot 2, seeded THP-1 plate slot 5
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", 1)
    source = ctx.load_labware("nest_96_wellplate_200ul_flat", 2)
    cells = ctx.load_labware("corning_96_wellplate_360ul_flat", 5)
    pip = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips])

    # Liquid definitions (placeholder volumes)
    vehicle = ctx.define_liquid(
        name="Vehicle/buffer control",
        description="Vehicle/buffer only, no stimulant",
        display_color="#B0B0B0",
    )
    lps = ctx.define_liquid(
        name="LPS positive control",
        description="LPS positive control",
        display_color="#FF8C00",
    )
    antigen = ctx.define_liquid(
        name="Purified antigen",
        description="Purified antigen for stimulation",
        display_color="#1E90FF",
    )
    pmb_antigen = ctx.define_liquid(
        name="Polymyxin B-treated antigen",
        description="Antigen treated with polymyxin B",
        display_color="#2E8B57",
    )
    mock_eluate = ctx.define_liquid(
        name="Empty-vector mock-purification eluate",
        description="Eluate from empty-vector mock purification",
        display_color="#8B4513",
    )

    # Declare initial contents of source wells (placeholder 150 uL each, under 200 uL well capacity)
    source["A1"].load_liquid(liquid=vehicle, volume=150)
    source["A2"].load_liquid(liquid=lps, volume=150)
    source["A3"].load_liquid(liquid=antigen, volume=150)
    source["A4"].load_liquid(liquid=pmb_antigen, volume=150)
    source["A5"].load_liquid(liquid=mock_eluate, volume=150)

    ctx.pause(
        "HUMAN [s11-setup]: Confirm differentiated THP-1 seeded plate is in slot 5 and "
        "the control/antigen source plate is in slot 2 with the listed liquids (A1 vehicle, "
        "A2 LPS, A3 antigen, A4 PmB-treated antigen, A5 mock eluate). Volumes are placeholders."
    )

    # Each transfer: (source well, target well, label). Fresh tip per transfer,
    # so tips never reuse a source after dispensing and none remain at the end.
    transfers = [
        ("A1", "A1", "Vehicle/buffer control"),
        ("A2", "A2", "LPS positive control"),
        ("A3", "A3", "Purified antigen"),
        ("A4", "A4", "Polymyxin B-treated antigen"),
        ("A5", "A5", "Empty-vector mock eluate"),
    ]

    volume = 50  # placeholder, must not exceed tip capacity or source contents

    for idx, (src_well, dst_well, label) in enumerate(transfers, start=1):
        ctx.comment(
            f"[s11.{idx}] Stimulate with {label} | ROBOT | "
            f"Placeholder {volume} uL from {src_well} to cells {dst_well}"
        )
        pip.pick_up_tip()
        pip.aspirate(volume, source[src_well])
        pip.dispense(volume, cells[dst_well])
        pip.drop_tip()

    ctx.comment(
        "[s11.6] Stimulation addition complete | ROBOT | "
        "All controls and antigen added; no tips remain on pipette."
    )

    ctx.pause(
        "HUMAN [s11-end]: Stimulation addition complete. Remove the THP-1 plate from slot 5 "
        "and continue the S8 incubation per the protocol."
    )
