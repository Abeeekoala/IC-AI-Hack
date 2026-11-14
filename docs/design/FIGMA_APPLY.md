# Putting the product design into Figma

Status: **not imported yet.** Nothing here was run against Figma. I read the tool schemas (`ToolSearch` only) and the Figma plugin's skill files. Everything marked "unverified" is an inference from those, not an observation.

- File: <https://www.figma.com/design/Vvjq4muQIEJeX5IK4JKGcf>, file key `Vvjq4muQIEJeX5IK4JKGcf`.
- Source: `docs/design/vaxforge_product_design.html`, one self-contained page: 17 fixed-size frames (1440 wide) on one 3120 px wide canvas, in two columns, left to right then down. No scripts, no external fonts or images. 4,853 elements, 215 KB.
- Goal: one page in the Figma file, one overall product design, the frames named `00 Overview and key`, `01 Run launcher`, and so on.

## Recommended path: `generate_figma_design` (3 to 14 calls, no `use_figma` needed)

The main session found that Figma's rate-limit page lists `generate_figma_design`, `create_new_file`, `whoami` and `add_code_connect_map` as exempt from the monthly 20-call limit on our Starter plan. That page does not mention `use_figma`, so whether it counts is unknown. This path avoids `use_figma` for the import itself.

**What the tool does (from its schema, no call made).** It captures a *live web page by URL* (localhost is allowed) into an existing design file and turns the DOM into editable layers. Called with only `fileKey`, it returns a capture script and a `captureId`. A browser must then load the page with that capture script; you poll with the `captureId` every 5 seconds, up to 10 times, until the status is `completed`. A capture id is single use and captures exactly one page. Maximum capture size 50 MB. Without `nodeId` it creates a **new page** in the file; with `nodeId` it appends under that node.

### Before the first call

1. **Free a page.** The file is on a Starter plan, which caps a file at 3 pages, and the file may already be at that cap (I could not read the file; the repo only names a `S9.5 · Evidence panel` frame and a `Components (local)` section, so the page count is unknown). A capture without `nodeId` needs a free slot. Open the file in the Figma UI (no quota cost), count the pages, compare the old S9.5 frame with screen `07b`, and delete a page only if it is fully superseded. Or skip this and use `nodeId` (see the call below). Do not delete anything until a person has looked.
2. **Install the fonts, then recheck.** The design asks for Inter and JetBrains Mono. Neither is installed on this Mac, so I verified the layout with the browser's fallback fonts (Helvetica Neue and Menlo), and again with +0.4 px letter-spacing to stand in for Inter's wider glyphs: no text overflows and no frame clips in either run. The capture records the browser's measured positions, so install Inter and JetBrains Mono locally before capturing (`brew install --cask font-inter font-jetbrains-mono`), or Figma will draw Inter into boxes measured for another font.
3. **Serve the file over http.** The capture script needs a real page, not `file://` (unverified for `file://`, so do not rely on it):
   ```bash
   cd docs/design && python3 -m http.server 8765
   # page: http://localhost:8765/vaxforge_product_design.html
   ```
4. **Keep the committed HTML script-free.** The tool returns a capture script. Put it into a *copy* of the page (for example `cp vaxforge_product_design.html /tmp/vf_capture.html`, add the returned `<script>` tag to the copy, serve the copy). Do not edit the file in `docs/design`.

### The calls

Generate one UUID for the whole job (`uuidgen`) and pass it as `promptId` on every call below.

| # | Tool | Parameters | Notes |
|---|---|---|---|
| 1 | `create_session` | `fileKey`, `description` ("Import VaxForge product design canvas") | The server instructions ask for it before any file tool. Not on the exempt list. If it is refused, continue without `sessionId` (optional in the schema). |
| 2 | `generate_figma_design` | `fileKey` `Vvjq4muQIEJeX5IK4JKGcf`, `promptId`, `sessionId`; **omit** `captureId`; `nodeId` optional | Returns the capture script and a `captureId`. To keep a single page, either leave `nodeId` out (a new page appears, then step "Keep one page" below) or pass the id of the page you want to keep. The schema's pattern for `nodeId` is `^\d+[:-]\d+$`, which fits a page id such as `0:1`, but whether a page is accepted as a parent is **unverified**. Copy the page's link in the Figma UI to get its `node-id`. If the call errors, repeat without `nodeId`. |
| 3 | browser | open the served copy with the hash and parameters that call 2 returned | Use the Claude in Chrome `navigate` tool. The exact URL form comes from the tool result. Wait for the page to finish (the capture of a 3120 x 18620 px page can take a while). |
| 4 to 13 | `generate_figma_design` | same `fileKey`, `promptId`, `sessionId`, plus `captureId` | Poll every 5 seconds, at most 10 times, until `completed`. |

One capture is one page, so the whole canvas is one capture. That is why the design is a single HTML page and not 17.

### What you get (unverified: depends on how the capture names things)

A new page with one top-level frame for the page body (`#canvas`), containing 17 child frames at the HTML's coordinates, built from text, rectangles and frames. No raster images are in the source, so nothing can be flattened to a picture. The capture's layer names are unknown; assume generic names and rename (next section).

### Keep one page, name the frames, rename the page

Do this by hand in the Figma UI (zero calls), or with one `use_figma` call if it works:

1. Look at the new page. There should be 17 frames, the first `00` at the top left and `C` at the bottom left.
2. Delete the other pages (right-click the page, Delete). Rename the remaining page `VaxForge`.
3. Rename each 1440-wide frame to `<code> <name>` from the table below.
4. **Rename the file itself in the Figma UI** (click the file name at the top left). The API refuses: "Setting the document name is currently not supported". Suggested name: `VaxForge`.

Optional one-call cleanup with `use_figma` (`fileKey`, `description`, `code`). It is **untested**; run it once with `DRY = true`, read what it would change, then run it with `DRY = false` after a person has checked the page. It does not use `loadAllPagesAsync`, which the tool forbids.

```js
const DRY = true;                       // true: report only. Set false after a person has checked the new page.
const NEW_PAGE_ID = 'PAGE_ID_HERE';     // the page the capture created (or the page you passed as nodeId)
const NAMES = {'00':'Overview and key','01':'Run launcher','01b':'Data and annotation','02':'Pipeline overview','03':'Ranked candidates',
  '03b':'Safety and conservancy','04':'Candidate detail','04b':'Epitopes and coverage','04c':'Structure','05':'Validation','06':'Wet-lab protocol',
  '07':'Robot code','07b':'S9.5 evidence panel','08':'Animal study','09':'Pre-registration and gate','10':'Limits and absent','C':'Foundations'};
const pages = figma.root.children;
const keep = pages.find(p => p.id === NEW_PAGE_ID);
if (!keep) return { error: 'page not found', pages: pages.map(p => p.name + ' ' + p.id) };
await figma.setCurrentPageAsync(keep);
const screens = [];
for (const top of keep.children) {
  if (top.type !== 'FRAME') continue;
  for (const f of top.findAll(n => n.type === 'FRAME' && Math.abs(n.width - 1440) < 2 && n.height > 1000)) screens.push(f);
}
const plan = [];
for (const f of screens) {
  // the screen code sits in a 13 px badge, e.g. "06" or "07b" or "C"
  const badge = f.findAll(n => n.type === 'TEXT' && n.fontSize === 13 && /^(\d{2}[bc]?|C)$/.test(n.characters))[0];
  const code = badge ? badge.characters : null;
  plan.push({ id: f.id, was: f.name, now: code && NAMES[code] ? code + ' ' + NAMES[code] : null });
  if (!DRY && code && NAMES[code]) f.name = code + ' ' + NAMES[code];
}
const others = pages.filter(p => p.id !== keep.id).map(p => p.name + ' ' + p.id);
if (!DRY) { keep.name = 'VaxForge'; for (const p of pages) if (p.id !== keep.id) p.remove(); }
return { dry: DRY, found: screens.length, expected: 17, plan, wouldDelete: others };
```

### Check the result without spending a counted call

Open the file in the Figma UI. Check: 17 frames; text is live text (click into one); no frame is a picture; Inter and JetBrains Mono show in the text panel; nothing is cut off at the right edge of a frame. `get_metadata` and `get_screenshot` read from Figma and were refused at the limit, so do not plan on them.

## Alternative A: `html_to_figma` (if the tool exists in the next session)

The Figma skills (`figma-generate-design`, `figma-code-workflows`) name a tool `html_to_figma` that "converts a self-contained static HTML document" into editable Design layers, with no server and no browser. `ToolSearch` found no such tool in this session (query `html_to_figma` and `figma static html editable design layers`). I never saw its schema, so its parameters are unknown. If it appears: one call with the HTML file's content, the same `fileKey` and `promptId`, then the same "keep one page" steps. The HTML already meets its description (self-contained, static).

## Alternative B: `use_figma` only (not recommended)

`use_figma` runs Plugin API JavaScript (code up to 50,000 characters per call) and cannot import HTML. Rebuilding 17 frames that way means generating the screens as data and writing a builder, and the earlier file's script (`docs/figma_s9_5_update.js`) shows how many helper lines that takes. If the import tools turn out to be blocked, ask for that as a separate job: roughly 6 to 8 calls, with no way to confirm the count against the quota.

## What the import path imposes on the HTML

Written for `generate_figma_design`, which renders the page in a browser and converts the result. All of these hold for the current file.

| Constraint | What the file does |
|---|---|
| The page must load over http in a real browser, and the capture script must run in it | Served with `python3 -m http.server`; the script goes into a copy, not the committed file |
| One capture is one page | One HTML page with one canvas of 17 frames |
| Layers come from the rendered DOM | Frames are absolutely positioned `section.frame` boxes (`left`, `top`, `width`, `height` all fixed in px); everything inside is flex layout with explicit widths |
| No script, external font or image is needed, and none is used | 0 `<script>`, 0 `<img>`, 0 `url()`, 0 `@font-face`, 0 `http` strings |
| Avoid CSS that has no Figma equivalent | None of these appear: gradients, box-shadow, filter, backdrop-filter, `transform` (except `text-transform: uppercase`), grid, `<table>`, `::before` or `::after` content, SVG, `position: sticky` or `fixed`, background images |
| Features that may degrade (unverified) | Dashed borders (`border: 1px dashed`) on placeholders, tags and state specimens; the 4 px left border on callouts and the "Honest ceiling" box; per-side radius is not used. If dashes arrive solid, they are still distinguishable by their label; if the left border is lost, the box keeps its tint and outline |
| Fonts must exist in Figma | Inter (400, 600, 700) and JetBrains Mono (400, 600). Figma ships both. Install them locally before capturing so the browser measures the same glyphs |
| Text must not depend on wrapping the browser does differently | Verified no overflow with fallback fonts and with +0.4 px letter-spacing; frames carry 120 px of bottom slack |
| Colours | Resolved by the browser from CSS variables, so Figma gets plain fills, **not Figma variables**. Making variables is a separate optional pass (palette in frame `C`, hex values in the `:root` block of the HTML) |

## Numbering: old design and new

Recovered from `src/vaxforge/report.py` (whose sections "follow the team's Figma shell"), `docs/figma_s9_5_update.js` and the team's notes. Screens 01 to 10 keep the old numbers, so references on the board to 06, 07 and 08 stay valid. The Figma file itself could not be read, so this is a reconstruction.

| Code | Screen | Old design |
|---|---|---|
| 00 | Overview and key | the old `Overview` frame (node `14:191`), now an index and legend |
| 01 | Run launcher | same |
| 01b | Data and annotation | **new** drill-down (S1, S2) |
| 02 | Pipeline overview | same (the script's "screen 02 Lane C band" is now a lane row) |
| 03 | Ranked candidates | same |
| 03b | Safety and conservancy | **new** drill-down (S4) |
| 04 | Candidate detail | same |
| 04b | Epitopes and coverage | **new** drill-down (S5, S6) |
| 04c | Structure | **new** drill-down (S7) |
| 05 | Validation | same, now with LOPOV, Bexsero and leakage |
| 06 | Wet-lab protocol | same number, **new content** (S8, designed here) |
| 07 | Robot code | same number, **new content** (S9, S9+) |
| 07b | S9.5 evidence panel | the old standalone `S9.5 · Evidence panel` frame (node `1:28` in `docs/figma_s9_5_update.js`), now a screen, **numbers updated to README section S9.5** (98 real pairs plus 1,150 synthetic, not the stale "16 usable pairs") |
| 08 | Animal study | same number, **new content** (S10) |
| 09 | Pre-registration and gate | same (S11, S12) |
| 10 | Limits and absent | same |
| C | Foundations | the old `Foundations` and `Components` frames, folded into one side frame (the owner may delete it) |

`docs/figma_s9_5_update.js` is **obsolete** once this is imported: it patched text in the old S9.5 page, which will be deleted. Do not run it.

## What I could not verify

- Nothing was imported, so how the capture names layers, whether dashed borders and the left callout border survive, and whether a page id is accepted as `nodeId` are all unknown.
- The URL form that opens the capture (hash parameters) is returned by the tool at call time. I did not see it.
- I checked layout in headless Chrome with fallback fonts, not with Inter.
- Whether `create_session` and `use_figma` count against the 20 calls is unknown.
