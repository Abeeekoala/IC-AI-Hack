"""Small stdlib-only HTTP client: retries, per-host rate limiting, streaming downloads.

The transport is injectable so tests (and offline demos) never touch the
network.  Everything that needs the internet goes through :class:`HttpClient`.
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Protocol

RETRY_STATUS = {429, 500, 502, 503, 504}


class HttpError(Exception):
    def __init__(self, status: int, url: str, body: bytes = b"", message: str | None = None):
        self.status = status
        self.url = url
        self.body = body
        super().__init__(message or f"HTTP {status} for {url}")


class OfflineError(Exception):
    """Raised when a network call is attempted in offline mode."""


@dataclass
class HttpResponse:
    url: str
    status: int
    headers: dict[str, str]
    body: bytes = b""

    def text(self, encoding: str = "utf-8") -> str:
        return self.body.decode(encoding, errors="replace")

    def json(self):
        return json.loads(self.body.decode("utf-8"))


@dataclass
class DownloadResult:
    url: str
    status: int
    headers: dict[str, str]
    path: Path
    bytes: int
    sha256: str


class RawResponse(Protocol):  # what a transport returns
    status: int
    headers: Mapping[str, str]

    def read(self, n: int = -1) -> bytes: ...
    def close(self) -> None: ...


Transport = Callable[[str, dict, float], RawResponse]


class _UrllibRaw:
    def __init__(self, resp):
        self._resp = resp
        self.status = getattr(resp, "status", 200)
        self.headers = {k.lower(): v for k, v in resp.headers.items()}

    def read(self, n: int = -1) -> bytes:
        return self._resp.read(n) if n and n > 0 else self._resp.read()

    def close(self) -> None:
        self._resp.close()


def urllib_transport(url: str, headers: dict, timeout: float) -> RawResponse:
    req = urllib.request.Request(url, headers=headers)
    try:
        return _UrllibRaw(urllib.request.urlopen(req, timeout=timeout))
    except urllib.error.HTTPError as exc:  # carries status + body
        body = b""
        try:
            body = exc.read()[:2000]
        except Exception:
            pass
        raise HttpError(exc.code, url, body) from None


def build_url(url: str, params: Mapping[str, object] | None) -> str:
    if not params:
        return url
    clean = {k: v for k, v in params.items() if v is not None}
    sep = "&" if "?" in url else "?"
    return url + sep + urllib.parse.urlencode(clean, doseq=True)


@dataclass
class HttpClient:
    user_agent: str = "vaxforge/0.1"
    timeout: float = 60.0
    retries: int = 3
    backoff: float = 1.5
    offline: bool = False
    # seconds between requests, per host (NCBI: 0.34 without key, 0.11 with)
    min_interval: dict[str, float] = field(default_factory=dict)
    transport: Transport = urllib_transport
    sleep: Callable[[float], None] = time.sleep
    _last: dict[str, float] = field(default_factory=dict, repr=False)
    request_log: list[str] = field(default_factory=list, repr=False)

    # -- internals --------------------------------------------------------
    def _throttle(self, url: str) -> None:
        host = urllib.parse.urlsplit(url).netloc.lower()
        gap = self.min_interval.get(host, 0.0)
        if gap > 0:
            wait = self._last.get(host, 0.0) + gap - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last[host] = time.monotonic()

    def _open(self, url: str, headers: Mapping[str, str] | None) -> RawResponse:
        if self.offline:
            raise OfflineError(f"offline mode: refusing network request to {url}")
        hdrs = {"User-Agent": self.user_agent, "Accept": "*/*"}
        if headers:
            hdrs.update(headers)
        last_exc: Exception | None = None
        for attempt in range(self.retries + 1):
            self._throttle(url)
            self.request_log.append(url)
            try:
                raw = self.transport(url, hdrs, self.timeout)
                if raw.status in RETRY_STATUS:
                    raw.close()
                    raise HttpError(raw.status, url)
                return raw
            except HttpError as exc:
                last_exc = exc
                if exc.status not in RETRY_STATUS:
                    raise
            except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as exc:
                last_exc = exc
            if attempt < self.retries:
                self.sleep(self.backoff * (2 ** attempt))
        if isinstance(last_exc, HttpError):
            raise last_exc
        hint = ""
        if "CERTIFICATE_VERIFY_FAILED" in str(last_exc):
            hint = (" (certificate check failed: on macOS with the python.org Python, run "
                    "'Install Certificates.command' in /Applications/Python 3.x/ once)")
        raise HttpError(0, url, message=f"network failure for {url}: {last_exc}{hint}") from last_exc

    # -- public ----------------------------------------------------------------
    def get(self, url: str, params: Mapping[str, object] | None = None,
            headers: Mapping[str, str] | None = None) -> HttpResponse:
        full = build_url(url, params)
        raw = self._open(full, headers)
        try:
            body = raw.read()
        finally:
            raw.close()
        if raw.status >= 400:
            raise HttpError(raw.status, full, body)
        return HttpResponse(full, raw.status, dict(raw.headers), body)

    def download(self, url: str, dest: str | os.PathLike, params: Mapping[str, object] | None = None,
                 headers: Mapping[str, str] | None = None, chunk: int = 1 << 20) -> DownloadResult:
        """Stream to ``dest`` via a ``.part`` file; verifies Content-Length when present."""
        full = build_url(url, params)
        dest = Path(dest)
        dest.parent.mkdir(parents=True, exist_ok=True)
        part = dest.with_name(dest.name + ".part")
        raw = self._open(full, headers)
        h = hashlib.sha256()
        total = 0
        try:
            if raw.status >= 400:
                raise HttpError(raw.status, full, raw.read()[:2000])
            with open(part, "wb") as fh:
                while True:
                    buf = raw.read(chunk)
                    if not buf:
                        break
                    fh.write(buf)
                    h.update(buf)
                    total += len(buf)
        except BaseException:
            raw.close()
            try:
                part.unlink()
            except OSError:
                pass
            raise
        raw.close()
        declared = raw.headers.get("content-length")
        if declared and declared.isdigit() and int(declared) != total:
            try:
                part.unlink()
            except OSError:
                pass
            raise HttpError(0, full, message=f"truncated download from {full}: got {total} of {declared} bytes")
        os.replace(part, dest)
        return DownloadResult(full, raw.status, dict(raw.headers), dest, total, h.hexdigest())


def client_from_config(config, transport: Transport | None = None) -> HttpClient:
    """Build an HttpClient from the ``network`` config section."""
    net = config.section("network")
    ncbi = net.get("ncbi") or {}
    has_key = bool(ncbi.get("api_key"))
    intervals = {
        "eutils.ncbi.nlm.nih.gov": 0.11 if has_key else 0.34,
        "rest.uniprot.org": 0.05,
    }
    intervals.update({str(k): float(v) for k, v in (net.get("min_interval_s") or {}).items()})
    kwargs = dict(
        user_agent=str(net.get("user_agent") or "vaxforge/0.1 (research prototype)"),
        timeout=float(net.get("timeout_s", 60)),
        retries=int(net.get("retries", 3)),
        backoff=float(net.get("backoff_s", 1.5)),
        offline=bool(net.get("offline", False)),
        min_interval=intervals,
    )
    if transport is not None:
        kwargs["transport"] = transport
    return HttpClient(**kwargs)
