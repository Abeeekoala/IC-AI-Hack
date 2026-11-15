# VaxForge demo: production reset

Production plan, 15 November 2026. **Latest owner direction:** see [Demo UI and Figma canvas journey](DEMO_UI_PLAN.md) for the product screens, real Anthropic/local-adapter request-and-test flow, and revised 1:56 canvas-navigation timeline. That brief supersedes the earlier timing and narration below; retain the evidence and production standards here. Supersedes the previous eight-shot plan. **This is a production plan and replacement script, not a rebuilt or approved film.** The existing 115.97-second MP4, its narration and storyboard remain the old version. Do not distribute it as the revised cut.

## Creative direction

**From a pathogen name to a shortlist a researcher can interrogate, and an experiment handoff they can review.**

The researcher is the protagonist. Their question is: “Which candidates deserve our lab's attention, and why?” Show the answer developing through one workflow. The promise is reduced manual triage; the measured evidence is the duration of an offline fixture run, not a measured saving in researcher hours.

Target **116 seconds**, including a four-second ending hold; hard ceiling 120 seconds. A self-contained voice-over is the recommended format, with captions so the film also works muted. No presenter must talk over the soundtrack. Aim for calm, interested delivery, not a trailer voice or a rapid feature recital.

The owner explicitly authorized pushing the completed plan directly to main. That supersedes the earlier local-only restriction for these documentation changes and the usual PR workflow. No board posts, issue creation, Figma edits, video uploads, purchases, hosted voice generation or model calls are part of this planning pass. Work is isolated from the owner's dirty checkout in `wt_demo_plan` and rebased onto current main before pushing.

## Why the old cut feels weak

Reviewed the build script, storyboard, terminal capture, sampled contact sheet and source evidence. The old MP4 probes at 115.970612 seconds. Audio quality criticism comes from the owner; this review did not independently audition it.

- Full-page screenshots carry navigation, dense tables and paragraphs. The viewer has to hunt for the point while listening to a different sentence.
- The builder fits images to width and scrolls tall ones from top to bottom. That moves content because of image height, rather than because the story needs it.
- Image changes use equal or preset weights inside a scene. They are not tied to spoken phrases or actions.
- Eight separately generated speech clips reset cadence; adding 0.7 seconds after each clip cannot create a continuous performance.
- The film switches between light Figma screens, dark reports, terminal JSON and a dashboard without a stable visual frame.
- A long Bexsero detour interrupts the journey before the researcher sees the useful handoff. Keep the result in the write-up; do not call it validation.
- The ending is a limits page. End with the user's desired outcome, while keeping the relevant limits next to each claim.
- “Every candidate ... a Boltz-2 structure” can imply a continuous successful run. The offline chain did not run Boltz; the CPU sample is separate, with different proteins.

Changing the voice alone will not solve these problems.

## What professional production practice contributes

These are production references, not claims that any vendor guarantees a prize:

1. [Wistia's production guide](https://wistia.com/blog/video-production): plan the script and shoot for the edit; rehearse, record multiple takes and leave handles at shot boundaries. Apply this as a timed audio storyboard before expensive capture work. Record two seconds before and after each UI action.
2. [Wistia's own product demo](https://wistia.com/demo): the published transcript introduces user needs and then demonstrates the associated product actions. Apply that task-first structure here; do not copy its much longer scope.
3. [Screen Studio's animation guide](https://screen.studio/guide/animations): cursor movement and screen animation can be controlled separately; its focused mode settles quickly for reading. Apply restrained focus changes and stable reading holds. This is a reference for behavior, not a purchase recommendation.
4. [Wistia's export guidance](https://support.wistia.com/en/articles/8268259-media-export-settings): use a conventional MP4/H.264 delivery file and constant frame rate. Produce a local 1920×1080 master; verify the actual export.

Our specific creative choices: one narrative spine, one visual frame, short readable evidence inserts, one continuous voice performance, and cuts motivated by actions. Avoid decorative zooms, stock laboratory footage, fake typing, swooshes and cinematic transitions.

## Target and sample organism

The demonstration target is one bacterium, *Neisseria meningitidis*, shown through the serogroup B case used for the Bexsero benchmark. In every label keep three things apart: what the researcher types, what the pipeline resolves it to, and which proteins the recorded run holds.

| Role | Exact label | Matching artifact |
|---|---|---|
| Intended research target (what is typed) | “Neisseria meningitidis” · species · NCBI taxid 487 · Gram-negative. Demonstration case: serogroup B (the README's Bexsero benchmark). | `demo_data/run_nmb_fixture/s0_taxonomy.json`: `payload.query`, `payload.taxon`, `payload.organism.gram_stain` |
| Sample organism in the recorded run | “Neisseria meningitidis serogroup B, strain MC58” · ATCC BAA-335 · NCBI taxid 122586 · UniProt reference proteome UP000000425, release 2026_04 | `s1_acquire.json`: `payload.proteome` (`name`, `strain`, `taxid`, `upid`, `release`) |
| Proteins in the film | “40 of 2,003 proteins · fixture subset”: fHbp, NadA, NHBA, PorA, the 8 strongest VFDB hits, then a seeded random sample | `s1_acquire.json`: `payload.proteome.fixture_subset`; `fixtures/nmb_fixture/FIXTURE.md` |
| Other strains the artifacts name | Strain M2091 (ATCC 13091, serogroup B, UP000005526) is S1's comparison proteome. The real-tool run's S4 conservation gate compared against M2091 and strain NM3081 (UP000005241). The offline demo folder did not run that gate. | `s1_acquire.json`: `payload.comparison_proteome`; `results/nmb_real_run/s4_filter.json`: `conservancy`; `demo_data/run_nmb_fixture/s4_filter.json`: `gates_unavailable` |

Label rules. S0 resolves a plain name to the species only; S1 then takes that species' UniProt reference proteome, which is strain MC58. Nothing in the run chooses a serogroup or strain beyond that. A label may say “Neisseria meningitidis” for the request and “strain MC58” for the proteins, but must not say the run covers all strains or serogroups, or a complete proteome. Suggested short on-screen form: “Neisseria meningitidis · strain MC58 (serogroup B) · 40 of 2,003 proteins · fixture subset”. `results/nmb_real_run/` has the same S0–S2 organism and proteins (identical query, taxon and proteome fields) with the real-tool S3–S12 output. The Boltz sample (see the evidence lock below) is the same strain but a different protein sample.

## Replacement timeline and narration

The narration below is the proposed master. Captions must be generated from the final recorded words, not independently paraphrased. The on-screen labels are additional evidence, not substitute subtitles. Timings are edit budgets, to be checked against the recorded performance.

| Time | What the viewer sees and why | Exact voice-over |
|---|---|---|
| 0:00–0:09 | Start on a clean product view with the pathogen name “Neisseria meningitidis” and a shortlist preview. Headline: “Which candidates deserve lab time?” No logo-only intro. | A pathogen has thousands of proteins. Which ones deserve a lab's time? VaxForge brings the triage into one reviewable workflow. |
| 0:09–0:24 | Show the launcher design, clearly tagged “Figma design”, then a clean insert of the real captured run. Keep “Neisseria meningitidis · strain MC58 (serogroup B) · 40 of 2,003 proteins · fixture subset” visible (labels: see Target and sample organism). Show “S0–S2 stored; S3–S12 offline” and “Unavailable tools: not run”. The capture says 26.5 s; do not replace it with a different run's time. | Start with a pathogen name. This demonstration uses forty proteins from a stored fixture. The offline stages finish in under a minute, with unavailable tools clearly marked. |
| 0:24–0:37 | Signature Figma-to-product transition (detailed below): establish the editable Ranked candidates frame, align its heading with the real report, then hand off to the browser. Click a real candidate link and land on its evidence. Persistent label: “Incomplete screening”. | Here is the Figma design, alongside the working report. Open a candidate to inspect its evidence and missing checks. The shortlist is a starting point for review. |
| 0:37–0:54 | Explicit cut to “Separate Boltz-2 sample · CPU · no MSA”. Show the three mean-confidence values 58.2 / 67.4 / 36.3, with “Low confidence; weak structural evidence”. If the original structure files can be verified, show one matching fold beside its own confidence. Otherwise use the confidence chart; never invent a molecule. | Boltz-2 adds structural evidence. This separate CPU smoke run used no sequence alignment, and confidence is low. VaxForge exposes that uncertainty so researchers can judge how much weight to give the prediction. |
| 0:54–1:09 | Return explicitly to “Offline demo report”. Highlight the protocol's falsification condition, then the real “36/36 simulator PASS” summary. Keep “Placeholder parameters · no lab testing” visible. Use the same candidate as before when the artifacts permit it. | The handoff includes a protocol with a condition that could disprove the hypothesis, plus thirty-six robot programs that passed simulation. These still require scientific review; no laboratory testing has happened. |
| 1:09–1:30 | Adaption proof insert in the same visual frame. Header “Code-generator training · earlier runs”. Show “1,000 rows: 429 real / 571 synthetic; leakage check: 0 violations”. Then hold two equally legible evaluation cards together: “9B · Adaption evaluation: adapted 94% / base 6%” and “4B · our held-out simulator test: 0 wins / 0 losses / 10 ties; neither arm passed”. Label “Different models and evaluations; no demonstrated quality improvement”. | We used Adaption to fine-tune the code generator on one thousand leakage-checked rows. Its nine-billion-parameter run reports ninety-four percent wins. Our separate held-out test of the four-billion adapter found no measurable difference: neither arm passed simulation. |
| 1:30–1:42 | Brief design-system callback: show a status-pill component and variables in Figma, then match-cut to the corresponding real report state. Show “16 editable screens · 19 colour variables”. Keep the result visible longer than the editor. Do not claim all colours are bound to variables. | Designed in Figma, the product makes evidence, missing checks and next actions visible together, so researchers can see what needs attention. |
| 1:42–1:48 | Return to report handoff; readable status card “Not cleared for synthesis” with “Missing toxicity results” and “No provider screening record”. Retain literal artifact state `blocked` in the evidence detail. | Synthesis remains blocked because toxicity results and provider screening are missing. The report makes those outstanding requirements explicit. |
| 1:48–1:52 | Return to the opening shortlist/handoff composition. End line: “From pathogen to a reviewable research shortlist.” Small but readable: “Candidate hypotheses · no lab validation”. | VaxForge: less manual triage, more focused research. |
| 1:52–1:56 | Four-second ending hold. Export target 1:56; the remaining four seconds before 2:00 are contingency, not additional content. | No additional speech. |

The eight spoken scenes plus closing line are 218 words (whitespace count; scene counts 20, 27, 27, 32, 30, 36, 21, 18, 7). Check per-scene budgets against the recorded delivery before capture. Do not speed up dense scientific terms to force a fit. Trim wording or reallocate time first. Finish the closing line by 1:52 and hold to 1:56. The six-second S12 line is the tightest beat: borrow up to two seconds from the preceding Figma beat if natural delivery needs it.

## Signature transition: Figma into the working product

Owner direction: make the design-to-product handoff a memorable visual moment, then demonstrate actual behavior. Put the primary transition at 0:24, where the story moves from running the pipeline to reviewing its shortlist. This replaces footage within the existing 13-second scene; it adds no runtime.

| Time | Capture and edit |
|---|---|
| 0:24–0:26 | Show the genuine Figma editor on “03 Ranked candidates”, with the layer panel visible. Select the heading layer so editability is unmistakable. Label “Figma design”. |
| 0:26–0:28 | Ease into the frame until the “Ranked candidates” heading and first evidence row become the visual anchor. The editor chrome moves out of view through the crop; avoid a dramatic fly-through. |
| 0:28–0:29 | Match the real report's heading position and scale to the design heading. Use a 10–14-frame soft wipe at 30 fps, keeping the heading stationary while the actual report replaces the design. Change the label to “Working report · recorded fixture run”. |
| 0:29–0:34 | Continue with an uninterrupted browser recording. Move the cursor to a candidate's real link, click, and let the browser navigate to its evidence anchor. Hold the candidate identity and source evidence long enough to read. |
| 0:34–0:37 | Focus on the missing-check state and provenance. Finish the sentence over the working report, then move to the separately labelled Boltz sample. |

The smoothness comes from a shared visual anchor, controlled motion and continuous narration. The click AFTER the transition is the proof that we have reached working software. No dissolve over the click or its response, and no invented loading state.

Implementation evidence checked: `src/vaxforge/report.py` says its sections follow the Figma shell; `ranked()` emits candidate links to real `#c…` anchors. `render()` includes ranked candidates, candidate detail and the other report sections. This is an offline HTML evidence report, not a live run launcher. It is not pixel-identical to the Figma design: the report's overall layout and navigation differ. Align a heading/card crop rather than implying a seamless full-screen implementation. If the crops still do not match cleanly, use a brief side-by-side labelled “Design” / “Working report”, then expand the report. Do not redraw the report to fake fidelity.

The process log also records HTML-to-Figma imports followed by editor work; this was iterative design and implementation, not verified one-click Figma-to-code generation. Narration therefore says “the Figma design, alongside the working report”. The transition communicates the relationship without inventing development chronology.

Capture both views at the same display scale and output resolution. Record actual editor selection and actual report navigation separately, with two-second handles. Freeze the pointer during the wipe, then use the real pointer movement from the browser clip. Keep the fixture badge visible once the report is on screen; do not blend mismatched scores or candidate identities across the wipe. Rehearse with a candidate present in both views, or transition on the heading alone when design/sample data differ.

The later 1:30 Figma beat is a short callback: component → implemented state → researcher action. It should explain why the design is useful rather than repeat a tour of the editor. This is an edit specification; these transitions have not yet been captured or rendered.

## Visual and transition treatment

Use the existing Figma palette and typography; neutral background, one accent, generous margins. Keep one outer frame across report, Figma and Adaption inserts. Do not recolour source evidence in ways that change status meaning.

At 1080p use roughly 48–64 px headlines, 28–36 px important labels, and at least 24 px source labels. Check at 50% playback size. If a source screenshot cannot be read, crop closer rather than adding a caption that asks the viewer to trust it. Subtitles get a dedicated lower safe area and never cover a status or metric.

Each beat: establish the view, perform one action, hold the result. Use direct cuts for navigation; carry the next sentence across a cut where it sounds natural. Use a short eased zoom only to direct attention to a specific result. Settle before the key number is spoken. No constant panning. Keep the cursor quiet while reading.

Use clear source badges: “Figma design”, “Recorded offline run”, “Separate Boltz sample”, “Earlier Adaption run”. These prevent polished editing from falsely implying a fully connected live application. Do not show a Figma click causing a real execution unless that integration exists.

## Voice and sound decision

**Recommendation: a natural human voice-over, recorded separately from screen capture.** A teammate who understands the product can make technical language feel intentional. It need not be an on-camera performance. Record two full takes in a quiet soft-furnished room, microphone distance consistent, then pickups for difficult terms. Preserve breaths and short pauses. Use one continuous edited voice track.

If human recording is unavailable, audition a natural synthetic voice on the opening and the Adaption paragraph before committing to the full script. Use an already available local engine first. A hosted voice service requires explicit permission to send the script; any cost requires explicit permission too. No voice cloning. Do not assume that changing macOS Daniel's rate is sufficient. No specific replacement voice has been auditioned or selected in this planning pass.

Audio finish: light noise cleanup, gentle compression, consistent loudness (working target about −16 LUFS integrated, true peak at or below −1 dBTP). Check on headphones and laptop speakers. Start without music. Add only a licensed, unobtrusive bed if it improves the film; narration must remain effortless to understand. No synthetic UI clicks or transition whooshes.

## Evidence lock and factual boundaries

| Claim / asset | Source and required qualification |
|---|---|
| Offline run under a minute | `demo/term/run.txt` records 26.5 s. Final-main repeats 15.8 / 16.7 / 17.0 s are documented in `docs/WRITEUP.md`; no new timing was run for this plan. These are execution durations, not person-hours saved. |
| Fixture scope | `fixtures/nmb_fixture/FIXTURE.md`, recorded run and write-up: 40 of 2,003 proteins of *N. meningitidis* strain MC58 (UniProt reference proteome UP000000425); stored S0–S2; S2 localisation stand-in, not live PSORTb. Exact labels and artifact fields: Target and sample organism, above. |
| Missing stages | Captured run: four S4 gates, epitope tools, coverage and Boltz did not run. Do not draw a funnel in which “not run” becomes “passed”. |
| Boltz | `docs/examples/s7_structure.real_mc58_boltz_cpu.json`: means round to 58.2 / 67.4 / 36.3. Same strain (MC58) but a different run and protein sample from the offline demo: the file's own note says it is not the 40-protein fixture and none of the three folded proteins is a Bexsero antigen. No GPU/MSA run, affinity, antigen discovery, design or validation claim. No refolder agreement result. |
| Adaption platform result | `docs/examples/s9_5_adaption_evidence.real_9b.json`: best_win_rate 0.9439; existing dashboard capture shows 94 / 6. It is the platform's evaluation, not our held-out result. No leaderboard rank. |
| Training composition | `docs/examples/s9_5_manifest.real_4b.trimmed.json` and write-up: 1,000 rows, 429 real and 571 synthetic. Say rows, not 429 distinct source protocols. Leakage check is not human verification of labels. |
| Held-out result | `docs/examples/s9_5_winrate_report.real_4b.json`: 0 wins, 0 losses, 10 ties, neither arm passed. Different model from 9B; no measurable difference is not demonstrated equivalence or improvement. |
| Simulator | Existing report/capture and `wt_a/runs/demo/s9_robot.json`: 36 passing sessions. Simulation is not experimental correctness or safe execution. Do not imply these were produced by the successful 9B adapter. |
| Figma | `docs/design/FIGMA_EVIDENCE.md`: 16 editable screens, 19 colour variables, status-pill component, owner cards. Process evidence, not proof of implementation or usability testing. |
| S12 | `wt_a/runs/demo/s12_biosafety.json` and `src/vaxforge/s12_biosafety.py`: toxicity not evaluated; provider record not supplied. Keep blocked. Virulence review flags are separate and are not the blocking cause in this run. |

Keep Bexsero's 0/3 circular result and LOPOV's unrun status in the write-up and optional Q&A evidence. Removing their dedicated scene does not turn the pipeline into a validated one. Do not say “validated shortlist”, “ready for the lab”, “vaccine”, efficacy, safety, lab testing, leaderboard placement or measured researcher hours saved.

S12 is a local handoff check before synthesis, not a provider's screening service. A stage envelope of `ok` means it wrote its result; that result can still be `blocked`. Do not turn it green for the video, invent a provider record, or equate a reviewed draft with clearance. No provider was contacted.

The Boltz award case remains limited by the available evidence. Better editing can make the use understandable; it cannot supply the missing GPU/MSA result or scientific validation. A new run would be separate work owned by stream B and would require permission for uploads/compute.

## Practical production sequence

1. **Story and evidence lock (45–60 min estimate).** Confirm the script against the listed artifacts; choose the exact report candidate and verify each crop. Save a local evidence ledger with file, digest/run, number, source mode and shot. No live reruns needed for planning.
2. **Voice test and audio storyboard (30–60 min).** Make a 20-second audition; then assemble the full voice with rough stills. Test whether an unfamiliar viewer can explain the input, useful output and major limitation. Fix the story before polishing.
3. **Capture (60–90 min).** Record short local report interactions and read-only Figma editor evidence. Keep two-second handles. Crop browser chrome and personal account details. The Figma storyboard is currently the old version; do not silently update its remotely saved page under local-only authorization.
4. **Edit (90–150 min).** Put voice on the timeline first; place captures at spoken cues; build only the explanatory overlays needed. Use an existing local editor if available. Existing ffmpeg tooling can assemble a draft, but its image-weight timing and per-shot `say` generation must not dictate the final edit. If extending it, support imported voice, explicit cue times, intentional crop/hold regions and actual screen clips; do not build a new video platform.
5. **Review and finish (30–45 min).** Check every shot and transition at full speed, muted, and at half display size. Fix readability and audio. Render only after the story works.

Estimated focused production effort: roughly 4–7 hours, excluding waiting for a human recording or new evidence. This is an estimate, not a promise or a claim about research time saved.

## Acceptance and handoff

- New filename, e.g. `vaxforge_demo_v2_review.mp4`, local only. Preserve the old export and storyboard for comparison.
- Probe duration ≤120 seconds; target 116 including end hold. No speeded-up narration to pass the check.
- Make a contact sheet containing every shot, plus boundary frames; inspect all and play the entire film. A contact sheet alone cannot verify transitions or sound.
- Every visible number has a source in the ledger. Inspect fine print too; remove stale fixture metrics and unrelated account UI.
- No emails, personal account names, local user paths, avatars, account menus or notifications. Review source footage and final export; crops must hold throughout transitions.
- Captions transcribe the final audio. This plan, the new storyboard and captions share identical narration. Update all together in the next authorized production change; do not claim the old MP4 matches this replacement script.
- Keep `docs/WRITEUP.md` factually aligned. It may include more detail than the film. No new scientific claims or results are introduced by this revision.
- Readable simultaneous Adaption labels: 9B platform evaluation and 4B held-out simulator result. No leaderboard insert.
- Boltz sample switch is explicit. S12 still says blocked with the plain-English reason.
- Final local package: MP4, captions, storyboard/script, evidence ledger, contact sheet. Owner receives local paths; publication and delivery remain the owner's decision.

## Main-branch reconciliation

Current main adds a recorded 2B leaderboard entry in `docs/WRITEUP.md`; the 9B and 4B runs remain unlisted according to that update. Preserve that write-up and do not repeat the obsolete statement that none of the runs is listed. The film still shows no leaderboard row or rank, consistent with the owner's requested scope. Main also supplies `demo_data/run_nmb_fixture/` with simulator-backed fixture artifacts; those are not outputs of the planned new Anthropic/local-model request.
