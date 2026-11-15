# Lysis reagent addition

Session `s06_lysis` covers S8 step(s): s06

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 2 | `nest_12_reservoir_15ml` | Lysis buffer |
| 5 | `nest_96_wellplate_2ml_deep` | Cell pellets (supernatant removed by the human) |
| 11 | `opentrons_96_tiprack_300ul` | p300 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p300_single_gen2` (right): 6 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 2 A1 | lysis buffer | 2300 | robot operator (load) |

## Plate layouts

**Pellet plate**

- induced: A1,B1,C1
- uninduced control: D1,E1,F1


## Step ownership

- **s06 Lyse cells; clarify lysate** - ROBOT-PARTIAL: Reagent addition yes; sonication/centrifugation no.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s06]: Incubate / sonicate as the lysis method requires, then centrifuge to clarify. Sonication and centrifugation are not automated. Transfer clarified lysate to the purification plate (next session).
