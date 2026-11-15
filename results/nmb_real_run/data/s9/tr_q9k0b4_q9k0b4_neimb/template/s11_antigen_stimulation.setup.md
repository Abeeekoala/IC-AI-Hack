# Antigen / control addition to seeded THP-1 plate

Session `s11_antigen_stimulation` covers S8 step(s): s11

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | Stimulus tubes (human-prepared) |
| 5 | `corning_96_wellplate_360ul_flat` | Seeded, differentiated THP-1 plate |
| 11 | `opentrons_96_tiprack_20ul` | p20 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p20_single_gen2` (left): 24 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 5 A1 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 A2 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 A3 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 B1 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 B2 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 B3 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 C1 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 C2 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 C3 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 D1 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 D2 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 D3 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 E1 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 E2 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 E3 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 F1 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 F2 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 F3 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 G1 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 G2 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 G3 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 H1 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 H2 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 5 H3 | THP-1 cells in medium - seeded and differentiated by the human | 180 | HUMAN (prepared beforehand) |
| slot 2 A1 | vehicle / buffer only - human-prepared stimulus | 90 | robot operator (load) |
| slot 2 A2 | antigen mid dose - human-prepared stimulus | 90 | robot operator (load) |
| slot 2 B1 | LPS positive control - human-prepared stimulus | 90 | robot operator (load) |
| slot 2 B2 | antigen high dose - human-prepared stimulus | 90 | robot operator (load) |
| slot 2 C1 | endotoxin-matched LPS control - prepare at the LPS level measured by LAL for the matching antigen dose | 90 | robot operator (load) |
| slot 2 C2 | antigen mid dose + polymyxin B - human-prepared stimulus | 90 | robot operator (load) |
| slot 2 D1 | antigen low dose - human-prepared stimulus | 90 | robot operator (load) |
| slot 2 D2 | mock-purification eluate - human-prepared stimulus | 90 | robot operator (load) |

## Plate layouts

**Stimulation plate (rows = conditions, columns = replicates)**

- vehicle / buffer only: A1,A2,A3
- LPS positive control: B1,B2,B3
- endotoxin-matched LPS control: C1,C2,C3
- antigen low dose: D1,D2,D3
- antigen mid dose: E1,E2,E3
- antigen high dose: F1,F2,F3
- antigen mid dose + polymyxin B: G1,G2,G3
- mock-purification eluate: H1,H2,H3


## Step ownership

- **s11 Stimulate differentiated THP-1 with purified antigen** - ROBOT: Addition to seeded plate.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s11]: Return the plate to the 37 C / 5% CO2 incubator for the stimulation period, then collect supernatants (ELISA) and lyse cells (RT-qPCR). The incubator is not automated.

## Notes

- Sterility: the OT-2 is not sterile by itself. Run inside a biosafety cabinet / HEPA enclosure with sterile filter tips.
- S8 control list for this step: vehicle/buffer only; LPS positive control; endotoxin-matched LPS control (at the measured residual level); polymyxin B-treated antigen; empty-vector mock-purification eluate
