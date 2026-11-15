# Plasmid assembly reaction set-up

Session `s02_plasmid_assembly` covers S8 step(s): s02

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 3 | temperature module gen2 | module |
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | Reagent tubes |
| 3 | `opentrons_96_aluminumblock_nest_wellplate_100ul` | Assembly reactions on the 4 C block on temp |
| 11 | `opentrons_96_tiprack_20ul` | p20 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p20_single_gen2` (left): 9 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 2 A1 | nuclease-free water | 56 | robot operator (load) |
| slot 2 A2 | assembly buffer (10x) | 34 | robot operator (load) |
| slot 2 A3 | linearised expression vector | 34 | robot operator (load) |
| slot 2 A4 | insert (synthesised gene fragment) - vendor delivery; S12 biosecurity screening must precede ordering | 32 | robot operator (load) |
| slot 2 A5 | assembly enzyme mix | 34 | robot operator (load) |

## Plate layouts

**Reaction block**

- candidate assembly: A1
- no-insert assembly control: A2


## Step ownership

- **s02 Clone into His-tagged expression vector** - ROBOT: Liquid handling + temperature module.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s02]: If the supplier programme needs thermal cycling, run it in a thermocycler (not part of this robot run). Keep the reactions cold, then resume to finish the session.

## Notes

- Assembly chemistry and thermal programme are not specified by S8; the 4 C hold + single incubation here is a stand-in.
