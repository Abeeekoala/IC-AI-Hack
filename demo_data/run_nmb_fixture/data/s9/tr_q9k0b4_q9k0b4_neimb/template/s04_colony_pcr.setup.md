# Colony PCR reaction set-up

Session `s04_colony_pcr` covers S8 step(s): s04

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 3 | temperature module gen2 | module |
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | PCR reagents + master-mix tube |
| 3 | `opentrons_96_aluminumblock_nest_wellplate_100ul` | PCR reactions on the 4 C block on temp |
| 5 | `biorad_96_wellplate_200ul_pcr` | Colony resuspensions (human-prepared) |
| 10 | `opentrons_96_tiprack_300ul` | p300 tips |
| 11 | `opentrons_96_tiprack_20ul` | p20 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p20_single_gen2` (left): 9 tip pick-up(s); 1 rack(s) loaded
- `p300_single_gen2` (right): 3 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 5 A1 | candidate colony 1 resuspension | 10 | HUMAN (prepared beforehand) |
| slot 5 B1 | candidate colony 2 resuspension | 10 | HUMAN (prepared beforehand) |
| slot 5 C1 | candidate colony 3 resuspension | 10 | HUMAN (prepared beforehand) |
| slot 5 D1 | candidate colony 4 resuspension | 10 | HUMAN (prepared beforehand) |
| slot 5 E1 | empty-vector colony control resuspension | 10 | HUMAN (prepared beforehand) |
| slot 2 A1 | nuclease-free water | 87 | robot operator (load) |
| slot 2 A2 | 2x PCR master mix | 110 | robot operator (load) |
| slot 2 A3 | forward primer | 38 | robot operator (load) |
| slot 2 A4 | reverse primer | 38 | robot operator (load) |

## Plate layouts

**Reaction block**

- candidate colony 1: A1
- candidate colony 2: B1
- candidate colony 3: C1
- candidate colony 4: D1
- empty-vector colony control: E1
- no-template control: F1


## Step ownership

- **s04 Colony PCR to confirm insert** - ROBOT: Reaction assembly only.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s04]: Seal the plate and run the PCR programme and gel/readout OFF the robot (thermocycling and reading are not automated).

## Notes

- Master mix is assembled for two spare reactions; the leftover stays in tube B1.
