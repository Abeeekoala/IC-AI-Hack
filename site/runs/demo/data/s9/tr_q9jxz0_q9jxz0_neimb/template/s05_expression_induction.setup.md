# Inducer addition to expression cultures

Session `s05_expression_induction` covers S8 step(s): s05

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | Inducer + vehicle |
| 5 | `nest_96_wellplate_2ml_deep` | Expression cultures (human-inoculated) |
| 10 | `opentrons_96_tiprack_300ul` | p300 tips |
| 11 | `opentrons_96_tiprack_20ul` | p20 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p20_single_gen2` (left): 6 tip pick-up(s); 1 rack(s) loaded
- `p300_single_gen2` (right): 6 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 5 A1 | expression culture (to be induced) | 1000 | HUMAN (prepared beforehand) |
| slot 5 B1 | expression culture (to be induced) | 1000 | HUMAN (prepared beforehand) |
| slot 5 C1 | expression culture (to be induced) | 1000 | HUMAN (prepared beforehand) |
| slot 5 D1 | expression culture (uninduced control) | 1000 | HUMAN (prepared beforehand) |
| slot 5 E1 | expression culture (uninduced control) | 1000 | HUMAN (prepared beforehand) |
| slot 5 F1 | expression culture (uninduced control) | 1000 | HUMAN (prepared beforehand) |
| slot 2 A1 | inducer stock | 45 | robot operator (load) |
| slot 2 A2 | vehicle (same solvent, no inducer) | 45 | robot operator (load) |

## Plate layouts

**Culture plate**

- induced: A1,B1,C1
- uninduced control: D1,E1,F1


## Step ownership

- **s05 Small-scale expression and induction** - ROBOT-PARTIAL: Inducer addition yes; shaking incubation no.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s05]: Move the plate to the shaking incubator for expression. Shaking incubation is not automated.

## Notes

- Inducer volume = culture x final/stock = 1000 x 0.5/100 = 5 uL.
