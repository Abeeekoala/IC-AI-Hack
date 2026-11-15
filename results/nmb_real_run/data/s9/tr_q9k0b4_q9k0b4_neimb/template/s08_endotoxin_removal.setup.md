# Endotoxin-removal plate set-up

Session `s08_endotoxin_removal` covers S8 step(s): s08

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 3 | `nest_96_wellplate_200ul_flat` | Eluates from purification |
| 4 | `nest_96_wellplate_2ml_deep` | Endotoxin-removal plate |
| 5 | `nest_12_reservoir_15ml` | Removal reagent |
| 11 | `opentrons_96_tiprack_300ul` | p300 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p300_single_gen2` (right): 4 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 3 A1 | candidate eluate | 100 | HUMAN (prepared beforehand) |
| slot 3 B1 | empty-vector mock eluate | 100 | HUMAN (prepared beforehand) |
| slot 5 A1 | endotoxin-removal reagent | 600 | robot operator (load) |

## Plate layouts

**Removal plate**

- candidate eluate: A1
- empty-vector mock eluate: B1


## Step ownership

- **s08 Endotoxin removal** - ROBOT-PARTIAL: Plate setup yes.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s08]: Incubate / separate per the chosen endotoxin-removal method (phase separation, resin or column), then recover the endotoxin-reduced protein into endotoxin-free tubes. These steps are not automated.

## Notes

- S8 does not fix the endotoxin-removal chemistry; choose a method and replace this set-up accordingly.
