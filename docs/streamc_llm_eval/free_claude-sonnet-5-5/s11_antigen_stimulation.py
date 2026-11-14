from opentrons import protocol_api

requirements = {"robotType": "OT-2", "apiLevel": "2.19"}


def run(ctx: protocol_api.ProtocolContext):
    # Deck: slot 1 = 20 uL tips, slot 2 = seeded THP-1 plate, slot 4 = reagent tube rack
    tips = ctx.load_labware("opentrons_96_tiprack_20ul", "1")
    plate = ctx.load_labware("corning_96_wellplate_360ul_flat", "2", label="Seeded differentiated THP-1 plate")
    rack = ctx.load_labware("opentrons_24_tube_rack_eppendorf_1.5ml_safelock_snapcap", "4")
    p20 = ctx.load_instrument("p20_single_gen2", "right", tip_racks=[tips])

    # Placeholder-sized volumes (not validated values)
    ADD_VOL = 10.0      # uL added per well (placeholder)
    TUBE_VOL = 200.0    # uL loaded per source tube (placeholder)
    REPLICATES = ["A", "B", "C"]

    conditions = [
        ("Purified antigen", "A1", "1"),
        ("Vehicle/buffer only", "A2", "2"),
        ("LPS positive control", "A3", "3"),
        ("Endotoxin-matched LPS control (measured residual level)", "A4", "4"),
        ("Polymyxin B-treated antigen", "A5", "5"),
        ("Empty-vector mock-purification eluate", "A6", "6"),
    ]

    for name, tube, col in conditions:
        liq = ctx.define_liquid(name=name, description=name, display_color="#" + {
            "A1": "1F77B4", "A2": "AAAAAA", "A3": "D62728",
            "A4": "FF7F0E", "A5": "2CA02C", "A6": "9467BD"}[tube])
        rack[tube].load_liquid(liquid=liq, volume=TUBE_VOL)

    ctx.comment("[s11] Stimulate differentiated THP-1 with purified antigen | ROBOT | "
                "Addition to seeded plate. All volumes are placeholder-sized (10 uL/well, triplicate wells); "
                "replace with validated values before real use.")

    ctx.pause("HUMAN [s11]: Confirm the seeded, differentiated THP-1 plate is in slot 2 (lid removed), "
              "and the tubes are in slot 4 rack: A1 purified antigen, A2 vehicle/buffer, A3 LPS positive control, "
              "A4 endotoxin-matched LPS control, A5 polymyxin B-treated antigen, A6 empty-vector mock-purification eluate. "
              "Then resume.")

    for name, tube, col in conditions:
        ctx.comment("[s11] Stimulate differentiated THP-1 with purified antigen | ROBOT | "
                    "Adding " + name + " to column " + col + " rows A-C")
        p20.pick_up_tip()
        for row in REPLICATES:
            p20.aspirate(ADD_VOL, rack[tube])
            p20.dispense(ADD_VOL, plate[row + col].top(2))
        p20.drop_tip()

    ctx.pause("HUMAN [s11]: Replace the plate lid and move the stimulated plate to the 37 C CO2 incubator.")
