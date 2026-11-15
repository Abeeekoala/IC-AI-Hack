# Pre-run: N. meningitidis (40-protein fixture), real tools, 2026-11-15

Pre-run so the demo does not wait on the tools. `report.html` is the self-contained evidence report; `s0` to `s12` are the stage artifacts
(`vaxforge verify results/nmb_real_run` passes); `HOW_THIS_WAS_RUN.sh` is the exact command (`/private/tmp/vf_tools` stood for the folder holding the
BLAST+, AlgPred2, ToxinPred2, BepiPred and cache installs on the machine that ran it).

| Stage | What ran | Result |
|---|---|---|
| S0-S2 | committed fixture, not re-run | 40 real UniProt proteins, real VFDB BLAST, localisation is a UniProt-text stand-in (not PSORTb) |
| S3 | real Vaxign-ML scores (stock model) | 30 candidates carried to S4; fHbp 14th, NadA 7th, NHBA 9th. LEAKAGE RISK: the model was trained on these antigens |
| S4 | BLAST+, AlgPred2, ToxinPred2, conservancy vs a second strain | 30 in, **27 kept**, 3 rejected (frpA and frpC toxicity, lldD human homology). **fHbp, NadA and NHBA are kept**, flagged (NadA: allergenicity + conservancy; fHbp, NHBA: conservancy) |
| S5 | MHCflurry (class I, an alternative to NetMHCpan), BepiPred-3.0 | class II and conformational B-cell did not run (listed in `degraded`) |
| S6 | own IEDB-method population coverage | World class I coverage 98.5% |
| S7 | no structure: Boltz not run on this machine | final order is sequence-only; NHBA 7th, NadA 10th, fHbp 15th of the 27 |
| S8-S12 | offline, template protocols, Opentrons simulator, real earlier Adaption run attached | all ok |

Read this before quoting it: the S4 allergenicity and conservancy settings (flag, not reject) were chosen after a first run in which strict
settings rejected all three Bexsero antigens. The run therefore shows the pipeline working as designed; it is not a validation. No wet-lab result exists.
