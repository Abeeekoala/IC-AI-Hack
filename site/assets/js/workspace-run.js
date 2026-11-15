/* VaxForge workspace: screens 04 Generate with models, 05 Test results, 06 Handoff.
   Loaded after workspace.js. Uses only the public window.WS interface (comment block at the top of workspace.js):
   data, state, run, setState, go, registerScreen, drawer, announce, esc, chip, badge, fmtInt, short, copyBtn, plain,
   fetchText, verifyBytes, verifyRun. Replay only: nothing here calls a model, runs generated code or fetches anything
   except this site's own relative files. Every datum is escaped with esc() before it reaches innerHTML. */
(function () {
  "use strict";
  var WS = window.WS;
  if (!WS) return;

  var MAXLINES = 220;
  var MON = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var RULES = [
    ["tip_before_aspirate", "Tip attached before aspirating"],
    ["no_overdispense", "No over-dispensing"],
    ["no_aspirate_from_empty_well", "No aspirating from an empty well"],
    ["tip_dropped_at_end", "Tip dropped at the end"],
    ["no_cross_contamination", "No cross-contamination"]
  ];
  var STATES = [
    ["queued", "info", "Accepted, waiting to start."],
    ["generating", "info", "The model request is in flight."],
    ["generated", "info", "The model returned code; nothing has checked it yet."],
    ["checking", "info", "Lint, the simulator and the rule checks are running."],
    ["passed", "ok", "Lint, the simulator and all five rules passed."],
    ["failed", "failed", "Lint, the simulator or a rule check failed."],
    ["not run", "notrun", "Never started, so no result exists."],
    ["refused", "refused", "The model declined to write code. An outcome, not a crash."],
    ["truncated", "incomplete", "The output stopped at the token limit before the code was complete."],
    ["timed out", "incomplete", "The job ran past its time limit."],
    ["unavailable", "notrun", "The lane or tool is not present on this machine."],
    ["cancelled", "notrun", "Stopped by a person before it finished."]
  ];
  var OWNER = { full: ["robot", "Robot does it"], partial: ["partial", "Robot does part"], manual: ["human", "A person does it"] };
  var CEILING = "A PASS means the code ran and broke none of the checked rules; it does not mean the protocol is right for the experiment.";
  /* Interface state that lives only on this page: filters, expanded code, digest results, open animations, selected step. */
  var ui = { filter: "all", full: {}, vout: {}, sim: {}, step: null, vr: {} };
  var codeCache = {}, mounted = {}, SCREENS = {};

  /* ---------- small helpers ---------- */
  function esc(s) {
    s = s == null ? "" : String(s);
    return typeof WS.esc === "function" ? WS.esc(s) : s.replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function chip(kind, text) { return WS.chip(kind, text); }
  function pill(kind, text) { return '<span class="pill pill-' + kind + '">' + esc(text) + "</span>"; }
  function badge(text) { return WS.badge(text); }
  function fmt(n) { return n == null ? "n/a" : typeof WS.fmtInt === "function" ? WS.fmtInt(n) : String(n); }
  function hex(d) { return String(d == null ? "" : d).replace(/^sha256:/, ""); }
  function sh(d, n) {
    if (!d) return "none";
    return typeof WS.short === "function" ? WS.short(hex(d), n || 12) : hex(d).slice(0, n || 12) + "…";
  }
  function dig(d, n, label) { return d ? '<code class="wsr-dig">' + esc(sh(d, n)) + "</code> " + WS.copyBtn(hex(d), label) : "none"; }
  function uniq(a) { return a.filter(function (x, i) { return a.indexOf(x) === i; }); }
  function plural(n, w) { return n + " " + w + (n === 1 ? "" : "s"); }
  function str(x) { return typeof x === "string" ? x : JSON.stringify(x); }
  function days(d) { return !d || d.length < 2 ? "n/a" : d[0] === d[1] ? String(d[0]) : d[0] + " to " + d[1]; }
  function when(iso) {
    var m = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}:\d{2}:\d{2})(Z|[+-]00:?00)$/.exec(iso || "");
    return m ? { date: +m[3] + " " + MON[+m[2] - 1] + " " + m[1], time: m[4] } : { date: String(iso || "unknown date"), time: "" };
  }
  function modelName(id) {
    var m = /^claude-([a-z]+)-(\d+)-(\d+)$/.exec(id || "");
    return m ? m[1].charAt(0).toUpperCase() + m[1].slice(1) + " " + m[2] + "." + m[3] : String(id || "unknown model");
  }
  function runLabel(run) { return run && run.key === "real" ? "Real-tools run" : "Recorded offline run"; }
  function local(u) { return typeof u === "string" && !!u && !/^([a-z][a-z0-9+.\-]*:|\/)/i.test(u); }
  /* Template-program files are listed relative to the run folder; model-test files relative to the site root. */
  function runUrl(run, rel) {
    if (!rel) return null;
    var u = /^runs\//.test(rel) ? rel : (run.folder || "") + rel;
    return local(u) ? u : null;
  }
  function getRun(ctx) {
    var r = ctx && ctx.run;
    return (typeof r === "function" ? r() : r) || WS.run();
  }
  function wide() { return !!(window.matchMedia && window.matchMedia("(min-width: 1200px)").matches); }
  function keep(fn) {              /* the core re-renders on setState: put keyboard focus back on the same control */
    var a = document.activeElement, id = a && a.getAttribute ? a.getAttribute("data-keep") : null;
    fn();
    var n = id ? document.querySelector('[data-keep="' + id + '"]') : null;
    if (n && n !== a && n.focus) n.focus({ preventScroll: true });
  }
  function card(id, title, side, body, cls) {
    return '<section class="ws-card' + (cls ? " " + cls : "") + '" aria-labelledby="' + id + '"><div class="ws-card-h"><h3 id="' + id + '">' + esc(title) + "</h3>"
      + (side ? "<span>" + side + "</span>" : "") + "</div>" + body + "</section>";
  }
  function tabs(label, opts, cur, action) {
    return '<div class="ws-tabs" role="group" aria-label="' + esc(label) + '">' + opts.map(function (o) {
      return '<button type="button" data-wsr="' + action + '" data-m="' + esc(o[0]) + '" data-keep="' + action + "-" + esc(o[0]) + '" aria-pressed="' + (cur === o[0]) + '">' + esc(o[1]) + "</button>";
    }).join("") + "</div>";
  }
  function select(id, label, opts, val, action) {
    return '<label class="ws-label">' + esc(label) + '<select class="ws-field" id="' + id + '" data-wsr="' + action + '" data-keep="' + id + '">'
      + opts.map(function (o) { return '<option value="' + esc(o[0]) + '"' + (String(o[0]) === String(val) ? " selected" : "") + ">" + esc(o[1]) + "</option>"; }).join("")
      + "</select></label>";
  }
  function facts(rows) {           /* [label, text] or [label, html, true] */
    return '<dl class="ws-kv">' + rows.map(function (r) { return "<dt>" + esc(r[0]) + "</dt><dd>" + (r[2] ? r[1] : esc(r[1])) + "</dd>"; }).join("") + "</dl>";
  }
  function ul(items) {
    return items && items.length ? '<ul class="ws-list">' + items.map(function (i) { return "<li>" + esc(i) + "</li>"; }).join("") + "</ul>" : '<p class="ws-note">None recorded.</p>';
  }
  function outcome(o) { return chip({ PASS: "ok", FAIL: "failed", REFUSED: "refused" }[o] || "info", o || "unknown"); }

  /* ---------- grade blocks shared by 05 and 06: explanation, three checks, log, code, digest ---------- */
  function errKind(err) { return (/^([A-Za-z_][A-Za-z0-9_]*)/.exec(err || "") || [])[1] || ""; }
  function errLine(err) { return (/\[line (\d+)\]/.exec(err || "") || [])[1] || ""; }
  function simLine(err) {
    var kind = errKind(err), ln = errLine(err), why = String((kind && typeof WS.plain === "function" && WS.plain(kind)) || "").trim();
    var lead = "The simulator stopped" + (ln ? " at line " + ln : "");
    if (!why) return lead + (kind ? ": it reported " + kind + "." : ".");
    if (/^(The|A|An|It|This)\s/.test(why)) why = why.charAt(0).toLowerCase() + why.slice(1);
    return lead + ": " + why.replace(/\.+$/, "") + ".";
  }
  function explain(g) {
    if (g.outcome === "REFUSED") return "The model declined to write code. This is reported as an outcome, not a crash.";
    if (g.outcome === "PASS") return "Passed lint, ran in the simulator and broke none of the five physical rules.";
    if (g.sim_ok === false) return simLine(g.sim_error);
    if (g.lint && g.lint.length) return "Static lint reported " + plural(g.lint.length, "finding") + ". See the log.";
    return "The recorded outcome is " + (g.outcome || "unknown") + ". See the log for the raw output.";
  }
  function checks(g) {
    var lint = g.lint, rc = g.rule_counts, ran = g.sim_ok === true && !!rc, kind = errKind(g.sim_error), ln = errLine(g.sim_error);
    var total = ran ? RULES.reduce(function (a, r) { return a + (rc[r[0]] || 0); }, 0) : 0;
    var rows = [
      ["Lint", lint == null ? chip("notrun", "not run") : lint.length ? chip("failed", plural(lint.length, "finding")) : chip("ok", "no findings"),
        lint == null ? "No code, so lint did not run." : "Static lint is a filter, not a sandbox."],
      ["Simulator", g.sim_ok === true ? chip("ok", "ran") : g.sim_ok === false ? chip("failed", "stopped") : chip("notrun", "not run"),
        g.sim_ok === true ? fmt(g.n_events) + " commands recorded." : g.sim_ok === false ? (kind || "Error") + (ln ? " at line " + ln : "") + "." : "No code, so the simulator did not run."],
      ["Rule checks", !ran ? chip("notrun", "not run") : total ? chip("failed", plural(total, "violation")) : chip("ok", "0 violations"),
        ran ? "Five physical rules, counted from the recorded commands." : g.sim_ok === false ? "The simulator stopped before it recorded any command, so no rule was checked." : "No code, so no rule was checked."]
    ];
    return '<dl class="wsr-checks">' + rows.map(function (r) { return "<div><dt>" + r[0] + "</dt><dd>" + r[1] + " <span>" + esc(r[2]) + "</span></dd></div>"; }).join("") + "</dl>"
      + '<table class="wsr-rules"><caption class="ws-sr">Rule checks: violations found per rule</caption><thead><tr><th scope="col">Rule</th><th scope="col" class="num">Violations</th></tr></thead><tbody>'
      + RULES.map(function (r) { return "<tr><td>" + esc(r[1]) + '</td><td class="num">' + (ran ? esc(rc[r[0]] || 0) : "not run") + "</td></tr>"; }).join("") + "</tbody></table>";
  }
  function gradeLog(g, extra) {
    var lines = ["outcome: " + g.outcome, "lint: " + (g.lint == null ? "not run" : g.lint.length ? g.lint.map(str).join("\n  ") : "no findings"),
      "sim_ok: " + String(g.sim_ok), "sim_error: " + (g.sim_error || "none"), "n_events: " + String(g.n_events)].concat(extra || []);
    return '<details class="wsr-log"><summary>Log: raw lint and simulator output</summary><pre class="ws-code" tabindex="0">' + esc(lines.join("\n")) + "</pre></details>";
  }
  function codeFig(url, sha, label, none) {
    if (!url) return '<p class="ws-note">' + esc(none) + "</p>";
    return '<figure class="wsr-codefig"><figcaption><strong>Generated code</strong> <a href="' + esc(url) + '" target="_blank" rel="noopener">Open the file<span class="ws-sr"> (new tab)</span></a></figcaption>'
      + '<pre class="ws-code wsr-code" tabindex="0" data-wsr-code="' + esc(url) + '" aria-label="' + esc(label) + '">Loading code ...</pre>'
      + '<button type="button" class="ws-btn ws-btn-sm" data-wsr="code-more" data-url="' + esc(url) + '" hidden></button></figure>'
      + (sha ? '<div class="wsr-verify"><div class="wsr-verify-row"><span>Recorded digest (code_sha256) ' + dig(sha, 12, "Copy code digest") + "</span>"
        + '<button type="button" class="ws-btn" data-wsr="verify" data-url="' + esc(url) + '" data-sha="' + esc(sha) + '">Verify digest</button></div>'
        + '<div class="wsr-vout" role="status" aria-live="polite">' + (ui.vout[url] || "") + "</div>"
        + '<p class="ws-note">Verify digest fetches the file above and hashes it in your browser. A match shows it is the file that was recorded, not that the code is right.</p></div>'
        : '<p class="ws-note">No recorded digest for this file, so it cannot be verified.</p>');
  }
  function getCode(url) {
    if (!codeCache[url]) codeCache[url] = Promise.resolve().then(function () { return WS.fetchText(url); }).then(String, function (e) { delete codeCache[url]; throw e; });
    return codeCache[url];
  }
  function paintCode(pre) {
    var url = pre.getAttribute("data-wsr-code"), btn = pre.parentNode.querySelector("[data-wsr=code-more]");
    getCode(url).then(function (text) {
      if (!document.body.contains(pre)) return;
      var lines = text.split(/\r?\n/);
      if (lines.length > 1 && lines[lines.length - 1] === "") lines.pop();
      var all = !!ui.full[url], cut = !all && lines.length > MAXLINES;
      pre.textContent = (cut ? lines.slice(0, MAXLINES) : lines).join("\n");
      pre.classList.toggle("is-full", all);
      if (btn) {
        btn.hidden = lines.length <= MAXLINES;
        btn.textContent = all ? "Show the first " + MAXLINES + " lines" : "Show all " + fmt(lines.length) + " lines";
      }
    }, function (err) {
      if (document.body.contains(pre)) pre.textContent = "Could not load this file (" + ((err && err.message) || "network error") + "). The recorded digest is unaffected.";
    });
  }

  /* ---------- evidence drawer content (plain text only: the drawer escapes it) ---------- */
  function jobSpec(r) {
    var w = when(r.started), kind = errKind(r.sim_error), ln = errLine(r.sim_error);
    var state = r.outcome === "PASS" ? "passed: lint clean, simulator ran, no rule broken"
      : r.outcome === "REFUSED" ? "refused: no code returned (an outcome, not a crash)"
      : "failed: " + (kind || "lint or simulator error") + (ln ? " at line " + ln : "");
    return {
      title: "Job record: " + modelName(r.model) + ", " + r.session,
      badges: ["Recorded model test"],
      facts: [["Lane", "Anthropic API (recorded)"], ["Task id (session)", r.session], ["Model id", r.model], ["Response model", r.resp_model], ["Response id", r.resp_id],
        ["Started", w.date + (w.time ? ", " + w.time + " UTC" : "")], ["Latency", r.latency_s + " s"],
        ["Tokens", fmt(r.input_tokens) + " in, " + fmt(r.output_tokens) + " out"], ["Stop reason", r.stop_reason],
        ["Decoding parameters", "none set; max_tokens 12000 (recorded in the test report)"], ["Test tool", "Opentrons 7.5.0 via vaxforge.s9_validate"],
        ["Error or refusal state", state]],
      missing: ["The request text is not stored on this site, only its digest."].concat(r.outcome === "REFUSED"
        ? ["No code was returned, so lint, the simulator and the rule checks did not run."]
        : r.sim_ok === false ? ["Rule checks did not run: the simulator stopped before it recorded any command."] : []),
      digests: [{ label: "Request digest (prompt_sha256)", value: r.prompt_sha256 }].concat(r.code_sha256 ? [{ label: "Output digest (code_sha256)", value: r.code_sha256 }] : []),
      links: r.code ? [{ label: "Open generated code", href: r.code, external: true }] : [],
      notes: ["Recorded on " + w.date + ". This is not a request made now.", "One attempt per model and session. A retry would be a separate attempt."]
    };
  }
  function progSpec(run, s) {
    var links = [["Open program code", s.code], ["Open setup sheet", s.setup], ["Open plan (JSON)", s.plan], ["Open recorded grade (JSON)", s.grade_file], ["Open animation page", s.animation]]
      .filter(function (l) { return runUrl(run, l[1]); }).map(function (l) { return { label: l[0], href: runUrl(run, l[1]), external: true }; });
    return {
      title: "Program: " + s.title,
      badges: [runLabel(run)],
      facts: [["Candidate", "rank " + s.candidate_rank + ", " + s.candidate], ["Session", s.session_id], ["Covers S8 step", (s.covers || []).join(", ") || "n/a"],
        ["Built by", "template backend, no language model"], ["Recorded grade", s.outcome], ["Simulator commands", fmt(s.n_events)],
        ["Rule violations", fmt(s.n_rule_violations)], ["Human hand-offs", fmt(s.n_human_pauses)]],
      missing: ["Not run on a robot: animation times are estimates.", "Volumes, times and temperatures are placeholders, not validated for any kit or assay."],
      digests: [{ label: "Program digest (code_sha256)", value: s.code_sha256 }],
      links: links,
      notes: [CEILING]
    };
  }
  function stepSpec(run, p, st) {
    return {
      title: "Step " + st.id + ": " + st.title,
      badges: [runLabel(run)],
      facts: [["Candidate", p.protein], ["Owner", (OWNER[st.owner] || [0, st.owner])[1]], ["Estimated duration", days(st.days) + " days (an estimate, not measured)"],
        ["Comes after", (st.after || []).join(", ") || "nothing (first step)"], ["Controls", (st.controls || []).join("; ") || "none listed"]],
      missing: ["Volumes, times and temperatures are placeholders.", "Cost is not estimated."],
      digests: [],
      links: [],
      notes: [st.note ? "Note: " + st.note : "", st.gate ? "Gate: " + st.gate : ""].filter(Boolean)
    };
  }
  function bundleSpec(run, p, c) {
    var byId = {};
    (run.stages || []).forEach(function (s) { byId[s.id] = s; });
    var n = c && c.s7_rank, bs = run.biosafety || {};
    return {
      title: "Handoff bundle: " + p.protein,
      badges: [runLabel(run)],
      facts: [["Candidate", p.protein + " (" + p.candidate_id + ")"], ["Final order", "rank " + p.rank], ["Draft protocol", p.steps.length + " steps"], ["S12 gate state", bs.state || "unknown"]],
      missing: ["Biosecurity: not cleared for synthesis.", "Cost: not estimated.", "Endotoxin release limit: not set.", "Volumes, times and temperatures: placeholders."],
      digests: [byId.s8 && { label: "S8 protocol artifact", value: byId.s8.digest }, byId.s12 && { label: "S12 biosafety artifact", value: byId.s12.digest },
        run.prereg && run.prereg.frozen_digest && { label: "Pre-registration frozen digest", value: run.prereg.frozen_digest }].filter(Boolean),
      links: [run.report && { label: "Open in report", href: run.report + (n ? "#c" + n : ""), external: true },
        byId.s8 && { label: "Open artifact JSON (S8)", href: (run.folder || "") + byId.s8.file, external: true }].filter(Boolean),
      notes: ["A reviewable draft, not an order to synthesise or to run anything."]
    };
  }

  /* ---------- 04 Generate with models ---------- */
  function renderGenerate(container, ctx) {
    var S = (ctx && ctx.state) || WS.state, data = WS.data, mt = data.model_test, rows = mt.rows;
    var models = uniq(rows.map(function (r) { return r.model; })), date = when(rows[0].started).date;
    var count = function (m, o) { return rows.filter(function (r) { return r.model === m && r.outcome === o; }).length; };
    var tally = models.map(function (m) {
      return modelName(m) + ": " + ["PASS", "FAIL", "REFUSED"].filter(function (o) { return count(m, o); }).map(function (o) { return count(m, o) + " " + o; }).join(", ");
    }).join(" · ");
    var shown = rows.map(function (r, i) { return [r, i]; }).filter(function (x) { return ui.filter === "all" || x[0].model === ui.filter; });
    var req = ["Protocol: " + mt.protocol, "Task: write one Opentrons OT-2 Python program per session", "Mode: spec mode, one attempt per model and session",
      "Checks afterwards: static lint, the Opentrons simulator, five physical rules",
      "(Short description only. The request text is not stored here; each job record carries its SHA-256 digest.)"].join("\n");
    var trs = shown.map(function (x) {
      var r = x[0], i = x[1], on = S.call === i, w = when(r.started);
      return '<tr class="' + (on ? "is-sel" : "") + '"><td><button type="button" class="wsr-rowbtn" data-wsr="job" data-i="' + i + '" data-keep="row-' + i + '" aria-pressed="' + on + '" aria-label="'
        + esc(r.session + ", " + modelName(r.model) + ": open job record") + '">' + esc(r.session) + "</button></td><td>" + esc(modelName(r.model)) + "</td><td>" + outcome(r.outcome) + '</td><td class="num">'
        + esc(w.time || r.started) + '</td><td class="num">' + esc(r.latency_s) + ' s</td><td class="num">' + fmt(r.input_tokens) + " / " + fmt(r.output_tokens) + "</td><td><code>" + esc(r.stop_reason) + "</code></td></tr>";
    }).join("");
    var ad = data.adaption || {}, h = ad.heldout_4b || {}, hc = h.counts || {}, a9 = ad["9b"] || {}, a4 = ad["4b"] || {};
    var task = card("wsr-task-h", "The task", "",
      "<p>One bounded task: a reviewed S8 protocol, compiled to Opentrons OT-2 programs, one per session. It is not an open request.</p>"
      + '<label class="ws-label wsr-req">Request preview (read-only)<textarea class="ws-field" readonly rows="5">' + esc(req) + "</textarea></label>"
      + '<p class="ws-note">Replay mode: a request cannot be sent from this page.</p>'
      + '<div class="ws-actions"><button type="button" class="ws-btn ws-btn-primary" disabled aria-describedby="wsr-why">Generate both</button>'
      + '<p class="ws-note wsr-why" id="wsr-why">Replay mode: no job API is connected. Nothing on this page sends a request or runs code.</p></div>');
    var laneA = card("wsr-la-h", "Lane A · Anthropic API", badge("Recorded model test"),
      '<p class="wsr-lane-note">Recorded ' + esc(date) + " (UTC). Not a request made now. One attempt per model and session.</p>"
      + tabs("Filter attempts by model", [["all", "All models"]].concat(models.map(function (m) { return [m, modelName(m)]; })), ui.filter, "filter")
      + '<p class="ws-count">Showing ' + shown.length + " of " + rows.length + " attempts. " + esc(tally) + ".</p>"
      + '<div class="ws-tablewrap" tabindex="0" role="region" aria-label="Recorded attempts, scrollable"><table class="ws-table"><caption class="ws-sr">Recorded Anthropic code-generation attempts. Select a session to open its job record in the evidence drawer.</caption>'
      + '<thead><tr><th scope="col">Session</th><th scope="col">Model</th><th scope="col">Outcome</th><th scope="col" class="num">Started (UTC)</th><th scope="col" class="num">Latency</th><th scope="col" class="num">Tokens in / out</th><th scope="col">Stop reason</th></tr></thead><tbody>'
      + trs + "</tbody></table></div>" + '<p class="ws-note">Select a session to open its job record in the evidence drawer.</p>', "wsr-lane");
    var laneB = card("wsr-lb-h", "Lane B · Local Adaption fine-tune", badge("Earlier Adaption run"),
      '<p class="wsr-lane-note">' + chip("notrun", "Not run in this recording") + " No local generation job and no verified adapter identity are stored.</p>"
      + '<div class="ws-empty"><strong>No local attempts to show.</strong> Lane A lists recorded attempts; this lane has none, and nothing is filled in to look like one.</div>'
      + "<h4>What does exist</h4>"
      + facts([["9B adapter checkpoint digest", dig(a9.checkpoint_sha256, 8, "Copy 9B checkpoint digest") + " base model " + esc(a9.base_model || "unknown"), true],
        ["4B adapter checkpoint digest", dig(a4.checkpoint_sha256, 8, "Copy 4B checkpoint digest") + " base model " + esc(a4.base_model || "unknown"), true],
        ["4B held-out simulator test", (hc.win || 0) + " wins, " + (hc.loss || 0) + " losses, " + (hc.tie || 0) + " ties on " + (h.n || 0) + " tasks from " + (h.families || 0)
          + " held-out families. Neither arm passed: " + (h.verdict || "no verdict recorded") + " (a floor effect)."]])
      + '<p class="ws-ceiling"><strong>A digest proves a file exists, not that it was loaded.</strong> A local lane may say “fine-tuned” only after the served adapter identity is verified.</p>', "wsr-lane");
    var legend = card("wsr-lg-h", "Job states", "", '<ul class="wsr-states">'
      + STATES.map(function (s) { return "<li>" + chip(s[1], s[0]) + " <span>" + esc(s[2]) + "</span></li>"; }).join("") + "</ul>"
      + '<p class="ws-note">Every state comes from the job record, never from a timer. A failed model request is never silently replaced by a template result. In this recording Lane A shows passed, failed and refused; Lane B shows not run.</p>');
    mount("generate", container, "Generate with models", "Two lanes can turn a reviewed protocol into robot code. This page replays what was recorded; it cannot start a job.",
      task + '<div class="wsr-lanes">' + laneA + laneB + "</div>" + legend
      + '<div class="ws-actions"><button type="button" class="ws-btn ws-btn-primary" data-wsr="to-tests" aria-describedby="wsr-tt">Test generated code</button>'
      + '<p class="ws-note" id="wsr-tt">Opens the recorded test results. Nothing is run.</p></div>');
  }

  /* ---------- 05 Test results ---------- */
  function sessionTitle(id) {      /* model-test rows carry no title: borrow it from the template session of the same name */
    var s = ((WS.data.runs.demo.robot || {}).sessions || []).filter(function (x) { return x.session_id === id; })[0];
    return s ? s.title : "";
  }
  function modelCol(r, i) {
    var id = "wsr-mc-" + i;
    return '<article class="ws-card wsr-run" aria-labelledby="' + id + '"><div class="ws-card-h"><h3 id="' + id + '">' + esc(modelName(r.model)) + " <code>" + esc(r.model) + "</code></h3><span>" + outcome(r.outcome) + "</span></div>"
      + '<p class="ws-note">One attempt · ' + esc(r.latency_s) + " s · " + fmt(r.input_tokens) + " tokens in, " + fmt(r.output_tokens) + " out · stop reason <code>" + esc(r.stop_reason) + "</code></p>"
      + '<p class="wsr-explain">' + esc(explain(r)) + "</p>" + checks(r) + gradeLog(r, ["stop_reason: " + r.stop_reason])
      + codeFig(r.code, r.code_sha256, "Generated code: " + modelName(r.model) + ", " + r.session, "No code: the model refused. There is nothing to verify.")
      + '<button type="button" class="ws-btn ws-btn-sm" data-wsr="job" data-i="' + i + '" data-keep="job-' + i + '">Show job record in the drawer</button></article>';
  }
  function modelView(S, data) {
    var rows = data.model_test.rows, sessions = uniq(rows.map(function (r) { return r.session; })), models = uniq(rows.map(function (r) { return r.model; }));
    var cur = S.mtSession && sessions.indexOf(S.mtSession) >= 0 ? S.mtSession : (S.call != null && rows[S.call] ? rows[S.call].session : sessions[0]);
    var cols = models.map(function (m) {
      var i = -1;
      rows.forEach(function (r, k) { if (r.model === m && r.session === cur) i = k; });
      return i < 0 ? '<article class="ws-card wsr-run"><h3>' + esc(modelName(m)) + "</h3><p>" + chip("notrun", "not run") + " No recorded attempt for this model and session.</p></article>" : modelCol(rows[i], i);
    }).join("");
    return '<div class="ws-controls">' + select("wsr-ses", "Session", sessions.map(function (s) { return [s, s + (sessionTitle(s) ? " · " + sessionTitle(s) : "")]; }), cur, "mt-session")
      + "<span>" + badge("Recorded model test") + "</span></div>"
      + '<p class="ws-note">Recorded ' + esc(when(rows[0].started).date) + ". Not a request made now. Both models are shown for the same session.</p>"
      + '<div class="wsr-pair">' + cols + "</div>";
  }
  function unverified(run, rb) {
    var n = rb.n_sessions || 0, un = rb.n_unverified == null ? n : rb.n_unverified, ds = ((WS.data.runs.demo.robot || {}).sessions || []).length;
    return card("wsr-uv-h", "Template programs of the " + runLabel(run), chip("incomplete", "UNVERIFIED") + " " + badge(runLabel(run)),
      '<p class="wsr-explain">S9 code was generated but not simulated (no simulator on that machine): UNVERIFIED, never PASS.</p>'
      + facts([["Programs generated", fmt(n)], ["Programs simulated", fmt(n - un)], ["Simulator", rb.opentrons_version ? "Opentrons " + rb.opentrons_version : "not installed on that machine"]])
      + '<p class="ws-note">This site does not list the individual programs of this run. Select Recorded offline run in the left rail to see the recorded grade of each of its ' + ds + " template programs.</p>");
  }
  function simFrame(url, title) {
    return '<iframe class="wsr-iframe" src="' + esc(url) + '" title="' + esc(title) + '" sandbox="allow-scripts allow-same-origin" loading="lazy" height="680"></iframe>';
  }
  function templateView(S, run) {
    var rb = run.robot || {}, ss = rb.sessions || [];
    if (!ss.length) return unverified(run, rb);
    var si = S.session != null && ss[S.session] ? S.session : 0, s = ss[si], cands = [], seen = {};
    ss.forEach(function (x) { if (!seen[x.candidate_id]) { seen[x.candidate_id] = 1; cands.push(x); } });
    cands.sort(function (a, b) { return a.candidate_rank - b.candidate_rank; });
    var mine = ss.map(function (x, i) { return i; }).filter(function (i) { return ss[i].candidate_id === s.candidate_id; });
    var code = runUrl(run, s.code), anim = runUrl(run, s.animation), on = !!(anim && ui.sim[anim]), title = "Protocol animation: " + s.title + " (" + s.candidate + ")";
    var links = [["Setup sheet (what to load on the deck)", s.setup], ["Plan (JSON)", s.plan], ["Recorded grade (JSON)", s.grade_file]].filter(function (l) { return runUrl(run, l[1]); })
      .map(function (l) { return '<li><a href="' + esc(runUrl(run, l[1])) + '" target="_blank" rel="noopener">' + esc(l[0]) + '<span class="ws-sr"> (new tab)</span></a></li>'; }).join("");
    return '<div class="ws-controls">'
      + select("wsr-tc", "Candidate", cands.map(function (c) { return [c.candidate_id, c.candidate_rank + " · " + c.candidate]; }), s.candidate_id, "tpl-cand")
      + select("wsr-ts", "Session", mine.map(function (i) { return [i, ss[i].session_id + " · " + ss[i].title]; }), si, "tpl-session")
      + "<span>" + badge(runLabel(run)) + "</span></div>"
      + '<article class="ws-card wsr-run" aria-labelledby="wsr-prog-h"><div class="ws-card-h"><h3 id="wsr-prog-h">' + esc(s.title) + "</h3><span>" + outcome(s.outcome) + "</span></div>"
      + '<p class="ws-note"><code>' + esc(s.session_id) + "</code> · covers S8 step " + esc((s.covers || []).join(", ") || "n/a") + " · candidate rank " + esc(s.candidate_rank) + ", " + esc(s.candidate) + "</p>"
      + "<p><strong>Built by the template backend, no language model.</strong></p>"
      + '<p class="wsr-explain">' + esc(explain(s)) + "</p>" + checks(s)
      + '<p class="ws-note">' + plural(s.n_human_pauses || 0, "human hand-off") + ": the robot pauses for a person (listed on the setup sheet).</p>"
      + gradeLog(s, ["n_rule_violations: " + s.n_rule_violations, "findings: " + ((s.findings || []).length ? s.findings.map(str).join("; ") : "none")])
      + codeFig(code, s.code_sha256, "Program code: " + s.session_id, "No program file is listed for this session.")
      + (links ? '<ul class="wsr-links">' + links + "</ul>" : "")
      + '<div class="wsr-sim">' + (anim ? '<button type="button" class="ws-btn" data-wsr="watch" data-url="' + esc(anim) + '" data-title="' + esc(title) + '" aria-expanded="' + on + '">' + (on ? "Hide the simulation" : "Watch the simulation") + "</button>"
        + '<p class="ws-note">Times in the animation are estimates, not measured on a robot.</p><div class="wsr-simbox">' + (on ? simFrame(anim, title) : "") + "</div>"
        : '<p class="ws-note">No animation page is stored for this program.</p>') + "</div></article>";
  }
  function renderTests(container, ctx) {
    var S = (ctx && ctx.state) || WS.state, run = getRun(ctx), data = WS.data, mode = S.mode === "template" ? "template" : "model";
    mount("tests", container, "Test results", "Each generated program is linted, run in the Opentrons simulator and checked against five physical rules. These are recorded results: nothing is generated or run on this page.",
      '<p class="ws-note">One recorded protocol with one attempt per model and session is not a benchmark. Read the counts as a description of this recording only.</p>'
      + "<div>" + tabs("Result source", [["model", "Recorded Anthropic outputs"], ["template", "Template programs"]], mode, "mode") + "</div>"
      + '<p class="ws-ceiling">' + esc(CEILING) + "</p>" + (mode === "model" ? modelView(S, data) : templateView(S, run)));
  }

  /* ---------- 06 Handoff ---------- */
  function protos(run) { return (run.protocols || []).slice().sort(function (a, b) { return a.rank - b.rank; }); }
  function candOf(run, id) { return (run.candidates || []).filter(function (c) { return c.protein_id === id; })[0] || null; }
  function candRows(c) {
    var s4 = c.s4 || {}, st = s4.status, ep = (c.s5 || {}).class_i_epitopes;
    var s4c = st === "kept" ? chip("ok", "kept") : st === "flagged" ? chip("review", "flagged") : st === "rejected" ? chip("failed", "rejected") : chip("notrun", "not run");
    var note = st === "kept" || st === "flagged" || st === "rejected" ? ((s4.flags || []).length ? " " + esc(s4.flags.join("; ")) : "") : " The S4 gates did not run.";
    return [["Protein", c.name + (c.gene ? " (" + c.gene + ")" : "")], ["Identifier", c.protein_id], ["Length", fmt(c.length) + " amino acids"],
      ["Protegenicity", c.score + " (Vaxign-ML percentile, not a probability)"], ["S4 safety and conservancy gates", s4c + note, true],
      ["Class I epitopes (S5)", ep == null ? "not run" : fmt(ep)], ["Final order (S7)", c.s7_rank ? "rank " + c.s7_rank + ", sequence-only: no structure term" : "not carried past S3"],
      ["Localisation (S2)", (c.localisation || "unknown") + " (a stand-in from UniProt text, not PSORTb)"],
      ["Closest virulence-factor match", c.vfdb_identity == null ? "none recorded" : c.vfdb_identity + "% identity to " + c.vfdb_hit]];
  }
  function animalRows(run) {
    var a = run.animal || {}, f = a.falsification || {}, by = f.by_status || {};
    var parts = Object.keys(by).map(function (k) { return by[k] + " " + k.replace(/_/g, " ").replace(/^not testable (no|in) /, "not testable: $1 "); });
    return [["Falsification rows", fmt(f.total) + " in total: " + (parts.join("; ") || "none")], ["Outstanding human inputs", fmt(a.outstanding_human_inputs)],
      ["Adjuvant in the design", a.adjuvant || "not recorded"], ["Study arms", fmt(a.n_arms)]];
  }
  function stepsTable(p) {
    var rows = p.steps.map(function (st) {
      var o = OWNER[st.owner] || ["human", String(st.owner)], on = ui.step === p.candidate_id + "/" + st.id;
      return '<tr class="' + (on ? "is-sel" : "") + '"><td><button type="button" class="wsr-rowbtn" data-wsr="step" data-id="' + esc(st.id) + '" aria-pressed="' + on + '"><code>' + esc(st.id) + "</code> " + esc(st.title) + "</button></td><td>"
        + pill(o[0], o[1]) + '</td><td class="num">' + esc(days(st.days)) + "</td><td>" + esc((st.controls || []).join("; ") || "none listed") + "</td></tr>";
    }).join("");
    return '<div class="ws-tablewrap" tabindex="0" role="region" aria-label="Draft protocol steps, scrollable"><table class="ws-table"><caption class="ws-sr">Draft protocol steps for ' + esc(p.protein)
      + '. Select a step to see its details in the evidence drawer.</caption><thead><tr><th scope="col">Step</th><th scope="col">Owner</th><th scope="col" class="num">Days (estimate)</th><th scope="col">Controls</th></tr></thead><tbody>'
      + rows + "</tbody></table></div>";
  }
  function softwareCol(run, p) {
    var rb = run.robot || {}, ss = rb.sessions || [], n = rb.n_sessions || ss.length, pass = ss.filter(function (s) { return s.outcome === "PASS"; }).length;
    var mine = ss.filter(function (s) { return s.candidate_id === p.candidate_id; }), mp = mine.filter(function (s) { return s.outcome === "PASS"; }).length;
    var s9 = rb.simulated && ss.length
      ? "<p>" + chip(pass === n ? "ok" : "failed", pass + " of " + n + " PASS") + " <strong>" + pass + " of " + n + "</strong> template-built programs pass lint, simulator and the five rules.</p>"
        + facts([["Programs for this candidate", mp + " of " + mine.length + " PASS"], ["Simulator", "Opentrons " + (rb.opentrons_version || "unknown")], ["Built by", "template backend, no language model"]])
        + '<p class="ws-ceiling">' + esc(CEILING) + "</p>"
      : "<p>" + chip("incomplete", "UNVERIFIED") + " Code generated, not simulated: <strong>UNVERIFIED</strong>, never PASS.</p>"
        + facts([["Programs generated", fmt(n)], ["Programs simulated", fmt(n - (rb.n_unverified == null ? n : rb.n_unverified))], ["Simulator", rb.opentrons_version ? "Opentrons " + rb.opentrons_version : "not installed on that machine"]]);
    return card("wsr-soft-h", "Software checks", "", '<p class="ws-note">What programs checked. None of this says the science is right.</p>'
      + "<h4>Robot code (S9)</h4>" + s9 + "<h4>Digest verification</h4>" + '<p data-wsr-v="status"></p>', "wsr-soft");
  }
  function outstanding(run, p) {
    var bs = run.biosafety || {}, rb = run.robot || {}, a = run.animal || {}, gate = p.steps.filter(function (s) { return /release limit/i.test(s.gate || ""); })[0];
    return card("wsr-out-h", "Outstanding requirements", "",
      '<p class="wsr-gate">' + chip("blocked", "Not cleared for synthesis") + " Artifact state: <code>" + esc(bs.state || "unknown") + "</code> (S12 biosecurity gate)</p>"
      + "<h4>Why it is not cleared</h4>" + ul(bs.reasons)
      + "<h4>Flagged for human review " + chip("review", "not blocked") + "</h4>" + ul(bs.review)
      + "<h4>Still to be set before any lab work</h4>"
      + ul(["Placeholder parameters: " + String(rb.parameters_status || "volumes, times and temperatures are defaults").replace(/^PLACEHOLDER PARAMETERS:\s*/i, ""),
        "Cost: not estimated. " + (p.estimate_basis || ""), "Endotoxin release limit: not set." + (gate ? " Step " + gate.id + " reads: " + gate.gate : ""),
        fmt(a.outstanding_human_inputs) + " human inputs are still outstanding in the animal-study design."]), "wsr-outstanding");
  }
  function verifyCard(run) {
    return card("wsr-ver-h", "Verify this run", "",
      "<p>Fetches the stage artifacts of the " + esc(runLabel(run)) + " from this site, recomputes each SHA-256 digest in your browser and checks that every artifact’s inputs match the digest of its upstream artifact.</p>"
      + '<p class="ws-note">A match shows the files are unchanged since they were recorded. It does not show that the results are right.</p>'
      + '<div class="ws-actions"><button type="button" class="ws-btn ws-btn-primary" data-wsr="verify-run">Verify this run</button></div>'
      + '<div data-wsr-v="body" aria-live="polite"></div>');
  }
  function renderHandoff(container, ctx) {
    var S = (ctx && ctx.state) || WS.state, run = getRun(ctx), ps = protos(run);
    var lede = "One candidate’s reviewable bundle from this recorded run. Scientific review and software checks are shown apart on purpose: a passing software check says nothing about whether the science is right.";
    if (!ps.length) { mount("handoff", container, "Handoff", lede, '<p class="ws-empty">No draft protocol is recorded for this run.</p>'); return; }
    var sel = S.cand != null && run.candidates[S.cand] ? run.candidates[S.cand].protein_id : null, k = 0;
    ps.forEach(function (p, i) { if (p.candidate_id === sel) k = i; });
    var miss = !!sel && ps[k].candidate_id !== sel, p = ps[k], c = candOf(run, p.candidate_id);
    var rep = run.report ? run.report + (c && c.s7_rank ? "#c" + c.s7_rank : "") : null;
    var pick = '<fieldset class="wsr-pick"><legend>Candidate for this bundle</legend><p class="ws-note">Only candidates with a draft protocol can be handed off: the top ' + ps.length + " of the final order.</p>"
      + ps.map(function (q, i) {
        return '<label class="wsr-opt' + (i === k ? " is-on" : "") + '"><input type="radio" name="wsr-cand" value="' + i + '" data-wsr="pick" data-keep="pick-' + i + '"' + (i === k ? " checked" : "") + "><span><strong>Final order " + esc(q.rank) + "</strong> "
          + esc(q.protein) + " <code>" + esc(q.candidate_id) + "</code></span></label>";
      }).join("") + "</fieldset>";
    var sci = card("wsr-sci-h", "Scientific review", "", '<p class="ws-note">A ranked hypothesis and a draft plan. A scientist still has to judge both.</p>'
      + "<h4>Candidate evidence summary</h4>" + (c ? facts(candRows(c)) : '<p class="ws-note">This candidate is not in the run’s candidate table.</p>')
      + (rep ? '<p><a href="' + esc(rep) + '" target="_blank" rel="noopener">Open this candidate in the report<span class="ws-sr"> (new tab)</span></a></p>' : "")
      + "<h4>Draft protocol</h4>" + stepsTable(p) + '<p class="ws-note">' + esc(p.estimate_basis || "Durations are estimates.") + "</p>"
      + '<h4>How this could be wrong</h4><div class="ws-ceiling"><strong>Falsification condition</strong><p>' + esc(p.falsifier) + "</p></div>"
      + "<h4>What this does not measure</h4>" + ul(p.does_not_measure) + "<h4>Animal-study design</h4>" + facts(animalRows(run)), "wsr-sci");
    mount("handoff", container, "Handoff", lede,
      (miss ? '<p class="ws-note">The candidate selected on the review screen has no draft protocol, so this bundle shows the final-order rank 1 candidate. Choose another below.</p>' : "")
      + pick + '<div class="wsr-cols">' + sci + softwareCol(run, p) + "</div>" + outstanding(run, p)
      + (rep ? '<div class="ws-actions"><a class="ws-btn ws-btn-primary" href="' + esc(rep) + '" target="_blank" rel="noopener">View report<span class="ws-sr"> (opens in a new tab)</span></a>'
        + '<p class="ws-note">The report is the same recorded run, rendered by the pipeline.</p></div>' : "")
      + verifyCard(run));
    paintVerify(run);
  }

  /* ---------- Verify this run: rows are WSVerify results {name, recorded, recomputed, ok, problems, chain, chainOk, error} ---------- */
  function normVerify(res) {
    var rows = Array.isArray(res) ? res : (res && (res.results || res.rows)) || [];
    var sm = (res && res.summary) || (window.WSVerify && typeof window.WSVerify.summarize === "function" ? window.WSVerify.summarize(rows) : null) || {};
    var good = rows.filter(function (r) { return r.ok; }).length;
    return { rows: rows, allOk: sm.allOk != null ? !!sm.allOk : rows.length > 0 && good === rows.length, text: sm.text || good + " of " + rows.length + " artifacts match", chainText: sm.chainText || "" };
  }
  function verifyHtml(v) {
    if (v.state === "loading") return [chip("info", "Checking") + " Fetching and hashing the artifacts ...", '<p class="ws-note" role="status">Fetching the artifacts and hashing them in your browser ...</p>'];
    if (v.state === "error") {
      var m = "Could not verify this run: " + v.msg + ".";
      return [chip("failed", "Could not check") + " " + esc(m), '<p class="ws-error" role="alert"><strong>' + esc(m) + "</strong> Nothing was changed and no result is shown. If this page was opened as a local file, serve the folder over http://127.0.0.1 or https: browsers block file reads and WebCrypto there.</p>"];
    }
    if (v.state !== "done") return [chip("notrun", "Not run yet") + " Use Verify this run below to recompute every digest in your browser.", ""];
    var r = v.res, kind = r.allOk ? "ok" : "failed", line = r.text + (r.chainText ? ". " + r.chainText : "") + ".";
    var trs = r.rows.map(function (x) {
      var broken = (x.chain || []).filter(function (l) { return !l.ok; }).map(function (l) { return l.note || "input " + l.input + " does not match"; });
      var why = (x.problems || []).concat(x.error ? [x.error] : [], broken).join("; ");
      return "<tr><td><code>" + esc(x.name) + "</code></td><td><code>" + esc(sh(x.recorded, 12)) + "</code></td><td><code>" + esc(sh(x.recomputed, 12)) + "</code></td><td>"
        + (x.ok ? chip("ok", "match") : chip("failed", x.error ? "not checked" : "mismatch")) + "</td><td>"
        + (!(x.chain || []).length ? '<span class="ws-note">no upstream inputs</span>' : x.chainOk ? chip("ok", "consistent") : chip("failed", "broken"))
        + (why ? '<div class="ws-note">' + esc(why) + "</div>" : "") + "</td></tr>";
    }).join("");
    return [chip(kind, r.allOk ? "Verified" : "Check failed") + " " + esc(line),
      '<p class="wsr-summary" role="status">' + chip(kind, r.allOk ? "Verified" : "Check failed") + " <strong>" + esc(line) + "</strong></p>"
      + '<div class="ws-tablewrap" tabindex="0" role="region" aria-label="Digest check per artifact, scrollable"><table class="ws-table"><caption class="ws-sr">Digest check for each stage artifact</caption>'
      + '<thead><tr><th scope="col">Artifact</th><th scope="col">Recorded digest</th><th scope="col">Recomputed digest</th><th scope="col">Result</th><th scope="col">Upstream chain</th></tr></thead><tbody>' + trs + "</tbody></table></div>"];
  }
  function paintVerify(run) {
    var root = document.querySelector('.wsr[data-screen="handoff"]');
    if (!root) return;
    var v = ui.vr[run.key] || { state: "idle" }, h = verifyHtml(v), st = root.querySelector("[data-wsr-v=status]"), body = root.querySelector("[data-wsr-v=body]"), btn = root.querySelector("[data-wsr=verify-run]");
    if (st) st.innerHTML = h[0];
    if (body) body.innerHTML = h[1];
    if (btn) { btn.disabled = v.state === "loading"; btn.setAttribute("aria-busy", v.state === "loading" ? "true" : "false"); }
  }

  /* ---------- mounting and events ---------- */
  function mount(screen, container, title, lede, body) {
    container.innerHTML = '<div class="wsr ws-screen" data-screen="' + screen + '"><p class="ws-lede">' + esc(lede) + '</p><div class="ws-body">' + body + "</div></div>";
    var root = container.firstElementChild;
    root.addEventListener("click", function (e) {
      var t = e.target && e.target.closest ? e.target.closest("[data-wsr]") : null;
      if (t && root.contains(t) && !t.disabled && ACT[t.getAttribute("data-wsr")]) ACT[t.getAttribute("data-wsr")](t, root);
    });
    root.addEventListener("change", function (e) {
      var t = e.target, f = t && t.getAttribute ? CHG[t.getAttribute("data-wsr")] : null;
      if (f) f(t, root);
    });
    mounted[screen] = container;
    [].forEach.call(root.querySelectorAll("pre[data-wsr-code]"), paintCode);
    return root;
  }
  function rerender(screen) {      /* local view changes (filter): redraw without touching the drawer or the URL */
    var c = mounted[screen];
    if (c && document.body.contains(c)) keep(function () { guard(screen)(c, null); });
  }
  function drawerNow(spec) { if (wide()) WS.drawer(spec); }   /* on narrow screens the drawer is a panel: open it only on an explicit pick */

  var ACT = {
    filter: function (t) {
      ui.filter = t.getAttribute("data-m");
      rerender("generate");
      WS.announce("Showing " + (ui.filter === "all" ? "all recorded attempts." : "attempts by " + modelName(ui.filter) + "."));
    },
    job: function (t) {
      var i = +t.getAttribute("data-i"), r = WS.data.model_test.rows[i];
      if (!r) return;
      keep(function () { WS.setState({ call: i }); });
      WS.drawer(jobSpec(r));
      WS.announce("Job record for " + modelName(r.model) + ", " + r.session + ", shown in the evidence drawer. Outcome " + r.outcome + ".");
    },
    "to-tests": function () {
      var S = WS.state, r = S.call != null ? WS.data.model_test.rows[S.call] : null;
      WS.setState(r ? { mode: "model", mtSession: r.session } : { mode: "model" });
      WS.go("tests");
    },
    mode: function (t) {
      var m = t.getAttribute("data-m");
      keep(function () { WS.setState({ mode: m }); });
      WS.announce(m === "template" ? "Showing template programs." : "Showing recorded Anthropic outputs.");
    },
    "code-more": function (t) {
      var url = t.getAttribute("data-url");
      ui.full[url] = !ui.full[url];
      paintCode(t.parentNode.querySelector("pre"));
    },
    verify: function (t) {
      var url = t.getAttribute("data-url"), want = hex(t.getAttribute("data-sha")), out = t.parentNode.parentNode.querySelector(".wsr-vout");
      if (!(window.crypto && window.crypto.subtle)) { out.textContent = "Verification needs a secure context (https or localhost)."; return; }
      t.disabled = true;
      out.textContent = "Fetching the file and hashing it in your browser ...";
      Promise.resolve().then(function () { return WS.verifyBytes(url, want); }).then(function (r) {
        ui.vout[url] = r && r.ok ? chip("ok", "Digest matches") + " The file hashes to <code>" + esc(sh(r.actual, 12)) + "</code>, the same as the recorded <code>" + esc(sh(r.expected || want, 12)) + "</code>."
          : chip("failed", "Digest mismatch") + " The file hashes to <code>" + esc(sh(r && r.actual, 12)) + "</code> but the recorded digest is <code>" + esc(sh((r && r.expected) || want, 12)) + "</code>. Treat this file as changed.";
      }, function (err) {
        ui.vout[url] = chip("failed", "Could not verify") + " " + esc((err && err.message) || "The file could not be read") + ". Nothing was compared.";
      }).then(function () {
        t.disabled = false;
        if (document.body.contains(out)) out.innerHTML = ui.vout[url];
      });
    },
    watch: function (t) {
      var url = t.getAttribute("data-url"), box = t.parentNode.querySelector(".wsr-simbox"), on = !ui.sim[url];
      ui.sim[url] = on;
      box.innerHTML = on ? simFrame(url, t.getAttribute("data-title")) : "";
      t.setAttribute("aria-expanded", on ? "true" : "false");
      t.textContent = on ? "Hide the simulation" : "Watch the simulation";
    },
    step: function (t, root) {
      var run = WS.run(), ps = protos(run), c = run.candidates[WS.state.cand], p = ps.filter(function (q) { return c && q.candidate_id === c.protein_id; })[0] || ps[0];
      var st = p.steps.filter(function (s) { return s.id === t.getAttribute("data-id"); })[0];
      if (!st) return;
      ui.step = p.candidate_id + "/" + st.id;
      [].forEach.call(root.querySelectorAll("[data-wsr=step]"), function (b) {
        b.setAttribute("aria-pressed", b === t ? "true" : "false");
        b.closest("tr").classList.toggle("is-sel", b === t);
      });
      WS.drawer(stepSpec(run, p, st));
      WS.announce("Step " + st.id + " shown in the evidence drawer.");
    },
    "verify-run": function () {
      var run = WS.run(), v = ui.vr[run.key] = { state: "loading" };
      paintVerify(run);
      Promise.resolve().then(function () { return WS.verifyRun(run.key); }).then(function (res) {
        v.state = "done";
        v.res = normVerify(res);
      }, function (err) {
        v.state = "error";
        v.msg = (err && err.message) || "the artifacts could not be read";
      }).then(function () {
        paintVerify(WS.run());
        WS.announce(v.state === "done" ? v.res.text + "." : "Verification failed: " + v.msg + ".");
      });
    }
  };
  var CHG = {
    "mt-session": function (t) {
      keep(function () { WS.setState({ mtSession: t.value }); });
      WS.announce("Showing both models for session " + t.value + ".");
    },
    "tpl-cand": function (t) {
      var run = WS.run(), ss = run.robot.sessions, cur = ss[WS.state.session != null && ss[WS.state.session] ? WS.state.session : 0];
      var idx = ss.map(function (s, i) { return i; }).filter(function (i) { return ss[i].candidate_id === t.value; });
      var same = idx.filter(function (i) { return ss[i].session_id === cur.session_id; }), pick = (same.length ? same : idx)[0];
      keep(function () { WS.setState({ session: pick }); });
      drawerNow(progSpec(run, ss[pick]));
      WS.announce("Showing " + ss[pick].session_id + " for " + ss[pick].candidate + ".");
    },
    "tpl-session": function (t) {
      var run = WS.run(), s = run.robot.sessions[+t.value];
      if (!s) return;
      keep(function () { WS.setState({ session: +t.value }); });
      drawerNow(progSpec(run, s));
      WS.announce("Showing " + s.session_id + " for " + s.candidate + ".");
    },
    pick: function (t) {
      var run = WS.run(), q = protos(run)[+t.value], ci = -1;
      if (!q) return;
      run.candidates.forEach(function (c, i) { if (c.protein_id === q.candidate_id) ci = i; });
      ui.step = null;
      keep(function () { WS.setState({ cand: ci < 0 ? null : ci }); });
      WS.drawer(bundleSpec(run, q, candOf(run, q.candidate_id)));
      WS.announce("Handoff bundle for " + q.protein + ", final order " + q.rank + ".");
    }
  };

  function guard(screen) {
    return function (container, ctx) {
      try {
        if (!WS.data) { container.innerHTML = '<p class="ws-loading" role="status">Loading recorded run ...</p>'; return; }
        SCREENS[screen](container, ctx);
      } catch (e) {
        container.innerHTML = '<div class="wsr ws-screen"><p class="ws-error" role="alert">This screen could not be drawn (' + esc(e && e.message) + "). The rest of the workspace still works.</p></div>";
      }
    };
  }
  SCREENS.generate = renderGenerate;
  SCREENS.tests = renderTests;
  SCREENS.handoff = renderHandoff;
  [["generate", "Generate with models"], ["tests", "Test results"], ["handoff", "Handoff"]].forEach(function (s) {
    WS.registerScreen(s[0], { title: s[1], render: guard(s[0]) });
  });
})();
