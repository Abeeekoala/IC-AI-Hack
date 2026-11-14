# Workflow - fHbp (c1)

Robot and human steps in order. `ROBOT` = the OT-2 owns it; `ROBOT-PARTIAL` = the robot does the liquid handling and a person does the rest; `HUMAN` = a person or other instrument.

> PLACEHOLDER PARAMETERS: volumes, times and temperatures are coarse defaults, not validated for any kit, enzyme, cell line or assay. Set them from the kit insert / your SOP before use.

> S12 biosecurity gate NOT EVIDENCED to S9. Do not order gene synthesis or run these protocols until the S12 gate clears the sequence.

## s01. Order codon-optimised gene for fHbp  `HUMAN`
- gate: Sequence cleared by S12 biosecurity screening before ordering.
- human part: Outsourced to a vendor; sequence must clear the S12 biosecurity gate before ordering.

## s02. Clone into His-tagged expression vector  `ROBOT`
- control: no-insert assembly
- robot session `s02_plasmid_assembly` (9 tip pick-ups)
    - hand-off: HUMAN [s02]: If the supplier programme needs thermal cycling, run it in a thermocycler (not part of this robot run). Keep the reactions cold, then resume to finish the session.
- human part: Nothing beyond loading the deck.

## s03. Transform expression host  `ROBOT-PARTIAL`
- control: untransformed host on selection plate
- robot session `s03_transformation` (5 tip pick-ups)
    - hand-off: HUMAN [s03]: Confirm 25 uL competent cells are in A1-A3 of the block, held at 4 C, then resume.
    - hand-off: HUMAN [s03]: Recover the cells (typically shaking at 37 C), then plate on selective agar BY HAND. Plating is not automated.
- human part: Robot does the liquid-handling portion only. Not automated: Temperature module; plating is manual.

## s04. Colony PCR to confirm insert  `ROBOT`
- control: empty-vector colony
- control: no-template
- robot session `s04_colony_pcr` (12 tip pick-ups)
    - hand-off: HUMAN [s04]: Seal the plate and run the PCR programme and gel/readout OFF the robot (thermocycling and reading are not automated).
- human part: Nothing beyond loading the deck.

## s05. Small-scale expression and induction  `ROBOT-PARTIAL`
- control: uninduced culture
- robot session `s05_expression_induction` (12 tip pick-ups)
    - hand-off: HUMAN [s05]: Move the plate to the shaking incubator for expression. Shaking incubation is not automated.
- human part: Robot does the liquid-handling portion only. Not automated: Inducer addition yes; shaking incubation no.

## s06. Lyse cells; clarify lysate  `ROBOT-PARTIAL`
- robot session `s06_lysis` (6 tip pick-ups)
    - hand-off: HUMAN [s06]: Incubate / sonicate as the lysis method requires, then centrifuge to clarify. Sonication and centrifugation are not automated. Transfer clarified lysate to the purification plate (next session).
- human part: Robot does the liquid-handling portion only. Not automated: Reagent addition yes; sonication/centrifugation no.

## s07. Ni-NTA (His-tag) purification  `ROBOT-PARTIAL`
- control: empty-vector lysate mock purification
- gate: Soluble protein recovered; if not, candidate falsified at expression (see falsifier).
- robot session `s07_ni_nta_purification` (22 tip pick-ups)
    - hand-off: HUMAN [s07]: Seal and store the eluates; take any gel / protein-assay aliquots by hand (not automated). Continue to endotoxin removal.
- human part: Robot does the liquid-handling portion only. Not automated: Magnetic-bead His-tag format yes; gravity column/FPLC no.

## s08. Endotoxin removal  `ROBOT-PARTIAL`
- robot session `s08_endotoxin_removal` (4 tip pick-ups)
    - hand-off: HUMAN [s08]: Incubate / separate per the chosen endotoxin-removal method (phase separation, resin or column), then recover the endotoxin-reduced protein into endotoxin-free tubes. These steps are not automated.
- human part: Robot does the liquid-handling portion only. Not automated: Plate setup yes.

## s09. Quantify residual endotoxin (LAL)  `ROBOT-PARTIAL`
- control: LAL standard curve
- control: buffer blank
- gate: Residual endotoxin the release limit (NOT YET SET - decide before running) before ANY THP-1 work.
- robot session `s09_lal_assay` (23 tip pick-ups)
    - hand-off: HUMAN [s09]: Move the plate to the incubating plate reader and read per the kit. Reading is not automated.
- human part: Robot does the liquid-handling portion only. Not automated: Plate setup yes; reading needs a plate reader.

## s10. THP-1 culture and PMA differentiation  `HUMAN`
- human part: CO2 incubator and sterile cell culture; done by a person.

## s11. Stimulate differentiated THP-1 with purified antigen  `ROBOT`
- control: vehicle/buffer only
- control: LPS positive control
- control: endotoxin-matched LPS control (at the measured residual level)
- control: polymyxin B-treated antigen
- control: empty-vector mock-purification eluate
- robot session `s11_antigen_stimulation` (24 tip pick-ups)
    - hand-off: HUMAN [s11]: Return the plate to the 37 C / 5% CO2 incubator for the stimulation period, then collect supernatants (ELISA) and lyse cells (RT-qPCR). The incubator is not automated.
- human part: Nothing beyond loading the deck.

## s12. ELISA readout  `ROBOT-PARTIAL`
- robot session `s12_elisa_setup` (39 tip pick-ups)
    - hand-off: HUMAN [s12]: Seal the plate and incubate 120 min (room temperature, shaking if the kit says so). Then put the plate back on the deck for the develop session. Sealing and incubation are not automated.
- robot session `s12_elisa_develop` (61 tip pick-ups)
    - hand-off: HUMAN [s12]: Seal and incubate 60 min, then unseal and resume.
    - hand-off: HUMAN [s12]: Seal and incubate 30 min, then unseal and resume.
    - hand-off: HUMAN [s12]: Protect from light and incubate 20 min, then resume.
    - hand-off: HUMAN [s12]: Read the plate in a plate reader at the kit's wavelength. Reading is not automated.
- human part: Robot does the liquid-handling portion only. Not automated: Plate setup and washes yes; reading no.

## s13. RT QPCR readout  `ROBOT-PARTIAL`
- robot session `s13_rt_qpcr` (93 tip pick-ups)
    - hand-off: HUMAN [s13]: Seal the plate, spin it down and load the qPCR instrument. Thermocycling and reading are not automated.
- human part: Robot does the liquid-handling portion only. Not automated: Reaction setup yes; thermocycling and reading no.

## s14. FACS readout  `ROBOT-PARTIAL`
- robot session `s14_facs_prep` (1 tip pick-ups)
    - hand-off: HUMAN [s14]: Incubate protected from light, wash / centrifuge and acquire on the cytometer. None of this is automated.
- human part: Robot does the liquid-handling portion only. Not automated: Staining prep yes; acquisition no.

---
S8 falsification condition: At the in-vitro stage this candidate is falsified if EITHER (a) it cannot be expressed soluble and purified to the endotoxin limit, OR (b) the cytokine/transcript response does not exceed the endotoxin-matched LPS control and the polymyxin B-treated arm. A response explained by residual endotoxin is not evidence for the antigen.

What the in-vitro cascade does NOT measure: adaptive immunity; antibody titre; neutralisation; T-cell memory; protection; population HLA diversity (THP-1 is a single-genotype cell line).
