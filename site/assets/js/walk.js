/* Pipeline page: step chips follow the scroll, the robot-code picker swaps the animation and program, and two verify buttons
   recompute hashes in the browser (WSVerify from ws-verify.js). Only this site's own files are fetched. */
(function () {
  "use strict";

  /* ---- step chips follow the scroll ---- */
  var links = Array.prototype.slice.call(document.querySelectorAll(".wk-chips a"));
  var steps = links.map(function (a) { return document.getElementById(a.getAttribute("href").slice(1)); }).filter(Boolean);
  if (links.length && "IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) {
        if (!e.isIntersecting) return;
        links.forEach(function (a) { a.classList.toggle("is-on", a.getAttribute("href") === "#" + e.target.id); });
        var on = document.querySelector(".wk-chips a.is-on");
        if (on && on.scrollIntoView) { var bar = on.parentNode.parentNode; bar.scrollLeft = on.offsetLeft - 40; }
      });
    }, { rootMargin: "-30% 0px -60% 0px", threshold: 0 });
    steps.forEach(function (s) { io.observe(s); });
  }

  function subtle() { return window.crypto && window.crypto.subtle ? window.crypto.subtle : null; }
  function fetchOk(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r; }); }

  /* ---- robot code: choose a program ---- */
  var wrap = document.getElementById("wk-sessions");
  if (wrap) {
    var list = [];
    try { list = JSON.parse(wrap.getAttribute("data-sessions")); } catch (e) { list = []; }
    var frame = document.getElementById("wk-sim-frame"), title = document.getElementById("wk-sim-title");
    var pre = document.getElementById("wk-code"), out = document.getElementById("wk-code-out");
    var cur = null;
    function select(i) {
      var s = list[i]; if (!s) return;
      cur = s;
      Array.prototype.forEach.call(wrap.querySelectorAll(".wk-cell"), function (b) {
        var on = Number(b.getAttribute("data-i")) === i;
        b.classList.toggle("is-on", on); b.setAttribute("aria-pressed", on ? "true" : "false");
      });
      title.textContent = s[3] + " · " + s[2];
      frame.src = s[0];
      out.textContent = "";
      fetchOk(s[1]).then(function (r) { return r.text(); }).then(function (t) { pre.textContent = t.split(/\r?\n/).slice(0, 46).join("\n"); },
        function () { pre.textContent = "The program could not be loaded."; });
    }
    wrap.addEventListener("click", function (ev) {
      var b = ev.target.closest ? ev.target.closest(".wk-cell") : null;
      if (b) select(Number(b.getAttribute("data-i")));
    });
    cur = list[Number((wrap.querySelector(".wk-cell.is-on") || {}).getAttribute ? wrap.querySelector(".wk-cell.is-on").getAttribute("data-i") : 0)] || list[0];
    var vc = document.getElementById("wk-verify-code");
    if (vc) vc.addEventListener("click", function () {
      if (!cur) return;
      if (!subtle() || !window.WSVerify) { out.textContent = "Verification needs a secure context (https or localhost)."; return; }
      out.textContent = "Checking…";
      fetchOk(cur[1]).then(function (r) { return r.arrayBuffer(); }).then(function (b) { return window.WSVerify.sha256Hex(new Uint8Array(b), subtle()); }).then(function (hex) {
        out.textContent = hex === cur[4] ? "Match: the file hashes to the digest the run recorded (" + hex.slice(0, 12) + "…)." : "Mismatch: file " + hex.slice(0, 12) + "…, recorded " + String(cur[4]).slice(0, 12) + "….";
      }, function (e) { out.textContent = "Could not check: " + (e && e.message ? e.message : e); });
    });
  }

  /* ---- verify the whole run ---- */
  var vb = document.getElementById("wk-verify-run"), vo = document.getElementById("wk-verify-out");
  if (vb && vo) vb.addEventListener("click", function () {
    if (!subtle() || !window.WSVerify) { vo.className = "bad"; vo.textContent = "Verification needs a secure context (https or localhost)."; return; }
    var folder = vb.getAttribute("data-folder"), files = [];
    try { files = JSON.parse(vb.getAttribute("data-files")); } catch (e) { files = []; }
    vb.disabled = true; vo.className = ""; vo.textContent = "Fetching and hashing " + files.length + " files…";
    var texts = {}, errors = {}, listed = {}, order = [];
    Promise.all(files.map(function (f) {
      order.push(f[0]); listed[f[0]] = f[1];
      return fetchOk(folder + f[0]).then(function (r) { return r.text(); }).then(function (t) { texts[f[0]] = t; }, function (e) { errors[f[0]] = e.message; });
    })).then(function () {
      return window.WSVerify.verifyRun({ files: texts, recorded: listed, order: order, errors: errors, subtle: subtle() });
    }).then(function (rows) {
      var s = rows.summary;
      vo.className = s.allOk ? "ok" : "bad";
      vo.textContent = s.text + ". " + s.chainText + "." + (s.allOk ? " Nothing has changed since the run was recorded." : " Something differs from the recorded run.");
    }, function (e) { vo.className = "bad"; vo.textContent = "Could not verify: " + (e && e.message ? e.message : e); })
      .then(function () { vb.disabled = false; });
  });
})();
