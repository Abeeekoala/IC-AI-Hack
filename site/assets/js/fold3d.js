/* fold3d: a small three.js (r128) protein-chain viewer with two honest modes.

   concept  A generated chain that unfolds and folds into a compact globule, coloured N to C terminus. It shows what a structure
            predictor does. The coordinates are generated, NOT predicted: the page labels it "Concept animation".
   real     If a Boltz-2 PDB file exists at data-real (for example runs/boltz/Q7DDG6.pdb), it is loaded and drawn as a C-alpha tube
            coloured by pLDDT (stored in the B-factor column), with the DiscoTope epitope residues (data-segs) marked in amber.
            If the file is missing the box stays in concept mode and says that no structure file is stored.

   Markup:  <div class="f3-box" data-fold3d data-n="65" data-seed="7" data-real="runs/boltz/X.pdb" data-segs="[[2,3],[11,11]]"></div>
   Written from scratch; needs window.THREE. Pauses off-screen, honours prefers-reduced-motion, drag to rotate. */
(function () {
  "use strict";
  if (!window.THREE) return;
  var THREE = window.THREE;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

  function rng(seed) { var s = seed >>> 0 || 1; return function () { s = (s * 1664525 + 1013904223) >>> 0; return s / 4294967296; }; }
  function ease(t) { t = Math.max(0, Math.min(1, t)); return t * t * (3 - 2 * t); }

  /* a compact chain of helices and loops, deterministic for a given seed */
  function foldedChain(n, seed) {
    var r = rng(seed), pts = [], c = new THREE.Vector3(0, 0, 0), axis = new THREE.Vector3(r() - 0.5, r() - 0.5, r() - 0.5).normalize();
    var rmax = 1.9 + Math.cbrt(n) * 0.55;
    while (pts.length < n) {
      var u = new THREE.Vector3().crossVectors(axis, new THREE.Vector3(0.3, 1, 0.2)).normalize(), v = new THREE.Vector3().crossVectors(axis, u).normalize();
      var h = 6 + Math.floor(r() * 6), i;
      for (i = 0; i < h && pts.length < n; i++) {
        var a = i * 1.745;
        pts.push(c.clone().addScaledVector(axis, i * 0.2).addScaledVector(u, Math.cos(a) * 0.42).addScaledVector(v, Math.sin(a) * 0.42));
      }
      c = c.clone().addScaledVector(axis, h * 0.2);
      var turn = 2 + Math.floor(r() * 3), next = new THREE.Vector3(r() - 0.5, r() - 0.5, r() - 0.5).normalize();
      if (c.length() > rmax * 0.55) next.addScaledVector(c.clone().normalize(), -1.4).normalize();
      for (i = 0; i < turn && pts.length < n; i++) {
        var k = (i + 1) / (turn + 1);
        c = c.clone().addScaledVector(axis.clone().lerp(next, k).normalize(), 0.24);
        pts.push(c.clone());
      }
      axis = next;
    }
    var mean = new THREE.Vector3(); pts.forEach(function (p) { mean.add(p); }); mean.multiplyScalar(1 / pts.length);
    return pts.map(function (p) { return p.sub(mean); });
  }
  function extendedChain(n) {
    var pts = [], i;
    for (i = 0; i < n; i++) pts.push(new THREE.Vector3((i - n / 2) * 0.17, Math.sin(i * 0.9) * 0.06, Math.cos(i * 0.9) * 0.06));
    return pts;
  }

  function rainbow(t) { var c = new THREE.Color(); c.setHSL(0.62 - 0.62 * t, 0.62, 0.55); return c; }       /* N terminus blue to C terminus red */
  function plddtColor(b) { return new THREE.Color(b > 90 ? 0x2f6fb0 : b > 70 ? 0x7fb8e0 : b > 50 ? 0xffd966 : 0xf4a259); }

  function parsePdb(text) {
    var ca = [], lines = text.split(/\r?\n/), i, l;
    for (i = 0; i < lines.length; i++) {
      l = lines[i];
      if (l.slice(0, 4) !== "ATOM" || l.slice(12, 16).trim() !== "CA") continue;
      ca.push({ res: parseInt(l.slice(22, 26), 10), x: parseFloat(l.slice(30, 38)), y: parseFloat(l.slice(38, 46)), z: parseFloat(l.slice(46, 54)), b: parseFloat(l.slice(60, 66)) });
    }
    return ca;
  }

  function Fold(el) {
    var self = this;
    this.el = el;
    this.n = parseInt(el.getAttribute("data-n"), 10) || 65;
    this.seed = parseInt(el.getAttribute("data-seed"), 10) || 7;
    this.segs = []; try { this.segs = JSON.parse(el.getAttribute("data-segs") || "[]"); } catch (e) { /* none */ }
    this.mode = "concept"; this.real = null; this.dragging = false; this.rotY = 0.6; this.rotX = 0.2; this.vel = reduce ? 0 : 0.004;
    this.t0 = performance.now(); this.visible = true; this.paused = reduce; this.last = 0;

    try {
      this.renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: "low-power" });
      if (!this.renderer.getContext()) throw new Error("no context");
    } catch (e) { el.classList.add("no-webgl"); return; }
    this.renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.75));
    el.appendChild(this.renderer.domElement);
    this.scene = new THREE.Scene();
    this.camera = new THREE.PerspectiveCamera(36, 1, 0.1, 100);
    this.camera.position.set(0, 0, 7);
    this.scene.add(new THREE.AmbientLight(0xffffff, 0.75));
    var dl = new THREE.DirectionalLight(0xffffff, 0.85); dl.position.set(3, 4, 5); this.scene.add(dl);
    this.group = new THREE.Group(); this.scene.add(this.group);
    this.fold = foldedChain(this.n, this.seed); this.ext = extendedChain(this.n);
    this.mat = new THREE.MeshLambertMaterial({ vertexColors: true });
    this.mesh = null;

    this.resize();
    if ("ResizeObserver" in window) new ResizeObserver(function () { self.resize(); }).observe(el);
    if ("IntersectionObserver" in window) new IntersectionObserver(function (es) { self.visible = es[0].isIntersecting; if (self.visible) self.loop(); }, { threshold: 0 }).observe(el);
    document.addEventListener("visibilitychange", function () { if (!document.hidden) self.loop(); });

    el.addEventListener("pointerdown", function (e) { self.dragging = true; self.lx = e.clientX; self.ly = e.clientY; self.vel = 0; try { el.setPointerCapture(e.pointerId); } catch (_) {} });
    el.addEventListener("pointermove", function (e) {
      if (!self.dragging) return;
      self.rotY += (e.clientX - self.lx) * 0.01; self.rotX = Math.max(-1.1, Math.min(1.1, self.rotX + (e.clientY - self.ly) * 0.008));
      self.lx = e.clientX; self.ly = e.clientY; self.loop();
    });
    var up = function () { self.dragging = false; if (!reduce) self.vel = 0.004; };
    el.addEventListener("pointerup", up); el.addEventListener("pointercancel", up);
    var btn = el.parentNode.querySelector("[data-fold-replay]");
    if (btn) btn.addEventListener("click", function () { self.t0 = performance.now(); self.paused = false; self.loop(); });
    var pb = el.parentNode.querySelector("[data-fold-pause]");
    if (pb) pb.addEventListener("click", function () { self.paused = !self.paused; pb.setAttribute("aria-pressed", self.paused ? "true" : "false"); pb.textContent = self.paused ? "Play" : "Pause"; self.loop(); });

    var url = el.getAttribute("data-real");
    if (url) fetch(url).then(function (r) { if (!r.ok) throw new Error("none"); return r.text(); }).then(function (t) {
      var ca = parsePdb(t);
      if (ca.length < 5) throw new Error("no atoms");
      self.useReal(ca);
    }).catch(function () { self.setMode("concept"); });
    else this.setMode("concept");
    this.loop();
  }

  Fold.prototype.setMode = function (mode) {
    this.mode = mode;
    this.el.setAttribute("data-mode", mode);
    var lab = this.el.parentNode.querySelector("[data-fold-label]");
    if (lab) lab.textContent = mode === "real" ? "Boltz-2 output (CPU, no MSA), coloured by pLDDT" : "Concept animation: generated coordinates, not a Boltz-2 prediction";
  };

  Fold.prototype.useReal = function (ca) {
    var c = new THREE.Vector3(); ca.forEach(function (a) { c.add(new THREE.Vector3(a.x, a.y, a.z)); }); c.multiplyScalar(1 / ca.length);
    var s = 0.17;
    this.real = ca.map(function (a) { return { p: new THREE.Vector3((a.x - c.x) * s, (a.y - c.y) * s, (a.z - c.z) * s), res: a.res, b: a.b }; });
    this.setMode("real");
    var max = 0; this.real.forEach(function (a) { max = Math.max(max, a.p.length()); });
    this.camera.position.z = Math.max(6, max * 3.4);
    this.camera.updateProjectionMatrix();
    this.buildReal();
    this.paused = false; this.loop();
  };

  Fold.prototype.setTube = function (points, colorAt) {
    var curve = new THREE.CatmullRomCurve3(points), seg = Math.max(60, points.length * 3), rad = 6;
    var geo = new THREE.TubeGeometry(curve, seg, 0.085, rad, false);
    var pos = geo.attributes.position, col = new Float32Array(pos.count * 3), i, row = rad + 1;
    for (i = 0; i < pos.count; i++) { var c = colorAt(Math.floor(i / row) / seg); col[i * 3] = c.r; col[i * 3 + 1] = c.g; col[i * 3 + 2] = c.b; }
    geo.setAttribute("color", new THREE.BufferAttribute(col, 3));
    if (this.mesh) { this.group.remove(this.mesh); this.mesh.geometry.dispose(); }
    this.mesh = new THREE.Mesh(geo, this.mat); this.group.add(this.mesh);
  };

  Fold.prototype.buildReal = function () {
    var self = this, pts = this.real.map(function (a) { return a.p; }), n = this.real.length;
    this.setTube(pts, function (t) { return plddtColor(self.real[Math.min(n - 1, Math.round(t * (n - 1)))].b); });
    var epi = new Set(); this.segs.forEach(function (s) { for (var r = s[0]; r <= s[1]; r++) epi.add(r); });
    var g = new THREE.SphereGeometry(0.09, 10, 8), m = new THREE.MeshBasicMaterial({ color: 0xf0a63a });
    this.real.forEach(function (a) { if (epi.has(a.res)) { var s = new THREE.Mesh(g, m); s.position.copy(a.p); self.group.add(s); } });
  };

  Fold.prototype.resize = function () {
    var w = this.el.clientWidth, h = this.el.clientHeight; if (!w || !h) return;
    this.renderer.setSize(w, h, false); this.camera.aspect = w / h; this.camera.updateProjectionMatrix(); this.loop();
  };

  Fold.prototype.frame = function (now) {
    this.raf = 0;
    if (!this.visible || document.hidden) return;
    var animate = !this.paused || this.dragging;
    if (this.mode === "concept") {
      var T = ((now - this.t0) / 1000) % 11, n = this.n, f = reduce ? 1 : (T < 1.2 ? 0 : T < 5.8 ? (T - 1.2) / 4.6 : T < 8.6 ? 1 : 1 - (T - 8.6) / 2.4);
      var pts = [], i;
      for (i = 0; i < n; i++) {
        var li = reduce ? 1 : ease(f * 1.45 - (i / n) * 0.45);
        pts.push(this.ext[i].clone().lerp(this.fold[i], li));
      }
      this.setTube(pts, rainbow);
    }
    if (animate && !this.dragging) this.rotY += this.vel;
    this.group.rotation.set(this.rotX, this.rotY, 0);
    this.renderer.render(this.scene, this.camera);
    if (animate) this.raf = requestAnimationFrame(this.frame.bind(this));
  };
  Fold.prototype.loop = function () { if (!this.raf && this.visible) this.raf = requestAnimationFrame(this.frame.bind(this)); };

  function init() { Array.prototype.forEach.call(document.querySelectorAll("[data-fold3d]"), function (el) { if (!el.__fold) el.__fold = new Fold(el); }); }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
