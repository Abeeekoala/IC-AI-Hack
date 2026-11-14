# The `runs/<run_id>/` artifact contract

Owned by stream A. **Streams B, C and D build against this document and against `fixtures/nmb_fixture/`** - a real,
committed S0-S2 run (40 proteins) that needs no downloads, BLAST or PSORTb. Code lives in `src/vaxforge/artifacts.py`.

## Rules everyone follows

1. **A stage is a pure function over the previous stage's artifact.** Read artifacts, write an artifact. Do not import another
   stream's `sN_*.py` module.
2. **Use `RunStore`** to read and write (`from vaxforge.artifacts import RunStore`). It does the hashing, the upstream chain and the manifest.
3. **Check `status`.** Read upstream with `store.read_artifact("s2").require_ok()`; it raises unless `status == "ok"`.
4. **Never invent a value for something that was not computed.** Use the explicit states below (`not_run`, `not_applicable`,
   `unknown`, ...). A `not_run` is not a `False`.

## Folder layout

```
runs/<run_id>/
  manifest.json          stage -> artifact, digest, status, finished_at
  config.resolved.json   configuration used (secrets redacted)
  s0_taxonomy.json  s1_acquire.json  s2_annotate.json  ...   one envelope per stage, named after its module
  data/                  sidecar files referenced by the artifacts (FASTA, TSV)
```
`cache/` (shared, gitignored) holds downloads. Both `runs/` and `cache/` are gitignored. The committed fixture lives in `fixtures/nmb_fixture/` - deliberately not under a folder called `runs/`, which the team repo ignores.

## Envelope (every `sN_*.json`)

| key | meaning |
|---|---|
| `schema` | `vaxforge/<module>/v1` |
| `stage`, `run_id` | `"s2"`, folder name |
| `created_at`, `environment` | when / Python + OS. **Not hashed.** |
| `producer` | `{module, vaxforge_version, tools: {blastp: "2.17.0+", ...}}` - tool versions (feeds S11 pre-registration) |
| `params` | everything that influenced the result (thresholds, flags) |
| `inputs` | `{"s0": "sha256:...", "s1": "sha256:..."}` digests of the upstream artifacts this was built from |
| `status` | `ok` \| `needs_user_input` \| `failed` |
| `warnings` | human-readable strings; surface them in the report |
| `payload` | stage-specific (below) |
| `volatile` | how/when this run obtained its data: cache hits, fetch timestamps (`retrieved_at` per resource), local paths. **Not hashed** - the file hashes already identify the data |
| `digest` | `sha256` over `schema, stage, producer, params, inputs, status, warnings, payload` |

The digest covers *what was computed*, never *how it was fetched*: re-running a stage on a warm cache gives the same digest,
so downstream artifacts do not go stale. If you do re-run an upstream stage with different results, `vaxforge verify` reports
every downstream artifact as `STALE`.

**File references** are objects, never bare strings: `{"root": "run"|"cache", "path": "data/proteome.faa", "sha256": "...", "bytes": 877000, "records": 2003}`.
Resolve with `store.resolve(ref)` (create the store with `RunStore(run_dir, cache_dir=cfg.cache_dir)` to resolve `cache` refs).

## Writing your stage

```python
from vaxforge.artifacts import RunStore, register_payload_keys

def run(store: RunStore, config) -> "Artifact":          # the entrypoint `vaxforge run` calls
    s2 = store.read_artifact("s2").require_ok()
    ...
    return store.write_artifact("s3", payload, params={...}, tools={"vaxign-ml": "1.0"},
                                inputs=["s2"], status="ok", warnings=[...])
```
* `inputs` defaults to `artifacts.DEFAULT_INPUTS[stage]` (s4 reads `s3` and `s1`; s8 reads `s4 s5 s6 s7`; ...). Pass your own list if you read others.
* Stage ids and artifact names: `s3_rank.json`, `s4_filter.json`, `s5_epitopes.json`, `s6_coverage.json`, `s7_structure.json`, `s8_protocol.json`,
  `s9_robot.json`, `s9plus_animate.json`, `s9_5_finetune.json` (stage id `s9_5`, reads `s8` + `s9`), `s10_animal.json`, `s11_prereg.json`, `s12_biosafety.json`.
* `vaxforge run` imports `vaxforge.<module>` and calls `run(store, config)` for every stage that exists; absent ones are skipped.
  Extra sub-commands (`validate`, `compile-protocol`, `animal-study`) register by defining `register_cli(subparsers, common)` in your module or
  in `vaxforge/validate_cli.py`.
* BLAST: always use `vaxforge.tools.BlastRunner` (native or Docker, and it works around BLAST+'s inability to open non-ASCII Windows paths).
* Config: add your own section to `config.yaml`; read it with `config.get("thresholds.allergenicity")`. Write floats with a dot (`1.0e-5`).

## S0 - `s0_taxonomy.json`

`status`: `ok` (exactly one taxon chosen) \| `needs_user_input` (ambiguous / too broad / spelling suggestion) \| `failed` (nothing matched).
Downstream stages call `require_ok()`, so they only ever see a resolved taxon.

```
payload.query                     the text the user typed
payload.resolution.status         resolved | needs_user_input | not_found
payload.resolution.reason         null | ambiguous | rank_too_broad | fuzzy_match | spelling_suggestion | not_found
payload.resolution.method         exact | alias | abbreviation | fuzzy | spelling | taxid | user_choice
payload.resolution.matched_name / matched_name_class / match_score / synonym_matched / user_confirmed / notes / message
payload.taxon                     null unless resolved:
    taxid, scientific_name, rank, division, species_taxid, species_name, lineage[{taxid,name,rank}], other_names[{name,class}]
payload.organism                  null unless resolved:
    domain            bacteria | archaea | eukaryote | virus | unknown
    gram_stain        negative | positive | positive_with_outer_membrane | negative_without_outer_membrane |
                      archaea | unknown | not_applicable                           (+ gram_basis: why)
                      (the two "outer membrane" classes - Mycobacterium/Deinococcus, Mycoplasma/Tenericutes - follow PSORTb's own advice)
    psortb_flag       "-n" | "-p" | "-a" | null
    psortb_extra_args []   or ["--x-skip-localization=Cellwall"] for Mycoplasma-type organisms
    virulence_db_applicable, localisation_applicable   (false for viruses/eukaryotes)
payload.candidates                [{taxid, scientific_name, rank, species_taxid, matched_name, match_score, ...}] to choose from
```
Note `taxon.taxid` may be strain-level (e.g. SARS-CoV-2 `2697049`); `species_taxid` is the species above it.

## S1 - `s1_acquire.json`

`status`: `ok` or `failed` (only the target proteome is fatal). Optional resources never fail the run - they report why.

```
payload.proteome                  upid, name, taxid, strain, proteome_type, n_sequences, release, sequence_source (uniprotkb|uniparc),
                                  file -> data/proteome.faa (run ref), cache_file, stats{records,residues,min_length,max_length}
payload.proteome_selection        method: reference_proteome | best_available_non_reference | pinned | user_supplied_fasta
payload.comparison_proteome       a second strain for S4 conservancy: same shape + selection_reason, or {status: unavailable|disabled, reason}
payload.reference_dbs.vfdb        status, set, n_sequences, cache_file          (skipped_not_applicable for non-bacteria)
payload.reference_dbs.protegen    status, category, version, cache_file (all pathogens), target_matches, target_file (data/protegen_target.faa), target_names_searched
payload.reference_dbs.human_proteome  status, upid, reviewed_only, n_sequences, cache_file     (S4 autoimmunity screen)
payload.reference_dbs.iedb        disabled by default (huge); manual drop-in supported - see config acquire.iedb
payload.reference_dbs.afnd        deferred_to_s6 - allele frequencies come from IEDB PopCover inside S6
payload.blast_dbs.<name>          {status, name, fasta_sha256, blast_version}  for vfdb, human_proteome, comparison_proteome, proteome
payload.resource_status           flat {resource: status}
```
Statuses: `ok`, `disabled`, `skipped_not_applicable`, `unavailable` (+`reason`), `manual_required`, `deferred_to_s6`, `skipped`.

**Using a BLAST database (stream B / D):**
```python
from vaxforge.tools import BlastRunner
runner = BlastRunner(config)
db = s1.payload["blast_dbs"]["human_proteome"]
prefix = runner.db_prefix(db["name"], db["fasta_sha256"])           # deterministic; rebuild with runner.make_db(fasta, name, sha) if absent
tsv = runner.blastp(query_fasta, prefix, "6 qseqid sseqid pident qcovs evalue bitscore", evalue=1e-3)
```

**Stream D:** Protegen's licence is not stated, so its files stay in the gitignored `cache/` and `runs/` - never commit them. `target_file`
holds the Protegen entries for the target pathogen; **hold these out of any training set before the Bexsero / LOPOV run.** Its headers look like
`>Protegen: 264|VO: VO_0010946|NP_275117.1 transferrin-binding protein-like protein [Neisseria meningitidis MC58]`.

## S2 - `s2_annotate.json` (+ `data/s2_annotations.tsv`)

`payload.proteins[]` has one entry per protein of `data/proteome.faa`, **same order**:

```
protein_id        first token of the FASTA header, unique (e.g. "sp|Q9JXV4|FHBP_NEIMB"); the key to join on
accession, entry_name, gene, description, length
virulence.status                  hit | no_hit | not_applicable | not_run
virulence.is_virulence_associated true | false | null     (null = not looked; false = BLASTed against VFDB and no qualifying hit)
virulence.evidence                strong | moderate | weak | none | n/a
virulence.best_hit                {vfdb_id, gene, product, factor, factor_id, category, category_id, species,
                                   identity_pct, query_coverage_pct, subject_coverage_pct, evalue, bitscore, alignment_length}
virulence.n_hits, virulence.hits  up to annotate.vfdb.max_hits hits
localisation.status               predicted | psortb_unknown | not_run | not_applicable | missing_from_psortb_output   (| fixture_stand_in in the fixture)
localisation.label                Cytoplasmic | CytoplasmicMembrane | Periplasmic | OuterMembrane | Extracellular | Cellwall | Unknown
localisation.score, .multiple_sites, .source, .reason
localisation.accessibility        accessible | partial | inaccessible | unknown
```

**Rules for consumers**
* **`Unknown` is its own value.** Do not treat `accessibility == "unknown"` as "not surface" or silently drop it (README S2). If you rank, say how unknowns are handled.
* `localisation.status == "not_run"` means *PSORTb did not run* (no Docker, Gram stain unknown, ...) - every label is then `Unknown` and `reason` says why.
  It is different from `psortb_unknown`, where PSORTb ran and could not decide.
* All three of identity, coverage and E-value are recorded. `is_virulence_associated` applies `payload.thresholds`
  (`max_evalue 1e-5`, `min_identity 30`, `min_query_coverage 50`): **assumed defaults, the README gives none** - report them.
* `accessibility` comes from `config.yaml: annotate.accessibility` (an engineering judgement, not a literature claim).
* `payload.summary` has counts by status/evidence/label/accessibility; `payload.tools.virulence|localisation` say what ran and why not.

`data/s2_annotations.tsv` is the same table flattened (`payload.columns` lists the columns) - convenient for pandas and for the report UI.

## Fixture

`fixtures/nmb_fixture/` - see its `FIXTURE.md`. Real taxonomy, proteome and BLAST results; **localisation is a flagged stand-in**
(from UniProt subcellular-location text), not PSORTb. It contains the Bexsero antigens (fHbp strong VFDB hit; NadA moderate; NHBA no hit; PorA strong),
useful edge cases for ranking and for the recall@3 test. `vaxforge verify fixtures/nmb_fixture` passes with no cache.

## What changes if you need something different

Ask on the board and mention stream A. Adding a field is free; renaming or removing one breaks others - post when it lands.
