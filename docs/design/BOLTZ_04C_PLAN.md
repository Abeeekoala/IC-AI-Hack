# Plan: make screen 04c (Structure, S7) a strong, honest demonstration of the Boltz-2 stage

Stream D (design) with a request to stream B (S7 owner). Written 2026-11-15.

## Why

The AI for Science track card names Boltz and Tamarind Bio as sponsors and has a bounty, "Best use of Boltz's biomolecular models: structure, binding affinity or molecule design". VaxForge uses Boltz-2 for one thing: the structure of the top-k candidates (S7). No affinity or design is produced, and the one run we know of (stream B's, CPU, offline, no sequence alignment) was low confidence (mean pLDDT 38 to 67). Screen 04c today is a field table. The plan is to turn it into the screen that shows what the structure stage knows, how well, and how we know.

The strength we can honestly show is **that the pipeline reports its own uncertainty and checks itself**: confidence per residue, agreement of independent predictors, epitope residues placed on that confidence, the mode the run used, and a refusal to invent numbers it does not have. A better run makes the same screen stronger; it must not be needed for the screen to be true.

## What 04c shows (top to bottom)

1. **Run-mode strip.** Engine and version (`analysis.folding.tool`, `.version`), `msa_mode` (empty / server), `accelerator`, `seed_controlled: false`, and whether any sequence left the machine. One badge: "Offline, single-sequence: low-confidence mode" or "MSA, GPU". Source: `payload.analysis.folding`.
2. **Candidate list (top-k).** One row each: rank, protein, structure status (`ran / failed / skipped / unavailable / not_selected`), mean pLDDT as a bar, validator agreement value, `rank_basis` (`structure_informed` or `pre_structure`). Source: `payload.candidates[].structure`, `.rank_basis`.
3. **Selected candidate, confidence bands and epitope track.** Per-residue pLDDT is NOT in the artifact (stream B, board 2026-11-15: only the mean and the four band fractions; the per-residue values live in the PDB B-factor column, not committed). So the screen draws what exists: a stacked bar of the four band fractions (`frac_very_high_gt90`, `frac_confident_70_90`, `frac_low_50_70`, `frac_very_low_le50`) in the standard pLDDT colours, and under it a track of length `n_residues_modelled` with the predicted conformational epitope segments (`conformational_epitopes.epitope_segments`, residue ranges) bracketed on it, plus `n_epitope_residues` and `epitope_fraction`. The track is NOT coloured by residue confidence, because that data is not in the artifact. A labelled slot beside it names the model file (`structure.model_file`: root, path, sha256, bytes) where the 3D view goes in the HTML report.
4. **Epitope confidence floor.** `conformational_epitopes.fraction_epitope_residues_below_floor` against the pLDDT floor of 70. Stream B fixed the earlier x100 scaling bug (PR #46, main d001c52) and replaced the sample file; the corrected values are: Q7DDG6 22 of 22 epitope residues below the floor, Q9JXZ9 10 of 21, Q9K0Q8 31 of 31. Show it per candidate as "epitope residues below the floor of 70", with the plain reading: in this offline, no-MSA smoke run most predicted epitope residues sit in low-confidence regions, which is why S7's structure term is weak evidence in that mode. Caption: "Structure confidence measures the model, not immunogenicity" (verbatim from docs/CONTRACT_S3_S7.md).
5. **Agreement table.** Boltz-2 against ESMFold and AF2-no-MSA. In the real sample neither refolder ran (`analysis.validators.*.status: unavailable`, with the reason), so every agreement cell reads "not run" and `validator_agreement` is "no validator ran" (`value: null`, `n_validators: 0`). The screen shows that honestly. It is a feature: the stage says what it could not check. When refolders do run, the columns are lDDT-Cα, TM-score, Cα RMSD, mean pLDDT.
6. **Deliberate absences.** Two quiet cards: "Binding affinity: not implemented" and "Docking: not implemented", each with the reason from `payload.analysis.affinity.reason` / `.docking`. (Boltz-2's affinity head covers protein to small-molecule binding only.)
7. **What would refute it.** One line from the S10 table: "folded vs denatured binding assay" for S7 (docs/CONTRACT_S3_S7.md §8).
8. **Owner card.** Stream B owns S7; check: run mode, pLDDT floor and that `seed_controlled` is false (runs are not reproducible bit for bit).
9. **Run comparison (only when a second run exists).** Two columns, "offline CPU, no MSA" and "MSA, GPU", per candidate: mean pLDDT, epitope fraction below floor, agreement. This is the strongest single picture for the Boltz bounty: the same stage, better input, measurably better confidence. Until the second run exists the column is absent, not filled with a guess.

## Rules for the design edit

- Every number is read from the real sample `docs/examples/s7_structure.real_mc58_boltz_cpu.json` (stream B, PR #44) and the card says so: "real S7 sample: 3 proteins of the N. meningitidis MC58 proteome, Boltz-2 2.2.1, CPU, no MSA, fast smoke settings; not the 40-protein fixture; none is a Bexsero antigen". Its values: Q7DDG6 rank 1, mean pLDDT 58.2, bands 0 / 0 / 89.2% / 10.8%, 65 residues, 22 epitope residues (22 below the floor); Q9JXZ9 rank 2, 67.4, bands 1.3 / 48.1 / 34.4 / 16.2%, 154 residues, 21 epitope residues (10 below the floor); Q9K0Q8 rank 3, 36.3, bands 0 / 0 / 11.1 / 88.9%, 172 residues, 31 epitope residues (31 below the floor); two more candidates `not_selected`. Anything not in that file (a second run, agreement values) is absent or tagged "example values", never invented.
- No wording that says Boltz found, validated or confirmed an antigen. The stage ranks and cross-checks; it does not test immunogenicity.
- Layout uses the file's variables and type styles; the confidence-strip colours go in as four new colour variables (`plddt/very-high`, `plddt/high`, `plddt/low`, `plddt/very-low`) so the screen is bound to the design system.
- Done in the Figma editor on the existing 04c frame (re-importing the HTML would overwrite the manual layout fixes). Update `docs/design/screen_fields.md` section 04c and `FIGMA_EVIDENCE.md` afterwards.
- Do this only after the independent re-check agent has left the editor: one agent in the file at a time.

## What stream B is asked for (board post)

1. DONE (PR #44, docs/examples/s7_structure.real_mc58_boltz_cpu.json): a real trimmed S7 payload, 3 folds, not the fixture.
2. DONE: field names confirmed; per-residue pLDDT is not in the artifact. The epitope-below-floor fraction bug I reported was fixed by stream B (PR #46) and the sample replaced.
3. Whether S7 can be run with an MSA on a GPU for the top 3 to 5 candidates, and what the artifact looks like then (same schema, `msa_mode: server`). The GPU route (a Colab notebook, or the sponsor's credits) is **not decided**; this is a question about what S7 needs, not a request to start anything.
4. No change to stream B files is asked beyond that; the design reads the artifact.

## In the demo video

Shot 3 (0:27 to 0:47) spends about 8 seconds on 04c: the run-mode badge, the confidence strip with the epitope brackets, the agreement table. If the second run exists, the run comparison replaces the agreement table for those seconds. Narration never says Boltz found or designed anything; it says what the stage measures and how confident it is.

## Open

- A better Boltz run needs a decision from the owner of stream D (this file's author's human) on the GPU route and an account, and stream B's agreement to run it.
- Which top candidates to run: the top 3 to 5 by S7's pre-structure order, or the Bexsero antigens (fHbp, NadA, NHBA), which would also tie the structure to the validation case. The second choice is the better story; it is stream B's call whether those are in the top-k.
