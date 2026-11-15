# RT-qPCR reaction set-up

Session `s13_rt_qpcr` covers S8 step(s): s13

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 3 | temperature module gen2 | module |
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | qPCR reagents + master-mix tubes |
| 3 | `opentrons_96_aluminumblock_nest_wellplate_100ul` | qPCR plate on the 4 C block on temp |
| 5 | `biorad_96_wellplate_200ul_pcr` | cDNA (human-prepared; same layout as the stimulation plate) |
| 10 | `opentrons_96_tiprack_300ul` | p300 tips |
| 11 | `opentrons_96_tiprack_20ul` | p20 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p20_single_gen2` (left): 81 tip pick-up(s); 1 rack(s) loaded
- `p300_single_gen2` (right): 12 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 5 A1 | cDNA: vehicle / buffer only | 20 | HUMAN (prepared beforehand) |
| slot 5 A2 | cDNA: vehicle / buffer only | 20 | HUMAN (prepared beforehand) |
| slot 5 A3 | cDNA: vehicle / buffer only | 20 | HUMAN (prepared beforehand) |
| slot 5 A4 | no-RT control (vehicle RNA, reverse transcriptase omitted) | 20 | HUMAN (prepared beforehand) |
| slot 5 B1 | cDNA: LPS positive control | 20 | HUMAN (prepared beforehand) |
| slot 5 B2 | cDNA: LPS positive control | 20 | HUMAN (prepared beforehand) |
| slot 5 B3 | cDNA: LPS positive control | 20 | HUMAN (prepared beforehand) |
| slot 5 C1 | cDNA: endotoxin-matched LPS control | 20 | HUMAN (prepared beforehand) |
| slot 5 C2 | cDNA: endotoxin-matched LPS control | 20 | HUMAN (prepared beforehand) |
| slot 5 C3 | cDNA: endotoxin-matched LPS control | 20 | HUMAN (prepared beforehand) |
| slot 5 D1 | cDNA: antigen low dose | 20 | HUMAN (prepared beforehand) |
| slot 5 D2 | cDNA: antigen low dose | 20 | HUMAN (prepared beforehand) |
| slot 5 D3 | cDNA: antigen low dose | 20 | HUMAN (prepared beforehand) |
| slot 5 E1 | cDNA: antigen mid dose | 20 | HUMAN (prepared beforehand) |
| slot 5 E2 | cDNA: antigen mid dose | 20 | HUMAN (prepared beforehand) |
| slot 5 E3 | cDNA: antigen mid dose | 20 | HUMAN (prepared beforehand) |
| slot 5 F1 | cDNA: antigen high dose | 20 | HUMAN (prepared beforehand) |
| slot 5 F2 | cDNA: antigen high dose | 20 | HUMAN (prepared beforehand) |
| slot 5 F3 | cDNA: antigen high dose | 20 | HUMAN (prepared beforehand) |
| slot 5 G1 | cDNA: antigen mid dose + polymyxin B | 20 | HUMAN (prepared beforehand) |
| slot 5 G2 | cDNA: antigen mid dose + polymyxin B | 20 | HUMAN (prepared beforehand) |
| slot 5 G3 | cDNA: antigen mid dose + polymyxin B | 20 | HUMAN (prepared beforehand) |
| slot 5 H1 | cDNA: mock-purification eluate | 20 | HUMAN (prepared beforehand) |
| slot 5 H2 | cDNA: mock-purification eluate | 20 | HUMAN (prepared beforehand) |
| slot 5 H3 | cDNA: mock-purification eluate | 20 | HUMAN (prepared beforehand) |
| slot 2 A1 | nuclease-free water | 462 | robot operator (load) |
| slot 2 A2 | 2x qPCR master mix | 870 | robot operator (load) |
| slot 2 A3 | GAPDH (reference) primer pair | 58 | robot operator (load) |
| slot 2 A4 | TNF primer pair | 58 | robot operator (load) |
| slot 2 A5 | IL1B primer pair | 58 | robot operator (load) |

## Plate layouts

**qPCR plate**

- GAPDH (reference): samples: columns 1-3 (rows = conditions)
- GAPDH (reference): no-template control: A4
- GAPDH (reference): no-RT control: B4
- TNF: samples: columns 5-7 (rows = conditions)
- TNF: no-template control: A8
- TNF: no-RT control: B8
- IL1B: samples: columns 9-11 (rows = conditions)
- IL1B: no-template control: A12
- IL1B: no-RT control: B12


## Step ownership

- **s13 RT QPCR readout** - ROBOT-PARTIAL: Reaction setup yes; thermocycling and reading no.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s13]: Seal the plate, spin it down and load the qPCR instrument. Thermocycling and reading are not automated.
