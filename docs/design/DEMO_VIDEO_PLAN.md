# Demo video plan: 2:00

Stream D. A shot list for a two-minute video that has to do three jobs at once: present the science (the AI for Science track), show evidence of how Figma was used (Best-Designed AI Product) and show evidence of how Adaption was used (Best Use of Adaption). Each shot carries tags: **[Sci]**, **[Figma]**, **[Adaption]**.

Status of this plan: a draft. Anything in `[FILL]` is a number that does not exist yet. Do not say it, show it or round it up until it does (see "Do not claim").

## The three prizes in one line each (from the organisers' text we were given)

- **Best Use of Adaption:** fine-tune a model with Adaption, ranked on Adaption's leaderboard by win rate against the base model; in the presentation, explain why we qualify and show evidence of how Adaption was used.
- **Best-Designed AI Product (Figma):** an AI product as well designed as it is smart, designed in Figma or built with Figma Make; in the demo, explain why we qualify and show evidence of how Figma was used.
- **AI for Science (main track, sponsors Boltz and Tamarind Bio; £700 + credits):** "Use AI to speed up how we understand and design molecules, cells and materials." The only other text we have is the track card; the judging criteria are not in the repository. We align with the README's position (§0, §3, §7, §8): held-out validation, limits disclosed up front, nothing built that raises virulence, claims only as strong as their tests.
- **Best use of Boltz (bounty inside that track, Boltz credits):** "Best use of Boltz's biomolecular models: structure, binding affinity or molecule design." What VaxForge actually uses Boltz-2 for is **structure only** (S7, top-k candidates). It produces no affinity number, because Boltz-2's affinity head covers protein to small-molecule binding only (docs/CONTRACT_S3_S7.md). See "Boltz in the video".

## Shot list

**Narrative (set by the project lead, 2026-11-15):** VaxForge is an end-to-end tool that saves researchers the slow, manual triage between "a pathogen" and "the few candidates worth a lab's time", so their effort goes into testing the most promising candidate. The video shows that funnel, then proves it can be trusted, then hands the researcher something to run. Time saved is shown with measured numbers from our own runs (`[FILL]` until measured), never with a claim about how long a person would take.

Speaking pace is about 2.3 words a second, so 2:00 is roughly 275 words. The narration below is 278 words (counted, per shot [29, 44, 55, 38, 41, 37, 19, 15]); the built-in voice at 165 words a minute reads it at about 2.6 words a second, so keep it at or under 275; recount after every edit.

| # | Time | On screen | Narration | Evidence the viewer sees | Tag |
|---|---|---|---|---|---|
| 1 | 0:00 to 0:12 | Figma: Cover, then `00 Overview and key` | A pathogen has thousands of proteins. Choosing the few worth a lab's time is slow and hard to audit. VaxForge does it from one name, and shows its working. | The pipeline strip S0 to S12 on one screen | Sci, Figma |
| 2 | 0:12 to 0:30 | Terminal `make demo-offline` (real output, captured; stream A, PR #68, main eab8cc0), then Figma `01 Run launcher` (design; the static `02 Pipeline overview` mock is left out because it shows the pre-run fixture state) | VaxForge starts from a pathogen name. Here a stored forty-protein subset of Neisseria meningitidis stands in for the first three stages, and one command runs the other ten, offline, on real Vaxign-ML scores. Tools not installed here show as not run. Under a minute. | `make demo-offline` finishing: it copies the committed fixture, ranks with stream B's real Vaxign-ML scores, runs s3 to s12 offline, attaches the real Adaption evidence (printed as NOT produced by the demo), prints each stage's `degraded (why)` reasons and the closing line (measured 2026-11-15 on this Mac: all ok, 26.6 s wall time quiet, 35 s and 52 s with a fine-tune and a local model running, so the narration says under a minute); "40 of 2003 (fixture subset)"; the S4 line "incomplete screening: 4 gates did not run" read from the run; the Vaxign-ML scores file's image and date (stream B, PR 1) | Sci |
| 3 | 0:30 to 0:50 | Figma `03 Ranked candidates`, `03b Safety and conservancy`, `04c Structure (S7)` | Every candidate keeps its evidence chain: rank, gates passed, epitopes, and a Boltz-2 structure with its confidence and run mode on screen. In this offline smoke run most epitope residues sit below the confidence floor, and the screen says that too. `[FILL if the MSA re-run exists: with an alignment, the same stage reads ...]` | One candidate opened down to its gate results; 04c with the run-mode badge, the pLDDT bands and "epitope residues below the floor of 70: 22 of 22, 10 of 21, 31 of 31" (real sample, PR #46); an owner card | Sci, Figma, Boltz |
| 4 | 0:50 to 1:04 | Figma `05 Validation` (design), then the real `vaxforge validate bexsero` output | Then a credibility check: do the three licensed Bexsero antigens reach the top three? Here, no: ranks seven, nine and fourteen. The model saw them in training, so VaxForge calls this test circular. That honesty is the point. | Bexsero recall@3 = 0 of 3 on the real Vaxign-ML scores (docs/examples/vaxign_ml_scores.nmb_fixture.tsv, stream B, PR #62): top 3 are IgA protease, NMB1829 and HmbR; NadA is 7th, NHBA 9th, fHbp 14th of 40, all above S3's 0.90 threshold; `vaxforge validate bexsero RUN/s3_rank.json --k 3` printed, 2026-11-15 02:48Z: `leakage assertion: not checked`, `recall@3: 0.0 (0/3; ties best 0.00, worst 0.00)`, fHbp rank 14, NadA rank 7, NHBA rank 9; the line `holdout_enforced: false` and the LEAKAGE RISK warning; do not say these ranks validate or invalidate the pipeline | Sci |
| 5 | 1:04 to 1:20 | Report sections Wet-lab protocol and Robot code (generated by `python -m vaxforge.report RUN` from the same run), then real `opentrons_simulate` output | The researcher gets what to test: a protocol with a falsification condition, and OT-2 code that passes the simulator, thirty-six programs in this run, so effort goes to the most promising candidate and not to triage. Refusals are counted, not hidden. | Simulator output passing (36 of 36 files re-simulated with `opentrons_simulate` 7.5.0 by the audit, 2026-11-15); the automatability matrix; the S12 line (`blocked`, 3 items FOR YOUR REVIEW) | Sci |
| 6 | 1:20 to 1:40 | The Adaption run page (captured), then the report's S9.5 section (generated from the attached evidence; Figma `07b` is the design of the same panel) | We fine-tuned the code generator with Adaption: 429 real OT-2 protocols and 571 synthetic rows, held-out families removed, leakage checked before upload. On Adaption's own evaluations the adapted model wins 78% against 22% for the base model. | Manifest counts (429 real, 571 synthetic, 42.9% real); the Adaption run page, Training Winrates chart base 22 / adapted 78 (captured: `~/vaxforge-work/demo/adaption_measure_winrate.jpg`; run `82c660bc-2c35-4917-b4d1-e7c58039b904`, Qwen3.5-4B, `best_win_rate` 0.7832, 1 of 2 iterations); our own held-out win rate `[FILL]`; leaderboard row `[FILL]` | Adaption |
| 7 | 1:40 to 1:52 | Figma editor: layers, Variables panel, version history; `How Figma was used` | The whole product was designed in Figma: editable layers, colours bound to variables, an owner card on every screen. | The Variables panel (29 variables, 19 of them colour, as counted in the editor on 2026-11-15; how many are bound was not checked); version history; the file's process log | Figma |
| 8 | 1:52 to 2:00 | Figma `10 Limits and absent` | Candidate antigens, not a vaccine, and every claim has a test that can refute it. | The limits list | Sci |

## What must be true before each shot is recorded

1. Shot 2: `make demo-offline` runs to the end on the machine that records (DONE 2026-11-15 on this Mac, main eab8cc0: all stages ok, S9.5 evidence_attached). It needs the Opentrons venv (`VAXFORGE_OT_PYTHON`, `tools/setup-ot-venv.sh`) for S9; nothing is fetched.
2. Shot 4: `vaxforge validate bexsero runs/demo/s3_rank.json --k 3` has been run on the demo run (DONE 2026-11-15: recall@3 0.0, fHbp 14, NadA 7, NHBA 9; `leakage assertion: not checked`). The number is shown only with the circularity statement.
3. Shot 5: S8 and S9 run on the final candidate set; `opentrons_simulate` output captured; stream C's refusal count read from the run, not typed.
4. Shot 6: the Adaption run is done (2026-11-15 02:24Z: Qwen3.5-4B, best_win_rate 0.7832). Say "Adaption's own evaluations": it is the platform's number, not ours. Our own held-out win rate needs the downloaded checkpoint served locally and `vaxforge finetune winrate`; until that exists the video does not show one. Do not mention a leaderboard rank until the public page lists the run.
5. Shot 7: Figma file open on the final version; the second pass of checks (the independent re-check) done.

## Do not claim

- An Adaption leaderboard rank before the public page lists the run (it does not yet, as of 2026-11-15 02:30Z). The 78% is Adaption's own win rate on evaluations it derives from our data, not a held-out result; our own held-out win rate does not exist yet. The local rehearsal (Qwen3-4B, 7 matched tasks: 1 win, 1 loss, 5 ties) found no measurable difference; if it is mentioned, say that.
- That the family labels were checked by a person. They are an agent's reading; the leakage check is the safeguard (README §S9.5).
- Wet-lab results, efficacy, safety or that anything is a vaccine. The outputs are candidates and a protocol that has not been run.
- That a Bexsero recall of 0 of 3 on this subset says the pipeline works or fails: it is 40 proteins, the model saw the antigens in training, and the leakage assertion was not checked.
- That model-written robot code is safe to run. S9's `static_lint` is a filter, not a sandbox (the auditor found a bypass, stream C is fixing it, board 02:44Z); say "passes the simulator", never "safe".
- That the report or the chain is complete: in the offline demo the four S4 gates, the T-cell and B-cell predictors, population coverage and Boltz all report `not run`, and S12 stays `blocked`; the video says so.

## Evidence we can show for each prize

| Prize | Show | Where it lives |
|---|---|---|
| Adaption | Manifest (included rows, real share, tags), generation manifest, win-rate report, Adaption job id and dashboard, leaderboard row, spend | `runs/.../s9_5`, `validation/`, Adaption dashboard |
| Figma | Import from code to editable layers, variables panel, owner cards, version history, the plain process log | Figma file `pzytlXMGdl8JuSNivTeOOq`, `docs/design/FIGMA_EVIDENCE.md` |
| AI for Science | LOPOV and Bexsero numbers, leakage report, simulator output, biosecurity gate, limits | `validation/`, `README.md` §3 §7 §8 |
| Best use of Boltz (bounty) | Boltz-2 structures of the top-k candidates, their confidence, the refolder cross-check and the epitope-residue confidence | `runs/.../s7_structure.json`, `s7/<id>/*.pdb` (stream B) |

## Boltz in the video

True today (docs/TOOLS_SETUP.md, docs/STREAM_B.md, and the real sample `docs/examples/s7_structure.real_mc58_boltz_cpu.json`, stream B, PR #44): Boltz-2 2.2.1 is wired into S7 and was run on CPU in offline single-sequence mode, fast smoke settings, on 3 proteins of the MC58 proteome (not the 40-protein fixture; none is a Bexsero antigen). The models are low confidence: mean pLDDT 58.2, 67.4 and 36.3 (the last has 89% of residues at or below 50), so stream B's own notes call S7's structure term weak evidence in that mode. Per-residue confidence is not in the artifact (only the mean and four band fractions). Stream B fixed a scaling bug in the epitope-below-floor fraction (PR #46, 2026-11-15); the corrected sample reads: epitope residues below the pLDDT floor of 70 are 22 of 22, 10 of 21 and 31 of 31 for the three proteins, so in this smoke run most predicted epitope residues sit in low-confidence regions. That is the honest message to show. ESMFold and AF2-no-MSA did not run, so there are no agreement values; the screen says "not run". The default `s7.boltz.msa: server` sends each sequence to the ColabFold MSA server; `empty` stays offline. No affinity value is produced. S7 also reports how three other refolders agree with the Boltz-2 model.

So in shot 3, show the structure **with its confidence and the mode it was run in**; do not present a low-confidence fold as a finding. If we want the Boltz bounty to be a real contender, the open option is a better run: Boltz-2 with an MSA on a GPU for the top-k (Tamarind Bio is a sponsor, and the track card lists Boltz credits), then show the pLDDT of the predicted epitope residues next to the epitope predictions. That is stream B's stage and needs the lead's decision and an account; nothing has been started.

## Storyboard in Figma (for the design agent)

Add a second page named `Demo video (2:00)` to the file. One 1920 x 1080 frame per shot, named `Shot 1 · 0:00 to 0:12 · Hook`, laid out left to right in order, each holding: the shot number and time range, a thumbnail or a reference label naming the Figma frame or terminal view to show, the narration text, the evidence the viewer sees, and the tag chips (Sci, Figma, Adaption). A first frame holds the run of show: total time, the pace, the "Do not claim" list and the prerequisites. Use the file's own variables and type styles. `[FILL]` values stay visibly marked as placeholders on the frames. Do not copy thumbnails as images of the whole UI: reference the frame by name.

## Open questions for the lead

1. The AI for Science track's judging criteria (we only have the track card), so the shot order and wording can match them.
2. Is two minutes a hard limit, and is a voice-over or on-camera presenter expected?
3. Do we record before or after the real Adaption run? Shot 6 depends on it.
