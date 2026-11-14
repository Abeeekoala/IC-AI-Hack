# Stream B - prediction core (S3 - S7)

Implements **S3 antigenicity ranking, S4 safety & conservancy filters, S5 epitope prediction, S6 population coverage and S7 structure + the final coarse ranking**.
Owner (`AGENTS.md`): `@benjamin-codes-labs`. Artifact schemas: [CONTRACT_S3_S7.md](CONTRACT_S3_S7.md) (read first). Every external tool, with install notes, licences and what is
verified versus unverified: [TOOLS_SETUP.md](TOOLS_SETUP.md). Config block (all values are also the built-in defaults): [config.stream_b.yaml](config.stream_b.yaml).

```
src/vaxforge/s3_rank.py  s4_filter.py  s5_epitopes.py  s6_coverage.py  s7_structure.py   # the five stages; each has run(store, config), exactly as `vaxforge run` calls it
src/vaxforge/prediction/                                                                  # stream-B-private helpers (tool wrappers, parsers, coverage maths); nobody else imports it
tests/stream_b/                                                                           # unit tests (synthetic data + Stream A's fixture); live/ = opt-in network checks and a real-data smoke run
scripts/stream_b_make_config_snippet.py                                                   # regenerates docs/config.stream_b.yaml from the defaults (a test keeps them in sync)
```

## Run

`vaxforge run` picks the stages up automatically once S0-S2 have run (`--stages s3,s4,s5,s6,s7` to choose). S5 takes minutes and S7 is slow on CPU when Boltz-2 is installed; a machine
with none of the external tools still completes every stage with labelled `unavailable` / fallback warnings. Per stage, on an existing run folder (this goes through `RunStore` when the folder has a `manifest.json`):

```bash
python -m vaxforge.s3_rank --run-dir runs/nmb --config config.yaml
python -m vaxforge.s4_filter --run-dir runs/nmb      # ... s5_epitopes, s6_coverage, s7_structure
# any config value can be overridden:  --set s3.backend='"scores_file"' --set s7.top_k=10
```

Tests: `pip install -e ".[dev]"` then `python -m pytest` (numpy is in the `dev` extra; stream B's tests skip cleanly without it). Opt-in network checks: `VAXFORGE_LIVE=1 python -m pytest tests/stream_b/live`.
Install numpy for real runs with `pip install -e ".[prediction]"` (without it S5's AlphaFold check is `skipped` and S7's structure comparison is `failed: numpy is not installed`, both labelled).

## What you should know before the demo

* **What has really been run (2026-11-14, real *N. meningitidis* MC58 + human Swiss-Prot data, macOS arm64):** BLAST+ 2.17, AlgPred2, ToxinPred2 (S4), **Vaxign-ML in Docker (S3)**,
  MHCflurry (S5 class I, `fidelity: alternative`), BepiPred-3.0 and DiscoTope-3.0 (S5), Boltz-2 2.2.1 (S7, CPU, offline single-sequence mode), real UniProt/UniParc/AlphaFold DB endpoints,
  real HLA frequencies from the IEDB bundle (S6) - plus Stream A's PSORTb container and the Opentrons 7.5.0 simulator. My S3-S7 also ran through **Stream A's real `RunStore` on their committed fixture**
  (their `vaxforge verify` passes afterwards). Docker is provided by Colima (`brew install colima docker`; `colima start --vm-type vz --vz-rosetta --cpu 4 --memory 8 --disk 30`; `colima stop` when done).
  **Not run:** NetMHCpan/NetMHCIIpan (DTU licence form), ESMFold (API would send sequences to a third party), ColabFold, and a live end-to-end `vaxforge run --pathogen ...`. Also only ever run on macOS arm64
  (no Windows / Linux test). `python tests/stream_b/live/real_smoke.py` reproduces the single-machine run (tools in `.venv`, `.venvs/{algpred2,bepipred,boltz,discotope,opentrons}`, `.envs/blast`, `.tools/`).
* **Real-data findings you should know about** (details in `docs/CONTRACT_S3_S7.md` section 4 and `docs/TOOLS_SETUP.md`):
  1. With the README's defaults **S4 rejects all three Bexsero antigens** (fHbp/NHBA by conservancy — variant-group antigens at 70-84 % identity to other strains;
     NadA by conservancy and by AlgPred2 at 0.596 >= 0.45). This directly affects stream D's recall@3 test; `s4.conservancy.on_fail: flag` and `s4.rescue` exist for it.
  2. UniProt returns **empty** FASTA for non-reference strain proteomes (fixed: UniParc fallback, empty files are never cached).
  3. AlgPred2's pip package needs `scikit-learn==1.2.2` / `numpy<2` (its own env); ToxinPred2 does not.
  4. The first T-cell score saturated at 1.0 for every real candidate and MHCflurry epitopes were counted once per peptide length; both fixed.
  5. The README's "BepiPred truncates above 1,023 aa" did not reproduce on the local CLI (1,144 residues came back intact); chunking is kept as a harmless precaution (r = 0.989 vs unchunked).
  6. Boltz-2's weights are 7.6 GB, `boltz --version` does not exist, and offline single-sequence folds are low-confidence (pLDDT 38-67), so S7's structure term is weak evidence in that mode.
  7. IEDB's own source shows the coverage tool treats loci as independent, counts homozygote hits once, and interpolates PC90 — S6 now matches it exactly (tested against a port).
* **Missing tools never turn into silent passes.** S3 may fall back to a clearly-labelled rule-based ranker (`fidelity: fallback`,
  `RULE-BASED FALLBACK` warning, no protegenicity). S4 gates become `unavailable` + `incomplete_screening: true`. S5/S6/S7 blocks
  become `unavailable`; S7 degrades to sequence-only ranking. Only MHCflurry (class I) and our own coverage calculator act as
  stand-ins, both labelled `alternative`.
* **Leakage is auditable.** The stock Vaxign-ML model was trained on all of Protegen: S3 records `leakage_audit.holdout_enforced=false` and warns.
  A LOPOV-retrained model can be plugged in (see the contract) so stream D can assert the holdout actually held out.
* **Deviations from the README/PDF** (all explained in `docs/CONTRACT_S3_S7.md` §9): no Boltz-2 affinity number (its affinity head is
  protein–small-molecule only); DiscoTope-3.0 also runs in S5 on AlphaFold DB models; S6 uses our own IEDB-method calculator (+ IEDB-format export for cross-checking);
  Vaxign-ML's "protegenicity" is a percentile.
* **One default sends sequences off the machine:** Boltz-2 with `s7.boltz.msa: server` (the ColabFold MSA server), only when Boltz is installed. It is flagged in the artifact's
  `warnings`/`tools`; set `s7.boltz.msa: empty` to stay offline (lower accuracy). The ESMFold refolder is **local by default** (needs `transformers` + torch, no network); the public ESM Atlas web
  service is an explicit opt-in (`s7.esmfold.mode: api`) because it sends the sequence to a third party.
* **Scores that are heuristics, not validated predictors:** S3's rule-based fallback, S5 `tcell.score`/`bcell.linear_score`, S7 `final_score`
  weights, and several S4 thresholds (marked "project-chosen" in the contract). They are config-driven so the validation harness can re-tune them.
* Nothing here simulates wet-lab readouts or clinical trials; S6/S7 output states the limits (`limits` arrays) in the artifact itself.

## How this was checked before the PR

* Unit tests (stream B): synthetic data plus Stream A's `fixtures/nmb_fixture`; the whole suite (A + D + B) passes together with and without numpy installed.
* On a Mac (arm64) with the real tools, 2026-11-14: BLAST+, AlgPred2, ToxinPred2, MHCflurry, BepiPred-3.0, DiscoTope-3.0, Boltz-2, Vaxign-ML (Docker), Stream A's PSORTb container, the Opentrons
  simulator, real UniProt / UniParc / AlphaFold DB endpoints and real HLA frequencies. S3-S7 also ran through Stream A's real `RunStore` on their fixture and `vaxforge verify` stayed clean.
* **Not checked:** Windows and Linux; NetMHCpan / NetMHCIIpan (DTU licence), ESMFold, ColabFold; a live `vaxforge run --pathogen ...` from S0 through S7; a Vaxign-ML model retrained for LOPOV.
