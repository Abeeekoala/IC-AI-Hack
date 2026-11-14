# Docker for BLAST+ and PSORTb (stream A)

Docker is **optional for BLAST+** (a native install works and is used first) and **required for PSORTb 3.0** on Windows
(there is no native Windows build). Nothing in the pipeline breaks without it - localisation is simply recorded as
`not_run`, with the reason (see `docs/STREAM_A.md`) - but the full S2 table needs PSORTb.

```bash
docker pull ncbi/blast:2.16.0                              # optional: only if you have no native BLAST+
docker pull brinkmanlab/psortb_commandline:1.0.2           # about 4 GB; needed for localisation
python -m vaxforge doctor                                  # shows which tools were found
```

## What the pipeline runs

The PSORTb command was taken from the image project's own wrapper script
([brinkmanlab/psortb_commandline_docker](https://github.com/brinkmanlab/psortb_commandline_docker), file `psortb`):

```
docker run --rm -v <results folder>:/tmp/results -e MOUNT=<results folder> \
    --entrypoint /usr/local/psortb/bin/psort brinkmanlab/psortb_commandline:1.0.2 \
    <-n|-p|-a> [extra args] -i /tmp/results/input.fasta -o terse
```
* The input FASTA is copied into the mounted folder and the result file is written there too.
* **`-r` is not passed.** For the real `psort` binary `-r` means "PSORT_ROOT" (see its man page); only the wrapper script reuses it as a
  results directory. An earlier draft of this repo passed it - that would have broken the container.
* The entrypoint is overridden explicitly because the wrapper passes the binary path itself.
* Gram class (`-n` Gram-negative, `-p` Gram-positive, `-a` archaea) comes from the S0 lineage or `--gram`. PSORTb's own USAGE notes say to run
  *Mycobacterium* / *Deinococcus* ("Gram-positive with an outer membrane") with `-n`, and *Mycoplasma* and other Tenericutes
  ("Gram-negative without an outer membrane") with `-p --x-skip-localization=Cellwall`; S0 applies both rules
  (`organism.gram_stain = positive_with_outer_membrane | negative_without_outer_membrane`).
* A natively installed `psort` (Debian/Ubuntu package `psortb`) is used first if found: `psort -n -o terse input.fasta`, result on stdout.
* BLAST+: `BlastRunner` (`src/vaxforge/tools.py`) prefers a native `blastp`/`makeblastdb` (`PATH`, `tools.blast_bin_dir`, `BLAST_BIN`), else
  `docker run --rm -v ... ncbi/blast:<tag> blastp ...`.

## What has and has not been verified

| | status |
|---|---|
| command line vs. the wrapper script's source | read and matched (2026-11) |
| `psort` options (`-n/-p/-a`, `-o terse`, `-i`, `--x-skip-localization`) | confirmed from the Debian man page / USAGE file and the wrapper |
| **running it** | **never run** - the author's machine has no Docker |
| terse output layout (`SeqID`, localisation, score; tab-separated) | **assumed** from a secondary description; `parse_psortb()` accepts terse TSV (with or without header) and the "normal" per-sequence report |

## First run on a machine with Docker (10-30 min for a whole proteome)

1. `python -m vaxforge doctor` - PSORTb should show `docker (brinkmanlab/psortb_commandline:1.0.2)`.
2. `python -m vaxforge annotate runs/nmb` (re-runs only S2).
3. If S2 reports `PSORTb failed: ...` or `none parses as PSORTb output`: run the container by hand on a 3-protein FASTA, look at the file it
   writes, and either fix `annotate.psortb.command` in `config.yaml` (a list of arguments; `{docker} {image} {out} {flag}` placeholders and a bare
   `{args}` element) or send the sample to stream A so `parse_psortb()` can be corrected.
4. Spot-check a few proteins against https://psort.org/psortb/ - e.g. a known outer-membrane protein such as PorA should come out `OuterMembrane`.

No Docker at all? Run PSORTb elsewhere and import: `python -m vaxforge annotate runs/nmb --psortb-results results.txt`.

## Opentrons simulator (stream C, S9) - separate Python interpreter

Per stream C's note on the message board: `opentrons==7.5.0` needs Python 3.10 and pins `pydantic<2` and `numpy<2`, so it cannot share a
virtual environment with a pydantic-2 pipeline. S9 therefore runs the simulator as a **subprocess of its own interpreter**. Set up that
environment the way stream C documents it (Python 3.10, `opentrons==7.5.0`) and point `VAXFORGE_OT_PYTHON` at its Python:

```powershell
$env:VAXFORGE_OT_PYTHON = "C:\path\to\opentrons-venv\Scripts\python.exe"
```
```bash
export VAXFORGE_OT_PYTHON=/path/to/opentrons-venv/bin/python
```

This is stream C's setting: S0-S2 do not read it, and `vaxforge doctor` does not check it. Not verified by stream A.

## BLAST on Windows with non-ASCII paths

BLAST+ cannot open paths containing non-ASCII characters (e.g. `D:\数据\...`). `BlastRunner` therefore builds databases in an ASCII-only
folder (`%TEMP%\vaxforge\blastdb-<hash>` when the cache path is not ASCII) and stages query files there. Always go through `BlastRunner`;
do not call `blastp` directly on project paths. The same ASCII-safe folder trick is used for PSORTb's working directory.
