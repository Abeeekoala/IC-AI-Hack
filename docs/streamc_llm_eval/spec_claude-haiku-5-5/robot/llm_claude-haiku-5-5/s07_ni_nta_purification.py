# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def mix_fresh(ctx, pipette, well, repetitions, volume):
    """Pick up a fresh tip, mix in the given well, then drop the tip."""
    pipette.pick_up_tip()
    pipette.mix(repetitions, volume, well)
    pipette.drop_tip()


def run(ctx: protocol_api.ProtocolContext):
    # ---------------- Deck ----------------
    mag = ctx.load_module("magnetic module gen2", "6")
    waste = ctx.load_labware("nest_1_reservoir_195ml", "1")  # Liquid waste
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")  # Bead slurry
    eluate = ctx.load_labware("nest_96_wellplate_200ul_flat", "3")  # Eluate collection plate
    res = ctx.load_labware("nest_12_reservoir_15ml", "5")  # Wash and elution buffers
    mp = mag.load_labware("nest_96_wellplate_2ml_deep")  # Clarified lysates on magnetic module
    tips_p300_1 = ctx.load_labware("opentrons_96_tiprack_300ul", "11")  # p300 tips
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips_p300_1])

    # ---------------- Initial contents ----------------
    lysate = ctx.define_liquid(
        name="candidate lysate",
        description="Clarified candidate lysate (His-tagged)",
    )
    empty_lysate = ctx.define_liquid(
        name="empty-vector lysate",
        description="Mock purification control lysate",
    )
    wash_buffer = ctx.define_liquid(
        name="wash buffer",
        description="Ni-NTA wash buffer",
    )
    elution_buffer = ctx.define_liquid(
        name="elution buffer",
        description="Ni-NTA elution buffer",
    )
    bead_slurry = ctx.define_liquid(
        name="Ni-NTA magnetic bead slurry",
        description="Ni-NTA magnetic bead slurry",
    )

    mp["A1"].load_liquid(liquid=lysate, volume=500)
    mp["B1"].load_liquid(liquid=empty_lysate, volume=500)
    res["A1"].load_liquid(liquid=wash_buffer, volume=1700)
    res["A2"].load_liquid(liquid=elution_buffer, volume=700)
    tubes["A1"].load_liquid(liquid=bead_slurry, volume=130)

    # ---------------- Steps ----------------
    ctx.comment("[s07] Ni-NTA (His-tag) purification | ROBOT-PARTIAL | Magnetic-bead His-tag format yes; gravity column/FPLC no.")

    # Bind
    ctx.comment("[s07.01] bind: add beads | ROBOT | bind: add beads, mix periodically (robot mixing stands in for a rotator)")
    p300.transfer(
        50,
        tubes["A1"],
        [mp["A1"], mp["B1"]],
        new_tip="always",
        mix_before=(3, 40),
    )
    # Mix after dispense, using a fresh tip (never reuse a tip that dispensed into the well)
    ctx.comment("[s07.02] bead mix after addition | ROBOT | mix 5x 100 uL after bead addition, fresh tip per well")
    mix_fresh(ctx, p300, mp["A1"], 5, 100)
    mix_fresh(ctx, p300, mp["B1"], 5, 100)

    ctx.comment("[s07.03] bead binding | ROBOT | delay 600 s (bead binding)")
    ctx.delay(seconds=600)

    ctx.comment("[s07.04] mix A1 | ROBOT | mix 5x 100 uL at mp:A1, fresh tip")
    mix_fresh(ctx, p300, mp["A1"], 5, 100)
    ctx.comment("[s07.05] mix B1 | ROBOT | mix 5x 100 uL at mp:B1, fresh tip")
    mix_fresh(ctx, p300, mp["B1"], 5, 100)

    ctx.comment("[s07.06] bead binding | ROBOT | delay 600 s (bead binding)")
    ctx.delay(seconds=600)

    ctx.comment("[s07.07] mix A1 | ROBOT | mix 5x 100 uL at mp:A1, fresh tip")
    mix_fresh(ctx, p300, mp["A1"], 5, 100)
    ctx.comment("[s07.08] mix B1 | ROBOT | mix 5x 100 uL at mp:B1, fresh tip")
    mix_fresh(ctx, p300, mp["B1"], 5, 100)

    ctx.comment("[s07.09] bead binding | ROBOT | delay 600 s (bead binding)")
    ctx.delay(seconds=600)

    ctx.comment("[s07.10] mix A1 | ROBOT | mix 5x 100 uL at mp:A1, fresh tip")
    mix_fresh(ctx, p300, mp["A1"], 5, 100)
    ctx.comment("[s07.11] mix B1 | ROBOT | mix 5x 100 uL at mp:B1, fresh tip")
    mix_fresh(ctx, p300, mp["B1"], 5, 100)

    # Capture and remove lysate
    ctx.comment("[s07.12] engage magnet | ROBOT | engage magnetic module mag")
    mag.engage()
    ctx.comment("[s07.13] beads settle | ROBOT | delay 120 s (beads settle)")
    ctx.delay(seconds=120)

    ctx.comment("[s07.14] remove lysate A1 | ROBOT | 540 uL mp:A1 -> waste, aspirate at bottom z=2.0")
    p300.transfer(
        540,
        mp["A1"].bottom(z=2.0),
        waste["A1"],
        new_tip="always",
    )
    ctx.comment("[s07.15] remove lysate B1 | ROBOT | 540 uL mp:B1 -> waste, aspirate at bottom z=2.0")
    p300.transfer(
        540,
        mp["B1"].bottom(z=2.0),
        waste["A1"],
        new_tip="always",
    )

    ctx.comment("[s07.16] disengage magnet | ROBOT | disengage magnetic module mag")
    mag.disengage()

    # Wash 1
    ctx.comment("[s07.17] wash 1 of 2 | ROBOT | wash 1 of 2")
    p300.transfer(
        300,
        res["A1"],
        [mp["A1"], mp["B1"]],
        new_tip="always",
    )
    ctx.comment("[s07.18a] wash 1 mix A1 | ROBOT | mix 5x 150 uL at mp:A1, fresh tip")
    mix_fresh(ctx, p300, mp["A1"], 5, 150)
    ctx.comment("[s07.18b] wash 1 mix B1 | ROBOT | mix 5x 150 uL at mp:B1, fresh tip")
    mix_fresh(ctx, p300, mp["B1"], 5, 150)

    ctx.comment("[s07.19] engage magnet | ROBOT | engage magnetic module mag")
    mag.engage()
    ctx.comment("[s07.20] beads settle | ROBOT | delay 120 s (beads settle)")
    ctx.delay(seconds=120)

    ctx.comment("[s07.21] remove wash 1 A1 | ROBOT | 300 uL mp:A1 -> waste, aspirate at bottom z=2.0")
    p300.transfer(
        300,
        mp["A1"].bottom(z=2.0),
        waste["A1"],
        new_tip="always",
    )
    ctx.comment("[s07.22] remove wash 1 B1 | ROBOT | 300 uL mp:B1 -> waste, aspirate at bottom z=2.0")
    p300.transfer(
        300,
        mp["B1"].bottom(z=2.0),
        waste["A1"],
        new_tip="always",
    )

    ctx.comment("[s07.23] disengage magnet | ROBOT | disengage magnetic module mag")
    mag.disengage()

    # Wash 2
    ctx.comment("[s07.24] wash 2 of 2 | ROBOT | wash 2 of 2")
    p300.transfer(
        300,
        res["A1"],
        [mp["A1"], mp["B1"]],
        new_tip="always",
    )
    ctx.comment("[s07.25a] wash 2 mix A1 | ROBOT | mix 5x 150 uL at mp:A1, fresh tip")
    mix_fresh(ctx, p300, mp["A1"], 5, 150)
    ctx.comment("[s07.25b] wash 2 mix B1 | ROBOT | mix 5x 150 uL at mp:B1, fresh tip")
    mix_fresh(ctx, p300, mp["B1"], 5, 150)

    ctx.comment("[s07.26] engage magnet | ROBOT | engage magnetic module mag")
    mag.engage()
    ctx.comment("[s07.27] beads settle | ROBOT | delay 120 s (beads settle)")
    ctx.delay(seconds=120)

    ctx.comment("[s07.28] remove wash 2 A1 | ROBOT | 300 uL mp:A1 -> waste, aspirate at bottom z=2.0")
    p300.transfer(
        300,
        mp["A1"].bottom(z=2.0),
        waste["A1"],
        new_tip="always",
    )
    ctx.comment("[s07.29] remove wash 2 B1 | ROBOT | 300 uL mp:B1 -> waste, aspirate at bottom z=2.0")
    p300.transfer(
        300,
        mp["B1"].bottom(z=2.0),
        waste["A1"],
        new_tip="always",
    )

    ctx.comment("[s07.30] disengage magnet | ROBOT | disengage magnetic module mag")
    mag.disengage()

    # Elute
    ctx.comment("[s07.31] elute | ROBOT | elute")
    p300.transfer(
        100,
        res["A2"],
        [mp["A1"], mp["B1"]],
        new_tip="always",
    )
    ctx.comment("[s07.32a] elution mix A1 | ROBOT | mix 5x 60 uL at mp:A1, fresh tip")
    mix_fresh(ctx, p300, mp["A1"], 5, 60)
    ctx.comment("[s07.32b] elution mix B1 | ROBOT | mix 5x 60 uL at mp:B1, fresh tip")
    mix_fresh(ctx, p300, mp["B1"], 5, 60)

    ctx.comment("[s07.33] elution incubation | ROBOT | delay 600 s (elution)")
    ctx.delay(seconds=600)

    ctx.comment("[s07.34] engage magnet | ROBOT | engage magnetic module mag")
    mag.engage()
    ctx.comment("[s07.35] beads settle | ROBOT | delay 120 s (beads settle)")
    ctx.delay(seconds=120)

    ctx.comment("[s07.36] collect eluate A1 | ROBOT | 100 uL mp:A1 -> eluate:A1, aspirate at bottom z=2.0")
    p300.transfer(
        100,
        mp["A1"].bottom(z=2.0),
        eluate["A1"],
        new_tip="always",
    )
    ctx.comment("[s07.37] collect eluate B1 | ROBOT | 100 uL mp:B1 -> eluate:B1, aspirate at bottom z=2.0")
    p300.transfer(
        100,
        mp["B1"].bottom(z=2.0),
        eluate["B1"],
        new_tip="always",
    )

    ctx.comment("[s07.38] disengage magnet | ROBOT | disengage magnetic module mag")
    mag.disengage()

    ctx.comment("[s07.39] human hand-off | HUMAN | pause for seal, storage and sampling by hand")
    ctx.pause("HUMAN [s07]: Seal and store the eluates; take any gel / protein-assay aliquots by hand (not automated). Continue to endotoxin removal.")
