"""Test/demo helpers: record live HTTP once, replay it offline forever.

``RecordingTransport`` wraps the real transport and stores every response
under a directory; ``ReplayTransport`` serves them back and fails loudly on a
request that was never recorded.  ``FakeTransport`` serves hand-written routes.
Query parameters that identify the user or carry secrets (``email``,
``api_key``, ``tool``) are ignored when matching so recordings are portable.
"""
from __future__ import annotations

import hashlib
import io
import json
import urllib.parse
from pathlib import Path
from typing import Callable

from .http import HttpError, urllib_transport

_IGNORED_PARAMS = {"email", "api_key", "tool"}


def normalize_url(url: str) -> str:
    parts = urllib.parse.urlsplit(url)
    q = sorted((k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True) if k not in _IGNORED_PARAMS)
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(q), ""))


class _Raw:
    def __init__(self, status: int, headers: dict, body: bytes):
        self.status = status
        self.headers = {k.lower(): v for k, v in headers.items()}
        self._buf = io.BytesIO(body)

    def read(self, n: int = -1) -> bytes:
        return self._buf.read() if n is None or n < 0 else self._buf.read(n)

    def close(self) -> None:
        pass


def _key(url: str) -> str:
    return hashlib.sha1(normalize_url(url).encode("utf-8")).hexdigest()[:24]


class RecordingTransport:
    def __init__(self, directory: str | Path, inner=urllib_transport, max_body: int = 2_000_000):
        self.dir = Path(directory)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.inner = inner
        self.max_body = max_body

    def __call__(self, url, headers, timeout):
        raw = self.inner(url, headers, timeout)
        body = raw.read()
        raw.close()
        if len(body) > self.max_body:
            raise RuntimeError(f"refusing to record {len(body)} bytes for {url}; choose a smaller query")
        k = _key(url)
        (self.dir / f"{k}.body").write_bytes(body)
        keep = {h: raw.headers.get(h) for h in ("content-type", "x-uniprot-release", "x-total-results") if raw.headers.get(h)}
        (self.dir / f"{k}.json").write_text(
            json.dumps({"url": normalize_url(url), "status": raw.status, "headers": keep}, indent=2), encoding="utf-8")
        return _Raw(raw.status, keep, body)


class ReplayTransport:
    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.calls: list[str] = []

    def __call__(self, url, headers, timeout):
        self.calls.append(normalize_url(url))
        k = _key(url)
        meta_path = self.dir / f"{k}.json"
        if not meta_path.is_file():
            raise HttpError(0, url, message=f"ReplayTransport: no recording for {normalize_url(url)}")
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        return _Raw(meta["status"], meta["headers"], (self.dir / f"{k}.body").read_bytes())


class FakeTransport:
    """Serve responses from ``routes``: ``[(substring_or_callable, status, headers, body)]``.

    The first route whose matcher hits wins.  Unmatched requests raise HttpError(404).
    """

    def __init__(self, routes=None):
        self.routes: list = list(routes or [])
        self.calls: list[str] = []

    def add(self, match, body=b"", status: int = 200, headers: dict | None = None) -> "FakeTransport":
        self.routes.append((match, status, headers or {}, body))
        return self

    def __call__(self, url, headers, timeout):
        self.calls.append(url)
        for match, status, hdrs, body in self.routes:
            ok = match(url) if callable(match) else (match in url)
            if ok:
                data = body(url) if callable(body) else body
                if isinstance(data, str):
                    data = data.encode("utf-8")
                if status >= 400:
                    raise HttpError(status, url, data)
                return _Raw(status, hdrs, data)
        raise HttpError(404, url, b"no fake route")
