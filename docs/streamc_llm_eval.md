# S9 LLM code path: live evaluation (task G3)

Calls made on 2026-11-14, 21:02 to 21:17 UTC. Driver, call ledger, raw replies, generated protocols, attempt records and tables are in `docs/streamc_llm_eval/`. No source file, test file or other doc was changed; this report and that directory are the only things created.

## 1. Result

pass@1 = PASS / n, one attempt per (model, task). n counts every task, refused ones included.

| mode | model | n | PASS | FAIL | REFUSED | ERROR | UNVERIFIED | pass@1 | PASS among tasks the model attempted | Wilson 95% of pass@1 (read section 7 first) |
|---|---|---|---|---|---|---|---|---|---|---|
| spec | claude-sonnet-5-5 | 13 | 13 | 0 | 0 | 0 | 0 | **13/13** | 13/13 | 0.77-1.00 |
| spec | claude-haiku-5-5 | 13 | 4 | 2 | 7 | 0 | 0 | **4/13** | 4/6 | 0.13-0.58 |
| free | claude-sonnet-5-5 | 12 | 10 | 2 | 0 | 0 | 0 | **10/12** | 10/12 | 0.55-0.95 |
| free | claude-haiku-5-5 | 12 | 10 | 2 | 0 | 0 | 0 | **10/12** | 10/12 | 0.55-0.95 |
| spec, template backend (no model; baseline) | - | 13 | 13 | 0 | 0 | 0 | 0 | 13/13 | - | - |

- Of the 50 model attempts, 37 passed, 6 failed and 7 were refused. None ended as ERROR or UNVERIFIED: no API error, no rate limit, no SDK retry, no crash in our code.
- Spec mode, Haiku: 7 of its 9 non-passes are provider refusals (`stop_reason == "refusal"`), 2 are code failures. On the 6 tasks it attempted it passed 4; Sonnet passed all 6 of those. Sonnet refused nothing.
- Free mode: both models 10/12, but the steps that failed differ (Sonnet: s06, s11; Haiku: s02, s12). No step failed for both models.
- Model calls: **53** (budget 80). That is 50 attempts (25 tasks x 2 models) plus 3 calls lost in flight when the first run was cut off (section 2.2). 27 calls were not used. The ledger spans two runs with two different API keys.
- For comparison, the task brief reports an earlier manual run of about 14 calls (spec 8/8, free 5/6; its free-mode failure was `ctx.fixed_trash[...]`). It is not part of this ledger, and the brief does not name its model. Sonnet's counts here (spec 13/13, free 10/12) are in the same range, and `fixed_trash` did not recur (section 5).

## 2. What was run

### 2.1 Set-up

| item | value |
|---|---|
| S8 protocol | `build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1), ProtocolConfig(readouts=[ELISA, RT_QPCR, FACS]))`. `validate_protocol` returns no error. 14 steps; 12 are robot-assisted (s02-s09, s11-s14); s01 gene synthesis and s10 THP-1 culture are human-only. |
| models | `claude-sonnet-5-5`, `claude-haiku-5-5`. The responses report these same ids. |
| spec mode | `compile_protocol(protocol, out, backends=[LLMBackend(model_id=M)], animate=False)`: 13 sessions (s12 is split into `s12_elisa_setup` and `s12_elisa_develop`). The plan is in the prompt. Graded by `grade_source(code, plan=plan)`: static lint, Opentrons 7.5.0 simulation, the five physical rules, plan check (deck, volume received per well, initial liquids, number of hand-off pauses). |
| free mode | For each of the 12 robot-assisted steps: `CodegenRequest(session_id="<step>_<kind>", plan=None, protocol_steps=[that step as a dict])`, `LLMBackend(model_id=M).generate`, then `grade_source(code)`: lint, simulation, the five rules, structure checks (a `def run(ctx)`, apiLevel at most 2.19), pipette-range and temperature warnings. No plan, so no plan check. |
| backend settings | The backend's own: `SYSTEM_PROMPT` (2,021 characters, includes the "API 2.19 pitfalls" note; SHA-256 prefix `551ef7002b82`, identical in all 53 calls), `max_tokens=12000`, no temperature or seed set, SDK default retries. |
| prompts | Both models got the same user prompt for each task (25 tasks, one prompt hash each, also across the re-issued calls). |
| how calls were made | Every call went through `LLMBackend.generate`. The driver's `CountingLLMBackend` only wraps the backend's client with a recorder (ledger line before the call, raw reply, usage) and refuses a second call for the same (model, mode, task) and any call beyond 80. |
| software | opentrons 7.5.0 (separate interpreter), `anthropic` 1.11.0, Python 3.14.6, Windows. |
| outcomes | PASS, FAIL, REFUSED, UNVERIFIED (not simulated), ERROR (no code: API error, or a reply without a protocol), as in `SessionReport.outcome`. |

### 2.2 Two runs, one ledger: key change, cut-off, resume

- The API key was changed in the middle of the work. The first run was cut off when its API account ran out of credit; a second run continued it with the new key. `calls.jsonl` spans both runs (`seq` 1-53 is continuous across them).
- First run, 21:02:20 to 21:03:46 UTC (calls 1-8): the two free-mode canary calls (Sonnet and Haiku on s02) and the first 6 calls of the spec/Sonnet batch. 5 of the 8 have a recorded reply. 3 (spec/Sonnet s04, s06, s07) were in flight when the run stopped: a `call_start` with no `call_end`. They count as made against the budget; whether they were billed is not known.
- Second run, from 21:11:54 UTC (calls 9-53). The driver gained a `--resume` mode: a recorded reply is replayed to the backend instead of being sent again (ledger event `replay`); a call with no recorded end is re-issued once (ledger field `reissue_of`); anything else already attempted stays blocked. A scripted-client self-test of this path (`run_eval.py selftest-resume`, 9 checks) passed before any live call.
- Reused, not called again (5 replays): spec/Sonnet s02, s03, s05 and the two canaries (free/Sonnet s02, free/Haiku s02). The partial output of the interrupted batch was removed and rebuilt from the recorded replies. The code of s02, s03 and s05 after the rebuild is byte-identical (SHA-256) to what the first run had written.
- Re-issued once each (3 calls, `seq` 9-11): spec/Sonnet s04, s06, s07. The prompts are hash-identical to the lost calls. A lost call has no recorded reply, so no result had been seen when it was re-issued; each of these tasks still has exactly one recorded attempt.
- Accounting: 53 started = 50 with a recorded reply (43 `end_turn`, 7 `refusal`) + 3 lost. 0 calls ended with an API error, 0 SDK retries were logged, 0 calls were blocked by the guard. No task was attempted twice.

| model | mode | calls with a reply | input tokens | output tokens (as reported) | median latency s | most output tokens in one call |
|---|---|---|---|---|---|---|
| Sonnet | spec | 13 | 40,542 | 31,535 | 15.2 | 4,080 |
| Sonnet | free | 12 | 11,534 | 32,256 | 19.2 | 5,886 |
| Haiku | spec | 13 | 40,542 | 43,947 | 13.1 | 10,040 |
| Haiku | free | 12 | 11,534 | 55,724 | 17.0 | 10,734 |

No call stopped on `max_tokens` (cap 12,000). The three lost calls have no usage figures.

### 2.3 Baseline

`compile_protocol(..., backends=[TemplateBackend()])` on the same protocol: 13/13 PASS in Opentrons 7.5.0, no model call (`template_baseline/`).

### 2.4 Not run

Second attempts of any task, prompt variants, other models, the S9.5 fine-tune, the S9+ animation (`animate=False`). The 27 unused calls were left unused on purpose.

## 3. Outcome per task

Spec mode (plan in the prompt):

| session | Sonnet | Haiku |
|---|---|---|
| s02_plasmid_assembly | PASS | REFUSED (api_stop_reason) |
| s03_transformation | PASS | PASS |
| s04_colony_pcr | PASS | FAIL: TipNotAttachedError |
| s05_expression_induction | PASS | REFUSED (api_stop_reason) |
| s06_lysis | PASS | PASS |
| s07_ni_nta_purification | PASS | FAIL: TypeError |
| s08_endotoxin_removal | PASS | PASS |
| s09_lal_assay | PASS | PASS |
| s11_antigen_stimulation | PASS | REFUSED (api_stop_reason) |
| s12_elisa_setup | PASS | REFUSED (api_stop_reason) |
| s12_elisa_develop | PASS | REFUSED (api_stop_reason) |
| s13_rt_qpcr | PASS | REFUSED (api_stop_reason) |
| s14_facs_prep | PASS | REFUSED (api_stop_reason) |

Free mode (S8 step only):

| session | Sonnet | Haiku |
|---|---|---|
| s02_plasmid_assembly | PASS | FAIL: LabwareDefinitionIsNotLabwareError |
| s03_transformation | PASS | PASS |
| s04_colony_pcr | PASS | PASS |
| s05_expression_induction | PASS | PASS (3 pipette_range warnings) |
| s06_lysis | FAIL: ProtocolCommandFailedError | PASS |
| s07_ni_nta_purification | PASS | PASS |
| s08_endotoxin_removal | PASS | PASS |
| s09_lal_assay | PASS | PASS |
| s11_antigen_stimulation | FAIL: ProtocolCommandFailedError | PASS |
| s12_elisa | PASS | FAIL: no_aspirate_from_empty_well x41 |
| s13_rt_qpcr | PASS | PASS (24 pipette_range warnings) |
| s14_facs | PASS | PASS |

The same data per attempt (rule counts, plan findings, error kind, stop reason, tokens, liquid statistics, code hash) is in `docs/streamc_llm_eval/results.csv` and `results.json`.

What the grades say, over the 50 attempts:

- 38 programs simulated, 5 failed in the simulator, 7 were refused (no code graded). The static lint flagged nothing.
- Rule violations in the 38 programs that simulated: `tip_before_aspirate` 0, `no_overdispense` 0, `no_aspirate_from_empty_well` 41 (one program: free/Haiku s12), `tip_dropped_at_end` 0, `no_cross_contamination` 0; "picked up a tip while one is attached" 0. For the 5 programs that failed in the simulator the rules were not evaluated.
- Plan check (spec mode, runs only after a successful simulation): 17 programs, 0 error findings, 0 warnings.
- A PASS here is not an empty protocol. All 17 passing spec programs have the same number of aspirations, dispenses and the same total uL aspirated as the template's code for that session (for example s13: 414 / 414 / 6,072 uL). Every passing free-mode program moves liquid (at least 1 aspiration, at least 30 uL).

## 4. Refusals

**7 refusals in 50 attempts. All 7 are claude-haiku-5-5 in spec mode (7 of 13); 0 of 13 for Sonnet in spec mode; 0 of 12 for each model in free mode.** The "no refusal was observed" case did not occur. The detector path `api_stop_reason` was exercised by 7 real replies. The `text_pattern` and `harness_error` paths were not exercised by any real reply; they are still tested only with scripted replies.

| session | stop_reason | detector kind | input tok | output tok | latency s | content blocks | text chars |
|---|---|---|---|---|---|---|---|
| s02_plasmid_assembly | refusal | api_stop_reason | 1973 | 886 | 5.9 | thinking | 0 |
| s05_expression_induction | refusal | api_stop_reason | 2092 | 2778 | 13.0 | thinking + text | 2942 |
| s11_antigen_stimulation | refusal | api_stop_reason | 2844 | 0 | 1.0 | none | 0 |
| s12_elisa_setup | refusal | api_stop_reason | 3810 | 1476 | 13.1 | thinking | 0 |
| s12_elisa_develop | refusal | api_stop_reason | 6549 | 0 | 1.1 | none | 0 |
| s13_rt_qpcr | refusal | api_stop_reason | 7387 | 5072 | 24.9 | thinking | 0 |
| s14_facs_prep | refusal | api_stop_reason | 2393 | 3956 | 18.4 | thinking + text | 3953 |

- Every one of the 7 ended with the provider's `stop_reason == "refusal"`. `LLMBackend.generate` reported them as refused (outcome REFUSED, `refusal_kind` `api_stop_reason`), the attempt records say `"outcome": "refused"`. Nothing crashed. None was retried or reworded.
- What the replies look like: 2 have no content block at all (0 output tokens, about 1 s); 3 have only a thinking block and no text; 2 (s05, s14) contain the start of a Python block that stops in mid-comment with the fence still open. Per `refusal.py` the stop reason decides first, so these two were not graded and their fragments were not used. Judged by the text alone, 5 of the 7 would have read as "no code", and the other 2 would have gone to the lint as a Python fragment and failed it with "syntax error: invalid syntax (line 1)" (checked with `extract_python` and `static_lint`). The classification depends on the harness passing `stop_reason` on.
- Which sessions: refused were s02 plasmid assembly, s05 expression induction, s11 antigen stimulation, s12 ELISA (both sessions), s13 RT-qPCR and s14 FACS prep. Not refused: s03 transformation, s04 colony PCR, s06 lysis, s07 Ni-NTA, s08 endotoxin removal, s09 LAL assay. README S9 expected refusals on Golden Gate and E. coli transformation prompts; here the transformation session was not refused and the readout sessions were. I have no explanation for the pattern.
- Haiku was not refused in free mode on the same 12 steps. The two prompts differ in many ways. Only the spec prompt carries the candidate line (`# Candidate: c1 (fHbp)`), the S12 biosecurity header ("Do not order gene synthesis or run these protocols until the S12 gate clears the sequence"), and the full deck/volume/action list. Whether any of it matters was not tested: pass@1 forbids a second attempt, and README S9 says not to work around refusals.

## 5. Failures

13 of the 50 attempts did not pass: 6 FAIL (below) and the 7 REFUSED (section 4). The cause is the error text in the grade (`Grade.sim_error` / findings); the labware names were checked against the 89 definitions shipped in the simulator's opentrons 7.5.0.

| # | cell | session | kind (`Grade.sim_error_kind`) | cause, one line | group | could the current system prompt have prevented it? |
|---|---|---|---|---|---|---|
| 1 | spec / Haiku | s04_colony_pcr | TipNotAttachedError | line 85: `p20.drop_tip()` right after `p20.distribute(19, ..., new_tip="once")`, which already drops its own tip; the plan and the template have no such step | tip handling | Only in part. "Do not add, drop or change steps" is there and was not followed; nothing says that `transfer`/`distribute` handle their own tips. |
| 2 | spec / Haiku | s07_ni_nta_purification | TypeError | line 29: `ctx.define_liquid(name=..., description=...)` without the required `display_color` (the only one of the 43 generated programs that omitted it) | API call signature | No. The prompt names `define_liquid(...)` with no arguments. |
| 3 | free / Sonnet | s06_lysis | ProtocolCommandFailedError (inner: FileNotFoundError) | line 9: labware `nest_12_well_reservoir_15ml` does not exist in 7.5.0 (the name is `nest_12_reservoir_15ml`) | labware identity (invented load name) | No. The prompt has no list of valid load names; spec mode gets them from the plan, free mode must supply them. |
| 4 | free / Sonnet | s11_antigen_stimulation | ProtocolCommandFailedError (inner: FileNotFoundError) | line 10: `opentrons_24_tube_rack_eppendorf_1.5ml_safelock_snapcap` does not exist (the name is `opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap`) | labware identity (invented load name) | No, same reason. |
| 5 | free / Haiku | s02_plasmid_assembly | LabwareDefinitionIsNotLabwareError | line 15: `temp_mod.load_labware("opentrons_96_well_aluminum_block")`; that definition is an adapter (`allowedRoles: ["adapter"]`), not labware | labware identity (adapter used as labware) | No. The prompt says to load module labware with `module.load_labware(name)` and says nothing about adapters. |
| 6 | free / Haiku | s12_elisa | none (simulated); `no_aspirate_from_empty_well` x41 | `reservoir["A1"]` is declared with 12,000 uL; the 8-channel head (`p300_multi_gen2`) draws 8 x 200 uL = 1,600 uL per aspiration, 48 aspirations draw 76,800 uL; 7 aspirations fit, then the well holds 800 uL and each of the other 41 asks for 1,600 | volume bookkeeping (declared contents) | No, and the wording points the wrong way: rule 3 in the prompt says "non-reservoir well", but the checker tracks a reservoir well once its contents are declared (section 8). |
| 7-13 | spec / Haiku | s02, s05, s11, s12 x2, s13, s14 | REFUSED | the provider ended the turn with `stop_reason == "refusal"` | refusal | Not a prompt matter. By design (README S9) it is reported, not worked around. |

Groups: labware identity 3 (all free mode); API call signature 1; tip handling 1; volume bookkeeping 1; refusal 7.

- Prevented by what the prompt already says: none of the six code failures cleanly; #1 is the nearest (a generic instruction was not followed). Not preventable by the current prompt: #2 to #6 and the refusals. Whether a prompt addition would have prevented #1 to #6 was not tried (nothing was changed, and a second attempt would break pass@1); a signature line for `define_liquid`, a note on complex commands, a list of valid load names and a corrected rule 3 are the obvious candidates.
- Not seen: `ctx.fixed_trash[...]`, the failure of the earlier manual run, is in 0 of the 43 generated programs. There is no run without the pitfall note, so this does not show that the note is the reason. No missing-declaration failure occurred in the 38 programs that simulated. Pipette-range problems appear only as warnings in two PASS programs (free/Haiku s05, s13: volumes below the pipette minimum, for example 10 uL on a 20-300 uL pipette); the grader does not fail them.

## 6. Attempt records (stream D format)

Written with `vaxforge.s9_attempt.make_record(..., split="probe")` from the recorded replies (`finish_reason` = the API's `stop_reason`), one file per mode and model:

| file | lines | outcomes | simulate_pass true / false / null |
|---|---|---|---|
| `attempts_spec_claude-sonnet-5-5.jsonl` | 13 | attempted 13 | 13 / 0 / 0 |
| `attempts_spec_claude-haiku-5-5.jsonl` | 13 | attempted 6, refused 7 | 4 / 2 / 7 |
| `attempts_free_claude-sonnet-5-5.jsonl` | 12 | attempted 12 | 10 / 2 / 0 |
| `attempts_free_claude-haiku-5-5.jsonl` | 12 | attempted 12 | 11 / 1 / 0 |

- 50 attempts, 50 records, 0 attempts left unrecorded (a harness error would not be recorded; none occurred). Each file loads with `json.loads` per line (checked independently of the driver) and with `validation/winrate.py` `load_records`. Task ids are unique within a file (`spec:<session>`, `free:<session>`). `split` is `probe`, `model` is `base` in every record (the stream D arm label; the model is named by the file).
- Consistency: `simulate_pass` and `rule_violations` in the records agree with the S9 grade for every attempt. The records are graded without the plan, so a spec-mode record cannot show a plan violation; there was none. `gt_diff` is null everywhere because no reference run was supplied.
- Cross-check: two records re-made with `python -m vaxforge.s9_attempt record ... --split probe --code ...` (free/Sonnet s02, free/Haiku s12) are identical to the driver's lines.
- Free/Haiku s12 is "simulate_pass true, rule_violations 1" (one of the five rules, 41 times), which is why that file has 11 simulating programs but 10 PASS.

## 7. What this does not show

- **n is small.** 13, 13, 12 and 12 tasks, one attempt each, one S8 protocol. The Wilson intervals only illustrate how little that fixes a rate. They treat tasks as independent draws, and they are not: one protocol, one prompt, two sessions from the same step (s12). They are not a claim about the models. README S9.5 itself says a repeat of one model moved single-task scores by up to 0.70 in Text2WetLab.
- **One attempt per task.** A task passing or failing is one sample. The free-mode tie (10/12 each, with disjoint failures) cannot be separated from sampling noise with these data. Haiku's 7 of 13 refusals against 0 of 13 for Sonnet on identical prompts is a large gap, but which sessions get refused is also one sample. No repeat was made, so run-to-run variation is not measured. The backend sets no temperature or seed.
- **Refusals are a provider behaviour on one day.** 7 of 13 for Haiku in spec mode, 0 elsewhere. Not retried, so it is unknown whether the same prompt is refused again, and what triggers it.
- **The models may have seen Opentrons code.** Opentrons protocols and docs are public. Nothing here says how much of 13/13 or 10/12 is recall; the three labware-name failures are the opposite kind of evidence (names that are wrong).
- **PASS is not correctness.** PASS means: lint clean, simulated in Opentrons 7.5.0, none of five rules broken, and in spec mode the plan matched. It says nothing about chemistry, and every volume, time and temperature is a placeholder. In free mode the grade has no plan: two Haiku free programs (s04, s07) contain no step banner comment, two have volumes below the pipette minimum (warnings only), and nothing checks that the program does what the S8 step asks.
- **Spec mode measures transcription.** The 17 passing spec programs reproduce the template's pipetting totals exactly. The model is given the deck, volumes and order; passing shows it can copy them into valid code, not that it can design a protocol.
- **The grader is the stream's own.** Same stream wrote the prompt and the rules. Reservoir wells with no declared contents count as unlimited stock, so under-declaring passes silently. The run is a simulation, not a robot.
- **Only these models and this setup.** Two models, one system prompt, default `max_tokens`, Opentrons 7.5.0. 48 of 50 responses contained a thinking block although the backend does not ask for one; the reported output tokens include it.
- **The two runs used different keys, and 3 calls were lost.** The results are not affected as far as the ledger shows (identical prompts, one recorded attempt per task), but the 3 lost calls were sent and their server-side fate is unknown.
- **Template code is the reproducible baseline.** It passed 13/13 with no model and gives byte-identical output for the same plan. LLM code differs on every call. The raw replies in `raw_replies.jsonl` are what makes this run reproducible.

## 8. Notes for the stream owner

1. **Haiku refused 7 of 13 spec sessions** (section 4). The pipeline handled it as designed. Any harness that does not hand the provider's end-of-turn label (`stop_reason` here) to the detector would score 5 of these 7 as "no code" and 2 as a syntax-error FAIL; I did not check how other harnesses in the repo treat it.
2. **Rule 3 in `SYSTEM_PROMPT` is narrower than the checker.** The prompt says "never aspirate more than a non-reservoir well holds" and also asks the model to declare the contents of every well it draws from. `check_physical_rules` tracks a reservoir well as soon as it is declared (its docstring: "Reservoir wells are treated as ample stocks UNLESS they appear in `initial`"). Seen once: free/Haiku s12, 41 violations. Update (2026-11-14): the checker now tracks a reservoir well only if its liquid is declared in the plan (fixed 2026-11-14), which matches rule 3's wording; the prompt itself was not changed so that the measured prompt stays the one reported. Two precisions from the regrade (section 10): "declared" means a `load_liquid` call in the protocol (in spec mode the plan lists them, in free mode the model chose them), and a declared reservoir well is still tracked, so free/Haiku s12 is graded as before (41 violations) and the gap between rule 3's text ("non-reservoir") and the checker remains for declared reservoir wells.
3. **`Grade.sim_error_kind` is generic for labware that is not found**: `ProtocolCommandFailedError` for failures #3 and #4; the cause (`FileNotFoundError: Labware "..." not found`) is only in `sim_error`. Grouping by kind alone merges different causes.
4. **`docs/streamc.md` section 7 looks out of date.** It says the simulator subprocess gets a copy of the parent's environment, including `ANTHROPIC_API_KEY`. `s9_validate._scrubbed_env()`, which `simulate` uses, drops every variable whose name contains KEY, TOKEN, SECRET, ANTHROPIC and similar; I confirmed that in-process with dummy variables. I did not test the lint for bypasses.
5. **Free mode needs labware names the prompt does not give.** 3 of the 24 free attempts failed on a labware name or role. Spec mode is not exposed to this (the plan supplies the names). `load_adapter` was used by none of the 43 programs.
6. **`LLMBackend` sets no temperature or seed**, so a rerun gives different code. Keep the raw replies if a result has to be re-graded.

## 9. Files and how to reproduce

In `docs/streamc_llm_eval/`:

- `run_eval.py` (driver: `template`, `spec [--resume]`, `free`, `records`, `selftest`, `selftest-resume`, `leakcheck`), `report_tables.py` (prints the tables quoted here), `_probe_offline.py`.
- `calls.jsonl` (ledger, one event per line: `call_start`, `call_end`, `replay`, `resume`), `raw_replies.jsonl` (50 replies as received, text only).
- `attempts_{spec,free}_{model}.jsonl` (stream D records), `results.csv`, `results.json`, `results.md`.
- `spec_<model>/robot/llm_<model>/` (code, plan, setup and grade per session; the simulation logs are not committed, see the next item), `spec_<model>/robot_report.json|md` and `workflow.md`, `free_<model>/` (code and `<session>.attempt.json`), `template_baseline/`.
- The simulator logs `*.sim.json` (one per spec-mode session that was graded, 19 of the 26; about 7 MB in all) were not committed. Re-running `run_eval.py` regenerates them: `python docs/streamc_llm_eval/run_eval.py spec --model <model> --resume` replays the recorded replies (section 2.2) and simulates the sessions again. It rebuilds the whole `spec_<model>/` directory, so the grade files there are then written by the checker as it is at that time (section 10). Free mode never wrote simulator logs. `regrade.py` (section 10) simulates the saved code again and keeps no logs either.

Offline rebuild of records and tables from the recorded data (re-simulates, no network): `python docs/streamc_llm_eval/run_eval.py records`, then `python docs/streamc_llm_eval/report_tables.py`. `spec --model M --resume` and `free --model M` replay the recorded replies and send nothing. Anything new (another model, another prompt) needs its own data directory, because the guard allows one attempt per (model, mode, task).

## 10. Regrade with the corrected check

Added after the run (task H7, 2026-11-14). No measured number of sections 1-9 was touched. Two notes were added there: the rule 3 note in section 8 and the missing simulator logs in section 9.

**Why.** After this run, a comparison with Text2WetLab's own grader (`docs/streamc_t2wl_agreement.md`) found four defects in `leaf_events` / `check_physical_rules` (`src/vaxforge/s9_validate.py`). They are fixed. The numbers in sections 1-9 come from the check before the fixes; this section gives the current check's result on the same code.

| fix | what the check did before |
|---|---|
| air_volume | the aspirate under an `Air gap` command was counted as liquid drawn from the well it hovers over |
| air_visit | the same aspirate counted as a visit of that well by the tip (rule 5, `no_cross_contamination`) |
| reservoir | dispensing into an undeclared reservoir well (for example a mix) made it a tracked well, so the next draw from it was held against what the mix had put in |
| tolerance | the two volume rules allowed 1e-6 uL of slack; Text2WetLab's `spec_check` allows 1e-9 |

In the agreement doc the first two are finding 1, the other two are findings 2 and 3.

**How.** `docs/streamc_llm_eval/regrade.py`, offline: 0 model calls, no network, no import of `LLMBackend` or `anthropic`. For each saved protocol it makes the call `run_eval.py` made: `grade_source(code, plan=plan)` in spec mode (the plan is the saved `<session>.plan.json`, loaded with the S9 plan model) and `grade_source(code)` in free mode. That is lint, a new Opentrons 7.5.0 simulation (the simulator logs were not kept) and the five rules. The saved code is byte-identical to what was graded (SHA-256 equal for 43 of 43; the reloaded plan hash equals the recorded one for 19 of 19 spec programs). The 7 refused attempts (spec, Haiku) produced no code, so there is nothing to regrade: they stay REFUSED. "Before" is the original attempt records plus `results.json`; the two agree for all 50 attempts.

**Result: no outcome changed, and no violation count changed.** Before -> after, exact counts:

| mode | model | n | PASS | FAIL | REFUSED | ERROR | UNVERIFIED | pass@1 |
|---|---|---|---|---|---|---|---|---|
| spec | claude-sonnet-5-5 | 13 | 13 -> 13 | 0 -> 0 | 0 -> 0 | 0 -> 0 | 0 -> 0 | 13/13 -> 13/13 |
| spec | claude-haiku-5-5 | 13 | 4 -> 4 | 2 -> 2 | 7 -> 7 | 0 -> 0 | 0 -> 0 | 4/13 -> 4/13 |
| free | claude-sonnet-5-5 | 12 | 10 -> 10 | 2 -> 2 | 0 -> 0 | 0 -> 0 | 0 -> 0 | 10/12 -> 10/12 |
| free | claude-haiku-5-5 | 12 | 10 -> 10 | 2 -> 2 | 0 -> 0 | 0 -> 0 | 0 -> 0 | 10/12 -> 10/12 |
| all | both | 50 | 37 -> 37 | 6 -> 6 | 7 -> 7 | 0 -> 0 | 0 -> 0 | |

- Rule violations in the 38 programs that simulate (38 before and after): `no_aspirate_from_empty_well` 41 -> 41 (still the one program, free/Haiku s12); the other four rules 0 -> 0; "picked up a tip while one is attached" 0 -> 0.
- Nothing else in the grade moved. The simulator error (kind and text) of the 5 programs that fail to simulate, the number of simulator events, the lint, the error findings and the warning findings are equal in all 43.
- No model's count moved in either direction, and all 50 attempts keep their outcome. The table of every attempt, before and after, is `docs/streamc_llm_eval/regrade_results.md`; the data are in `regrade_results.json`.

**Why nothing moved.** The programs do not touch what the fixes changed:

- Air gaps (air_volume, air_visit): none of the 38 programs that simulate has an `Air gap` sub-step in its run log, and the text `air_gap` is in none of the 43 sources. No model output exercised these two fixes; only the hand-built logs below do.
- Reservoir wells: 6 of the 38 programs dispense into a reservoir well they never declared (spec/Sonnet s07, s12_elisa_develop, s12_elisa_setup; free/Sonnet s07, s12; free/Haiku s07; 80 dispenses in all, 1 to 48 per program). None of them draws from such a well afterwards, which is the case the old check got wrong. free/Haiku s12, the one program that failed on a reservoir, declares that well (12,000 uL, `load_liquid`), and a declared well is tracked before and after: 41 violations either way.
- Tolerance: none of the 3,358 volume checks in the 38 programs has a slack between -1e-6 and -1e-9 uL, the only band where the old and the new tolerance disagree.

**Checks on the method.** A copy of the pre-fix lines of the check, with each fix as a switch (taken from the diff of `s9_validate.py`; an aid for assigning a cause, not a grade), reproduces the original per-rule counts of all 38 programs that simulate. With all four switches on it equals `check_physical_rules` on all 38. Had an attempt changed, this is how the change would have been assigned to one or more of the four fixes. `regrade.py --selftest` runs it on 7 hand-built logs (an air gap over a nearly empty tube, an air gap above a destination, a mix in an undeclared and in a declared reservoir, a dispense and an aspirate 5e-7 uL too large, a clean run): the current check gives the corrected counts on all 7, the pre-fix copy the old ones, and each fix changes exactly the case it should.

**What this does not show.**

- "No outcome changed" is about these 43 programs. They contain no air gap, so the air-gap fixes are not tested by any model output here.
- Four defects found by one comparison are not all that can be wrong. The agreement doc lists limits the fixes do not touch: the check keeps one tip flag for the whole robot (its finding 4), and `tip_before_aspirate` can fire on a program that simulates only through that flag (finding 5). 7 of the 38 programs load two pipettes, all in spec mode (Sonnet s03, s04, s05, s09, s13; Haiku s03, s09); in them a tip left on one pipette could be hidden by the other's flag. That was not examined.
- Nothing was sampled again: it is still one attempt per task, so section 7 applies unchanged, and so does "PASS is not correctness". The 7 refusals have no code and never reach the five rules.

**Versions.** Grader: `s9_validate.py` in the working tree (the fixes are not committed), SHA-256 prefix `0ff525b92995`. Simulator runner `_ot_sim_runner.py`: SHA-256 prefix `8a7cbdb84a27` when this task started. Another run edited it while this task ran (a fix so that a protocol that loads a module below apiLevel 2.14 no longer fails in `_deck_snapshot`): `556b9442d383` after the first edit, `5239ece3e013` after a second one that adds only a docstring. The first regrade ran on `556b9442d383`; because the hash had changed since the start, it was rerun once at the end. **The numbers above and in `regrade_results.*` come from that final run, on `5239ece3e013`** (the hash at its start, at its end and when this task finished); the first run gave the same counts. All 43 saved protocols declare apiLevel 2.19, where the edit returns what the old line returned, so it cannot change them. Full hashes: `regrade_results.json`.

**Files.** `docs/streamc_llm_eval/regrade.py` (about 20 s with 4 simulations in parallel; `--selftest` runs only the offline checks), `regrade_results.json`, `regrade_results.md`. The original attempt records, `results.*` and the other files of the run were only read.
