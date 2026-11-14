"""Local file cache with atomic writes, sidecar metadata and content hashes.

Cache-first contract (README S1): look locally, download only on a miss,
persist, then return.  Every cached payload may have a ``<name>.meta.json``
sidecar recording where it came from and when, so a run can say exactly which
upstream release it used.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


class CacheMiss(Exception):
    """Raised in offline mode when a required item is not cached."""


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str | os.PathLike, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def atomic_write_bytes(path: str | os.PathLike, data: bytes) -> None:
    """Write ``data`` to ``path`` so readers never observe a partial file."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def atomic_write_text(path: str | os.PathLike, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def cache_from_config(config) -> "Cache":
    """The one place a Cache is built from configuration (``paths.cache_dir``, ``cache.*``, ``network.offline``)."""
    return Cache(config.cache_dir, offline=bool(config.get("network.offline", False)),
                 refresh=bool(config.get("cache.refresh", False)), max_age_days=config.get("cache.max_age_days"))


def safe_name(text: str, maxlen: int = 80) -> str:
    """Filesystem-safe slug (keeps letters, digits, dot, dash, underscore)."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip()).strip("_")
    return (slug or "item")[:maxlen]


class Cache:
    """Directory-backed cache.

    ``offline``  : never touch the network; a miss raises :class:`CacheMiss`.
    ``refresh``  : treat every existing entry as stale for this process.
    ``max_age_days`` : entries older than this are stale (``None`` = never expire,
                       which keeps runs reproducible against a pinned snapshot).
    """

    def __init__(self, root: str | os.PathLike, offline: bool = False, refresh: bool = False,
                 max_age_days: float | None = None):
        self.root = Path(root)
        self.offline = offline
        self.refresh = refresh
        self.max_age_days = max_age_days

    # -- paths ------------------------------------------------------------
    def path(self, *parts: str) -> Path:
        return self.root.joinpath(*parts)

    def rel(self, path: str | os.PathLike) -> str:
        """Posix path of ``path`` relative to the cache root."""
        return Path(path).resolve().relative_to(self.root.resolve()).as_posix()

    @staticmethod
    def meta_path(path: Path) -> Path:
        return path.with_name(path.name + ".meta.json")

    @staticmethod
    def key(*parts: Any) -> str:
        blob = json.dumps(parts, sort_keys=True, default=str, ensure_ascii=False)
        return hashlib.sha1(blob.encode("utf-8")).hexdigest()[:20]

    # -- freshness -------------------------------------------------------------
    def is_fresh(self, path: Path) -> bool:
        if self.refresh and not self.offline:
            return False
        if not path.is_file():
            return False
        if self.max_age_days is None:
            return True
        meta = self.read_meta(path)
        stamp = meta.get("retrieved_at")
        if stamp:
            try:
                when = datetime.strptime(stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                return datetime.now(timezone.utc) - when <= timedelta(days=self.max_age_days)
            except ValueError:
                pass
        mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
        return datetime.now(timezone.utc) - mtime <= timedelta(days=self.max_age_days)

    def usable(self, path: Path) -> bool:
        """True if the entry may be used right now (offline mode accepts stale entries)."""
        if self.offline:
            return path.is_file()
        return self.is_fresh(path)

    # -- metadata ----------------------------------------------------------------
    def read_meta(self, path: Path) -> dict:
        mp = self.meta_path(path)
        if not mp.is_file():
            return {}
        try:
            return json.loads(mp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def write_meta(self, path: Path, meta: dict) -> None:
        atomic_write_text(self.meta_path(path), json.dumps(meta, indent=2, sort_keys=True, ensure_ascii=False))

    # -- convenience IO ----------------------------------------------------------------
    def put_bytes(self, path: Path, data: bytes, meta: dict | None = None) -> None:
        atomic_write_bytes(path, data)
        m = dict(meta or {})
        m.setdefault("retrieved_at", utcnow_iso())
        m["sha256"] = sha256_bytes(data)
        m["bytes"] = len(data)
        self.write_meta(path, m)

    def get_bytes(self, path: Path) -> bytes:
        return Path(path).read_bytes()

    def require(self, path: Path, what: str) -> None:
        """Raise CacheMiss with a helpful message if offline and absent."""
        if self.offline and not path.is_file():
            raise CacheMiss(
                f"offline mode: {what} is not cached (expected at {path}). "
                f"Run `vaxforge warm-cache` while online first."
            )
