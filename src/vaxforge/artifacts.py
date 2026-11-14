"""The ``runs/<run_id>/`` artifact contract (owned by stream A).

Every stage is a pure function over the previous stage's artifact.  Artifacts
are content-addressed JSON; any claim in a final report can be traced to the
exact tool versions and parameters recorded here.

Layout::

    runs/<run_id>/
        manifest.json            run-level index (stage -> digest/status)
        config.resolved.json     the exact configuration used (secrets redacted)
        s0_taxonomy.json         one envelope per stage, named after its module
        s1_acquire.json
        s2_annotate.json
        ...
        data/                    sidecar files (FASTA, TSV) referenced by file refs

Envelope (every ``sN_*.json``)::

    {
      "schema":      "vaxforge/s2_annotate/v1",
      "stage":       "s2",
      "run_id":      "nmb",
      "created_at":  "2026-11-14T10:00:00Z",       # NOT part of the digest
      "environment": {"python": "...", "platform": "..."},   # NOT part of the digest
      "producer":    {"module": "vaxforge.s2_annotate", "vaxforge_version": "0.1.0",
                      "tools": {"blastp": "2.17.0+"}},
      "params":      {...},                          # everything that influenced the result
      "inputs":      {"s0": "sha256:...", "s1": "sha256:..."},   # upstream digests
      "status":      "ok" | "needs_user_input" | "failed",
      "warnings":    ["..."],
      "payload":     {...},                          # stage-specific, see docs/ARTIFACT_CONTRACT.md
      "volatile":    {"cache_hits": {...}},          # how this run got its data; NOT part of the digest
      "digest":      "sha256:..."                    # over schema, stage, producer, params,
    }                                                #   inputs, status, warnings, payload

The digest covers *what was computed*, never *how it was fetched*: re-running a stage against a warm cache
must not invalidate the stages downstream of it, so cache hits, timings and the like go in ``volatile``.

File references inside payloads are objects, never bare strings::

    {"root": "run" | "cache", "path": "data/proteome.faa", "sha256": "...", "bytes": 123}

``root`` says what the path is relative to: the run directory or the cache directory.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
import platform
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import __version__
from .cache import atomic_write_text, sha256_file, utcnow_iso

SCHEMA_VERSION = 1
VALID_STATUS = ("ok", "needs_user_input", "failed")

# stage id -> module name (also the artifact basename)
STAGES: dict[str, str] = {
    "s0": "s0_taxonomy",
    "s1": "s1_acquire",
    "s2": "s2_annotate",
    "s3": "s3_rank",
    "s4": "s4_filter",
    "s5": "s5_epitopes",
    "s6": "s6_coverage",
    "s7": "s7_structure",
    "s8": "s8_protocol",
    "s9": "s9_robot",
    "s9plus": "s9plus_animate",
    "s9_5": "s9_5_finetune",          # README S9.5 (stream D): fine-tuned codegen beside S9, before S10
    "s10": "s10_animal",
    "s11": "s11_prereg",
    "s12": "s12_biosafety",
}
STAGE_ORDER = list(STAGES)

# Upstream artifacts a stage reads.  s1/s2 are binding; the rest are the
# suggested default and are used only when a writer does not pass ``inputs``.
DEFAULT_INPUTS: dict[str, list[str]] = {
    "s0": [],
    "s1": ["s0"],
    "s2": ["s0", "s1"],
    "s3": ["s2"],
    "s4": ["s3", "s1"],
    "s5": ["s4"],
    "s6": ["s5"],
    "s7": ["s4"],
    "s8": ["s4", "s5", "s6", "s7"],
    "s9": ["s8"],
    "s9plus": ["s9"],
    "s9_5": ["s8", "s9"],             # README: input is the S8 protocol plus S9's output
    "s10": ["s4"],
    "s11": ["s4"],
    "s12": ["s8"],
}

# Minimal structural check for the stages stream A owns.  Other streams are
# welcome to register theirs via ``register_payload_keys``.
REQUIRED_PAYLOAD_KEYS: dict[str, tuple[str, ...]] = {
    "s0": ("query", "resolution", "taxon", "organism", "candidates"),
    "s1": ("taxid", "proteome", "comparison_proteome", "reference_dbs", "blast_dbs", "resource_status"),
    "s2": ("proteome", "thresholds", "tools", "summary", "columns", "proteins"),
}


class ContractError(Exception):
    """An artifact violates the contract (bad digest, missing key, broken chain...)."""


def register_payload_keys(stage: str, keys: Iterable[str]) -> None:
    REQUIRED_PAYLOAD_KEYS[stage] = tuple(keys)


def canonical_json(obj: Any) -> str:
    """Deterministic JSON used for hashing (sorted keys, no whitespace, no NaN)."""
    try:
        return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)
    except ValueError as exc:
        raise ContractError(f"payload contains NaN/Infinity, which is not valid JSON: {exc}") from exc


def compute_digest(envelope: Mapping[str, Any]) -> str:
    body = {k: envelope.get(k) for k in ("schema", "stage", "producer", "params", "inputs", "status", "warnings", "payload")}
    return "sha256:" + hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()


def validate_payload(stage: str, payload: Mapping[str, Any]) -> None:
    need = REQUIRED_PAYLOAD_KEYS.get(stage, ())
    missing = [k for k in need if k not in payload]
    if missing:
        raise ContractError(f"{stage} payload is missing required keys: {', '.join(missing)}")


@dataclass
class Artifact:
    stage: str
    path: Path
    data: dict

    @property
    def payload(self) -> dict:
        return self.data["payload"]

    @property
    def digest(self) -> str:
        return self.data["digest"]

    @property
    def status(self) -> str:
        return self.data["status"]

    @property
    def warnings(self) -> list[str]:
        return list(self.data.get("warnings") or [])

    @property
    def inputs(self) -> dict[str, str]:
        return dict(self.data.get("inputs") or {})

    @property
    def params(self) -> dict:
        return self.data.get("params") or {}

    @property
    def producer(self) -> dict:
        return self.data.get("producer") or {}

    @property
    def volatile(self) -> dict:
        return self.data.get("volatile") or {}

    def require_ok(self) -> "Artifact":
        """Raise ContractError unless this stage finished with status ``ok`` (use at the top of a downstream stage)."""
        if self.status != "ok":
            why = (self.warnings or ["no details recorded"])[0]
            raise ContractError(f"{self.stage} artifact has status {self.status!r}: {why}")
        return self


def file_ref(path: str | os.PathLike, root: str, base: str | os.PathLike, **extra: Any) -> dict:
    """Build a file reference for ``path`` located under ``base`` (the run or cache dir)."""
    p = Path(path)
    rel = p.resolve().relative_to(Path(base).resolve()).as_posix()
    ref = {"root": root, "path": rel, "sha256": sha256_file(p), "bytes": p.stat().st_size}
    ref.update(extra)
    return ref


class RunStore:
    """Reads and writes the artifacts of a single run directory."""

    def __init__(self, run_dir: str | os.PathLike, run_id: str | None = None, cache_dir: str | os.PathLike | None = None):
        self.run_dir = Path(run_dir)
        self.run_dir.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or self.run_dir.name
        self.cache_dir = Path(cache_dir) if cache_dir else None

    # -- paths ------------------------------------------------------------
    @staticmethod
    def artifact_name(stage: str) -> str:
        if stage not in STAGES:
            raise ContractError(f"unknown stage {stage!r}; known: {', '.join(STAGES)}")
        return STAGES[stage] + ".json"

    def artifact_path(self, stage: str) -> Path:
        return self.run_dir / self.artifact_name(stage)

    @property
    def manifest_path(self) -> Path:
        return self.run_dir / "manifest.json"

    def data_path(self, *parts: str) -> Path:
        p = self.run_dir.joinpath("data", *parts)
        p.parent.mkdir(parents=True, exist_ok=True)
        return p

    def has(self, stage: str) -> bool:
        return self.artifact_path(stage).is_file()

    def stages_present(self) -> list[str]:
        return [s for s in STAGE_ORDER if self.has(s)]

    # -- file refs ------------------------------------------------------------
    def run_ref(self, path: str | os.PathLike, **extra: Any) -> dict:
        return file_ref(path, "run", self.run_dir, **extra)

    def cache_ref(self, path: str | os.PathLike, **extra: Any) -> dict:
        if self.cache_dir is None:
            raise ContractError("RunStore has no cache_dir; cannot build cache file references")
        return file_ref(path, "cache", self.cache_dir, **extra)

    def resolve(self, ref: Mapping[str, Any], cache_dir: str | os.PathLike | None = None) -> Path:
        """Absolute path for a file reference (does not check the hash; see :meth:`verify_ref`)."""
        root = ref.get("root")
        rel = ref.get("path")
        if not isinstance(rel, str) or root not in ("run", "cache"):
            raise ContractError(f"malformed file reference: {dict(ref)!r}")
        if root == "run":
            return self.run_dir / rel
        base = cache_dir or self.cache_dir
        if base is None:
            raise ContractError("cache directory unknown: pass cache_dir to RunStore or resolve()")
        return Path(base) / rel

    def verify_ref(self, ref: Mapping[str, Any], cache_dir: str | os.PathLike | None = None) -> str | None:
        """Return None if the referenced file exists and matches its hash, else a problem string."""
        try:
            p = self.resolve(ref, cache_dir)
        except ContractError as exc:
            return str(exc)
        if not p.is_file():
            return f"missing file: {p}"
        want = ref.get("sha256")
        if want and sha256_file(p) != want:
            return f"hash mismatch: {p}"
        return None

    # -- reading ------------------------------------------------------------------
    def read_artifact(self, stage: str, verify: bool = True) -> Artifact:
        path = self.artifact_path(stage)
        if not path.is_file():
            raise ContractError(f"{stage} artifact not found at {path}; run that stage first")
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except ValueError as exc:
            raise ContractError(f"{path} is not valid JSON: {exc}") from exc
        if verify:
            want = data.get("digest")
            got = compute_digest(data)
            if want != got:
                raise ContractError(f"{path}: digest mismatch (file edited or corrupted): stored {want}, computed {got}")
        return Artifact(stage, path, data)

    def digest_of(self, stage: str) -> str:
        return self.read_artifact(stage, verify=False).digest

    # -- writing --------------------------------------------------------------------
    def write_artifact(
        self,
        stage: str,
        payload: Mapping[str, Any],
        *,
        params: Mapping[str, Any] | None = None,
        inputs: Iterable[str] | None = None,
        tools: Mapping[str, Any] | None = None,
        status: str = "ok",
        warnings: Iterable[str] | None = None,
        module: str | None = None,
        volatile: Mapping[str, Any] | None = None,
    ) -> Artifact:
        if status not in VALID_STATUS:
            raise ContractError(f"status must be one of {VALID_STATUS}, got {status!r}")
        if status == "ok":
            validate_payload(stage, payload)
        upstream = list(DEFAULT_INPUTS.get(stage, []) if inputs is None else inputs)
        input_digests: dict[str, str] = {}
        for up in upstream:
            if not self.has(up):
                raise ContractError(f"cannot write {stage}: upstream artifact {up} is missing")
            input_digests[up] = self.digest_of(up)

        envelope: dict[str, Any] = {
            "schema": f"vaxforge/{STAGES[stage]}/v{SCHEMA_VERSION}",
            "stage": stage,
            "run_id": self.run_id,
            "created_at": utcnow_iso(),
            "environment": {"python": sys.version.split()[0], "platform": platform.platform()},
            "producer": {
                "module": module or f"vaxforge.{STAGES[stage]}",
                "vaxforge_version": __version__,
                "tools": dict(sorted((tools or {}).items())),
            },
            "params": copy.deepcopy(dict(params or {})),
            "inputs": input_digests,
            "status": status,
            "warnings": [str(w) for w in (warnings or [])],
            "payload": copy.deepcopy(dict(payload)),
            "volatile": copy.deepcopy(dict(volatile or {})),
        }
        envelope["digest"] = compute_digest(envelope)
        text = json.dumps(envelope, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        atomic_write_text(self.artifact_path(stage), text + "\n")
        self._update_manifest(stage, envelope)
        return Artifact(stage, self.artifact_path(stage), envelope)

    def snapshot_config(self, config) -> Path:
        """Write ``config.resolved.json`` (secrets redacted) and remember its digest."""
        path = self.run_dir / "config.resolved.json"
        atomic_write_text(path, json.dumps(config.redacted(), indent=2, sort_keys=True, ensure_ascii=False, default=str) + "\n")
        manifest = self.read_manifest()
        manifest["config_digest"] = config.digest()
        self._write_manifest(manifest)
        return path

    # -- manifest ------------------------------------------------------------------------
    def read_manifest(self) -> dict:
        if self.manifest_path.is_file():
            try:
                return json.loads(self.manifest_path.read_text(encoding="utf-8"))
            except ValueError:
                pass
        return {"run_id": self.run_id, "created_at": utcnow_iso(), "vaxforge_version": __version__, "stages": {}}

    def _write_manifest(self, manifest: dict) -> None:
        atomic_write_text(self.manifest_path, json.dumps(manifest, indent=2, sort_keys=True, ensure_ascii=False) + "\n")

    def _update_manifest(self, stage: str, envelope: Mapping[str, Any]) -> None:
        manifest = self.read_manifest()
        manifest.setdefault("stages", {})[stage] = {
            "artifact": self.artifact_name(stage),
            "digest": envelope["digest"],
            "status": envelope["status"],
            "finished_at": envelope["created_at"],
            "warnings": len(envelope.get("warnings") or []),
        }
        manifest["run_id"] = self.run_id
        manifest["vaxforge_version"] = __version__
        self._write_manifest(manifest)

    # -- integrity -------------------------------------------------------------------------
    def verify(self, cache_dir: str | os.PathLike | None = None, check_files: bool = True) -> list[str]:
        """Return a list of problems (empty list = run is internally consistent).

        Checks: each artifact's own digest, the manifest, that each artifact's
        recorded upstream digests still match the upstream artifacts on disk
        (so a re-run upstream flags stale downstream work), and optionally that
        every referenced file exists with the recorded hash.
        """
        problems: list[str] = []
        loaded: dict[str, Artifact] = {}
        for stage in self.stages_present():
            try:
                loaded[stage] = self.read_artifact(stage, verify=True)
            except ContractError as exc:
                problems.append(str(exc))
        manifest = self.read_manifest()
        for stage, art in loaded.items():
            m = manifest.get("stages", {}).get(stage)
            if m is None:
                problems.append(f"{stage}: artifact present but absent from manifest.json")
            elif m.get("digest") != art.digest:
                problems.append(f"{stage}: manifest digest differs from artifact digest")
            for up, dig in art.inputs.items():
                if up not in loaded:
                    problems.append(f"{stage}: upstream {up} missing")
                elif loaded[up].digest != dig:
                    problems.append(f"{stage}: STALE - built from {up} {dig[:19]}..., but {up} is now {loaded[up].digest[:19]}...")
            if check_files:
                for ref in iter_file_refs(art.payload):
                    prob = self.verify_ref(ref, cache_dir)
                    if prob:
                        problems.append(f"{stage}: {prob}")
        return problems


def iter_file_refs(obj: Any) -> Iterable[dict]:
    """Yield every file-reference dict found anywhere inside ``obj``."""
    if isinstance(obj, dict):
        if obj.get("root") in ("run", "cache") and isinstance(obj.get("path"), str) and "sha256" in obj:
            yield obj
        else:
            for v in obj.values():
                yield from iter_file_refs(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_file_refs(v)
