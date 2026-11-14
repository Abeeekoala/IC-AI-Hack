"""Structure predictors for S7: Boltz-2 (primary) and two independent "refolders".

Boltz-2 facts verified from its docs/prediction.md:
  * ``boltz predict INPUT.yaml --out_dir DIR [--accelerator gpu|cpu|tpu] [--recycling_steps N]
    [--sampling_steps N] [--diffusion_samples N] [--output_format pdb|mmcif] [--use_msa_server]
    [--use_potentials]``; without ``--use_msa_server`` an MSA must be supplied (or ``msa: empty``
    single-sequence mode, which the docs say reduces accuracy).
  * Outputs ``*_model_0.{pdb,cif}`` and ``confidence_*_model_0.json`` with ``confidence_score``,
    ``ptm``, ``complex_plddt`` ... all in [0, 1].
  * The affinity module only supports protein - **small-molecule** binding; it is not applicable
    to an antigen monomer, so this project emits no affinity number (see docs).
  * No seed option is documented, so runs are not seed-reproducible; results are cached by content.

Privacy: ``--use_msa_server`` (ColabFold MMseqs2 server) and the ESM Atlas API send the *sequence*
to a third-party server. Every such use is flagged on the tool record (``sends_sequence_off_machine``).
"""
from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

from .errors import ToolFailed, ToolUnavailable
from .fasta import write_fasta
from .tools import package_version, run_cmd


@dataclass
class FoldResult:
    pdb: Path
    meta: dict = field(default_factory=dict)


def rewrite_bfactors(src: Path, dst: Path, factor: float) -> Path:
    """Copy a PDB scaling the B-factor column (DiscoTope-3.0 expects AF2-style 0-100 pLDDT there)."""
    lines = []
    for ln in Path(src).read_text().splitlines():
        if ln.startswith(("ATOM", "HETATM")) and len(ln) >= 66:
            try:
                b = float(ln[60:66]) * factor
                ln = f"{ln[:60]}{b:6.2f}{ln[66:]}"
            except ValueError:
                pass
        lines.append(ln)
    Path(dst).write_text("\n".join(lines) + "\n")
    return Path(dst)


def _sha(*parts: str) -> str:
    h = hashlib.sha256()
    for p in parts:
        h.update(p.encode())
        h.update(b"\0")
    return h.hexdigest()[:24]


class BoltzRunner:
    name = "Boltz-2"
    fidelity = "reference"

    def __init__(self, argv_prefix: Sequence[str], *, accelerator: str = "gpu", msa: str = "server",
                 recycling_steps: int = 3, sampling_steps: int = 200, diffusion_samples: int = 1,
                 use_potentials: bool = False, timeout: float = 7200, extra_args: Sequence[str] = (),
                 cache_dir: str | Path = "cache"):
        self.argv = list(argv_prefix)
        self.accelerator = ("gpu" if shutil.which("nvidia-smi") else "cpu") if accelerator == "auto" else accelerator
        self.msa = msa
        self.recycling, self.sampling, self.samples = recycling_steps, sampling_steps, diffusion_samples
        self.potentials, self.timeout, self.extra = use_potentials, timeout, list(extra_args)
        self.cache = Path(cache_dir) / "boltz"
        self._version: str | None = None

    @property
    def version(self) -> str | None:
        if self._version is None:
            # the Boltz CLI has no --version flag (it prints usage), so read the installed package's metadata
            self._version = package_version(self.argv[0], "boltz") or "unknown"
        return self._version

    @property
    def sends_sequence_off_machine(self) -> bool:
        return self.msa == "server"

    def _yaml(self, seq: str) -> str:
        lines = ["version: 1", "sequences:", "  - protein:", "      id: A", f"      sequence: {seq}"]
        if self.msa == "empty":
            lines.append("      msa: empty")
        return "\n".join(lines) + "\n"

    def fold(self, pid: str, seq: str, outdir: Path) -> FoldResult:
        key = _sha(seq, self.msa, str(self.recycling), str(self.sampling), str(self.samples), str(self.potentials), self.version or "")
        cdir = self.cache / key
        pdb_c, conf_c = cdir / "model_0.pdb", cdir / "confidence.json"
        if not (pdb_c.exists() and conf_c.exists()):
            with tempfile.TemporaryDirectory(prefix="vf_boltz_") as td:
                tdp = Path(td)
                (tdp / "input.yaml").write_text(self._yaml(seq))
                args = self.argv + ["predict", str(tdp / "input.yaml"), "--out_dir", str(tdp / "out"),
                                    "--accelerator", self.accelerator, "--recycling_steps", str(self.recycling),
                                    "--sampling_steps", str(self.sampling), "--diffusion_samples", str(self.samples),
                                    "--output_format", "pdb"]
                if self.msa == "server":
                    args.append("--use_msa_server")
                if self.potentials:
                    args.append("--use_potentials")
                run_cmd(args + self.extra, timeout=self.timeout, what="boltz predict")
                pdbs = sorted((tdp / "out").rglob("*_model_0.pdb"))
                confs = sorted((tdp / "out").rglob("confidence_*_model_0.json"))
                if not pdbs or not confs:
                    raise ToolFailed("boltz predict finished but wrote no *_model_0.pdb / confidence_*_model_0.json")
                cdir.mkdir(parents=True, exist_ok=True)
                shutil.copy(pdbs[0], pdb_c)
                shutil.copy(confs[0], conf_c)
        conf = json.loads(conf_c.read_text())
        outdir.mkdir(parents=True, exist_ok=True)
        dst = outdir / "boltz2.pdb"
        shutil.copy(pdb_c, dst)
        (outdir / "boltz2_confidence.json").write_text(json.dumps(conf, indent=2))
        return FoldResult(dst, {"confidence": conf, "cache_key": key})


class ESMFoldRunner:
    """ESMFold via the public ESM Atlas API (sequence leaves the machine) or a local ``transformers`` install."""

    name = "ESMFold"
    fidelity = "reference"

    def __init__(self, *, mode: str = "api", api_url: str = "https://api.esmatlas.com/foldSequence/v1/pdb/",
                 max_residues_api: int = 400, timeout: float = 600):
        if mode not in ("api", "local"):
            raise ValueError("mode must be 'api' or 'local'")
        self.mode, self.url, self.max_api, self.timeout = mode, api_url, max_residues_api, timeout
        self.version = f"esmfold_v1 ({mode})"

    @property
    def sends_sequence_off_machine(self) -> bool:
        return self.mode == "api"

    def fold(self, pid: str, seq: str, outdir: Path) -> FoldResult:
        outdir.mkdir(parents=True, exist_ok=True)
        dst = outdir / "esmfold.pdb"
        if self.mode == "api":
            if len(seq) > self.max_api:
                raise ToolUnavailable(f"ESM Atlas API accepts <= {self.max_api} residues; use esmfold mode 'local'")
            req = urllib.request.Request(self.url, data=seq.encode(), method="POST",
                                         headers={"Content-Type": "text/plain", "User-Agent": "vaxforge-prediction-core/0.1"})
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    pdb = r.read().decode()
            except (urllib.error.URLError, TimeoutError) as e:
                raise ToolFailed(f"ESM Atlas API call failed: {e}") from e
        else:
            try:                                           # UNVERIFIED in this repo: HF transformers ESMFold API
                from transformers import EsmForProteinFolding  # type: ignore
            except ImportError as e:
                raise ToolUnavailable("transformers (with torch) is not installed; needed for local ESMFold") from e
            model = EsmForProteinFolding.from_pretrained("facebook/esmfold_v1").eval()
            pdb = model.infer_pdb(seq)
        if "ATOM" not in pdb:
            raise ToolFailed("ESMFold returned no ATOM records")
        dst.write_text(pdb)
        return FoldResult(dst, {})


class ColabFoldSingleSeqRunner:
    """AlphaFold2 without an MSA via ``colabfold_batch --msa-mode single_sequence`` (the README's 'af2-no-msa')."""

    name = "AF2-no-MSA (ColabFold single_sequence)"
    fidelity = "reference"
    sends_sequence_off_machine = False

    def __init__(self, argv_prefix: Sequence[str], *, num_recycle: int = 3, timeout: float = 7200):
        self.argv, self.num_recycle, self.timeout = list(argv_prefix), num_recycle, timeout
        self.version = package_version(self.argv[0], "colabfold") or "colabfold_batch"

    def fold(self, pid: str, seq: str, outdir: Path) -> FoldResult:
        outdir.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="vf_colabfold_") as td:
            fa = write_fasta(Path(td) / "in.fasta", [("query", seq)])
            run_cmd(self.argv + ["--msa-mode", "single_sequence", "--num-models", "1", "--num-recycle", str(self.num_recycle),
                                 str(fa), str(Path(td) / "out")], timeout=self.timeout, what="colabfold_batch")
            pdbs = sorted((Path(td) / "out").glob("*rank_001*.pdb"))
            if not pdbs:
                raise ToolFailed("colabfold_batch wrote no *rank_001*.pdb")
            dst = outdir / "af2_no_msa.pdb"
            shutil.copy(pdbs[0], dst)
        return FoldResult(dst, {})
