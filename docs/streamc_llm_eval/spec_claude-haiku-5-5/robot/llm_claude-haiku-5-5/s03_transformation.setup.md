# Heat-shock transformation set-up

Session `s03_transformation` covers S8 step(s): s03

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 3 | temperature module gen2 | module |
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | Recovery medium |
| 3 | `opentrons_96_aluminumblock_nest_wellplate_100ul` | Competent cells on the temperature block on temp |
| 5 | `biorad_96_wellplate_200ul_pcr` | Assembly products (from the assembly session) |
| 10 | `opentrons_96_tiprack_300ul` | p300 tips |
| 11 | `opentrons_96_tiprack_20ul` | p20 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p20_single_gen2` (left): 2 tip pick-up(s); 1 rack(s) loaded
- `p300_single_gen2` (right): 3 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 3 A1 | competent cells - candidate assembly | 25 | HUMAN (prepared beforehand) |
| slot 3 A2 | competent cells - no-insert assembly control | 25 | HUMAN (prepared beforehand) |
| slot 3 A3 | competent cells - untransformed host control (no DNA) | 25 | HUMAN (prepared beforehand) |
| slot 5 A1 | assembled plasmid (candidate) - from the candidate assembly reaction | 4 | robot operator (load) |
| slot 5 A2 | assembled plasmid (no-insert control) - from the no-insert assembly control | 4 | robot operator (load) |
| slot 2 A1 | SOC / recovery medium | 180 | robot operator (load) |

## Plate layouts

**Transformation block**

- candidate assembly: A1
- no-insert assembly control: A2
- untransformed host control (no DNA): A3


## Step ownership

- **s03 Transform expression host** - ROBOT-PARTIAL: Temperature module; plating is manual.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s03]: Confirm 25 uL competent cells are in A1-A3 of the block, held at 4 C, then resume.
2. HUMAN [s03]: Recover the cells (typically shaking at 37 C), then plate on selective agar BY HAND. Plating is not automated.

## Notes

- The untransformed-host control is plated by the human; S8 lists it as 'untransformed host on selection plate'.
