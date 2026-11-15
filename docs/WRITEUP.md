# VaxForge: submission write-up

Draft, kept current with the repository. Anything marked `[FILL]` is a number that does not exist yet; it is not estimated here. Every claim below has a command or file behind it, listed in "Evidence".

## What it is

VaxForge takes a pathogen name and does the slow, manual triage between "a pathogen" and "the few candidates worth a lab's time": it resolves the species, fetches the proteome, screens and ranks candidate protective antigens, predicts epitopes and population coverage, folds the top candidates with Boltz-2, writes a protocol with a falsification condition for each candidate, and compiles its liquid-handling steps to Opentrons OT-2 code that passes the simulator. A researcher's effort then goes into testing the most promising candidate instead of into triage.

It also says how far to trust any of it. A retrospective validation harness (leave-one-pathogen-out, and the Bexsero recall@3 case) and a leakage check sit next to the ranking, every stage records the tool version and parameters that produced it, and a stage that could not run says so instead of inventing a number.

What it is not: it does not predict wet-lab results, simulate trials, or build anything that raises virulence, transmissibility or immune escape. Its output is candidate antigens and a protocol that has not been run, not a vaccine (README §0 and §8).

## The three prizes

### AI for Science (main track; sponsors Boltz and Tamarind Bio)

- The pipeline is S0 to S12 over content-addressed JSON artifacts (`runs/<run_id>/`), so every number in the final report traces to a tool, a version and its parameters.
- Held-out evidence, not self-assessment: LOPOV and the Bexsero recall@3 case (`vaxforge validate lopov|bexsero`), with the leakage check beside the answer. Bexsero recall@3: `[FILL]`.
- Limits first: README §0, §4, §7 and §8 are written before the results, and the demo shows them.
- Time saved is measured on our own runs, not claimed for a person: the offline run on the Neisseria meningitidis fixture takes `[FILL]` seconds (40 of 2,003 proteins, a fixture subset, stated on every screen that shows it).

### Best use of Boltz

- Boltz-2 (2.2.1) is wired into S7 for the top-k candidates and its confidence is reported, not hidden: mean pLDDT, the four confidence bands, the run mode (engine, MSA mode, accelerator) and the share of predicted epitope residues below the pLDDT floor of 70.
- Real sample (`docs/examples/s7_structure.real_mc58_boltz_cpu.json`, three proteins of the MC58 proteome, CPU, offline, no MSA, fast smoke settings): mean pLDDT 58.2, 67.4 and 36.3; epitope residues below the floor 22 of 22, 10 of 21 and 31 of 31. In that mode most predicted epitope residues sit in low-confidence regions, and S7's structure term is weak evidence. We show that rather than a hopeful number.
- No affinity or design claim: Boltz-2's affinity head covers protein to small-molecule binding only, and VaxForge produces no affinity value.
- `[FILL if the MSA run exists: the same stage with an alignment, per candidate, side by side with the smoke run.]`

### Best-Designed AI Product (Figma)

- The product surface was designed in Figma: 16 screens as editable layers, colours defined as variables (19 colour variables of 29), a status-pill component set, and an owner card on every screen naming who owns that stage and what to check.
- The process is recorded in `docs/design/FIGMA_EVIDENCE.md` (what was imported, what was fixed by hand, what was checked by an independent re-check).
- The design is honest by construction: "fixture subset", "not run", "unverified" and "needs review" are first-class states, not footnotes.

### Best Use of Adaption

- S9.5 fine-tunes the OT-2 code generator with Adaption's AutoScientist (SFT), and our own harness compares it with the base model on held-out Text2WetLab tasks.
- Training set: 1,000 rows, 429 real (42.9%, from 136 pinned public sources: permissively licensed repositories and the Opentrons protocol library, every pair passing `opentrons_simulate` v7.5.0) and 571 synthetic rows from our generator, disclosed and labelled as such. Real rows above 32,000 characters were excluded as a run-time control.
- Held-out families are removed (7 families, 11 tasks) and `validation/leakage_check.py` checked 1,000 pairs against 223 held-out texts: 0 violations. An agent-run refusal pass removed pairs the base model refuses. The family labels are an agent's reading and no person checked them; the leakage check is the safeguard.
- Runs: Qwen3.5-4B (run `82c660bc-2c35-4917-b4d1-e7c58039b904`) and Qwen3.5-2B (run `d84a8798-c9ef-441d-872c-0a6c409dcbd3`), each at most 2 iterations, stopped at 0.6 target win rate or a 90-minute cap. Result: `[FILL: status, iterations, best_win_rate]`. Leaderboard: `[FILL: row, domain]`.
- Our own win rate (`vaxforge finetune winrate`): `[FILL]`. It is not Adaption's number: the platform's `best_win_rate` is computed on its own evaluations, which we do not control.
- Local rehearsal before the run (Qwen3-4B, 7 matched tasks): 1 win, 1 loss, 5 ties, no measurable difference. We report it as that.

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
- The 98 original real pairs became 429 after a larger data hunt; the family labels on them are an agent's reading. The training set is 57.1% synthetic.
- The S12 virulence check is a review flag, not a block, by the project owner's decision for an exploratory build; toxin, sequence sanity, selected-candidate and provider-screening checks still block.
- Adaption does not document run time or credit cost, and which leaderboard domain a run lands in is not under our control.
