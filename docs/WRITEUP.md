# VaxForge: submission write-up

Draft, kept current with the repository. Anything marked `[FILL]` is a number that does not exist yet; it is not estimated here. Every claim below has a command or file behind it, listed in "Evidence".

## What it is

VaxForge takes a pathogen name and does the slow, manual triage between "a pathogen" and "the few candidates worth a lab's time": it resolves the species, fetches the proteome, screens and ranks candidate protective antigens, predicts epitopes and population coverage, folds the top candidates with Boltz-2, writes a protocol with a falsification condition for each candidate, and compiles its liquid-handling steps to Opentrons OT-2 code that passes the simulator. A researcher's effort then goes into testing the most promising candidate instead of into triage.

It also says how far to trust any of it. A retrospective validation harness (leave-one-pathogen-out, and the Bexsero recall@3 case) and a leakage check sit next to the ranking, every stage records the tool version and parameters that produced it, and a stage that could not run says so instead of inventing a number.

What it is not: it does not predict wet-lab results, simulate trials, or build anything that raises virulence, transmissibility or immune escape. Its output is candidate antigens and a protocol that has not been run, not a vaccine (README §0 and §8).

## The three prizes

### AI for Science (main track; sponsors Boltz and Tamarind Bio)

- The pipeline is S0 to S12 over content-addressed JSON artifacts (`runs/<run_id>/`), so every number in the final report traces to a tool, a version and its parameters.
- Held-out evidence, not self-assessment: LOPOV and the Bexsero recall@3 case (`vaxforge validate lopov|bexsero`), with the leakage check beside the answer. Bexsero recall@3 on the real Vaxign-ML scores of the 40-protein fixture subset: 0 of 3 (fHbp rank 14, NadA rank 7, NHBA rank 9). The test is circular, because the stock Vaxign-ML model was trained on all of Protegen (`holdout_enforced: false`, `leakage assertion: not checked`), so the result neither validates nor refutes the pipeline; the demo says so.
- Limits first: README §0, §4, §7 and §8 are written before the results, and the demo shows them.
- Time saved is measured on our own runs, not claimed for a person: the offline run on the Neisseria meningitidis fixture runs stages 3 to 12 in 25 seconds on a quiet laptop and 43 seconds with a fine-tune and a local model running at the same time (40 of 2,003 proteins, a fixture subset, stated on every screen that shows it; Boltz, BLAST+, PSORTb and the T-cell and toxicity predictors are not installed on that machine, so those parts report `not run`).

### Best use of Boltz

- Boltz-2 (2.2.1) is wired into S7 for the top-k candidates and its confidence is reported, not hidden: mean pLDDT, the four confidence bands, the run mode (engine, MSA mode, accelerator) and the share of predicted epitope residues below the pLDDT floor of 70.
- Real sample (`docs/examples/s7_structure.real_mc58_boltz_cpu.json`, three proteins of the MC58 proteome, CPU, offline, no MSA, fast smoke settings): mean pLDDT 58.2, 67.4 and 36.3; epitope residues below the floor 22 of 22, 10 of 21 and 31 of 31. In that mode most predicted epitope residues sit in low-confidence regions, and S7's structure term is weak evidence. We show that rather than a hopeful number.
- No affinity or design claim: Boltz-2's affinity head covers protein to small-molecule binding only, and VaxForge produces no affinity value.
- Not done: a Boltz-2 run with an alignment (MSA) on a GPU, the obvious upgrade for this stage. Stream B measured nothing for it and advises against starting one on the 16 GB CPU machine we had: the top three candidates of the demo run are 1,815, 708 and 791 residues, the first is over S7's 1,500-residue limit (S7 skips it), and a standard-settings fold of the others on CPU is far beyond the time we had. The CPU smoke run above is the only Boltz run on record.

### Best-Designed AI Product (Figma)

- The product surface was designed in Figma: 16 screens as editable layers, colours defined as variables (19 colour variables of 29), a status-pill component set, and an owner card on every screen naming who owns that stage and what to check.
- The process is recorded in `docs/design/FIGMA_EVIDENCE.md` (what was imported, what was fixed by hand, what was checked by an independent re-check).
- The design is honest by construction: "fixture subset", "not run", "unverified" and "needs review" are first-class states, not footnotes.

### Best Use of Adaption

- S9.5 fine-tunes the OT-2 code generator with Adaption's AutoScientist (SFT), and our own harness compares it with the base model on held-out Text2WetLab tasks.
- Training set: 1,000 rows, 429 real (42.9%, from 136 pinned public sources: permissively licensed repositories and the Opentrons protocol library, every pair passing `opentrons_simulate` v7.5.0) and 571 synthetic rows from our generator, disclosed and labelled as such. Real rows above 32,000 characters were excluded as a run-time control.
- Held-out families are removed (7 families, 11 tasks) and `validation/leakage_check.py` checked 1,000 pairs against 223 held-out texts: 0 violations. An agent-run refusal pass removed pairs the base model refuses. The family labels are an agent's reading and no person checked them; the leakage check is the safeguard.
- Runs, all three on the same 1,000 rows, SFT with LoRA: Qwen3.5-9B (run `eeee1dc9-d699-4618-a3a2-8ca9e8ad4dc1`, target 0.85, up to 3 iterations), Qwen3.5-4B (run `82c660bc-2c35-4917-b4d1-e7c58039b904`, target 0.6, up to 2) and Qwen3.5-2B (run `d84a8798-c9ef-441d-872c-0a6c409dcbd3`, target 0.6, up to 2), each also capped at 90 minutes. Results as Adaption reports them: the 9B run succeeded after 2 of 3 iterations with `best_win_rate` 0.9439 (0.8411 after the first; run page: adapted model wins 94, base 6; about 62 minutes); the 4B run succeeded after 1 of 2 with 0.7832 (run page: 78 against 22; 41 minutes); the 2B run succeeded after 1 of 2 with 0.6037. LoRA checkpoints for 9B (sha256 `9cb41802...`) and 4B (sha256 `c7b45c54...`) were downloaded. Leaderboard: not listed on the public page as of 2026-11-15 03:30Z; its entries carry a Domain Win Rate that our run page shows only behind a paid tier, so whether and where our runs appear is not under our control. `[FILL: row, domain]` if it appears.
- Our own held-out test (`vaxforge finetune winrate`, 4B adapter only, one run): 0 wins, 0 losses, 10 ties over 10 tasks in 7 families, every tie "neither passes", because no program from either arm passed `opentrons_simulate` 7.5.0; verdict "no measurable difference" (`docs/examples/s9_5_winrate_report.real_4b.json`). It is a floor effect, not a verdict on the adapter: 7 of 10 base replies and 4 of 10 adapter replies hit the token cap, the task prompts (1,000 to 12,000 tokens) are far longer than the adapter's training prompts, the adapter was trained on a bf16 base and applied to a 4-bit one, and each task was sampled once. The 9B adapter was not tested. So the claim is limited to what Adaption measured, labelled as Adaption's own number (`best_win_rate` is computed on evaluations the platform derives from our data); we do not claim the fine-tune writes better protocols.
- An earlier local rehearsal (Qwen3-4B, 7 matched tasks: 1 win, 1 loss, 5 ties) is not used as evidence: its server log suggests the adapter was not applied (`mlx_lm.server` drops `--adapter-path` when a request names the model by its Hugging Face id), so it probably compared the base model with itself. Inferred from the log, not re-run; the README (S9.5, Running the models) now carries the warning.

## Evidence

| Claim | Where |
|---|---|
| Pipeline artifacts, digests, verify | `vaxforge verify <run>`, `docs/ARTIFACT_CONTRACT.md` |
| Held-out validation, leakage | `validation/`, `vaxforge validate ...`, README §3 |
| Boltz structure and confidence | `docs/examples/s7_structure.real_mc58_boltz_cpu.json` (stream B) |
| Training manifest, guard, leakage | `vaxforge finetune manifest|check`, `validation/leakage_check.py`, README §S9.5 |
| Adaption runs, win rate | `adaption_evidence.json` per run, Adaption dashboard |
| Figma use | Figma file `pzytlXMGdl8JuSNivTeOOq`, `docs/design/FIGMA_EVIDENCE.md` |
| Demo video script | `docs/design/DEMO_VIDEO_PLAN.md` |

## Honest limits

- Candidates, not a vaccine; nothing here was tested in a lab. Every prediction is mapped to the assay that would refute it (S10).
- The Boltz run on record is a CPU smoke run in single-sequence mode: low confidence by design of the settings, not a finding.
- The offline demo chain is degraded on purpose and says so: the four S4 gates (human homology, allergenicity, toxicity, conservancy) did not run, so S4 reports incomplete screening, and S12 stays `blocked` because no toxicity result and no provider screening record exist.
- The 98 original real pairs became 429 after a larger data hunt; the family labels on them are an agent's reading. The training set is 57.1% synthetic.
- The S12 virulence check is a review flag, not a block, by the project owner's decision for an exploratory build; toxin, sequence sanity, selected-candidate and provider-screening checks still block.
- Adaption does not document run time or credit cost, and which leaderboard domain a run lands in is not under our control.
