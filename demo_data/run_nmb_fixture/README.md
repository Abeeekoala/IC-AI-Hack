# run_nmb_fixture: the offline demo run for Neisseria meningitidis

A complete run folder (S0 to S12, 15 artifacts, all `status: ok`) plus `report.html`. For the UI builder: read section 4 before you show any number, because `status: ok` also appears on degraded stages (S4 to S7).

## 1. How it was made

- `python -m vaxforge demo-offline --out <dir> --offline`, then `python -m vaxforge.report <dir>`.
- Code: main `2221800`. Date: 2026-11-15. Machine: Windows; Python 3.14 (S3 to S12 artifacts record 3.14.6); Opentrons 7.5.0 simulator in a separate venv.
- S0 to S2 are older: their artifacts have `created_at` 2026-11-14 and record Python 3.15.0, so, as far as the timestamps show, they were written earlier (the 40-protein projection in `FIXTURE.md`) and S3 onward ran on 2026-11-15.
- `verify` said OK for the run folder. The generation folder was `C:\vf_demo\...`. Absolute paths of that folder remain in `config.resolved.json` (`finetune.evidence`, `finetune.manifest`, `s3.scores_file`), `s12_biosafety.json` (`volatile.order_path`), `s3_rank.json` (`params.scores_file`), `s6_coverage.json` (one warning text) and `report.html`. They name no real location here. A UI must use the run-relative file references `{root, path, sha256, bytes}` (for example `data/s8/order.fasta`), never these strings.

## 2. Byte exactness: do not edit anything here

- Four files have CRLF line ends (`FIXTURE.md`, `report.html`, `data/s9_5/adaption_evidence.json`, `data/s9_5/manifest.json`); all others have LF.
- `.gitattributes` (`* -text`) stops git from converting line ends, so the committed bytes equal the digests recorded in the artifacts. Keep that file.
- Check: `python -m vaxforge verify demo_data/run_nmb_fixture` should print `OK - digests, upstream chain and referenced files are consistent`. I ran it on this working copy and it printed that; a clean checkout and other operating systems were not tested here.
- Any edit, even whitespace, breaks `verify`. `data/s5/` is an empty folder (git does not keep it).

## 3. File map

| Stage | Artifact | Holds | `data/` files it references |
|---|---|---|---|
| S0 | `s0_taxonomy.json` | organism, taxon, Gram-negative call | none |
| S1 | `s1_acquire.json` | proteome record (40 of 2003 proteins), resource status | `data/proteome.faa` |
| S2 | `s2_annotate.json` | 40 proteins: localisation (stand-in), VFDB virulence hits | `data/s2_annotations.tsv` |
| S3 | `s3_rank.json` | `candidates` (30) and `ranking` (all 40), ties, leakage audit | none |
| S4 | `s4_filter.json` | gate results: no gate ran, 30 in, 30 out | none |
| S5 | `s5_epitopes.json` | epitope analyses: none ran, no epitopes | none |
| S6 | `s6_coverage.json` | population coverage: did not run | none |
| S7 | `s7_structure.json` | 30 candidates, `final_candidates` (20 ids in final order), sequence-only | none |
| S8 | `s8_protocol.json` | 3 protocols x 13 steps, falsifier, cost (`not_estimated`) | `data/s8/order.fasta` (3 records) |
| S9 | `s9_robot.json` | per candidate 12 sessions with outcome and file references | per session in `data/s9/<candidate>/template/`: `*.py`, `.setup.md`, `.plan.json`, `.sim.json`, `.grade.json`; per candidate `robot_report.json`, `robot_report.md`, `workflow.md` |
| S9+ | `s9plus_animate.json` | 36 animations (0 skipped) | per session in `data/s9plus/<candidate>/template/<session>/`: `timeline.json`, `protocol_animation.html` |
| S9.5 | `s9_5_finetune.json` | attached Adaption evidence | `data/s9_5/adaption_evidence.json`, `data/s9_5/manifest.json` |
| S10 | `s10_animal.json` | study design, 7 arms, power, falsification map | `data/s10/animal_study.md` |
| S11 | `s11_prereg.json` | frozen predictions and digest | none |
| S12 | `s12_biosafety.json` | gate state and reasons | none (reads `data/s8/order.fasta`) |

Also: `manifest.json` (stage index and digests), `config.resolved.json`, `report.html`, `FIXTURE.md`. `<candidate>` is the lower-case id: `tr_q9k0b4_q9k0b4_neimb` (rank 1, iga), `tr_q9jxz0_q9jxz0_neimb` (rank 2, NMB1829), `tr_q9jya8_q9jya8_neimb` (rank 3, hmbR). Screen-to-path map: `docs/design/screen_fields.md`.

## 4. What is real, what is a stand-in, what is degraded

- S0 to S2: fixture projection, 40 of 2003 proteins. Taxonomy, UniProt sequences and BLAST+ virulence hits are real. Localisation is a stand-in from UniProt text (`status: fixture_stand_in`, all 40), not PSORTb. Protegen data omitted. See `FIXTURE.md`.
- S3: real Vaxign-ML scores read from stream B's scores file (stock model trained on all of Protegen; `holdout_enforced` false). Leakage risk: rediscovering known antigens is not validation. Rank 30 is `rank_padding` (below 0.85). One tie group: 4 candidates at rank 18; inside it the order is by protein_id only.
- S4: degraded. None of the four gates ran: human_homology and conservancy (BLAST+ not available), allergenicity (algpred2 not found), toxicity (toxinpred2 not found). All 30 candidates passed on by default, so a survivor is not a screened candidate.
- S5: degraded. Class I (no netMHCpan, no mhcflurry), class II (no netMHCIIpan), linear B-cell (BepiPred-3.0 not configured) and conformational B-cell (DiscoTope-3.0 not configured) all unavailable; the epitope lists are empty.
- S6: degraded. No coverage, because S5 gave no epitopes (`set_level.n_epitopes` 0).
- S7: degraded. Boltz-2 not on PATH; ESMFold and AF2-no-MSA unavailable. Ranking is sequence-only: the only score term used is the S3 one (`terms_used: ["s3"]`), so the final order of the 20 equals the S3 order. 3 candidates tie at rank 18.
- S8: a design on placeholder numbers (volumes, times, quantities from defaults). Cost is `not_estimated`: no prices were supplied. One template, not candidate-specific (see `docs/streamc.md` section 7).
- S9: PASS means static lint, Opentrons 7.5.0 simulation, five physical rules and the plan check passed. It does not mean the experiment works. Parameters are placeholders. Code is from the `template` backend, not from a fine-tuned model. S9 says the S12 gate is not evidenced to it.
- S9+: animation times come from a fixed time model, not measurements.
- S9.5: shows an attached real Adaption run (`status: evidence_attached`). `best_win_rate` is Adaption's own number on its own evaluations; the file's caveat says it is not evidence of protocol quality. `held_out_win_rate` is null: no held-out result of ours is in this run.
- S10: design only; no results. Effect size (0.7 log10, SD 0.5) is an assumption: "replace with pilot data". Doses are placeholders. No challenge model, so immunogenicity only; positive-control arm is "check". 15 of 18 falsification rows are `not_testable_no_upstream_prediction`.
- S11: pre-registration: frozen config digest, S3 ranking (40 entries), S4 predictions (5 entries) and upstream digests, under one `frozen_digest`. `registered_at` is this machine's clock; it proves a date only once the digest is published elsewhere (the artifact's own note).
- S12: `blocked`. Reasons: S4 recorded no toxicity result for any of the 3 candidates, and no provider screening record was supplied. Listed for human review (not blocking): iga (`Q9K0B4`) and hmbR (`Q9JYA8`), both virulence-associated with strong S2 evidence. This is a status, not a screening result.

## 5. Where the UI numbers come from

| Value | Where |
|---|---|
| Stage states, warnings per stage | `manifest.json stages.*`; each artifact's `warnings` and `payload.degraded` |
| Candidate ids, ranks, tie groups (S3) | `s3_rank.json payload.candidates[]` (`rank`, `protegenicity`, `tie_group`), `payload.ties` |
| Final order (20 ids), S7 tie | `s7_structure.json payload.final_candidates`, `payload.ties.top_k` |
| The 3 candidates used by S8 to S10 | `s8_protocol.json payload.selection.used` |
| S8 step count, falsification condition | `payload.narratives.n_steps` (39); `payload.candidates[].protocol.steps` (13 each), `.protocol.falsifier`; cost `payload.cost.status` |
| S9 outcomes | `s9_robot.json payload.summary` (36 sessions, `PASS` 36, `all_pass` true); `payload.candidates[].backends[].sessions[]` |
| Animations | `s9plus_animate.json payload.summary`, `payload.sessions[]` (`html`, `timeline`) |
| S9.5 evidence | `s9_5_finetune.json payload.fine_tune.status`, `.adaption.best_win_rate` (0.9439), `.adaption.caveat`, `.held_out_win_rate` |
| S10 power, arms, animals | `s10_animal.json payload.design.power_analysis` (`total_animals` 91, `n_per_group_allocated` 13, `n_arms` 7, `achieved_power`), `payload.design.arms[]`, `payload.falsification_rows.by_status` |
| S11 digest | `s11_prereg.json payload.frozen_digest` |
| S12 state | `s12_biosafety.json payload.state` (`blocked`), `payload.reasons`, `payload.review` |

## 6. Not verified, limits

- Generated on one Windows machine; a re-run on another machine was not tried here.
- No UI or report was built from this folder yet. `report.html` was only rendered by `python -m vaxforge.report`.
- PSORTb, Boltz and the other real tools for S4 to S7 were not part of this run (see stream B's folder when it exists).
- The animation HTML files were not played in a browser by us.
- Nothing here was tested in a lab. These are candidates and designs, not a vaccine.
