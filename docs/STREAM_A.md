# Stream A - backbone & data (S0, S1, S2)

Implements README stages S0-S2, the `runs/<run_id>/` artifact contract, `config.yaml`, the CLI, `make warm-cache`, and the Docker/BLAST plumbing.
Contract for other streams: [ARTIFACT_CONTRACT.md](ARTIFACT_CONTRACT.md). Fixture: `fixtures/nmb_fixture/`.

## Run it

```bash
pip install -e ".[dev]"                       # or: set PYTHONPATH=src and use python -m vaxforge ...
python -m vaxforge doctor --online            # tools, cache, network
python -m vaxforge warm-cache --pathogens "Neisseria meningitidis,SARS-CoV-2"     # the night before the demo
python -m vaxforge run --pathogen "Neisseria meningitidis" --top-k 20 --out runs/nmb
python -m vaxforge run --pathogen "Neisseria meningitidis" --out runs/nmb --offline   # demo: no network needed
python -m vaxforge verify runs/nmb ; python -m vaxforge show runs/nmb
python -m pytest                              # 80+ tests, all offline (BLAST tests need a local BLAST+)
```
`vaxforge resolve "N. meningitidis" --json` runs S0 alone. `acquire RUN_DIR` / `annotate RUN_DIR` rerun S1 / S2 alone. Set `NCBI_EMAIL` (NCBI asks for one) and optionally `NCBI_API_KEY`.

**Offline demo** (`make demo-offline`, or `python -m vaxforge demo-offline --out runs/demo --force`): copies the committed fixture and runs S3-S12 on the copy with
`--offline`. S0-S2 are the committed fixture, not a live run, and its S2 localisation is a stand-in, not PSORTb. To keep the demo offline it never uses S3's `auto`
backend (which would start the Vaxign-ML Docker image, and pull 4.9 GB the first time): by default it sets `s3.backend=scores_file` with stream B's real Vaxign-ML scores
`docs/examples/vaxign_ml_scores.nmb_fixture.tsv` (stock model, trained on all of Protegen: `holdout_enforced` is false, S3 prints a LEAKAGE RISK warning, and the Bexsero
recall on this run is not a validation); `--scores-file FILE` uses your own scores, `--rule-based` sets `s3.backend=subtractive_filter` (the rule-based fallback). It also sets
`s7.boltz.msa=empty` (the default `server` would send the sequences to an MSA server). It also attaches stream D's real earlier Adaption run to S9.5
(`finetune.evidence` / `finetune.manifest` = the two `docs/examples/s9_5_*real_4b*` files, status `evidence_attached`) and says so before the run: that run was not produced
by the demo, and its `best_win_rate` is Adaption's own target, not protocol quality; `--no-evidence` leaves S9.5 as "no fine-tune ran". `make` picks `python3` when it runs, else `python`.
`vaxforge run --out RUN_DIR --stages s7,s8` runs later stages on an existing folder without `--pathogen`; only S0 needs a target.
`--set KEY=VALUE` (repeatable, any command) overrides one config value for that run; the value is read as JSON when it parses (`true`, `3`, `"text"`), else as text.
Run from the repo root, `demo-offline` hands the stages repo-root-relative file paths (scores, Adaption evidence and manifest), so `config.resolved.json` and S3's params hold no local
folder names; from any other folder it uses absolute paths. The result of that command is committed as `demo_data/run_nmb_fixture/` (its README says what is real, fixture and stand-in).

**The closing line of `run`** says `(verify: ok)` only for a clean run. It says `(degraded: ...; verify: ok)` when S2 virulence or localisation has any status other than
`ok` / `not_applicable`, or when a later stage wrote a non-empty `payload.degraded` (a list of reason strings; stream B writes it for S3-S7): the reasons are printed
above the line under `degraded (why):`, and the line itself counts them per stage. `vaxforge doctor` also lists the
S3-S12 tools (Opentrons interpreter, Vaxign-ML image, AlgPred2, ToxinPred2, NetMHCpan, NetMHCIIpan, MHCflurry, BepiPred3, DiscoTope3, Boltz) as found / not found; it runs none of them.

## What each stage does

**S0** - plain-language name to one taxid. Tiers: exact NCBI lookup -> small alias table -> abbreviation expansion ("N. meningitidis", "Staph aureus")
-> UniProt word search verified through NCBI (finds renamed species: "Clostridium difficile" -> *Clostridioides difficile*) -> spelling suggestions.
A genus ("Neisseria") or an unconfirmed fuzzy/spelling match is **returned as a candidate list** (exit code 2, or an interactive prompt) and never
resolved silently; a failed lookup returns suggestions. The artifact records which name matched and whether it was a synonym. It also classifies the
organism (bacteria/archaea/virus/eukaryote, Gram stain) because S2 needs it.

**S1** - cache-first (`cache/`, atomic writes, hashes, UniProt release recorded; `--offline` uses only the cache). Fetches the UniProt reference proteome, a
**second strain** (S4 conservancy), VFDB set A, Protegen (all categories + the target pathogen's entries, for stream D), the Swiss-Prot **human** proteome
(S4 autoimmunity) and builds BLAST databases. IEDB is opt-in (hundreds of MB; manual drop-in supported). Optional resources that cannot be fetched are
reported with a reason, not hidden and not fatal.

**S2** - local blastp of every protein against VFDB (identity, query coverage and E-value kept, plus VFDB gene / virulence factor / category) and PSORTb 3.0
localisation. `Unknown` stays `Unknown`; *not run* is a different state from PSORTb answering Unknown.

## Decisions worth knowing

* **Content-addressed, with a `volatile` escape hatch.** The digest excludes timestamps, fetch times, cache hits and machine paths, so re-running S1 on a warm cache -
  or with `--refresh` on unchanged data - does not invalidate S2. (Tests caught this twice: first "downloaded" vs "cached", then `retrieved_at` after a live `--refresh`.)
* **Comparison strain comes from UniParc.** Found live: for *N. meningitidis* only the reference proteome (MC58) has UniProtKB entries; the other 2,942 proteomes
  UniProt lists are not downloadable from UniProtKB. S1 therefore tries UniProtKB first and falls back to UniParc by `upid`
  (headers are `>UPI... status=active`: sequences only, no protein names). Whether other pathogens behave the same was not checked.
* **Human proteome = reviewed (Swiss-Prot) only** (~20k sequences) - the full reference proteome has 263,886 entries.
* **BLAST on Windows with non-ASCII paths** (this project lives under `D:\数据\...`): BLAST+ silently builds nothing. `BlastRunner` builds databases in an ASCII folder.
* **`taxid` may be strain-level** (SARS-CoV-2); `species_taxid` is carried alongside. NCBI has since filed SARS-CoV-2 under *Betacoronavirus pandemicum*.

## Not verified / assumptions - read before the demo

* **PSORTb was never run here** (no Docker on this machine). The Docker command now matches the image project's own wrapper script (read from its source), and the
  parser is tested on hand-made samples - but nothing has run against the real container, and the terse output layout is assumed. Without PSORTb, S2 records
  `localisation.status = "not_run"` for every protein and says so loudly. See `docker/README.md` for the first-run check, or import results with `--psortb-results`.
* **Virulence thresholds are assumed** (E <= 1e-5, identity >= 30%, query coverage >= 50%; "strong" = 50% / 80%). The README specifies none. They are in `config.yaml` and in every artifact.
* **Gram class is a lineage heuristic** (`classify_organism`), following PSORTb's own notes: *Mycobacterium*/*Deinococcus* -> `positive_with_outer_membrane` (run with `-n`),
  Mollicutes (*Mycoplasma*) -> `negative_without_outer_membrane` (`-p --x-skip-localization=Cellwall`). A lineage that matches no rule is `unknown` and PSORTb is not run
  until you pass `--gram`. Override any of it with `--gram`.
* **`accessibility` mapping** (Periplasmic -> inaccessible, CytoplasmicMembrane -> partial) is judgement, configurable.
* **IEDB download URL could not be verified** (the endpoint returned HTML). Treat IEDB as manual until someone confirms the export link.
* VFDB set A currently has 4,755 sequences (README says "~4,496"); the artifact records the actual count.
* Live-tested: *N. meningitidis*, SARS-CoV-2 and the S0 edge cases against real NCBI/UniProt/VFDB/Protegen. Not live-tested: Docker modes, `--refresh`, API-key rate limits.

## Layout

```
src/vaxforge/  artifacts.py cache.py config.py http.py fasta.py tools.py uniprot.py cli.py testing.py   shared plumbing
               s0_taxonomy.py s1_acquire.py s2_annotate.py                                              the three stages
config.yaml  Makefile  pyproject.toml  docker/README.md  docs/  fixtures/nmb_fixture/  scripts/  tests/
```
Merging with the repo: copy `src/vaxforge/` (other streams add `s3_*.py ...` beside these), `config.yaml` (each stream owns its section), `fixtures/`, `docs/`, `Makefile`,
`pyproject.toml` (add your dependencies), `tests/` and `scripts/`. The repo already has `README.md`, `AGENTS.md`, `CLAUDE.md` - keep those.
