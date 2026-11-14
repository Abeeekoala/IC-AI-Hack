# Ni-NTA magnetic-bead purification

Session `s07_ni_nta_purification` covers S8 step(s): s07

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 6 | magnetic module gen2 | module |
| 1 | `nest_1_reservoir_195ml` | Liquid waste |
| 2 | `opentrons_24_tuberack_nest_1.5ml_snapcap` | Bead slurry |
| 3 | `nest_96_wellplate_200ul_flat` | Eluate collection plate |
| 5 | `nest_12_reservoir_15ml` | Wash and elution buffers |
| 6 | `nest_96_wellplate_2ml_deep` | Clarified lysates on the magnetic module on mag |
| 11 | `opentrons_96_tiprack_300ul` | p300 tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p300_single_gen2` (right): 22 tip pick-up(s); 1 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 6 A1 | candidate lysate - clarified lysate placed by the human | 500 | HUMAN (prepared beforehand) |
| slot 6 B1 | empty-vector lysate (mock purification control) - clarified lysate placed by the human | 500 | HUMAN (prepared beforehand) |
| slot 5 A1 | wash buffer | 1700 | robot operator (load) |
| slot 5 A2 | elution buffer | 700 | robot operator (load) |
| slot 2 A1 | Ni-NTA magnetic bead slurry | 130 | robot operator (load) |

## Plate layouts

**Magnetic plate**

- candidate lysate: A1
- empty-vector lysate (mock purification control): B1


## Step ownership

- **s07 Ni-NTA (His-tag) purification** - ROBOT-PARTIAL: Magnetic-bead His-tag format yes; gravity column/FPLC no.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s07]: Seal and store the eluates; take any gel / protein-assay aliquots by hand (not automated). Continue to endotoxin removal.

## Notes

- Gravity-column and FPLC formats are NOT supported; this is the magnetic-bead His-tag format only.
