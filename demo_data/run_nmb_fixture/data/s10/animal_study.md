# Animal immunogenicity study proposal - Neisseria meningitidis

> **DESIGN ONLY.** This document is a study DESIGN. It specifies arms, group sizes, schedule, endpoints, statistics and ethics documentation. It contains no results, no simulated data and no assay values. It does not contain pathogen handling, culture or challenge-inoculum procedures: those belong with the containment facility and its biosafety committee and are deliberately out of scope for this tool.

Schema s10.v1. Candidates (rank order): tr|Q9K0B4|Q9K0B4_NEIMB, tr|Q9JXZ0|Q9JXZ0_NEIMB, tr|Q9JYA8|Q9JYA8_NEIMB.

## 1. Assumptions to replace before submission

- Effect size: **ASSUMPTION - replace with pilot data**. Assumed difference 0.7 log10 titre, assumed SD 0.5 log10 (Cohen's d = 1.4). This is not a finding.
- Dose levels (ug per immunisation): 1, 5, 20 - ASSUMPTION - placeholder levels for the species; set from pilot data or literature.
- Humane-endpoint weight-loss threshold 15% is a design default to be confirmed by AWERB / IACUC / the named veterinary person.
- Warning: Licensed-vaccine availability not supplied: CHECK and add the positive control arm if one exists.
- Warning: No established challenge model: the study can show immunogenicity only, not protection.
- Warning: Effect size and SD are ASSUMPTIONS (no pilot data cited); the sample size is only as good as they are.

## 2. Species and model

- Goal: `basic_immunogenicity`; species: **mouse_wild_type**; strain: BALB/c.
- Rationale: Basic immunogenicity: cheap and fast (README S10). Wild-type mouse MHC (H-2) does NOT present the epitopes NetMHCpan predicted for human HLA, so the S5 T-cell and S6 population-coverage predictions are NOT under test in this design; only S3 (antibody titre), S5 B-cell epitopes and S7 (folded vs denatured) are.
- Strain: BALB/c: Th2-leaning, IgG1-prone background with robust antibody responses - chosen as the default because the primary endpoint is a serum IgG titre. A poor choice if IFN-gamma / Th1 readouts are primary. IgG subclass detection must include IgG2a.
- Sex: female; age (weeks): (6, 8).
- Human MHC predictions (S5/S6) under test in this design: **False**.
- Non-human primates are OUT OF SCOPE for this proposal (last resort; ethically and financially constrained, README S10). No NHP design is provided.

### Challenge model

- Status: `unknown_check`. Whether a susceptible small-animal challenge model exists for Neisseria meningitidis is UNKNOWN in the supplied pathogen context - CHECK the literature. No challenge model is established in this proposal and none is proposed or invented. The study measures immunogenicity only and cannot test protection in vivo.
- No challenge phase is designed here. Challenge dose, route and any pathogen handling belong with the containment facility and its biosafety committee and are out of scope for this tool.

### Correlate of protection

- No correlate of protection is known or agreed for Neisseria meningitidis in the supplied pathogen context. Antibody titres from this study are therefore NOT evidence of protection. Most vaccines lack an agreed correlate (README S10); do not assume one - check, and read Plotkin (Clin Vaccine Immunol, 2010) before making correlate claims.

## 3. Intervention

- Adjuvant: **alum** (Th2). Aluminium-salt adjuvant: biases the response towards Th2 / IgG1 (README S10). Long clinical use makes it a defensible default for a first antibody-focused study; it is a weak inducer of IFN-gamma / Th1 responses, so it is a poor match for T-cell epitope readouts.
- Route: **IM** - target compartment: systemic (serum IgG). Intramuscular: depot in muscle, the usual route for injectable and alum-adjuvanted vaccines. Targets the systemic compartment, which is what the primary endpoint (serum IgG) measures. Mouse muscle volume is small: injection volume per site must respect licence limits.
- Primary dose: 20 ug. Highest dose level, so that a null result cannot be explained simply by under-dosing - the falsification claim ('no titre rise over adjuvant-only') is only informative at a dose given a fair chance. Reactogenicity at this level must be checked against the humane endpoints.
- Dose ranging: All dose levels are run for the rank-1 candidate only; other candidates are run at the primary dose (Reduction). Dose-response is an exploratory, not confirmatory, comparison.

## 4. Study arms

| Arm | Role | Label | Dose (ug) | n/group | Confirmatory | Purpose |
|---|---|---|---|---|---|---|
| A01 | candidate_antigen_plus_adjuvant | IgA-specific serine endopeptidase (tr\|Q9K0B4\|Q9K0B4_NEIMB, S7 final rank 1) 1 ug + alum | 1 | 13 | no | Exploratory dose-response arm (not in the confirmatory family). |
| A02 | candidate_antigen_plus_adjuvant | IgA-specific serine endopeptidase (tr\|Q9K0B4\|Q9K0B4_NEIMB, S7 final rank 1) 5 ug + alum | 5 | 13 | no | Exploratory dose-response arm (not in the confirmatory family). |
| A03 | candidate_antigen_plus_adjuvant | IgA-specific serine endopeptidase (tr\|Q9K0B4\|Q9K0B4_NEIMB, S7 final rank 1) 20 ug + alum | 20 | 13 | yes | Confirmatory arm: candidate vs adjuvant-only on the primary endpoint. |
| A04 | candidate_antigen_plus_adjuvant | TonB-dependent receptor (NMB1829) (tr\|Q9JXZ0\|Q9JXZ0_NEIMB, S7 final rank 2) 20 ug + alum | 20 | 13 | yes | Confirmatory arm: candidate vs adjuvant-only on the primary endpoint. |
| A05 | candidate_antigen_plus_adjuvant | Hemoglobin receptor (hmbR) (tr\|Q9JYA8\|Q9JYA8_NEIMB, S7 final rank 3) 20 ug + alum | 20 | 13 | yes | Confirmatory arm: candidate vs adjuvant-only on the primary endpoint. |
| A06 | adjuvant_only | Adjuvant only (alum) + antigen vehicle/buffer | - | 13 | no | Comparator for the primary endpoint: the adjuvant alone can raise non-specific responses. Same adjuvant dose, buffer composition, route and schedule as the candidate arms. One shared arm serves every candidate comparison (Reduction). |
| A07 | naive_pbs | Naive / PBS (no antigen, no adjuvant) | - | 13 | no | Baseline for assay background and handling effects; same route, schedule and bleeds. |

Positive control: `check`. CHECK: whether a licensed vaccine exists for this pathogen was not supplied, so no positive control arm is added. If one exists, add the licensed-vaccine positive control arm before ethics submission (README S10 minimum arms).

## 5. Schedule

- Immunise: days [0, 21, 42]. Bleed: days [0, 20, 41, 56]. Primary timepoint: day 56.
- Prime-boost: immunise on days [0, 21, 42]; bleed on days [0, 20, 41, 56]. Bleeds on or before an immunisation day give baseline / pre-boost samples; the primary timepoint is day 56, 14 days after the final immunisation. Total bleed volume per animal must stay within the limits of the project licence / institutional guidance (needs_human_input).
- Terminal exsanguination and serum collection at day 56.

## 6. Sample size (power analysis)

- Test: Two-sided two-sample t-test (Welch in analysis; equal-n/equal-SD for planning), noncentral t; endpoint: log10 antigen-specific serum IgG endpoint titre; comparator: adjuvant-only arm.
- Effect size d = 1.4 (ASSUMPTION - replace with pilot data); family-wise alpha = 0.05; target power = 0.8.
- Multiplicity: holm; 3 comparison(s); planning alpha per comparison = 0.01667.
- **n per group (analysed) = 13** (power at that n = 0.831); attrition allowance = 0; **n per group (allocated) = 13**; 7 arms; **total animals = 91**.

| Sensitivity | Effect size d | n per group |
|---|---|---|
| as assumed | 1.4 | 13 |
| effect 25% smaller | 1.05 | 21 |
| effect 50% smaller | 0.7 | 45 |

- Sample size is PLANNED at the Bonferroni level alpha/k for either method; Holm's step-down procedure is uniformly at least as powerful, so the plan is conservative for Holm.
- The adjuvant-only arm is shared by all k candidate comparisons, so the comparisons are positively correlated and Bonferroni is conservative; Dunnett's many-to-one test is a pre-specifiable alternative.
- Group size is floored at 3 per group (variance estimation); floor applied: False.
- Titres at or below the assay limit of detection violate the normality assumption; see the non-parametric sensitivity analysis in the analysis plan.
- The result is only as good as the assumed effect size and SD. If the true effect is smaller, the study is under-powered (see sensitivity table).

## 7. Endpoints

**Primary (single, pre-specified, locked before data collection):** Antigen-specific serum IgG endpoint titre (log10) at the primary timepoint.

- Definition: Log10 of the reciprocal endpoint dilution of serum IgG binding the immunising antigen, one value per animal, from the primary-timepoint bleed
- Assay: Antigen-specific IgG ELISA with a pre-specified endpoint definition and lower limit of quantification; timepoint: day 56.
- Comparison: Each candidate + adjuvant arm at the primary dose vs the adjuvant-only arm (3 comparison(s)); summary: Difference in mean log10 titre (equivalently the geometric-mean titre ratio) with a 95% CI.
- Lock: Frozen, with the analysis plan, in the S11 pre-registration manifest (timestamped) and/or a public registry chosen by the investigators, before the first animal is dosed.

**Secondary / exploratory:**

- IgG titre at every scheduled bleed (kinetics) - exploratory.
- IgG subclass profile (IgG1 vs IgG2a/c) at the primary timepoint - exploratory; reports the Th bias that the alum rationale (Th2) suggests.
- Dose-response across levels 1, 5, 20 ug - exploratory.

## 8. Randomisation and blinding

- Unit: individual animal (the experimental unit); cage ID is recorded for every animal.
- Method: Stratified randomisation to arms using a computer-generated sequence. Sequence: Seeded pseudo-random permutation, generated by someone who does not run the experiment.
- Allocation concealment: The allocation key is held by a named custodian (needs_human_input); other staff see only coded syringe and cage labels.
- Stratification: baseline body weight, cage/litter of origin, sex (if both sexes are used).
- Seed: The random seed and generated allocation are recorded in the S11 pre-registration manifest.
- Cage effects: Animals of one arm may need to be co-housed for welfare; cage is then recorded and used as a cluster in a sensitivity analysis.
- Assay plates: Samples are coded and arms are interleaved and randomised across assay plates so that plate and batch effects are not confounded with arm.

| Role | Blinded to arm? | How | Limitation |
|---|---|---|---|
| Allocation custodian / dose preparer | no | Holds the allocation key and prepares coded syringes; takes no part in outcome assessment | - |
| Animal technician administering immunisations and taking bleeds | yes | Works from coded syringes and cage cards | Partial: adjuvant formulations can look different from PBS (e.g. turbidity) |
| Welfare / humane-endpoint assessor | yes | Scores from coded cage cards | Welfare decisions are never delayed to preserve blinding: unblind immediately if needed |
| Assay operators (ELISA, ELISpot, functional assays) | yes | Coded sample IDs; arm labels withheld until the raw data are locked | - |
| Statistician | yes | Analyses coded arm labels; labels revealed only after the analysis plan and dataset are locked | - |

## 9. Humane endpoints

- Anticipated severity: mild (proposal only; AWERB / IACUC to confirm). Weighing: On every immunisation and bleed day, and at least twice weekly otherwise.
- Monitoring: Day of each immunisation, after recovery and again at end of the working day - at least every 8 h.
- Monitoring: Days 1-3 after each immunisation - at least every 24 h.
- Monitoring: All remaining days of the schedule - at least every 24 h.

| Criterion | Action |
|---|---|
| Body-weight loss >= 15% of baseline | Humane killing (design default threshold; confirm against the licence/protocol severity limit) |
| Persistent hunched posture, piloerection or markedly reduced activity | Same-day consultation with the named animal-care / veterinary person; humane killing if not rapidly resolving |
| Laboured breathing | Humane killing without delay |
| Injection-site ulceration, abscess or swelling impairing normal movement | Veterinary consultation; humane killing if normal function is impaired |
| Unable to reach food or water, or unresponsive to gentle stimulation | Immediate humane killing |
| Excessive bleeding or failure to recover after blood sampling | Stop sampling; veterinary consultation |

Humane killing by a method approved for the project (UK: Schedule 1 / licence-specified; US: per the IACUC protocol and AVMA guidance). Animals reaching a humane endpoint are reported, not silently excluded.

## 10. Statistical analysis plan (pre-specified)

- Primary analysis: Welch two-sample t-test on log10 titres, each candidate arm (primary dose) vs adjuvant-only; scale: log10 endpoint titre.
- Below-LLOQ rule: Values below the assay lower limit of quantification are assigned half the LLOQ, by one rule pre-specified for all arms.
- Confirmatory family (3):
  - A03 (IgA-specific serine endopeptidase (tr|Q9K0B4|Q9K0B4_NEIMB, S7 final rank 1) 20 ug + alum) vs A06 (adjuvant only)
  - A04 (TonB-dependent receptor (NMB1829) (tr|Q9JXZ0|Q9JXZ0_NEIMB, S7 final rank 2) 20 ug + alum) vs A06 (adjuvant only)
  - A05 (Hemoglobin receptor (hmbR) (tr|Q9JYA8|Q9JYA8_NEIMB, S7 final rank 3) 20 ug + alum) vs A06 (adjuvant only)
- Multiplicity (holm): Holm step-down across the 3 candidate-vs-adjuvant-only comparison(s), family-wise alpha 0.05 (two-sided); controls the family-wise error rate and is uniformly at least as powerful as Bonferroni. Sample size was planned at the Bonferroni level alpha/k = 0.01667.
- Analysis population: All randomised animals with a valid sample at the primary timepoint, analysed in the arm allocated; numbers analysed per arm are reported.
- Exclusion rules: Technical failure of dosing (documented leak or mis-injection), recorded before unblinding; Failed or insufficient sample at the primary timepoint (no imputation); No exclusion of data points as 'outliers' after unblinding.
- Outliers: No post-hoc outlier removal; any exclusion must be one of the pre-specified rules above.
- Sensitivity analyses: Mann-Whitney U on the same comparisons with the same multiplicity correction (censored titres); Cage as a cluster / random effect; Analysis including animals that reached a humane endpoint, where data exist.
- Exploratory: Dose-response, IgG subclass, kinetics and the falsification-map readouts: descriptive, with estimates and CIs, no confirmatory claims; Positive control vs naive: assay/model sensitivity check, outside the confirmatory family.

## 11. Falsification map

Each upstream prediction is paired with the readout that can refute it. Rows without an upstream prediction are `not_testable_no_upstream_prediction`; nothing is fabricated. Under each table, `Rule if testable` is the README's fixed 'falsified if' rule for that row's readout, given for every row; for a row that is not testable it is the rule that would apply, not a claim that the row was tested.

### tr|Q9K0B4|Q9K0B4_NEIMB - IgA-specific serine endopeptidase

| Stage | Upstream prediction (from data) | Animal readout | Falsified if | Status |
|---|---|---|---|---|
| S3 | S3 scores IgA-specific serine endopeptidase (protegenicity 0.997939); S7's final order, which also weighs T-cell, B-cell, coverage and structure terms, puts it at position 1, i.e. predicts it is immunogenic. | Antigen-specific serum IgG ELISA titre (endpoint titre, log10) at the primary timepoint. | No titre rise over the adjuvant-only arm. | `testable` |
| S5_T_cell | - | IFN-gamma ELISpot on splenocytes stimulated with (a) the predicted class I pool(s), (b) the predicted class II pool(s), (c) whole recombinant antigen. | - | `not_testable_no_upstream_prediction` |
| S5_B_cell | - | Competition ELISA (immune sera pre-incubated with the predicted epitope peptide vs an irrelevant peptide) or peptide-array mapping (overlapping peptides spanning the predicted regions). | - | `not_testable_no_upstream_prediction` |
| S6 | - | IFN-gamma ELISpot to the predicted T-cell pools in a panel of HLA-transgenic lines, one line per restricting allele predicted to cover the populations of interest. | - | `not_testable_no_upstream_prediction` |
| S7 | - | ELISA binding of immune sera to the folded (native) vs denatured (heat or chemically denatured) antigen. | - | `not_testable_no_upstream_prediction` |
| functional | - | Species-appropriate functional assay on immune sera. | - | `not_testable_no_upstream_prediction` |

- S3: Rule if testable: No titre rise over the adjuvant-only arm.
- S5_T_cell: Rule if testable: Response to whole antigen but NOT to the predicted pools - antigen right, epitope prediction wrong. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S5_T_cell: No T-cell epitopes (T_class_I / T_class_II) in the upstream S5 output for this candidate.
- S5_B_cell: Rule if testable: Sera bind the antigen but not the predicted epitopes. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S5_B_cell: No B-cell epitopes (B_linear / B_conformational) in the upstream S5 output for this candidate.
- S6: Rule if testable: Response absent in backgrounds predicted to be covered. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S6: No S6 population-coverage data (pc90 / coverage_by_population) for this candidate.
- S7: Rule if testable: Sera recognise only the denatured form - the conformational epitope was an artifact. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S7: No S7 structure model for this candidate: S7 is top-k only, or its structure step did not run (see the artifact warnings).
- functional: Rule if testable: Titres rise but functional activity is absent. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- functional: No correlate of protection is known or agreed for this pathogen, so no functional assay can be named as the test of protection and titres are not protection. An investigator may add a mechanism-based functional assay (e.g. neutralisation or opsonophagocytosis) as an exploratory readout; that is not a correlate.

### tr|Q9JXZ0|Q9JXZ0_NEIMB - TonB-dependent receptor (NMB1829)

| Stage | Upstream prediction (from data) | Animal readout | Falsified if | Status |
|---|---|---|---|---|
| S3 | S3 scores TonB-dependent receptor (NMB1829) (protegenicity 0.996565); S7's final order, which also weighs T-cell, B-cell, coverage and structure terms, puts it at position 2, i.e. predicts it is immunogenic. | Antigen-specific serum IgG ELISA titre (endpoint titre, log10) at the primary timepoint. | No titre rise over the adjuvant-only arm. | `testable` |
| S5_T_cell | - | IFN-gamma ELISpot on splenocytes stimulated with (a) the predicted class I pool(s), (b) the predicted class II pool(s), (c) whole recombinant antigen. | - | `not_testable_no_upstream_prediction` |
| S5_B_cell | - | Competition ELISA (immune sera pre-incubated with the predicted epitope peptide vs an irrelevant peptide) or peptide-array mapping (overlapping peptides spanning the predicted regions). | - | `not_testable_no_upstream_prediction` |
| S6 | - | IFN-gamma ELISpot to the predicted T-cell pools in a panel of HLA-transgenic lines, one line per restricting allele predicted to cover the populations of interest. | - | `not_testable_no_upstream_prediction` |
| S7 | - | ELISA binding of immune sera to the folded (native) vs denatured (heat or chemically denatured) antigen. | - | `not_testable_no_upstream_prediction` |
| functional | - | Species-appropriate functional assay on immune sera. | - | `not_testable_no_upstream_prediction` |

- S3: Rule if testable: No titre rise over the adjuvant-only arm.
- S5_T_cell: Rule if testable: Response to whole antigen but NOT to the predicted pools - antigen right, epitope prediction wrong. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S5_T_cell: No T-cell epitopes (T_class_I / T_class_II) in the upstream S5 output for this candidate.
- S5_B_cell: Rule if testable: Sera bind the antigen but not the predicted epitopes. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S5_B_cell: No B-cell epitopes (B_linear / B_conformational) in the upstream S5 output for this candidate.
- S6: Rule if testable: Response absent in backgrounds predicted to be covered. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S6: No S6 population-coverage data (pc90 / coverage_by_population) for this candidate.
- S7: Rule if testable: Sera recognise only the denatured form - the conformational epitope was an artifact. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S7: No S7 structure model for this candidate: S7 is top-k only, or its structure step did not run (see the artifact warnings).
- functional: Rule if testable: Titres rise but functional activity is absent. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- functional: No correlate of protection is known or agreed for this pathogen, so no functional assay can be named as the test of protection and titres are not protection. An investigator may add a mechanism-based functional assay (e.g. neutralisation or opsonophagocytosis) as an exploratory readout; that is not a correlate.

### tr|Q9JYA8|Q9JYA8_NEIMB - Hemoglobin receptor (hmbR)

| Stage | Upstream prediction (from data) | Animal readout | Falsified if | Status |
|---|---|---|---|---|
| S3 | S3 scores Hemoglobin receptor (hmbR) (protegenicity 0.994046); S7's final order, which also weighs T-cell, B-cell, coverage and structure terms, puts it at position 3, i.e. predicts it is immunogenic. | Antigen-specific serum IgG ELISA titre (endpoint titre, log10) at the primary timepoint. | No titre rise over the adjuvant-only arm. | `testable` |
| S5_T_cell | - | IFN-gamma ELISpot on splenocytes stimulated with (a) the predicted class I pool(s), (b) the predicted class II pool(s), (c) whole recombinant antigen. | - | `not_testable_no_upstream_prediction` |
| S5_B_cell | - | Competition ELISA (immune sera pre-incubated with the predicted epitope peptide vs an irrelevant peptide) or peptide-array mapping (overlapping peptides spanning the predicted regions). | - | `not_testable_no_upstream_prediction` |
| S6 | - | IFN-gamma ELISpot to the predicted T-cell pools in a panel of HLA-transgenic lines, one line per restricting allele predicted to cover the populations of interest. | - | `not_testable_no_upstream_prediction` |
| S7 | - | ELISA binding of immune sera to the folded (native) vs denatured (heat or chemically denatured) antigen. | - | `not_testable_no_upstream_prediction` |
| functional | - | Species-appropriate functional assay on immune sera. | - | `not_testable_no_upstream_prediction` |

- S3: Rule if testable: No titre rise over the adjuvant-only arm.
- S5_T_cell: Rule if testable: Response to whole antigen but NOT to the predicted pools - antigen right, epitope prediction wrong. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S5_T_cell: No T-cell epitopes (T_class_I / T_class_II) in the upstream S5 output for this candidate.
- S5_B_cell: Rule if testable: Sera bind the antigen but not the predicted epitopes. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S5_B_cell: No B-cell epitopes (B_linear / B_conformational) in the upstream S5 output for this candidate.
- S6: Rule if testable: Response absent in backgrounds predicted to be covered. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S6: No S6 population-coverage data (pc90 / coverage_by_population) for this candidate.
- S7: Rule if testable: Sera recognise only the denatured form - the conformational epitope was an artifact. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- S7: No S7 structure model for this candidate: S7 is top-k only, or its structure step did not run (see the artifact warnings).
- functional: Rule if testable: Titres rise but functional activity is absent. This row is `not_testable_no_upstream_prediction`: it is NOT tested. The rule is what would apply if it were testable, not a claim that it was tested.
- functional: No correlate of protection is known or agreed for this pathogen, so no functional assay can be named as the test of protection and titres are not protection. An investigator may add a mechanism-based functional assay (e.g. neutralisation or opsonophagocytosis) as an exploratory readout; that is not a correlate.

## 12. Ethics and regulatory checklist

| Framework | Item | Status | Where |
|---|---|---|---|
| ARRIVE 2.0 Essential 10 | Study design | `addressed` | arms; randomisation.unit_of_randomisation; primary_endpoint |
| ARRIVE 2.0 Essential 10 | Sample size | `addressed` | power_analysis |
| ARRIVE 2.0 Essential 10 | Inclusion and exclusion criteria | `addressed` | analysis_plan.exclusion_rules; species_selection (sex, age) |
| ARRIVE 2.0 Essential 10 | Randomisation | `addressed` | randomisation |
| ARRIVE 2.0 Essential 10 | Blinding | `addressed` | blinding |
| ARRIVE 2.0 Essential 10 | Outcome measures | `addressed` | primary_endpoint; secondary_endpoints |
| ARRIVE 2.0 Essential 10 | Statistical methods | `addressed` | analysis_plan |
| ARRIVE 2.0 Essential 10 | Experimental animals | `needs_human_input` | species_selection (species/strain/sex/age set); supplier field |
| ARRIVE 2.0 Essential 10 | Experimental procedures | `needs_human_input` | schedule; dosing; route |
| ARRIVE 2.0 Essential 10 | Results | `needs_human_input` | post-study reporting |
| 3Rs | Replacement | `addressed` | species_selection.rationale; honest_limits |
| 3Rs | Reduction | `addressed` | power_analysis; arms |
| 3Rs | Refinement | `addressed` | humane_endpoints; schedule; blinding |
| 3Rs | Documented search for non-animal alternatives and prior data (incl. pilot data for the effect size) | `needs_human_input` | applicant |
| 3Rs | Housing, enrichment and anaesthesia/analgesia for bleeds and dosing | `needs_human_input` | facility |
| UK ASPA 1986 | Project licence (PPL): number, holder, authorisation covering these procedures and severity | `needs_human_input` | applicant |
| UK ASPA 1986 | Personal licence (PIL): named holders for immunisation and blood sampling | `needs_human_input` | applicant |
| UK ASPA 1986 | AWERB review: reference and date | `needs_human_input` | applicant / institution |
| UK ASPA 1986 | Establishment licence (facility) | `needs_human_input` | institution |
| UK ASPA 1986 | Named persons (NACWO, NVS) | `needs_human_input` | institution |
| UK ASPA 1986 | Harm-benefit assessment | `needs_human_input` | applicant |
| US IACUC | IACUC protocol approval: number and date | `needs_human_input` | applicant / institution |
| US IACUC | Attending veterinarian consultation (pain/distress category) | `needs_human_input` | institution |
| US IACUC | Personnel training and occupational-health enrolment | `needs_human_input` | institution |
| Biosafety / containment | Containment level and biosafety committee review (mandatory for any challenge phase) | `needs_human_input` | challenge_model.scope_note; facility |
| Study-specific checks | Strain / HLA-transgenic line availability and allele match | `addressed` | species_selection |
| Study-specific checks | Licensed-vaccine positive control: existence checked | `needs_human_input` | positive_control |
| Study-specific checks | Challenge model existence verified | `needs_human_input` | challenge_model |

17 item(s) need human input (licence numbers, facility, named people are never invented).

## 13. Honest limits

- Mouse immunogenicity does not predict human protection. It shows an antigen is immunogenic in a mouse: necessary, not sufficient. (README S10)
- Industry base rate for vaccines: Phase 1 to approval 39.6% (6.8% for non-industry sponsors). The narrowest transition is Phase 2 to 3 at 65.4% - precisely the step where immunogenicity data exists and efficacy data does not. No amount of preclinical work closes that gap. (README S10)
- A positive animal result moves a candidate from 'predicted' to 'immunogenic in one species'. It does not move it to 'works'. (README S10)
- This proposal reports no results of any kind and does not simulate assay readouts or clinical trials (README section 0).
- Base rates: Phase 1 to approval 39.6% (industry) / 6.8% (non-industry); Phase 2 to 3 65.4%.
- Evidence ladder: predicted -> immunogenic in one species. A positive result does not move a candidate to 'works'.
- Source: README.md, section S10 'Honest limits' (README-sourced; not independently verified by this module)
