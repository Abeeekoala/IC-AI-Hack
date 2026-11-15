# Licensing notes and citations

Moved from README sections 9 and 10.

## Licensing notes for dependencies

| Component | Licence | Constraint |
|---|---|---|
| Boltz-2 | MIT | Academic and commercial use |
| AlphaFold 3 | Code Apache 2.0; **weights CC-BY-NC-SA 4.0** | Weights only from Google, by application, at their sole discretion; non-commercial; not for clinical use |
| Text2WetLab | MIT | Benchmark + grading harness |
| BindCraft | MIT | **But it pulls in PyRosetta** |
| PyRosetta | Requires a Rosetta licence | Paper signature; international applications can take months. **Avoid entirely at a hackathon** |
| Vaxign-ML | Open-source Docker build | — |
| Protegen / VIOLIN | Not stated | Contact corresponding author before redistributing |
| Fine-tuned S9.5 model, and its base model | Not determined | Bound by the base model's licence, unknown until the base model is pinned (the three candidates are Apache-2.0 on their own cards); Adaption names no licence for any base model or for the trained weights. The checkpoint is typically a LoRA adapter, which needs the base weights. The training-data licences in §S9.5 also bind. **Read all of them before publishing the weights** |

---

## Citations

Numbers quoted in this README trace to the primary sources below. Where we could not verify a claim, the text says so explicitly rather than filling the gap.

- Pizza et al., *Science* 287:1816 (2000) — reverse vaccinology funnel, 570 → 3
- NERVE 2.0, *BMC Bioinformatics* 25:378 (2024) — fold-enrichment benchmark
- Vaxign-ML, *Bioinformatics* 36(10):3185–3191 (2020) — nested CV + LOPOV protocol
- Text2WetLab — https://github.com/PhysicalAIBenchmarks/Text2WetLab (MIT; self-hosted preprint, not peer-reviewed, no third-party citations found)
- Boltz-2 (2025-06-06) — MIT, affinity module
- ICH M15, EMA/CHMP/ICH/496426/2024 — MIDD general principles
- Lo, Siah & Wong, NBER WP 27176 — vaccine clinical success rates
- Zumárraga et al., *Front. Immunol.* 17:1730217 (2026-02-05) — reverse-vaccinology output quality analysis
- Responsible AI × Biodesign community commitments (2024-03-08)
- Adaption AutoScientist documentation — https://docs.adaptionlabs.ai (read 2026-11-14): model list, create request, win-rate and download pages; leaderboard — https://adaptionlabs.ai/auto-scientist-leaderboard
- protocols.io Terms of Service (effective 2026-01-27) and https://www.protocols.io/llms.txt (read 2026-11-14)
- Opentrons/Protocols — https://github.com/Opentrons/Protocols (`develop` at `2f447c9`; no licence file); Opentrons EULA (2024-07-10)

---

