"""S9.5 generation client: one pass@1 attempt per (split, task, arm) against any OpenAI-compatible chat endpoint.

Standard library only. It needs one route, POST <endpoint>/chat/completions, so it works with vLLM, mlx_lm.server,
llama.cpp or a hosted API. endpoint is the base URL an OpenAI SDK takes, so it ends in /v1 (for example
http://localhost:8000/v1). Hosting the models is out of scope. One endpoint may serve every arm (vLLM with a LoRA module),
or each arm may be run in its own call against its own server: calls into the same out_dir accumulate.

  * pass@1. A (split, task, arm) pair gets one sample. A pair already in generations.jsonl is skipped on resume and never
    regenerated, whatever the sample was: truncated, a refusal, code that does not run. Only a request that got no
    response at all (refused or dropped connection, timeout) is retried, since nothing was sampled that anyone could
    look at. Any HTTP reply, an error status included, stops the run with that pair unrecorded; resuming asks for it again.
    finish_reason is recorded as received: "length" is a truncated answer, not a refusal.
  * Decoding is fixed before the first request. Only temperature has a default (0); max_tokens is required. Every field
    is written to generation_manifest.json, and resuming into a directory whose manifest holds another block is an error.
  * The endpoint is https, or http for localhost, 127.0.0.1 and ::1 only. Redirects are never followed. An optional key is
    read from S9_5_ENDPOINT_KEY while a request is sent, goes to the endpoint's host only, and is never stored, logged or
    returned.
  * Reasoning models: run them with a reasoning parser, or with thinking off (for Qwen3.5,
    chat_template_kwargs {"enable_thinking": false}), so reasoning is not part of the answer text.

Outputs in out_dir: generations.jsonl (one row per pair, appended as the answer arrives), code/<arm>/<task_id>.py (the
code extracted from the answer, when there is any) and generation_manifest.json.
"""
from __future__ import annotations

import ast
import hashlib
import http.client
import json
import math
import os
import re
import socket
import ssl
import time
import urllib.parse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

KEY_ENV = "S9_5_ENDPOINT_KEY"
SCHEMA_VERSION = 1
GENERATIONS_FILE = "generations.jsonl"
MANIFEST_FILE = "generation_manifest.json"
SPLITS = ("heldout", "probe", "candidate", "pipeline")
LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})

_USER_AGENT = "python-http-client/1.0"
_TASK_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
_ARM = re.compile(r"[a-z][a-z0-9_-]{0,31}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/@+-]{0,199}\Z")
_NO_RESPONSE = (ConnectionError, TimeoutError, socket.timeout, socket.gaierror, http.client.IncompleteRead)
_PYTHON_FENCES = ("", "python", "py", "python3")
_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})[ \t]*([^\s`]*)")


class GenerationError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


@dataclass
class Response:
    """What a transport returns. stream needs read(n=None) and close()."""
    status: int
    headers: dict
    stream: object

    def read(self, n=None):
        return self.stream.read(n)

    def close(self):
        self.stream.close()


# ---- transport (the same shape as s9_5_adaption's, kept separate so this module depends on nothing else) ----------------

def _ssl_context():
    cafile = os.environ.get("SSL_CERT_FILE")
    if not cafile:
        try:
            import certifi
            cafile = certifi.where()
        except ImportError:
            pass  # python.org builds on macOS ship no CA bundle; certifi or SSL_CERT_FILE fixes that
    return ssl.create_default_context(cafile=cafile)  # certificate and hostname checks stay on


class _Stream:
    def __init__(self, resp, conn):
        self._resp, self._conn = resp, conn

    def read(self, n=None):
        return self._resp.read(n)

    def close(self):
        self._resp.close()
        self._conn.close()


def _http(method, url, headers, body, timeout):
    """Default transport: https, or http to loopback. http.client never follows a redirect, so a 3xx comes back as is."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout, context=_ssl_context())
    elif parts.scheme == "http" and parts.hostname in LOOPBACK:
        conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=timeout)
    else:
        raise GenerationError("refusing a URL that is neither https nor loopback http")
    try:
        conn.request(method, (parts.path or "/") + ("?" + parts.query if parts.query else ""), body=body, headers=headers)
        resp = conn.getresponse()
    except BaseException:
        conn.close()
        raise
    return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, _Stream(resp, conn))


def _scrub(raw):
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    key = os.environ.get(KEY_ENV, "").strip()
    return text.replace(key, "[REDACTED]") if key else text


# ---- inputs ---------------------------------------------------------------------------------------------------------

def _endpoint(url):
    """(base URL without a trailing slash, host label for the manifest) for a valid endpoint."""
    parts = urllib.parse.urlsplit(url.strip() if isinstance(url, str) else "")
    host = parts.hostname
    if parts.scheme not in ("http", "https") or not host:
        raise ValueError("endpoint must be an http(s) URL with a host, for example https://host/v1")
    if parts.scheme == "http" and host not in LOOPBACK:
        raise ValueError("endpoint must be https; plain http is allowed only for localhost, 127.0.0.1 and ::1")
    if parts.username is not None or parts.password is not None or parts.query or parts.fragment:
        raise ValueError("endpoint must not carry credentials, a query or a fragment")
    port = parts.port  # a bad port raises ValueError here
    label = (f"[{host}]" if ":" in host else host) + (f":{port}" if port else "")
    path = parts.path.rstrip("/")
    if path.endswith("/chat/completions"):
        path = path[: -len("/chat/completions")]
    return f"{parts.scheme}://{label}{path}", label


def _num(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


_DECODING_RULES = {
    "temperature": lambda v: _num(v) and v >= 0,
    "max_tokens": lambda v: type(v) is int and v >= 1,
    "top_p": lambda v: _num(v) and 0 < v <= 1,
    "top_k": lambda v: type(v) is int and v >= -1,
    "min_p": lambda v: _num(v) and 0 <= v <= 1,
    "presence_penalty": lambda v: _num(v) and -2 <= v <= 2,
    "frequency_penalty": lambda v: _num(v) and -2 <= v <= 2,
    "repetition_penalty": lambda v: _num(v) and v > 0,
    "seed": lambda v: type(v) is int,
    "stop": lambda v: isinstance(v, list) and 0 < len(v) <= 4 and all(isinstance(s, str) and s for s in v),
    "chat_template_kwargs": lambda v: isinstance(v, dict) and all(
        isinstance(k, str) and (x is None or isinstance(x, (bool, str)) or _num(x)) for k, x in v.items()),
}


def _decoding(raw):
    """The decoding block as it will be sent and recorded. temperature defaults to 0; nothing else has a default."""
    if not isinstance(raw, dict):
        raise ValueError("decoding must be a dict")
    unknown = sorted(set(raw) - set(_DECODING_RULES))
    if unknown:
        raise ValueError(f"unknown decoding field(s) {unknown}; allowed: {sorted(_DECODING_RULES)}")
    if "max_tokens" not in raw:
        raise ValueError("decoding needs max_tokens: there is no default")
    block = {"temperature": 0, **raw}
    for name, value in block.items():
        if not _DECODING_RULES[name](value):
            raise ValueError(f"decoding.{name} is not valid: {value!r}")
    return json.loads(json.dumps(block))


def _arms(arms):
    if not isinstance(arms, dict) or not arms:
        raise ValueError("arms must map each arm name to the model name the endpoint serves")
    for name, model in arms.items():
        if not isinstance(name, str) or not _ARM.match(name):
            raise ValueError(f"unsafe arm name {name!r}")
        if not isinstance(model, str) or not _MODEL.match(model):
            raise ValueError(f"unsafe served model name for arm {name!r}: {model!r}")
    return dict(arms)


def _check_tasks(tasks):
    seen, out = set(), []
    for i, task in enumerate(tasks, 1):
        if not isinstance(task, dict):
            raise ValueError(f"task {i} is not an object")
        task_id, split, family, prompt = task.get("task_id"), task.get("split"), task.get("family"), task.get("prompt")
        if not isinstance(task_id, str) or not _TASK_ID.match(task_id):
            raise ValueError(f"task {i}: unsafe task_id {task_id!r}")
        if task_id in seen:
            raise ValueError(f"task_id {task_id!r} appears twice")
        if split not in SPLITS:
            raise ValueError(f"{task_id}: split must be one of {SPLITS}, got {split!r}")
        if not isinstance(prompt, str) or not prompt.strip():
            raise ValueError(f"{task_id}: prompt must be a non-empty string")
        if family is not None and not isinstance(family, str):
            raise ValueError(f"{task_id}: family must be a string or absent")
        if split in ("heldout", "probe") and not family:
            raise ValueError(f"{task_id}: family is required for {split} tasks")
        seen.add(task_id)
        out.append({"task_id": task_id, "family": family, "split": split, "prompt": prompt})
    return out


def load_tasks(path):
    """Read a tasks.jsonl: one object per line with task_id, split, prompt, and family for heldout and probe tasks. The
    prompt must come from the same function that built the training prompts, so train and serve do not drift."""
    tasks = []
    for n, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if line.strip():
            try:
                tasks.append(json.loads(line))
            except ValueError:
                raise ValueError(f"{path} line {n} is not valid JSON") from None
    return _check_tasks(tasks)


# ---- code extraction ------------------------------------------------------------------------------------------------

def _looks_like_python(text):
    try:
        ast.parse(text)
    except (SyntaxError, ValueError):
        return False
    return bool(re.search(r"^\s*(import |from \S+ import |def )", text, re.M))


def extract_code(text):
    """The code in a model answer, or None.

    Text up to the last </think> is reasoning and is ignored; an opening <think> that never closes means the answer never
    started. The first non-empty fenced block whose language is python (or none) wins, and a fence that is never closed
    runs to the end of the answer, which is how a truncated answer looks. If there is no fence at all, the whole answer
    counts only when it is Python that imports or defines something, so a refusal in plain prose gives None."""
    if not isinstance(text, str):
        return None
    if "</think>" in text:
        text = text.rsplit("</think>", 1)[1]
    elif "<think>" in text:
        return None
    lines, i, fenced = text.splitlines(), 0, False
    while i < len(lines):
        opening = _FENCE_OPEN.match(lines[i])
        i += 1
        if not opening:
            continue
        fenced = True
        fence, language = opening.group(1), opening.group(2).lower()
        closing = re.compile(rf"^ {{0,3}}{re.escape(fence[0])}{{{len(fence)},}}[ \t]*$")
        body = []
        while i < len(lines) and not closing.match(lines[i]):
            body.append(lines[i])
            i += 1
        i += 1  # past the closing fence
        code = "\n".join(body).strip("\n")
        if language in _PYTHON_FENCES and code.strip():
            return code + "\n"
    if fenced:
        return None
    return text.strip() + "\n" if _looks_like_python(text) else None


# ---- the request ----------------------------------------------------------------------------------------------------

def _parse_reply(reply):
    try:
        choice = reply["choices"][0]
        message = choice["message"]
    except (KeyError, IndexError, TypeError):
        raise GenerationError("the reply has no choices[0].message") from None
    if not isinstance(message, dict):
        raise GenerationError("choices[0].message is not an object")
    content, refusal = message.get("content"), message.get("refusal")
    if content is None:
        text = refusal if isinstance(refusal, str) else ""  # an OpenAI-style refusal arrives in its own field
    elif isinstance(content, str):
        text = content
    else:
        raise GenerationError("message.content is not a string")
    finish = choice.get("finish_reason")
    usage = reply.get("usage") if isinstance(reply.get("usage"), dict) else {}
    tokens = [usage.get(k) if type(usage.get(k)) is int else None for k in ("prompt_tokens", "completion_tokens")]
    return text, finish if isinstance(finish, str) else None, tokens[0], tokens[1]


def _complete(url, model, prompt, decoding, *, timeout, max_attempts, backoff, transport, sleep):
    """One chat completion. Retries only a request that got no response; any reply is final."""
    body = json.dumps({**decoding, "model": model, "messages": [{"role": "user", "content": prompt}], "stream": False},
                      sort_keys=True, separators=(",", ":")).encode("utf-8")
    where = "POST " + urllib.parse.urlsplit(url).path
    attempt = 0
    while True:
        attempt += 1
        headers = {"Content-Type": "application/json", "Accept": "application/json", "User-Agent": _USER_AGENT}
        key = os.environ.get(KEY_ENV, "").strip()  # read now, used for this one request, never kept
        if key:
            headers["Authorization"] = "Bearer " + key
        try:
            resp = (transport or _http)("POST", url, headers, body, timeout)
            try:
                raw = resp.read()
            finally:
                resp.close()
            break
        except ssl.SSLCertVerificationError as e:
            raise GenerationError("TLS certificate check failed; install certifi or set SSL_CERT_FILE") from e
        except _NO_RESPONSE as e:
            if attempt >= max_attempts:
                raise GenerationError(f"{where}: no response after {attempt} attempt(s) ({type(e).__name__}); nothing was "
                                      "recorded for this pair") from None
            sleep(backoff * 2 ** (attempt - 1))
    if 300 <= resp.status < 400:
        raise GenerationError(f"{where} -> HTTP {resp.status}: redirects are not followed", resp.status)
    if not 200 <= resp.status < 300:
        raise GenerationError(f"{where} -> HTTP {resp.status}: {_scrub(raw)[:300]}", resp.status)
    try:
        reply = json.loads(raw)
    except ValueError:
        raise GenerationError(f"{where} -> HTTP {resp.status} but the body is not JSON") from None
    return (*_parse_reply(reply), attempt)


# ---- the output directory -------------------------------------------------------------------------------------------

def _stamp(moment):
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _read_rows(path):
    rows, seen = [], set()
    if not path.exists():
        return rows
    for n, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            raise GenerationError(f"{path.name} line {n} is not valid JSON; fix or remove it before resuming") from None
        if not isinstance(row, dict) or not all(isinstance(row.get(k), str)
                                                for k in ("task_id", "arm", "split", "prompt_sha256")):
            raise GenerationError(f"{path.name} line {n} is not a generation row")
        key = (row["task_id"], row["arm"])
        if key in seen:
            raise GenerationError(f"{path.name} holds two rows for task {key[0]!r}, arm {key[1]!r}: pass@1 allows one")
        seen.add(key)
        rows.append(row)
    return rows


def _load_manifest(path):
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        raise GenerationError(f"{path.name} is unreadable; refusing to resume without the recorded decoding") from None
    arms_ok = isinstance(manifest, dict) and isinstance(manifest.get("arms"), dict) and all(
        isinstance(a, dict) and isinstance(a.get("served_model"), str) and isinstance(a.get("endpoint_hosts"), list)
        for a in manifest["arms"].values())
    if not (arms_ok and manifest.get("schema_version") == SCHEMA_VERSION and isinstance(manifest.get("decoding"), dict)
            and isinstance(manifest.get("extra"), dict)):
        raise GenerationError(f"{path.name} is not a generation manifest this module wrote")
    return manifest


def _update_manifest(path, decoding, arms, host, extra, *, has_rows, moment):
    """Record the decoding, arms and provenance before any request. A recorded value is never changed."""
    stamp = _stamp(moment)
    if path.exists():
        manifest = _load_manifest(path)
        if manifest["decoding"] != decoding:
            raise GenerationError("the decoding block differs from the one recorded in generation_manifest.json; "
                                  "use a new output directory to change it")
    elif has_rows:
        raise GenerationError("generations.jsonl exists but generation_manifest.json does not; refusing to resume "
                              "without the recorded decoding")
    else:
        manifest = {"schema_version": SCHEMA_VERSION, "created_at_utc": stamp, "decoding": decoding, "arms": {}, "extra": {}}
    for name, value in extra.items():
        if manifest["extra"].get(name, value) != value:
            raise GenerationError(f"extra[{name!r}] differs from the value already recorded")
        manifest["extra"][name] = value
    for arm, model in arms.items():
        record = manifest["arms"].setdefault(arm, {"served_model": model, "endpoint_hosts": []})
        if record["served_model"] != model:
            raise GenerationError(f"arm {arm!r} was recorded as served model {record['served_model']!r}, not {model!r}")
        if host not in record["endpoint_hosts"]:
            record["endpoint_hosts"] = sorted(record["endpoint_hosts"] + [host])
    manifest["updated_at_utc"] = stamp
    text = json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    key = os.environ.get(KEY_ENV, "").strip()
    if key and key in text:
        raise GenerationError("the endpoint key must not appear in the manifest (check extra)")
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _append_row(path, row):
    line = json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n"
    with open(path, "ab") as f:
        f.write(line.encode("utf-8"))
        f.flush()
        os.fsync(f.fileno())


def _write_code(out, arm, task_id, code, *, only_if_missing):
    if code is None:
        return
    path = out / "code" / arm / f"{task_id}.py"
    if only_if_missing and path.exists():
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(code, encoding="utf-8")
    os.replace(tmp, path)


def generate(tasks, arms, endpoint, decoding, out_dir, *, extra=None, timeout=1800.0, max_attempts=3, backoff=2.0,
             workers=1, transport=None, sleep=time.sleep, now=None):
    """Sample each (task, arm) pair once and record it in out_dir. Returns {"recorded", "skipped", "rows"}.

    tasks: dicts or load_tasks(path). arms: {arm name: model name the endpoint serves}, for example
    {"base": "Qwen/Qwen3.5-9B", "finetune": "finetune"}. endpoint: the base URL, ending in /v1. decoding: a dict with
    max_tokens (required) and optionally temperature (default 0), top_p, top_k, min_p, presence_penalty,
    frequency_penalty, repetition_penalty, seed, stop and chat_template_kwargs; it is sent on every request and written
    to the manifest. extra: JSON-able provenance for the manifest (model revision, adapter SHA-256, server version); it may
    gain keys between calls but a recorded value never changes, and it must not hold the endpoint key. timeout is the
    seconds one request may take; max_attempts counts tries at a pair that got no response, with exponential backoff.
    workers is how many requests may be in flight at once. A server that batches (mlx_lm.server, vLLM) decodes them
    together, which is much faster than one at a time on memory-bound hardware. Each pair is still sampled once and is
    recorded as soon as it arrives, so rows land in the order they finish. A request that fails stops new ones from
    starting; the ones already in flight are still recorded.

    Tasks are walked in order with every arm inside each task, so a stopped run leaves balanced pairs (to within workers
    - 1 of them). One process per out_dir: nothing locks the directory."""
    tasks, arms, decoding = _check_tasks(tasks), _arms(arms), _decoding(decoding)
    base_url, label = _endpoint(endpoint)
    if extra is not None and not isinstance(extra, dict):
        raise ValueError("extra must be a dict")
    try:
        extra = json.loads(json.dumps(extra or {}))
    except (TypeError, ValueError):
        raise ValueError("extra must be JSON-serialisable") from None
    if type(max_attempts) is not int or max_attempts < 1:
        raise ValueError("max_attempts must be an integer of at least 1")
    if not _num(timeout) or timeout <= 0 or not _num(backoff) or backoff < 0:
        raise ValueError("timeout must be positive and backoff must not be negative")
    if type(workers) is not int or workers < 1:
        raise ValueError("workers must be an integer of at least 1")
    now = now or (lambda: datetime.now(timezone.utc))
    out = Path(out_dir)
    (out / "code").mkdir(parents=True, exist_ok=True)
    rows = _read_rows(out / GENERATIONS_FILE)
    _update_manifest(out / MANIFEST_FILE, decoding, arms, label, extra, has_rows=bool(rows), moment=now())
    done = {(row["task_id"], row["arm"]): row for row in rows}
    url = base_url + "/chat/completions"
    recorded = skipped = 0
    inflight, failure = {}, None  # future -> what the row needs; the first failure, raised once the rest are recorded

    def sample(task, model):
        return _complete(url, model, task["prompt"], decoding, timeout=timeout, max_attempts=max_attempts,
                         backoff=backoff, transport=transport, sleep=sleep)

    def drain(limit):
        """Record finished requests until at most limit are still in flight."""
        nonlocal recorded, failure
        while len(inflight) > limit:
            finished, _ = wait(inflight, return_when=FIRST_COMPLETED)
            for future in finished:
                task, arm, model, prompt_sha256, started = inflight.pop(future)
                try:
                    text, finish_reason, prompt_tokens, completion_tokens, attempts = future.result()
                except Exception as e:  # a pair that failed stays unrecorded; resuming asks for it again
                    failure = failure or e
                    continue
                row = {"task_id": task["task_id"], "family": task["family"], "split": task["split"], "arm": arm,
                       "served_model": model, "prompt_sha256": prompt_sha256, "text": text, "code": extract_code(text),
                       "finish_reason": finish_reason, "prompt_tokens": prompt_tokens,
                       "completion_tokens": completion_tokens, "attempts": attempts, "started_at_utc": started}
                _append_row(out / GENERATIONS_FILE, row)  # the record comes first; the code file can always be rebuilt from it
                _write_code(out, arm, task["task_id"], row["code"], only_if_missing=False)
                done[(task["task_id"], arm)] = row
                recorded += 1

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for task in tasks:
            prompt_sha256 = hashlib.sha256(task["prompt"].encode("utf-8")).hexdigest()
            for arm, model in arms.items():
                if failure:
                    break
                prior = done.get((task["task_id"], arm))
                if prior is not None:
                    if prior["split"] != task["split"] or prior["prompt_sha256"] != prompt_sha256:
                        failure = GenerationError(f"task {task['task_id']!r}, arm {arm!r} is already recorded with another "
                                                  "split or prompt; refusing to treat it as the same pair")
                        break
                    _write_code(out, arm, task["task_id"], prior.get("code"), only_if_missing=True)
                    skipped += 1
                    continue
                inflight[pool.submit(sample, task, model)] = (task, arm, model, prompt_sha256, _stamp(now()))
                drain(workers - 1)
        drain(0)
    if failure:
        raise failure
    return {"recorded": recorded, "skipped": skipped, "rows": len(done)}
