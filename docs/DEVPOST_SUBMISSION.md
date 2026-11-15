# Devpost submission text

Paste-ready text for the Imperial AI Hackathon, 2026-11-15, following the organisers' "How to submit" list. Numbers trace to `docs/WRITEUP.md` and the README. The video is separate (`docs/design/DEMO_VIDEO_PLAN.md`).

**Name:** VaxForge
**Tagline:** From a pathogen name to ranked antigens and robot code

## Short description

**Problem.** Vaccine design starts with a bacterium with thousands of proteins and ends, after weeks of manual triage across a dozen tools, with a few worth a lab's time. The output rarely says how far to trust it.

**What we built.** Give VaxForge a pathogen name. It returns a ranked shortlist of candidate antigens with the evidence behind each rank, a lab protocol that states what result would prove each candidate wrong, Opentrons OT-2 robot code that passes the simulator, and an animal-study design for ethics review. We tested it on *Neisseria meningitidis*, the bacterium behind the Bexsero vaccine.

**How it works.** Thirteen stages (S0 to S12), each reading the previous stage's hashed artifact, so `vaxforge verify` re-checks the whole chain. Vaxign-ML ranks the proteome; BLAST+, AlgPred2 and ToxinPred2 screen for human similarity, allergenicity and toxicity; MHCflurry and BepiPred-3.0 predict epitopes; population coverage comes from HLA frequencies; Boltz-2 folds the top candidates. Each protocol compiles to OT-2 code checked by `opentrons_simulate` (36 of 36 sessions pass in the demo run), and a biosecurity gate holds synthesis orders for human review.

**How far to trust it.** It narrows; it does not prove. A leave-one-pathogen-out harness and a leakage check sit beside the ranking. The Bexsero check is circular (the stock model was trained on those antigens; recall@3 is 0 of 3), and a stage that did not run says "not run". On the saved 40-protein run, stages 3 to 12 take 16 to 17 s offline on a laptop.

## Extra prizes

- **Boltz.** Boltz-2 is wired into S7 and its confidence is reported, not hidden. The only Boltz run on record is a CPU smoke run without an alignment on three proteins: mean pLDDT 58.2, 67.4 and 36.3. The saved real-tool run did not run Boltz. No affinity or design claim.
- **Figma.** The product surface is designed in Figma: 16 editable screens, colour variables, a status-pill component set, and "not run" and "unverified" as first-class states (`docs/design/FIGMA_EVIDENCE.md`).
- **Adaption.** We fine-tuned the OT-2 code generator with AutoScientist (SFT, LoRA) on 1,000 rows: 429 real pairs from public repositories and 571 synthetic, all simulator-checked and leakage-checked (0 violations). The best run, Qwen3.5-9B, reached a **best win rate of 0.9439 against the base model** (adapted 94, base 6; 2 of 3 iterations, about 62 min). The 4B run reached 0.7832 and the 2B run 0.6037. These are Adaption's own numbers on evaluations it derives from our data. Our own held-out simulator test of the 4B adapter found no measurable difference, and the 9B adapter was not tested that way. The demo's robot code comes from templates; the fine-tune is not in that path.

## APIs, models and datasets used

**APIs and services:** NCBI Taxonomy (S0), UniProt REST (S1), AlphaFold DB API, IEDB population-coverage bundle, Adaption AutoScientist, Anthropic API (optional S8/S9 backends; `claude-sonnet-5-5` passed 13 of 13 sessions, `claude-haiku-5-5` 4 of 13; the demo runs use the template backend), Figma.

**Models and tools:** Vaxign-ML (stock model), BLAST+ 2.17.0, AlgPred2 1.4, ToxinPred2 1.1, MHCflurry 2.3.28, BepiPred-3.0, Boltz-2 2.2.1, Opentrons simulator 7.5.0, Qwen3.5-9B, 4B and 2B (fine-tuned through Adaption; a 4-bit MLX build of the 4B for the local test). Not run: NetMHCpan, NetMHCIIpan, DiscoTope-3.0; the demo fixture's localisation is a UniProt-text stand-in for PSORTb.

**Datasets:**
- UniProt *N. meningitidis* MC58 proteome (2,003 proteins; a 40-protein fixture for the demo).
- VFDB virulence factors.
- Protegen (VIOLIN) protective antigens, for validation; Vaxign-ML was trained on it.
- IEDB and AFND HLA frequencies.
- Text2WetLab (MIT), held-out evaluation only.
- Our S9.5 training set: 429 real pairs and 571 synthetic rows. The real pairs come from public repositories under Apache-2.0, MIT, BSD-3-Clause or CC0-1.0, plus 21 rows from the Opentrons protocol library, which states no licence (used for non-commercial research on the owner's decision). protocols.io was excluded. An agent labelled the families and no person checked them.

## Rules

Code was written during the hackathon (first commit 2026-11-12) by a team of four, one workstream each, with Claude Code agents. Scope is protective antigens only; nothing supports raising virulence, transmissibility or immune escape (README §0, §8). The output is candidates and an untested protocol, not a vaccine.
