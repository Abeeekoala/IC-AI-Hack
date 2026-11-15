# How Figma was used on VaxForge: plain log

Process evidence for the "Best-Designed AI Product" (Figma) prize. Every line is something that was done and seen, or says it was not. Times are local on 2026-11-14, read from the shell clock where one was taken and otherwise approximate (marked `~`). Figma Make was not used.

File: `VaxForge`, key `pzytlXMGdl8JuSNivTeOOq`, one page, now named `VaxForge`. The same account in the Figma web editor was driven through browser automation.

## Tools

| Tool | Used for | Used? |
|---|---|---|
| Figma MCP `generate_figma_design` | Import an HTML page, or one element of it, as editable frames, text and shapes | yes, five captures in this work (a test page, import 2, import 3, and the `#fHow` frame twice) |
| Figma web editor, driven by browser automation | Read the layer tree, check results, delete my own superseded imports, make components and variables, read version history | yes: until 22:36, then again after the browser was relaunched at 22:56 |
| Figma MCP `use_figma`, `get_metadata`, `get_screenshot`, `get_design_context` | Script or read the file | no: the plan's monthly call cap was already spent |
| Figma Make | n/a | no |
| TalkToFigma MCP | n/a | no: its relay was not running |

## Log: first session

| Time | What | Observed |
|---|---|---|
| 22:19 | Read `FIGMA_APPLY.md`, `screen_fields.md`, the HTML source, README sections 0, 4, 7, 8 and S9.5, `AGENTS.md`. | Plan: improve the HTML, re-import, then edit in the editor. |
| ~22:20 | Opened the file in the editor. | One page `Page 1`, one top-level frame `VaxForge product design` (3120 x 18620, auto layout), 17 children. File title already `VaxForge`. Layers named by tag (`Section`, `Container`). |
| 22:21 | **Test capture** of a 15-element page with `id`, `class`, `title`, `name`, `data-*`, `aria-label` and semantic tags. | `body` gives `Body`, `main` `Main Content`, `section` `Section`, `div` `Container`, `aside` `Sidebar`, `nav` `Navigation`, `header` `Header`, `li` `List Item`, `p` `Paragraph`, `span` `Text`. `id`, `class`, `title`, `name`, `data-*` are ignored. **`aria-label` is used** as the layer name. The capture was appended to `Page 1`, no new page. Test frame deleted by hand afterwards. |
| ~22:25 | Opened the Variables panel. | No variables in the file. The import path did not turn CSS variables into Figma variables. |
| 22:19 to 22:30 | Wrote a scratch generator (not kept) that builds the v2 page from the v1 page: an `aria-label` on every frame, card and repeated part; an **Owner and attention** card beside each of the 16 screens (content from `AGENTS.md`, README, `screen_fields.md`); new frames `Cover`, `How to read this file`, `Design system`, `How Figma was used`; `C Foundations` renamed `Design system`. Checked in the browser: owner cards fit beside their frames, nothing overflows a frame sideways. | `docs/design/vaxforge_product_design.html` (v2, 20 frames, 17 `own` cards counting the legend sample). |
| 22:30 | **Import 2** (node `8:2`): v2 without `How Figma was used`. | Frames named `Section - 06 Wet-lab protocol`: a semantic tag with an `aria-label` gives `<Tag name> - <label>`. Cover rendered correctly in the editor. |
| ~22:32 | Changed `section`, `aside`, `main` to `div`, and chip, badge, placeholder spans to `div`. | Plain `div` gives the label alone. |
| ~22:35 | **Import 3** (node `11:2`, root `VaxForge product design`, 4160 x 21780, child `Body`). | Names read `Cover`, `How to read this file`, `00 Overview and key`, `01 Run launcher`, `01b Data and annotation`, `02 Pipeline overview`, `03 Ranked candidates`, `03b ...`, `04 ...`, `04b ...`, `04c Structure`, `05 Validation`, `06 Wet-lab protocol`, `07 Robot code`, `07b S9.5 evidence panel`, `08 Animal study`, `09 ...`, `10 Limits and absent`, `Design system`, and `Owner and attention · <screen>` for each card. Seen in the layers panel. |
| ~22:36 | Deleted import 2 in the editor (checked: node `8:2`, X 3160). | Two roots left: the first import (X 0) and import 3 (X 7360). |
| ~22:36 | Zoomed to the `Design system` frame to check how it rendered. | The editor stopped responding. Two screenshots timed out. |
| ~22:37 | Reloaded the file. | "We can't open this file because WebGL isn't supported, or is disabled." A fresh tab gave the same. `getContext('webgl')` and `getContext('webgl2')` returned nothing in a plain page, three times between 22:37 and 22:39. No editor work was possible until 22:57. |
| 22:37 | **Capture 4**, one element (`#fHow`) of the full page, selector in the hash. | Added as a separate top-level frame, node `16:2`. The tool reported success. Not looked at in the editor in that session. |
| 22:38 | Wrote v2 to `docs/design/vaxforge_product_design.html`; wrote `vaxforge_tokens.json`; folded stream C's facts into `screen_fields.md`; updated `FIGMA_APPLY.md`. | Applied the lead's change: S9.5 provenance row now reads "family labels are an agent's reading; no person has checked them; the leakage check is the safeguard", and the "labels unconfirmed" chip is now "leakage check". |

## The WebGL outage (22:36 to 22:57)

The editor could not open the file for about 21 minutes because the browser's WebGL was off. This agent saw the symptom (the editor stopped, then "WebGL isn't supported, or is disabled", and `getContext('webgl')` returning nothing). The cause below was reported by the lead and **not observed by this agent**: Chrome's GPU process crashed three times at 22:33:45, the same minute the lead's local model server died, and Chrome keeps WebGL off until it is fully quit. The timing was observed by the lead; the causation is an inference. The fix was a full quit and relaunch of Chrome at 22:56 (Chrome 157.0.8103.79, GPU crash count 0, WebGL verified on). After the relaunch `getContext('webgl')` returned a context in the editor tab, and it did again at each later check that returned a result in this session (one check was blocked by the tool).

## Log: second session (editor back, after 22:57)

Times from here are approximate. They are bracketed by the file's autosave versions (11:04 PM and 11:15 PM) and the shell clock (23:22 when the doc was written).

| Time | What | Observed |
|---|---|---|
| ~23:00 | Opened the file in my own tab. WebGL probe, then looked at the layer tree before touching anything. | WebGL on. Two roots on `Page 1` (the first import, and `11:2`), plus the `How Figma was used` frame `16:2`. |
| ~23:00 | Deleted the **first import** only (node `1:2`, the one at X 0). | Gone from the layers panel; `11:2` and `16:2` still there. |
| ~23:01 | Set `11:2` to X 0. Selected it and ungrouped it (root), then ungrouped `Body` (`11:3`). Moved `16:2` to X 2120, Y 18960 and zoomed to it. | The 20 frames and 17 owner cards now sit at page level at the HTML's positions. `16:2` renders beside `Design system`. |
| ~23:03 | Renamed the page `Page 1` to `VaxForge`. | Seen in the Pages list. |
| ~23:05 to 23:15 | **Components.** Selected each drawn specimen and made it a component with Cmd+Alt+K, renamed it, then combined the families with "Combine as variants". | Slip, undone: Cmd+Alt+K on nine chips selected together made one wrapping component, not nine; undone with Cmd+Z, then done one chip at a time. Result, from Assets > "Created in this file": **12 components**. Variant sets: `Component / Status pill` (State: ok, not run, refused, incomplete, blocked, failed, needs user input, fixture, info), `Component / Evidence badge` (Kind: FIXTURE, GENERATED, README, DOC, PLACEHOLDER, NOT RUN, NOT ON MAIN, STATE SPECIMEN), `Component / Button` (Variant: primary, secondary), `Component / Nav item` (State: default, current, sub), `Component / Callout` (Tone: ok, blocked, not run, info, bad, warn). Single components: `Component / Placeholder value`, `Step marker`, `Stat tile`, `Card`, `Input`, `Candidate row`, `Honest ceiling`. Node ids seen in the editor address bar when each was made: sets `18:10`, `18:19`, `18:22`, `18:29`, `18:36`; singles `18:23` to `18:25` and `18:37` to `18:40`. Looked at the whole Components card afterwards: every specimen still draws, no detached or empty part. Side effect: a variant set adds its own dashed outline and a little padding, so each set sits slightly inside its specimen card and its outline touches the card label. |
| ~23:05 to 23:15 | Slip, fixed: in the Callout step, three clicks landed on the layer-lock icon instead of the row, which locked three layers. | Padlock icons showed on the three rows; unlocked them by clicking the icon; the icons were gone in the next zoom. |
| ~23:10 | **Variables**, by hand in Local variables: collection `VaxForge tokens`, **25 variables**. | Colour (15): `surface/canvas` D9DBE0, `surface/card` FFFFFF, `surface/bg` F6F6F3, `text/ink` 1B1F24, `text/mute` 59626E, `accent/accent` 0F6B6B, `accent/tint` E0F0EF, `state/ok` 1C6B3B, `state/ok-bg` E2F3E8, `state/warn` 7A4A00, `state/warn-bg` FDECC8, `state/bad` 8E1B1B (the danger colour), `state/bad-bg` FBE3E3, `fixture/bg` FFE08A, `fixture/ink` 4A3200. Number (10): `spacing/` 4, 8, 12, 16, 24, 32; `radius/` badge 4, button 6, card 10, pill 11. That is 15 of the 27 drawn colours, 6 of 9 spacing steps and 4 of 7 radii. **Not bound to any fill or radius**: the screens still carry plain values. The JSON import was **not tried** (it opens a native file picker, which the automation cannot drive). |
| ~23:12 | Renamed layer `16:2` to `How Figma was used` (it carried the suffix `(VaxForge product design)`). | Seen in the layers panel. |
| ~23:13 | **Accident.** I tried to edit the text of `How Figma was used` item 3. Pressing Return on the layer did not open text editing, because the text uses Helvetica Neue, which this browser does not have: the editor shows a "Missing font" dialog first. The sentence I then typed ran as keyboard shortcuts. | Seen afterwards: the item 3 text was wrapped in a new `Frame 1` (Shift+A, add auto layout), and opacity on a layer showed 25%. I pressed Cmd+Z six times (it restored the opacity; the next steps changed the selection among top-level frames, so I could not tell what they undid), then stopped guessing and used version history instead. |
| ~23:15 | **Restored the 11:15 PM autosave** from File > Show version history, after previewing it. | The preview showed the layer already named `How Figma was used`, the Components card with its variant sets, and the 01b owner card at full opacity; it still had the stray `Frame 1`. After the restore the history list shows a `Restored version` entry above the three autosaves. Checked after: Variables panel (25 variables), Assets (12 components), `How Figma was used` frame and the 3 % overview of the page all look right. I did not diff every layer, so a small stray change elsewhere cannot be ruled out. |
| ~23:17 | Removed the stray `Frame 1` (selected it, ungrouped it). | Text back under its `Container`. |
| ~23:20 | Because editing text in the editor is blocked by the missing-font dialog, updated the **HTML** and **re-captured `#fHow`** (capture 5), instead of editing in the editor. | New frame, node `21:2`. Checked by zooming in: item 3 and the `Still open` callout show the new text. The new frame's text layers also list `Helvetica Neue` (checked on the title in the Typography panel, with the missing-font marker), so they cannot be edited in this browser either. |
| ~23:21 | Deleted my own superseded copy (`16:2`), renamed `21:2` to `How Figma was used`, set it to X 2120, Y 18960. | Layers panel shows one `How Figma was used`; position fields read X 2120, Y 18960. |
| ~23:22 | Opened **version history**. | List: `Current version`, `Restored version` (a few minutes old), then 3 autosave versions at 11:15 PM, 11:04 PM and 10:24 PM, all by Abraham. A note says older versions need a Professional plan. |

## State of the file now

| Item | State |
|---|---|
| Page | One page, named `VaxForge`. |
| Frames at page level | Yes: Cover, How to read this file, 00 to 10 (16 screens), `Owner and attention · <screen>` cards, `Design system` (`11:7620`), `How Figma was used` (`21:2`). Root and `Body` were ungrouped. |
| First import (`1:2`), import 2, test capture | Deleted. |
| Components | 12 (5 variant sets, 7 single) in `Design system`. The 16 screens do **not** use them: they are plain frames, not instances. |
| Variables | `VaxForge tokens`, 25 variables (15 colours, 6 spacing, 4 radii). **Fourth session:** the 15 colour variables are bound to the 15 matching colour swatch fills in `Design system`. The 6 spacing and 4 radius variables are bound to nothing, and the screens carry plain values. |
| Sections | None (see below). |
| Font | **Replaced (third session).** In the Missing font dialog I mapped Helvetica Neue (the five styles it listed: Regular, Italic, Semi Bold, Bold, Bold Italic) to Inter of the same weight, and Menlo (Regular, Semi Bold) to JetBrains Mono (Regular, Bold), and clicked Replace fonts. Why: the web editor has no Helvetica Neue, so every text layer opened a dialog and could not be edited, and any judge opening the file would see it. After a page reload the file opened with no Missing font dialog, and a text layer opened by Return showed its text selected (edit mode) and Inter in the Typography panel. Text is now editable in place. |
| Version history | Read: current, one restored version, autosaves 11:15 PM, 11:04 PM, 10:24 PM. |

## Not done, and why

- **Sections** (Overview, Pipeline screens, Evidence, Reference). The frames sit in a two-column grid with each owner card to its right, and the two columns interleave the logical groups (for example `00` is beside `01 Run launcher`, and `09` is beside `Design system`). A Section is a rectangle, so it would cut across groups or need frames to be moved. Not done rather than done wrongly.
- **Binding variables to the screens' fills, and swapping screen parts for component instances.** Each is a large edit of 3,000+ layers. The components and variables exist so that a person can do it. (Only the colour swatches in `Design system` are bound; see the fourth session.)
- **Variable import from `vaxforge_tokens.json`.** Not attempted (native file picker). The JSON is still untested for import. It holds 27 colours, 9 spacing steps and 7 radii; the file's collection holds 15, 6 and 4.
- **Text edits in the editor.** Were blocked by the Helvetica Neue dialog; unblocked by the font replacement above (see the third session).

## For a person

1. Open the file and look at `Design system` > Components, and at `How Figma was used`.
2. If you want the screens to use the system: bind fills to `VaxForge tokens`, and replace repeated parts with component instances.
3. If you want Sections, move frames into the order you want first.
4. Before any new capture, the HTML now loads Inter and JetBrains Mono from Google Fonts (its font stack already listed Inter first; the capture used Helvetica Neue only because Inter was not installed locally). Not tested by a new capture.

## Third session, 2026-11-14 night: font, content, and where I stopped

Paused on the lead's order. Nothing is open: no dialog, no text layer in edit mode, the Figma tab is closed, no local server is running. Nothing was committed.

**Done and seen**

- Font replacement, as in the state table. Seen: no dialog after reload; edit mode works on `How Figma was used` (title text node `21:19`).
- Overflow check. I looked at `07b S9.5 evidence panel` top to bottom at 50% and at `Design system` at 24%.
  - One real overflow, fixed: in 07b, card "How a pair is decided", the "Equal violations..." line (node `11:6082`) ran past the card edge. I set its width to 456 so it wraps, and set its parent row `11:6075` to hug height so the next paragraph does not overlap. The three rows above it now sit slightly tighter than before.
  - **Left, not fixed:** in `How Figma was used`, the line "Frame names match ... docs/design/screen_fields.md." touches the card edge. In the owner cards (seen on 07b), "Credibility and product" and "@Abeeekoala" run together with no space (probably in every owner card). I did not look at screens 00 to 07 and 08 to 10 in detail, nor at the other owner cards.
- Content (PR #37), edited by hand in the editor, each edit checked in edit mode before typing:
  - Owner card 06 (`11:9278`): label now "On main (PR #37)"; badge "ON MAIN" (`11:9382`); the cost line now ends "Not estimated today: no prices are built in."; the last line is now "payload.narratives; steps[].narrative and narrative_source". The block (`11:9381`) was restyled from purple dashed to neutral (fill F6F6F3, stroke D6D9DE solid; badge stroke and text 59626E solid) and renamed "On main block"; the badge layer is named "Evidence badge · ON MAIN".
  - Owner card 08 (`11:9633`): label "On main (PR #37)", badge "ON MAIN", text "S10 design.falsification_map[].rule_if_testable. The README rule still sits beside the row." Block (`11:9726`) fill and stroke colours changed to the same neutral values.
- HTML (`vaxforge_product_design.html`) and `screen_fields.md` were updated to the same wording. I read the field names in `origin/main` (`s8_protocol.py`, `s8_cost.py`, `s8_narrative.py`, `s10_animal.py`); I did not run that code.

**Not done**

- 08 block: the stroke is still **dashed** (colour is neutral); the HTML says solid. Its layers are still named "Pending block" and "Evidence badge · NOT ON MAIN". Fix: select the block, Stroke settings, Style: Solid; rename the two layers.
- Screen `06 Wet-lab protocol`: the HTML now drops the three NOT ON MAIN badges, changes the label to "Needed by this screen. The fields are on main (PR #37), but no committed run artifact holds values yet, so these stay placeholders", and changes the Reagent quantities and Cost estimate bodies (cost: "payload.cost.status is not_estimated today: no prices are built in. cost_gbp_range is None"). **The Figma frame still has the old text and badges**, so the file and the HTML disagree there.
- `00 Overview and key`: the HTML's owner-card specimen now says "Per-candidate uncertainty: roadmap item only, not on main." Figma still says "payload.order_fasta. Names may change."
- `How to read this file` and `How Figma was used` were not updated for this session. Sections and a frame-order list were not done.

## Fourth session, 2026-11-15: leftovers, a full look at every frame, variables, frame order

Resumed on the lead's word. I probed WebGL first (the page script returned `{"webgl":true}`; the editor opened and rendered, no Missing font dialog). All edits are by hand in the Figma web editor; each text edit was made only after the layer showed a caret or an edit box with its text selected. No Figma tool with a call cap was used. Nothing was committed or posted.

**Leftovers from the third session, done**

- `06 Wet-lab protocol` (`11:4570`): the three pending badges were deleted, the label now reads "Needed by this screen. The fields are on main (PR #37), but no committed run artifact holds values yet, so these stay placeholders", and the Reagent quantities and Cost estimate bodies carry the HTML's wording. The three callout layers no longer say "NOT ON MAIN" in their names (`11:5078`, `11:5086`, `11:5094`).
- `00 Overview and key` owner-card specimen (`11:8258`): now "Per-candidate uncertainty: roadmap item only, not on main."
- Owner card 08 block (`11:9726`): stroke replaced with a solid one (D6D9DE); layers renamed "On main block" and "Evidence badge · ON MAIN". Figma and the HTML now agree.
- The two text collisions: in `How Figma was used` (`21:129`) the line no longer touches the card edge; in the owner cards "Credibility and product" and "@Abeeekoala" are separate (seen on 07b, `11:9508`).

**Look at every frame, Inter font, 36 to 59% zoom (screenshots, not code)**

Cause of most problems: with Inter, text layers captured as auto width or a fixed width wider than their card or cell ran past it. Fix used each time: set the text to auto height and a narrower width, or set a clipped table or card to hug its height.

- Fixed: `00` (`11:397`) was **vertically flipped** (the whole frame upside down); flipped back with Shift+V (cause unknown). `01` text `11:1169` and table `11:1149` (hug height). `04b` texts `11:3664`, `11:3650`. `05` callout text `11:4280`. `07` texts `11:5246`, `11:5750`. `08` table `11:6700` (hug height, rows were clipped) and text `11:6900`. `09` texts `11:7152`, `11:7221`. `02` owner-card texts `11:8571`, `11:8553`. `How Figma was used` text `21:129`.
- `Design system` (`11:7620`): in Components, the Callout specimen cell was about 40 px wider than its card (it stuck out of the right edge). I set its row (`11:8129`) to wrap and fill the card, so Callout now sits under Nav item. That made the card taller than the frame, so I set the frame to hug its height (it was a fixed 2700, which clipped the bottom of the card and the source line); it is now about 2,870 tall and nothing sits below it.
- `Cover` (`11:5`): looked at, no problem found.
- **Left, not fixed** (seen, judged not a real overflow, or fixture data): `04` a text line touches its table or card edge; `03b` the active nav label wraps onto two lines (as in the HTML); `01b` protein names are truncated (fixture data); `10` owner card, the words "whole screen" touch the box edge; `05` owner card, the Stream B row is tight. Components `Status pill` and `Evidence badge` dashed outlines sit over the specimen label text; that is the variant-set outline, left as is.
- Layout changes in this session are in Figma only. The HTML (`vaxforge_product_design.html`) was not changed to match.

**Variables**

- The 15 colour variables in `VaxForge tokens` are now bound to the 15 matching swatch fills in `Design system` > Colour: surface/canvas, bg, card; text/ink, mute; accent/accent, tint; state/ok, ok-bg, warn, warn-bg, bad, bad-bg; fixture/bg, ink. Done through the fill's variable picker; each was checked in the Fill row (for example it reads `surface/canvas`). The swatches look the same as before.
- Not bound: the other 12 colour swatches (`side`, `faint`, `line`, `line-2`, `not-run`, `not-run-bg`, `info`, `info-bg`, `blocked`, `blocked-bg`, `ceiling-bg`, `ceiling-line`; the collection has no variable for them), all 6 spacing and 4 radius variables, and everything in the 16 screens. The `Still open` box in `How Figma was used` says so.

**Frame order**

- `How to read this file` (`11:153`) now ends with a line listing the frame order (Cover, How to read this file, 00 to 10 with their names, then Design system and How Figma was used). It is a copy of the footer text layer, set to wrap at 1,370 px. I did not add Sections: the two-column grid interleaves the logical groups, as noted above.

**Not checked**

- I did not re-open every screen after the last edit to `Design system`; the other frames were not touched by it. I did not compare Figma with the HTML by diff.

## Log: fifth session (independent re-check)

2026-11-15. A separate agent looked at every top-level frame in the Figma web editor (Cover, How to read this file, 00 to 10 with the b and c variants, Design system, How Figma was used, and the owner cards), using screenshots at 25 to 60% zoom, plus the layer Find box and the Variables and Assets panels. WebGL was on and the editor rendered; no Missing font dialog. Only the two fixes below were made. Nothing was committed.

**Fixes made (both layout, same cause)**

- Owner card `10 Limits and absent` (`11:9822`): in the DATA OWNER box the text "whole screen" ran flush to the box edge (no right padding). Its row (`11:9834`, a horizontal auto layout, width hug 386 inside a 372 px box) is now width Fill and Wrap. "whole screen" now sits on its own line inside the box; the card is about 26 px taller (it hugs).
- Owner card `00 Overview and key` (`11:8258`): same defect, same fix on its row (`11:8270`).

**Seen and left (not in the fix list)**

- Owner card `00` (`11:8258`) has layer opacity 12%, so it is almost unreadable. Probably left over from the third-session accident. Set it to 100%.
- Owner card `04b Epitopes and coverage` (`11:8962`) is hidden (eye closed in the layers panel), so nothing sits to the right of frame `04b`. Show it.
- `03b` nav: the active label "Safety and conservancy" wraps to two lines and touches the top and bottom of its pill.
- `04`: in "What to trust" the last column's text ends about 5 px from the table edge; in its owner card "S3 to S7" is tight against the box.
- `01b`: some protein names are cut ("...methyltransfe", "...DNA-binding prot"); fixture data.
- `Design system` > Components: the dashed outline of the Status pill and Evidence badge variant sets crosses the specimen labels. The Status pill set has `info` but no `unverified`, which `00` and `07` use.
- `02` has no "40 of 2003" wording; `01` reads "40 (fixture subset of 2003 declared)", `01b` reads "40 of 2,003 proteins in the fixture". `00` says "15 screens", `Cover` says "16 screens".
- Variables: `VaxForge tokens`, 25 variables (15 colour, 6 spacing, 4 radius), as logged. Sampled swatch fills: `fixture-bg` and `warn-bg` are bound; `info` and `ceiling-bg` are plain hex. Assets: 12 components.
- Layer names: all 36 top-level layers are named. Find showed no `Frame N`, `Rectangle`, `Vector` or `Image` layers (for `Group` I read 14 of 22 results: all named layers or text); 1,365 layers are still named `Container` and 19 `Heading 1`.

**Slip, undone**

While using the Find box I triple-clicked a spot just below the search field. It selected a text layer (`11:1237`, "(fixture subset of 2003 declared)" on `01`) and the next keystrokes went into that layer. One Cmd+Z restored it. Checked afterwards: the layer reads "(fixture subset of 2003 declared)", width 188, and Find shows no stray text.

## Log: sixth session, part A (defects from the fifth-session re-check)

2026-11-15. Probed WebGL first (`{"webgl":true}`); editor rendered. Chrome only, no Figma MCP. Each text edit was made after the layer showed an edit box with its text selected. Before the edits I did not find stray text on screen 01 (looked at the whole frame at 50%; the checker's slip layer `11:1237` was still there reading "(fixture subset of 2003 declared)", width 188, which I then replaced, see 3).

1. Owner card `00` (`11:8258`): opacity was 12% (seen in the Appearance panel); set to 100. The card now reads normally.
2. Owner card `04b` (`11:8962`): eye was closed in the layers panel; opened. It sits at X 3592, Y 7940 beside frame 04b (same offset as the other second-column cards: frame X 2120 + 1440 + 32).
3. Proteome count, one wording "40 of 2003 (fixture subset)":
   - `01` Run launcher, table row "Proteins in proteome file": text `11:1235` now "40 of 2003 (fixture subset)"; the old sub-line `11:1237` deleted.
   - `01b` Data and annotation, Resources table, Reference proteome detail `11:1471`: "N. meningitidis serogroup B MC58 (UP000000425), 40 of 2003 (fixture subset)". The stat tile "40 / fixture subset" was left (it is the annotated count, not the proteome count).
   - `02` Pipeline overview, S1 card text `11:2068`: "Reference proteome, 40 of 2003 (fixture subset). VFDB, Protegen and human proteome fetched." (02 had no count before; I added it to the S1 card because that is where the proteome is fetched.) No stand-in values were needed.
   - The same strings were changed in `vaxforge_product_design.html`.
4. Screen count: the 00 table lists 15 rows and 00 is the 16th, matching the Cover's "16 screens" (top-level screens counted: 00, 01, 01b, 02, 03, 03b, 04, 04b, 04c, 05, 06, 07, 07b, 08, 09, 10). The 00 subtitle (`11:521`) now says "One canvas, 16 screens (this one and the 15 in the table)". HTML changed to match.
5. Layout leftovers:
   - `03b` active nav label `11:2811`: set to auto width; now one line inside its pill (seen at 50%).
   - `01b`: the two cut names are cut in the **text itself** (the source text ends "DNA-binding prot" and "methyltransfe"), not by the layout. I did not complete the words; I appended "…" to both (`11:1763`, `11:1801`) so they read as deliberately truncated.
   - `04` "What to trust" last column (`11:3419` and two siblings): set to auto height and width 232 so the text no longer touches the table edge; this made rows wrap and the table (fixed height 322) clipped its last row, so I set the table (`11:3394`) to hug contents; all six rows now show.
   - **Not done:** the `unverified` variant of the Status pill set (screens `00` and `07` use that pill, the set has only `info`), and the owner-card tightness on `04`.

## Log: sixth session, part B (04c Structure rebuilt as a confidence-first screen)

2026-11-15. Chrome and the Figma web editor only (the MCP cap was spent). Edited the existing 04c frame (`11:3882`) in place; no HTML re-import. Source of every number: the real S7 sample `docs/examples/s7_structure.real_mc58_boltz_cpu.json` (stream B, after the PR #46 scaling fix), read from `origin/main`. It is 3 proteins of the N. meningitidis MC58 proteome, Boltz-2 2.2.1, CPU, no MSA, fast smoke settings; not the 40-protein fixture; none is a Bexsero antigen. The screen says so in a callout at the top.

**What 04c now holds, top to bottom (all inside `11:3882`)**

1. Callout "Real S7 sample, not the fixture run".
2. Run mode card (`30:306`, a copy of the Confidence card with its graphic deleted): folding fields from `analysis.folding`, badge "Offline, single-sequence: low-confidence mode", `seed_controlled false` with the file's seed note.
3. Candidates table (`11:4020`): rank, protein, final score, structure status, mean pLDDT (58.2, 67.4, 36.3), "no validator ran", rank basis; two `not_selected` rows kept.
4. Confidence card (`30:103`) with the graphic `30:177`: per candidate a stacked bar of the four band fractions and the band numbers, an epitope-segment track for Q7DDG6 only (10 segments, 22 of 65 residues, drawn from `epitope_segments`, not coloured by confidence, no per-residue strip), and "Epitope residues below the pLDDT floor of 70: Q7DDG6 22 of 22, Q9JXZ9 10 of 21, Q9K0Q8 31 of 31" with the plain reading (most predicted epitope residues sit in low-confidence regions in this run) and "Structure confidence measures the model, not immunogenicity". The "under review" wording from the earlier brief is not on the screen.
5. Agreement card (`30:179`, a copy of the Candidates card cut to 3 rows): ESMFold, AF2-no-MSA, Metrics and Validator agreement all read "not run" / "no validator ran"; reason "unavailable"; the file's two reason strings are under the table.
6. Row: Structure status per candidate (status vocabulary, kept) beside "Absent by design", whose two texts are now the file's `affinity.reason` and `docking.reason`.
7. How the final score is built (kept), then "3D view: a labelled slot, no model drawn" (`30:371`: model_file root `run`, path `data/s7/Q7DDG6/boltz2.pdb`, sha256 shown as its first 16 characters, 43,658 bytes; the other two sizes), then "What would refute it" (`30:381`: folded vs denatured binding, from `CONTRACT_S3_S7.md` section 8; marked as a design, not a result), then the Honest ceiling callout.
8. Owner card `11:9060` rewritten: reads `candidates[]` and `analysis.*`, four "pay attention" points (run mode and `seed_controlled`, affinity and docking not implemented, epitope residues below the floor and "not run", top-k only), and a pending block "A real S7 sample is committed ... the 40-protein fixture run has no S7 artifact".

**Colour variables.** Four colours in group `plddt` of `VaxForge tokens`: very-high `0053D6`, high `65CBF3`, low `FFDB13`, very-low `FF7D45`. The Fill panel showed them bound on the bar segments I checked. I did not re-check the legend swatches' binding.

**Layout.** 04c grew from about 1,460 to 2,723 px tall (hug). To keep it from running into frame 06, I moved the left-column frames below it (06, 07b, 09, Design system) and their owner cards down by 800 px with Shift+Down; the right column was not moved, so the two columns no longer share row heights from 06 down. I did not renumber any positions in this log or in the HTML.

**Slips, all undone or checked**
- A paste landed a 1,872 px wide stray text layer at page level (the copy had not taken); deleted at once, nothing else touched.
- Typing "+800" into the Y field with several layers selected set every layer's Y to 800 (it is absolute, not relative). Undone from the Edit menu; the layers returned to their old positions (checked in the overview), then moved with Shift+Down instead.
- Shift+Down was sent as 80 repeats in one call; the key action accepted a repeat count.

**Not done / not checked**
- No run comparison column (no second run exists). Per-residue pLDDT is not drawn (not in the artifact).
- The HTML source `vaxforge_product_design.html` was not changed for 04c, so it now differs from the Figma frame; the Figma frame is the newer one. `screen_fields.md` section 04c was rewritten to match the frame.
- Only one epitope track (Q7DDG6) is drawn; the other two proteins have counts but no track.
- Not observed: how the frame looks in the editor at 100% zoom beyond screenshots at 26% to 69%.

## Log: sixth session, part C (storyboard page "Demo video (2:00)")

2026-11-15. Chrome and the Figma web editor only. Built from the version of `DEMO_VIDEO_PLAN.md` rewritten around the project lead's narrative (8 shots, 260 words, new narration and evidence columns, shot 2 with screen 02 and a measured elapsed time). I first filled five frames from the older table and then re-typed those from the new one; the page now carries the new text only.

**Page.** A second page, `Demo video (2:00)`, with nine top-level frames, 1920 x 1080, vertical auto layout, padding 80, gap 28, fill bound to `surface/canvas`, left to right:

| Frame | Node | Time | Tags |
|---|---|---|---|
| Run of show · 0:00 to 2:00 · 8 shots | `30:540` | | none |
| Shot 1 · 0:00 to 0:12 · Hook | `30:392` | 0:00 to 0:12 | Sci, Figma |
| Shot 2 · 0:12 to 0:30 · Species to ranked list | `30:410` | 0:12 to 0:30 | Sci |
| Shot 3 · 0:30 to 0:50 · Evidence chain | `30:427` | 0:30 to 0:50 | Sci, Figma, Boltz |
| Shot 4 · 0:50 to 1:04 · Validation | `30:444` | 0:50 to 1:04 | Sci |
| Shot 5 · 1:04 to 1:20 · Protocol and robot code | `30:461` | 1:04 to 1:20 | Sci |
| Shot 6 · 1:20 to 1:40 · Adaption fine-tune | `30:478` | 1:20 to 1:40 | Adaption |
| Shot 7 · 1:40 to 1:52 · Built in Figma | `30:495` | 1:40 to 1:52 | Figma |
| Shot 8 · 1:52 to 2:00 · Limits | `30:512` | 1:52 to 2:00 | Sci |

Each shot frame holds the title (shot number, time range, name), ON SCREEN (the Figma frame or terminal view, named, no screenshot copied), NARRATION (the plan's text, word for word), WHAT THE VIEWER SEES (the plan's evidence column) and the tag chips. The run-of-show frame holds the story and pace (2:00, 8 shots, 260 words at about 2.3 words a second, time saved shown only as measured numbers), the Do-not-claim list (four items) and the five prerequisites (shots 2, 4, 5, 6, 7). Fonts and sizes are the file's text styles; colours are variables.

**`[FILL]` markers.** The text keeps the plan's literal `[FILL]` (shot 2: elapsed time, funnel counts, seconds; shot 3: the MSA re-run sentence; shot 4: Bexsero recall@3; shot 6: counts in the narration, job id, win rate, leaderboard row). Frames 2, 3, 4 and 6 also carry an amber chip "[FILL] = placeholder, not measured yet" (text bound to `state/warn`, fill to `state/warn-bg`). Shot 6's evidence line keeps the manifest counts 429 real, 571 synthetic and 42.9 % real as the plan states them; I did not check them against the manifest.

**One change from the plan's text.** Shot 7's evidence reads "19 colour variables in the VaxForge tokens collection (15 first logged, plus 4 pLDDT colours added for 04c; counted in the Variables panel)" instead of "15 colour variables bound". Observed: the collection has 29 variables, 19 of them colours (surface 3, text 2, accent 2, state 6, fixture 2, plddt 4). Not checked: that every one of the 19 is bound on a layer.

**Slips.**
- After "Collapse all layers" the first click in the layers panel did not change the selection once, so the Shot 2 text went onto the frame at X 0 (frame 1). Caught on the Design panel (X 0), re-typed frame 1 as Shot 1 and then did frame 2 on the frame at X 1980. Rule now: click the row, wait, press Shift+2 and read the X in the Design panel before typing.
- The first run-of-show text overflowed the 1080 px frame (clipped at the bottom); shortened it and removed the empty chip row.

**Not done.** No thumbnails (by the brief). The lower half of each shot frame is empty. The three open questions in the plan (track criteria, hard 2:00 limit, record before or after the Adaption run) are unanswered. Frame names carry the time ranges of the plan as of today; if the shot table changes, the names and the title texts both need editing. Not observed: the page at 100% zoom beyond screenshots at 34%.

## Log: sixth session, part D (pass 6: storyboard re-sync, scene sketches, timeline, prize map)

2026-11-15. Chrome and the Figma web editor only, file `pzytlXMGdl8JuSNivTeOOq`, page `Demo video (2:00)`. No Figma MCP tool was called. Nothing committed, pushed or posted. The VaxForge page and the old team file `Vvjq4muQIEJeX5IK4JKGcf` were not touched. Ran over the 45 minute budget.

**Frames changed (all nine on the page).** Run of show `30:540`; Shot 1 `30:392`; Shot 2 `30:410`; Shot 3 `30:427`; Shot 4 `30:444`; Shot 5 `30:461`; Shot 6 `30:478`; Shot 7 `30:495`; Shot 8 `30:512`.

**1. Text re-synced to the current `DEMO_VIDEO_PLAN.md`** (file modified 2026-11-15 02:58). Substantive changes on shots 4 to 6 (shot 4 now carries the real Bexsero result, recall@3 = 0 of 3, NadA 7th, NHBA 9th, fHbp 14th, `leakage assertion: not checked`, `holdout_enforced: false`; shot 5 and 6 evidence lines), minor on shots 7 and 8. Run of show: story, do-not-claim (four items) and prerequisites re-typed from the plan. The Run of show frame is now Hug height (it grew with the new content).

**2. Wireframe scene sketch in the lower half of every shot frame.** Structure: left column (880 wide: NARRATION, narration, WHAT THE VIEWER SEES, evidence, chips) and a scene frame (800 x 450, vertical, padding 24, fill `surface/card`, 2 px stroke `text/mute`) holding ON SCREEN label, the exact Figma frame or terminal view, one real-data element, and a caption bar at the bottom. Caption bars use the CAPTIONS dict from `~/vaxforge-work/demo/make_storyboard.py` (all eight). Real-data elements, only numbers present in the plan or the 04c sample:
- Shot 3: three confidence bars from `docs/examples/s7_structure.real_mc58_boltz_cpu.json` (mean pLDDT 58.2, 67.4, 36.3; bars bound to `plddt/low` and `plddt/very-low`) and the line "Epitope residues below the floor of 70: 22 of 22, 10 of 21, 31 of 31".
- Shot 5: "opentrons_simulate: 36 of 36 files pass".
- Shot 6: two bars, base 22 and adapted 78, labelled "Adaption's own evaluation".
- Shot 8: the limits list (names from `src/vaxforge/report.py` CEILINGS and ABSENT).
- Shots 1, 2, 4, 7: the named view plus caption bar; no data element invented.

Variables bound in the new content: `plddt/low`, `plddt/very-low`, `text/mute`, `text/ink`, `accent/accent`, `accent/tint`, `surface/card`.

**`[FILL]` handling.** Kept only where the plan still has one: shots 3 (MSA re-run sentence) and 6 (counts, job id, win rate, leaderboard row), each with the amber "[FILL] = placeholder, not measured yet" chip. The chip was removed from shot 4 because the plan no longer has a `[FILL]` there; shots 1, 2, 5, 7, 8 carry none. Not checked: that the plan has no `[FILL]` I missed on shot 2 (I read none).

**3. Run of show: timeline, legend, prize map** (layer names "Timeline strip (8 segments)", "Legend (prize tags)", "Prize map table"). Strip: eight segments in a 1760 px row with 4 px gaps; widths measured on screen at 34% match the seconds (about 58, 86, 96, 67, 76, 96, 56, 40 px for 12, 18, 20, 14, 16, 20, 12, 8 s). Tag chips inside each segment: shot 1 Sci and Figma; 2 Sci; 3 Sci, Figma, Boltz; 4 Sci; 5 Sci; 6 Adaption; 7 Figma; 8 Sci. Chips are bound to existing variables (`accent/accent`, `text/ink`, `text/mute`, `accent/tint`). Legend: Sci, Figma, Adaption, Boltz. Prize map, four rows from the lead's order: Best use of Boltz, shot 3, "Structure only (S7); shot 4's circularity warning is context, not evidence"; Best-Designed AI Product (Figma), shots 1, 3, 7; Best use of Adaption, shot 6, "Adaption's own evaluation, not a held-out result"; AI for Science (main track), shots 1 to 5, 8. The note cells are my wording, taken from the plan's do-not-claim list; I first wrote "Held-out validation" in the AI for Science row, caught that it contradicts the plan (`holdout_enforced: false`) and replaced it.

**Word count.** The Run of show story says "278 words counted from the plan's table (keep at or under 275)", which is what the plan's narration counts to (per shot 29, 39, 55, 43, 41, 37, 19, 15). The lead's Pass 6 message said 272. I used the plan's count; the two differ by 6 and the plan is over its own 275 target by 3.

**Slips.**
- Shot 3 had five duplicated chips from an earlier pass; three were reused as bars, one as note, one as caption.
- Fill-container clicked without a wait failed on shot 5 (text overflowed the scene); reselected and waited 1 s between chevron and menu click.
- Shift+Return after Cmd+click on a chip's padding selected the scene, so Cmd+D duplicated the scene twice on shot 6; deleted both copies and redid it.
- Shot 4's evidence overflowed the frame; shortened.
- A text fill rebind to `text/ink` did not apply the first time and left white text on white; fixed with a screenshot after the click.
- Legend chips copied by Cmd+click on the chip's text pasted bare text nodes into segments 1, 3, 6, 7 (white text on white); deleted them. Segment 3's stripe order was wrong (Figma above Sci); fixed by cut and re-paste so the order is Sci, Figma, Boltz.
- Typing with no focused field triggers Figma tool shortcuts; the search field was checked first each time afterwards.

**Not done.**
- The unverified-pill status variant and the owner-card-04 tightness on the VaxForge page: out of time.
- Layer names inside the strip, legend and label rows are Figma defaults ("Frame 17" and so on); only the three container rows were renamed.
- The lower half of frames with short text still has empty space beside the 800 x 450 scene; accepted.
- Scene sketches are wireframes: no screenshot of the named Figma frame or terminal output is embedded.
- Not observed: any frame at 100% zoom; checked at 34% and 81% only. Not checked: that every number in the new content matches the artifact it cites beyond reading it against the plan and the 04c sample JSON as I typed it.

## Log: seventh session (pass 7: storyboard re-sync to the 257-word plan)

2026-11-15. Chrome and the Figma web editor only, file `pzytlXMGdl8JuSNivTeOOq`, page `Demo video (2:00)`. No Figma MCP tool called, nothing committed, pushed or posted, VaxForge page and old team file untouched. Source: `DEMO_VIDEO_PLAN.md` as modified 03:45 (257 narration words, per shot 29, 39, 41, 38, 31, 45, 19, 15) and the CAPTIONS dict in `/Users/abrahamlin/vaxforge-work/demo/make_storyboard.py`. Method: select the text layer in the Layers panel, Return, select all, type (the row positions stay put while the text grows).

**Frames changed.** Shot 2 `30:410`, Shot 3 `30:427`, Shot 4 `30:444`, Shot 5 `30:461`, Shot 6 `30:478`, Run of show `30:540`. Shots 1 `30:392`, 7 `30:495` and 8 `30:512` were read against the plan and already match; not edited.

**Text re-synced.**
- Shot 2: narration, on-screen, evidence, caption (new CAPTIONS wording) and the data line ("make demo-offline: s3 to s12 ok, under a minute; S0 to S2 are the stored fixture").
- Shot 3: narration (the MSA `[FILL]` sentence is gone, plan text restored), on-screen text. Amber `[FILL]` chip deleted. Caption unchanged (matches the dict).
- Shot 4: narration, on-screen, evidence; data line "recall@3 0 of 3; fHbp 14, NadA 7, NHBA 9; leakage assertion not checked". Caption already matched.
- Shot 5: narration, on-screen; data line "opentrons_simulate 7.5.0: 36 of 36 programs pass; placeholder parameters". Evidence and caption already matched.
- Shot 6: narration, evidence, on-screen, caption (new wording). Scene now shows two things: the heading "Adaption's own evaluation (9B): base 6 / adapted 94" over two bars (base 6 wide 45, adapted 94 wide 709, same scale of about 7.5 px per point; the bars carry only "6" and "94" because a "base 6" label does not fit a 45 px bar) and a grey full-width line "Our own held-out test (4B adapter): 0 wins, 0 losses, 10 ties, no measurable difference". The held-out line was made by copying the base bar frame (so it reuses `text/mute` fill); heading and held-out text set to 24 and 16 px to fit the 800 x 450 scene. Caption stays last (cut and pasted back after the new line).
- Run of show: story (257 words with the per-shot counts; "the built cut is 115.8 s", a number from the lead's message, not from the plan file), the seven do-not-claim items, the five prerequisites; prize map "Best use of Adaption" note now "Adaption's own evaluation, not a held-out result; our own held-out test: no measurable difference". Frame height is now 2754 (Hug).

**`[FILL]`.** Only shot 6 keeps the amber chip and the `[FILL]` for the leaderboard row. Removed from shot 3. Shots 1, 2, 4, 5, 7, 8 have none.

**Abridged on the frames (the plan cell is longer).** Shot 2 evidence (dropped "stream B's", the 35 s measurement detail and "image and date" tail); shot 2 on-screen (dropped "because it shows the pre-run fixture state"); shot 3 on-screen (dropped the "(captured: ...)" clause); shot 4 evidence (dropped the file path, the exact `vaxforge validate` command line and the ties figures); shot 6 evidence (dropped the 4B/2B run ids, the `~/vaxforge-work` capture path, the S9.5 report mention and the report file path); shot 6 on-screen (dropped "generated from the attached evidence"); do-not-claim 6 (dropped the PR and bypass details); prerequisite 6 reworded in the same words but shorter. The cause is the 1080 px frame height: the full evidence cells do not fit under the narration. The plan file remains the full text.

**Slips.** The first shot 2 evidence run (full length) overflowed the frame and was retyped shorter. Shot 3's first on-screen text was too long (caption clipped); shortened. A mis-click while expanding layers zoomed the canvas to 1176% on a chip label in shot 3; nothing was edited, I pressed Escape and refit. Typing "\n" in a text edit makes a paragraph break, as wanted.

**Not done.** The unverified-pill status variant and owner-card-04 tightness on the VaxForge page: not started (budget; the details were not in this session's brief, only in the lead's one-line mention). Scenes remain wireframes with no screenshots. Layer names inside bars and chips are Figma defaults.

**Not checked.** Frames only viewed at 34% and 25% zoom, not 100%. Shots 7 and 8 data lines ("Variables: 29, of which 19 are colour"; the limits list) were left as they were. The 115.8 s figure is as given by the lead, not re-measured here. I did not re-count the 257 words.

**Addendum to the seventh session (plan as modified 03:50).** Shot 6 `30:478` ON SCREEN now reads "Adaption run page (captured), then our held-out win-rate report (terminal)", the short form of the plan's new cell (the real `vaxforge finetune winrate` report, terminal). The sentence about the report's S9.5 section and Figma `07b` is removed, as the plan says it is out of the cut. Nothing else changed. The earlier line above that lists the S9.5 section in shot 6's On screen text is superseded by this one. Viewed at 34% only.
