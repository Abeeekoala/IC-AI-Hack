/* Pipeline page extras: charts reveal as their step scrolls into view, and the epitope plot answers the pointer.
   Reduced motion is respected in CSS (everything is shown at once). */
(function () {
  "use strict";

  document.body.classList.add("wk-anim");
  Array.prototype.forEach.call(document.querySelectorAll(".wk-step .wk-bar"), function (b, i) { b.style.transitionDelay = Math.min(i * 14, 700) + "ms"; });
  var steps = Array.prototype.slice.call(document.querySelectorAll(".wk-step"));
  if ("IntersectionObserver" in window) {
    var io = new IntersectionObserver(function (es) {
      es.forEach(function (e) { if (e.isIntersecting) { e.target.classList.add("is-in"); io.unobserve(e.target); } });
    }, { threshold: 0.12 });
    steps.forEach(function (st) { io.observe(st); });
  } else {
    steps.forEach(function (st) { st.classList.add("is-in"); });
  }

  Array.prototype.forEach.call(document.querySelectorAll(".wk-hover"), function (box) {
    var d;
    try { d = JSON.parse(box.getAttribute("data-hover")); } catch (e) { return; }
    var svg = box.querySelector("svg"), cur = box.querySelector(".wk-cursor"), tip = box.querySelector(".wk-tip");
    var VW = 860, L = 44, R = 10;
    function fmt(n) { return Number(n).toLocaleString("en-GB"); }
    box.addEventListener("mousemove", function (ev) {
      var r = svg.getBoundingClientRect(), vx = (ev.clientX - r.left) / r.width * VW;
      if (vx < L || vx > VW - R) { cur.hidden = true; tip.hidden = true; return; }
      var pos = Math.max(1, Math.min(d.len, Math.round((vx - L) / (VW - L - R) * d.len)));
      var pi = Math.min(d.prof.length - 1, Math.floor((pos - 1) / d.w)), bi = Math.min(d.bins.length - 1, Math.floor((pos - 1) / d.bs));
      var left = ev.clientX - box.getBoundingClientRect().left;
      cur.hidden = false; tip.hidden = false; cur.style.left = left + "px";
      tip.innerHTML = "residue " + fmt(pos) + " · B-cell score " + d.prof[pi].toFixed(2) + "<br>" + d.bins[bi] + " class I epitopes in residues " + fmt(bi * d.bs + 1) + " to " + fmt((bi + 1) * d.bs);
      var w = tip.offsetWidth;
      tip.style.left = Math.max(0, Math.min(box.clientWidth - w, left + 12)) + "px";
    });
    box.addEventListener("mouseleave", function () { cur.hidden = true; tip.hidden = true; });
  });
})();
