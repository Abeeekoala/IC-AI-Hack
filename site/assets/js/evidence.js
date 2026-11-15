/* VaxForge evidence page (evidence.html).
   Reads this site's own data/workspace.json and draws three things: the recorded model-test outcome grid with a detail panel,
   the Adaption cards and run-facts table, and the Boltz confidence bars. No libraries. The only requests are that one data
   file and, when a failed attempt is opened, the recorded program under runs/modeltest/ (to show the line the simulator
   stopped at). Every value from the data passes through esc() before it reaches innerHTML, and every number that lives in
   the data is drawn from it, so the text cannot drift from the record. Nothing here calls a model or runs code. */
(function () {
  "use strict";

  var DATA_URL = "data/workspace.json";
  var MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  var ELLIPSIS = String.fromCharCode(0x2026);
  var MIDDOT = String.fromCharCode(0xb7);
  var LOAD_ERR = "Could not load data/workspace.json, so this part cannot be drawn. The text around it does not depend on that file.";
  var PART_ERR = "This part could not be drawn from the recorded data. The text around it does not depend on it.";
  var SLOTS = ["ev-grid-slot", "ev-tally-slot", "ev-9b-slot", "ev-4b-slot", "ev-runs-slot", "ev-boltz-mode", "ev-boltz-slot"];
  var state = { rows: [], trigger: null, token: 0 };

  /* ---------- small helpers ---------- */
  function esc(v) {
    return String(v === null || v === undefined ? "" : v).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function byId(id) { return document.getElementById(id); }
  function has(o, k) { return o !== null && typeof o === "object" && Object.prototype.hasOwnProperty.call(o, k); }
  function isNum(n) { return typeof n === "number" && isFinite(n); }
  function num(n) { return isNum(n) ? String(n) : "?"; }
  function plural(n, one, many) { return num(n) + " " + (n === 1 ? one : many); }
  function fmtInt(n) { return isNum(n) ? n.toLocaleString("en-GB") : "not recorded"; }
  function pad2(n) { return (n < 10 ? "0" : "") + n; }
  function sentence(s) { var t = String(s).trim(); return /[.!?]$/.test(t) ? t : t + "."; }
  function fmtStarted(iso) {
    var d = typeof iso === "string" ? new Date(iso) : null;
    if (!d || isNaN(d.getTime())) return "not recorded";
    return d.getUTCDate() + " " + MONTHS[d.getUTCMonth()] + " " + d.getUTCFullYear() + ", " +
      pad2(d.getUTCHours()) + ":" + pad2(d.getUTCMinutes()) + ":" + pad2(d.getUTCSeconds()) + " UTC";
  }
  function announce(msg) {
    var el = byId("ev-live");
    if (!el) return;
    el.textContent = "";
    setTimeout(function () { el.textContent = msg; }, 40);
  }

  /* ---------- names, chips, plain-English reasons ---------- */
  var WORDS = { pcr: "PCR", lal: "LAL", elisa: "ELISA", facs: "FACS", qpcr: "qPCR", rt: "RT", ni: "Ni", nta: "NTA" };
  function modelLabel(id) {
    var m = /^claude-([a-z]+)-(\d+)-(\d+)$/.exec(String(id || ""));
    return m ? m[1].charAt(0).toUpperCase() + m[1].slice(1) + " " + m[2] + "." + m[3] : String(id || "unknown model");
  }
  function sessionTitle(id) {
    var parts = String(id || "").replace(/^s\d+_/, "").split("_").filter(Boolean);
    if (!parts.length) return String(id || "unknown session");
    var text = parts.map(function (w) { return has(WORDS, w) ? WORDS[w] : w; }).join(" ");
    text = text.replace("RT qPCR", "RT-qPCR").replace("Ni NTA", "Ni-NTA");
    return text.charAt(0).toUpperCase() + text.slice(1);
  }

  var KIND = { ok: "pill pill-ok", notrun: "pill pill-notrun", failed: "pill ev-pill-failed", info: "pill pill-info" };
  function chip(kind, word) { return '<span class="' + KIND[kind] + '">' + esc(word) + "</span>"; }
  function outcomeChip(o) {
    if (o === "PASS") return chip("ok", "PASS");
    if (o === "FAIL") return chip("failed", "FAIL");
    if (o === "REFUSED") return '<span class="pill pill-refused ev-pill-dashed">REFUSED</span>';
    return chip("notrun", o ? String(o) : "no outcome");
  }

  function simLine(err) {
    var m = /\[line (\d+)\]/.exec(String(err === null || err === undefined ? "" : err));
    return m ? parseInt(m[1], 10) : null;
  }
  function ruleTotal(rc) {
    if (!rc || typeof rc !== "object") return 0;
    return Object.keys(rc).reduce(function (s, k) { return s + (isNum(rc[k]) ? rc[k] : 0); }, 0);
  }
  var ERR_PLAIN = {
    NameError: "the program used a name that was never defined",
    AttributeError: "the program used a method or attribute that does not exist",
    SyntaxError: "the code is not valid Python",
    IndentationError: "the code is not valid Python (bad indentation)",
    ImportError: "the program imported a module that is not available",
    ModuleNotFoundError: "the program imported a module that is not available"
  };
  /* Plain text out (the caller escapes it). The wording says what the simulator found, not why the model wrote it. */
  function explainError(err) {
    var raw = String(err === null || err === undefined ? "" : err).trim();
    if (!raw) return "The simulator reported a failure, but no error text was recorded.";
    var line = simLine(raw);
    var head = line ? "The simulator stopped at line " + line + ": " : "The simulator stopped the program: ";
    var first = /^([A-Za-z_][A-Za-z0-9_.]*)\s*:/.exec(raw);
    var name = first ? first[1] : "";
    if (name === "TipNotAttachedError" || raw.indexOf("TipNotAttachedError") !== -1) {
      return head + "the pipette had no tip attached when the program tried to use it.";
    }
    if (name === "TypeError") {
      var miss = /([A-Za-z_][A-Za-z0-9_.]*)\(\) missing (\d+) required positional arguments?: (.+)$/.exec(raw);
      if (miss) {
        return head + "the program called " + miss[1] + "() without " + (miss[2] === "1" ? "an argument" : miss[2] + " arguments") +
          " that the function requires (" + miss[3].replace(/'/g, "") + ").";
      }
      return head + "the program called a function with a missing or wrong argument.";
    }
    if (name && has(ERR_PLAIN, name)) return head + ERR_PLAIN[name] + ".";
    return head + "the program raised " + (name || "an error") + ". The raw message is in the log below.";
  }
  function explain(r) {
    if (r.outcome === "PASS") {
      var clean = r.sim_ok === true && Array.isArray(r.lint) && r.lint.length === 0;
      return clean ? "Passed lint, ran in the simulator and broke none of the five physical rules."
        : "Recorded as PASS. The checks below show what was run.";
    }
    if (r.outcome === "REFUSED") {
      return "The model declined to write code. This is reported as an outcome, not a crash, and there was no program to check.";
    }
    if (r.outcome === "FAIL") {
      if (r.sim_error) return explainError(r.sim_error);
      if (Array.isArray(r.lint) && r.lint.length) {
        return "The program was written, but lint reported " + plural(r.lint.length, "finding", "findings") + ". The raw log lists them.";
      }
      var broken = ruleTotal(r.rule_counts);
      if (broken > 0) return "The program ran, but the rule checks found " + plural(broken, "violation", "violations") + ".";
      return "The program failed a check, but the record does not say which.";
    }
    return "No outcome was recorded for this attempt.";
  }

  /* ---------- digests, facts, checks, raw log ---------- */
  function validDigest(h) { return typeof h === "string" && /^[0-9a-f]{64}$/i.test(h); }
  function digestChip(hex, what) {
    if (!validDigest(hex)) return "";
    return '<span class="ev-digest"><code title="Full SHA-256: ' + esc(hex) + '">' + esc(hex.slice(0, 8)) + ELLIPSIS + "</code>" +
      '<button type="button" class="ev-copy" data-copy="' + esc(hex) + '" aria-label="Copy the full SHA-256 digest of ' + esc(what) + '">Copy</button></span>';
  }
  function fact(label, html) { return "<div><dt>" + esc(label) + "</dt><dd>" + html + "</dd></div>"; }
  function codeOrNot(v) { return typeof v === "string" && v ? "<code>" + esc(v) + "</code>" : "not recorded"; }
  var STOP_PLAIN = { end_turn: "the model finished its reply", refusal: "the model declined" };
  function stopHtml(s) {
    if (typeof s !== "string" || !s) return "not recorded";
    return "<code>" + esc(s) + "</code>" + (has(STOP_PLAIN, s) ? " " + esc("(" + STOP_PLAIN[s] + ")") : "");
  }
  function codeUrl(r) {
    return typeof r.code === "string" && /^runs\/modeltest\/[A-Za-z0-9_.\-]+\.py$/.test(r.code) ? r.code : null;
  }

  function checkRow(name, chipHtml, note) {
    return '<li><span class="ev-check-name">' + esc(name) + "</span>" + chipHtml + '<span class="ev-check-note">' + esc(note) + "</span></li>";
  }
  function ruleNames(rc) {
    return Object.keys(rc).map(function (k) { return k.replace(/_/g, " ") + " " + rc[k]; }).join(", ");
  }
  /* A check that did not run is "not run", never a tick: no program means no lint, no simulation and no rule check. */
  function checksHtml(r) {
    var lint, sim, rules, line, total;
    if (Array.isArray(r.lint)) {
      lint = r.lint.length ? checkRow("Lint", chip("failed", plural(r.lint.length, "finding", "findings")), "listed in the raw log")
        : checkRow("Lint", chip("ok", "ok"), "no findings");
    } else {
      lint = checkRow("Lint", chip("notrun", "not run"), "there was no program to check");
    }
    if (r.sim_ok === true) {
      sim = checkRow("Simulator", chip("ok", "ok"),
        "ran to the end" + (isNum(r.n_events) ? ", " + plural(r.n_events, "event", "events") + " recorded" : ""));
    } else if (r.sim_ok === false) {
      line = simLine(r.sim_error);
      sim = checkRow("Simulator", chip("failed", "stopped"), line ? "stopped at line " + line : "stopped with an error");
    } else {
      sim = checkRow("Simulator", chip("notrun", "not run"), "there was no program to run");
    }
    if (r.sim_ok === true && r.rule_counts && typeof r.rule_counts === "object") {
      total = ruleTotal(r.rule_counts);
      rules = checkRow("Rule checks", total === 0 ? chip("ok", "ok") : chip("failed", plural(total, "violation", "violations")),
        (total === 0 ? "none broken. " : "") + "Counts: " + ruleNames(r.rule_counts));
    } else {
      rules = checkRow("Rule checks", chip("notrun", "not run"), "they need a simulation that finished");
    }
    return '<ul class="ev-checks">' + lint + sim + rules + "</ul>";
  }
  function logText(r) {
    var parts = [];
    if (Array.isArray(r.lint) && r.lint.length) {
      parts.push("lint findings:\n" + r.lint.map(function (x) { return "  " + (typeof x === "string" ? x : JSON.stringify(x)); }).join("\n"));
    }
    if (typeof r.sim_error === "string" && r.sim_error) parts.push(r.sim_error);
    if (parts.length) return parts.join("\n\n");
    if (r.outcome === "REFUSED" || r.code === null || r.code === undefined) {
      return "No log. The model returned no code, so nothing was linted or simulated.";
    }
    return "No error recorded. Lint found nothing and the simulator ran to the end" +
      (isNum(r.n_events) ? " (" + r.n_events + " events recorded)." : ".");
  }

  /* ---------- the outcome grid and the detail panel ---------- */
  function cellHtml(r, i) {
    var label = modelLabel(r.model) + ", " + sessionTitle(r.session) + ": ";
    return '<button type="button" class="ev-cell" data-i="' + i + '" aria-pressed="false" aria-controls="ev-detail">' +
      '<span class="sr-only">' + esc(label) + "</span>" + outcomeChip(r.outcome) + "</button>";
  }
  function gridHtml(mt) {
    var rows = mt && Array.isArray(mt.rows) ? mt.rows : [];
    if (!rows.length) throw new Error("no rows");
    state.rows = rows;
    var models = [], sessions = [], at = {};
    rows.forEach(function (r, i) {
      if (models.indexOf(r.model) === -1) models.push(r.model);
      if (sessions.indexOf(r.session) === -1) sessions.push(r.session);
      at[r.model + "|" + r.session] = i;
    });
    var sub = [mt.protocol, mt.note].filter(function (s) { return typeof s === "string" && s; }).map(sentence).map(esc).join(" ");
    var h = '<div class="table-wrap"><table class="ev-table ev-grid"><caption>Outcome for each model and session' +
      (sub ? '<span class="ev-cap-sub">' + sub + "</span>" : "") + '</caption><thead><tr><th scope="col">Session</th>';
    models.forEach(function (m) { h += '<th scope="col">' + esc(modelLabel(m)) + "</th>"; });
    h += "</tr></thead><tbody>";
    sessions.forEach(function (s) {
      h += '<tr><th scope="row">' + esc(sessionTitle(s)) + '<span class="sub">' + esc(s) + "</span></th>";
      models.forEach(function (m) {
        var i = at[m + "|" + s];
        h += "<td>" + (i === undefined ? chip("notrun", "no attempt") : cellHtml(rows[i], i)) + "</td>";
      });
      h += "</tr>";
    });
    return h + "</tbody></table></div>";
  }

  function detailHtml(r) {
    var who = modelLabel(r.model), what = sessionTitle(r.session), url = codeUrl(r);
    var digest = digestChip(r.code_sha256, who + " " + what + " output");
    var match = "";
    if (digest && r.code_sha256_matches === true) match = ' <span class="ev-check-note">The stored program matches this digest.</span>';
    if (digest && r.code_sha256_matches === false) match = ' <span class="ev-check-note">The stored program does not match this digest.</span>';
    return '<div class="ev-detail-head"><h3 id="ev-detail-h" tabindex="-1">' + esc(who) + " " + MIDDOT + " " + esc(what) + "</h3>" + outcomeChip(r.outcome) +
        '<button type="button" class="ev-btn" data-close="1">Close details</button></div>' +
      '<p class="ev-reason"><b>What happened.</b> ' + esc(explain(r)) + "</p>" +
      '<p class="ev-srcline" id="ev-line-slot"></p>' +
      '<dl class="ev-facts">' +
        fact("Session", "<b>" + esc(what) + "</b> <code>" + esc(r.session) + "</code>") +
        fact("Model id", codeOrNot(r.model) + (typeof r.resp_model === "string" && r.resp_model && r.resp_model !== r.model
          ? " (response model: <code>" + esc(r.resp_model) + "</code>)" : "")) +
        fact("Started", esc(fmtStarted(r.started))) +
        fact("Latency", isNum(r.latency_s) ? esc(r.latency_s) + " s" : "not recorded") +
        fact("Input tokens", esc(fmtInt(r.input_tokens))) +
        fact("Output tokens", esc(fmtInt(r.output_tokens))) +
        fact("Stop reason", stopHtml(r.stop_reason)) +
        fact("Response id", codeOrNot(r.resp_id)) +
        fact("Output digest", digest ? digest + match : (r.code ? "not recorded" : "none: the model returned no code")) +
      "</dl>" +
      '<h4 class="ev-h4">Checks</h4>' + checksHtml(r) +
      '<details class="ev-log"><summary>Raw log</summary><pre>' + esc(logText(r)) + "</pre></details>" +
      (url ? '<p class="ev-prog"><a href="' + esc(url) + '">Open the recorded program (.py)</a></p>' : "");
  }

  /* For a failed attempt, fetch the recorded program and quote the line the simulator named, so the reason can be checked. */
  function loadSourceLine(r, token) {
    var line = simLine(r.sim_error), url = codeUrl(r), slot = byId("ev-line-slot");
    if (!line || !url || !slot || typeof fetch !== "function") return;
    fetch(url).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.text();
    }).then(function (text) {
      if (token !== state.token) return;
      var src = text.split(/\r?\n/)[line - 1];
      slot.innerHTML = typeof src === "string" ? "Line " + esc(line) + " of the recorded program reads: <code>" + esc(src.trim()) + "</code>" : "";
    }, function () {
      if (token === state.token) slot.textContent = "The recorded program could not be loaded to show that line.";
    });
  }

  function markSelected(i) {
    var cells = document.querySelectorAll(".ev-cell");
    for (var k = 0; k < cells.length; k++) {
      cells[k].setAttribute("aria-pressed", parseInt(cells[k].getAttribute("data-i"), 10) === i ? "true" : "false");
    }
  }
  function select(i, trigger, viaKeyboard) {
    var r = state.rows[i], panel = byId("ev-detail");
    if (!r || !panel) return;
    state.trigger = trigger || null;
    state.token += 1;
    markSelected(i);
    panel.innerHTML = detailHtml(r);
    panel.hidden = false;
    if (r.outcome === "FAIL") loadSourceLine(r, state.token);
    if (viaKeyboard) {
      var h = byId("ev-detail-h");
      if (h) h.focus();
    } else {
      announce("Details for " + modelLabel(r.model) + ", " + sessionTitle(r.session) + ", " + r.outcome + ", are shown below the grid.");
      if (panel.scrollIntoView) panel.scrollIntoView({ block: "nearest" });
    }
  }
  function closeDetail(returnFocus) {
    var panel = byId("ev-detail"), trig = state.trigger;
    state.trigger = null;
    state.token += 1;
    markSelected(-1);
    if (panel) { panel.hidden = true; panel.innerHTML = ""; }
    if (returnFocus && trig && typeof trig.focus === "function") trig.focus();
  }

  /* ---------- tallies ---------- */
  function countRows(rows, model) {
    var c = { PASS: 0, FAIL: 0, REFUSED: 0, n: 0 };
    rows.forEach(function (r) {
      if (r.model !== model) return;
      c.n += 1;
      if (r.outcome === "PASS" || r.outcome === "FAIL" || r.outcome === "REFUSED") c[r.outcome] += 1;
    });
    return c;
  }
  function tallyHtml(mt) {
    var tally = mt && mt.tally && typeof mt.tally === "object" ? mt.tally : null;
    if (!tally) throw new Error("no tally");
    var rows = Array.isArray(mt.rows) ? mt.rows : [];
    var items = "", off = [];
    Object.keys(tally).forEach(function (m) {
      var t = tally[m] || {};
      var parts = [num(t.PASS) + " PASS"];
      if (t.FAIL) parts.push(t.FAIL + " FAIL");
      if (t.REFUSED) parts.push(t.REFUSED + " REFUSED");
      items += "<li><b>" + esc(modelLabel(m)) + ":</b> " + esc(parts.join(", ") + " of " + num(t.n)) + ".</li>";
      var c = countRows(rows, m);
      if (rows.length && (c.PASS !== (t.PASS || 0) || c.FAIL !== (t.FAIL || 0) || c.REFUSED !== (t.REFUSED || 0) || c.n !== t.n)) {
        off.push(modelLabel(m));
      }
    });
    var fm = mt.free_mode_pass_at_1;
    if (fm && typeof fm === "object" && Object.keys(fm).length) {
      var got = Object.keys(fm).map(function (k) {
        var m = /^\s*(\d+)\s*\/\s*(\d+)\s*$/.exec(String(fm[k]));
        return { model: k, text: m ? m[1] + " of " + m[2] : String(fm[k]) };
      });
      var same = got.length > 1 && got.every(function (g) { return g.text === got[0].text; });
      var said = same ? "both models passed " + got[0].text
        : got.map(function (g) { return modelLabel(g.model) + " passed " + g.text; }).join(", ");
      items += "<li><b>Free mode</b> (one step at a time, no plan in the prompt): " + esc(said) +
        ' on the first attempt <span class="ev-key">(<code>free_mode_pass_at_1</code>)</span>.</li>';
    }
    if (off.length) {
      items += '<li class="ev-warn">The tally for ' + esc(off.join(" and ")) + " does not match the rows above. The figures shown are the tally as recorded.</li>";
    }
    return '<h3 class="ev-tally-head">Tallies</h3><ul class="ev-tally">' + items + "</ul>";
  }

  /* ---------- Adaption ---------- */
  function swatchLabel(cls, text) { return '<span><i class="ev-sw ' + cls + '" aria-hidden="true"></i>' + esc(text) + "</span>"; }
  function duel(n, cls, label) { return "<div><b>" + esc(num(n)) + "</b>" + swatchLabel(cls, label) + "</div>"; }
  function seg(cls, n) { return isNum(n) && n > 0 ? '<i class="' + cls + '" style="flex-grow:' + n + '"></i>' : ""; }

  function nineHtml(a) {
    if (!a || typeof a !== "object") throw new Error("no 9b record");
    return '<dl class="ev-mini">' +
      fact("Best win rate in the run record", '<b class="ev-num">' + esc(a.best_win_rate) + "</b>") +
      fact("Base model", codeOrNot(a.base_model)) + "</dl>";
  }
  function fourHtml(h) {
    if (!h || !h.counts) throw new Error("no held-out record");
    var c = h.counts, tb = h.tie_breakdown || {};
    var label = num(c.win) + " wins, " + num(c.loss) + " losses, " + num(c.tie) + " ties";
    var tied = has(tb, "neither-passes") && isNum(c.tie)
      ? " " + tb["neither-passes"] + " of the " + c.tie + " ties: neither arm passed the simulator." : "";
    return '<div class="ev-duel">' + duel(c.win, "ev-seg-a", "wins") + duel(c.loss, "ev-seg-l", "losses") + duel(c.tie, "ev-seg-t", "ties") + "</div>" +
      '<div class="ev-split" role="img" aria-label="' + esc(label) + '">' + seg("ev-seg-a", c.win) + seg("ev-seg-l", c.loss) + seg("ev-seg-t", c.tie) + "</div>" +
      '<p class="ev-line">' + esc(plural(h.n, "task", "tasks") + " in " + plural(h.families, "family", "families") + "." + tied) + "</p>" +
      '<p class="ev-line"><b>Verdict:</b> ' + chip("info", h.verdict || "not recorded") + "</p>";
  }
  function runsHtml(ad) {
    var rows = "";
    [["9b", "9B"], ["4b", "4B"]].forEach(function (p) {
      var r = ad && ad[p[0]];
      if (!r || typeof r !== "object") return;
      rows += '<tr><th scope="row">' + esc(p[1]) + ' run<span class="sub">' + esc(r.base_model) + "</span></th>" +
        "<td><code>" + esc(r.run_id) + "</code></td>" +
        "<td>" + esc(num(r.iterations) + " of " + num(r.max_iterations)) + "</td>" +
        "<td>" + (digestChip(r.checkpoint_sha256, p[1] + " checkpoint") || "not recorded") + "</td></tr>";
    });
    if (!rows) throw new Error("no runs");
    return '<div class="table-wrap"><table class="ev-table"><caption class="sr-only">Run facts for the 9B and 4B Adaption runs</caption>' +
      '<thead><tr><th scope="col">Model</th><th scope="col">Run id</th><th scope="col">Iterations completed</th>' +
      '<th scope="col">Checkpoint digest</th></tr></thead><tbody>' + rows + "</tbody></table></div>";
  }

  /* ---------- Boltz ---------- */
  var BANDS = [
    { key: "gt90", label: "above 90", cls: "ev-b1" },
    { key: "70_90", label: "70 to 90", cls: "ev-b2" },
    { key: "50_70", label: "50 to 70", cls: "ev-b3" },
    { key: "le50", label: "50 or below", cls: "ev-b4" }
  ];
  function clampPct(f) { return isNum(f) ? Math.max(0, Math.min(100, f * 100)) : null; }
  function proteinHtml(p) {
    p = p && typeof p === "object" ? p : {};
    var bands = p.bands && typeof p.bands === "object" ? p.bands : {};
    var mean = isNum(p.mean_plddt) ? Math.max(0, Math.min(100, p.mean_plddt)) : null;
    var segs = "", vals = "";
    BANDS.forEach(function (b) {
      var v = clampPct(bands[b.key]);
      if (v !== null && v > 0) segs += '<i class="' + b.cls + '" style="flex-grow:' + v.toFixed(2) + '"></i>';
      vals += '<li><i class="ev-sw ' + b.cls + '" aria-hidden="true"></i>' + esc(b.label) + ": <b>" +
        esc(v === null ? "not recorded" : (Math.round(v * 10) / 10).toFixed(1) + "%") + "</b></li>";
    });
    return '<li class="ev-prot">' +
      '<div class="ev-prot-id"><b class="ev-prot-name">' + esc(p.name) + '</b><span class="ev-prot-meta">' + esc(p.accession) + " " + MIDDOT + " " +
        esc(isNum(p.length) ? p.length + " aa" : "length not recorded") + "</span></div>" +
      '<div class="ev-prot-fig">' +
        '<div class="ev-bar-row"><span class="ev-bar-lab">Mean pLDDT</span>' +
          '<div class="ev-track" aria-hidden="true">' +
            (mean === null ? "" : '<i class="ev-fill" style="width:' + mean.toFixed(1) + '%"></i><i class="ev-tick" style="left:70%"></i>') + "</div>" +
          '<b class="ev-val">' + (mean === null ? "n/a" : esc(p.mean_plddt) + '<span class="sr-only"> out of 100</span>') + "</b></div>" +
        '<div class="ev-bar-row"><span class="ev-bar-lab">Residues by band</span><div class="ev-seg" aria-hidden="true">' + segs + "</div></div>" +
        '<ul class="ev-bandvals">' + vals + "</ul>" +
        '<p class="ev-epi">Predicted epitope residues: <b>' + esc(isNum(p.epitope_residues) ? p.epitope_residues : "not recorded") + "</b></p>" +
      "</div></li>";
  }
  function boltzModeHtml(b) {
    if (!b || typeof b !== "object") throw new Error("no boltz record");
    return '<p class="ev-mode"><b>Run mode:</b> ' + esc(b.engine) + " " + MIDDOT + " " + esc(b.mode) + "</p>";
  }
  function boltzHtml(b) {
    var list = b && Array.isArray(b.proteins) ? b.proteins : [];
    if (!list.length) throw new Error("no proteins");
    return '<ul class="ev-prots" aria-label="' + esc("Boltz-2 sample: " + list.length + " proteins") + '">' + list.map(function (p) { return proteinHtml(p); }).join("") + "</ul>";
  }

  /* ---------- copy ---------- */
  function legacyCopy(text) {
    var prior = document.activeElement, ok = false;
    try {
      var ta = document.createElement("textarea");
      ta.value = text;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.top = "-1000px";
      document.body.appendChild(ta);
      ta.select();
      ok = document.execCommand("copy");
      document.body.removeChild(ta);
    } catch (e) { ok = false; }
    if (prior && typeof prior.focus === "function") prior.focus();
    return ok;
  }
  function copyText(text, btn) {
    function done(ok) {
      announce(ok ? "Copied the full digest to the clipboard." : "Copy did not work. Select the short digest or open the program to check it by hand.");
      if (btn) {
        btn.textContent = ok ? "Copied" : "Copy failed";
        setTimeout(function () { btn.textContent = "Copy"; }, 1800);
      }
    }
    if (!text) { done(false); return; }
    if (navigator.clipboard && window.isSecureContext) {
      navigator.clipboard.writeText(text).then(function () { done(true); }, function () { done(legacyCopy(text)); });
    } else {
      done(legacyCopy(text));
    }
  }

  /* ---------- numbers inside static sentences: drawn from the data when it loads, the typed text stays if it does not ---------- */
  function bindFacts(d) {
    var rows = d && d.model_test && Array.isArray(d.model_test.rows) ? d.model_test.rows : [];
    var seen = {}, n = 0;
    rows.forEach(function (r) { if (!has(seen, r.session)) { seen[r.session] = true; n += 1; } });
    var facts = { mt_sessions: n || null };
    var els = document.querySelectorAll("[data-ev]");
    for (var i = 0; i < els.length; i++) {
      var k = els[i].getAttribute("data-ev");
      if (has(facts, k) && facts[k] !== null) els[i].textContent = String(facts[k]);
    }
  }

  /* ---------- drawing, errors, start-up ---------- */
  function fail(el, msg) {
    el.innerHTML = '<p class="ev-error">' + esc(msg) + "</p>";
    el.classList.add("is-failed");
    el.removeAttribute("aria-busy");
  }
  function draw(id, build) {
    var el = byId(id);
    if (!el) return;
    try {
      el.innerHTML = build();
      el.removeAttribute("aria-busy");
    } catch (e) {
      fail(el, PART_ERR);
    }
  }
  function renderAll(d) {
    d = d || {};
    draw("ev-grid-slot", function () { return gridHtml(d.model_test); });
    draw("ev-tally-slot", function () { return tallyHtml(d.model_test); });
    draw("ev-9b-slot", function () { return nineHtml(d.adaption && d.adaption["9b"]); });
    draw("ev-4b-slot", function () { return fourHtml(d.adaption && d.adaption.heldout_4b); });
    draw("ev-runs-slot", function () { return runsHtml(d.adaption); });
    draw("ev-boltz-mode", function () { return boltzModeHtml(d.boltz); });
    draw("ev-boltz-slot", function () { return boltzHtml(d.boltz); });
    try { bindFacts(d); } catch (e) { /* the typed fallback text stays */ }
  }
  function failAll() {
    SLOTS.forEach(function (id) {
      var el = byId(id);
      if (el) fail(el, LOAD_ERR);
    });
    announce("The recorded data could not be loaded.");
  }
  function wire() {
    document.addEventListener("click", function (e) {
      var t = e.target;
      if (!t || typeof t.closest !== "function") return;
      var cell = t.closest(".ev-cell");
      if (cell) { select(parseInt(cell.getAttribute("data-i"), 10), cell, e.detail === 0); return; }
      var copy = t.closest(".ev-copy");
      if (copy) { copyText(copy.getAttribute("data-copy") || "", copy); return; }
      if (t.closest("[data-close]")) closeDetail(true);
    });
    var panel = byId("ev-detail");
    if (panel) panel.addEventListener("keydown", function (e) { if (e.key === "Escape") closeDetail(true); });
  }
  function init() {
    if (!byId("ev-grid-slot")) return;
    wire();
    SLOTS.forEach(function (id) {
      var el = byId(id);
      if (!el) return;
      el.setAttribute("aria-busy", "true");
      el.innerHTML = '<p class="ev-loading">Loading recorded results ...</p>';
    });
    if (typeof fetch !== "function") { failAll(); return; }
    fetch(DATA_URL).then(function (res) {
      if (!res.ok) throw new Error("HTTP " + res.status);
      return res.json();
    }).then(function (d) { renderAll(d); }, function () { failAll(); });
  }

  /* Exports are for the Node self-check only; a browser has no `module`, so this line does nothing there. */
  if (typeof module !== "undefined" && module.exports) {
    module.exports = {
      esc: esc, modelLabel: modelLabel, sessionTitle: sessionTitle, explain: explain, explainError: explainError, fmtStarted: fmtStarted,
      gridHtml: gridHtml, detailHtml: detailHtml, tallyHtml: tallyHtml, nineHtml: nineHtml, fourHtml: fourHtml, runsHtml: runsHtml,
      boltzModeHtml: boltzModeHtml, boltzHtml: boltzHtml, proteinHtml: proteinHtml, state: state
    };
  }
  if (typeof document !== "undefined") {
    if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init);
    else init();
  }
})();
