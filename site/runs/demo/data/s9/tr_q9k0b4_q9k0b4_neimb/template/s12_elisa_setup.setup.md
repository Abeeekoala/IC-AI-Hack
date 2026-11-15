# ELISA (TNF-alpha) standards and sample loading

Session `s12_elisa_setup` covers S8 step(s): s12

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 1 | `nest_1_reservoir_195ml` | Liquid waste |
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | Assay diluent + top standard |
| 3 | `nest_96_wellplate_200ul_flat` | ELISA plate (pre-coated and blocked) |
| 5 | `nest_96_wellplate_200ul_flat` | Collected supernatants (human-prepared) |
| 11 | `opentrons_96_tiprack_300ul` | p300 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p300_single_gen2` (right): 39 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 5 A1 | supernatant: vehicle / buffer only - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 A2 | supernatant: vehicle / buffer only - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 A3 | supernatant: vehicle / buffer only - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 B1 | supernatant: LPS positive control - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 B2 | supernatant: LPS positive control - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 B3 | supernatant: LPS positive control - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 C1 | supernatant: endotoxin-matched LPS control - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 C2 | supernatant: endotoxin-matched LPS control - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 C3 | supernatant: endotoxin-matched LPS control - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 D1 | supernatant: antigen low dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 D2 | supernatant: antigen low dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 D3 | supernatant: antigen low dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 E1 | supernatant: antigen mid dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 E2 | supernatant: antigen mid dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 E3 | supernatant: antigen mid dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 F1 | supernatant: antigen high dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 F2 | supernatant: antigen high dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 F3 | supernatant: antigen high dose - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 G1 | supernatant: antigen mid dose + polymyxin B - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 G2 | supernatant: antigen mid dose + polymyxin B - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 G3 | supernatant: antigen mid dose + polymyxin B - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 H1 | supernatant: mock-purification eluate - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 H2 | supernatant: mock-purification eluate - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 5 H3 | supernatant: mock-purification eluate - collected from the stimulation plate | 100 | HUMAN (prepared beforehand) |
| slot 2 A1 | assay diluent | 380 | robot operator (load) |
| slot 2 A2 | TNF-alpha top standard | 130 | robot operator (load) |

## Plate layouts

**ELISA plate**

- standard column 1 (A=top ... G=lowest, H=blank): A1-H1
- vehicle / buffer only: A2,A3,A4
- LPS positive control: B2,B3,B4
- endotoxin-matched LPS control: C2,C3,C4
- antigen low dose: D2,D3,D4
- antigen mid dose: E2,E3,E4
- antigen high dose: F2,F3,F4
- antigen mid dose + polymyxin B: G2,G3,G4
- mock-purification eluate: H2,H3,H4


## Step ownership

- **s12 ELISA readout** - ROBOT-PARTIAL: Plate setup and washes yes; reading no.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s12]: Seal the plate and incubate 120 min (room temperature, shaking if the kit says so). Then put the plate back on the deck for the develop session. Sealing and incubation are not automated.

## Notes

- One analyte per plate. A single standard column is used by default; set elisa_std_columns=2 for duplicates (costs more tips).
