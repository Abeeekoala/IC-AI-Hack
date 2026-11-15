"""Content-addressed stage artifacts.

README section 1: "Every stage is a pure function over the previous stage's artifact.
Artifacts are content-addressed JSON written to runs/<run_id>/".

Envelope (identical for S3-S7)::

    {"schema": "vaxforge.s3_rank/1", "stage": "s3_rank", "artifact_id": "sha256:...",
     "run_id": "...", "created_at": "...Z",
     "inputs": [{"stage", "artifact_id", "path"}], "params": {...},
     "tools": [{"name", "version", "backend", "fidelity", ...}],
     "warnings": [...], "payload": {...}}

``artifact_id`` hashes everything except ``created_at`` / ``run_id`` / ``artifact_id``, so
re-running a stage with identical inputs, parameters, tool versions and results yields the
identical id. Stream A may replace this module with the repo-wide implementation; the stage
code only touches ``build_envelope`` / ``write_artifact`` / ``read_artifact``.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Iterable, Mapping


def _default(o: Any):
    if isinstance(o, Path):
        return str(o)
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    raise TypeError(f"not JSON serialisable: {type(o).__name__}")


def canonical_json(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False,
                      default=_default, allow_nan=False).encode("utf-8")


def sha256_id(obj: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(obj)).hexdigest()


def file_sha256(path: str | Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return "sha256:" + h.hexdigest()


def _sanitise_floats(o: Any) -> Any:
    """JSON has no NaN/Inf; map them to None so canonical hashing never fails."""
    if isinstance(o, float):
        return o if o == o and o not in (float("inf"), float("-inf")) else None
    if isinstance(o, Mapping):
        return {str(k): _sanitise_floats(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_sanitise_floats(v) for v in o]
    return o


def build_envelope(*, stage: str, version: int, run_id: str, inputs: Iterable[Mapping],
                   params: Mapping, tools: Iterable[Mapping], warnings: Iterable[str],
                   payload: Mapping) -> dict:
    env = {
        "schema": f"vaxforge.{stage}/{version}",
        "stage": stage,
        "inputs": [dict(i) for i in inputs],
        "params": _sanitise_floats(dict(params)),
        "tools": [_sanitise_floats(dict(t)) for t in tools],
        "warnings": list(dict.fromkeys(warnings)),   # de-duplicate, keep order
        "payload": _sanitise_floats(dict(payload)),
    }
    env["artifact_id"] = sha256_id({k: env[k] for k in ("schema", "stage", "inputs", "params", "tools", "payload")}
                                   | {"inputs": [(i.get("stage"), i.get("artifact_id")) for i in env["inputs"]]})
    env["run_id"] = run_id
    env["created_at"] = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return env


def write_artifact(run_dir: str | Path, env: Mapping) -> Path:
    run = Path(run_dir)
    run.mkdir(parents=True, exist_ok=True)
    out = run / f"{env['stage']}.json"
    fd, tmp = tempfile.mkstemp(dir=run, prefix=f".{env['stage']}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as fh:
            json.dump(env, fh, indent=2, sort_keys=False, ensure_ascii=False, default=_default, allow_nan=False)
            fh.write("\n")
        os.replace(tmp, out)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)
    return out


def run_file_ref(run_dir: str | Path, path: str | Path, **extra: Any) -> dict:
    """Stream A file reference ``{"root": "run", "path", "sha256", "bytes"}`` (docs/ARTIFACT_CONTRACT.md: file
    references are objects, never bare strings). ``sha256`` is the bare hex digest, as ``RunStore.verify_ref`` expects."""
    p = Path(path)
    ref = {"root": "run", "path": p.resolve().relative_to(Path(run_dir).resolve()).as_posix(),
           "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "bytes": p.stat().st_size}
    ref.update(extra)
    return ref


def read_artifact(path: str | Path) -> dict:
    return json.loads(Path(path).read_text())


# Stream A's stage ids (docs/ARTIFACT_CONTRACT.md); our artifacts are named after the same modules.
STAGE_IDS = {"s3_rank": "s3", "s4_filter": "s4", "s5_epitopes": "s5", "s6_coverage": "s6", "s7_structure": "s7"}


def is_store(obj: Any) -> bool:
    """Duck-typed check for Stream A's ``vaxforge.artifacts.RunStore`` (no import, so this package stays standalone)."""
    return all(hasattr(obj, a) for a in ("write_artifact", "read_artifact", "run_dir", "has", "digest_of"))


def split_target(target: Any) -> tuple[Any | None, Path]:
    """``target`` is a run directory or a ``RunStore`` -> ``(store_or_None, run_dir)``."""
    if is_store(target):
        return target, Path(target.run_dir)
    return None, Path(target)


# The upstream artifacts each stage really reads, declared explicitly so ``vaxforge verify`` flags a stage (and
# everything downstream of it) when one of them is re-run. S7 reads only S6 (S5's results reach it through S6);
# RunStore's own default for s7 is ['s4'], which left S7 un-flagged after S6 changed. S4 also reads S1's proteome files.
DECLARED_INPUTS = {"s3": ["s2"], "s4": ["s3", "s1"], "s5": ["s4"], "s6": ["s5"], "s7": ["s6"]}


def write_stage(target: Any, env: Mapping) -> Path:
    """Write a finished envelope: our own format into a plain directory, or through ``RunStore.write_artifact``
    (their envelope, digest, manifest and upstream chain) when ``target`` is a store."""
    store, run_dir = split_target(target)
    if store is None:
        return write_artifact(run_dir, env)
    sid = STAGE_IDS[env["stage"]]
    tools = {}
    for t in env["tools"]:
        tools[str(t["name"])] = str(t.get("version") or t.get("backend") or "unknown")
    store.write_artifact(sid, env["payload"], params=env["params"], tools=tools, status="ok", warnings=env["warnings"],
                         inputs=DECLARED_INPUTS[sid])
    return Path(store.artifact_path(sid))


def try_reuse_store(store: Any, stage: str, params: Mapping) -> bool:
    """Reuse an existing RunStore artifact if its params and upstream digests are unchanged."""
    sid = STAGE_IDS[stage]
    if not store.has(sid):
        return False
    try:
        a = store.read_artifact(sid)
        same_inputs = all(store.has(k) and store.digest_of(k) == v for k, v in a.inputs.items())
        return bool(same_inputs and a.status == "ok" and canonical_json(_sanitise_floats(dict(params))) == canonical_json(a.params))
    except Exception:                                                           # noqa: BLE001 - any doubt => recompute
        return False


def input_ref(path: str | Path, env: Mapping | None = None) -> dict:
    """Reference to an upstream artifact. Falls back to a file hash when the upstream stream
    does not write ``artifact_id`` (A's format is not fixed yet)."""
    p = Path(path)
    doc = env if env is not None else (read_artifact(p) if p.suffix == ".json" else None)
    aid = (doc.get("artifact_id") or doc.get("digest")) if isinstance(doc, Mapping) else None   # ours / Stream A's
    return {"stage": (doc or {}).get("stage", p.stem) if isinstance(doc, Mapping) else p.stem,
            "artifact_id": aid or file_sha256(p), "path": p.name}


def find_artifact(run_dir: str | Path, stage: str, aliases: Iterable[str] = ()) -> Path | None:
    run = Path(run_dir)
    for name in (stage, *aliases):
        cand = run / f"{name}.json"
        if cand.exists():
            return cand
    return None


def try_reuse(run_dir: str | Path, stage: str, expected_inputs: list[dict], params: Mapping) -> Path | None:
    """Return the existing artifact if it was produced from the same inputs and params."""
    p = Path(run_dir) / f"{stage}.json"
    if not p.exists():
        return None
    try:
        doc = read_artifact(p)
    except (OSError, ValueError):
        return None
    same_inputs = [(i.get("stage"), i.get("artifact_id")) for i in doc.get("inputs", [])] == \
                  [(i.get("stage"), i.get("artifact_id")) for i in expected_inputs]
    return p if same_inputs and canonical_json(_sanitise_floats(dict(params))) == canonical_json(doc.get("params")) else None
