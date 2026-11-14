"""Deterministic stand-ins for external tools, used only by the test-suite.

Everything here is SYNTHETIC. None of it is, or should ever be presented as, real predictor output.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import numpy as np

from vaxforge.prediction.bepipred import ResidueScores
from vaxforge.prediction.discotope import DiscoTopeResidue
from vaxforge.prediction.folders import FoldResult
from vaxforge.prediction.netmhc import Binding
from vaxforge.s3_rank import ProtegenicityResult
from vaxforge.s4_filter import Gate, GateResult

AA = "ACDEFGHIKLMNPQRSTVWY"


def synth_seq(seed: int, n: int) -> str:
    rng = random.Random(seed)
    return "".join(rng.choice(AA) for _ in range(n))


def make_s2(path: Path, n: int = 12, *, with_meta: bool = True) -> Path:
    """A synthetic S2 artifact: ``SYN001..`` proteins with rotating localisation labels."""
    locs = ["Extracellular", "OuterMembrane", "Periplasmic", "Cytoplasmic", "Unknown", "CytoplasmicMembrane"]
    recs = []
    for i in range(n):
        rec = {
            "protein_id": f"SYN{i + 1:03d}", "name": f"synthetic protein {i + 1}", "gene": f"syn{i + 1}",
            "taxid": 487, "sequence": synth_seq(100 + i, 120 + 15 * i),
            "localisation": {"label": locs[i % len(locs)], "score": 9.5 - 0.1 * i},
        }
        if i % 4 == 0:
            rec["virulence"] = {"hit": f"VFG{i:04d}", "identity": 62.0 + i, "coverage": 88.0, "evalue": 1e-30}
        recs.append(rec)
    doc = {"schema": "vaxforge.s2_annotate/1", "stage": "s2_annotate", "artifact_id": "sha256:fake-s2",
           "payload": {"proteins": recs, **({"taxid": 487, "species_taxid": 487, "organism": "Synthetic organism",
                                              "lineage": ["Bacteria", "Pseudomonadota", "Betaproteobacteria"]} if with_meta else {})}}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc))
    return path


class FakeScores:
    """Protegenicity backend returning fixed scores (descending by protein index)."""
    name = "scores_file"

    def __init__(self, scores: dict[str, float] | None = None, *, holdout_enforced=None):
        self._scores, self._holdout = scores, holdout_enforced

    def score(self, proteins, organism_type, workdir) -> ProtegenicityResult:
        scores = self._scores or {p.protein_id: max(0.0, 0.99 - 0.06 * i) for i, p in enumerate(proteins)}
        return ProtegenicityResult(
            scores, {k: v * 100 for k, v in scores.items()},
            {"name": "fake-protegenicity", "version": "0", "backend": "fake", "fidelity": "alternative"},
            {"model": "fake", "holdout_enforced": self._holdout, "declared_holdout_taxids": None, "note": "test"}, [])


class FakeGate(Gate):
    def __init__(self, name: str, reject: set[str] = frozenset(), flag: set[str] = frozenset(), unavailable: bool = False):
        self.name, self._rej, self._flag, self._unavail = name, set(reject), set(flag), unavailable

    def thresholds(self):
        return {"fake": True}

    def tool_info(self):
        return {"name": f"fake-{self.name}", "version": "0", "backend": "fake", "fidelity": "alternative"}

    def evaluate(self, cands):
        from vaxforge.prediction.errors import ToolUnavailable
        if self._unavail:
            raise ToolUnavailable("fake gate unavailable")
        out = {}
        for c in cands:
            pid = c["protein_id"]
            st = "reject" if pid in self._rej else "flag" if pid in self._flag else "pass"
            out[pid] = GateResult(st, f"fake {st}", {"fake": True})
        return out


class FakeBinder:
    """Class I / II binder: every window whose start % 7 == 0 binds a rotating subset of the panel."""

    def __init__(self, kind: str = "I"):
        self.kind, self.name = kind, f"fake-binder-{kind}"
        self.fidelity = "reference"
        self.calls = []

    def predict(self, seqs, alleles, lengths, **kw):
        self.calls.append({"n_seqs": len(seqs), "alleles": list(alleles), "lengths": list(lengths), **kw})
        out = []
        L = lengths[0]
        for pid, seq in seqs.items():
            for start in range(0, len(seq) - L + 1, 7):
                core = seq[start:start + 9]
                pep = seq[start:start + L]
                for k, a in enumerate(alleles):
                    if (start // 7 + k) % 3 == 0:
                        rank = 0.1 + 0.05 * (k % 8)
                        out.append(Binding(pid, start + 1, pep, a, core, rank, 0.9, "SB" if rank < 0.5 else "WB"))
        return out


class FakeBepi:
    name = "BepiPred-3.0"

    def predict(self, seqs, *, chunk_size, chunk_overlap, **kw):
        out = {}
        for pid, seq in seqs.items():
            n = len(seq)
            scores = [0.30 if (i // 12) % 3 == 1 else 0.05 for i in range(n)]
            out[pid] = ResidueScores(pid, scores, scores, max(1, -(-n // (chunk_size - chunk_overlap))))
        return out


class FakeDisco:
    name = "DiscoTope-3.0"

    def predict(self, structure, *, struc_type="alphafold"):
        ca = [ln for ln in Path(structure).read_text().splitlines() if ln.startswith("ATOM")]
        return [DiscoTopeResidue(i + 1, "A", 0.95 if 5 <= i < 15 else 0.2, 0.5, 80.0, None) for i in range(len(ca))]


def write_pdb_ca(path: Path, seq: str, coords: np.ndarray, bfac) -> Path:
    inv = {"A": "ALA", "R": "ARG", "N": "ASN", "D": "ASP", "C": "CYS", "Q": "GLN", "E": "GLU", "G": "GLY", "H": "HIS",
           "I": "ILE", "L": "LEU", "K": "LYS", "M": "MET", "F": "PHE", "P": "PRO", "S": "SER", "T": "THR", "W": "TRP",
           "Y": "TYR", "V": "VAL"}
    lines = []
    for i, (aa, xyz) in enumerate(zip(seq, coords)):
        b = bfac[i] if hasattr(bfac, "__len__") else bfac
        lines.append(f"ATOM  {i + 1:5d}  CA  {inv[aa]} A{i + 1:4d}    {xyz[0]:8.3f}{xyz[1]:8.3f}{xyz[2]:8.3f}  1.00{b:6.2f}           C")
    path.write_text("\n".join(lines) + "\nEND\n")
    return path


def helix_coords(n: int, *, noise: float = 0.0, seed: int = 0) -> np.ndarray:
    t = np.arange(n)
    xyz = np.stack([2.3 * np.cos(t * 100 / 180 * np.pi), 2.3 * np.sin(t * 100 / 180 * np.pi), 1.5 * t], axis=1)
    if noise:
        xyz = xyz + np.random.default_rng(seed).normal(0, noise, xyz.shape)
    return xyz


class FakeFolder:
    """Boltz-2 stand-in: writes a helix CA trace with a pLDDT-like B-factor column."""
    name, fidelity, version, msa, accelerator = "Boltz-2", "reference", "fake-0", "empty", "cpu"
    sends_sequence_off_machine = False

    def __init__(self, fail: set[str] = frozenset(), plddt: float = 85.0):
        self.fail, self.plddt = set(fail), plddt

    def fold(self, pid, seq, outdir):
        from vaxforge.prediction.errors import ToolFailed
        if pid in self.fail:
            raise ToolFailed("fake boltz failure")
        outdir.mkdir(parents=True, exist_ok=True)
        pdb = write_pdb_ca(outdir / "boltz2.pdb", seq, helix_coords(len(seq)), self.plddt)
        return FoldResult(pdb, {"confidence": {"confidence_score": 0.8, "ptm": 0.7, "complex_plddt": self.plddt / 100}})


class FakeRefolder:
    def __init__(self, name: str, noise: float):
        self.name, self.noise, self.fidelity, self.version = name, noise, "reference", "fake"
        self.sends_sequence_off_machine = False

    def fold(self, pid, seq, outdir):
        outdir.mkdir(parents=True, exist_ok=True)
        pdb = write_pdb_ca(outdir / f"{self.name}.pdb", seq, helix_coords(len(seq), noise=self.noise, seed=len(seq)), 70.0)
        return FoldResult(pdb, {})


FREQ_TSV = """MHC class\tMHC locus\tMHC allele\tWorld\tEurope\tEast Asia\tWest Africa
I\tHLA-A\tHLA-A*01:01\t0.10\t0.16\t0.02\t0.04
I\tHLA-A\tHLA-A*02:01\t0.15\t0.25\t0.10\t0.10
I\tHLA-A\tHLA-A*24:02\t0.12\t0.09\t0.30\t0.02
I\tHLA-B\tHLA-B*07:02\t0.06\t0.13\t0.04\t0.03
I\tHLA-B\tHLA-B*35:01\t0.05\t0.06\t0.07\t0.12
I\tHLA-B\tHLA-B*58:01\t0.03\t0.01\t0.06\t0.10
II\tHLA-DRB1\tHLA-DRB1*01:01\t0.07\t0.09\t0.04\t0.05
II\tHLA-DRB1\tHLA-DRB1*07:01\t0.10\t0.14\t0.08\t0.09
II\tHLA-DRB1\tHLA-DRB1*15:01\t0.08\t0.13\t0.07\t0.06
II\tHLA-DQB1\tHLA-DQB1*02:01\t0.12\t0.17\t0.03\t0.15
II\tHLA-DQB1\tHLA-DQB1*03:02\t0.08\t0.10\t0.12\t0.05
"""


# ------------------------------------------------------------------ a minimal stand-in for Stream A's RunStore
class MiniArtifact:
    def __init__(self, stage, envelope):
        self.stage, self._e = stage, envelope

    payload = property(lambda self: self._e["payload"])
    params = property(lambda self: self._e["params"])
    inputs = property(lambda self: self._e["inputs"])
    status = property(lambda self: self._e["status"])
    warnings = property(lambda self: self._e["warnings"])
    digest = property(lambda self: self._e["digest"])


class MiniStore:
    """Implements the part of Stream A's ``vaxforge.artifacts.RunStore`` API (docs/ARTIFACT_CONTRACT.md) that stream B calls:
    ``run_dir``, ``has``, ``digest_of``, ``read_artifact``, ``write_artifact``, ``artifact_path``. The real class is exercised
    in the merged dry run (tests/stream_b/live/merged_dry_run.py)."""
    STAGES = {"s0": "s0_taxonomy", "s1": "s1_acquire", "s2": "s2_annotate", "s3": "s3_rank", "s4": "s4_filter", "s5": "s5_epitopes", "s6": "s6_coverage", "s7": "s7_structure"}

    def __init__(self, run_dir, run_id=None, cache_dir=None):
        self.run_dir, self.run_id, self.cache_dir = Path(run_dir), run_id or Path(run_dir).name, cache_dir
        self.envelopes, self.writes = {}, []
        for sid, mod in self.STAGES.items():
            f = self.run_dir / f"{mod}.json"
            if f.exists():
                self.envelopes[sid] = json.loads(f.read_text())

    def artifact_path(self, stage):
        return self.run_dir / f"{self.STAGES[stage]}.json"

    def has(self, stage):
        return stage in self.envelopes

    def digest_of(self, stage):
        return self.envelopes[stage].get("digest") or "sha256:" + "0" * 64

    def read_artifact(self, stage, verify=True):
        return MiniArtifact(stage, self.envelopes[stage])

    def write_artifact(self, stage, payload, *, params=None, inputs=None, tools=None, status="ok", warnings=None, **_):
        assert status in ("ok", "needs_user_input", "failed")
        upstream = {"s3": ["s2"], "s4": ["s3", "s1"], "s5": ["s4"], "s6": ["s5"], "s7": ["s4"]}[stage] if inputs is None else list(inputs)
        env = {"schema": f"vaxforge/{self.STAGES[stage]}/v1", "stage": stage, "run_id": self.run_id, "status": status,
               "params": json.loads(json.dumps(params or {})), "inputs": {u: self.digest_of(u) for u in upstream if self.has(u)},
               "warnings": list(warnings or []), "payload": json.loads(json.dumps(payload, allow_nan=False)),
               "producer": {"tools": dict(tools or {})}}
        env["digest"] = "sha256:" + format(abs(hash(json.dumps(env, sort_keys=True))) % (1 << 64), "064x")
        self.envelopes[stage] = env
        self.writes.append(stage)
        self.artifact_path(stage).write_text(json.dumps(env))
        return MiniArtifact(stage, env)
