/* ws-verify.js: digest verification for VaxForge run artifacts.
   Pure code: no DOM, no network. Browser: window.WSVerify = {...}. Node: module.exports = {...}.

   The rule (vaxforge/artifacts.py): digest = "sha256:" + sha256(utf8(canonical)), where canonical is the compact JSON
   (separators "," and ":", keys sorted by code point, no NaN, non-ASCII kept as it is) of the object
   {schema, stage, producer, params, inputs, status, warnings, payload}; a missing key becomes null.
   JSON.parse would turn 1.0 into 1, so parseRaw keeps every number as its original text. Python wrote the files with its own
   float repr, so re-emitting the raw token reproduces the hashed bytes.

   API (all digests are strings like "sha256:<64 hex>"):
     parseRaw(text)                    -> node tree: null | true | false | string | RawNum{raw} | Array | RawObj{keys, map}
     canonical(node)                   -> the canonical JSON text of any node
     envelopeBody(node)                -> RawObj holding the eight hashed keys of an artifact
     digestOfEnvelope(text, subtle)    -> Promise<"sha256:...">  (subtle = crypto.subtle)
     inputsOf(text)                    -> {upstreamStageId: digest}  (the "inputs" map; {} when empty)
     verifyRun({files, recorded, order, errors, subtle})
                                       -> Promise<Array<result>> with a .summary property
         files    {fileName: text}      the artifact texts of one run
         recorded {fileName: digest}    optional digests listed elsewhere (manifest.json or workspace.json); compared as well
         order    [fileName]            optional display order (default: stage order)
         errors   {fileName: message}   optional reasons a file could not be loaded
       result = {name, id, recorded, listed, recomputed, ok, problems[], inputs{}, chain[{input, expected, actual, ok, note}],
                 chainOk, error}
         ok       the recomputed digest equals the digest written in the file (and the listed one, when given)
         chainOk  every value in "inputs" equals the recorded digest of the named upstream artifact of the same run
       summary = {total, matched, mismatched, unchecked, links, brokenLinks, allOk, text, chainText}
     summarize(results), sha256Hex(bytes, subtle), NO_SUBTLE_MESSAGE */
(function (root, factory) {
  var api = factory();
  if (typeof module === "object" && module && module.exports) module.exports = api;
  else root.WSVerify = api;
})(typeof window !== "undefined" ? window : this, function () {
  "use strict";

  var KEYS = ["schema", "stage", "producer", "params", "inputs", "status", "warnings", "payload"];
  var NO_SUBTLE_MESSAGE = "Verification needs a secure context (https or localhost)";

  /* ---------- tree nodes ---------- */
  function RawNum(raw) { this.raw = raw; }
  function RawObj() { this.keys = []; this.map = Object.create(null); }
  RawObj.prototype.has = function (k) { return k in this.map; };
  RawObj.prototype.get = function (k) { return k in this.map ? this.map[k] : undefined; };
  RawObj.prototype.set = function (k, v) {
    if (!(k in this.map)) this.keys.push(k);   /* a repeated key keeps its first position and takes the last value, as Python does */
    this.map[k] = v;
  };

  /* ---------- tokenising parser ---------- */
  var NUM = /-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?/y;

  function parseRaw(text) {
    if (typeof text !== "string") throw new TypeError("parseRaw needs the file text as a string");
    var i = text.charCodeAt(0) === 0xFEFF ? 1 : 0;
    var n = text.length;

    function fail(msg) { throw new SyntaxError("Invalid JSON at offset " + i + ": " + msg); }
    function skip() {
      while (i < n) {
        var c = text.charCodeAt(i);
        if (c === 32 || c === 10 || c === 13 || c === 9) i++; else break;
      }
    }
    function number() {
      NUM.lastIndex = i;
      var m = NUM.exec(text);
      if (!m) fail("bad number");
      i += m[0].length;
      return new RawNum(m[0]);
    }
    function string() {
      i++;                                  /* opening quote */
      var out = "", start = i;
      for (;;) {
        if (i >= n) fail("unterminated string");
        var c = text.charCodeAt(i);
        if (c === 34) { out += text.slice(start, i); i++; return out; }
        if (c < 32) fail("control character inside a string");
        if (c === 92) {
          out += text.slice(start, i);
          i++;
          var e = text.charAt(i);
          if (e === '"') out += '"';
          else if (e === "\\") out += "\\";
          else if (e === "/") out += "/";
          else if (e === "b") out += "\b";
          else if (e === "f") out += "\f";
          else if (e === "n") out += "\n";
          else if (e === "r") out += "\r";
          else if (e === "t") out += "\t";
          else if (e === "u") {
            var hex = text.substr(i + 1, 4);
            if (!/^[0-9a-fA-F]{4}$/.test(hex)) fail("bad unicode escape");
            out += String.fromCharCode(parseInt(hex, 16));
            i += 4;
          } else fail("bad escape");
          i++;
          start = i;
        } else i++;
      }
    }
    function array(depth) {
      i++;
      var out = [];
      skip();
      if (text.charCodeAt(i) === 93) { i++; return out; }
      for (;;) {
        out.push(value(depth + 1));
        skip();
        var c = text.charCodeAt(i);
        if (c === 44) { i++; continue; }
        if (c === 93) { i++; return out; }
        fail("expected a comma or ]");
      }
    }
    function object(depth) {
      i++;
      var o = new RawObj();
      skip();
      if (text.charCodeAt(i) === 125) { i++; return o; }
      for (;;) {
        skip();
        if (text.charCodeAt(i) !== 34) fail("expected a string key");
        var k = string();
        skip();
        if (text.charCodeAt(i) !== 58) fail("expected a colon");
        i++;
        o.set(k, value(depth + 1));
        skip();
        var c = text.charCodeAt(i);
        if (c === 44) { i++; continue; }
        if (c === 125) { i++; return o; }
        fail("expected a comma or }");
      }
    }
    function value(depth) {
      if (depth > 512) fail("nested too deeply");
      skip();
      if (i >= n) fail("unexpected end of text");
      var c = text.charCodeAt(i);
      if (c === 123) return object(depth);
      if (c === 91) return array(depth);
      if (c === 34) return string();
      if (c === 45 || (c >= 48 && c <= 57)) return number();
      if (text.startsWith("true", i)) { i += 4; return true; }
      if (text.startsWith("false", i)) { i += 5; return false; }
      if (text.startsWith("null", i)) { i += 4; return null; }
      return fail("unexpected character " + JSON.stringify(text.charAt(i)));   /* NaN and Infinity land here: not allowed */
    }

    var v = value(0);
    skip();
    if (i < n) fail("extra text after the value");
    return v;
  }

  /* ---------- canonical form ---------- */
  /* Python sorts keys by code point. UTF-16 order differs only when a supplementary character meets U+E000..U+FFFF. */
  function cmpCodePoints(a, b) {
    if (a === b) return 0;
    var la = a.length, lb = b.length, k = 0;
    while (k < la && k < lb) {
      var ca = a.charCodeAt(k), cb = b.charCodeAt(k);
      if (ca !== cb) {
        var sa = ca >= 0xD800 && ca <= 0xDFFF, sb = cb >= 0xD800 && cb <= 0xDFFF;
        if (sa !== sb) return sa ? 1 : -1;
        return ca < cb ? -1 : 1;
      }
      k++;
    }
    return la < lb ? -1 : la > lb ? 1 : 0;
  }

  function emit(node, out) {
    if (node === null) { out.push("null"); return; }
    var t = typeof node;
    if (t === "boolean") { out.push(node ? "true" : "false"); return; }
    if (t === "string") { out.push(JSON.stringify(node)); return; }
    if (node instanceof RawNum) { out.push(node.raw); return; }
    if (Array.isArray(node)) {
      out.push("[");
      for (var a = 0; a < node.length; a++) { if (a) out.push(","); emit(node[a], out); }
      out.push("]");
      return;
    }
    if (node instanceof RawObj) {
      var keys = node.keys.slice().sort(cmpCodePoints);
      out.push("{");
      for (var b = 0; b < keys.length; b++) {
        if (b) out.push(",");
        out.push(JSON.stringify(keys[b]), ":");
        emit(node.map[keys[b]], out);
      }
      out.push("}");
      return;
    }
    throw new TypeError("canonical: unsupported value");
  }

  function canonical(node) {
    var out = [];
    emit(node, out);
    return out.join("");
  }

  function envelopeBody(node) {
    if (!(node instanceof RawObj)) throw new TypeError("An artifact must be a JSON object");
    var body = new RawObj();
    for (var k = 0; k < KEYS.length; k++) body.set(KEYS[k], node.has(KEYS[k]) ? node.get(KEYS[k]) : null);
    return body;
  }

  /* ---------- hashing ---------- */
  function utf8(s) {
    var TE = typeof TextEncoder !== "undefined" ? TextEncoder : (typeof require === "function" ? require("util").TextEncoder : null);
    if (!TE) throw new Error("TextEncoder is not available in this browser");
    return new TE().encode(s);
  }

  function toHex(buf) {
    var b = new Uint8Array(buf), s = "";
    for (var k = 0; k < b.length; k++) s += (b[k] < 16 ? "0" : "") + b[k].toString(16);
    return s;
  }

  function sha256Hex(bytes, subtle) {
    if (!subtle || typeof subtle.digest !== "function") return Promise.reject(new Error(NO_SUBTLE_MESSAGE));
    return Promise.resolve(subtle.digest("SHA-256", bytes)).then(toHex);
  }

  function digestOfEnvelope(text, subtle) {
    return new Promise(function (resolve) {
      resolve(utf8(canonical(envelopeBody(parseRaw(text)))));
    }).then(function (bytes) { return sha256Hex(bytes, subtle); })
      .then(function (hex) { return "sha256:" + hex; });
  }

  /* ---------- what an artifact says about itself ---------- */
  function describe(node) {
    var d = { stage: null, digest: null, inputs: {} };
    if (!(node instanceof RawObj)) return d;
    var stage = node.get("stage"), digest = node.get("digest"), inputs = node.get("inputs");
    if (typeof stage === "string") d.stage = stage;
    if (typeof digest === "string") d.digest = digest;
    if (inputs instanceof RawObj) {
      for (var k = 0; k < inputs.keys.length; k++) {
        var v = inputs.map[inputs.keys[k]];
        d.inputs[inputs.keys[k]] = typeof v === "string" ? v : null;
      }
    }
    return d;
  }

  function inputsOf(text) { return describe(parseRaw(text)).inputs; }

  /* file name -> stage id, used only when a file cannot be read: s3_rank.json -> s3, s9plus_animate.json -> s9plus, s9_5_finetune.json -> s9_5 */
  function stageOfName(name) {
    var m = /^(s\d+(?:plus|_\d+)?)_/.exec(name) || /^(s\d+(?:plus)?)\./.exec(name);
    return m ? m[1] : null;
  }

  /* natural stage order: s0 .. s9, s9plus, s9_5, s10 .. s12 */
  function stageRank(name) {
    var m = /^s(\d+)(plus|_(\d+))?/.exec(name);
    if (!m) return [1e9, 0];
    return [parseInt(m[1], 10), m[2] === "plus" ? 0.5 : m[3] ? 0.5 + parseInt(m[3], 10) / 100 : 0];
  }
  function byStage(a, b) {
    var x = stageRank(a), y = stageRank(b);
    return x[0] - y[0] || x[1] - y[1] || (a < b ? -1 : a > b ? 1 : 0);
  }

  /* ---------- a whole run ---------- */
  function verifyRun(opts) {
    opts = opts || {};
    var files = opts.files || {}, listed = opts.recorded || {}, errors = opts.errors || {}, subtle = opts.subtle;
    var names = opts.order && opts.order.length ? opts.order.slice() : Object.keys(files).sort(byStage);

    var items = names.map(function (name) {
      var row = {
        name: name, id: stageOfName(name), recorded: null, listed: listed[name] || null, recomputed: null,
        ok: false, problems: [], inputs: {}, chain: [], chainOk: false, error: null
      };
      var item = { row: row, bytes: null };
      var text = files[name];
      if (typeof text !== "string") { row.error = errors[name] || "the file was not loaded"; return item; }
      try {
        var node = parseRaw(text), d = describe(node);
        if (d.stage) row.id = d.stage;
        row.recorded = d.digest;
        row.inputs = d.inputs;
        item.bytes = utf8(canonical(envelopeBody(node)));
      } catch (e) {
        row.error = "the file could not be read: " + (e && e.message ? e.message : e);
      }
      return item;
    });

    return Promise.all(items.map(function (it) {
      if (!it.bytes) return null;
      return sha256Hex(it.bytes, subtle).then(
        function (hex) { it.row.recomputed = "sha256:" + hex; },
        function (err) { it.row.error = err && err.message ? err.message : String(err); }
      );
    })).then(function () {
      var byId = {};
      items.forEach(function (it) { if (it.row.id) byId[it.row.id] = it.row; });
      var rows = items.map(function (it) {
        var r = it.row;
        if (r.recomputed) {
          if (!r.recorded) r.problems.push("the file records no digest");
          else if (r.recomputed !== r.recorded) r.problems.push("the recomputed digest differs from the digest written in the file");
          if (r.listed && r.recorded && r.listed !== r.recorded) r.problems.push("the digest listed in the manifest differs from the one in the file");
          r.ok = r.problems.length === 0;
        }
        Object.keys(r.inputs).forEach(function (up) {
          var expected = r.inputs[up], upRow = byId[up] || null;
          var actual = upRow ? upRow.recorded : null;
          var link = { input: up, expected: expected, actual: actual, ok: !!expected && !!actual && expected === actual, note: "" };
          if (!upRow) link.note = "the upstream artifact " + up + " is not in this run";
          else if (!actual) link.note = "the upstream artifact records no digest";
          else if (!link.ok) link.note = "the digest recorded for " + up + " is different";
          r.chain.push(link);
        });
        r.chainOk = r.chain.every(function (l) { return l.ok; });
        return r;
      });
      rows.summary = summarize(rows);
      return rows;
    });
  }

  function summarize(rows) {
    var total = rows.length, matched = 0, mismatched = 0, unchecked = 0, links = 0, brokenLinks = 0;
    rows.forEach(function (r) {
      if (!r.recomputed) unchecked++;
      else if (r.ok) matched++;
      else mismatched++;
      (r.chain || []).forEach(function (l) { links++; if (!l.ok) brokenLinks++; });
    });
    var text = matched + " of " + total + " artifacts match", extra = [];
    if (mismatched) extra.push(mismatched + (mismatched === 1 ? " mismatch" : " mismatches"));
    if (unchecked) extra.push(unchecked + " could not be checked");
    if (extra.length) text += " (" + extra.join(", ") + ")";
    return {
      total: total, matched: matched, mismatched: mismatched, unchecked: unchecked, links: links, brokenLinks: brokenLinks,
      allOk: total > 0 && matched === total && brokenLinks === 0, text: text,
      chainText: (links - brokenLinks) + " of " + links + " upstream links consistent"
    };
  }

  return {
    parseRaw: parseRaw, canonical: canonical, envelopeBody: envelopeBody, digestOfEnvelope: digestOfEnvelope,
    inputsOf: inputsOf, verifyRun: verifyRun, summarize: summarize, sha256Hex: sha256Hex,
    RawNum: RawNum, RawObj: RawObj, NO_SUBTLE_MESSAGE: NO_SUBTLE_MESSAGE
  };
});
