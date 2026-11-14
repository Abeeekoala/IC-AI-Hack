# FACS staining-cocktail addition

Session `s14_facs_prep` covers S8 step(s): s14

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | Staining cocktail |
| 5 | `nest_96_wellplate_200ul_flat` | Cells to stain (human-transferred) |
| 11 | `opentrons_96_tiprack_300ul` | p300 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p300_single_gen2` (right): 1 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 5 A1 | cells: vehicle / buffer only | 100 | HUMAN (prepared beforehand) |
| slot 5 A2 | cells: vehicle / buffer only | 100 | HUMAN (prepared beforehand) |
| slot 5 A3 | cells: vehicle / buffer only | 100 | HUMAN (prepared beforehand) |
| slot 5 B1 | cells: LPS positive control | 100 | HUMAN (prepared beforehand) |
| slot 5 B2 | cells: LPS positive control | 100 | HUMAN (prepared beforehand) |
| slot 5 B3 | cells: LPS positive control | 100 | HUMAN (prepared beforehand) |
| slot 5 C1 | cells: endotoxin-matched LPS control | 100 | HUMAN (prepared beforehand) |
| slot 5 C2 | cells: endotoxin-matched LPS control | 100 | HUMAN (prepared beforehand) |
| slot 5 C3 | cells: endotoxin-matched LPS control | 100 | HUMAN (prepared beforehand) |
| slot 5 D1 | cells: antigen low dose | 100 | HUMAN (prepared beforehand) |
| slot 5 D2 | cells: antigen low dose | 100 | HUMAN (prepared beforehand) |
| slot 5 D3 | cells: antigen low dose | 100 | HUMAN (prepared beforehand) |
| slot 5 E1 | cells: antigen mid dose | 100 | HUMAN (prepared beforehand) |
| slot 5 E2 | cells: antigen mid dose | 100 | HUMAN (prepared beforehand) |
| slot 5 E3 | cells: antigen mid dose | 100 | HUMAN (prepared beforehand) |
| slot 5 F1 | cells: antigen high dose | 100 | HUMAN (prepared beforehand) |
| slot 5 F2 | cells: antigen high dose | 100 | HUMAN (prepared beforehand) |
| slot 5 F3 | cells: antigen high dose | 100 | HUMAN (prepared beforehand) |
| slot 5 G1 | cells: antigen mid dose + polymyxin B | 100 | HUMAN (prepared beforehand) |
| slot 5 G2 | cells: antigen mid dose + polymyxin B | 100 | HUMAN (prepared beforehand) |
| slot 5 G3 | cells: antigen mid dose + polymyxin B | 100 | HUMAN (prepared beforehand) |
| slot 5 H1 | cells: mock-purification eluate | 100 | HUMAN (prepared beforehand) |
| slot 5 H2 | cells: mock-purification eluate | 100 | HUMAN (prepared beforehand) |
| slot 5 H3 | cells: mock-purification eluate | 100 | HUMAN (prepared beforehand) |
| slot 2 A1 | staining antibody cocktail | 1230 | robot operator (load) |

## Plate layouts

**Staining plate**

- vehicle / buffer only: A1,A2,A3
- LPS positive control: B1,B2,B3
- endotoxin-matched LPS control: C1,C2,C3
- antigen low dose: D1,D2,D3
- antigen mid dose: E1,E2,E3
- antigen high dose: F1,F2,F3
- antigen mid dose + polymyxin B: G1,G2,G3
- mock-purification eluate: H1,H2,H3


## Step ownership

- **s14 FACS readout** - ROBOT-PARTIAL: Staining prep yes; acquisition no.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s14]: Incubate protected from light, wash / centrifuge and acquire on the cytometer. None of this is automated.

## Notes

- A V-bottom plate is usual for cytometry; the S9 catalogue only has flat-bottom plates, so substitute labware before use.
