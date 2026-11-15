# S9 robot report - Major outer membrane protein P.IA (porA) (sp|P0DH58|OMPA_NEIMB)

> PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.

> S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

Simulator: Opentrons NOT RUN. Rules checked: tip_before_aspirate, no_overdispense, no_aspirate_from_empty_well, tip_dropped_at_end, no_cross_contamination.

## Backend `template`

Outcome tally: {'UNVERIFIED': 12}

| Session | Outcome | Tips | Rule violations | Findings |
|---|---|---|---|---|
| `s02_plasmid_assembly` | **UNVERIFIED** | 9 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s03_transformation` | **UNVERIFIED** | 5 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s04_colony_pcr` | **UNVERIFIED** | 12 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s05_expression_induction` | **UNVERIFIED** | 12 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s06_lysis` | **UNVERIFIED** | 6 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s07_ni_nta_purification` | **UNVERIFIED** | 22 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s08_endotoxin_removal` | **UNVERIFIED** | 4 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s09_lal_assay` | **UNVERIFIED** | 23 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s11_antigen_stimulation` | **UNVERIFIED** | 24 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s12_elisa_setup` | **UNVERIFIED** | 39 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s12_elisa_develop` | **UNVERIFIED** | 61 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |
| `s13_rt_qpcr` | **UNVERIFIED** | 93 | 0 | warning: simulation: no Opentrons interpreter available (set VAXFORGE_OT_PYTHON) |

## Automatability coverage (README matrix)

| Step | Owner | Robot session(s) | What a human still does |
|---|---|---|---|
| s01 Order codon-optimised gene for Major outer membrane protein P.IA (porA) | HUMAN | - | Outsourced to a vendor; sequence must clear the S12 biosecurity gate before ordering. |
| s02 Clone into His-tagged expression vector | ROBOT | `s02_plasmid_assembly` | Nothing beyond loading the deck. |
| s03 Transform expression host | ROBOT-PARTIAL | `s03_transformation` | Robot does the liquid-handling portion only. Not automated: Temperature module; plating is manual. |
| s04 Colony PCR to confirm insert | ROBOT | `s04_colony_pcr` | Nothing beyond loading the deck. |
| s05 Small-scale expression and induction | ROBOT-PARTIAL | `s05_expression_induction` | Robot does the liquid-handling portion only. Not automated: Inducer addition yes; shaking incubation no. |
| s06 Lyse cells; clarify lysate | ROBOT-PARTIAL | `s06_lysis` | Robot does the liquid-handling portion only. Not automated: Reagent addition yes; sonication/centrifugation no. |
| s07 Ni-NTA (His-tag) purification | ROBOT-PARTIAL | `s07_ni_nta_purification` | Robot does the liquid-handling portion only. Not automated: Magnetic-bead His-tag format yes; gravity column/FPLC no. |
| s08 Endotoxin removal | ROBOT-PARTIAL | `s08_endotoxin_removal` | Robot does the liquid-handling portion only. Not automated: Plate setup yes. |
| s09 Quantify residual endotoxin (LAL) | ROBOT-PARTIAL | `s09_lal_assay` | Robot does the liquid-handling portion only. Not automated: Plate setup yes; reading needs a plate reader. |
| s10 THP-1 culture and PMA differentiation | HUMAN | - | CO2 incubator and sterile cell culture; done by a person. |
| s11 Stimulate differentiated THP-1 with purified antigen | ROBOT | `s11_antigen_stimulation` | Nothing beyond loading the deck. |
| s12 ELISA readout | ROBOT-PARTIAL | `s12_elisa_setup`, `s12_elisa_develop` | Robot does the liquid-handling portion only. Not automated: Plate setup and washes yes; reading no. |
| s13 RT QPCR readout | ROBOT-PARTIAL | `s13_rt_qpcr` | Robot does the liquid-handling portion only. Not automated: Reaction setup yes; thermocycling and reading no. |

## Notes

- A PASS means the code simulated in Opentrons 7.5.0 and broke none of the five physical rules or the plan; it does NOT mean the protocol is right for the experiment.
- Reading plates, sealing, incubating, centrifuging, sonicating, plating and cell culture are human steps; each appears as a `HUMAN [step]` pause in the code.
- No Opentrons interpreter was found, so nothing was simulated (outcome UNVERIFIED). Set VAXFORGE_OT_PYTHON to a Python 3.10 environment with opentrons==7.5.0.
