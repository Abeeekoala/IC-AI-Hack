# Stream B contract: S3 - S7 ("Prediction core")

Owner per `AGENTS.md` on branch `agents-work-breakdown`: `@benjamin-codes-labs`
Files: `src/vaxforge/s3_rank.py`, `s4_filter.py`, `s5_epitopes.py`, `s6_coverage.py`, `s7_structure.py`
plus the stream-B-private helper package `src/vaxforge/prediction/` (nothing outside stream B imports it).

Rule from `AGENTS.md`: *read and write artifacts; don't import another stream's module.* Everything below is
therefore a JSON contract, not a Python API.

> **Status: A's `runs/<run_id>/` contract had not landed when this was written.** Section 2 is what S3
> *assumes* from A. It is isolated in `prediction/schema.py` (`_ALIASES`) so reconciling is a one-file change.

---

## 1. Files and envelope

All five stages write through stream A's `RunStore` (see `docs/ARTIFACT_CONTRACT.md`): `runs/<run_id>/<stage module>.json`, one `run(store, config)` per stage module. In store mode `run()` returns the artifact's path. Sidecar files go under `runs/<run_id>/data/s5|s6|s7/` and the payload refers to them with run-folder file references `{root: "run", path, sha256, bytes}`.

| Stage | Declared `inputs` | Reads | Writes |
|---|---|---|---|
| S3 | `s2` | `s2_annotate.json` (sequences from the proteome FASTA its file reference points to) | `s3_rank.json` |
| S4 | `s3`, `s1` | `s3_rank.json`, S1's proteomes and BLAST databases | `s4_filter.json` |
| S5 | `s4` | `s4_filter.json` | `s5_epitopes.json`, `data/s5/<id>_tcell.tsv`, `data/s5/<id>_bepipred.tsv` |
| S6 | `s5` | `s5_epitopes.json` + HLA frequency table | `s6_coverage.json`, `data/s6/iedb_epitope_allele.tsv` |
| S7 | `s6` | `s6_coverage.json` (S5's results reach S7 through S6) | `s7_structure.json`, `data/s7/<id>/{boltz2,esmfold,af2_no_msa}.pdb`, `boltz2_confidence.json` |

The declared `inputs` are what `vaxforge verify` compares: re-running a stage changes its digest and flags every stage that lists it as `STALE`. They are set in `prediction/artifacts.py` (`DECLARED_INPUTS`), not left to the `RunStore` defaults (its default for `s7` is `s4`, which left S7 un-flagged after S6 changed).
Each stage's output is **self-contained for the next stage** (candidates carry their sequences and evidence forward).

Envelope (stream A's, same for all five; `payload` is what this document describes):

```jsonc
{
  "schema": "vaxforge/s3_rank/v1", "stage": "s3", "run_id": "nmb", "created_at": "2026-11-14T15:00:00Z",
  "environment": {"python", "platform"},
  "producer": {"module", "vaxforge_version", "tools": {"<tool name>": "<version>"}},   // name -> version string only
  "params":  { ...resolved config slice... },
  "inputs":  {"s2": "sha256:..."},                 // upstream stage -> its digest (the staleness check)
  "status": "ok", "warnings": ["..."],             // human-readable; includes LEAKAGE / DEGRADED / INCOMPLETE SCREENING banners
  "payload": { ... }, "volatile": {...}, "digest": "sha256:..."   // digest excludes created_at and volatile
}
```

* `producer.tools` holds only name -> version; a tool without a version is recorded under its backend name, and `fidelity`
  (`reference` = the tool the README names; `alternative` = open-source stand-in, only MHCflurry for class I and our own
  population-coverage implementation; `fallback` = transparent heuristic, **not** the named tool, S3 only) is NOT stored there.
  Read the fidelity and any "sent to a third-party server" statement from `warnings` and from the payload (`ranker`, `analysis`).
* Every stage writes status `ok` even when a tool was missing; what could not run is in `payload` and `warnings`, and
  `payload.degraded` (S3-S7) summarises it: a list of short reason strings, **always present, empty when nothing is degraded**.
  S3: the rule-based fallback ranker was used. S4: a gate did not run (same as `unavailable_gates`). S5: a T-cell or B-cell prediction
  did not run. S6: population coverage could not be computed. S7: no structure was folded (sequence-only ranking), a validator or
  DiscoTope-3.0 was unavailable. A run can therefore say it is degraded without parsing warnings.
* **Not computed is `null`, never 0 or `[]`.** An analysis that did not run reports `null` for its counts, lists and score
  (S5 `tcell.class_i|class_ii`: `n_epitopes`, `n_promiscuous`, `promiscuous_per_100aa`, `score`, `top`, `selected` and
  `tcell.all_epitopes_file`) and writes no empty result file. A predictor that ran and found nothing reports a real `0` / `[]`.
* A stage is skipped if its artifact already has the same params and the same upstream digests (`--force` to override);
  the tool versions that were available are not part of that comparison, so a result produced without a tool is reused after
  the tool appears unless `--force` is given (S3 is the exception: a fallback ranking is not reused once Vaxign-ML could run).
* Without a `RunStore` (a folder with no `manifest.json`, as the unit tests use) the same payloads are written in stream B's own
  envelope (`artifact_id`, `inputs` as a list, `tools` as a list of records with `backend`, `fidelity`, `sends_sequence_off_machine`).

**Ties.** `rank` is always the ordinal position (1, 2, 3, ...), so readers can rely on it. Candidates the evidence cannot tell apart
are ordered by `protein_id` only; they carry `tie_group: {size, competition_rank}` (`null` when unique) and the payload has a `ties`
summary. S3: equal composite score, protegenicity and localisation prior. S7: equal final and pre-structure score (top-k) or equal
pre-structure score and S3 position (the rest). A warning is written when a selection cut (S3 `max_candidates`, S7 `top_k`) falls
inside a tie, because which members were kept is then decided by `protein_id`, not by evidence.

Positions are **1-based inclusive** everywhere. Percentages are fractions in [0, 1].

---

## 2. What S3 assumes from stream A  (please confirm or correct)

`runs/<run_id>/s2_annotate.json` — a JSON object whose `payload.proteins` (or top-level `proteins` / `annotations` /
`records` / `rows`, or a bare array) lists one record per protein. Per record (aliases accepted, first match wins):

| Need | Accepted names | Notes |
|---|---|---|
| id | `protein_id`, `accession`, `uniprot_id`, `uniprot`, `id`, `entry` | **Must be the UniProt accession** if S5 should fetch AlphaFold DB structures; other ids are fine but skip that step |
| sequence | `sequence`, `seq`, `aa_sequence` | trailing `*` stripped |
| name / gene | `name`, `protein_name`, `description`, `product` / `gene`, `gene_name` | optional |
| taxid | `taxid`, `tax_id`, `organism_id`, `ncbi_taxid` | optional per record |
| localisation | `localisation`, `localization`, `psortb`, `subcellular_location` | string **or** `{label\|prediction\|final_localization, score}`. **`Unknown` is kept as a first-class value** and is never folded into "not surface" |
| virulence | `virulence`, `vfdb`, `vf`, `virulence_factor` | `{hit, identity, coverage, evalue}` (percent or fraction both OK). A bare boolean is ignored: README S2 requires all three numbers |

Run-level metadata (next to the list, or in `payload`) that stream B reads **if present**:

| Key | Used by | Why |
|---|---|---|
| `taxid` | S3, S4 | recorded; S4 excludes the target strain when looking for other strains |
| `species_taxid` | S4 | UniProt `taxonomy_id:` lookup of *other* strains for the conservancy gate (a strain-level taxid has no siblings) |
| `lineage` (list of names, `;`-string, or list of `{scientific_name}`) or `organism_type` (`gram-`/`gram+`/`virus`) | S3 | Vaxign-ML **requires** the organism type. If it cannot be inferred (e.g. Mycobacterium, Mollicutes, Archaea) S3 stops and asks for `s3.organism_type` instead of guessing |
| `proteome_id` / `upid` | S4 | excluded from the "other strain" list |

### Reference data S1 does not list but S4/S5/S6 need

README S1 fetches target proteome, VFDB, Protegen and IEDB. Stream B additionally needs:

| Need | Used by | Default behaviour (cache-first, `cache/refdata/`) | Config override |
|---|---|---|---|
| Human proteome | S4 autoimmunity gate | UniProt Swiss-Prot `proteome:UP000005640 AND reviewed:true` | `refdata.human_proteome` |
| 1-2 other strain proteomes | S4 conservancy gate | UniProt proteome search by `species_taxid`; FASTA from UniProtKB (Reference proteomes) **or UniParc** (all other strains — UniProtKB returns an empty body for them); proteomes yielding no sequences are skipped | `refdata.conservancy_proteomes` |
| IEDB human MHC ligands | S4 NERVE-style re-check | none (gate degrades, see S4) | `refdata.iedb_human_ligands` (one peptide per line, or `mhc_ligand_full.csv`) |
| AlphaFold DB models | S5 DiscoTope-3.0 | `alphafold.ebi.ac.uk/api/prediction/<acc>` | — |
| **HLA allele frequencies (AFND)** | S6 | built from the IEDB Population Coverage 3.0.2 bundle (3,245 alleles; restricted unpickle, nothing executed) when `refdata.afnd_frequencies` is unset and `refdata.offline` is false | `refdata.afnd_frequencies` |

**AFND table format** = the IEDB Population Coverage *user population* file (verified from the IEDB help page):
tab-separated, header `MHC class | MHC locus | MHC allele | <population columns...>`, class `I`/`II`, alleles at 2-field
resolution (`HLA-A*02:01`), values are frequencies in [0, 1]. Population columns should be named like IEDB's
(`World`, the 16 areas such as `Europe`, `East Asia`, ...). Optional `refdata.afnd_sample_sizes` (TSV `population<TAB>n`
or JSON) supplies per-population sample sizes; without it sample sizes are reported as `null` (never invented).
S1's "AFND via IEDB PopCover" item can simply call `RefData.afnd_table(populations)` (stream B's cache-first builder) or point `refdata.afnd_frequencies` at its own file.

Config keys stream B adds (all with defaults in `prediction/config.py:DEFAULTS`; the five README §5 keys are honoured verbatim):
`run.top_k`, `refdata.*`, `tools.*`, `s3.*`, `s4.*`, `s5.*`, `s6.*`, `s7.*`.

---

## 3. S3 `s3_rank.json` payload

```jsonc
{
  "context":  {"taxid", "organism_type", "s2_meta": {...passthrough...}, "n_proteins"},
  "ranker":   {"backend": "vaxign_ml|scores_file|subtractive_filter", "score_semantics": "...",
               "weights_used": {...}, "thresholds": [0.9, 0.85]},
  "leakage_audit": {"model", "holdout_enforced": false|true|null, "declared_holdout_taxids", "target_taxid",
                    "requested_holdout", "note"},
  "counts":   {"n_proteins","n_scored","n_unscored","n_selected",
               "threshold_summary": {"0.90": {"n_pass","fraction_of_proteome","available"}, "0.85": {...}},
               "fold_enrichment": null},                       // needs ground truth -> validation harness
  "ties":     {n_groups, n_tied_candidates, largest_group: {size, competition_rank} | null, basis},
  "candidates": [ {protein_id, name, gene, length, rank, tie_group, composite_score, protegenicity, protegenicity_raw,
                   passes_thresholds: {"0.90": bool|null, "0.85": ...}, terms, evidence:{localisation, virulence},
                   selected, selected_reason, sequence, taxid} ],   // the set handed to S4, in rank order
  "ranking":    [ ...same fields, no sequence, ALL proteins... ]
}
```

* `protegenicity` is Vaxign-ML's **percentile / 100** (the container emits a 0-100 percentile of the model score against
  the training-score distribution, not a probability; raw value kept in `protegenicity_raw`).
* Both thresholds are always reported. No AUROC/AUPRC is written anywhere (README: unverified). VaxiJen is not used.
* Default weights `{protegenicity: 1, localisation: 0, virulence: 0}` reproduce the pure Vaxign-ML order; the other terms
  are recorded as evidence at weight 0. **Re-weighting hook for the validation loop:** set `s3.weights` (dict or path to a
  JSON file) and re-run.
* Selection: everything with protegenicity >= the lower threshold, padded to `s3.min_candidates`, capped at `s3.max_candidates`.

### Hooks for stream D (validation / leakage)

* **LOPOV / holdout:** run Vaxign-ML's `Train.sh` on Protegen-minus-holdout, then either
  (a) set `s3.vaxign_ml.saved_model_dir` to the trained directory — S3 reads an optional sidecar
  `<model_dir>/holdout.json` = `{"holdout_taxids": [...], "protegen_version": "..."}` and records it in `leakage_audit`; or
  (b) write a precomputed score file and set `s3.backend=scores_file`, `s3.scores_file=...` (TSV `protein_id<TAB>score`
  or JSON `{id: score}`; optional sidecar `<file>.meta.json` = `{"scale": 100, "holdout_taxids": [...], "model": "..."}`).
* The stock pretrained model sets `leakage_audit.holdout_enforced = false` and adds a `LEAKAGE RISK` warning. A custom model sets
  it to `null` ("declared, not verified") — verifying is `validation/leakage_check.py`'s job.
* With the rule-based fallback there is no trained model: `holdout_enforced = true`.

---

## 4. S4 `s4_filter.json` payload

```jsonc
{
  "context": {...}, "upstream": {"s3_ranker": {...}, "s3_leakage_audit": {...}},
  "gates": {"human_homology": {"status": "ran|unavailable|disabled", "thresholds": {...}, "reason"?}, "allergenicity": {...},
            "toxicity": {...}, "conservancy": {...}},
  "incomplete_screening": false, "unavailable_gates": [],
  "funnel": [{"gate": "input", "n": 40}, {"gate": "human_homology", "n_pass","n_flag","n_reject","n_unavailable","remaining_after_gate"}, ...],
  "counts": {"n_input","n_survivors","n_rejected","n_rescued"},
  "candidates": [ {protein_id, name, gene, length, sequence, taxid, s3: {rank, composite_score, protegenicity, passes_thresholds, terms, evidence},
                   gates: {"<gate>": {status, reason, metrics}}, gates_unavailable: [], flags: [], rescued_despite?: []} ],  // survivors, S3 order
  "rejected":   [ { ...same..., rejected_by: ["toxicity"], recoverable: true } ],
  "recovery": "..."
}
```

Gate semantics (all thresholds configurable; the README gives only allergenicity 0.45, toxicity 0.80, conservancy identity 0.80):

| Gate | Tool | Reject when | Project-chosen (not from a paper) |
|---|---|---|---|
| `human_homology` | BLAST+ blastp vs human Swiss-Prot, then exact 9-mers shared inside the alignment re-checked against IEDB human MHC ligands | a shared 9-mer is a known human MHC ligand. Homology/shared peptides without a ligand hit => `flag`. **If no ligand set is configured**, homologs (identity >= 0.35 and query coverage >= 0.50) are rejected *with a note that this may be over-aggressive* | 0.35 / 0.50 / k=9 |
| `allergenicity` | AlgPred 2.0 | score >= `thresholds.allergenicity` (0.45) | model 1 (AAC-RF) |
| `toxicity` | ToxinPred2 | score >= `thresholds.toxicity` (0.80) | model 1 |
| `conservancy` | BLAST+ blastp vs other strain proteome(s) | not conserved (identity >= `thresholds.conservancy_identity`, coverage >= 0.80) in **all** comparison strains; `s4.conservancy.on_fail: flag` keeps them as `flag` instead of `reject` | 2 strains, all, `reject` |

> **Real-data warning for the Bexsero recall@3 test (stream D).** Run on real *N. meningitidis* MC58 with BLAST+ 2.17 and AlgPred2/ToxinPred2
> (40 proteins incl. the three Bexsero antigens, 2 comparison strains): with README defaults **S4 rejects all three antigens** —
> fHbp (Q9JXV4) and NHBA (Q7DD37) by *conservancy* (best identity 71-74 % / 70-84 % vs the other strains: they are variant-group antigens),
> NadA (Q9JXK7) by *conservancy* (no homolog in the comparison strains) **and** *allergenicity* (AlgPred2 ML score 0.596 >= 0.45).
> Measure recall@3 on S3's ranking *and* separately on S4 survivors, or run with `s4.conservancy.on_fail: flag` (fHbp, NHBA then survive flagged;
> NadA still falls to the allergenicity gate unless added to `s4.rescue`). Housekeeping proteins GroEL / EF-Tu / AtpA were correctly rejected by
> the human-homology gate (52-57 % identity, 97-99 % coverage).

* **Nothing is dropped silently**: every rejection stays in `rejected` with all gate evidence. `s4.rescue: [ids]` forces a keep
  (recorded as `rescued_despite`).
* **A gate that cannot run is `unavailable`, never `pass`**; candidates carry `gates_unavailable`, the artifact sets
  `incomplete_screening: true` and a warning. `s4.on_unavailable: fail` turns that into an error instead.

---

## 5. S5 `s5_epitopes.json` payload

```jsonc
{
  "context": {...}, "skipped_candidates": [{protein_id, reason}],
  "analysis_status": {"class_i": {"status": "ran|unavailable|disabled", "tool","fidelity","panel","thresholds",...},
                      "class_ii": {... "weight": 0.3}, "bcell_linear": {...}, "conformational": {...}},
  "upstream": {s4_incomplete_screening, s4_unavailable_gates, s3_ranker, s3_leakage_audit},
  "candidates": [{
      protein_id, name, gene, length, sequence, taxid, upstream: {s3, s4},
      tcell: {score, score_basis, class_i: {n_epitopes, n_promiscuous, score, top[<=50], selected[ids]},
              class_ii: {...}, all_epitopes_file, all_epitopes_sha256},
      bcell: {linear: {n_residues, n_chunks, chunked, threshold, top20pct_mean_score, regions[<=20], per_residue_file, ...},
              linear_score, conformational: {status, ...DiscoTope summary...}} }],
  "epitope_set": {
      "t_cell": [{epitope_id, protein_id, class: "I|II", peptide, core, start, end, alleles[], n_alleles, best_rank}],
      "b_cell_linear": [{protein_id, start, end, peptide, mean_score, max_score}] },
  "limits": [...]
}
```

* T-cell epitopes are collapsed by **9-mer binding core** (different peptide lengths over the same core are one epitope).
  Binder = `%Rank_EL` below the weak threshold (class I 2 %, class II 5 %; strong 0.5 % / 1 %).
* `tcell.score` = `d / (d + half_score_density)` with `d` = epitopes **strongly** bound (`%Rank` < 0.5 % / 1 %) by >= 3 panel alleles per 100 aa; class II is down-weighted:
  `(1.0*s_I + 0.3*s_II) / 1.3`. Strictly monotone, so candidates never tie (a first version capped at 1.0 and gave all 21 real candidates exactly 1.0).
  Each class block also reports `promiscuous_per_100aa`, `random_expectation_per_100aa` and `enrichment_vs_random` (a rough independent-alleles null; real MC58 proteins sat ~3-11x above it). It is an **unvalidated heuristic summary** (see `score_note`); the epitope lists are the real output.
* Epitopes are grouped by 9-mer core (NetMHCpan/NetMHCIIpan). MHCflurry reports no core, so its lengths 8-11 are grouped by start position (`epitope_id` = `<protein>|I|<start>:<peptide>`).
* `epitope_set.t_cell` is exactly the per-candidate *selected* epitopes (<= 8 class I + <= 6 class II, near-duplicate cores
  removed). It feeds S6 **and is the peptide pool for S10's ELISpot readout**.
* BepiPred: long proteins are windowed (1000 aa, 100 overlap), per-chunk row counts are verified (a real run showed no truncation at 1,144 aa, but the check stays),
  and scores merged by centrality. Per-residue scores are in the sidecar.
* DiscoTope-3.0 runs on AlphaFold DB models only when the model's sequence is identical to the candidate's.
* Missing tool => that block is `unavailable` with a reason. No heuristic is substituted for NetMHCIIpan, BepiPred or DiscoTope
  (BepiPred-2.0 is never used). Class I may use MHCflurry (`fidelity: alternative`, with a warning).

## 6. S6 `s6_coverage.json` payload

```jsonc
{
  "context": {...},
  "analysis": {"status": "ran|unavailable", "reason"?, "table": {...}, "modes": ["class_i","class_ii","combined"],
               "populations": [...], "score_population": "World", "independence_assumption": "..."},
  "set_level": {
      "n_epitopes": 40,
      "by_population": {"Europe": {"combined": {coverage, average_hits, pc90, n_epitopes, n_epitopes_covered_by_table,
                                               unmapped_alleles, approximations, sample_size}, "class_i": {...}, "class_ii": {...}}, ...},
      "fairness": {"available": true, "reference_population": "Europe", "budget_k", "mode", "reference_set": [...],
                   "rows": [{population, coverage_with_reference_optimised_set, coverage_with_own_optimised_set, coverage_lost, sample_size}],
                   "mean_coverage_lost_excluding_reference", "max_coverage_lost", "note"},
      "iedb_crosscheck_file": {"file": {"root": "run", "path": "data/s6/iedb_epitope_allele.tsv", "sha256", "bytes"}, "use"} },
  "candidates": [ ...S5 candidate (bulky lists removed)..., "coverage": {score, score_population, mode_used, n_selected_epitopes,
                   by_mode_at_score_population, by_population: {pop: coverage}, min_population} ],
  "epitope_set": {...passthrough...}, "limits": [...]
}
```

Coverage maths are our own implementation of the IEDB method, written after **reading the official tool's source** (v3.0.2) and tested
against a line-by-line port of it on 25 random scenarios (coverage, average hits and PC90 agree to 1e-9). Facts from that source:
per-locus Hardy-Weinberg draws; **loci are convolved, i.e. treated as independent** (the IEDB *docs* are silent on this, the *code* does it,
and so do we — LD is ignored); a heterozygote's hits are the sum over its two alleles but a **homozygote's hits count once**;
frequencies > 1 are renormalised and < 1 get an `UNKNOWN` allele; **`pc90` is a linearly interpolated real number** (0.18 when coverage is
below 90 %), reported as `pc90`, with the integer definition kept as `pc90_integer`. The IEDB tool matches allele names as exact strings and
does not understand DQ/DP heterodimers; we look a heterodimer up exactly and otherwise use its beta chain (`approximations`), and the exported
`data/s6/iedb_epitope_allele.tsv` uses the same mapping so the official tool reproduces our numbers. The IEDB data has **no DRB3/DRB4/DRB5**, so
those panel alleles are reported in `unmapped_alleles` and add nothing.
`set_level.fairness_by_budget` repeats the fairness analysis for budgets 1, 2, 3, 5, 10: on real data the Europe-vs-own gap is real only at
k=1 (West Africa -9 points, Central Africa -13) and closes by k=3 because a pool of ~200 promiscuous epitopes saturates the 27-allele panel.
`min_locus_frequency_mass` / `set_level.sparse_data_populations` flag populations whose source frequencies are sparse (Central America:
coverage 0.078 with every panel allele — missing data, not a bad epitope set).

## 7. S7 `s7_structure.json` payload  (final coarse ranking for S8-S11)

```jsonc
{
  "context": {...}, "top_k": 20,
  "selection": {"basis", "pre_structure_weights", "top_k_ids": [...]},
  "analysis": {"folding": {status, tool, version, msa_mode, accelerator, seed_controlled: false},
               "validators": {"esmfold": {...}, "af2-no-msa": {...}}, "degraded_to_sequence_only": false,
               "affinity": {"status": "not_implemented", "reason": "..."}, "docking": {"status": "not_implemented", ...},
               "validator_policy": "..."},
  "final_candidates": ["P12345", ...],            // top-k, final order  <-- S8 / S10 / S11 input
  "candidates": [{                                // ALL candidates; top-k first
      ...S6 candidate fields..., rank, in_top_k, rank_basis: "structure_informed|pre_structure",
      scores: {pre_structure, final, terms_raw, terms_percentile, terms_used, terms_missing, weights, percentile_basis},
      structure: {status: "ran|failed|skipped|unavailable|not_selected", engine, model_file {root:"run", path, sha256, bytes},
                  mean_plddt, frac_*, reported_confidence, sequence_matches_input,
                  validators: {"esmfold": {lddt_ca_vs_boltz, tm_score_vs_boltz, ca_rmsd_vs_boltz, mean_plddt, model_file}, "af2-no-msa": {...}},
                  validator_agreement: {value, n_validators, spread, note}, structure_term, structure_term_basis,
                  conformational_epitopes: {status, n_epitope_residues, epitope_segments, fraction_epitope_residues_below_floor, ...}} }],
  "epitope_set": {...}, "set_level_coverage": {...}, "upstream": {...}, "limits": [...]
}
```

* `final_score` = weighted mean of **percentile-ranked** terms (`s3, tcell, bcell, coverage, structure`; weights in `s7.final_weights`
  are unvalidated priors for the validation harness to re-tune). Raw values are kept beside the percentiles.
* Structure confidence (pLDDT, validator agreement) measures the **model**, not immunogenicity.
* **No affinity and no docking number is produced** (Boltz-2's affinity head supports protein-small-molecule binding only; README
  §7 removes ClusPro-TLR).
* Boltz-2 / ESMFold-API usage that sends the sequence off the machine is flagged on the tool record and in `warnings`.
* Without Boltz-2 the stage still runs and ranks the top-k sequence-only (`degraded_to_sequence_only: true`).

---

## 8. Which field answers which S10 falsification row  (for stream C)

| README S10 row | Field(s) to read |
|---|---|
| S3 antigenicity rank -> IgG ELISA | `candidates[].scores.terms_raw.s3`, `s3_rank.json#payload.ranker` |
| S5 T-cell epitopes -> IFN-g ELISpot against predicted pools | `epitope_set.t_cell[].peptide` (group by `protein_id`) |
| S5 B-cell epitopes -> competition ELISA / peptide array | `candidates[].bcell.linear.regions[]` (`start`,`end`,`peptide`) |
| S6 population coverage -> HLA-transgenic panel | `candidates[].coverage.by_population`, `set_level_coverage.by_population`, alleles in `epitope_set.t_cell[].alleles` |
| S7 structure -> folded vs denatured binding | `candidates[].structure.conformational_epitopes`, `.mean_plddt`, `.validator_agreement` |
| Functional protection | not produced by B (needs a correlate; do not assume one exists) |

For S11 (pre-registration): every stage `digest`, `params`, `producer.tools` version (see section 1: it can be a backend name when a tool has no version) and the thresholds are already in the envelope.
For S12 (biosecurity gate): sequences leaving B are `candidates[].sequence` and `epitope_set.*[].peptide`.

## 9. Deliberate deviations from the README / PDF (please sanity-check)

1. **No Boltz-2 affinity value.** The PDF titles S7 "structure *and affinity*", but Boltz-2's affinity module is documented as
   protein-small-molecule only ("if run with an RNA/DNA/co-factor target the code will not crash but the output will be unreliable").
   There is no valid target for a vaccine-antigen monomer, so S7 states `affinity: not_implemented` and gives the reason.
2. **DiscoTope-3.0 runs in S5**, on AlphaFold DB models (the README's own justification: AUC 0.783 on AF models), because S5 precedes S7.
   S7 re-runs it on the Boltz-2 model and adds the pLDDT-confidence of the predicted epitope residues.
3. **S6 uses our own coverage calculator** (IEDB method) rather than shelling out to the IEDB tool, because the fairness number
   needs re-optimisation under a budget. The IEDB-format export is provided for cross-checking.
4. **Vaxign-ML's score is a percentile**, so "0.90 / 0.85" means the 90th / 85th percentile of its training-score distribution.
5. **Real-data findings that change how S4 should be read** (see the warning box in section 4): the conservancy and allergenicity gates, applied as the README specifies, remove the three Bexsero antigens.
6. **S7 owns the final coarse ranking** (README: "Structures + coarse ranking"); S3's ranking alone is not the final one.
