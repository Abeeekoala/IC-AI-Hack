"""S9.5 platform client: Adaption's AutoScientist used as a training service (README §S9.5).

Standard library only. The official SDK was not used: it needs six dependencies and this repo has no dependency file
for them, it keeps the key on its client object, it reads the base URL from the environment, and it has no dry-run
mode. Only endpoints documented at https://docs.adaptionlabs.ai are called.

  * Anything that uploads our data or starts a run is a DRY RUN. It returns a DryRun holding the exact requests, with the
    Authorization header redacted, and sends nothing. A real send needs confirm=True AND guard=<callable>. The guard is
    called with no arguments just before the first request and must raise when the run is not allowed, for example
    guard=lambda: assert_run_allowed(manifest, model_id, cleared_sources, violations). Returning False also blocks.
  * The key is read from ADAPTION_API_KEY only while a request is being sent. It is never stored, logged or returned, and
    it is attached to api.prod.adaptionlabs.ai only, never to the presigned storage URL.
  * The run is SFT only (training_method="instruction"), carries no augmentation keys, and has no seed: the create
    request has no such field.

record_evidence() stores Adaption's own numbers labelled as such. They are an optimisation target, not evidence of
protocol quality (README §S9.5, last evaluation limit).
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import re
import ssl
import time
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

API_BASE = "https://api.prod.adaptionlabs.ai"
KEY_ENV = "ADAPTION_API_KEY"
TRAINING_METHOD = "instruction"
EVIDENCE_FILE = "adaption_evidence.json"
EVIDENCE_SCHEMA_VERSION = 1  # the same convention as validation/winrate.py's report
RUN_DONE = frozenset({"succeeded", "failed", "cancelled"})
CAVEAT = ("best_win_rate is Adaption's own optimisation target, measured on evaluations it derives from the uploaded "
          "dataset. It is not evidence of protocol quality; the held-out win rate is validation/winrate.py.")

_API_HOST = urllib.parse.urlsplit(API_BASE).hostname
_REDACTED = "Bearer [REDACTED]"
_USER_AGENT = "python-http-client/1.0"
_TRANSIENT_STATUS = frozenset({502, 503, 504})
_TRANSIENT_NET = (ConnectionError, TimeoutError, http.client.IncompleteRead)
_TABULAR = {".csv": "csv", ".json": "json", ".jsonl": "jsonl", ".parquet": "parquet"}  # raw mode needs tabular input
_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}\Z")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}\Z")


class AdaptionError(Exception):
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class GuardRequired(AdaptionError):
    """A real send was requested without a guard that passes."""


class RunTimeout(AdaptionError, TimeoutError):
    """Polling stopped waiting. The run keeps going on the platform; poll again with the same id."""


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


@dataclass
class DryRun:
    """The exact requests a mutating call would send. Nothing was sent."""
    requests: list

    def to_dict(self):
        return {"dry_run": True, "requests": self.requests}


# ---- transport -----------------------------------------------------------------------------------------------------

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
    """Default transport. HTTPS only, and redirects are never followed, so a header cannot be forwarded to another host."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise AdaptionError("refusing a non-https URL")
    conn = http.client.HTTPSConnection(parts.hostname, parts.port or 443, timeout=timeout, context=_ssl_context())
    try:
        conn.request(method, (parts.path or "/") + ("?" + parts.query if parts.query else ""), body=body, headers=headers)
        resp = conn.getresponse()
    except BaseException:
        conn.close()
        raise
    return Response(resp.status, {k.lower(): v for k, v in resp.getheaders()}, _Stream(resp, conn))


def _key():
    key = os.environ.get(KEY_ENV, "").strip()
    if not key:
        raise AdaptionError(f"{KEY_ENV} is not set")
    return key


def _scrub(raw):
    text = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
    key = os.environ.get(KEY_ENV, "").strip()
    return text.replace(key, "[REDACTED]") if key else text


def _req(method, path, body=None, *, url=None, headers=None, data=None, auth=True):
    """The one place a request is described. The dry run shows exactly this, with the key redacted."""
    h = {"Accept": "application/json", "User-Agent": _USER_AGENT, **(headers or {})}
    if body is not None:
        data = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
        h["Content-Type"] = "application/json"
    return {"method": method, "url": url or API_BASE + path, "headers": h, "body": body, "data": data, "auth": auth}


def _describe(req):
    headers = dict(req["headers"])
    if req["auth"]:
        headers["Authorization"] = _REDACTED
    return {"method": req["method"], "url": req["url"], "headers": headers, "body": req["body"]}


def _do(req, transport, timeout):
    headers = dict(req["headers"])
    if req["auth"]:
        if urllib.parse.urlsplit(req["url"]).hostname != _API_HOST:
            raise AdaptionError("refusing to send the API key to another host")
        headers["Authorization"] = "Bearer " + _key()
    try:
        return (transport or _http)(req["method"], req["url"], headers, req["data"], timeout)
    except ssl.SSLCertVerificationError as e:
        raise AdaptionError("TLS certificate check failed; install certifi or set SSL_CERT_FILE") from e


def _json(req, transport=None, timeout=60.0):
    resp = _do(req, transport, timeout)
    try:
        raw = resp.read()
    finally:
        resp.close()
    where = f"{req['method']} {urllib.parse.urlsplit(req['url']).path}"  # path only: a storage URL carries a signature
    if not 200 <= resp.status < 300:
        raise AdaptionError(f"{where} -> HTTP {resp.status}: {_scrub(raw)[:300]}", resp.status)
    try:
        return json.loads(raw) if raw else {}
    except ValueError:
        raise AdaptionError(f"{where} -> HTTP {resp.status} but the body is not JSON") from None


def _gate(confirm, guard):
    """True when the request may really be sent. confirm=True without a passing guard raises."""
    if confirm is not True:
        return False
    if not callable(guard):
        raise GuardRequired("confirm=True also needs a guard callable that raises when the run is not allowed")
    if guard() is False:
        raise GuardRequired("the guard returned False")
    return True


def _check(value, what, pattern=_ID):
    if not isinstance(value, str) or not pattern.match(value):
        raise ValueError(f"unsafe {what}: {value!r}")
    return value


def _seg(value, what):
    """A validated id, quoted for use as one URL path segment."""
    return urllib.parse.quote(_check(value, what), safe="")


def _sft_mapping(mapping):
    mapping = dict(mapping)
    if set(mapping) != {"prompt", "completion"} or not all(isinstance(v, str) and v for v in mapping.values()):
        raise ValueError("column_mapping must name exactly a prompt column and a completion column (SFT only)")
    return mapping


# ---- read-only calls -----------------------------------------------------------------------------------------------

def list_models(*, transport=None):
    """GET /api/v1/autoscientist/models. The live list is authoritative; the docs page differs from it."""
    models = _json(_req("GET", "/api/v1/autoscientist/models"), transport).get("models")
    if not isinstance(models, list):
        raise AdaptionError("the model list response has no 'models' array")
    return models


def get_run(run_id, *, transport=None):
    """GET /api/v1/autoscientist/{id}."""
    return _json(_req("GET", f"/api/v1/autoscientist/{_seg(run_id, 'run id')}"), transport)


def get_dataset_status(dataset_id, *, transport=None):
    """GET /api/v1/datasets/{id}/status."""
    return _json(_req("GET", f"/api/v1/datasets/{_seg(dataset_id, 'dataset id')}/status"), transport)


def _poll(fetch, done, *, first, cap, timeout, sleep, clock, what):
    start, wait, last = clock(), first, "no status yet"
    while True:
        try:
            state = fetch()
            last = str(state.get("status"))
            if done(state):
                return state
        except AdaptionError as e:
            if e.status not in _TRANSIENT_STATUS:
                raise
            last = f"HTTP {e.status}"
        except _TRANSIENT_NET:
            last = "network error"
        if clock() - start >= timeout:
            raise RunTimeout(f"{what} did not finish within {timeout:.0f}s (last: {last}); it may still be running")
        sleep(wait)
        wait = min(wait * 2, cap)


def wait_for_run(run_id, *, timeout=14400.0, first=10.0, cap=60.0, transport=None, sleep=time.sleep,
                 clock=time.monotonic):
    """Poll until the run is succeeded, failed or cancelled and return it. The caller reads status: succeeded can mean
    target_win_rate was reached or max_iterations ran out. 502/503/504 and dropped connections are retried."""
    return _poll(lambda: get_run(run_id, transport=transport), lambda r: r.get("status") in RUN_DONE,
                 first=first, cap=cap, timeout=timeout, sleep=sleep, clock=clock, what=f"run {run_id}")


def wait_for_dataset(dataset_id, *, timeout=1800.0, first=5.0, cap=30.0, transport=None, sleep=time.sleep,
                     clock=time.monotonic):
    """Poll until the dataset is processed and return its status (it carries row_count). failed raises."""
    def done(s):
        if s.get("status") == "failed":
            raise AdaptionError(f"dataset processing failed: {(s.get('error_data') or {}).get('message', 'no message')}")
        if s.get("status") == "awaiting_input":
            raise AdaptionError("dataset is awaiting_input; a raw tabular upload should not stop there")
        return s.get("status") == "succeeded" and s.get("row_count") is not None
    return _poll(lambda: get_dataset_status(dataset_id, transport=transport), done, first=first, cap=cap,
                 timeout=timeout, sleep=sleep, clock=clock, what=f"dataset {dataset_id}")


def download_checkpoint(run_id, dest, *, chunk_size=1 << 20, timeout=60.0, transport=None):
    """GET /api/v1/autoscientist/{id}/download: stream the best checkpoint archive to dest and return its SHA-256.
    Never extracts it (treat the archive as untrusted) and never overwrites an existing file."""
    dest = Path(dest)
    if dest.exists():
        raise FileExistsError(dest)
    resp = _do(_req("GET", f"/api/v1/autoscientist/{_seg(run_id, 'run id')}/download",
                    headers={"Accept": "application/gzip"}), transport, timeout)
    part, made, digest, size = dest.with_name(dest.name + ".part"), False, hashlib.sha256(), 0
    try:
        if not 200 <= resp.status < 300:
            raise AdaptionError(f"download -> HTTP {resp.status}: {_scrub(resp.read(300))}", resp.status)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(part, "xb") as f:
            made = True
            for chunk in iter(lambda: resp.read(chunk_size), b""):
                f.write(chunk)
                digest.update(chunk)
                size += len(chunk)
        want = {k.lower(): v for k, v in resp.headers.items()}.get("content-length")
        if want is not None and int(want) != size:
            raise AdaptionError(f"truncated download: {size} of {want} bytes")
        os.replace(part, dest)
    except BaseException:
        if made:
            part.unlink(missing_ok=True)
        raise
    finally:
        resp.close()
    return digest.hexdigest()


# ---- mutating calls: dry run unless confirm=True and a guard passes --------------------------------------------------

def upload_training_file(path, *, column_mapping=None, name=None, confirm=False, guard=None, transport=None):
    """Raw (non-adapted) upload of a training-ready prompt/completion file, so Adaptive Data never touches it.

    Three requests: POST /api/v1/datasets, PUT the bytes to the presigned URL it returns (no key sent there), then
    POST /api/v1/datasets/{id}/upload/complete. A failed PUT leaves the empty dataset on the platform and deletes
    nothing; the error names it. name (default: the file stem) is sent to Adaption as the dataset's name, so keep it
    free of anything you would not publish. Returns dataset_id, status, file_sha256 and file_size_bytes."""
    p = Path(path)
    fmt = _TABULAR.get(p.suffix.lower())
    if fmt is None:
        raise ValueError(f"raw processing needs a {', '.join(_TABULAR)} file, got {p.suffix!r}")
    mapping = _sft_mapping({"prompt": "prompt", "completion": "completion"} if column_mapping is None else column_mapping)
    data = p.read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    create = _req("POST", "/api/v1/datasets", {"source": {
        "name": name or p.stem, "file_format": fmt, "processing_mode": "raw", "column_mapping": mapping}})
    complete = {"file_size_bytes": len(data), "sha256": sha}
    if not _gate(confirm, guard):
        return DryRun([
            _describe(create),
            {"method": "PUT", "url": "<upload_instructions.url from request 1>",
             "headers": {"Content-Length": str(len(data))}, "body": f"<{len(data)} bytes of {p.name}, sha256 {sha}>"},
            _describe(_req("POST", "/api/v1/datasets/<dataset_id from request 1>/upload/complete", complete))])
    made = _json(create, transport)
    dataset_id = made.get("dataset_id")
    complete_path = f"/api/v1/datasets/{_seg(dataset_id, 'dataset id')}/upload/complete"  # checked before any bytes move
    inst = made.get("upload_instructions") or {}
    if inst.get("method") != "PUT" or not str(inst.get("url", "")).startswith("https://"):
        raise AdaptionError(f"dataset {dataset_id} was created but the upload instructions are not an https PUT")
    resp = _do(_req("PUT", "", url=inst["url"], headers={"Content-Length": str(len(data))}, data=data, auth=False),
               transport, 120.0)
    try:
        resp.read()
    finally:
        resp.close()
    if not 200 <= resp.status < 300:
        raise AdaptionError(f"upload to storage failed with HTTP {resp.status}; dataset {dataset_id} was left "
                            "pending on the platform and nothing was deleted", resp.status)
    done = _json(_req("POST", complete_path, complete), transport)
    return {"dataset_id": dataset_id, "status": done.get("status"), "file_sha256": sha, "file_size_bytes": len(data)}


def create_run(dataset_id, *, model, max_iterations, target_win_rate, data_format=None, column_mapping=None,
               idempotency_key=None, confirm=False, guard=None, transport=None):
    """POST /api/v1/autoscientist. model must be pinned (an id from list_models), so the dry run shows what trains. The
    body never carries augmentation keys, hyperparams, a voucher or a seed. On a real send pass idempotency_key (the
    docs scope it to the dataset and honour it while the run is in progress): a retry after a dropped connection would
    otherwise start a second paid run. Returns the run dict, or a DryRun."""
    body = {"dataset_id": dataset_id, "model": model, "training_method": TRAINING_METHOD}
    _check(dataset_id, "dataset id")
    _check(model, "model id", _MODEL)
    if type(max_iterations) is not int or not 1 <= max_iterations <= 5:
        raise ValueError("max_iterations must be an integer from 1 to 5")
    if type(target_win_rate) not in (int, float) or not 0.5 < target_win_rate <= 1:
        raise ValueError("target_win_rate must be greater than 0.5 and at most 1")
    body.update(max_iterations=max_iterations, target_win_rate=target_win_rate)
    if data_format is not None:
        if data_format not in ("chat", "instruction"):
            raise ValueError("data_format must be 'chat' or 'instruction'")
        body["data_format"] = data_format
    if column_mapping is not None:
        body["column_mapping"] = _sft_mapping(column_mapping)
    if idempotency_key is not None:
        body["idempotency_key"] = _check(idempotency_key, "idempotency key")
    req = _req("POST", "/api/v1/autoscientist", body)
    if not _gate(confirm, guard):
        return DryRun([_describe(req)])
    return _json(req, transport)


# ---- evidence ------------------------------------------------------------------------------------------------------

def _utc(value):
    if not value:
        return None
    try:
        t = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return str(value)
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _on_unit_scale(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and 0 <= v <= 1 for v in values)


def record_evidence(run, out_dir, *, checkpoint_sha256=None, now=None):
    """Write out_dir/adaption_evidence.json from a run dict and return its path.

    The file is one flat object of snake_case keys with scalar values, so a UI can bind each key as it is. Only the
    fields below are copied, so no dataset, user or organisation id and no secret can reach the file. null means "not
    known yet" (best_win_rate before an iteration finishes, completed_at_utc while running) or "not downloaded"
    (checkpoint_sha256). target_reached is null unless both rates are on the 0 to 1 scale the docs give for
    target_win_rate: they do not state best_win_rate's scale, and a percentage would otherwise read as "reached"."""
    if not isinstance(run, dict) or not run.get("id") or not run.get("status"):
        raise ValueError("run must be the dict returned by get_run or wait_for_run")
    if checkpoint_sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", str(checkpoint_sha256)):
        raise ValueError("checkpoint_sha256 must be 64 lowercase hex characters")
    best, target = run.get("best_win_rate"), run.get("target_win_rate")
    record = {
        "schema_version": EVIDENCE_SCHEMA_VERSION,
        "run_id": run["id"], "status": run["status"], "training_method": run.get("training_method"),
        "base_model_id": run.get("model"), "iterations_completed": run.get("iterations_completed"),
        "max_iterations": run.get("max_iterations"), "best_win_rate": best, "target_win_rate": target,
        "target_reached": best >= target if _on_unit_scale(best, target) else None,
        "checkpoint_sha256": checkpoint_sha256,
        "created_at_utc": _utc(run.get("created_at")), "completed_at_utc": _utc(run.get("completed_at")),
        "recorded_at_utc": _utc((now or datetime.now(timezone.utc)).isoformat()),
        "caveat": CAVEAT,
    }
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / EVIDENCE_FILE
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path
