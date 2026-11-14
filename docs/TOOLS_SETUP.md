# External tools for S3 - S7: setup, licences, and what is verified

Nothing in this folder runs real predictors by itself: the Python code calls the tools below. **Every tool is optional at import
time**; if one is missing the corresponding stage block is recorded as `unavailable` (or, for S3 only, falls back to a labelled
rule-based ranker). Nothing falls back silently.

Status labels used below:
* **RUN** = executed for real on 2026-11-14 (macOS arm64, 16 GB) on N. meningitidis MC58 data, results inspected (`tests/stream_b/live/real_smoke.py`).
* **Verified (docs)** = checked against the tool's own README / source / documentation page, **not run**.
* **UNVERIFIED** = written from memory or inference, not run — see the checklist in section 3.

## 0. What has actually been run (2026-11-14)

| Tool | Version | Where | Result |
|---|---|---|---|
| BLAST+ `blastp`/`makeblastdb` | 2.17.0 | project-local conda env `.envs/blast` (`conda create -p .envs/blast -c conda-forge -c bioconda blast`, ~640 MB) | **RUN** — S4 human-homology and conservancy gates on the real human Swiss-Prot (20,414 seqs) and N. meningitidis proteomes |
| ToxinPred2 | 1.1 (pip) | `.venv` | **RUN** — header `ID,Sequence,ML_Score,Prediction`; works with current scikit-learn/numpy (ONNX model) |
| AlgPred2 | 1.4 (pip) | **own env** `.venvs/algpred2` | **RUN** — fails in a normal env (`ValueError: node array from the pickle has an incompatible dtype`): its pickled random forest needs `scikit-learn==1.2.2`, `numpy<2`, `pandas<2.2`. Point `tools.algpred2` at `.venvs/algpred2/bin/algpred2`. `-m 1` = AAC random forest (header `ID,Sequence,ML_Score,Prediction`), `-m 2` = hybrid (header `Subject,ML Score,MERCI Score,BLAST Score,Hybrid Score,Prediction`, hybrid score can exceed 1). The pip `--help` mislabels `-m` ("1: Allergen, 2: Non-Allergen"); observed behaviour matches the standalone README |
| MHCflurry | 2.3.28 (pip, `mhcflurry downloads fetch models_class1_presentation`, 160 MB) | `.venv` | **RUN** — all 27 panel alleles supported; `presentation_percentile` column exists; ~2 min for 21 proteins x 27 alleles x lengths 8-11 on CPU |
| BepiPred-3.0 | git `UberClifford/BepiPred-3.0` (clone in `.tools/`, 122 MB) | own env `.venvs/bepipred` (Python 3.10, torch 2.14 CPU, `numpy==1.22`, `pandas==1.4.3`, `fair-esm==1.0.3`; skip the pinned `bp3` pip line, the clone's `bp3/` package is used) + ESM-2 650M weights (2.5 GB in `~/.cache/torch/hub`) | **RUN** — real linear B-cell scores for MC58 candidates through `BepiPredRunner` and S5; ~22 s for 3 proteins on CPU |
| Boltz-2 | 2.2.1 (pip) | own env `.venvs/boltz` (Python 3.11) + weights in `~/.boltz` (**7.6 GB**: `boltz2_conf.ckpt` 2.3 GB, `boltz2_aff.ckpt` 2.1 GB — the affinity model, unused by us — and the CCD `mols` library 1.9 GB tar + extracted dir) | **RUN** — real CPU folds of 65-154 aa MC58 proteins through `BoltzRunner` and S7; `msa: empty` (offline) |
| Colima 0.10.3 + Docker CLI 29.9.3 | Homebrew (`brew install colima docker`), VM started with `colima start --vm-type vz --vz-rosetta --cpu 4 --memory 8 --disk 30` | CLI-only Docker runtime (no Docker Desktop, no admin password); Rosetta lets Intel-only images run on Apple Silicon | **RUN** — `docker run --platform linux/amd64 alpine uname -m` prints `x86_64`. Stop it with `colima stop` (the VM keeps 4 CPUs / 8 GB reserved while running). The default Colima mount of `$HOME` was writable, so run directories under the project folder work; run dirs under `/tmp` are NOT visible to the VM |
| **Vaxign-ML** image `e4ong1031/vaxign-ml:latest` | amd64-only, 1.4 GB download, **4.9 GB on disk** | `docker pull --platform linux/amd64 …` | **RUN** through `VaxignMLBackend` / S3: 40 proteins in 61 s; result file `<stem>.result.tsv` (`sample`, `prediction`, `protegenicity` as a 0-100 percentile) parsed as expected; image digest recorded; works without `--platform` under Colima |
| **PSORTb 3.0** image `brinkmanlab/psortb_commandline:1.0.2` (Stream A's S2) | amd64-only, **3.65 GB on disk** | `docker pull --platform linux/amd64 …` | **RUN** — 4 proteins in 6 s. Output `SeqID<TAB>Localization<TAB>Score` (note a trailing space after the SeqID). PorA = OuterMembrane 10.00, GroEL / EF-Tu = Cytoplasmic, fHbp = **Unknown 2.50**. **Stream A's `parse_psortb()` and `PsortbRunner` both work on the real output** (their docs said "never run") |
| Opentrons simulator | 7.5.0 (pip) | own env `.venvs/opentrons` (Python 3.10) | **RUN** — a good protocol simulates, a bad one (`aspirate 500 uL` into a 300 uL pipette) is rejected with a precise error; examples in `tests/stream_b/live/opentrons/` |
| UniProt REST (proteomes, FASTA stream, UniParc), AlphaFold DB API | live | network | **RUN** — see section 2 for the surprises |
| IEDB Population Coverage 3.0.2 bundle (0.8 MB) | live | network | **RUN** — source read; algorithm ported as a test oracle; data pickle read with a restricted unpickler (only `collections.OrderedDict` allowed). 3,245 alleles, 239/274 populations (class I / II). **Nothing from the archive was executed.** |

**Not installed / not run:** NetMHCpan and NetMHCIIpan (DTU licence form), ESMFold (API deliberately not called: it would send sequences to a third party), ColabFold. Their wrappers remain at "Verified (docs)" / UNVERIFIED. Disk used by everything installed so far is roughly 13 GB (`.venv` 1.4 GB, `.venvs` ~3 GB incl. torch, `.envs` 0.6 GB, `.tools` 0.1 GB, `~/.boltz` 7.6 GB, `~/.cache/torch` 2.5 GB, MHCflurry models 0.2 GB).

| Stage | Tool | Role | Config key | How we call it |
|---|---|---|---|---|
| S3 | Vaxign-ML (Docker `e4ong1031/vaxign-ml:latest`) | protegenicity | `s3.vaxign_ml.*` | `docker run --rm -v … -v <out>/_FEATURE/PSORTB:/tmp/results IMAGE python3.6 VaxignML.py -i IN -o OUT -t gram-\|gram+\|virus [-s MODELDIR] [-p N]` |
| S4 | BLAST+ (`blastp`, `makeblastdb`) or Docker `ncbi/blast` | human homology, conservancy | `tools.blast_mode`, `tools.blast_docker_image` | `-outfmt "6 qseqid sseqid pident length qlen slen qstart qend sstart send evalue bitscore qseq sseq"` |
| S4 | AlgPred 2.0 | allergenicity | `tools.algpred2` | `algpred2 -i in.fa -o out.csv -m 1 -t T -d 2` |
| S4 | ToxinPred2 | toxicity | `tools.toxinpred2` | `toxinpred2 -i in.fa -o out.csv -m 1 -t T -d 2` |
| S5 | NetMHCpan-4.1 | class I | `tools.netmhcpan` | `netMHCpan -a HLA-A02:01,… -f in.fsa -l 8,9,10,11` |
| S5 | NetMHCIIpan | class II | `tools.netmhciipan` | `netMHCIIpan -a DRB1_0101,… -f in.fsa -length 15` |
| S5 | MHCflurry (optional class I stand-in) | class I (`fidelity: alternative`) | auto if importable | Python API `Class1PresentationPredictor.predict(peptides, alleles=[a])` |
| S5 | BepiPred-3.0 | linear B-cell | `tools.bepipred3` | `python bepipred3_CLI.py -i in.fasta -o OUT -pred vt_pred -t 0.1512 [-esm_dir D]` |
| S5/S7 | DiscoTope-3.0 | conformational B-cell | `tools.discotope3` | `python discotope3/main.py --pdb_or_zip_file X.pdb --struc_type alphafold --out_dir OUT` |
| S7 | Boltz-2 | structure | `tools.boltz` | `boltz predict input.yaml --out_dir OUT --accelerator auto→gpu\|cpu --recycling_steps 3 --sampling_steps 200 --diffusion_samples 1 --output_format pdb [--use_msa_server]` |
| S7 | ESMFold (API or local `transformers`) | refolder #1 | `s7.esmfold.mode` | POST sequence to the ESM Atlas API, or `EsmForProteinFolding.infer_pdb` |
| S7 | ColabFold `colabfold_batch` | refolder #2 (AF2-no-MSA) | `tools.colabfold_batch` | `colabfold_batch --msa-mode single_sequence --num-models 1 --num-recycle 3 in.fasta OUT` |

`tools.*` values may be a command string with arguments (e.g. `"python /opt/BepiPred-3.0/bepipred3_CLI.py"`) or a path; `None` = look on `PATH`.

## 1. Per-tool notes

### Vaxign-ML (S3)  — **RUN** (Colima + Rosetta, 2026-11-14)
* **Verified** from `VIOLINet/Vaxign-ML-docker`: image `e4ong1031/vaxign-ml:latest`; `VaxignML.sh IN OUT TYPE [MODEL_DIR]`; organism type must be
  `gram-`, `gram+` or `virus`; result file `<input-stem>.result.tsv` with columns `sample, prediction, protegenicity`.
* **`protegenicity` is a percentile (0-100)** of the model score against the training-score distribution (`scipy.stats.percentileofscore`),
  not a probability. S3 divides by 100 so the README's 0.90 / 0.85 thresholds are the 90th / 85th percentile.
* **The container swallows its own exceptions and exits 0** (`except: print(sys.exc_info())`). S3 therefore treats a missing result file as failure and surfaces the container output.
* The image runs PSORTb, SPAAN, SignalP, TMHMM etc. internally (S3 does not reuse S2's PSORTb) and is `linux/amd64` only (confirmed). On Apple Silicon it ran under Colima's Rosetta without any `--platform` flag (**RUN**); if your Docker complains about the platform, pass `--platform linux/amd64` via `s3.vaxign_ml.extra_docker_args`.
* **Real result on Stream A's 40-protein fixture** (pretrained, i.e. leaky model): PorA 0.984, NadA 0.978, NHBA 0.976, fHbp 0.954 protegenicity; 24 of 40 pass 0.90 and 29 pass 0.85. These high scores are expected *because* the pretrained model was trained on Protegen, which contains the Bexsero antigens — see `leakage_audit`; this is not validation.
* The original wrapper uses `sudo docker`; set `s3.vaxign_ml.use_sudo: true` if your Docker needs it.
* **Custom / LOPOV model:** `Train.sh POS_FASTA NEG_FASTA OUT_MODEL_DIR TYPE [PROCS]`, then `s3.vaxign_ml.saved_model_dir`. S3 reads an optional `holdout.json` there (see the contract).
* Licence of the image/model: the repo is GPL-3.0 (LICENSE file); check before redistributing the model.
* Results are cached in `cache/vaxign_ml/<hash>/` (keyed on FASTA + organism + image + model dir).

### BLAST+ (S4)  — **RUN** (local binaries)
* No NCBI URL API anywhere (README S1). Local binaries (**RUN**) or `docker run ncbi/blast blastp …` (**UNVERIFIED**: image entrypoint behaviour; if it differs, install BLAST+ locally).
* Put the binaries on `PATH` (e.g. `PATH=$PWD/.envs/blast/bin:$PATH`); `tests/stream_b/live/real_smoke.py` does this.
* BLAST databases are built once per FASTA under `cache/blastdb/`, keyed on the file's path, size and mtime (a changed FASTA never reuses a stale DB).

### AlgPred 2.0 / ToxinPred2 (S4)  — **RUN** (see section 0 for the AlgPred2 environment trap)
* The pip console scripts accept `-i -o -t -m {1,2} -d {1,2}` exactly as the standalone README says (**RUN**). ToxinPred2's standalone zip needs a separately downloaded `RF_model.zip`; the pip package did not.
* The parser is header-driven and skips banner lines ("Please cite …"): prefers `Hybrid Score`, then `ML Score`, then any `*score*`; the first column is the id (`ID` or `Subject`). Verified against the real headers above.
* We apply the README thresholds (0.45 / 0.80) ourselves to the numeric score; the tool's own `-t` only affects its label.
* If the tool crashes (e.g. the sklearn mismatch) the gate is recorded `unavailable` with the tool's own error text, never `pass`.
* **Observed:** with AlgPred2 `-m 1` at 0.45, NadA (a Bexsero antigen) scores 0.596 and is rejected; fHbp 0.334 and NHBA 0.384 pass.
* `-m 1` (amino-acid-composition RF) is the default; `-m 2` (hybrid with BLAST + MERCI) needs those helpers installed.

### NetMHCpan / NetMHCIIpan (S5)
* **Licence:** DTU Health Tech software — obtain from DTU under their licence. The project README says current API/licence terms could not be confirmed, so we only drive **local** installs, never DTU's web servers.
* **Verified** (NetMHCpan-4.1 instructions page): `Pos` is 0-based; columns include `Core, Of, Gp, Gl, Ip, Il, Icore, Identity, Score_EL, %Rank` and `BindLevel` (`<= SB`/`<= WB`); defaults strong < 0.5 %, weak < 2 %.
* **UNVERIFIED:** exact CLI flag spellings for your installed version (`-a`, `-f`, `-l` for class I; `-length` for class II) and the allele spelling (`HLA-A02:01`, `DRB1_0101`, `HLA-DQA10501-DQB10201`). The parser is header-driven and the runner raises if any requested allele returns no rows, so a wrong spelling fails loudly instead of silently shrinking the panel.
* Class II uses 15-mers and is down-weighted (0.3). Default panels are the IEDB reference panels (27 class I, 26 class II) *as recalled from the literature* — verify before quoting.

### MHCflurry (S5, optional)  — **RUN**
* Open-source class I predictor; used only if NetMHCpan is absent. `pip install "mhcflurry>=2.3,<2.4"` then `mhcflurry downloads fetch models_class1_presentation`. Outputs are labelled `alternative`.
* `predict(peptides, alleles=[a])` returns `presentation_percentile` (**RUN**), which we use as `%Rank`. It reports **no binding core**, so S5 groups lengths 8-11 by start position (`Binding.group`); grouping by peptide would have counted every length separately (4x inflation seen on the first real run).

### BepiPred-3.0 (S5)  — **RUN**
* **Licence (verified):** free for academic, non-commercial use; a separate licence is needed for any for-profit use (contact in the repo README).
* **RUN:** `python bepipred3_CLI.py -i in.fasta -o OUT -pred vt_pred -t 0.1512 -esm_dir DIR` writes `raw_output.csv` with header `Accession,Residue,BepiPred-3.0 score,BepiPred-3.0 linear epitope score` and one row per residue (exactly as parsed).
* **The README's "truncates above 1,023 aa" did NOT reproduce.** The local CLI returned all 1,144 rows for a 1,144-residue protein (Q9JXZ2) with no warning; the code encodes the whole sequence with ESM-2, which uses rotary embeddings (no hard length cap). The "Outputting predictions up till N position" branch exists in the source but never fired. The limit may apply to DTU's web server, or to other versions.
  We still window long proteins (1000 aa, 100 overlap) because ESM-2 was trained on <= 1024-token crops, and still verify per-window row counts. **Measured effect on Q9JXZ2:** chunked vs unchunked scores correlate r = 0.989 (max abs difference 0.06) and give the same number of regions (10 vs 10), so chunking is harmless; set `s5.bcell.chunk_size` high (e.g. 2000) if you prefer to send whole sequences.
* First run downloads ESM-2 (`esm2_t33_650M_UR50D` + contact-regression file, ~2.5 GB) into `~/.cache/torch/hub`. The CLI writes per-sequence ESM encodings into `-esm_dir` (we pass `s5.bcell.esm_dir`; the default would be inside the BepiPred clone).
* Needs its own environment (old pins: `numpy==1.22`, `pandas==1.4.3`, `plotly==5.8.0`, `fair-esm==1.0.3`) on Python 3.10; point `tools.bepipred3` at `"<env>/bin/python <clone>/bepipred3_CLI.py"`. The CLI also builds an interactive HTML figure per run (`output_interactive_figures.html`), which we ignore.

### DiscoTope-3.0 (S5, S7)  — **RUN** (env `.venvs/discotope`, Python 3.12; clone in `.tools/DiscoTope-3.0`; `unzip models.zip` inside the clone; `uv pip install -e .tools/DiscoTope-3.0` is needed so `import discotope3` works; ESM-IF1 weights ~1.6 GB land in `~/.cache/torch`)
* **RUN:** on the AlphaFold DB model of fHbp: 274 residues scored in ~14 s on CPU, 56 residues flagged by the tool's own `epitope` column. Real CSV header: `pdb,chain,res_id,residue,DiscoTope-3.0_score,calibrated_score,epitope,rsa,pLDDTs,length,alphafold_struc_flag`. The tool's default `--models_dir` is the relative path `models/`, so it fails outside its own checkout; the wrapper now passes `--models_dir` and the checkout as the working directory.
* **Verified:** README usage and the per-chain CSV header `pdb,res_id,residue,DiscoTope-3.0_score,rsa,pLDDTs,length,alphafold_struc_flag`; threshold guide low 0.40 / moderate 0.90 / higher 1.50 on the *calibrated* score; `--struc_type alphafold` expects AF2-style pLDDT in the B-factor column (S7 rescales Boltz B-factors to 0-100 before calling).
* Licence: the README says "Creative Commons license found in the LICENSE file" — read it for non-commercial terms.
* The `epitope` boolean is what we use; `calibrated_score` (not the raw score) is thresholded only if a version omits it.

### Boltz-2 (S7)  — **RUN** (CPU, offline single-sequence mode)
* **RUN:** `boltz predict input.yaml --out_dir OUT --accelerator cpu --recycling_steps 1 --sampling_steps 50 --diffusion_samples 1 --output_format pdb` with our YAML (`version: 1`, one protein chain, `msa: empty`) works; outputs `*_model_0.pdb` and `confidence_*_model_0.json` (`confidence_score`, `ptm`, `complex_plddt`, ... in 0-1) are found and parsed. The PDB B-factor column holds **pLDDT on a 0-100 scale** (S7's scale detection reports `0-100`).
* **Weights are big:** the first `boltz predict` downloaded **7.6 GB** into `~/.boltz` (conf 2.3 GB, **affinity 2.1 GB which we never use**, CCD molecule library 1.9 GB as a tar plus the extracted `mols/` directory). Plan disk accordingly.
* **Speed (Apple Silicon CPU, 16 GB):** a 90-residue fold took 545 s *including* the weight download; the three 65-154 aa folds of the S7 run took about 10 minutes in total. Every `boltz` start also spends ~30-60 s importing torch. CPU is fine for a demo of 2-3 short candidates, not for 20.
* **Quality caveat:** with `msa: empty` (needed to stay offline) the models are low-confidence: mean pLDDT 38-67 for the MC58 proteins tried. In that mode S7's `structure_term` mostly ranks noise; use `s7.boltz.msa: server` (sends the sequence to the ColabFold MMseqs2 server) or supply MSAs for a meaningful structure signal.
* **`boltz --version` does not exist** (it prints usage text; an early version of our wrapper recorded that text as the "version"). The wrapper now reads the version from the installed package (`2.2.1`) and ignores non-zero-exit output everywhere. The version is part of the cache key, so upgrading Boltz re-folds.
* **Affinity module: protein-small-molecule only** (docs) — so S7 emits **no** affinity for antigens.
* **Off-machine:** `s7.boltz.msa: server` (default) passes `--use_msa_server`, sending each candidate sequence to the ColabFold MMseqs2 server. `real_smoke.py` defaults to `empty`.
* No documented seed option, so runs are not seed-reproducible; results are cached in `cache/boltz/<hash>/`.
* AlphaFold 3 is never used (config validation rejects any other engine).

### ESMFold / ColabFold (S7 refolders)
* **Local by default (no network).** `s7.esmfold.mode: local` needs `transformers` + `torch` and ~8 GB of weights (`infer_pdb` **UNVERIFIED**); if they are missing the refolder is reported unavailable with that reason. **Opt in** to the public ESM Atlas web service with `s7.esmfold.mode: api` — it POSTs the sequence to a third-party server (limit 400 aa in our guard; **endpoint UNVERIFIED**).
* `colabfold_batch --msa-mode single_sequence …` is the README's "AF2-no-MSA". Flags **UNVERIFIED**; install LocalColabFold separately.
* Validator results are never merged silently: each refolder's lDDT-Cα / TM-score / RMSD vs the Boltz-2 model is reported, plus the spread between validators.

## 2. Reference data downloads (cache-first; need network once)

**RUN** on 2026-11-14 (`VAXFORGE_LIVE=1 pytest tests/stream_b/live` for the two small endpoint checks; the full downloads via `tests/stream_b/live/real_smoke.py`).

**Surprise found on real data:** UniProt only keeps UniProtKB entries for *Reference* proteomes. All "Non Reference proteome" strains (almost every other N. meningitidis strain) return **HTTP 200 with an empty body** from `uniprotkb/stream`, even though `proteomes/search` lists them with protein counts. The first version of this code cached that empty file and the conservancy gate would have compared against an empty database. Now: empty bodies are never cached, `proteome_fasta` falls back to **UniParc** (`uniparc/stream?query=upid:<UPID>`, **RUN**: 1,913 sequences for UP000000626), and `strain_proteomes` skips any proteome that yields nothing.

| Item | Source | Size note |
|---|---|---|
| Human Swiss-Prot proteome | `rest.uniprot.org/uniprotkb/stream?query=(proteome:UP000005640) AND (reviewed:true)&format=fasta&compressed=true` | ~tens of MB (UniProt returned HTTP 429 once during testing; the client retries with back-off) |
| Other strain proteomes | UniProt proteome search by `species_taxid`, then `…uniprotkb/stream?query=proteome:<UPID>` (Reference proteomes) or `…uniparc/stream?query=upid:<UPID>` (everything else) | ~1-2 MB each |
| HLA allele frequencies | IEDB Population Coverage 3.0.2 tarball (`downloads.iedb.org/tools/population/LATEST/…`, 0.8 MB); only `population_genotype_map.p` is read, in memory, with an OrderedDict-only unpickler; written as the IEDB user-population TSV | cached in `cache/refdata/afnd/` |
| AlphaFold DB models | `alphafold.ebi.ac.uk/api/prediction/<accession>` → `pdbUrl` (host-checked) | ~100-500 KB each |

Pre-warm before the demo by running S4/S5 once online; afterwards set `refdata.offline: true` and the stages never touch the network.

## 3. First-contact checklist for the UNVERIFIED items

Run each tool once by hand on a short sequence and compare with what the parsers expect:

1. `netMHCpan -a HLA-A02:01 -f x.fsa -l 9` → header contains `Pos … Identity Score_EL %Rank_EL …`; `Pos` starts at 0. Then `netMHCpan -listMHC | grep A02:01`.
2. `netMHCIIpan -a DRB1_0101 -f x.fsa -length 15` and `-a HLA-DQA10501-DQB10201` → rows appear (else fix the spelling in `hla.to_netmhciipan`).
3. ~~algpred2 / toxinpred2~~ — done (section 0).
4. `python bepipred3_CLI.py -i long.fasta -o out -pred vt_pred` with a >1100 aa sequence → confirm the window limit and that `raw_output.csv` has one row per residue for a 1000-aa window.
5. `boltz predict` on one short sequence with `--output_format pdb` → confirm `*_model_0.pdb` lands under `OUT/` and that the B-factor column is pLDDT (0-1 or 0-100: S7 auto-detects).
6. ~~mhcflurry~~ — done (section 0).
7. Docker: `docker run --rm ncbi/blast blastp -version` (only needed if you want BLAST in Docker; local BLAST+ is already verified).
8. Vaxign-ML: `docker pull e4ong1031/vaxign-ml:latest` then run `VaxignML.sh` on a 20-protein FASTA; compare `<stem>.result.tsv` with S3's parser expectations.

Until these are ticked, treat results from the corresponding wrappers as unvalidated.
