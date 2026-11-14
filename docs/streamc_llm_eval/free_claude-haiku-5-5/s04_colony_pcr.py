from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# Placeholder-sized volumes for s04 colony PCR reaction assembly (not validated values).
# Per reaction: 18 uL master mix + 2 uL water (no-template control) or colony (empty-vector control).
MM_VOL_UL = 18
WATER_VOL_UL = 2
MM_RESERVOIR_START_UL = 200   # placeholder stock volume in reservoir A1
WATER_RESERVOIR_START_UL = 100  # placeholder stock volume in reservoir A2


def run(ctx: protocol_api.ProtocolContext):
    # Deck layout (robot-owned design):
    #   Slot 1: 20 uL tip rack
    #   Slot 2: 12-well reservoir (A1 = master mix, A2 = water)
    #   Slot 3: 96-well PCR plate (A1 = empty-vector colony control, B1 = no-template control)
    tips = ctx.load_labware("opentrons_96_tiprack_20ul", 1)
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", 2)
    plate = ctx.load_labware("nest_96_wellplate_100ul_pcr_full_skirt", 3)
    pipette = ctx.load_instrument("p20_single_gen2", "right", tip_racks=[tips])

    master_mix = ctx.define_liquid(
        name="Master mix",
        description="PCR master mix (placeholder)",
        display_color="#00AA00",
    )
    water = ctx.define_liquid(
        name="Nuclease-free water",
        description="No-template control diluent (placeholder)",
        display_color="#0000FF",
    )

    reservoir["A1"].load_liquid(liquid=master_mix, volume=MM_RESERVOIR_START_UL)
    reservoir["A2"].load_liquid(liquid=water, volume=WATER_RESERVOIR_START_UL)

    ctx.comment(
        "[s04-00] Deck setup and placeholder volumes | ROBOT | "
        "Placeholder-sized volumes: 18 uL master mix and 2 uL water per reaction; "
        "replace with validated values before use."
    )

    ctx.pause(
        "HUMAN [s04-01]: Load the 96-well PCR plate in slot 3, confirm the reservoir in slot 2 "
        "has master mix in A1 and water in A2, then resume."
    )

    # Master mix to empty-vector colony control well A1 (fresh tip, master mix source).
    ctx.comment(
        "[s04-02] Dispense master mix to empty-vector control | ROBOT | "
        "18 uL master mix into PCR well A1 with a fresh tip."
    )
    pipette.pick_up_tip()
    pipette.aspirate(MM_VOL_UL, reservoir["A1"])
    pipette.dispense(MM_VOL_UL, plate["A1"])
    pipette.drop_tip()

    # Master mix to no-template control well B1 (fresh tip, same liquid, never touched plate wells).
    ctx.comment(
        "[s04-03] Dispense master mix to no-template control | ROBOT | "
        "18 uL master mix into PCR well B1 with a fresh tip."
    )
    pipette.pick_up_tip()
    pipette.aspirate(MM_VOL_UL, reservoir["A1"])
    pipette.dispense(MM_VOL_UL, plate["B1"])
    pipette.drop_tip()

    ctx.pause(
        "HUMAN [s04-04]: Add the empty-vector colony to PCR well A1 (colony pick is manual), "
        "then resume so the robot can add the no-template water to B1."
    )

    # Water to no-template control well B1 (fresh tip, water source differs from master mix).
    ctx.comment(
        "[s04-05] Dispense no-template water | ROBOT | "
        "2 uL nuclease-free water into PCR well B1 in place of template, fresh tip."
    )
    pipette.pick_up_tip()
    pipette.aspirate(WATER_VOL_UL, reservoir["A2"])
    pipette.dispense(WATER_VOL_UL, plate["B1"])
    pipette.drop_tip()

    ctx.comment(
        "[s04-06] Reaction assembly complete | ROBOT | "
        "Controls assembled in A1 (empty-vector colony) and B1 (no-template). No tips left on pipette."
    )
