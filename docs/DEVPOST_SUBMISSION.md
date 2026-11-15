# Devpost submission text

Paste-ready, Imperial AI Hackathon, 2026-11-15. Numbers trace to `docs/WRITEUP.md` and the README. The video is separate (`docs/design/DEMO_VIDEO_PLAN.md`).

**Name:** VaxForge
**Tagline:** From a pathogen name to ranked antigens and robot code

## Short description

**The problem.** A bacterium has thousands of proteins; only a few deserve lab time. Choosing them takes weeks of manual triage across a dozen tools, and the result rarely says how far to trust it.

**What we built.** Type a pathogen name. VaxForge returns a ranked shortlist of candidate vaccine antigens with the evidence behind each rank, a protocol that says what result would prove each candidate wrong, and Opentrons OT-2 robot code that passes the simulator (36 of 36 sessions in the demo run). Researchers test the best candidate instead of doing triage. Tested on *Neisseria meningitidis*, the bacterium behind the Bexsero vaccine.

**How it works.** Thirteen stages, each reading the previous stage's hashed artifact, so `vaxforge verify` re-checks the whole chain: Vaxign-ML ranking, BLAST+/AlgPred2/ToxinPred2 safety screens, MHCflurry/BepiPred epitopes, HLA population coverage, Boltz-2 structure, protocol, OT-2 code, animal-study design, biosecurity gate. On the saved 40-protein run, stages 3 to 12 take 16 to 17 s offline on a laptop.

**How far to trust it.** It narrows; it does not prove. The Bexsero check is circular (the stock model was trained on those antigens; recall@3 is 0 of 3), and a stage that did not run says "not run".

## How we worked with Figma, Adaption and Boltz

One rule across all three: every claim sits next to its evidence and its limit.

- **Figma: design first.** 16 editable screens with colour variables and a status-pill set. Each screen has an owner card naming who owns the stage and what to check, and "not run" and "unverified" are designed states. The file is the team's UI spec (`docs/design/FIGMA_EVIDENCE.md`).
- **Adaption: measure, don't trust.** We built a 1,000-row dataset (429 real pairs, 571 synthetic, all simulator-checked, 0 leakage violations) and fine-tuned the OT-2 code generator with AutoScientist on three model sizes. The best, Qwen3.5-9B, reached a **best win rate of 0.9439 against the base model** (adapted 94, base 6); 4B reached 0.7832 and 2B 0.6037. These are Adaption's numbers on evaluations it derives from our data. Our own held-out simulator test of the 4B adapter showed no measurable difference, so we report both. The demo's code comes from templates; the fine-tune is not in that path. The leaderboard lists our 2B run (Task Win Rate 60.4%, Domain Win Rate 35.5%); the 9B and 4B runs are not listed there.
- **Boltz: confidence stays visible.** S7 reports mean pLDDT and the share of epitope residues below pLDDT 70. Our CPU smoke run (three proteins, no alignment) gave 58.2, 67.4 and 36.3, mostly low-confidence, and we show that. The saved real-tool run did not run Boltz. No affinity claim.

## APIs, models and datasets used

**APIs:** NCBI Taxonomy, UniProt REST, AlphaFold DB, IEDB population coverage, Adaption AutoScientist, Anthropic API (optional S8/S9 backends; `claude-sonnet-5-5` 13 of 13 sessions, `claude-haiku-5-5` 4 of 13; demo runs use templates), Figma.

**Models and tools:** Vaxign-ML (stock), BLAST+ 2.17.0, AlgPred2 1.4, ToxinPred2 1.1, MHCflurry 2.3.28, BepiPred-3.0, Boltz-2 2.2.1, Opentrons simulator 7.5.0, Qwen3.5-9B/4B/2B (fine-tuned via Adaption). Not run: NetMHCpan, NetMHCIIpan, DiscoTope-3.0; the demo's localisation is a UniProt-text stand-in for PSORTb.

**Datasets:** UniProt *N. meningitidis* MC58 proteome (2,003 proteins; a 40-protein fixture for the demo), VFDB, Protegen (validation; Vaxign-ML was trained on it), IEDB/AFND, Text2WetLab (MIT; held-out evaluation only), and our 1,000-row S9.5 training set. Its real pairs come from public repositories under Apache-2.0, MIT, BSD-3-Clause or CC0-1.0, plus 21 rows from the Opentrons protocol library, which states no licence (non-commercial research, the owner's decision). protocols.io was excluded. An agent labelled the families; no person checked them.

## Rules

Code written during the hackathon (first commit 2026-11-12) by a team of four, one workstream each, with Claude Code agents. Scope is protective antigens only; nothing supports raising virulence, transmissibility or immune escape (README §0, §8). Output is candidates and an untested protocol, not a vaccine.
