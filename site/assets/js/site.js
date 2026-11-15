/* Site behaviour: mobile nav, tabs, the pipeline story, the side contents list. No dependencies. */
(function () {
  "use strict";

  /* Mobile navigation */
  var toggle = document.querySelector(".nav-toggle");
  var links = document.getElementById("nav-links");
  if (toggle && links) {
    toggle.addEventListener("click", function () {
      var open = links.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
    document.addEventListener("keydown", function (e) {
      if (e.key === "Escape" && links.classList.contains("is-open")) {
        links.classList.remove("is-open");
        toggle.setAttribute("aria-expanded", "false");
        toggle.focus();
      }
    });
  }

  /* A dark nav over a dark opening section, a light nav once the page turns light */
  var nav = document.querySelector(".site-nav.on-plate");
  var first = document.querySelector("main > section");
  if (nav && first && "IntersectionObserver" in window) {
    new IntersectionObserver(function (es) {
      nav.classList.toggle("on-plate", es[0].isIntersecting);
    }, { rootMargin: "-68px 0px 0px 0px", threshold: 0 }).observe(first);
  }

  /* Tabs (WAI-ARIA tabs pattern with arrow-key support) */
  document.querySelectorAll("[data-tabs]").forEach(function (root) {
    var tabs = Array.prototype.slice.call(root.querySelectorAll('[role="tab"]'));
    var panels = tabs.map(function (t) { return document.getElementById(t.getAttribute("aria-controls")); });
    function select(i, focus) {
      tabs.forEach(function (t, k) {
        var on = k === i;
        t.setAttribute("aria-selected", on ? "true" : "false");
        t.tabIndex = on ? 0 : -1;
        if (panels[k]) panels[k].hidden = !on;
      });
      if (focus) tabs[i].focus();
    }
    tabs.forEach(function (t, i) {
      t.addEventListener("click", function () { select(i, false); });
      t.addEventListener("keydown", function (e) {
        var n = tabs.length, k = i;
        if (e.key === "ArrowRight") k = (i + 1) % n;
        else if (e.key === "ArrowLeft") k = (i - 1 + n) % n;
        else if (e.key === "Home") k = 0;
        else if (e.key === "End") k = n - 1;
        else return;
        e.preventDefault();
        select(k, true);
      });
    });
    select(0, false);
  });

  /* Pipeline story: highlight the act in view */
  var acts = Array.prototype.slice.call(document.querySelectorAll(".act"));
  var chainActs = Array.prototype.slice.call(document.querySelectorAll(".chain-act"));
  if (acts.length && chainActs.length && "IntersectionObserver" in window) {
    var current = 0;
    function paint(i) {
      current = i;
      chainActs.forEach(function (c, k) {
        c.classList.toggle("is-on", k === i);
        c.classList.toggle("is-done", k < i);
      });
    }
    paint(0);
    var io = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) { if (e.isIntersecting) paint(acts.indexOf(e.target)); });
    }, { rootMargin: "-42% 0px -48% 0px", threshold: 0 });
    acts.forEach(function (a) { io.observe(a); });
    chainActs.forEach(function (c, k) {
      var a = c.querySelector("a");
      if (a) a.addEventListener("click", function () { paint(k); });
    });
  } else {
    chainActs.forEach(function (c) { c.classList.add("is-on"); });
  }

  /* Side contents list on the how-it-works page */
  var toc = document.querySelectorAll(".toc a");
  if (toc.length && "IntersectionObserver" in window) {
    var map = {};
    toc.forEach(function (a) { map[a.getAttribute("href").slice(1)] = a; });
    var secs = Object.keys(map).map(function (id) { return document.getElementById(id); }).filter(Boolean);
    var io2 = new IntersectionObserver(function (entries) {
      entries.forEach(function (e) {
        if (e.isIntersecting) {
          toc.forEach(function (a) { a.classList.remove("is-on"); });
          map[e.target.id].classList.add("is-on");
        }
      });
    }, { rootMargin: "-20% 0px -70% 0px", threshold: 0 });
    secs.forEach(function (s) { io2.observe(s); });
  }
})();
