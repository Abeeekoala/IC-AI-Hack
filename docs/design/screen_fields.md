# Screen fields to artifact paths

Companion to `docs/design/vaxforge_product_design.html` (one canvas, 15 screens plus an overview and a foundations frame). For each screen: the field it shows, the artifact path that supplies it, and whether that path exists on `main` today. Stream C (S8, S9, S9+, S10) should read sections 06, 07 and 08 and answer the "Check" lines.

Paths are relative to `runs/<run_id>/`. `payload.x[]` means a list. Artifact files are named after the stage module (`s8_protocol.json`, `s9_robot.json`, `s9plus_animate.json`, `s9_5_finetune.json`, `s10_animal.json`). Sidecar files live under `data/` and are referenced by a run ref (`{root, path, sha256, bytes}`).

**Status column**

| Mark | Meaning |
|---|---|
| on main | The stage code on `main` writes this field. I read the code, and for S0 to S2 and S8 to S10 I ran it on `fixtures/nmb_fixture`. |
| contract | Stated in `docs/CONTRACT_S3_S7.md` (stream B). `main` has the S3 to S7 code, but no committed artifact exists, so I did not see the field written. Stream B: please confirm. |
| not on main | The screen needs this and no stage writes it yet. |
| sidecar | Not in the JSON payload; only in a markdown or code sidecar. |

Every screen also reads the envelope of each stage it shows: `status`, `warnings[]`, `digest`, `producer.tools{}` (the "source" text next to every value) and the run `manifest.json`.

## 00 Overview and key

| Field | Path | Status |
|---|---|---|
| Screen index, tags, state vocabulary | fixed text | on main (`src/vaxforge/report.py` `state()`) |

## 01 Run launcher (S0, S1)

| Field | Path | Status |
|---|---|---|
| Pathogen as typed | `s0_taxonomy.json payload.query` | on main |
| Resolution message, status, reason, method | `payload.resolution.{message,status,reason,method,matched_name,matched_name_class,synonym_matched,user_confirmed,notes}` | on main |
| Resolved taxon, taxid, rank, lineage | `payload.taxon.{scientific_name,taxid,rank,species_taxid,lineage[]}` | on main |
| Domain, Gram stain and its basis | `payload.organism.{domain,gram_stain,gram_basis}` | on main |
| Candidate list for the ambiguous state | `payload.candidates[]` | on main |
| Proteome name, id, count, release | `s1_acquire.json payload.proteome.{name,upid,n_sequences,declared_protein_count,release}` | on main |
| Top-k for S7 | `s7_structure.json payload.top_k`, else `config.resolved.json pipeline.top_k` | on main (S7: contract) |
| Settings card (thresholds, B-cell tool, class II weight, engine, seed) | `config.resolved.json thresholds, epitopes, structure, reproducibility` | on main |
| Offline mode | `config.resolved.json network.offline` | on main |
| Command preview | built from `payload.query` and `pipeline.top_k`; README section 5 | on main (derived) |

## 01b Data and annotation (S1, S2)

| Field | Path | Status |
|---|---|---|
| Resource table (proteome, comparison proteome, VFDB, Protegen, human proteome, IEDB, AFND) | `s1_acquire.json payload.proteome, comparison_proteome, reference_dbs.{vfdb,protegen,human_proteome,iedb,afnd}`, `payload.resource_status` | on main |
| Protegen `target_matches`, version, licence note | `payload.reference_dbs.protegen.{version,n_sequences,target_matches,redistribution}` | on main |
| Summary tiles | `s2_annotate.json payload.summary.{n_proteins,virulence.*,localisation.*}` | on main |
| Annotation table row | `payload.proteins[]`: `protein_id, gene, description, length, virulence.{status,evidence,best_hit.{gene,identity_pct,query_coverage_pct,evalue}}, localisation.{label,status,accessibility}` | on main |
| "Stand-in, not PSORTb" banner | `payload.tools.localisation.{status,tool,note}`, `localisation.status == fixture_stand_in`, `producer.tools.psortb == not_run` | on main |
| VFDB thresholds (assumed defaults) | `payload.thresholds.{max_evalue,min_identity,min_query_coverage}` | on main |
| Accessibility mapping | `config.resolved.json annotate.accessibility` | on main |

## 02 Pipeline overview

| Field | Path | Status |
|---|---|---|
| One card per stage: state | derived as in `report.py Run.state()` from `status` and `payload.status` or `payload.fine_tune.status` or `payload.decision` | on main |
| Card reason | `payload.resolution.message`, `payload.fine_tune.reasons`, `payload.reason`, else `warnings[0]` | on main |
| Digest, warning count | `manifest.json stages.<stage>.{digest,warnings}` | on main |
| Lane grouping A, B, C, D | `AGENTS.md` section 4 | fixed |

## 03 Ranked candidates (S3)

| Field | Path | Status |
|---|---|---|
| Rank, composite score | `s3_rank.json payload.candidates[].{rank,composite_score}` and `payload.ranking[]` | contract |
| Protegenicity, pass at 0.90 and 0.85 | `payload.candidates[].{protegenicity,passes_thresholds}` | contract |
| Ranker backend, score meaning, thresholds | `payload.ranker.{backend,score_semantics,thresholds}` | contract |
| Leakage audit state | `payload.leakage_audit.{holdout_enforced,model,declared_holdout_taxids,note}` | contract |
| Proteins scored and selected, fold enrichment | `payload.counts.{n_scored,n_selected,fold_enrichment}` | contract |
| S2 columns (name, length, localisation, virulence) | `s2_annotate.json payload.proteins[]` joined on `protein_id` | on main |
| S4 notes column | `s4_filter.json payload.candidates[].flags` | contract |
| "Known accuracy" card | fixed text, README section S3 | fixed |

## 03b Safety and conservancy (S4)

| Field | Path | Status |
|---|---|---|
| Gate status and thresholds | `s4_filter.json payload.gates.<gate>.{status,thresholds,reason}` | contract |
| Funnel | `payload.funnel[]`, `payload.counts.{n_input,n_survivors,n_rejected,n_rescued}` | contract |
| Incomplete screening banner | `payload.incomplete_screening`, `payload.unavailable_gates[]` | contract |
| Rejected list with reason and recoverable | `payload.rejected[].{protein_id,name,rejected_by[],recoverable,gates}` | contract |
| Rescued | `payload.candidates[].rescued_despite` | contract |
| Real-run example (fHbp, NHBA, NadA rejected) | `docs/CONTRACT_S3_S7.md` section 4, not an artifact | doc only |

## 04 Candidate detail (S2 to S7)

| Field | Path | Status |
|---|---|---|
| Evidence rows, S2 | as `report.py evidence_rows()`: `s2_annotate.json payload.proteins[].{localisation,virulence}` | on main |
| Evidence rows, S3 to S7 | `s3.candidates[].{rank,protegenicity,composite_score,passes_thresholds}`, `s4.candidates[].{gates,flags,rescued_despite}`, `s5.candidates[].{tcell.score,tcell.class_i.n_epitopes,tcell.class_i.n_promiscuous,tcell.class_ii.n_epitopes,bcell.linear_score,bcell.conformational.status}`, `s6.candidates[].coverage.{score,score_population,mode_used,min_population}`, `s7.candidates[].{scores.final,rank_basis,structure.status,structure.mean_plddt,structure.validator_agreement.value}` | contract |
| "Source" text | `producer.tools{}` of each stage | on main |
| "Known accuracy" column | fixed text, README sections S3, S5, S6, S7, 4 | fixed |
| Per-candidate uncertainty display | roadmap item in README; no stage computes an uncertainty | not on main |

## 04b Epitopes and coverage (S5, S6)

| Field | Path | Status |
|---|---|---|
| Analysis status per predictor | `s5_epitopes.json payload.analysis_status.{class_i,class_ii,bcell_linear,conformational}.{status,tool,fidelity,reason}` | contract |
| Selected T-cell epitopes | `payload.epitope_set.t_cell[].{epitope_id,protein_id,class,peptide,core,start,end,alleles[],best_rank}` | contract |
| Linear B-cell regions | `payload.candidates[].bcell.linear.regions[].{start,end,peptide,mean_score}` | contract |
| Coverage by population | `s6_coverage.json payload.set_level.by_population.<pop>.<mode>.{coverage,average_hits,pc90,sample_size}` | contract |
| Fairness rows | `payload.set_level.fairness.rows[].{population,coverage_with_reference_optimised_set,coverage_with_own_optimised_set,coverage_lost,sample_size}` | contract |
| Sparse-data populations | `payload.set_level.sparse_data_populations` | contract |
| Unmapped alleles | `payload.set_level.by_population.<pop>.<mode>.unmapped_alleles` | contract |
| Findings text (k=1 gap, Central America) | `docs/CONTRACT_S3_S7.md` section 6 | doc only |

## 04c Structure (S7)

| Field | Path | Status |
|---|---|---|
| Final order | `s7_structure.json payload.final_candidates[]` | contract |
| Rank, final score, rank basis | `payload.candidates[].{rank,rank_basis,scores.final,scores.pre_structure,scores.terms_missing}` | contract |
| Structure status, pLDDT, validators | `payload.candidates[].structure.{status,engine,mean_plddt,validators,validator_agreement}` | contract |
| Degraded to sequence only | `payload.analysis.degraded_to_sequence_only` | contract |
| Affinity and docking not implemented | `payload.analysis.affinity`, `payload.analysis.docking` | contract |
| Final weights | `config s7.final_weights`, `payload.candidates[].scores.weights` | contract |

## 05 Validation

| Field | Path | Status |
|---|---|---|
| Bexsero antigen rows, match status, rank, tie range | `validation/bexsero_case.py` report: `antigens[].{antigen,status,matches[].{rank,rank_best_case,rank_worst_case},rank}` | on main (script output; **no fixed file name**) |
| recall@k and tie bounds | report `recall_at_k.{value,reason,if_ties_go_best_case,if_ties_go_worst_case,n_hit,n_truth}` | on main (script output) |
| Leakage assertion state | report `leakage_assertion.status` (not checked, ok, violated) | on main (script output) |
| S3 leakage audit pass-through | report `s3.leakage_audit`; `s3_rank.json payload.leakage_audit` | on main / contract |
| LOPOV metrics | `validation/lopov.py lopov_report()` output | on main (script output; no fixed file name) |
| Where these files are found | `runs/<run_id>/validation/` by S11 convention; `report.py` lists the files and does not read them | **not on main**: no schema or file names in the artifact contract. Stream D: fix names so the screen can read them. |
| Tier 3 funnel (570, 350, 85, 7, 3) | fixed text, README section 3 | fixed |
| Leakage check of the S9.5 manifest | `validation/leakage_check.py` output (exit 2 on violations) | on main (script output) |

## 06 Wet-lab protocol (S8)

Candidate entries are at `s8_protocol.json payload.candidates[]`. When S3 to S7 are absent, candidates are an S2 user selection (`rank_source == user_selection_order`) and the first warning says so.

| Field | Path | Status |
|---|---|---|
| Candidate id, name, rank, rank source, length, localisation | `payload.candidates[].{candidate_id,protein_name,rank,rank_source,length_aa,localisation}` | on main |
| Selection banner | `payload.selection.{source,reason}`, `warnings[0]` | on main |
| Step id, kind, title | `payload.candidates[].protocol.steps[].{step_id,kind,title}` | on main |
| Owner chip (ROBOT, ROBOT-PARTIAL, HUMAN) | `steps[].automation` (`full`, `partial`, `manual`) plus `steps[].automation_note` | on main (the UI maps `full` to ROBOT, `partial` to ROBOT-PARTIAL, `manual` to HUMAN, as `s9_robot` does in `coverage[].owner`) |
| Depends on | `steps[].depends_on[]` | on main |
| Gate text | `steps[].gate` | on main |
| Controls | `steps[].controls[]` | on main |
| Duration range (days, estimate) | `steps[].est_duration_days` | on main |
| Falsification condition | `protocol.falsifier` | on main |
| Does not measure | `protocol.does_not_measure[]` | on main |
| Endotoxin limit state | `protocol.config.endotoxin_limit_eu_per_ug` (null shows "NOT YET SET"); gate text on step `s09` | on main |
| Readouts | `protocol.config.readouts[]` | on main |
| Estimate basis note | `protocol.estimate_basis` | on main |
| Cost range | `protocol.cost_gbp_range` (always null today) | on main as null; **value not on main** (in progress) |
| Reagent quantities | `steps[].reagents[]` holds names only; volumes are set in S9 | **not on main** (in progress) |
| Order FASTA for gene synthesis | none. S12 reads a user-supplied FASTA (`s12_biosafety.py read_order`) | **not on main** (in progress) |
| Protocol markdown | `candidates/<id>/protocol.md` under the stand-alone tree; not referenced from the artifact payload | sidecar |

Check (stream C): when the order FASTA lands, which key holds it, and does each record's id match what S12's `read_order` expects? When quantities land, is it `steps[].reagents[]` as objects (name, quantity, unit) or a new key? The screen has three dashed "not on main" boxes ready for these.

## 07 Robot code (S9, S9+)

Entries are at `s9_robot.json payload.candidates[].backends[].sessions[]`.

| Field | Path | Status |
|---|---|---|
| Placeholder banner | `payload.parameters_status` | on main |
| Biosecurity banner | `payload.biosecurity` | on main |
| Simulator and version | `payload.simulator.{simulated,opentrons_version}`, `producer.tools.opentrons` | on main |
| Totals and outcome counts | `payload.summary.{n_sessions,outcomes,all_pass}` | on main |
| Session id, title, covers | `sessions[].{session_id,title,covers[]}` | on main |
| Outcome chip | `sessions[].outcome` (PASS, FAIL, REFUSED, UNVERIFIED, ERROR) | on main |
| Tips and pauses | `sessions[].{tips_needed{},tips_total,n_human_pauses}` | on main |
| Grade: rule counts, lint, findings, sim error | `sessions[].grade.{verdict,sim_ok,sim_error,lint[],rule_counts{},n_rule_violations,findings[]}` | on main |
| Refusal record | `sessions[].refusal.{kind,detail}` | on main |
| Code file (excerpt) | `sessions[].files.code` (run ref to the `.py`) | on main |
| Operator sheet (deck table, load list) | `sessions[].files.setup` (run ref to `.setup.md`) and `files.plan` (`.plan.json`) | sidecar. The screen shows the deck rows parsed from markdown. **Check (stream C): which keys of `.plan.json` are stable, so the deck, pipettes and liquids can be read from JSON?** |
| Step ownership table | `payload.candidates[].coverage[].{step_id,kind,title,owner,sessions[],human_remainder}` | on main |
| Artifact-level state | `payload.status` (`not_run`, `refused`) and `payload.reasons[]` | on main |
| S9+ state and animation | `s9plus_animate.json payload.sessions[].{ok,timeline,html,skipped}`, `payload.summary`, `payload.status`, `payload.reasons[]` | on main |
| Animation time note | `warnings[]` ("times are estimates") | on main |
| S9.5 card | `s9_5_finetune.json payload.fine_tune.{status,reasons[]}`, `payload.base_model_id`, `payload.model_id` | on main |

## 07b S9.5 evidence panel

| Field | Path | Status |
|---|---|---|
| Stage state and reasons | `s9_5_finetune.json payload.fine_tune.{status,reasons[]}` | on main |
| Base model id, trained model id | `payload.base_model_id`, `payload.model_id` | on main (both null today) |
| Win, loss, tie rates, intervals, n, verdict | `validation/winrate.py score()`: `rates, counts, intervals, n, families, tie_breakdown, decided_by, verdict, win_rate_reportable, withheld` | on main (script output; **not written into `s9_5_finetune.json`**) |
| Refusal rates, base vs fine-tune, and red flag | `score()` `refusal.{heldout,probe}`, `refusal_comparison`, `red_flag_splits` | on main (script output) |
| Training data provenance table | README section S9.5 text; the row-level `origin` and `assigned_by` are in the training manifest from `vaxforge finetune manifest` | README text only. **Not on main as an artifact field.** |
| Size check (real pairs, synthetic rows, minimum) | README section S9.5; counts are not in any artifact | **not on main** |
| Held-out families | `validation/heldout_families.json families{}` | on main |
| Leakage check result | `validation/leakage_check.py` exit status and violation list | on main (script output) |
| Evidence for the track | `adaption_evidence.json` from `s9_5_adaption.record_evidence` | on main (code), no file exists yet |
| "Could not be verified" list | README section S9.5 callout | README text only |

Check (stream D): decide where `winrate` and the manifest counts are written so this panel reads artifacts and not README prose. Today the numbers on this screen are the README's, not computed.

## 08 Animal study (S10)

The stage writes the whole design to `s10_animal.json payload.design`; the table below names fields inside it.

| Field | Path | Status |
|---|---|---|
| Selection banner | `payload.selection`, `warnings[]` (the "S3-S7 not available" line) | on main |
| Candidates included | `payload.candidates_included[]` | on main |
| Species, strain status, sex, age, HLA match | `design.species_selection.{species,strain,strain_status,sex,age_weeks_range,human_mhc_predictions_under_test,hla_alleles_to_match}` | on main |
| Challenge model and correlate statements | `design.challenge_model.{status,statement}`, `design.correlate.{correlate,statement}` | on main |
| Adjuvant, route, doses | `design.adjuvant`, `design.route`, `design.dosing.{dose_levels_ug,levels_status,primary_dose_ug}` | on main |
| Schedule | `design.schedule.{immunisation_days,bleed_days,primary_timepoint_day,terminal_day}` | on main |
| Power analysis and sensitivity | `design.power_analysis.{effect_size_d,effect_size_status,alpha_familywise,n_comparisons,multiplicity_method,target_power,achieved_power,n_per_group_allocated,n_arms,total_animals,sensitivity[]}` | on main |
| Arms | `design.arms[].{arm_id,role,label,dose_ug,n_per_group,primary_comparison}` | on main |
| Positive control | `design.positive_control.{status,note}` | on main |
| Falsification map | `design.falsification_map[].{candidate_id,upstream_stage,animal_readout,falsified_if,status,peptide_pools[],controls[]}` and `payload.falsification_rows.by_status` | on main |
| Randomisation, blinding, endpoints, humane endpoints, analysis plan | `design.{randomisation,blinding[],primary_endpoint,secondary_endpoints[],humane_endpoints,analysis_plan}` | on main |
| Ethics checklist counts | `design.ethics_checklist[].{framework,item,status}`, `payload.outstanding_human_inputs` | on main |
| Honest limits | `design.honest_limits.{statements[],evidence_ladder}` | on main |
| Design warnings | `design.warnings[]` | on main |
| README "falsified if" column | fixed text, README section S10; shown beside the row because `falsified_if` is empty when a row is not testable | fixed |
| Study markdown | `payload.study_file` (run ref) | sidecar |

Check (stream C): `falsified_if` is empty for `not_testable_no_upstream_prediction` rows, so the screen shows the README rule beside it. If you prefer the rule inside the artifact, add it as `rule_if_testable`.

## 09 Pre-registration and gate (S11, S12)

| Field | Path | Status |
|---|---|---|
| Registered at, frozen digest, note | `s11_prereg.json payload.frozen.registered_at`, `payload.frozen_digest`, `payload.note` | on main |
| Frozen settings | `payload.frozen.config.{reproducibility.seed,reproducibility.pin_versions,finetune.seed,pipeline.top_k,thresholds,epitopes,structure}`, `payload.frozen.config_digest` | on main |
| Upstream frozen | `payload.frozen.upstream.<stage>.{status,digest,tools}` | on main |
| Frozen predictions | `payload.frozen.predictions.{s3,s4}` | on main |
| Refusal to write after results exist | stage returns `failed` with the reason; `validation_outputs()` | on main |
| Verify result | `vaxforge prereg verify RUN_DIR` output | on main (CLI output; not an artifact) |
| S12 state and reasons | `s12_biosafety.json payload.{state,reasons[]}` (cleared, not_screened, blocked) | on main |
| Order list and provider record | `payload.order.{status,file,n_sequences}`, `payload.provider_record.{status,provider,tool,tool_version,date,covers_every_ordered_sequence,authenticity}` | on main |
| Per-sequence local checks and verdict | `payload.sequences[].{id,digest,length,local_checks.{selected_candidate,sequence_sanity,virulence_flag,toxin_flag},provider_verdict}` | on main |
| Limits | `payload.limits[]` | on main |
| Exit codes (cleared 0, blocked 2, not_screened 3) | `s12_biosafety.py EXIT` | on main |

## 10 Limits and absent

| Field | Path | Status |
|---|---|---|
| Honest ceilings and deliberately absent | fixed text; `report.py CEILINGS` and `ABSENT` (README sections 4 and 7) | on main |

## C Foundations

No data. Palette from `report.py` CSS, accent teal as in the earlier Figma file.

---

## Fields the design needs that are not on main yet

| Screen | Field | Owner | Note |
|---|---|---|---|
| 06 | Order FASTA | C (S8) | In progress. Feeds S12's `--order` file. |
| 06 | Reagent quantities | C (S8) | In progress. Today `reagents[]` is names only. |
| 06 | Cost | C (S8) | In progress. `cost_gbp_range` is null. |
| 07 | Deck, pipettes, liquids as JSON | C (S9) | Today only in `.setup.md`; `.plan.json` keys not documented. |
| 05 | Names and schema of the files under `validation/` | D | None fixed. |
| 07b | Win-rate report and manifest counts as artifact fields | D | Today script output and README prose. |
| 04 | Per-candidate uncertainty | B or D | Roadmap item only. |

S3 to S7 rows are "contract" because no committed artifact exists. They are not missing, but I could not check them against a written file.
