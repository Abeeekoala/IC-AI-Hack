/* Hero scene: a contour map wrapped around an invented antigen.
   A noise field displaces a sphere; the fragment shader draws isolines of that same field (every fifth is a
   heavier "index" contour, like a survey map) and tints the patches where a second field is high amber:
   the place an epitope could sit. Three small amber markers orbit it for the pipeline's top three.
   It is an illustration, not data, and the page says so. Written from scratch with three.js r128 (MIT).
   Behaviour: drag to turn it, pauses off-screen and when the tab is hidden, honours reduced motion,
   and falls back to the static poster if WebGL is unavailable. */
(function () {
  var host = document.getElementById("hero-3d");
  if (!host || !window.THREE) return;
  var THREE = window.THREE;
  var reduce = window.matchMedia("(prefers-reduced-motion: reduce)").matches;
  var small = window.matchMedia("(max-width: 860px)").matches;

  var renderer;
  try {
    renderer = new THREE.WebGLRenderer({ antialias: !small, alpha: true, powerPreference: "low-power" });
    if (!renderer.getContext()) throw new Error("no context");
  } catch (e) {
    host.classList.add("no-webgl");
    return;
  }
  renderer.setClearColor(0x000000, 0);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, small ? 1.25 : 1.75));
  host.appendChild(renderer.domElement);

  var scene = new THREE.Scene();
  var camera = new THREE.PerspectiveCamera(32, 1, 0.1, 50);
  camera.position.set(0, 0, 7.4);
  var group = new THREE.Group();
  scene.add(group);

  var NOISE = [
    "vec3 mod289(vec3 x){return x-floor(x*(1.0/289.0))*289.0;}",
    "vec4 mod289(vec4 x){return x-floor(x*(1.0/289.0))*289.0;}",
    "vec4 permute(vec4 x){return mod289(((x*34.0)+1.0)*x);}",
    "vec4 taylorInvSqrt(vec4 r){return 1.79284291400159-0.85373472095314*r;}",
    "float snoise(vec3 v){",
    " const vec2 C=vec2(1.0/6.0,1.0/3.0); const vec4 D=vec4(0.0,0.5,1.0,2.0);",
    " vec3 i=floor(v+dot(v,C.yyy)); vec3 x0=v-i+dot(i,C.xxx);",
    " vec3 g=step(x0.yzx,x0.xyz); vec3 l=1.0-g; vec3 i1=min(g.xyz,l.zxy); vec3 i2=max(g.xyz,l.zxy);",
    " vec3 x1=x0-i1+C.xxx; vec3 x2=x0-i2+C.yyy; vec3 x3=x0-D.yyy;",
    " i=mod289(i);",
    " vec4 p=permute(permute(permute(i.z+vec4(0.0,i1.z,i2.z,1.0))+i.y+vec4(0.0,i1.y,i2.y,1.0))+i.x+vec4(0.0,i1.x,i2.x,1.0));",
    " float n_=0.142857142857; vec3 ns=n_*D.wyz-D.xzx;",
    " vec4 j=p-49.0*floor(p*ns.z*ns.z); vec4 x_=floor(j*ns.z); vec4 y_=floor(j-7.0*x_);",
    " vec4 x=x_*ns.x+ns.yyyy; vec4 y=y_*ns.x+ns.yyyy; vec4 h=1.0-abs(x)-abs(y);",
    " vec4 b0=vec4(x.xy,y.xy); vec4 b1=vec4(x.zw,y.zw);",
    " vec4 s0=floor(b0)*2.0+1.0; vec4 s1=floor(b1)*2.0+1.0; vec4 sh=-step(h,vec4(0.0));",
    " vec4 a0=b0.xzyw+s0.xzyw*sh.xxyy; vec4 a1=b1.xzyw+s1.xzyw*sh.zzww;",
    " vec3 p0=vec3(a0.xy,h.x); vec3 p1=vec3(a0.zw,h.y); vec3 p2=vec3(a1.xy,h.z); vec3 p3=vec3(a1.zw,h.w);",
    " vec4 norm=taylorInvSqrt(vec4(dot(p0,p0),dot(p1,p1),dot(p2,p2),dot(p3,p3)));",
    " p0*=norm.x; p1*=norm.y; p2*=norm.z; p3*=norm.w;",
    " vec4 m=max(0.6-vec4(dot(x0,x0),dot(x1,x1),dot(x2,x2),dot(x3,x3)),0.0); m=m*m;",
    " return 42.0*dot(m*m,vec4(dot(p0,x0),dot(p1,x1),dot(p2,x2),dot(p3,x3)));",
    "}"
  ].join("\n");

  var vert = [
    NOISE,
    "uniform float uTime; uniform float uAmp;",
    "varying vec3 vN; varying vec3 vView; varying float vH; varying float vE;",
    "float field(vec3 p){",
    "  float t=uTime*0.045;",
    "  return snoise(p*1.15+vec3(0.0,t,0.0))*0.58+snoise(p*2.35-vec3(t,0.0,t*0.5))*0.28+snoise(p*4.6+vec3(3.0))*0.11;",
    "}",
    "vec3 surf(vec3 d){return d*(1.0+uAmp*field(d))*1.28;}",
    "void main(){",
    "  vec3 d=normalize(position);",
    "  vec3 pos=surf(d);",
    "  vec3 t1=normalize(cross(d,vec3(0.0,1.0,0.013))); vec3 t2=normalize(cross(d,t1));",
    "  vec3 p1=surf(normalize(d+t1*0.012)); vec3 p2=surf(normalize(d+t2*0.012));",
    "  vec3 n=normalize(cross(p1-pos,p2-pos)); if(dot(n,d)<0.0) n=-n;",
    "  vN=normalize(normalMatrix*n);",
    "  vec4 mv=modelViewMatrix*vec4(pos,1.0);",
    "  vView=-mv.xyz;",
    "  vH=field(d);",
    "  vE=snoise(d*1.55+vec3(7.3,1.1,4.2)+vec3(uTime*0.02));",
    "  gl_Position=projectionMatrix*mv;",
    "}"
  ].join("\n");

  var frag = [
    "uniform vec3 uBaseA; uniform vec3 uBaseB; uniform vec3 uLine; uniform vec3 uAmber; uniform float uDensity;",
    "varying vec3 vN; varying vec3 vView; varying float vH; varying float vE;",
    "void main(){",
    "  vec3 N=normalize(vN); vec3 V=normalize(vView);",
    "  float fres=pow(1.0-max(dot(N,V),0.0),2.4);",
    "  float lam=max(dot(N,normalize(vec3(-0.45,0.65,0.62))),0.0)*0.6+0.4;",
    "  float v=vH*uDensity;",
    "  float d=abs(fract(v-0.5)-0.5);",
    "  float line=1.0-smoothstep(0.0,fwidth(v)*1.25,d);",
    "  float v5=v/5.0;",
    "  float d5=abs(fract(v5-0.5)-0.5);",
    "  float major=1.0-smoothstep(0.0,fwidth(v5)*2.2,d5);",
    "  float ep=smoothstep(0.2,0.5,vE);",
    "  vec3 base=mix(uBaseA,uBaseB,lam);",
    "  vec3 lc=mix(uLine,uAmber,smoothstep(0.35,0.7,ep));",
    "  vec3 col=base+lc*(line*0.62+major*0.6)*(0.35+0.65*lam);",
    "  col+=uAmber*ep*0.22*lam;",
    "  col+=uLine*fres*0.5;",
    "  gl_FragColor=vec4(col,1.0);",
    "}"
  ].join("\n");

  var segW = small ? 96 : 176, segH = small ? 64 : 120;
  var mat = new THREE.ShaderMaterial({
    vertexShader: vert,
    fragmentShader: frag,
    extensions: { derivatives: true },
    uniforms: {
      uTime: { value: 0 },
      uAmp: { value: 0.2 },
      uDensity: { value: 11.0 },
      uBaseA: { value: new THREE.Color(0x061214) },
      uBaseB: { value: new THREE.Color(0x0e3a3d) },
      uLine: { value: new THREE.Color(0x56e0d1) },
      uAmber: { value: new THREE.Color(0xf0a63a) }
    }
  });
  var blob = new THREE.Mesh(new THREE.SphereBufferGeometry(1, segW, segH), mat);
  group.add(blob);

  /* Orbit rings, and three amber markers for the top three candidates. */
  function ring(radius, tilt, opacity) {
    var pts = [];
    for (var i = 0; i <= 180; i++) {
      var a = (i / 180) * Math.PI * 2;
      pts.push(new THREE.Vector3(Math.cos(a) * radius, 0, Math.sin(a) * radius));
    }
    var g = new THREE.BufferGeometry().setFromPoints(pts);
    var l = new THREE.Line(g, new THREE.LineBasicMaterial({ color: 0x7fe3d6, transparent: true, opacity: opacity }));
    l.rotation.set(tilt[0], tilt[1], tilt[2]);
    return l;
  }
  var ringA = ring(2.0, [1.15, 0.2, 0.3], 0.34);
  var ringB = ring(2.55, [0.35, 0.0, -0.5], 0.16);
  group.add(ringA, ringB);
  var markers = [];
  for (var m = 0; m < 3; m++) {
    var mk = new THREE.Mesh(new THREE.SphereBufferGeometry(0.055, 14, 10), new THREE.MeshBasicMaterial({ color: 0xf0a63a }));
    var halo = new THREE.Mesh(new THREE.RingBufferGeometry(0.1, 0.115, 32), new THREE.MeshBasicMaterial({ color: 0xf0a63a, transparent: true, opacity: 0.6, side: THREE.DoubleSide }));
    mk.add(halo);
    markers.push(mk);
    ringA.add(mk);
  }

  /* A little dust for depth. */
  var N = small ? 160 : 360;
  var pos = new Float32Array(N * 3);
  for (var i = 0; i < N; i++) {
    var r = 2.2 + Math.random() * 2.4, th = Math.random() * Math.PI * 2, ph = Math.acos(2 * Math.random() - 1);
    pos[i * 3] = r * Math.sin(ph) * Math.cos(th);
    pos[i * 3 + 1] = r * Math.sin(ph) * Math.sin(th) * 0.7;
    pos[i * 3 + 2] = r * Math.cos(ph);
  }
  var dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute("position", new THREE.BufferAttribute(pos, 3));
  var dust = new THREE.Points(dustGeo, new THREE.PointsMaterial({ color: 0x9fe9de, size: 0.018, transparent: true, opacity: 0.55, sizeAttenuation: true }));
  group.add(dust);

  /* Layout: keep the object on the right on wide screens, behind the copy on narrow ones. */
  function resize() {
    var w = host.clientWidth, h = host.clientHeight;
    if (!w || !h) return;
    renderer.setSize(w, h, false);
    camera.aspect = w / h;
    var wide = w > 1100;
    camera.position.z = wide ? 7.2 : (w / h > 1.3 ? 6.0 : 8.4);
    camera.setViewOffset(w, h, wide ? -w * 0.2 : 0, wide ? 0 : -h * 0.04, w, h);
    camera.updateProjectionMatrix();
    needs = true;
  }

  /* Interaction: drag to turn, gentle parallax toward the pointer. */
  var rotY = -0.5, rotX = 0.28, velY = reduce ? 0 : 0.0016, dragging = false, lastX = 0, lastY = 0, px = 0, py = 0;
  host.addEventListener("pointerdown", function (e) {
    dragging = true; lastX = e.clientX; lastY = e.clientY; velY = 0;
    try { host.setPointerCapture(e.pointerId); } catch (_) {}
  });
  host.addEventListener("pointermove", function (e) {
    var r = host.getBoundingClientRect();
    px = ((e.clientX - r.left) / r.width - 0.5) * 2;
    py = ((e.clientY - r.top) / r.height - 0.5) * 2;
    if (dragging) {
      var dx = e.clientX - lastX, dy = e.clientY - lastY;
      lastX = e.clientX; lastY = e.clientY;
      rotY += dx * 0.006; rotX = Math.max(-0.9, Math.min(0.9, rotX + dy * 0.004)); velY = dx * 0.0016;
    }
    needs = true;
    loop();
  });
  function release() { dragging = false; }
  host.addEventListener("pointerup", release);
  host.addEventListener("pointercancel", release);

  /* Run state: visible, not paused, tab shown. */
  var visible = true, paused = reduce, needs = true, raf = 0, t0 = performance.now();
  var btn = document.getElementById("motion-toggle");
  function syncBtn() {
    if (!btn) return;
    btn.setAttribute("aria-pressed", paused ? "true" : "false");
    btn.textContent = paused ? "Play motion" : "Pause motion";
  }
  if (btn) btn.addEventListener("click", function () { paused = !paused; syncBtn(); needs = true; loop(); });
  syncBtn();

  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function (es) { visible = es[0].isIntersecting; if (visible) loop(); }, { threshold: 0 }).observe(host);
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden) loop(); });
  if ("ResizeObserver" in window) new ResizeObserver(resize).observe(host); else window.addEventListener("resize", resize);

  function frame(now) {
    raf = 0;
    if (!visible || document.hidden) return;
    var animate = !paused || dragging;
    if (animate) {
      mat.uniforms.uTime.value = (now - t0) / 1000;
      if (!dragging) { rotY += velY; velY += ((paused ? 0 : 0.0016) - velY) * 0.03; }
    }
    group.rotation.y = rotY + px * 0.12;
    group.rotation.x = rotX + py * 0.07;
    ringA.rotation.y += animate ? 0.0016 : 0;
    ringB.rotation.y -= animate ? 0.0009 : 0;
    var tt = (now - t0) / 1000;
    for (var k = 0; k < markers.length; k++) {
      var a = tt * 0.32 + (k * Math.PI * 2) / 3;
      markers[k].position.set(Math.cos(a) * 2.0, 0, Math.sin(a) * 2.0);
      markers[k].lookAt(camera.position);
    }
    renderer.render(scene, camera);
    needs = false;
    if (animate) raf = requestAnimationFrame(frame);
  }
  function loop() { if (!raf && visible) raf = requestAnimationFrame(frame); }

  resize();
  host.classList.add("is-live");
  var poster = document.querySelector(".hero-poster"); if (poster) poster.classList.add("is-hidden");
  loop();
})();
