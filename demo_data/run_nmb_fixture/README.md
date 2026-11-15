# demo_data/run_nmb_fixture: the offline demo run (stream A)

The complete run folder written by `vaxforge demo-offline`: every stage artifact (`s*_*.json`), `manifest.json`, `config.resolved.json`, the `data/`
sidecars and `report.html`. S0 to S2 are the committed fixture `fixtures/nmb_fixture`, **not a live run**; S3 to S12 were run on top of it, offline,
on 2026-11-15. About 180 files, about 2 MB, the largest is `report.html` (172 KB). Every file was scanned: no secrets, no local absolute paths, nothing over 10 MB.

## How it was made

- Commit: `main` at 2221800 (merge of PR #88) plus the portable-path change of this PR (`demo-offline` writes repo-root-relative paths).
- Machine: Windows 11, Python 3.15.0, no network, none of the S4 to S7 tools and no Opentrons simulator installed (see below).
- From the repo root:

```
python -m vaxforge demo-offline --out demo_data/run_nmb_fixture --force
python -m vaxforge report demo_data/run_nmb_fixture --out demo_data/run_nmb_fixture/report.html
python -m vaxforge verify demo_data/run_nmb_fixture     # OK - digests, upstream chain and referenced files are consistent
```

- Regenerating on the same code gave identical digests for 14 of 15 stages (checked twice, on this machine only). `s11` differs every time: it records
  `registered_at`, this machine's clock.
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
| S9 | Robot code generated from templates (3 candidates, 36 sessions). **Not simulated** on this machine: `payload.status` is `not_run` and all 36 sessions are `UNVERIFIED`, which is not a pass. Set `VAXFORGE_OT_PYTHON` and re-run S9 to simulate them. |
| S9+ | `not_run`. |
| S9.5 | The real earlier Adaption run (Qwen3.5-9B, run `eeee1dc9-d699-4618-a3a2-8ca9e8ad4dc1`) attached from `docs/examples`, status `evidence_attached`. It was **not produced by this demo**. `held_out_win_rate` is null here. |
| S10 | An animal-study design from built-in tables; it records 17 `outstanding_human_inputs` (values a person must still supply). No animal data. |
| S11 | A pre-registration with a frozen digest. `registered_at` is this machine's clock, so it is evidence of timing only if the digest is published somewhere the team does not control. |
| S12 | State `blocked`: S4 recorded no toxicity result for the three selected candidates (its tool did not run) and no provider screening record was supplied. Two candidates are flagged for human review (virulence-associated), not blocked. |

## Whose number is whose

- Vaxign-ML scores: the tool's own percentile (0 to 100); S3 divides by 100. Not probabilities.
- `best_win_rate` 0.9439 in S9.5: **Adaption's own** optimisation target, measured on evaluations it derives from our uploaded data. Not protocol quality.
- Everything else was computed by this pipeline and is ours. None of it is a validation.

## Not verified

- No wet-lab or animal data anywhere in this folder.
- S4 to S7 predictions never ran here; S9 and S9+ were not simulated.
- The Bexsero positions in S3 are circular (the stock model was trained on them).
- Digest reproducibility was checked on one machine only. `report.html` was generated by `vaxforge report`; its appearance was not reviewed here.

## For the UI

Read the artifacts in this folder instead of copying values: `docs/design/screen_fields.md` maps each screen field to an artifact key, and `vaxforge report`
(`src/vaxforge/report.py`) already renders the whole evidence chain from a run folder.
