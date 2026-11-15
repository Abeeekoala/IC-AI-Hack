# VaxForge

**Type a pathogen name. Get a short, explained list of vaccine antigen candidates, a lab protocol that says what result would prove each one wrong, and robot code that has passed the simulator.**

VaxForge automates the slow first step of vaccine design: going from "a bacterium with thousands of proteins" to "the few proteins worth a lab's time". Every step records which tool made which number, so a judge or a scientist can follow the evidence from the pathogen name to the lab bench. Where a tool could not run, the output says so instead of inventing a value.

## At a glance

```
name  ->  proteome  ->  rank antigens  ->  safety + conservancy  ->  epitopes  ->  population coverage  ->  structure (top-k)
 S0         S1-S2           S3                    S4                   S5               S6                     S7
                                                                                                   |
   biosecurity <- animal study design <- robot code (simulator-checked) <- lab protocol <-----------+
       S12              S10                       S9                         S8            (+ pre-registration S11)
```

| | |
|---|---|
| **One input** | A plain pathogen name, for example *Neisseria meningitidis* |
| **Real tools** | Vaxign-ML, BLAST+, AlgPred2, ToxinPred2, MHCflurry, BepiPred-3.0, Boltz-2, the IEDB population-coverage method |
| **Outputs** | Ranked shortlist with the evidence behind each rank; wet-lab protocol with a falsification condition per candidate; Opentrons OT-2 code; animal-study design for ethics review |
| **Traceable** | Every stage writes a hashed artifact; `vaxforge verify` re-checks the whole chain; a stage that did not run is shown as "not run" |
| **Tested** | More than 2,200 automated tests |

## Pre-demo run: *Neisseria meningitidis*

A full run takes a long time (the annotation stage alone took 48 minutes on the whole 2,003-protein proteome), so the demo shows runs we made beforehand and saved in the repo. Both are real runs of this code, and `vaxforge verify` passes on each.

| Saved run | What it shows |
|---|---|
| [`results/nmb_real_run/`](results/nmb_real_run/) | S3 to S12 with the real prediction tools installed, plus a self-contained `report.html` and a one-page `SUMMARY.md` |
| [`demo_data/run_nmb_fixture/`](demo_data/run_nmb_fixture/) | S3 to S12 end to end, including all 36 robot sessions passing the Opentrons 7.5.0 simulator and the protocol animations |

**What the real-tool run found** (40 *N. meningitidis* proteins, including the three Bexsero vaccine antigens):

| Step | Result |
|---|---|
| S3 ranking | Vaxign-ML scores all proteins; 30 go forward |
| S4 safety and conservancy | BLAST+, AlgPred2, ToxinPred2 and a second-strain check: **27 of 30 kept**; 3 removed (frpA and frpC for toxicity, lldD for similarity to a human protein) |
| S5 epitopes | T-cell epitopes for 27 HLA alleles (MHCflurry) and antibody epitopes (BepiPred-3.0) |
| S6 population coverage | 98.5% of the world population covered by the class I epitopes |
| S7 final shortlist | porA, hmbR, iga, NMB1829, hap, mtrD, **NHBA (7th)**, lbpA, potD-1, **NadA (10th)** ... **fHbp (15th)** |
| S8 to S12 | Protocol, robot code, animal-study design, pre-registration and a biosecurity gate that holds synthesis orders for human review |

**The Bexsero antigens.** The licensed Bexsero vaccine uses three *N. meningitidis* antigens (fHbp, NadA, NHBA). They differ between strains, so S4 **flags** a strain-variability or allergen-score failure and keeps the protein, instead of discarding it; toxicity and human similarity still remove proteins. All three stay in the shortlist with their flags recorded. We chose that rule after a first strict run removed all three, so this is a demonstration of how the pipeline treats variable antigens, not held-out validation (see below).

## How much to trust it

- **It narrows; it does not prove.** Nothing here shows that any candidate protects anyone. That needs lab and animal work, which is exactly what the protocol and the animal-study design are for.
- **The Bexsero check is circular.** The stock Vaxign-ML model was trained on these antigens, so their high scores are expected. S3 prints a `LEAKAGE RISK` warning, and the recall@3 check (0 of 3 on the stock model) is not evidence either way. The fair test, leaving the pathogen out of training, is built but has not been run.
- **Some tools were not run:** class II T-cell prediction (NetMHCIIpan needs a licence), conformational B-cell prediction (DiscoTope-3.0) and Boltz-2 structures in the saved run. Each artifact lists them under `degraded`.
- **The test set is small:** 40 of 2,003 proteins, chosen to include the Bexsero antigens, with localisation from UniProt text instead of PSORTb.
- **Pre-registration:** the S11 manifest in the saved runs was frozen after the S4 rule above was chosen, so it records that configuration; it is not a blind prediction.

The sections below give the full scope, the stage reference, the validation protocol and the known limits.

---

## 0. What this is, and what it is not

Most in-silico vaccine design projects produce a ranked list and stop. The list looks authoritative, nothing in the repo tells you whether the ranking is better than chance, and the published literature in this area has a measurable credibility problem: multi-epitope in-silico vaccine papers grew from ~5% to ~43% of reverse-vaccinology output by 2024 while the field's mean impact factor fell from 6.4 to 4.2, and none of the 39 FDA-monitored trials for WHO priority pathogens originated from reverse-vaccinology antigen selection.

VaxForge is built around the opposite commitment.

**This project DOES:**
- Resolve a plain-language pathogen name to a reference proteome and run a transparent, versioned screening cascade over it
- Rank candidate antigens and expose the full evidence chain behind every rank
- Report **held-out** performance against experimentally verified protective antigens, including a leave-one-pathogen-out protocol
- Emit a complete wet-lab protocol with controls, cost, timeline, and an explicit falsification condition per candidate
- Compile the liquid-handling subset of that protocol to executable Opentrons OT-2 Python, validated by simulation
- Emit an animal immunogenicity study proposal, ready for ethics review, in which **every in-silico prediction is mapped to the assay that would refute it**

**This project DOES NOT:**
- Simulate wet-lab results. No predicted ELISA readings, no synthetic flow cytometry, no fabricated cytokine values. A prediction is a hypothesis; a measurement is an observation. Presenting the former as the latter is not exaggeration, it is a category error.
- Simulate Phase 1/2/3 clinical trials. As of late 2026 there is no scientific or regulatory basis for this in vaccines. ICH M15 (CHMP-adopted 2026-01-23, effective 2026-07-23) does not mention vaccines or immunology anywhere in its text. The FDA MIDD Paired Meeting Program explicitly excludes using simulation to determine the operating characteristics of confirmatory trials. The two real cases where in-silico work substituted for trials — the UVA/Padova T1D simulator (FDA Master File #1521, accepted 2008, as a substitute for *animal* trials) and VICTRE — are both outside vaccines and both outside efficacy endpoints.
- Design, enhance, or characterise anything that increases pathogen virulence, transmissibility, or immune escape. Scope is restricted to protective antigens and therapeutic binders. See §8.

---

## 1. Architecture

```mermaid
flowchart TD
    A["S0 · Taxonomy resolution<br/>name → taxid"] --> B["S1 · Data acquisition<br/>cache-first fetch"]
    B --> C["S2 · Virulence + localisation<br/>VFDB, PSORTb"]
    C --> D["S3 · Antigenicity ranking<br/>Vaxign-ML"]
    D --> E["S4 · Safety + conservancy<br/>autoimmunity, allergen, toxin"]
    E --> F["S5 · Epitope prediction<br/>MHCflurry (NetMHCpan if licensed), BepiPred-3.0"]
    F --> G["S6 · Population coverage<br/>IEDB method + AFND"]
    G --> H["S7 · Structure, top-k only<br/>Boltz-2"]
    H --> I["S8 · Protocol generation<br/>NL protocol + falsifier"]
    I --> J["S9 · Robot code<br/>Opentrons OT-2 Python"]
    J -.-> J2["S9+ · Protocol animation<br/>optional"]
    J --> J5["S9.5 · Fine-tuned codegen<br/>Adaption, win rate vs base"]
    J5 --> AN["S10 · Animal study proposal<br/>design, not results"]
    AN --> K["S11 · Hypothesis pre-registration"]
    K --> L["S12 · Biosecurity gate"]
    L --> M["Ranked candidates + evidence chain<br/>+ protocol + robot code<br/>+ animal study design"]
    V["Retrospective validation<br/>Protegen + LOPOV + Bexsero"] -.->|re-weights| D
    W["Fine-tune + win-rate harness<br/>AutoScientist, opentrons_simulate"] -.->|replaces codegen model| J
```

Every stage is a pure function over the previous stage's artifact. Artifacts are content-addressed JSON written to `runs/<run_id>/`, so any stage can be re-run without recomputing upstream work, and any claim in the final report can be traced to the exact tool version and parameters that produced it.

---

## 2. Stage reference

| Stage | Input | Output | Primary tooling | Typical runtime |
|---|---|---|---|---|
| S0 | Pathogen name (free text) | `taxid`, resolved lineage | NCBI Taxonomy via E-utilities | <2 s |
| S1 | `taxid` | Reference proteome + reference DBs | UniProt, VFDB, Protegen, IEDB | 5–120 s cold, <1 s cached |
| S2 | Proteome FASTA | Annotation table | BLAST+ vs VFDB, PSORTb 3.0 | about 48 min for all 2,003 proteins of the *N. meningitidis* reference proteome (measured by the lead's agent with Docker BLAST+ and PSORTb on a Mac; PSORTb is the slow part), 37 s for the 40-protein fixture (measured on Windows) |
| S3 | Annotation table | Ranked candidates | Vaxign-ML (Docker) | 1–3 min |
| S4 | Ranked candidates | Filtered candidates | BLASTp vs human proteome, AlgPred 2.0, ToxinPred2 | 2–5 min |
| S5 | Filtered candidates | T-cell + B-cell epitopes | MHCflurry (NetMHCpan if licensed), NetMHCIIpan, BepiPred-3.0, DiscoTope-3.0 | 2–10 min |
| S6 | Epitope set | Per-population coverage, PC90 | IEDB Population Coverage + AFND | <1 min |
| S7 | top-k candidates | Structures + coarse ranking | Boltz-2 | 2–30 min GPU |
| S8 | Final candidates | Structured NL protocol | LLM + protocol templates | <1 min |
| S9 | NL protocol | OT-2 Python + validation report | LLM codegen + `opentrons_simulate` | 1–3 min |
| S9.5 | NL protocol (S8) + S9 output | Fine-tuned OT-2 Python + win/loss/tie vs S9 + refusal rates | AutoScientist (SFT), `opentrons_simulate` v7.5.0 | Training "hours" per Adaption's docs; not measured here |
| S10 | Final candidates | Animal study design + ethics pack | Power analysis + ARRIVE 2.0 template | <1 min |
| S11 | Final ranking | Timestamped prediction manifest | git tag + JSON manifest | <1 s |
| S12 | Any sequence output | Screening-cleared sequences | Synthesis-provider screening | manual |

Per-stage notes (data sources, tool choices, caveats): [docs/STAGE_REFERENCE.md](docs/STAGE_REFERENCE.md).

**S9.5 results.** Three Adaption fine-tunes of the protocol-code model finished. Adaption's own `best_win_rate`, measured on evaluations it derives from our data, was 0.9439 (Qwen3.5-9B), 0.7832 (4B) and 0.6037 (2B). Our own held-out test of the 4B adapter showed no measurable difference from the base model (0 wins, 0 losses, 10 ties), so we do not claim better protocol quality. Details in the stage reference.

**S11** freezes the ranking, tool versions and thresholds in a timestamped manifest before validation. **S12** is a biosecurity gate: any sequence that could be sent for synthesis is screened first, and the demo run holds the order for human review.

### S10 — Animal study proposal

A ranked list is a set of hypotheses. An animal immunogenicity study is the first experiment that can actually refute them. This stage emits a **study design ready for ethics review** — arms, group sizes, schedule, endpoints, statistics, and ethics documentation. As everywhere else in this pipeline, the output is a design, never results.

#### Falsification mapping — the core of this stage

Each upstream prediction is paired with the readout that can kill it. This table is what turns the project from "we ranked some proteins" into "we made testable claims and stated in advance how we would be wrong."

| Upstream prediction | Animal readout | Falsified if |
|---|---|---|
| S3 antigenicity rank | Antigen-specific IgG ELISA titre | No titre rise over the adjuvant-only arm |
| S5 T-cell epitopes | IFN-γ ELISpot against predicted peptide pools | Response to whole antigen but **not** to the predicted pools — antigen right, epitope prediction wrong |
| S5 B-cell epitopes | Competition ELISA or peptide-array mapping | Sera bind the antigen but not the predicted epitopes |
| S6 population coverage | Panel of HLA-transgenic lines | Response absent in backgrounds predicted to be covered |
| S7 structure / conformation | Binding to folded vs denatured antigen | Sera recognise only the denatured form — the conformational epitope was an artifact |
| Functional protection | Species-appropriate functional assay | Titres rise but functional activity is absent |

On that last row: for meningococcal B the serum bactericidal assay (hSBA) is an accepted correlate of protection, which is why immunobridging was possible there. **This is unusual.** Most vaccines have no agreed correlate. Do not assume one exists for your pathogen — check, and if there is none, say so in the proposal.

#### Species selection

| Goal | Species | Note |
|---|---|---|
| Basic immunogenicity | BALB/c or C57BL/6 mice | Cheap and fast; strain drives Th bias, so state which and why |
| **Validating S5/S6 human epitope predictions** | **HLA-transgenic mice** | The only design in which your human MHC predictions are actually under test. Wild-type mouse MHC does not present the epitopes NetMHCpan predicted for humans |
| Large-volume polyclonal sera | Rabbit | More serum per animal |
| Challenge model | A species genuinely susceptible to the pathogen | Many human pathogens have no good small-animal model. If none exists, **write that in the proposal** rather than inventing one |
| Non-human primates | Last resort | Ethically and financially constrained; out of scope for a hackathon proposal |

The HLA-transgenic row is what makes this stage coherent with the rest of the pipeline. Without it, S5 and S6 remain unfalsifiable.

#### Minimum study arms

1. Candidate antigen + adjuvant
2. Adjuvant only
3. Naive / PBS
4. Licensed vaccine positive control, where one exists

#### Parameters the generator must fill in

- **Dose levels:** ≥3 for dose-ranging
- **Route:** IM / SC / IN, justified against the target immune compartment
- **Schedule:** prime-boost with bleed points, e.g. immunise d0/21/42, bleed d0/20/41/56
- **Adjuvant:** named, with rationale (alum biases Th2; TLR-agonist systems bias Th1)
- **Group size:** from a **power analysis**, stating effect size, α and power. n=5–10/group is conventional in mouse immunogenicity work, but convention is not a justification — compute it
- **Randomisation and blinding:** specify both, including who is blinded to what
- **Primary endpoint:** pre-specified, single, fixed before any data is collected
- **Humane endpoints:** explicit, with monitoring frequency
- **Statistical analysis plan:** pre-specified, including the multiplicity correction for comparing several candidates at once

#### Ethics and regulatory layer

The proposal is not a proposal without these:

- **ARRIVE 2.0** — report against the Essential 10 at minimum
- **3Rs** — Replacement, Reduction, Refinement, each addressed explicitly. Reduction is where the power analysis earns its place
- **UK:** Animals (Scientific Procedures) Act 1986 — project licence (PPL) and personal licence (PIL), plus AWERB review
- **US:** IACUC approval

Generate these as a populated checklist, not as prose a reviewer has to mine.

#### Scope limit on challenge studies

This stage produces study **design**: arms, endpoints, group sizes, statistics, and ethics documentation. It does **not** produce pathogen handling, culture, or challenge-inoculum preparation procedures. Those belong with the containment facility and its biosafety committee, and are deliberately out of scope for this tool.

#### Honest limits — state these inside the proposal itself

- **Mouse immunogenicity does not predict human protection.** It shows an antigen is immunogenic in a mouse. Necessary, not sufficient.
- Industry base rate for vaccines: **Phase 1 → approval 39.6%** (6.8% for non-industry sponsors). The narrowest transition is **Phase 2 → 3 at 65.4%** — precisely the step where immunogenicity data exists and efficacy data does not. No amount of preclinical work closes that gap.
- A positive animal result moves a candidate from "predicted" to "immunogenic in one species." It does not move it to "works."

> We were unable to retrieve Plotkin's *Correlates of Protection Induced by Vaccination* (Clin Vaccine Immunol, 2010) in full, so this README carries no authoritative definition of correlate vs surrogate vs mechanistic correlate, and no list of vaccines lacking an agreed correlate. **Read it before making correlate claims in your pitch.**

## 3. Retrospective validation — the part that makes the demo credible

A pipeline that runs is not a pipeline that works. This harness is the difference.

### Three-tier protocol

**Tier 1 — Antigen-level retrieval.**
- Positives: experimentally verified protective antigens from Protegen.
- Negatives: UniProt proteins at <30% sequence identity, with homologues excluded at `blastp` E ≤ 1e-3.
- Protocol: nested 5-fold cross-validation **plus leave-one-pathogen-out (LOPOV)**, following Vaxign-ML's published precedent.
- Metrics: fold enrichment, precision@k, PR-AUC, rank-of-truth.
- **Status (checked by an independent audit, 2026-11-15): only the metrics half exists.** `validation/lopov.py` computes precision@k, recall, fold enrichment, PR-AUC and rank-of-truth from per-fold scores the caller supplies (hand-checked on a toy input). It does not build the <30%-identity negatives, run the nested cross-validation or train a model with the holdout, and `training_set_assertion` reads "not checked" unless a reference set is supplied. The protocol above is the plan, not something this repository runs.

**Tier 2 — Case retrieval: Bexsero.**
Bexsero/4CMenB contains three recombinant antigens — **fHbp, NadA, NHBA** (50 µg each, plus 25 µg OMV supplying PorA 1.4). Run the pipeline on *N. meningitidis* serogroup B and report **recall@3** and **rank-of-truth**. A retrieval question with exactly three correct answers is far more legible to a judge than an AUC.

**Tier 3 — Honest base rate.**
Pizza et al., *Science* 287:1816 (2000) is the canonical reverse-vaccinology funnel:

```
570 candidate ORFs
  → 350 successfully expressed (61%)
    → 85 strongly positive in ≥1 assay
      → 7 positive in all three assays across 31 strains
        → 3 in the licensed vaccine
```

**Experimental hit rate: 1.2%. Timeline from genome paper to approval: roughly 13–15 years.** Put this on a slide. It is more persuasive than any precision figure you can report, because it shows you know what your numbers mean.

### Data leakage — say it before a judge finds it

**Vaxign-ML is trained on Protegen. The Bexsero antigens are necessarily in that training set. "Rediscovering" them with a Protegen-trained predictor is circular.**

Mitigation: remove *N. meningitidis* entirely from the training/reference set before the Tier 2 run — that is the point of LOPOV — and state this explicitly in the demo.

This failure mode is endemic, not hypothetical: DeepSol and SKADE were found to have 75.1% overlap between training and test sets. A 2025 paper in this exact subfield reports no held-out test set, no AUC, and no cross-validation at all. **Getting this one thing right puts you ahead of most published work in the area**, which is precisely why you should say it out loud rather than bury it.

---

## 4. Known limitations

Report these; do not let a judge discover them.

| Claim | Honest ceiling |
|---|---|
| B-cell epitope prediction | BepiPred-2.0 AUC 0.574; field ceiling ~0.70–0.84 |
| Antigenicity (VaxiJen) | AUC 0.56, MCC 0.08; flags ~64% of proteome |
| Blind docking (ClusPro) | 13.7% top-1, 45.1% top-10 |
| Affinity prediction (Boltz-2) | Pearson 0.62 vs FEP+ |
| Solubility prediction | Best of 12 predictors: 58.4% accuracy (AUC 0.60, MCC 0.17). Value is enrichment — top 10% of SoluProt rankings yielded 199 true positives vs 145 expected by chance, a 37.2% relative lift — **not** replacing the experiment |
| De novo binder design | BindCraft reports 46.3% mean success (10–100% by target). An independent reproduction of RFdiffusion found near-total failure on several targets, with no measurable affinity. Design success is target-dependent and often much worse than headline numbers |
| Mouse immunogenicity → human protection | No reliable predictive relationship. Most vaccines lack an agreed correlate of protection; meningococcal hSBA is an exception, not the rule |
| Expression itself | ProteinGenerator designs *intended* to be soluble monomers passed 32/42; 8/19 when tryptophan-enriched. "It expresses" is itself a high-failure experimental outcome |
| Trial outcome prediction | HINT reports PR-AUC 0.772/0.607/0.623 (Ph 1/2/3) on its own benchmark; on an independent evaluation of 2,163 trials its balanced accuracy was 0.470–0.594 with overall MCC 0.050. In the same evaluation GPT-4o-mini degenerated to predicting "success" for everything (balanced accuracy 0.500, recall 1.000, specificity 0.000) |
| Vaccine industry base rate | Phase 1 → approval 39.6% (industry-sponsored), 6.8% (non-industry). The narrowest transition is Phase 2 → 3 at 65.4% — exactly the point where immunogenicity data exists but efficacy data does not, which is the furthest any in-silico immune model can reach |
| Fine-tuning for protocol code (S9.5) | Improves syntactic and procedural conformance to the Opentrons API and to protocol conventions. Does **not** improve biological validity: a protocol that compiles and passes simulation can still ruin an experiment, and the win rate inherits that limit |

**S4 gate settings.** The allergenicity and conservancy defaults (flag, not reject) were chosen after seeing that strict settings removed fHbp, NadA and NHBA, so any Bexsero result is a demonstration, not validation. The IEDB human-ligand re-check was not run, so human-homology rejections may be over-aggressive.

---

## 5. Install and quickstart

```bash
git clone https://github.com/Abeeekoala/IC-AI-Hack.git
cd IC-AI-Hack
make setup              # python deps + pulls Docker images
make warm-cache PATHOGENS="Neisseria meningitidis,SARS-CoV-2"
```

```bash
# Full pipeline
vaxforge run --pathogen "Neisseria meningitidis" --top-k 20 --out runs/nmb

# Offline demo, no network and no container: copies the committed fixture (S0-S2) and runs S3-S12 on the copy
make demo-offline                                      # = vaxforge demo-offline --out runs/demo --force; S3 uses the real Vaxign-ML scores in docs/examples (stock model: not a validation)
make demo-offline DEMO_FLAGS=--rule-based              # S3 with the rule-based fallback instead (or DEMO_FLAGS="--scores-file FILE" for your own scores)
vaxforge run --out runs/demo --stages s7,s8 --offline  # later stages on an existing run folder need no --pathogen
vaxforge run ... --set s3.backend=scores_file          # --set KEY=VALUE overrides one config value for this run

# Look at, check and report on a run
vaxforge show runs/nmb                                 # summary of the stages in a run folder
vaxforge verify runs/nmb                               # digests, upstream chain, file hashes
vaxforge report runs/nmb --out report.html             # one self-contained HTML page for the run
vaxforge prereg verify runs/nmb                        # S11: recompute the pre-registration digest
vaxforge biosafety check ORDER.fasta --run runs/nmb    # S12: check an order list before it goes to a synthesis provider
vaxforge finetune manifest                             # S9.5 tools (manifest, check, tasks, generate, grade, winrate): see --help
vaxforge doctor --online                               # tools, cache, network; S3-S12 tools are listed as found / not found only

# Retrospective validation (do this before the demo)
vaxforge validate lopov INPUT.json --k 3 5 10          # leave-one-pathogen-out metrics (input format: validation/lopov.py)
vaxforge validate bexsero runs/nmb/s3_rank.json --k 3  # recall@3 of the Bexsero antigens in the S3 ranking

# Robot code only, from an existing protocol artifact
vaxforge compile-protocol runs/nmb --target opentrons-ot2

# Animal study proposal for the top candidates
vaxforge animal-study runs/nmb --species hla-transgenic --power 0.8
# After this, `vaxforge verify runs/nmb` reports s11 as STALE: a stage upstream of S11 was re-written after S11 recorded
# its digests, so S11's chain no longer matches. That is expected, not corruption; it is flagged on purpose.
```

Docker is required for BLAST+ and Vaxign-ML. A GPU with ≥16 GB VRAM is recommended for S7; the pipeline degrades gracefully to sequence-only ranking without one.

### Configuration

```yaml
# config.yaml
thresholds:
  protegenicity: [0.90, 0.85]   # always report both
  allergenicity: 0.45           # AlgPred 2.0
  toxicity: 0.80                # ToxinPred2
  conservancy_identity: 0.80
epitopes:
  bcell_tool: bepipred-3.0      # never 2.0
  mhc_class_ii_weight: 0.3      # down-weighted deliberately
structure:
  engine: boltz-2               # MIT; do not switch to AF3 weights
  refolder: [esmfold, af2-no-msa]
finetune:
  base_model_id: null           # unpinned: set from the live AutoScientist model list before training
  model_id: null                # unpinned: exists only after a run; record run id + checkpoint SHA-256
  eval_tasks: 11                # Text2WetLab: 11 tasks, 7 families, before the refusal filter
  seed: 20261114                # bootstrap + any subsampling; Adaption's create request has no seed
reproducibility:
  seed: 20261114
  pin_versions: true
```

---

## 6. Repository layout

```
vaxforge/
├── src/vaxforge/
│   ├── s0_taxonomy.py        # name → taxid, synonymy + ambiguity handling
│   ├── s1_acquire.py         # cache-first fetch
│   ├── s2_annotate.py        # VFDB + PSORTb
│   ├── s3_rank.py            # Vaxign-ML wrapper
│   ├── s4_filter.py          # autoimmunity / allergen / toxin / conservancy
│   ├── s5_epitopes.py        # NetMHCpan, BepiPred-3.0, DiscoTope-3.0
│   ├── s6_coverage.py        # IEDB PopCover + AFND, fairness report
│   ├── s7_structure.py       # Boltz-2, dual refolder
│   ├── s8_protocol.py        # NL protocol + falsification condition
│   ├── s9_robot.py           # OT-2 codegen + simulate + rule checks
│   ├── s9plus_animate.py     # optional: command list → timeline → canvas
│   ├── s9_5_finetune.py      # training manifest + run guard (family split, refusal filter)
│   ├── s9_5_corpus.py        # candidate pairs from protocol files: AST prompt, family flags, simulate gate
│   ├── s9_5_adaption.py      # AutoScientist client: dry run unless confirmed and guarded
│   ├── s9_5_generate.py      # pass@1 generation against any OpenAI-compatible endpoint
│   ├── s10_animal.py         # study design, power analysis, ARRIVE pack
│   ├── s11_prereg.py         # manifest + timestamp
│   └── s12_biosafety.py      # screening gate
├── validation/
│   ├── lopov.py              # leave-one-pathogen-out
│   ├── bexsero_case.py       # recall@3 retrieval test
│   ├── leakage_check.py      # asserts holdout actually held out
│   ├── winrate.py            # S9.5 pairwise win rate vs base, bootstrap CI
│   ├── t2wl_adapter.py       # S9.5 grading: Text2WetLab's own lint, traps, simulation and checks
│   └── heldout_families.json # the 7 Text2WetLab families held out of S9.5 training
├── cache/                    # gitignored
├── runs/                     # gitignored
└── config.yaml
```

---

## 7. What is deliberately absent

| Not implemented | Why |
|---|---|
| Phase 1/2/3 trial simulation | No scientific or regulatory basis for vaccines (§0) |
| C-ImmSim immune simulation | Model described only partially in the literature, no complete manual, derivative IMMSIM++ shipped as binary only so third parties cannot test it. Widely used in this genre; treated here as decorative |
| MD + MM-PBSA "validation" | Flagged in the literature as computational validation that does not demonstrate immunological efficacy |
| ClusPro–TLR docking | See §S7 |
| Predicted assay readouts | See §0 |

Listing what you refused to build is itself a result. Judges at science-track hackathons have consistently rewarded systems that disclose the limits of their own evidence.

---

## 8. Scope and responsible use

VaxForge is restricted to **protective antigen identification and therapeutic binder design**. It does not support, and will not be extended to support, work that increases pathogen virulence, transmissibility, or immune escape.

Sequence screening before synthesis is implemented as a pipeline node (S12), not an afterthought. The Responsible AI × Biodesign commitments (published 2024-03-08, 206 signatories as of the 2024-10-26 list update) include an explicit undertaking to *"obtain DNA synthesis services only from providers that demonstrate adherence"* to screening practice, alongside red-teaming commitments.

> The US OSTP *Framework for Nucleic Acid Synthesis Screening*, Executive Order 14292 (May 2025), and the International Gene Synthesis Consortium's Harmonized Screening Protocol all exist and are relevant. We verified only their titles, publishers and dates — **do not cite specific provisions or compliance deadlines on a slide without reading the primary documents yourself.**

---

## 9. Licensing notes for dependencies

Boltz-2 and Text2WetLab are MIT; AlphaFold 3 weights are non-commercial and are not used; PyRosetta needs a licence and is avoided; the licence of Protegen and of the S9.5 base model is not determined. The full table is in [docs/LICENSING_AND_CITATIONS.md](docs/LICENSING_AND_CITATIONS.md).

## 10. Citations

Numbers quoted in this README trace to primary sources listed in [docs/LICENSING_AND_CITATIONS.md](docs/LICENSING_AND_CITATIONS.md); where a claim could not be verified, the text says so.

---

## 11. Roadmap

- [ ] Replace the Vaxign-ML baseline with a model trained on a leakage-audited split and report both
- [ ] Add a second validator for S7 beyond ESMFold and report the spread
- [ ] Extend the automatability matrix to PyLabRobot so the codegen is not OT-2-specific
- [ ] Add mRNA construct design (LinearDesign is CPU-fast — ~11 min for spike — and reported 57–128× higher IgG titre in mouse)
- [ ] Auto-populate the ARRIVE 2.0 Essential 10 checklist directly from the study design artifact
- [ ] Per-candidate uncertainty display: show the evidence chain and the predictor's known accuracy next to every score
