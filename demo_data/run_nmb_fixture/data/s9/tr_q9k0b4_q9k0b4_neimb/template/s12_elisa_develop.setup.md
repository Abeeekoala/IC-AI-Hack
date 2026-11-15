# ELISA (TNF-alpha) washes, detection and development

Session `s12_elisa_develop` covers S8 step(s): s12

> PLACEHOLDER PARAMETERS - volumes and times are defaults, not validated for any kit. Confirm against the kit insert / your SOP before use.

## Deck

| Slot | Labware | Notes |
|---|---|---|
| 2 | `nest_12_reservoir_15ml` | Detection reagents |
| 3 | `nest_96_wellplate_200ul_flat` | ELISA plate (sealed incubation finished) |
| 4 | `nest_1_reservoir_195ml` | Liquid waste |
| 5 | `nest_1_reservoir_195ml` | Wash buffer |
| 6 | `opentrons_96_tiprack_300ul` | p300m tips |
| 7 | `opentrons_96_tiprack_300ul` | p300m tips |
| 8 | `opentrons_96_tiprack_300ul` | p300m tips |
| 9 | `opentrons_96_tiprack_300ul` | p300m tips |
| 10 | `opentrons_96_tiprack_300ul` | p300m tips |
| 11 | `opentrons_96_tiprack_300ul` | p300m tips |
| 12 | fixed trash | |

## Pipettes and tips

- `p300_multi_gen2` (right): 61 tip pick-up(s); 6 rack(s) loaded

## Load before starting

| Where | What | Volume (uL) | Prepared by |
|---|---|---|---|
| slot 3 A1 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 A2 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 A3 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 A4 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 B1 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 B2 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 B3 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 B4 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 C1 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 C2 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 C3 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 C4 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 D1 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 D2 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 D3 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 D4 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 E1 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 E2 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 E3 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 E4 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 F1 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 F2 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 F3 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 F4 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 G1 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 G2 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 G3 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 G4 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 H1 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 H2 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 H3 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 3 H4 | standard / sample (from the setup session) | 50 | HUMAN (prepared beforehand) |
| slot 2 A1 | detection antibody | 3700 | robot operator (load) |
| slot 2 A2 | streptavidin-HRP | 3700 | robot operator (load) |
| slot 2 A3 | substrate | 3700 | robot operator (load) |
| slot 2 A4 | stop solution | 2100 | robot operator (load) |
| slot 5 A1 | wash buffer | 53840 | robot operator (load) |

## Step ownership

- **s12 ELISA readout** - ROBOT-PARTIAL: Plate setup and washes yes; reading no.

## Human hand-offs inside this run (the robot pauses)

1. HUMAN [s12]: Seal and incubate 60 min, then unseal and resume.
2. HUMAN [s12]: Seal and incubate 30 min, then unseal and resume.
3. HUMAN [s12]: Protect from light and incubate 20 min, then resume.
4. HUMAN [s12]: Read the plate in a plate reader at the kit's wavelength. Reading is not automated.

## Notes

- Fresh tip per column for every aspiration (no cross-contamination between samples); this is why the session is tip-hungry.
