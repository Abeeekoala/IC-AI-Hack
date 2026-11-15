# demo_data

Data for the VaxForge UI (stream C) and for the final deliverable. Everything here is a committed copy of real tool output or of a run folder, so the UI and the write-up can be built without re-running the pipeline.

## Folders present now

| Folder | What it is | Owner |
|---|---|---|
| `run_nmb_fixture/` | Stream C's copy of the complete offline demo run: stages S0 to S12 all `ok`, plus `report.html`. See `run_nmb_fixture/README.md`. | made by `python -m vaxforge demo-offline`; copy held by stream C |

## Folders requested, not present

The board request asked three more owners to add a folder each. None of them is in this checkout, so nothing below can be relied on yet:

- stream A (owner of the demo run) to add its own folder of that run;
- stream B: `s3_s7_real/` (outputs of the real tools for S3 to S7);
- stream D: `s9_5_s11_s12/` (Adaption runs, win rate, S11/S12, validation, figures).

Until `s3_s7_real/` exists, the S4 to S7 content in `run_nmb_fixture/` is degraded (the tools were absent in that run).

## Rules for every folder

- No secrets (no API keys, tokens, `.env` files) and no local absolute paths of the machine that made the data.
- No model weights.
- Each file under about 10 MB (largest file in `run_nmb_fixture/` is 1.7 MB; the folder is about 35 MB in 288 files, README included).
- A `README.md` in each folder saying how the data was made, what is real, what is a fixture and what is a stand-in, and what was not verified.
- Do not edit files inside a run folder: the artifacts record the digests of their files, and `python -m vaxforge verify <folder>` fails after any change, whitespace included.
