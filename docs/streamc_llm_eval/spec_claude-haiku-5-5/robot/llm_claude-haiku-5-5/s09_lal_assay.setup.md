# LAL assay plate set-up

Session `s09_lal_assay` covers S8 step(s): s09

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | LAL reagents and samples |
| 3 | `nest_96_wellplate_200ul_flat` | LAL assay plate (endotoxin-free) |
| 10 | `opentrons_96_tiprack_300ul` | p300 tips |
| 11 | `opentrons_96_tiprack_20ul` | p20 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p20_single_gen2` (left): 9 tip pick-up(s); 1 rack(s) loaded
- `p300_single_gen2` (right): 14 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 2 A1 | LAL reagent water (diluent) | 415 | robot operator (load) |
| slot 2 A2 | endotoxin standard (top concentration) | 96 | robot operator (load) |
| slot 2 A3 | candidate sample (endotoxin-reduced) - from endotoxin removal | 130 | robot operator (load) |
| slot 2 A4 | mock eluate sample (endotoxin-reduced) - from endotoxin removal | 130 | robot operator (load) |
| slot 2 A5 | LAL reagent | 680 | robot operator (load) |

## Plate layouts

**LAL plate**

- standard 1: A1
- standard 2: B1
- standard 3: C1
- standard 4: D1
- standard 5: E1
- standard 6: F1
- blank (diluent only): G1
- candidate neat: A2
- candidate 1:10: B2
- candidate + endotoxin spike (PPC): C2
- mock eluate neat: D2
- mock eluate 1:10: E2
- mock eluate + endotoxin spike (PPC): F2


## Step ownership

- **s09 Quantify residual endotoxin (LAL)** - ROBOT-PARTIAL: Plate setup yes; reading needs a plate reader.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s09]: Move the plate to the incubating plate reader and read per the kit. Reading is not automated.

## Notes

- S8 gate for this step: Residual endotoxin the release limit (NOT YET SET - decide before running) before ANY THP-1 work.
- Use certified endotoxin-free tips and plates for every liquid-handling step of this assay.
