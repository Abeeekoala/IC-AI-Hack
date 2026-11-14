from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # ---------------- Deck ----------------
    mag = ctx.load_module("magnetic module gen2", 1)
    bead_plate = mag.load_labware("nest_96_wellplate_2ml_deep")
    lysate_rack = ctx.load_labware("opentrons_24_tuberack_eppendorf_2ml_safelock_snapcap", 2)
    collect_plate = ctx.load_labware("nest_96_wellplate_2ml_deep", 3)
    tips = ctx.load_labware("opentrons_96_tiprack_300ul", 4)
    reagents = ctx.load_labware("nest_12_reservoir_15ml", 5)
    waste = ctx.load_labware("nest_1_reservoir_195ml", 6)
    p300 = ctx.load_instrument("p300_single_gen2", "left", tip_racks=[tips])

    # ---------------- Liquids (placeholder volumes) ----------------
    lys_cand = ctx.define_liquid("Candidate lysate (clarified)", "Soluble lysate with His-tagged candidate", "#C98A2B")
    lys_mock = ctx.define_liquid("Empty-vector lysate", "Mock purification control lysate", "#8E8E8E")
    beads = ctx.define_liquid("Ni-NTA magnetic beads", "Bead slurry", "#3B6FB6")
    buf = ctx.define_liquid("Equilibration/Wash buffer", "Placeholder buffer", "#9AD0EC")
    elu = ctx.define_liquid("Elution buffer", "Placeholder imidazole elution buffer", "#7FBF7F")

    lysate_rack["A1"].load_liquid(liquid=lys_cand, volume=1000)
    lysate_rack["B1"].load_liquid(liquid=lys_mock, volume=1000)
    reagents["A1"].load_liquid(liquid=beads, volume=1000)
    reagents["A2"].load_liquid(liquid=buf, volume=10000)
    reagents["A3"].load_liquid(liquid=elu, volume=5000)

    # Placeholder-sized volumes (uL)
    V_BEAD = 50
    V_BEAD_REMOVE = 40
    V_EQ = 150
    V_LYSATE = 200
    V_WASH = 200
    V_ELUTE = 100
    V_ELUTE_COLLECT = 90
    BIND_MIN = 1  # placeholder incubation, minutes
    MAG_WAIT = 60  # seconds

    samples = [
        ("candidate", lysate_rack["A1"], bead_plate["A1"], collect_plate["A1"], collect_plate["A2"], collect_plate["A3"]),
        ("empty-vector mock", lysate_rack["B1"], bead_plate["B1"], collect_plate["B1"], collect_plate["B2"], collect_plate["B3"]),
    ]

    def add(src, dest, vol, mix_src=False, mix_dest=False):
        p300.pick_up_tip()
        if mix_src:
            p300.mix(3, 100, src)
        p300.aspirate(vol, src)
        p300.dispense(vol, dest)
        if mix_dest:
            p300.mix(5, 100, dest.bottom(2))
        p300.drop_tip()

    def remove(well, vol, dest):
        p300.pick_up_tip()
        p300.aspirate(vol, well.bottom(1), rate=0.5)
        p300.dispense(vol, dest)
        p300.drop_tip()

    def magnet_on():
        mag.engage(height_from_base=5)
        ctx.delay(seconds=MAG_WAIT)

    # ---------------- Start ----------------
    ctx.comment("[s07] Ni-NTA (His-tag) purification | ROBOT | Volumes are placeholder-sized, to be replaced with validated values")
    ctx.pause("HUMAN [s07]: Load clarified candidate lysate (tube A1) and empty-vector lysate (tube B1) in the slot 2 rack; load bead slurry (A1), buffer (A2), elution buffer (A3) in the slot 5 reservoir; place empty deep-well plates on the magnet (slot 1) and in slot 3.")

    # Bead dispensing
    ctx.comment("[s07] Dispense Ni-NTA magnetic beads | ROBOT | One fresh tip per sample")
    mag.disengage()
    for name, lys, bw, ft, wsh, el in samples:
        add(reagents["A1"], bw, V_BEAD, mix_src=True)

    # Remove storage liquid
    ctx.comment("[s07] Remove bead storage buffer | ROBOT | Magnet engaged")
    magnet_on()
    for name, lys, bw, ft, wsh, el in samples:
        remove(bw, V_BEAD_REMOVE, waste["A1"])
    mag.disengage()

    # Equilibrate
    ctx.comment("[s07] Equilibrate beads | ROBOT | Buffer added, mixed, then removed")
    for name, lys, bw, ft, wsh, el in samples:
        add(reagents["A2"], bw, V_EQ, mix_dest=True)
    magnet_on()
    for name, lys, bw, ft, wsh, el in samples:
        remove(bw, V_EQ, waste["A1"])
    mag.disengage()

    # Bind
    ctx.comment("[s07] Bind His-tagged protein to beads | ROBOT | Candidate and empty-vector mock control processed in parallel")
    for name, lys, bw, ft, wsh, el in samples:
        add(lys, bw, V_LYSATE, mix_dest=True)
    ctx.delay(minutes=BIND_MIN, msg="Placeholder binding incubation")

    # Flow-through
    ctx.comment("[s07] Collect flow-through | ROBOT | Flow-through retained for the soluble-recovery gate")
    magnet_on()
    for name, lys, bw, ft, wsh, el in samples:
        remove(bw, V_LYSATE, ft)
    mag.disengage()

    # Wash
    ctx.comment("[s07] Wash beads | ROBOT | One placeholder wash; wash retained for analysis")
    for name, lys, bw, ft, wsh, el in samples:
        add(reagents["A2"], bw, V_WASH, mix_dest=True)
    magnet_on()
    for name, lys, bw, ft, wsh, el in samples:
        remove(bw, V_WASH, wsh)
    mag.disengage()

    # Elute
    ctx.comment("[s07] Elute His-tagged protein | ROBOT | Eluate collected in slot 3 plate")
    for name, lys, bw, ft, wsh, el in samples:
        add(reagents["A3"], bw, V_ELUTE, mix_dest=True)
    magnet_on()
    for name, lys, bw, ft, wsh, el in samples:
        remove(bw, V_ELUTE_COLLECT, el)
    mag.disengage()

    ctx.comment("[s07] Gate: soluble protein recovered | HUMAN | Assess candidate versus empty-vector mock eluate")
    ctx.pause("HUMAN [s07]: Remove the slot 3 plate (flow-through, wash, eluate) and assess soluble protein recovery against the empty-vector mock. If no soluble protein is recovered, the candidate is falsified at expression.")
