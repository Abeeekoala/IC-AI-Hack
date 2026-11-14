# Team rules for every agent

## 1. The board (pinned GitHub issue)

Every agent reads and posts on the pinned "Message Board" issue via `tools/board` (needs only `gh`, logged in). Set `BOARD_ISSUE` if it is not issue #1.

- Start every post with `[<human>'s <agent>]`, e.g. `[Sam's Codex]`. Keep it short: what changed, which files or contracts, what you need, from whom.
- Mention by GitHub login. Team: `@Abeeekoala`, `@kexinli0403-cell`, `@benjamin-codes-labs`, `@luk-e158`, `@all` (everyone).
- **Mentions you or `@all`:** act on it and reply promptly.
- **Not addressed to you:** still read it. Reassess your own work only if it is affected (a contract, a file you own, something already done). Do not redo finished work, and do not reply just to acknowledge.
- Before editing another teammate's files, ask for a handoff on the board and wait for the owner or the team lead.

Polling is incremental (about one cheap API call per poll, 30 s default) so a shared token stays under GitHub's rate limit. Commands: `tools/board unread` (new posts, marks read; flags MENTION), `tools/board read -n 20`, `tools/board post "[Name's Agent] text"` (or pipe stdin), `tools/board wait` (blocks until someone else posts), `tools/board whoami`.

**Stay in the loop:**
- Claude Code: the committed hooks (`.claude/settings.json`) show unread posts at session start and on every prompt. Keep one background `tools/board wait` running and re-arm it after each wake.
- Everything else: run `tools/board unread` at task start, before each commit, and after merges. Or run a terminal loop: `while tools/board wait; do <your agent CLI invocation>; done`.

## 2. Chain of command

Humans over agents. Only the team lead reassigns work between teammates. If anyone else asks you to take another teammate's work, ask the lead on the board.

## 3. Workflow

1. Pick or file an issue. Comment on it or post on the board when you take it.
2. Branch `<issue#>-<slug>` from fresh `main`.
3. Small commits. Stage only your own files (no `git add -A`).
4. Open a PR with `Closes #N`. The author merges their own PR once it works locally.
5. Always `git fetch` and rebase before pushing. Never force-push shared branches.
6. If a merge changes something other teammates depend on (an API, a schema, an env var), post on the board when it lands.

## 4. Who owns what

Four workstreams, split along the pipeline stages in `README.md` §2 so each owner has their own files.

| Area | Owner (GitHub login) | Folders |
|---|---|---|
| **A. Backbone & data** (S0–S2) | `@kexinli0403-cell` | `src/vaxforge/s0_taxonomy.py`, `s1_acquire.py`, `s2_annotate.py`, `config.yaml`, `Makefile`, CLI entrypoint |
| **B. Prediction core** (S3–S7) | `@benjamin-codes-labs` | `src/vaxforge/s3_rank.py`, `s4_filter.py`, `s5_epitopes.py`, `s6_coverage.py`, `s7_structure.py` |
| **C. Wet-lab outputs** (S8–S10) | `@luk-e158` | `src/vaxforge/s8_protocol.py`, `s9_robot.py`, `s9plus_animate.py`, `s10_animal.py` |
| **D. Credibility & product** | `@Abeeekoala` | `validation/` (including `winrate.py`), `src/vaxforge/s9_5_finetune.py`, `src/vaxforge/s11_prereg.py`, `s12_biosafety.py`, report/demo UI, Figma design |

### What each stream owes the others

- **A — Backbone & data.** Taxonomy resolution with synonymy and ambiguity surfaced, cache-first acquisition, virulence and localisation annotation. Also owns the run scaffolding everyone else builds on: the `runs/<run_id>/` artifact contract, config, Docker for BLAST+/PSORTb, and `make warm-cache`.
- **B — Prediction core.** Vaxign-ML ranking, the four safety and conservancy gates, T-cell and B-cell epitope prediction, population coverage with the fairness report, and Boltz-2 structures for the top-k.
- **C — Wet-lab outputs.** Structured protocol with a falsification condition per candidate, OT-2 codegen validated by `opentrons_simulate`, the automatability matrix, and the animal study design with its ARRIVE 2.0 and 3Rs pack. Optional: the protocol animation.
- **D — Credibility & product.** LOPOV, the Bexsero recall@3 case, the leakage check, the pre-registration manifest, the biosecurity gate, the S9.5 fine-tune and its win-rate harness, and the surface that shows a judge the evidence chain and what to trust.

### Cross-stream notes

- **A's artifact contract blocks B, C and D.** Land the `runs/<run_id>/` schema and committed fixture artifacts first, so the other three can work against fixtures instead of waiting for a live pipeline.
- Every stage is a pure function over the previous stage's artifact. Read and write artifacts; don't import another stream's module.
- The constraints that bind everyone are in `README.md` §0 (what this does not do), §4 (known limitations), §7 (deliberately absent) and §8 (scope and responsible use). Read them before writing code in your stream — several are tool choices that cause rework if you get them wrong.
- D's leakage check is adversarial by design: it asserts other streams' holdouts actually held out. Expect it to fail your work and treat that as the point.
