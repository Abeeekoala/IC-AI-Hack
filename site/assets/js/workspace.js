/* workspace.js: core and screens 01 to 03 of the VaxForge workspace. The workspace replays recorded runs: it fetches only this
   site's own files (data/workspace.json, runs/...), calls no model and runs no code.

   PUBLIC INTERFACE  (window.WS; workspace-run.js registers screens 04 to 06 against it)
   WS.data      the parsed data/workspace.json (null until it has loaded; screens only run after it has)
   WS.state     {screen, run, cand, stage, call, session, mtSession, mode}. One object, changed in place.
                screen "request"|"candidates"|"structure"|"generate"|"tests"|"handoff"; run "demo" (Recorded offline run) | "real" (Real-tools run)
                cand index into run().candidates | null; stage stage id ("s0".."s12","s9plus","s9_5") | null
                call index into data.model_test.rows | null; session index into run().robot.sessions (demo only) | null
                mtSession a model_test session id | null; mode "model" | "template" (the screen 05 switch)
   WS.run()     the selected run object. WS.go(screenId) = setState({screen}); the focus moves to the new screen heading.
   WS.setState(patch)   merge into state, re-render the central area and the drawer, update the URL hash (#/screen?run=..&c=..&stage=..&call=..&session=..&mt=..&mode=..).
                A selection key (cand, stage, call, session, mtSession) set to a new non-null value also opens the drawer on small screens.
   WS.registerScreen(id, {title, render(container, ctx)})
                ctx = {data, state, run, WS}. On every state change the core empties the drawer, then calls render with a FRESH empty
                container (listeners added to it die with it; the core draws the h2 title). render fills the container and decides the drawer
                by calling WS.drawer(spec) while it renders. A throw inside render is caught and shown in the container. Until a screen is
                registered the core shows "This screen is loading." with role="status".
                Re-render helpers: give a control data-fk="unique-key" and the focus returns to it after a re-render; give a scroll box
                data-keep-scroll="name" and its scrollTop is kept. Keep filters and other local UI state in your own module variables.
   WS.drawer(spec)  spec = {title, badges:[text], facts:[[label, value]], missing:[text], digests:[{label, value}],
                links:[{label, href, external}], notes:[text], sections:[{title, html}]}   (every field optional)
                value of a fact and a note may be {html: "..."} (the caller escapes it with WS.esc); section html is trusted, escape it too.
                Order: title, badges, facts, sections, "Missing checks", "Digests" (short value + Copy), "Links", notes.
                Opens the drawer on small screens when called from an event handler or while a selection has just changed.
   WS.drawerEmpty()  show the empty state. Not needed inside render (the core already did it).
   HELPERS      esc(s)  chip(kind, text) kind: ok|fixture|notrun|incomplete|blocked|review|refused|failed|info  badge(text)  fmtInt(n)
                short(digest, n=12)  copyBtn(value, label)  stageState(stage, run) -> {kind, text}  fetchText(url)  fetchBytes(url) -> Uint8Array
                sha256Hex(bytes) -> Promise<hex>  verifyBytes(url, expectedHex) -> Promise<{ok, actual, expected, error?}> (never rejects)
                verifyRun(runKey) -> Promise<results> where results is the array of WSVerify.verifyRun plus results.summary
                ({total, matched, mismatched, unchecked, links, brokenLinks, allOk, text, chainText}); rejects with a plain Error when
                crypto.subtle is missing ("Verification needs a secure context (https or localhost)")
                announce(msg)  plain(simError) -> plain-English sentence (text, not HTML) for a simulator error string or kind name
   EXTRAS       scrub(text) hides <local path>; stageLabel(id) "s9plus" -> "S9+"; isBexsero(candidate); canVerify(); screens (the registry) */
(function () {
  "use strict";

  var SCREENS = [
    { id: "request", n: "01", title: "Request" }, { id: "candidates", n: "02", title: "Candidate review" },
    { id: "structure", n: "03", title: "Structure evidence" }, { id: "generate", n: "04", title: "Generate with models" },
    { id: "tests", n: "05", title: "Test results" }, { id: "handoff", n: "06", title: "Handoff" }
  ];
  var RUN_LABEL = { demo: "Recorded offline run", real: "Real-tools run" };
  var GATE = { human_homology: "Human homology", allergenicity: "Allergenicity", toxicity: "Toxicity", conservancy: "Conservancy" };
  var GATE_CHIP = { pass: ["ok", "pass"], flag: ["review", "flag"], reject: ["failed", "reject"] };
  var S4 = { kept: ["ok", "kept"], flagged: ["review", "flagged"], rejected: ["failed", "rejected"], unscreened: ["notrun", "unscreened"], not_run: ["notrun", "not carried"] };
  var S4_ORDER = ["kept", "flagged", "rejected", "unscreened", "not_run"];
  var TOOLS5 = [["class_i", "Class I T-cell epitopes"], ["class_ii", "Class II T-cell epitopes"], ["bcell_linear", "Linear B-cell regions"], ["conformational", "Conformational B-cell epitopes"]];
  var BEXSERO = ["factor h binding protein", "neisseria adhesin a", "neisserial heparin binding antigen"];
  var BEXSERO_NOTE = "Bexsero antigen. In the Real-tools run it survives S4 only because allergenicity and conservancy are set to flag instead of reject, after an earlier strict run rejected it. recall@3 reads the S3 ranking only and stays 0 of 3.";
  var CEILING = "A rank is a hypothesis from predictors this project has not validated. It says nothing about expression, immunogenicity or protection.";
  var REQUEST_TEXT = "Which candidates from this stored fixture deserve a lab's time? Show what was checked and what was not.";
  var ESC = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" };
  var CHIP = { ok: "pill-ok", fixture: "pill-fixture", notrun: "pill-notrun", incomplete: "pill-incomplete", blocked: "pill-blocked", review: "pill-review", refused: "pill-refused", failed: "ws-pill-failed", info: "pill-info" };

  var WS = window.WS = {
    data: null,
    state: { screen: "request", run: "demo", cand: null, stage: null, call: null, session: null, mtSession: null, mode: "model" },
    screens: {}
  };
  var screens = WS.screens, els = {}, ui = { q: "", s4: "all", sort: "rank", boltz: null };
  var lastHash = "", rendering = false, autoOpen = false, drawerFocused = false, returnKey = null, announceTimer = 0, baseTitle = "";
  var mqWide = window.matchMedia("(min-width: 1200px)"), mqMid = window.matchMedia("(min-width: 900px)");

  /* ---------- small helpers ---------- */
  function $(id) { return document.getElementById(id); }
  function el(tag, cls) { var e = document.createElement(tag); if (cls) e.className = cls; return e; }
  function assign(a, b) { for (var k in b) if (Object.prototype.hasOwnProperty.call(b, k)) a[k] = b[k]; return a; }
  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) { return ESC[c]; }); }
  function chip(kind, text) { return '<span class="pill ' + (CHIP[kind] || "pill-info") + '">' + esc(text) + "</span>"; }
  function badge(text) { return '<span class="ws-badge">' + esc(text) + "</span>"; }
  function fmtInt(n) { return n == null || n === "" || !isFinite(Number(n)) ? "–" : Number(n).toLocaleString("en-GB"); }
  function short(d, n) { var s = String(d == null ? "" : d).replace(/^sha256:/, ""); n = n || 12; return s.length > n ? s.slice(0, n) + "…" : (s || "–"); }
  function scrub(t) { return String(t == null ? "" : t).replace(/<local path>/g, "(path removed)").replace(/\b[A-Za-z]:\\[^\s"']*/g, "(path removed)"); }
  function stageLabel(id) { return id === "s9plus" ? "S9+" : id === "s9_5" ? "S9.5" : String(id).toUpperCase(); }
  function words(s) { var t = String(s || "unknown").replace(/([a-z])([A-Z])/g, "$1 $2").toLowerCase(); return t.charAt(0).toUpperCase() + t.slice(1); }
  function pct(v) { return +(Number(v) * 100).toFixed(1) + "%"; }
  function isBexsero(c) { return !!c && BEXSERO.indexOf(String(c.name || "").toLowerCase()) >= 0; }
  function findStage(r, id) { for (var i = 0; i < r.stages.length; i++) if (r.stages[i].id === id) return r.stages[i]; return null; }
  function run() { return WS.data && WS.data.runs ? WS.data.runs[WS.state.run] : null; }
  function lis(items) { return items.map(function (t) { return "<li>" + esc(t) + "</li>"; }).join(""); }
  function card(id, title, right, inner) {
    return '<section class="ws-card" aria-labelledby="' + id + '"><div class="ws-card-h"><h3 id="' + id + '">' + esc(title) + "</h3><span>" + (right || "") + "</span></div>" + inner + "</section>";
  }

  function stageState(stage, run) {
    var bs = run && run.biosafety ? run.biosafety.state : null;
    if (stage.id === "s12" && bs === "blocked") return { kind: "blocked", text: "blocked" };
    if (stage.id === "s12" && bs === "not_screened") return { kind: "notrun", text: "not run" };   /* the artifact says nothing was screened */
    if (stage.payload_status === "not_run") return { kind: "notrun", text: "not run" };
    if (stage.degraded && stage.degraded.length) return { kind: "incomplete", text: "incomplete" };
    return { kind: "ok", text: "ok" };
  }

  function plain(err) {
    var s = String(err == null ? "" : err), kind = (/^\s*([A-Za-z_][A-Za-z0-9_]*)\s*(?::|$)/.exec(s) || [])[1] || "";
    var line = (/\[line (\d+)\]/.exec(s) || [])[1], at = line ? " at line " + line : "";
    if (kind === "TipNotAttachedError") return "The simulator stopped" + at + ": the pipette had no tip attached.";
    if (kind === "ProtocolCommandFailedError") return "The simulator stopped" + at + ": a robot command could not run in the state the program had set up.";
    if (kind === "LabwareDefinitionIsNotLabwareError") return "The simulator stopped" + at + ": the program used something that is not a labware definition where a labware was expected.";
    if (kind === "TypeError") return "The simulator stopped" + at + ": the program called a function with missing or wrong arguments.";
    return "The simulator reported an error" + (kind ? " (" + kind + ")" : "") + at + ". The raw message is in the log.";
  }

  function announce(msg) {
    if (!els.live) return;
    clearTimeout(announceTimer);
    els.live.textContent = "";
    announceTimer = setTimeout(function () { els.live.textContent = msg; }, 80);
  }

  function copyBtn(value, label) {
    return '<button type="button" class="ws-copy" data-act="copy" data-copy="' + esc(value) + '" aria-label="Copy ' + esc(label || "value") + '">Copy</button>';
  }
  function legacyCopy(text) {
    var ta = el("textarea"), ok = false;
    ta.value = text; ta.setAttribute("readonly", ""); ta.style.position = "fixed"; ta.style.top = "-1000px";
    document.body.appendChild(ta); ta.select();
    try { ok = document.execCommand("copy"); } catch (e) { ok = false; }
    document.body.removeChild(ta);
    return ok;
  }
  function copyFrom(btn) {
    var v = btn.getAttribute("data-copy") || "";
    function done(ok) {
      btn.textContent = ok ? "Copied" : "Not copied";
      announce(ok ? "Copied to the clipboard." : "Copy failed. Select the text and copy it by hand.");
      setTimeout(function () { btn.textContent = "Copy"; }, 1600);
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(v).then(function () { done(true); }, function () { done(legacyCopy(v)); });
    else done(legacyCopy(v));
  }

  /* ---------- fetching and digest checks (this site's own files only) ---------- */
  function fetchOk(url) {
    return fetch(url).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status + " for " + url); return r; });
  }
  function fetchText(url) { return fetchOk(url).then(function (r) { return r.text(); }); }
  function fetchBytes(url) { return fetchOk(url).then(function (r) { return r.arrayBuffer(); }).then(function (b) { return new Uint8Array(b); }); }
  function subtle() { return window.crypto && window.crypto.subtle ? window.crypto.subtle : null; }
  function canVerify() { return !!subtle() && !!window.WSVerify; }
  function sha256Hex(bytes) { return window.WSVerify.sha256Hex(bytes, subtle()); }
  function verifyBytes(url, expectedHex) {
    var exp = String(expectedHex || "").replace(/^sha256:/, "").toLowerCase();
    return fetchBytes(url).then(sha256Hex).then(function (act) { return { ok: act === exp, actual: act, expected: exp }; })
      .catch(function (e) { return { ok: false, actual: null, expected: exp, error: e && e.message ? e.message : String(e) }; });
  }
  function verifyRun(runKey) {
    if (!canVerify()) return Promise.reject(new Error(window.WSVerify ? window.WSVerify.NO_SUBTLE_MESSAGE : "Verification needs a secure context (https or localhost)"));
    var r = WS.data.runs[runKey], files = {}, errors = {}, listed = {}, order = [];
    return Promise.all(r.stages.map(function (s) {
      order.push(s.file); listed[s.file] = s.digest;
      return fetchText(r.folder + s.file).then(function (t) { files[s.file] = t; }, function (e) { errors[s.file] = e.message; });
    })).then(function () {
      return window.WSVerify.verifyRun({ files: files, recorded: listed, order: order, errors: errors, subtle: subtle() });
    });
  }

  /* ---------- state and URL hash ---------- */
  function inRange(v, n) { return typeof v === "number" && v >= 0 && v < n && v % 1 === 0 ? v : null; }
  function toInt(v) { return /^\d+$/.test(String(v == null ? "" : v)) ? parseInt(v, 10) : null; }

  function parseHash() {
    var h = String(location.hash || "").replace(/^#\/?/, ""), q = h.indexOf("?"), p = {};
    (q < 0 ? "" : h.slice(q + 1)).split("&").forEach(function (kv) {
      if (!kv) return;
      var i = kv.indexOf("=");
      try { p[decodeURIComponent(i < 0 ? kv : kv.slice(0, i))] = i < 0 ? "" : decodeURIComponent(kv.slice(i + 1)); } catch (e) { /* ignore a bad escape */ }
    });
    return { screen: q < 0 ? h : h.slice(0, q), run: p.run, cand: toInt(p.c), stage: p.stage || null, call: toInt(p.call), session: toInt(p.session), mtSession: p.mt || null, mode: p.mode };
  }

  function hashOf(st) {
    var q = ["run=" + st.run];
    if (st.cand != null) q.push("c=" + st.cand);
    if (st.stage) q.push("stage=" + encodeURIComponent(st.stage));
    if (st.call != null) q.push("call=" + st.call);
    if (st.session != null) q.push("session=" + st.session);
    if (st.mtSession) q.push("mt=" + encodeURIComponent(st.mtSession));
    if (st.mode && st.mode !== "model") q.push("mode=" + st.mode);
    return "#/" + st.screen + "?" + q.join("&");
  }

  /* check a requested state against the data; anything unknown falls back to its default */
  function clean(st) {
    var d = WS.data, key = st.run === "real" ? "real" : "demo", r = d ? d.runs[key] : null, rows = d && d.model_test ? d.model_test.rows : [];
    var out = { screen: "request", run: key, cand: null, stage: null, call: null, session: null, mtSession: null, mode: st.mode === "template" ? "template" : "model" };
    SCREENS.forEach(function (s) { if (s.id === st.screen) out.screen = s.id; });
    if (r) {
      out.cand = inRange(st.cand, r.candidates.length);
      if (st.stage && findStage(r, st.stage)) out.stage = st.stage;
      out.session = inRange(st.session, r.robot && r.robot.sessions ? r.robot.sessions.length : 0);
    }
    out.call = inRange(st.call, rows.length);
    if (st.mtSession && rows.some(function (x) { return x.session === st.mtSession; })) out.mtSession = st.mtSession;
    return out;
  }

  function syncHash(push) {
    var h = hashOf(WS.state);
    if (h === lastHash) return;
    lastHash = h;
    try { history[push ? "pushState" : "replaceState"](null, "", h); } catch (e) { location.hash = h; }
  }

  function setState(patch) {
    var st = WS.state, prev = assign({}, st), next = assign(assign({}, st), patch || {});
    if (!WS.data) { assign(st, next); return; }
    if (next.run !== prev.run) next.session = null;   /* template sessions exist for the demo run only */
    next = clean(next);
    autoOpen = ["cand", "stage", "call", "session", "mtSession"].some(function (k) { return next[k] != null && next[k] !== prev[k]; });
    assign(st, next);
    syncHash(next.screen !== prev.screen || next.run !== prev.run);
    var meta = screenMeta(next.screen), say = next.screen !== prev.screen ? meta.n + " " + meta.title : next.run !== prev.run ? "Run changed to " + RUN_LABEL[next.run] + "." : "";
    render({ moved: next.screen !== prev.screen, say: say });
  }
  function go(id) { setState({ screen: id }); }
  function screenMeta(id) { for (var i = 0; i < SCREENS.length; i++) if (SCREENS[i].id === id) return SCREENS[i]; return SCREENS[0]; }

  function onHash() {
    if (!WS.data || location.hash === lastHash) return;
    lastHash = location.hash;
    var prev = WS.state.screen;
    autoOpen = false;
    assign(WS.state, clean(parseHash()));
    syncHash(false);
    render({ moved: WS.state.screen !== prev });
  }

  function registerScreen(id, def) {
    screens[id] = def || {};
    if (WS.data && WS.state.screen === id) render({});
  }

  /* ---------- the evidence drawer ---------- */
  function layout() { return mqWide.matches ? "wide" : mqMid.matches ? "mid" : "narrow"; }
  function setOpen(on) {
    els.dr.classList.toggle("is-open", on);
    els.toggle.setAttribute("aria-expanded", on ? "true" : "false");
    els.toggle.textContent = on ? "Hide evidence" : "Show evidence";
  }
  function openDrawer(focus) {
    var mode = layout();
    if (mode === "wide") return;
    setOpen(true);
    if (!focus) return;
    drawerFocused = true;
    var t = $("ws-dr-title");
    if (t) { t.focus({ preventScroll: mode === "mid" }); if (mode === "narrow" && t.scrollIntoView) t.scrollIntoView({ block: "start" }); }
  }
  function closeDrawer(restore) {
    setOpen(false);
    if (!restore) return;
    var t = returnKey ? els.central.querySelector('[data-fk="' + returnKey + '"]') : null;
    (t || els.toggle).focus();
  }
  function factVal(v) { return v && typeof v === "object" && v.html != null ? v.html : esc(v); }
  function drawerEmpty() { if (els.drContent) els.drContent.innerHTML = '<p class="ws-empty-note">Select a candidate, stage or result to see its evidence here.</p>'; }

  function drawer(spec) {
    spec = spec || {};
    var h = '<h3 class="ws-dr-title" id="ws-dr-title" tabindex="-1">' + esc(spec.title || "Evidence") + "</h3>";
    if (spec.badges && spec.badges.length) h += '<div class="ws-dr-badges">' + spec.badges.map(function (b) { return badge(b); }).join("") + "</div>";
    if (spec.facts && spec.facts.length) h += '<dl class="ws-kv">' + spec.facts.map(function (f) { return "<dt>" + esc(f[0]) + "</dt><dd>" + factVal(f[1]) + "</dd>"; }).join("") + "</dl>";
    (spec.sections || []).forEach(function (s) { h += '<section class="ws-dr-sec"><h4>' + esc(s.title) + "</h4>" + s.html + "</section>"; });
    if (spec.missing && spec.missing.length) h += '<section class="ws-dr-sec"><h4>Missing checks</h4><ul class="ws-missing">' + lis(spec.missing) + "</ul></section>";
    if (spec.digests && spec.digests.length) {
      h += '<section class="ws-dr-sec"><h4>Digests</h4><ul class="ws-digests">' + spec.digests.map(function (d) {
        return "<li><span>" + esc(d.label) + '</span><code title="' + esc(d.value) + '">' + esc(short(d.value)) + "</code>" + copyBtn(d.value, d.label) + "</li>";
      }).join("") + "</ul></section>";
    }
    if (spec.links && spec.links.length) {
      h += '<section class="ws-dr-sec"><h4>Links</h4><ul class="ws-links">' + spec.links.map(function (l) {
        return '<li><a href="' + esc(l.href) + '"' + (l.external ? ' target="_blank" rel="noopener"' : "") + ">" + esc(l.label) + (l.external ? '<span class="ws-sr"> (opens in a new tab)</span>' : "") + "</a></li>";
      }).join("") + "</ul></section>";
    }
    (spec.notes || []).forEach(function (n) { h += '<p class="ws-dr-note">' + factVal(n) + "</p>"; });
    els.drContent.innerHTML = h;
    els.drScroll.scrollTop = 0;
    if (autoOpen || !rendering) openDrawer(true);
  }

  /* ---------- rendering the shell and the current screen ---------- */
  function focusKey() { var a = document.activeElement; return a && a.getAttribute ? a.getAttribute("data-fk") : null; }
  function readScrolls() {
    var m = {};
    Array.prototype.forEach.call(els.central.querySelectorAll("[data-keep-scroll]"), function (e) { m[e.getAttribute("data-keep-scroll")] = e.scrollTop; });
    return m;
  }
  function restoreScrolls(m) {
    Object.keys(m).forEach(function (k) { var e = els.central.querySelector('[data-keep-scroll="' + k + '"]'); if (e) e.scrollTop = m[k]; });
  }

  function updateChrome(meta, r) {
    var st = WS.state;
    Array.prototype.forEach.call(els.radios, function (inp) { var on = inp.value === st.run; inp.checked = on; inp.parentNode.classList.toggle("is-on", on); });
    Array.prototype.forEach.call(els.links, function (a) {
      var id = a.getAttribute("data-screen"), cur = id === st.screen;
      a.setAttribute("href", hashOf(assign(assign({}, st), { screen: id })));
      if (cur) a.setAttribute("aria-current", "step"); else a.removeAttribute("aria-current");
      if (cur && els.steps.scrollWidth > els.steps.clientWidth) els.steps.scrollLeft = Math.max(0, a.parentNode.offsetLeft - 24);
    });
    els.crumbs.innerHTML = '<li>Run <span class="ws-mono">' + esc(r.run_id) + '</span></li><li aria-current="page">' + esc(meta.title) + "</li>";
    els.runbadge.innerHTML = badge(RUN_LABEL[st.run]);
  }

  function render(opts) {
    opts = opts || {};
    if (!WS.data || !els.central) return;
    var st = WS.state, r = run(), meta = screenMeta(st.screen), reg = screens[st.screen];
    updateChrome(meta, r);
    var fk = focusKey(), keep = readScrolls(), top = els.central.scrollTop;
    if (fk) returnKey = fk;
    els.central.innerHTML = "";
    var wrap = el("div", "ws-screen"), h2 = el("h2", "ws-h2"), body = el("div", "ws-body");
    wrap.setAttribute("data-screen", st.screen);
    h2.id = "ws-h2"; h2.tabIndex = -1; h2.textContent = meta.n + " " + meta.title;
    wrap.appendChild(h2); wrap.appendChild(body); els.central.appendChild(wrap);
    drawerEmpty();
    drawerFocused = false; rendering = true;
    try {
      if (reg && typeof reg.render === "function") reg.render(body, { data: WS.data, state: st, run: r, WS: WS });
      else body.innerHTML = '<p class="ws-loading" role="status">This screen is loading.</p>';
    } catch (err) {
      body.innerHTML = '<div class="ws-error" role="alert"><p><b>This screen could not be drawn.</b></p><p class="ws-mono">' + esc(err && err.message ? err.message : err) + "</p></div>";
      if (window.console && console.error) console.error(err);
    }
    rendering = false; autoOpen = false;
    document.title = meta.n + " " + meta.title + " · " + baseTitle;
    if (opts.moved) { els.central.scrollTop = 0; if (!opts.initial) h2.focus(); }
    else { els.central.scrollTop = top; restoreScrolls(keep); if (!drawerFocused && fk) { var t = els.central.querySelector('[data-fk="' + fk + '"]'); if (t) t.focus({ preventScroll: true }); } }
    if (opts.say) announce(opts.say);
  }

  /* ---------- evidence drawer content ---------- */
  function toolsText(t) {
    var k = Object.keys(t || {});
    if (!k.length) return "none recorded";
    return k.map(function (n) { var v = String(t[n]); return v === "not_run" ? n + ": not run" : n + " " + (v.length > 40 ? v.slice(0, 37) + "…" : v); }).join("; ");
  }

  function stageDrawer(s, r, key) {
    var stt = stageState(s, r), warn = (s.warnings || []).slice(0, 3).map(scrub), sections = [];
    var facts = [["Stage", stageLabel(s.id) + " " + s.name],
      ["Display state", { html: chip(stt.kind, stt.text) + (/^s[012]$/.test(s.id) ? " " + chip("fixture", "fixture") : "") }],
      ["Artifact status", s.status + " (the stage wrote its artifact; this is not a pass)"]];
    if (s.id === "s12" && r.biosafety) facts.push(["Biosafety state", r.biosafety.state]);
    facts.push(["Tools", toolsText(s.tools)], ["Warnings", String(s.n_warnings)], ["Written", s.created_at]);
    if (s.n_warnings > warn.length) warn.push("… and " + (s.n_warnings - warn.length) + " more in the artifact.");
    if (warn.length) sections.push({ title: "First warnings", html: '<ul class="ws-list">' + lis(warn) + "</ul>" });
    return { title: stageLabel(s.id) + " " + s.name, badges: [RUN_LABEL[key]], facts: facts, sections: sections,
      missing: (s.degraded || []).concat(s.reasons || []).map(scrub), digests: [{ label: "Artifact digest", value: s.digest }],
      links: [{ label: "Open artifact JSON", href: r.folder + s.file, external: true }] };
  }

  function missingFor(c, r) {
    var m = [], s4 = c.s4 || {}, tools = (r.s5 && r.s5.tools) || {}, tox = s4.gates && s4.gates.toxicity ? s4.gates.toxicity.status : null;
    if (s4.status === "not_run") m.push("S4 to S7: this protein was not carried past S3.");
    else if (s4.status === "rejected") m.push("S5 to S7: this protein was not carried after the S4 rejection.");
    else {
      if (r.s4.incomplete || s4.status === "unscreened") m.push("S4 gates not run: " + (r.s4.unavailable_gates || []).map(function (g) { return (GATE[g] || g).toLowerCase(); }).join(", ") + ".");
      TOOLS5.forEach(function (t) { var x = tools[t[0]]; if (x && x.status !== "ran") m.push(t[1] + ": not run" + (x.note ? " (" + scrub(x.note) + ")" : "") + "."); });
      m.push("Structure: no structure was folded, so the order is sequence-only.");
      if (tox !== "pass" && tox !== "flag" && tox !== "reject") m.push("Toxicity result: S4 recorded none for this protein, and S12 needs one.");
    }
    m.push("Provider screening: no screening record was supplied, so S12 cannot clear any sequence.", "Wet-lab assay: none exists for any protein here.");
    return m;
  }

  function nextAction(c) {
    var s = c.s4 && c.s4.status;
    if (s === "not_run") return "This protein was not carried past S3, so only its Vaxign-ML score and virulence-factor match exist. Widen the run only if it matters to the question.";
    if (s === "rejected") return "Read the rejecting gate above. S4 settings are choices, so a person should confirm the rejection before dropping this protein.";
    if (s === "flagged") return "A person should read each flagged gate above and decide whether the flag is acceptable for this antigen. A flag is a warning, not a rejection.";
    if (s === "kept") return "No S4 gate flagged it. Read the S5 counts and the missing checks, then decide whether it deserves a closer look.";
    return "S4 did not run for this protein, so safety and conservancy are unknown. Run S4 with its tools (the Real-tools run did) before relying on this rank.";
  }

  function candDrawer(c, r, key) {
    var s4 = c.s4 || {}, st = S4[s4.status] || ["notrun", String(s4.status)], s5 = c.s5, has5 = !!s5 && s5.class_i_epitopes != null, gates = s4.gates ? Object.keys(s4.gates) : [];
    var carried = r.candidates.filter(function (x) { return x.s7_rank != null; }).length;
    var proto = (r.protocols || []).some(function (p) { return p.candidate_id === c.protein_id; });
    var order = c.s7_rank != null ? "#" + c.s7_rank + " of " + carried + " · sequence-only: no structure term" : (s4.status === "rejected" ? "not carried: rejected at S4" : "not carried past S3");
    var facts = [["Protein", c.name + (c.gene ? " (" + c.gene + ")" : "")], ["Protein id", c.protein_id], ["Length", fmtInt(c.length) + " aa"],
      ["S3 rank", "#" + c.rank + " of " + r.candidates.length],
      ["Protegenicity", Number(c.score).toFixed(1) + " percentile (a Vaxign-ML percentile, not a probability)"],
      ["Score source", "Vaxign-ML, stock model trained on all of Protegen; holdout_enforced: " + String(r.leakage && r.leakage.holdout_enforced)],
      ["S2 localisation", words(c.localisation) + " (a stand-in from UniProt text, not PSORTb)"],
      ["Closest virulence factor", c.vfdb_identity != null ? (c.vfdb_hit || "match") + ", " + c.vfdb_identity + "% identity" : "no match recorded"],
      ["S4", { html: chip(st[0], st[1]) }], ["Final order (S7)", order],
      ["Draft protocol (S8)", proto ? "drafted: see the Handoff screen" : "not drafted (S8 covers the top 3 of the final order)"]];
    var g4 = gates.length
      ? '<table class="ws-mini"><caption class="ws-sr">S4 gates for this protein</caption><thead><tr><th scope="col">Gate</th><th scope="col">Status</th><th scope="col">Reason</th></tr></thead><tbody>' +
        gates.map(function (g) { var v = s4.gates[g], gs = GATE_CHIP[v.status] || ["notrun", String(v.status)]; return '<tr><th scope="row">' + esc(GATE[g] || g) + "</th><td>" + chip(gs[0], gs[1]) + "</td><td>" + esc(scrub(v.reason)) + "</td></tr>"; }).join("") + "</tbody></table>"
      : "<p>" + (s4.status === "not_run" ? "Not carried past S3: S4 did not see this protein." : "S4 gates did not run" + ((r.s4.unavailable_gates || []).length ? " (" + r.s4.unavailable_gates.map(function (g) { return (GATE[g] || g).toLowerCase(); }).join(", ") + ")." : ".")) + "</p>";
    var g5 = has5
      ? "<p>Class I epitopes: <b>" + fmtInt(s5.class_i_epitopes) + "</b>, of which " + fmtInt(s5.class_i_promiscuous) + " are promiscuous. Enrichment against a random expectation: " + esc(s5.class_i_enrichment) + ". B-cell regions: " + fmtInt(s5.bcell_regions) + ".</p>"
      : "<p>" + chip("notrun", "not run") + " No epitope prediction exists for this protein in this run.</p>";
    var digests = [{ label: "S3 artifact", value: (findStage(r, "s3") || {}).digest }], links = [];
    links.push(c.s7_rank != null ? { label: "Open in report", href: r.report + "#c" + c.s7_rank, external: true } : { label: "Open report (no entry for this protein)", href: r.report, external: true });
    links.push({ label: "Open S3 artifact JSON", href: r.folder + "s3_rank.json", external: true });
    if (gates.length) { links.push({ label: "Open S4 artifact JSON", href: r.folder + "s4_filter.json", external: true }); digests.push({ label: "S4 artifact", value: (findStage(r, "s4") || {}).digest }); }
    return { title: c.name, badges: [RUN_LABEL[key]], facts: facts,
      sections: [{ title: "S4 gates", html: g4 }, { title: "S5 epitopes", html: g5 }],
      missing: missingFor(c, r), digests: digests.filter(function (d) { return d.value; }), links: links,
      notes: [{ html: "<b>Next review action.</b> " + esc(nextAction(c)) }].concat(isBexsero(c) ? [BEXSERO_NOTE] : []) };
  }

  /* ---------- screen 01: Request ---------- */
  function runLines(r, key) {
    var s3 = findStage(r, "s3"), on = s3 && s3.created_at ? " on " + s3.created_at.slice(0, 10) : "";
    if (key === "real") return ["S0 to S2 are the same stored fixture, not a live fetch. S3 to S12 were run" + on + " with real BLAST+, AlgPred2, ToxinPred2, MHCflurry (in place of NetMHCpan) and BepiPred-3.0.",
      "Boltz, NetMHCpan, NetMHCIIpan and DiscoTope did not run, so the S7 order is sequence-only.", "S9 code was generated but not simulated, so it is UNVERIFIED. There are no animations."];
    return ["S0 to S2 are a stored fixture, not a live fetch. S3 to S12 were run offline" + on + ".",
      "None of the S4 to S7 tools were installed, so those stages record what did not run instead of a result. The S7 order is sequence-only.",
      "The Opentrons " + r.robot.opentrons_version + " simulator was present: " + r.robot.n_sessions + " template-built programs were simulated. No language model wrote them."];
  }

  function renderRequest(box, ctx) {
    var r = ctx.run, key = ctx.state.run, fx = r.fixture || {}, sel = ctx.state.stage;
    var strip = r.stages.map(function (s) {
      var stt = stageState(s, r), on = sel === s.id;
      return '<li><button type="button" class="ws-stage" data-act="stage" data-id="' + esc(s.id) + '" data-fk="stage-' + esc(s.id) + '" aria-pressed="' + on + '">' +
        '<span class="ws-stage-top"><span class="ws-stage-id">' + esc(stageLabel(s.id)) + "</span>" + chip(stt.kind, stt.text) + (/^s[012]$/.test(s.id) ? chip("fixture", "fixture") : "") + "</span>" +
        '<span class="ws-stage-name">' + esc(s.name) + "</span></button></li>";
    }).join("");
    box.innerHTML =
      '<p class="ws-lede">This workspace replays stored output from two runs of the pipeline. Nothing is computed in your browser and nothing is sent anywhere.</p>' +
      '<div class="ws-grid">' +
      card("ws-h-run", "Run", badge(RUN_LABEL[key]), '<p class="ws-strong">' + esc(RUN_LABEL[key]) + ' <span class="ws-mono">' + esc(r.run_id) + '</span></p><ul class="ws-list">' + lis(runLines(r, key)) + "</ul>" +
        '<p><a href="' + esc(r.report) + '" target="_blank" rel="noopener">Open the evidence report<span class="ws-sr"> (opens in a new tab)</span></a></p>') +
      card("ws-h-fx", "Fixture", chip("fixture", "fixture"), '<dl class="ws-kv"><dt>Intended research target</dt><dd><i>' + esc(fx.organism) + "</i> (species, taxid " + esc(fx.taxid) + "). Demonstration case: serogroup B, the Bexsero benchmark.</dd><dt>Sample organism in this run</dt><dd>" + esc(fx.sample_name || fx.organism) + (fx.sample_taxid ? " (taxid " + esc(fx.sample_taxid) + ")" : "") + "</dd><dt>Proteome</dt><dd>" + esc(fx.proteome) + ", release " + esc(fx.release) +
        "</dd><dt>Proteins</dt><dd><b>" + fmtInt(fx.n_sequences) + " of " + fmtInt(fx.declared_protein_count) + " proteins (fixture subset)</b></dd></dl>" +
        '<p class="ws-note">S0 resolves the species only and S1 takes that species’ reference proteome, so this run does not cover all strains or serogroups, or a complete proteome. S2 localisation is a stand-in from UniProt text, not PSORTb.</p>') +
      "</div>" +
      card("ws-h-req", "Request", '<span class="ws-fieldstate"><span class="ws-sr">Request field state: </span>' + chip("info", "ready") + "</span>",
        '<label class="ws-label" for="ws-req">Example request (read only)</label><textarea id="ws-req" class="ws-field" rows="3" readonly aria-readonly="true">' + esc(REQUEST_TEXT) + "</textarea>" +
        '<p class="ws-mute" style="margin-top:10px">Parsed as a bounded task: review the ranked candidates of this recorded run. No new computation.</p>' +
        '<div class="ws-actions"><button type="button" class="ws-btn ws-btn-primary" data-act="go" data-screen="candidates">Review candidates</button>' +
        '<button type="button" class="ws-btn" data-act="go" data-screen="generate">Prepare model request</button></div>') +
      card("ws-h-stages", "Stages", "", '<p class="ws-note" style="margin-bottom:10px">Select a stage to see its tools, what did not run, its first warnings and its digest in the evidence drawer. An <b>ok</b> chip means the stage wrote its artifact, not that its output is right.</p><ol class="ws-stages" aria-label="Pipeline stages">' + strip + "</ol>");
    var s = sel ? findStage(r, sel) : null;
    if (s) WS.drawer(stageDrawer(s, r, key));
  }

  /* ---------- screen 02: Candidate review ---------- */
  function s4chip(c) { var m = S4[c.s4 && c.s4.status] || ["notrun", String(c.s4 && c.s4.status)]; return chip(m[0], m[1]); }

  function candRow(c, i, sel) {
    var s5 = !!c.s5 && c.s5.class_i_epitopes != null, on = sel === i, score = Math.max(0, Math.min(100, Number(c.score)));
    var thr = c.pass_090 && c.pass_085 ? "Both" : c.pass_085 ? "0.85 only" : c.pass_090 ? "0.90 only" : "Neither";
    return '<tr class="ws-row' + (on ? " is-sel" : "") + '" data-i="' + i + '"' + (on ? ' aria-current="true"' : "") + '><td class="num">' + esc(c.rank) + "</td>" +
      '<th scope="row"><span class="ws-name">' + esc(c.name) + "</span>" + (isBexsero(c) ? ' <span class="ws-tag">Bexsero antigen</span>' : "") + '<span class="ws-id">' + esc(c.protein_id) + "</span></th>" +
      '<td class="num">' + fmtInt(c.length) + '</td><td><span class="ws-bar" aria-hidden="true"><i style="width:' + score + '%"></i></span><b>' + score.toFixed(1) + '</b> <span class="ws-unit">percentile</span></td>' +
      "<td>" + thr + "</td><td>" + s4chip(c) + '</td><td class="num">' + (s5 ? fmtInt(c.s5.class_i_epitopes) : '<span class="ws-mute">not run</span>') + "</td>" +
      "<td>" + (c.s7_rank != null ? "#" + esc(c.s7_rank) : '<span class="ws-mute">not carried</span>') + "</td>" +
      '<td><button type="button" class="ws-btn ws-btn-sm" data-act="cand" data-i="' + i + '" data-fk="cand-' + i + '" aria-pressed="' + on + '" aria-label="Review ' + esc(c.name) + '">Review</button></td></tr>';
  }

  function funnelCard(r) {
    var total = (r.s4.counts && r.s4.counts.n_input) || 1;
    var items = (r.s4.funnel || []).filter(function (g) { return g.gate !== "input"; }).map(function (g) {
      var seg = [["pass", g.n_pass], ["flag", g.n_flag], ["reject", g.n_reject], ["na", g.n_unavailable]].filter(function (x) { return x[1]; });
      var gs = r.s4.gate_settings && r.s4.gate_settings[g.gate], txt = g.n_pass + " pass, " + g.n_flag + " flag, " + g.n_reject + " reject. " + g.remaining_after_gate + " remain.";
      return "<li><span>" + esc(GATE[g.gate] || g.gate) + (gs && gs.on_fail ? ' <span class="ws-mute">(on fail: ' + esc(gs.on_fail) + ")</span>" : "") + '</span><span class="ws-stack" role="img" aria-label="' + esc((GATE[g.gate] || g.gate) + ": " + txt) + '">' +
        seg.map(function (x) { return '<i class="ws-s-' + x[0] + '" style="width:' + (100 * x[1] / total).toFixed(2) + '%"></i>'; }).join("") + '</span><span class="ws-note">' + esc(txt) + "</span></li>";
    }).join("");
    return card("ws-h-fn", "S4 funnel", badge("Real-tools run"), '<p class="ws-note" style="margin-bottom:10px">' + fmtInt(total) + ' proteins went into S4. Each gate looked at all of them; "remain" counts proteins still kept after that gate.</p><ul class="ws-funnel">' + items +
      '</ul><ul class="ws-legend"><li><i class="ws-s-pass"></i>pass</li><li><i class="ws-s-flag"></i>flag (kept, needs a look)</li><li><i class="ws-s-reject"></i>reject</li></ul>');
  }

  function coverageCard(r) {
    var p = r.s6.populations || [], keep = {}, s6 = findStage(r, "s6"), w = s6 && s6.warnings && s6.warnings[0] ? scrub(s6.warnings[0]) : "";
    p.slice(0, 5).concat(p.slice(-3)).forEach(function (x) { keep[x.population] = 1; });
    var shown = p.filter(function (x) { return keep[x.population] || x.population === "World"; });
    var items = shown.map(function (x) {
      var v = Number(x.class_i_coverage) * 100, name = x.population === "World" ? "<b>World</b>" : esc(x.population);
      return "<li><span>" + name + '</span><span class="ws-stack" role="img" aria-label="' + esc(x.population + ": " + v.toFixed(1) + " percent") + '"><i style="width:' + v.toFixed(1) + '%"></i></span><b>' + v.toFixed(1) + "%</b></li>";
    }).join("");
    return card("ws-h-cov", "S6 class I population coverage", badge("Real-tools run"), '<ul class="ws-cov">' + items + '</ul><p class="ws-note" style="margin-top:10px">Top 5 and bottom 3 of ' + p.length + " populations, plus World. Class I only: class II did not run.</p>" +
      '<p class="ws-note">Assumption: ' + esc(r.s6.independence) + ". Alleles used: " + fmtInt(r.s6.n_alleles) + ".</p>" + (w ? '<p class="ws-note">S6 warning: ' + esc(w) + "</p>" : ""));
  }

  function renderCandidates(box, ctx) {
    var r = ctx.run, key = ctx.state.run, cs = r.candidates, sel = ctx.state.cand, counts = {};
    cs.forEach(function (c) { counts[c.s4.status] = (counts[c.s4.status] || 0) + 1; });
    var sum = S4_ORDER.filter(function (k) { return counts[k]; }).map(function (k) { return chip(S4[k][0], S4[k][1] + " " + counts[k]); }).join("");
    var s4opts = [["all", "All (" + cs.length + ")"]].concat(S4_ORDER.map(function (k) { return [k, S4[k][1] + " (" + (counts[k] || 0) + ")"]; }));
    var sorts = [["rank", "Rank (S3)"], ["length", "Length, longest first"], ["score", "Protegenicity, highest first"]];
    function opt(o, cur) { return '<option value="' + esc(o[0]) + '"' + (o[0] === cur ? " selected" : "") + ">" + esc(o[1]) + "</option>"; }
    box.innerHTML =
      '<p class="ws-lede">The proteins of the stored fixture in S3 order. Select Review on a row to see its evidence in the drawer.</p>' +
      '<div class="ws-ceiling"><b>Honest ceiling.</b> ' + esc(CEILING) + "</div>" +
      '<div class="ws-controls" role="group" aria-label="Table controls"><label for="ws-q">Filter by name, gene or id<input id="ws-q" class="ws-field" type="search" autocomplete="off" value="' + esc(ui.q) + '"></label>' +
      '<label for="ws-s4">S4 status<select id="ws-s4" class="ws-field">' + s4opts.map(function (o) { return opt(o, ui.s4); }).join("") + "</select></label>" +
      '<label for="ws-sort">Sort by<select id="ws-sort" class="ws-field">' + sorts.map(function (o) { return opt(o, ui.sort); }).join("") + "</select></label></div>" +
      '<div class="ws-summary"><span class="ws-note">' + cs.length + " proteins, by S4 status:</span>" + sum + "</div>" +
      '<div><p class="ws-count" id="ws-count"></p><div class="ws-tablewrap" tabindex="0" role="region" aria-label="Candidate table; scrolls sideways on small screens" data-keep-scroll="cands">' +
      '<table class="ws-table"><caption class="ws-sr">Proteins of the stored fixture with their S3 rank, S4 status and S5 and S7 results</caption><thead><tr>' +
      '<th scope="col" class="num">Rank</th><th scope="col">Protein</th><th scope="col" class="num">Length (aa)</th><th scope="col">Protegenicity<span class="ws-th-sub">percentile</span></th><th scope="col">Passes 0.90 and 0.85</th><th scope="col">S4</th>' +
      '<th scope="col" class="num">Class I epitopes</th><th scope="col">Final order (S7)</th><th scope="col"><span class="ws-sr">Review</span></th></tr></thead><tbody id="ws-rows"></tbody></table></div></div>' +
      (key === "real" ? funnelCard(r) + coverageCard(r)
        : card("ws-h-nr", "S4 gates and S6 coverage", chip("notrun", "not run"), '<p class="ws-strong">S4 gates and S6 coverage: not run.</p><p class="ws-note">None of the S4 to S7 tools were installed for this run, so there is no funnel and no coverage to draw. Switch to the Real-tools run to see both.</p>'));
    var rows = box.querySelector("#ws-rows"), count = box.querySelector("#ws-count");
    function update() {
      var q = ui.q.trim().toLowerCase(), list = [];
      cs.forEach(function (c, i) {
        if (ui.s4 !== "all" && c.s4.status !== ui.s4) return;
        if (q && (c.name + " " + (c.gene || "") + " " + c.protein_id).toLowerCase().indexOf(q) < 0) return;
        list.push(i);
      });
      list.sort(function (a, b) { var A = cs[a], B = cs[b]; return ui.sort === "length" ? B.length - A.length : ui.sort === "score" ? B.score - A.score : A.rank - B.rank; });
      rows.innerHTML = list.map(function (i) { return candRow(cs[i], i, sel); }).join("") || '<tr><td colspan="9" class="ws-mute">No proteins match these controls.</td></tr>';
      count.textContent = "Showing " + list.length + " of " + cs.length + " proteins";
    }
    box.addEventListener("input", function (e) { if (e.target.id === "ws-q") { ui.q = e.target.value; update(); announce(count.textContent); } });
    box.addEventListener("change", function (e) {
      if (e.target.id === "ws-s4") ui.s4 = e.target.value; else if (e.target.id === "ws-sort") ui.sort = e.target.value; else return;
      update(); announce(count.textContent);
    });
    update();
    if (sel != null && cs[sel]) WS.drawer(candDrawer(cs[sel], r, key));
  }

  /* ---------- screen 03: Structure evidence ---------- */
  var BANDS = [["gt90", "Very high (>90)", "ws-b1"], ["70_90", "Confident (70–90)", "ws-b2"], ["50_70", "Low (50–70)", "ws-b3"], ["le50", "Very low (≤50)", "ws-b4"]];

  function boltzAbout(b) {
    try { var a = typeof b.about === "string" ? JSON.parse(b.about) : b.about; return a && typeof a === "object" ? a : {}; } catch (e) { return {}; }
  }
  function bandText(p) { return BANDS.map(function (x) { return x[1] + ": " + pct((p.bands || {})[x[0]] || 0); }).join("; "); }

  function boltzDrawer(p, b, about) {
    var miss = ["Structure file: none is stored in the repository, so no molecule can be shown.", "Per-residue pLDDT: not stored. Only the mean and the band shares are.",
      "Validator agreement: not available" + (about.validators ? " (" + about.validators + ")" : ".")];
    return { title: p.name, badges: ["Separate Boltz sample"],
      facts: [["Accession", p.accession], ["Length", fmtInt(p.length) + " aa"], ["Mean pLDDT", p.mean_plddt + " of 100"], ["Confidence bands", bandText(p)],
        ["Epitope residues predicted", fmtInt(p.epitope_residues)], ["Run mode", b.engine + " · CPU · no MSA · fast smoke settings"]],
      missing: miss, notes: ["A different protein from the 40-protein fixture. Confidence in a structural model says nothing about immunogenicity or protection."] };
  }

  function renderStructure(box, ctx) {
    var b = ctx.data.boltz || {}, r = ctx.run, key = ctx.state.run, ps = b.proteins || [], about = boltzAbout(b);
    var low = ps.length > 0 && ps.every(function (p) { return Number(p.mean_plddt) < 70; });
    var rows = ps.map(function (p, i) {
      var on = ui.boltz === i, plddt = Math.max(0, Math.min(100, Number(p.mean_plddt)));
      var seg = BANDS.filter(function (x) { return (p.bands || {})[x[0]]; }).map(function (x) { return '<i class="' + x[2] + '" style="width:' + (p.bands[x[0]] * 100).toFixed(2) + '%"></i>'; }).join("");
      var leg = BANDS.map(function (x) { return '<li><i class="' + x[2] + '"></i>' + esc(x[1]) + ": " + pct((p.bands || {})[x[0]] || 0) + "</li>"; }).join("");
      return "<li><div><h3>" + esc(p.name) + '</h3><p class="ws-id">' + esc(p.accession) + " · " + fmtInt(p.length) + ' aa</p><p style="margin-top:8px">epitope residues predicted: <b>' + fmtInt(p.epitope_residues) + "</b></p>" +
        '<p style="margin-top:10px"><button type="button" class="ws-btn ws-btn-sm" data-act="boltz" data-i="' + i + '" data-fk="boltz-' + i + '" aria-pressed="' + on + '" aria-label="Show evidence for ' + esc(p.name) + '">Show evidence</button></p></div>' +
        '<div><div class="ws-metric"><div class="ws-metric-h"><span>Mean pLDDT</span><b>' + esc(p.mean_plddt) + ' of 100</b></div><div class="ws-stack ws-plddt" role="img" aria-label="Mean pLDDT ' + esc(p.mean_plddt) + ' out of 100"><i class="ws-s-pass" style="width:' + plddt + '%"></i></div></div>' +
        '<div class="ws-metric"><div class="ws-metric-h"><span>Confidence bands, share of residues</span></div><div class="ws-seg" role="img" aria-label="' + esc(bandText(p)) + '">' + seg + '</div><ul class="ws-legend">' + leg + "</ul></div></div></li>";
    }).join("");
    var deg = (r.s7 && r.s7.degraded) || [];
    box.innerHTML =
      '<p class="ws-lede">Structure confidence from a separate Boltz sample. It is not part of either recorded run.</p>' +
      card("ws-h-bz", "Boltz sample", badge("Separate Boltz sample") + (low ? chip("review", "Low confidence: weak structural evidence").replace('class="pill ', 'class="pill ws-pill-wrap ') : ""),
        '<p class="ws-strong">' + esc(b.engine) + " · CPU · no MSA · fast smoke settings</p>" +
        '<div class="ws-empty" style="margin-top:12px">No structure file is stored in the repository, so no molecule is shown.</div>') +
      '<ul class="ws-prot" aria-label="Three proteins of the Boltz sample">' + rows + "</ul>" +
      '<p class="ws-note">Different proteins from the 40-protein fixture; no candidate on the review screen has a Boltz structure in either run (s7.degraded).</p>' +
      card("ws-h-s7", "Structure in the " + RUN_LABEL[key], badge(RUN_LABEL[key]) + chip("notrun", "not run"),
        (deg.length ? '<ul class="ws-list">' + lis(deg.map(scrub)) + "</ul>" : "<p>No degraded note is recorded.</p>") +
        '<p class="ws-note" style="margin-top:10px">Final order basis: ' + esc((r.s7 && r.s7.basis) || "not recorded") + ". No structure was folded in either run, so the order is sequence-only.</p>") +
      card("ws-h-ab", "About this sample", "", '<dl class="ws-kv"><dt>Inputs</dt><dd>' + esc(about.inputs || "not recorded") + "</dd><dt>Settings</dt><dd>Fast smoke settings, not the defaults, so pLDDT may be lower than a default run would give.</dd>" +
        "<dt>Validators</dt><dd>" + esc(about.validators || "not recorded") + "</dd></dl>");
    if (ui.boltz != null && ps[ui.boltz]) WS.drawer(boltzDrawer(ps[ui.boltz], b, about));
  }

  /* ---------- events and start-up ---------- */
  function onClick(e) {
    var t = e.target && e.target.closest ? e.target.closest("[data-act]") : null, st = WS.state;
    if (!t) {
      var tr = e.target && e.target.closest ? e.target.closest("tr[data-i]") : null;
      if (tr && !e.target.closest("a, button, input, select, textarea, label")) setState({ cand: parseInt(tr.getAttribute("data-i"), 10) });
      return;
    }
    var act = t.getAttribute("data-act");
    if (act === "go") {
      if (e.ctrlKey || e.metaKey || e.shiftKey || e.altKey || e.button > 0) return;
      e.preventDefault();
      go(t.getAttribute("data-screen"));
    } else if (act === "cand") { var i = parseInt(t.getAttribute("data-i"), 10); setState({ cand: st.cand === i ? null : i }); }
    else if (act === "stage") { var id = t.getAttribute("data-id"); setState({ stage: st.stage === id ? null : id }); }
    else if (act === "boltz") { var k = parseInt(t.getAttribute("data-i"), 10); ui.boltz = ui.boltz === k ? null : k; autoOpen = ui.boltz != null; render({}); }
    else if (act === "copy") copyFrom(t);
    else if (act === "drawer-toggle") { if (els.dr.classList.contains("is-open")) closeDrawer(true); else { returnKey = null; openDrawer(true); } }
    else if (act === "drawer-close") closeDrawer(true);
  }

  function init() {
    els.ws = $("ws");
    if (!els.ws) return;   /* this script was loaded on another page */
    els.central = $("ws-central"); els.dr = $("ws-drawer"); els.drScroll = $("ws-dr-scroll"); els.drContent = $("ws-dr-content"); els.toggle = $("ws-drawer-toggle");
    els.crumbs = $("ws-crumbs"); els.runbadge = $("ws-runbadge"); els.live = $("ws-live"); els.steps = $("ws-steps");
    els.radios = els.ws.querySelectorAll('input[name="ws-run"]'); els.links = els.ws.querySelectorAll(".ws-step-link");
    baseTitle = document.title;
    var first = parseHash();
    WS.state.run = first.run === "real" ? "real" : "demo";
    SCREENS.forEach(function (s) { if (s.id === first.screen) WS.state.screen = s.id; });
    lastHash = location.hash;
    els.ws.addEventListener("click", onClick);
    els.ws.addEventListener("change", function (e) { if (e.target && e.target.name === "ws-run") setState({ run: e.target.value }); });
    document.addEventListener("keydown", function (e) { if (e.key === "Escape" && els.dr.classList.contains("is-open")) { e.preventDefault(); closeDrawer(true); } });
    window.addEventListener("hashchange", onHash);
    window.addEventListener("popstate", onHash);
    [mqWide, mqMid].forEach(function (mq) {
      var on = function () { if (layout() === "wide") setOpen(false); };
      if (mq.addEventListener) mq.addEventListener("change", on); else if (mq.addListener) mq.addListener(on);
    });
    els.central.innerHTML = '<p class="ws-loading" role="status">Loading recorded run ...</p>';
    fetch("data/workspace.json").then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); }).then(function (data) {
      WS.data = data;
      assign(WS.state, clean(parseHash()));
      els.central.setAttribute("aria-busy", "false");
      syncHash(false);
      render({ moved: true, initial: true });
    }, function (err) {
      els.central.setAttribute("aria-busy", "false");
      els.central.innerHTML = '<div class="ws-error" role="alert"><p><b>The recorded run could not be loaded.</b></p><p>The workspace reads this site’s own file data/workspace.json (' + esc(err && err.message ? err.message : err) +
        "). If you opened the page straight from a folder, serve the site folder over http instead.</p></div>";
    });
  }

  assign(WS, {
    run: run, setState: setState, go: go, registerScreen: registerScreen, drawer: drawer, drawerEmpty: drawerEmpty, esc: esc, chip: chip, badge: badge,
    fmtInt: fmtInt, short: short, copyBtn: copyBtn, stageState: stageState, fetchText: fetchText, fetchBytes: fetchBytes, sha256Hex: sha256Hex,
    verifyRun: verifyRun, verifyBytes: verifyBytes, announce: announce, plain: plain, scrub: scrub, stageLabel: stageLabel, isBexsero: isBexsero, canVerify: canVerify
  });
  registerScreen("request", { title: "Request", render: renderRequest });
  registerScreen("candidates", { title: "Candidate review", render: renderCandidates });
  registerScreen("structure", { title: "Structure evidence", render: renderStructure });
  init();
})();
