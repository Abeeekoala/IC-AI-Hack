# demo_data/run_nmb_fixture: the offline demo run (stream A)

The complete run folder written by `vaxforge demo-offline`: every stage artifact (`s*_*.json`), `manifest.json`, `config.resolved.json`, the `data/`
sidecars and `report.html`. S0 to S2 are the committed fixture `fixtures/nmb_fixture`, **not a live run**; S3 to S12 were run on top of it, offline,
on 2026-11-15; S9 was simulated and S9+ was run (see the table). 287 files, 35.6 MB (including this README); the largest are the S9+ `timeline.json` files (the three of the `s13_rt_qpcr`
session are 1.7 MB each). `report.html` is 160 KB. Nothing is over 10 MB. Every file was scanned: no secrets, no user names. The only local-path text is the neutral generation folder
`C:\vf_demo\wt100\...`, inside one warning of `s6_coverage.json` (the cache file it built) and in `report.html`, which shows that warning. A UI must not display it as a path.

## How it was made

- Commit: `main` at `7d2d664` (main after PR #95, #97 and #98; it includes the portable-path change of PR #93, so `demo-offline` writes repo-root-relative paths).
  Generated from a clean clone at the neutral path `C:\vf_demo\wt100`.
- Machine: Windows 11, Python 3.14.6 (as recorded in the artifacts' `environment`), none of the S4 to S7 tools, with the Opentrons 7.5.0 simulator in a separate
  Python 3.10 venv (`VAXFORGE_OT_PYTHON` points to it).
- From the repo root:

```
python -m vaxforge demo-offline --out demo_data/run_nmb_fixture --force --offline
python -m vaxforge report demo_data/run_nmb_fixture --out demo_data/run_nmb_fixture/report.html
python -m vaxforge verify demo_data/run_nmb_fixture     # OK - digests, upstream chain and referenced files are consistent
```

- Digest reproducibility of this version was not measured. (The first version's check, identical digests for 14 of 15 stages, was made on stream A's machine for
  the earlier, unsimulated run only.) `s11` differs on every run: it records `registered_at`, the generating machine's clock.
- Line endings: `.gitattributes` marks `demo_data/**` as `-text`, so git keeps these files byte for byte on every OS (four of them have Windows line endings,
  and the artifacts record the sha256 of the files they reference; `vaxforge verify` re-checks that). Regenerating the S9.5 files on a machine whose checkout of
  `docs/examples` has other line endings gives different bytes for those two sidecars and so a different S9.5 digest.

## What is real, fixture or stand-in

| Stage | What this folder holds |
|---|---|
| S0, S1 | The committed fixture (live NCBI, UniProt and VFDB data fetched on 2026-11-14): *Neisseria meningitidis* serogroup B MC58, proteome UP000000425 (release 2026_04), **40 of its 2,003 proteins** (a subset: 4 Bexsero-relevant, the 8 strongest VFDB hits, the rest seeded random). |
| S2 | Virulence: computed against VFDB when the fixture was built (16 of 40 proteins virulence-associated). Localisation: a **stand-in**, derived from UniProt's subcellular-location text and labelled `NOT PSORTb` in the artifact. |
| S3 | **Real Vaxign-ML scores** (stock pretrained model, made 2026-11-14 by stream B; see `docs/examples/vaxign_ml_scores.nmb_fixture.tsv.meta.json`). The model was trained on all of Protegen, so it saw the Bexsero antigens: `holdout_enforced` is false and S3 raised a LEAKAGE RISK warning. In S3's ranking NadA is 7th, NHBA 9th and fHbp 14th. That is **not a validation**. |
| S4 to S7 | Run with **no external tool** (AlgPred2, ToxinPred2, NetMHCpan, NetMHCIIpan, MHCflurry, BepiPred-3.0, DiscoTope-3.0, Boltz) and no network. Each stage lists what did not run under `payload.degraded`: S4 four gates, S5 four predictions, S6 population coverage, S7 no structure folded. These artifacts hold **no real prediction from those tools**. Real tool output is in `docs/examples` (stream B's Boltz sample), not here. |
| S8 | Protocols from the template backend (no language model). |
| S9 | Robot code from the template backend (no language model): 3 candidates, 36 sessions, simulated with Opentrons 7.5.0 (`s9_robot.json`: `simulator.simulated` true). All 36 sessions are `PASS` and `all_pass` is true. PASS means the code passed the static lint, ran in the Opentrons 7.5.0 simulator, broke none of the five physical rules (tip before aspirate, no overdispense, no aspirate from an empty well, tip dropped at the end, no cross-contamination) and matched the S9 plan. It does **not** mean the protocol works for the experiment: the volumes, times and temperatures are placeholder defaults (the artifact says so) and the S12 gate has not cleared. Per session under `data/s9/<candidate>/template/`: `.py`, `.plan.json`, `.sim.json`, `.grade.json`, `.setup.md`. |
| S9+ | 36 of 36 sessions animated (`s9plus_animate.json`: `n_animated` 36, `n_skipped` 0): `protocol_animation.html` and `timeline.json` per session under `data/s9plus/`. The times in the animations are **estimates** from a fixed time model, not measurements on a robot (the artifact warns of this). |
| S9.5 | The real earlier Adaption run (Qwen3.5-9B, run `eeee1dc9-d699-4618-a3a2-8ca9e8ad4dc1`) attached from `docs/examples`, status `evidence_attached`. It was **not produced by this demo**. `held_out_win_rate` is null here. |
| S10 | An animal-study design from built-in tables; it records 17 `outstanding_human_inputs` (values a person must still supply). No animal data. |
| S11 | A pre-registration with a frozen digest. `registered_at` is the generating machine's clock, so it is evidence of timing only if the digest is published somewhere the team does not control. |
| S12 | State `blocked`: S4 recorded no toxicity result for the three selected candidates (its tool did not run) and no provider screening record was supplied (4 reasons in all: 3 `toxin_flag`, 1 missing provider record). Two candidates (`Q9K0B4`, `Q9JYA8`) are on the review list as virulence-associated, flagged for human review, not blocked. The S9 artifact warns that the S12 gate is not evidenced to S9: do not order gene synthesis or run these protocols until it clears. |

## Whose number is whose

- Vaxign-ML scores: the tool's own percentile (0 to 100); S3 divides by 100. Not probabilities.
- `best_win_rate` 0.9439 in S9.5: **Adaption's own** optimisation target, measured on evaluations it derives from our uploaded data. Not protocol quality.
- Everything else was computed by this pipeline and is ours. None of it is a validation.

## Not verified

- No wet-lab or animal data anywhere in this folder.
- S4 to S7 predictions never ran here. S9 was simulated, but PASS is not evidence that a protocol works; the S9 parameters are placeholders and the S9+ times are estimates.
- The Bexsero positions in S3 are circular (the stock model was trained on them).
- Digest reproducibility of this version was not measured. `report.html` was rendered by `python -m vaxforge report`; its appearance was not reviewed here.

## Difference from the first version of this folder

The first version (stream A's PR #93) had S9 `UNVERIFIED` and no animations, because that machine had no Opentrons simulator. This version replaces it; stream A
pre-approved the regeneration on the board. Comparing with the first version: the S0 to S3 digests are unchanged (S3: unchanged). The S4 to S12 digests changed
(S4 to S8, S9.5, S10, S11 and S12 too, not only S9 and S9+). For S4 the `params`, the gate-unavailable reasons and the `environment` (Python and Windows build) differ from the first version; the cause for the
other stages was not traced. The S4 to S7 findings in the table still hold (no external tool ran).

## For the UI

Read the artifacts in this folder instead of copying values: `docs/design/screen_fields.md` maps each screen field to an artifact key, and `vaxforge report`
(`src/vaxforge/report.py`) already renders the whole evidence chain from a run folder.
