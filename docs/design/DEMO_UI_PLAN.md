# Demo UI and Figma canvas journey

15 November 2026. Implementation brief, not a claim that the UI or new model runs exist. Requested by the owner for the next demo video. Read with `DEMO_VIDEO_PLAN.md`; the timeline here supersedes its earlier shot timing. Target 1:56, hard limit 2:00.

## The experience

A researcher sees a whole research workspace, chooses a request, follows real computation and inspects the result. The film moves like a person exploring the Figma canvas: overview → zoom into a screen → transition to the working product → perform an action → zoom back out → move to the next screen. Keep spatial continuity, rather than cutting between unrelated slides.

The product is a focused local web workspace with a left workflow rail, a central task area and a right evidence drawer. The Figma canvas is the film's map; the shipped UI does not need an infinite canvas. Keep the same typography, card shapes, spacing and status colours across design and implementation.

## Canvas arrangement

Arrange six primary frames left to right in two rows, with clear numbered headings and generous whitespace. A small design-system frame sits beside them. Avoid attempting to read all frames at overview scale.

```
01 Request       → 02 Candidate review → 03 Structure evidence
                                              ↓
06 Handoff       ← 05 Test results     ← 04 Generate with models
```

Use three major zoom journeys, not a zoom for every sentence:

1. Overview → Request → candidate review.
2. Overview reposition → Structure → generation workspace.
3. Overview reposition → Test results → Handoff → whole-canvas ending.

Each journey has a stable landing and a meaningful real interaction. Show Figma chrome briefly at the opening to prove the source, then minimize visual distraction. Keep a small “Figma design” label until the transition into the working UI, where it changes to “Working product · recorded run”.

## Screens to implement

| Screen | Primary content and action | Evidence and states |
|---|---|---|
| 01 Request | Pathogen/run selector; explicit fixture source; compact research-request field; “Review candidates”. Separate “Prepare model request” action after selecting a suitable task. | Show 40 of 2,003 fixture proteins; recorded offline stages. Do not imply typing a name fetched or screened a complete proteome. Fields: ready, loading, completed, failed. |
| 02 Candidate review | Ranked list; select one candidate to open evidence drawer. Show score source, missing gates and next review action together. | Read existing stage artifacts; no invented “validated” score. Preserve candidate/run identity through navigation. |
| 03 Structure evidence | One verified Boltz sample's structure if its original file is available, otherwise confidence chart; run mode and confidence beside the visual. | Explicit “Separate CPU/no-MSA sample”; means 58.2, 67.4, 36.3. Missing structure file gets an honest empty state, not a decorative molecule. |
| 04 Generate with models | One reviewed, bounded software task, with two output lanes: “Anthropic API” and “Local Adaption fine-tune”. “Generate both” submits the task; the UI shows each lane's actual progress independently. | Exact model IDs, timestamps, duration, output excerpt, completion/refusal/error status. Local lane includes verified adapter identity. “Test generated code” is enabled only for retained, eligible outputs. |
| 05 Test results | Side-by-side results on the actual outputs: lint, simulator, rule checks, each with a log drawer. Show outcome and failure explanation in plain English. | Code digest connects generation to testing; simulator version and exit result; missing tool is “Not run”. A single demo task is not a benchmark or win-rate result. |
| 06 Handoff | Candidate evidence, draft protocol, generated code and test record in one reviewable bundle. “View report” opens real report. Show outstanding requirements. | S12 “Not cleared for synthesis”; missing toxicity and provider screening. Keep scientific review distinct from software checks. |

On screen 04 the task should be a benign, existing liquid-handling software task using non-biological or water-only materials, chosen from an approved fixture. The viewer sees the same logical task given to both generators; exact provider wrappers and parameters remain in evidence. This demonstrates model-backed implementation without inventing a new biological experiment or extending the fine-tune into a pathogen-ranking role.

Do not make the natural-language request an unrestricted agent terminal. Parse the user's intent into a bounded task, show the actual request before dispatch, and execute only the defined generation/test workflow. No claim that the current repository already supports a general conversational orchestrator.

## Real execution, not progress animation

The intended flow is:

```
UI request → local job record → same bounded task
                               ├─ Anthropic Messages API → saved output A
                               └─ local model + verified adapter → saved output B
saved outputs → lint → isolated simulator/rule checks → result artifacts → UI
```

Anthropic and the local fine-tune are parallel generation lanes, not a base-versus-adapter experiment. Claude is not the local adapter's base model. The existing held-out comparison remains a separate result. Never imply that the earlier 36 template programs were generated by this new request.

Keep secrets on the backend. Anthropic uses the existing SDK integration and server-side credentials; the local model uses a loopback endpoint. No API key field in a recorded browser, no keys in responses/logs, no account identity in footage. Official API reference: https://platform.claude.com/docs/en/api/messages/create . Model ID is explicitly configured and recorded rather than guessed from a marketing name.

Minimum job record: job ID, task ID, source run, request digest, start/end times, model ID, provider response/request identifier when exposed, adapter digest and loaded-model evidence, decoding parameters, output digest, stop reason, error/refusal state, test tool/version, test exit status and log reference. UI events must derive from the job, never from timer-based success animation. Label recorded/replayed jobs visibly; preserve actual elapsed time if the film cuts a wait.

Use real states: queued → generating → generated → checking → passed/failed/not run. Also handle refused, truncated, timed out, unavailable and cancelled. Cancellation may stop local work without undoing a provider request already sent. Never silently substitute a template result when a model request fails. Keep retry attempts distinct; do not replace a failed take's output while retaining its original timing.

Before a local lane says “fine-tuned”, verify the adapter was loaded. The repository documents an MLX routing pitfall: requesting a Hugging Face base model name can drop the adapter. Use the documented adapter-serving route, record server startup/config evidence and checkpoint digest, and confirm the served identity. A digest alone proves a file exists, not that it was used. If identity is unverified, label that state and do not film it as a successful fine-tuned request.

Generated Python is untrusted. Existing lint and environment scrubbing are useful but are not isolation. Run simulator work in a disposable constrained worker with no credentials, no network and bounded time/resources. No hardware connection or lab execution. Show failures honestly; a simulator pass does not establish biological validity.

## Existing foundation and implementation gaps

Checked in the repository:

- `src/vaxforge/report.py`: artifact-driven HTML report and candidate links; its launcher only displays recorded input and does not start jobs.
- `src/vaxforge/s9_llm.py`: real Anthropic `messages.create` code-generation path. `s8_narrative.py` also has an Anthropic-backed narrative option. Reuse approved stage interfaces rather than creating a second incompatible generation path.
- `src/vaxforge/s9_5_generate.py`: OpenAI-compatible local generation route, saved outputs and generation manifests. It is not yet a complete product UI for the paired demo task.
- `src/vaxforge/s9_validate.py`: lint, simulator and grading support; needs an isolated execution wrapper for the UI workflow.
- `demo_data/run_nmb_fixture/`: committed demo artifacts, regenerated with 36 simulator PASS sessions on current main. Read `README.md` and `FIXTURE.md`; these are pre-existing fixture results, not outputs of the new model request.

Build next: local UI shell, bounded job API, two provider adapters, adapter verification, isolated test worker, event/polling status and evidence drawer. Use existing project tooling; do not introduce a general agent framework, distributed queue or new training pipeline. Stages continue to exchange artifacts. UI may orchestrate public stage entrypoints; do not introduce cross-stream module dependencies inside pipeline stages.

Product work is stream D. Changes to S8/S9 require stream C handoff; scaffolding/CLI changes require stream A handoff. This plan does not assign their work or edit their files.

## Revised filming timeline

| Time | Canvas movement and working proof |
|---|---|
| 0:00–0:08 | Whole Figma canvas, human cursor movement, headline question “Which candidates deserve lab time?” Ease into Request. |
| 0:08–0:21 | Match design into working Request/Candidate UI; choose the fixture and inspect a candidate. Keep fixture and missing-check labels. |
| 0:21–0:27 | Zoom back to Figma overview; pan toward Structure; land and hold. Movement has no tiny text the viewer must read. |
| 0:27–0:41 | Structure evidence, separate sample label, confidence/run mode. Use an actual original fold only if verified. |
| 0:41–0:47 | Pull back to canvas; move to Generate with models; transition into real UI. |
| 0:47–1:08 | Submit the reviewed request to Anthropic and the local adapter. Show request → actual generation → retained outputs. Cut long waits with “Elapsed time …; wait shortened”. No promised inference duration. |
| 1:08–1:23 | Real “Test generated code” action and results/log drawer. Keep output identity continuous. Show the observed outcome, including failure. |
| 1:23–1:40 | Compact Adaption training evidence: 1,000 rows; 429 real/571 synthetic; 9B platform result 94/6 next to 4B held-out result 0/0/10, neither passed. These earlier evaluations are separate from today's demonstration. |
| 1:40–1:51 | Pull back briefly, land on Handoff; view real report and explain missing synthesis requirements. |
| 1:51–1:56 | Return to the full canvas, ending on the product promise. Hold with no extra feature list. |

For each canvas move, use approximately 0.8–1.2 seconds to pull out, 0.5–0.8 seconds to reposition and 0.8–1.2 seconds to settle, followed by a readable hold. These are initial edit targets, not constant motion. Preserve the same canvas layout throughout. No spinning, flying through layers, bounce or repeated zoom pulses. The wider view reorients the viewer before each new section.

At each Figma-to-product boundary, align a shared heading/card and transition over roughly 0.3–0.45 seconds. The next click and visible response must be genuine screen recording, not an animated mock. If implementation differs, show a brief labelled design/implementation comparison. Never fake pixel-level fidelity.

## Recording script status

The previous narration in `DEMO_VIDEO_PLAN.md` is an editorial draft, now superseded in timing. Final speech for the new generation/testing sequence must be written after the actual run: no prewritten success claim. Suggested neutral line: “We send this request to Anthropic and our locally running fine-tuned model. Each output keeps its source. Now we test the generated code and inspect what passed, what failed, and why.”

Keep total narration around 210–230 words. Figma is proven through the whole-canvas opening, editable layers and transitions, so no separate long design-system tour is needed. Preserve the earlier truthful Boltz and Adaption qualifications. Update storyboard, narration and subtitles together when the new footage exists.

## Delivery gates

Plan-only scope is authorized now, including pushing these documents to main. No new API/model calls, Figma changes, uploads or paid work are performed by this plan. The next implementation/recording pass must establish the exact task, configured models and permitted cloud request before dispatch.

Before calling the new video ready: implemented screens run against actual artifacts; real Anthropic response retained; local adapter loading verified; both outputs tested or explicitly marked failed/not run; no stale result substitution; no credentials or personal data; every number traceable; captions match actual speech; every shot and transition reviewed; final MP4 ≤120 seconds. The existing video does not satisfy the new request-and-testing brief and must not be relabelled as if it does.
