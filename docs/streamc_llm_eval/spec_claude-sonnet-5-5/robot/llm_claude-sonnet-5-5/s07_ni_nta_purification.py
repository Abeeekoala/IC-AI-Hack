from opentrons import protocol_api

# Candidate: c1 (fHbp)
# Parameters: PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.
# Biosecurity: S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    mag = ctx.load_module("magnetic module gen2", "6")
    waste = ctx.load_labware("nest_1_reservoir_195ml", "1")
    tubes = ctx.load_labware("opentrons_24_tuberack_nest_1.5ml_snapcap", "2")
    eluate = ctx.load_labware("nest_96_wellplate_200ul_flat", "3")
    res = ctx.load_labware("nest_12_reservoir_15ml", "5")
    mp = mag.load_labware("nest_96_wellplate_2ml_deep")
    tips_p300_1 = ctx.load_labware("opentrons_96_tiprack_300ul", "11")
    p300 = ctx.load_instrument("p300_single_gen2", "right", tip_racks=[tips_p300_1])

    # Liquids
    lysate = ctx.define_liquid(name="candidate lysate", description="Clarified candidate lysate", display_color="#8E44AD")
    ev_lysate = ctx.define_liquid(name="empty-vector lysate", description="Mock purification control lysate", display_color="#2980B9")
    wash = ctx.define_liquid(name="wash buffer", description="Wash buffer", display_color="#27AE60")
    elution = ctx.define_liquid(name="elution buffer", description="Elution buffer", display_color="#F39C12")
    beads = ctx.define_liquid(name="Ni-NTA magnetic bead slurry", description="Ni-NTA magnetic beads", display_color="#7F8C8D")

    mp["A1"].load_liquid(liquid=lysate, volume=500)
    mp["B1"].load_liquid(liquid=ev_lysate, volume=500)
    res["A1"].load_liquid(liquid=wash, volume=1700)
    res["A2"].load_liquid(liquid=elution, volume=700)
    tubes["A1"].load_liquid(liquid=beads, volume=130)

    ctx.comment("[s07] Ni-NTA (His-tag) purification | ROBOT-PARTIAL | Magnetic-bead His-tag format yes; gravity column/FPLC no.")

    def fresh_mix(well):
        p300.pick_up_tip()
        p300.mix(5, 100, well)
        p300.drop_tip()

    def settle_and_remove(volume):
        mag.engage()
        ctx.delay(seconds=120)
        for w in ("A1", "B1"):
            p300.transfer(volume, mp[w].bottom(z=2.0), waste["A1"], new_tip="always")
        mag.disengage()

    # Bind
    ctx.comment("bind: add beads, mix periodically (robot mixing stands in for a rotator)")
    p300.transfer(50, tubes["A1"], [mp["A1"], mp["B1"]], new_tip="always",
                  mix_before=(3, 40), mix_after=(5, 100))
    for i in range(3):
        ctx.delay(seconds=600)
        fresh_mix(mp["A1"])
        fresh_mix(mp["B1"])

    settle_and_remove(540)

    # Wash 1
    ctx.comment("wash 1 of 2")
    p300.transfer(300, res["A1"], [mp["A1"], mp["B1"]], new_tip="always", mix_after=(5, 150))
    settle_and_remove(300)

    # Wash 2
    ctx.comment("wash 2 of 2")
    p300.transfer(300, res["A1"], [mp["A1"], mp["B1"]], new_tip="always", mix_after=(5, 150))
    settle_and_remove(300)

    # Elute
    ctx.comment("elute")
    p300.transfer(100, res["A2"], [mp["A1"], mp["B1"]], new_tip="always", mix_after=(5, 60))
    ctx.delay(seconds=600)
    mag.engage()
    ctx.delay(seconds=120)
    p300.transfer(100, mp["A1"].bottom(z=2.0), eluate["A1"], new_tip="always")
    p300.transfer(100, mp["B1"].bottom(z=2.0), eluate["B1"], new_tip="always")
    mag.disengage()

    ctx.pause("HUMAN [s07]: Seal and store the eluates; take any gel / protein-assay aliquots by hand (not automated). Continue to endotoxin removal.")
