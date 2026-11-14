from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}

# All volumes are PLACEHOLDER values for the magnetic-bead Ni-NTA (His-tag) format.
# Replace with validated volumes before any real run.
BEAD_VOL = 100     # uL Ni-NTA magnetic bead slurry (placeholder)
BIND_VOL = 200     # uL binding buffer (placeholder)
WASH_VOL = 200     # uL wash buffer per wash, two washes (placeholder)
ELUTE_VOL = 100    # uL elution buffer (placeholder)
MAX_ASP = 200      # uL per aspirate chunk, keeps every draw within a 300 uL tip
LYSATE_START = 800 # uL empty-vector mock lysate in sample well (placeholder)


def remove_supernatant(pip, src, waste, amount):
    # One tip for the whole removal series. The tip only draws from src and
    # dispenses to waste, so it never returns to src.
    pip.pick_up_tip()
    left = amount
    while left > 0:
        chunk = min(MAX_ASP, left)
        pip.aspirate(chunk, src.bottom(z=1))
        pip.dispense(chunk, waste.top())
        left -= chunk
    pip.drop_tip()


def run(ctx: protocol_api.ProtocolContext):
    # ---- Labware and modules -------------------------------------------
    mag_mod = ctx.load_module("magnetic module gen2", 1)
    sample_plate = mag_mod.load_labware("nest_96_wellplate_2ml_deep")
    tips1 = ctx.load_labware("opentrons_96_tiprack_300ul", 2)
    tips2 = ctx.load_labware("opentrons_96_tiprack_300ul", 3)
    reservoir = ctx.load_labware("nest_12_reservoir_15ml", 5)
    elution_plate = ctx.load_labware("nest_96_wellplate_100ul_pcr_full_skirt", 4)
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips1, tips2])

    # ---- Liquids -------------------------------------------------------
    lysate = ctx.define_liquid(
        name="Clarified empty-vector mock lysate",
        description="Control lysate (empty vector), mock purification",
        display_color="#A0C4FF",
    )
    beads = ctx.define_liquid(
        name="Ni-NTA magnetic beads",
        description="Ni-NTA magnetic bead slurry",
        display_color="#B5E48C",
    )
    binding = ctx.define_liquid(
        name="Binding buffer",
        description="Ni-NTA binding buffer (placeholder composition)",
        display_color="#FFD6A5",
    )
    wash = ctx.define_liquid(
        name="Wash buffer",
        description="Ni-NTA wash buffer (placeholder composition)",
        display_color="#FDFFB6",
    )
    elution = ctx.define_liquid(
        name="Elution buffer",
        description="Ni-NTA elution buffer, imidazole (placeholder)",
        display_color="#CAFFBF",
    )

    sample_plate["A1"].load_liquid(liquid=lysate, volume=LYSATE_START)
    reservoir["A1"].load_liquid(liquid=beads, volume=150)
    reservoir["A2"].load_liquid(liquid=binding, volume=300)
    reservoir["A3"].load_liquid(liquid=wash, volume=500)
    reservoir["A4"].load_liquid(liquid=elution, volume=200)
    # reservoir A12 is an empty waste well and is not loaded.

    sample = sample_plate["A1"]
    waste = reservoir["A12"]
    vol = LYSATE_START  # tracked working volume in sample well (uL)

    # ---- Steps ---------------------------------------------------------
    ctx.comment(
        "[s07.0] Deck setup and reagent check | HUMAN | "
        "All volumes are placeholders for the mock empty-vector control; verify before run"
    )
    ctx.pause(
        "HUMAN [s07.0]: Confirm deck. Magnetic module slot 1 with 2 mL deep-well plate; "
        "A1 holds clarified empty-vector mock lysate. Slot 5 reservoir: A1 Ni-NTA beads, "
        "A2 binding buffer, A3 wash buffer, A4 elution buffer, A12 empty waste. "
        "Elution plate slot 4 empty. Tipracks slots 2 and 3. Volumes are placeholders."
    )

    ctx.comment(
        "[s07.1] Bead addition to lysate | ROBOT | "
        "Placeholder bead volume; fresh tip from bead source"
    )
    p300.pick_up_tip()
    p300.aspirate(BEAD_VOL, reservoir["A1"].bottom(z=1))
    p300.dispense(BEAD_VOL, sample.top())
    p300.drop_tip()
    vol += BEAD_VOL

    ctx.comment(
        "[s07.2] Binding buffer addition and bead capture | ROBOT | "
        "Placeholder volume; fresh tip from binding buffer source"
    )
    p300.pick_up_tip()
    p300.aspirate(BIND_VOL, reservoir["A2"].bottom(z=1))
    p300.dispense(BIND_VOL, sample.top())
    p300.drop_tip()
    vol += BIND_VOL
    ctx.delay(minutes=5)  # placeholder binding incubation

    mag_mod.engage(height_from_base=6)
    ctx.delay(minutes=2)  # placeholder magnetic capture time

    ctx.comment(
        "[s07.3] Remove unbound lysate (flow-through) | ROBOT | "
        "Removes 1000 uL of 1100 uL placeholder volume; beads retained on magnet"
    )
    remove_supernatant(p300, sample, waste, 1000)
    vol -= 1000

    ctx.comment(
        "[s07.4] Wash 1 | ROBOT | "
        "Placeholder wash volume; magnet disengaged for resuspension, then re-captured"
    )
    mag_mod.disengage()
    p300.pick_up_tip()
    p300.aspirate(WASH_VOL, reservoir["A3"].bottom(z=1))
    p300.dispense(WASH_VOL, sample.top())
    p300.drop_tip()
    vol += WASH_VOL
    mag_mod.engage(height_from_base=6)
    ctx.delay(minutes=2)
    remove_supernatant(p300, sample, waste, WASH_VOL)
    vol -= WASH_VOL

    ctx.comment(
        "[s07.5] Wash 2 | ROBOT | "
        "Placeholder wash volume; same routine as wash 1"
    )
    mag_mod.disengage()
    p300.pick_up_tip()
    p300.aspirate(WASH_VOL, reservoir["A3"].bottom(z=1))
    p300.dispense(WASH_VOL, sample.top())
    p300.drop_tip()
    vol += WASH_VOL
    mag_mod.engage(height_from_base=6)
    ctx.delay(minutes=2)
    remove_supernatant(p300, sample, waste, WASH_VOL)
    vol -= WASH_VOL

    ctx.comment(
        "[s07.6] Elution of His-tagged protein | ROBOT | "
        "Placeholder elution volume; magnet disengaged for elution"
    )
    mag_mod.disengage()
    p300.pick_up_tip()
    p300.aspirate(ELUTE_VOL, reservoir["A4"].bottom(z=1))
    p300.dispense(ELUTE_VOL, sample.top())
    p300.drop_tip()
    vol += ELUTE_VOL
    ctx.delay(minutes=5)  # placeholder elution incubation
    mag_mod.engage(height_from_base=6)
    ctx.delay(minutes=2)

    ctx.comment(
        "[s07.7] Transfer eluate to collection plate | ROBOT | "
        "Placeholder eluate volume drawn from magnet-captured beads; fresh tip"
    )
    p300.pick_up_tip()
    p300.aspirate(ELUTE_VOL, sample.bottom(z=1))
    p300.dispense(ELUTE_VOL, elution_plate["A1"].bottom(z=2))
    p300.drop_tip()
    vol -= ELUTE_VOL

    mag_mod.disengage()

    ctx.comment(
        "[s07.8] Gate check: soluble protein recovered? | HUMAN | "
        "Gate: soluble protein recovered from empty-vector control. "
        "Falsifier: if not recovered, candidate is falsified at expression."
    )
    ctx.pause(
        "HUMAN [s07.8]: Remove elution plate from slot 4. Analyze eluate by SDS-PAGE. "
        "Gate: soluble protein recovered from the empty-vector mock purification. "
        "If not recovered, falsify the candidate at expression (see falsifier) and stop."
    )
