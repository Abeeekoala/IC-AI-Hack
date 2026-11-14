"""Configuration loading for VaxForge.

``config.yaml`` is shared by all streams; each stream edits only its own
section.  Stream A owns ``paths``, ``network``, ``taxonomy``, ``acquire``,
``annotate`` and ``tools``.

PyYAML is used when importable.  When it is not (for example on a very new
Python without wheels) a small YAML-subset reader is used instead.  The subset
covers everything ``config.yaml`` needs: nested mappings, block and flow
lists, comments, quoted strings, numbers, booleans and null.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Iterable

try:  # pragma: no cover - exercised implicitly depending on environment
    import yaml as _yaml  # type: ignore
except Exception:  # pragma: no cover
    _yaml = None


class ConfigError(Exception):
    """Raised for unreadable or invalid configuration."""


# --------------------------------------------------------------------------
# Minimal YAML-subset reader
# --------------------------------------------------------------------------
_INT_RE = re.compile(r"^[-+]?\d+$")
_FLOAT_RE = re.compile(r"^[-+]?(\d+\.\d*|\.\d+|\d+)([eE][-+]?\d+)?$")


def _strip_comment(line: str) -> str:
    quote: str | None = None
    for i, ch in enumerate(line):
        if quote:
            if ch == quote:
                quote = None
            continue
        if ch in "\"'" and (i == 0 or line[i - 1] in " \t[,{:-"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i].rstrip()
    return line.rstrip()


def _parse_scalar(token: str) -> Any:
    token = token.strip()
    if token == "":
        return None
    if token[0] in "\"'" and len(token) >= 2 and token[-1] == token[0]:
        inner = token[1:-1]
        if token[0] == '"':
            inner = inner.replace('\\"', '"').replace("\\n", "\n").replace("\\\\", "\\")
        else:
            inner = inner.replace("''", "'")
        return inner
    low = token.lower()
    if low in ("null", "~"):
        return None
    if low == "true":
        return True
    if low == "false":
        return False
    if _INT_RE.match(token):
        return int(token)
    if _FLOAT_RE.match(token):
        return float(token)
    if token.startswith("[") or token.startswith("{"):
        return _parse_flow(token)
    return token


def _split_flow(body: str) -> list[str]:
    parts: list[str] = []
    depth = 0
    quote: str | None = None
    cur: list[str] = []
    for ch in body:
        if quote:
            cur.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in "\"'":
            quote = ch
            cur.append(ch)
        elif ch in "[{":
            depth += 1
            cur.append(ch)
        elif ch in "]}":
            depth -= 1
            cur.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    tail = "".join(cur)
    if tail.strip() != "":
        parts.append(tail)
    return parts


def _parse_flow(token: str) -> Any:
    token = token.strip()
    if token.startswith("["):
        if not token.endswith("]"):
            raise ConfigError(f"unterminated flow list: {token!r}")
        return [_parse_scalar(p) for p in _split_flow(token[1:-1])]
    if token.startswith("{"):
        if not token.endswith("}"):
            raise ConfigError(f"unterminated flow mapping: {token!r}")
        out: dict[str, Any] = {}
        for part in _split_flow(token[1:-1]):
            k, _, v = part.partition(":")
            out[str(_parse_scalar(k))] = _parse_scalar(v)
        return out
    return _parse_scalar(token)


def _split_key(text: str) -> tuple[str, str] | None:
    """Split ``key: value`` honouring a quoted key. Returns None if not a mapping line."""
    if text and text[0] in "\"'":
        q = text[0]
        end = text.find(q, 1)
        if end == -1:
            return None
        rest = text[end + 1:]
        if rest.startswith(":") and (len(rest) == 1 or rest[1] == " "):
            return text[1:end], rest[1:].strip()
        return None
    m = re.match(r"^([^:\s][^:]*?):(?:\s+(.*))?$", text)
    if not m:
        return None
    return m.group(1).strip(), (m.group(2) or "").strip()


def _parse_block(lines: list[tuple[int, str]], idx: int, indent: int) -> tuple[Any, int]:
    if lines[idx][1].startswith("- ") or lines[idx][1] == "-":
        items: list[Any] = []
        while idx < len(lines) and lines[idx][0] == indent and (
            lines[idx][1].startswith("- ") or lines[idx][1] == "-"
        ):
            text = lines[idx][1][1:].strip()
            if text == "":
                if idx + 1 < len(lines) and lines[idx + 1][0] > indent:
                    val, idx = _parse_block(lines, idx + 1, lines[idx + 1][0])
                    items.append(val)
                else:
                    items.append(None)
                    idx += 1
            elif _split_key(text) is not None and not text.startswith(("[", "{")):
                # "- key: value" starts an inline mapping item
                lines[idx] = (indent + 2, text)
                val, idx = _parse_block(lines, idx, indent + 2)
                items.append(val)
            else:
                items.append(_parse_scalar(text))
                idx += 1
        return items, idx

    mapping: dict[str, Any] = {}
    while idx < len(lines) and lines[idx][0] == indent:
        text = lines[idx][1]
        kv = _split_key(text)
        if kv is None:
            raise ConfigError(f"cannot parse config line: {text!r}")
        key, value = kv
        if value == "":
            if idx + 1 < len(lines) and lines[idx + 1][0] > indent:
                val, idx = _parse_block(lines, idx + 1, lines[idx + 1][0])
                mapping[key] = val
            elif idx + 1 < len(lines) and lines[idx + 1][0] == indent and (
                lines[idx + 1][1].startswith("- ") or lines[idx + 1][1] == "-"
            ):
                val, idx = _parse_block(lines, idx + 1, indent)
                mapping[key] = val
            else:
                mapping[key] = None
                idx += 1
        else:
            mapping[key] = _parse_scalar(value)
            idx += 1
    return mapping, idx


def mini_yaml_load(text: str) -> Any:
    lines: list[tuple[int, str]] = []
    for raw in text.splitlines():
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise ConfigError("tabs are not allowed for indentation")
        stripped = _strip_comment(raw)
        if stripped.strip() == "":
            continue
        indent = len(stripped) - len(stripped.lstrip(" "))
        lines.append((indent, stripped.strip()))
    if not lines:
        return {}
    value, idx = _parse_block(lines, 0, lines[0][0])
    if idx != len(lines):
        raise ConfigError(f"unexpected indentation near: {lines[idx][1]!r}")
    return value


def load_yaml_text(text: str) -> Any:
    if _yaml is not None:
        return _yaml.safe_load(text) or {}
    return mini_yaml_load(text)


# --------------------------------------------------------------------------
# Config object
# --------------------------------------------------------------------------
_SECRET_KEYS = {"api_key", "email"}
# Sections that change *how/where* we run, not *what* the science is.
DIGEST_EXCLUDED_SECTIONS = ("paths", "network", "tools")


def _deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = copy.deepcopy(v)
    return out


def _redact(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {k: ("***" if k in _SECRET_KEYS and v else _redact(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


class Config:
    """Dotted-key access over the merged configuration dictionary."""

    def __init__(self, data: dict | None = None, source: Path | None = None, root: Path | None = None):
        self._data: dict = copy.deepcopy(data or {})
        self.source = Path(source) if source else None
        self.root = Path(root) if root else (self.source.parent if self.source else Path.cwd())

    # -- access ---------------------------------------------------------
    @property
    def raw(self) -> dict:
        return self._data

    def get(self, dotted: str, default: Any = None) -> Any:
        cur: Any = self._data
        for part in dotted.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return default if cur is None and default is not None else cur

    def section(self, name: str) -> dict:
        val = self._data.get(name)
        return val if isinstance(val, dict) else {}

    def set(self, dotted: str, value: Any) -> None:
        parts = dotted.split(".")
        cur = self._data
        for part in parts[:-1]:
            if not isinstance(cur.get(part), dict):
                cur[part] = {}
            cur = cur[part]
        cur[parts[-1]] = value

    def path(self, dotted: str, default: str) -> Path:
        """Resolve a path setting relative to the config root."""
        val = self.get(dotted, default)
        p = Path(os.path.expandvars(os.path.expanduser(str(val))))
        return p if p.is_absolute() else (self.root / p)

    # -- conveniences -----------------------------------------------------
    @property
    def cache_dir(self) -> Path:
        return self.path("paths.cache_dir", "cache")

    @property
    def runs_dir(self) -> Path:
        return self.path("paths.runs_dir", "runs")

    @property
    def offline(self) -> bool:
        return bool(self.get("network.offline", False))

    # -- hashing / snapshots --------------------------------------------------
    def redacted(self) -> dict:
        return _redact(copy.deepcopy(self._data))

    def digest(self, exclude: Iterable[str] = DIGEST_EXCLUDED_SECTIONS) -> str:
        data = {k: v for k, v in self.redacted().items() if k not in set(exclude)}
        blob = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
        return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()

    def copy(self) -> "Config":
        return Config(self._data, self.source, self.root)


def find_config_file(explicit: str | os.PathLike | None = None) -> Path | None:
    if explicit:
        p = Path(explicit)
        if not p.is_file():
            raise ConfigError(f"config file not found: {p}")
        return p
    env = os.environ.get("VAXFORGE_CONFIG")
    if env:
        p = Path(env)
        if not p.is_file():
            raise ConfigError(f"VAXFORGE_CONFIG points to a missing file: {p}")
        return p
    cur = Path.cwd().resolve()
    for d in [cur, *cur.parents]:
        cand = d / "config.yaml"
        if cand.is_file():
            return cand
    pkg_root = Path(__file__).resolve().parents[2] / "config.yaml"  # <repo>/src/vaxforge/config.py
    return pkg_root if pkg_root.is_file() else None


def load_config(path: str | os.PathLike | None = None, overrides: dict | None = None) -> Config:
    """Load ``config.yaml`` (searched upward from cwd), apply env overrides, then ``overrides``."""
    file = find_config_file(path)
    data: dict = {}
    if file is not None:
        try:
            parsed = load_yaml_text(file.read_text(encoding="utf-8"))
        except ConfigError:
            raise
        except Exception as exc:
            raise ConfigError(f"could not parse {file}: {exc}") from exc
        if not isinstance(parsed, dict):
            raise ConfigError(f"{file} must contain a mapping at the top level")
        data = parsed
    cfg = Config(data, source=file, root=file.parent if file else Path.cwd())

    env = os.environ
    if env.get("VAXFORGE_CACHE_DIR"):
        cfg.set("paths.cache_dir", env["VAXFORGE_CACHE_DIR"])
    if env.get("VAXFORGE_RUNS_DIR"):
        cfg.set("paths.runs_dir", env["VAXFORGE_RUNS_DIR"])
    if env.get("VAXFORGE_OFFLINE", "").lower() in ("1", "true", "yes"):
        cfg.set("network.offline", True)
    if env.get("NCBI_API_KEY"):
        cfg.set("network.ncbi.api_key", env["NCBI_API_KEY"])
    if env.get("NCBI_EMAIL"):
        cfg.set("network.ncbi.email", env["NCBI_EMAIL"])
    if env.get("BLAST_BIN"):
        cfg.set("tools.blast_bin_dir", env["BLAST_BIN"])

    if overrides:
        for k, v in overrides.items():
            cfg.set(k, v)
    return cfg
