"""Minimal structure utilities for S7: Cα parsing, superposition, TM-score, lDDT-Cα, pLDDT summaries.

All comparisons assume a 1:1 residue correspondence (same sequence folded by different
predictors), so no sequence alignment is needed. TM-score here is therefore *fixed-alignment*
(an optimal-superposition search, but not TM-align's alignment search); lDDT-Cα is
superposition-free and is the more robust agreement measure for flexible multi-domain proteins.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .errors import ToolUnavailable

try:                                   # numpy is only needed when structures are actually compared; the stage modules
    import numpy as np                 # must still import (and `vaxforge run` must still start) without it
except ImportError:                    # pragma: no cover - exercised by tests via monkeypatching
    np = None                          # type: ignore[assignment]


def _need_numpy() -> None:
    if np is None:
        raise ToolUnavailable("numpy is not installed; it is needed to read and compare structures (pip install numpy)")


AA3 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q", "GLU": "E", "GLY": "G",
    "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K", "MET": "M", "PHE": "F", "PRO": "P", "SER": "S",
    "THR": "T", "TRP": "W", "TYR": "Y", "VAL": "V", "SEC": "C", "PYL": "K", "MSE": "M",
}


@dataclass
class CaTrace:
    resids: list[int]
    sequence: str
    coords: np.ndarray        # (N, 3)
    bfactors: np.ndarray      # (N,)


def parse_structure_ca(path: str | Path) -> CaTrace:
    _need_numpy()
    p = Path(path)
    text = p.read_text()
    return _parse_cif(text) if p.suffix.lower() in (".cif", ".mmcif") else _parse_pdb(text)


def _parse_pdb(text: str) -> CaTrace:
    resids, seq, xyz, bf = [], [], [], []
    chain0 = None
    for ln in text.splitlines():
        if not ln.startswith(("ATOM", "HETATM")) or ln[12:16].strip() != "CA":
            continue
        alt = ln[16:17]
        if alt not in (" ", "A", ""):
            continue
        chain = ln[21:22]
        chain0 = chain if chain0 is None else chain0
        if chain != chain0:
            break                                   # first chain only (antigens are monomers here)
        aa = AA3.get(ln[17:20].strip().upper())
        if aa is None:
            continue
        resids.append(int(ln[22:26]))
        seq.append(aa)
        xyz.append((float(ln[30:38]), float(ln[38:46]), float(ln[46:54])))
        bf.append(float(ln[60:66]))
    if not xyz:
        raise ValueError("no Cα atoms found in PDB text")
    return CaTrace(resids, "".join(seq), np.array(xyz, float), np.array(bf, float))


def _parse_cif(text: str) -> CaTrace:
    lines = text.splitlines()
    cols: list[str] = []
    rows: list[list[str]] = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "loop_":
            j = i + 1
            hdr = []
            while j < len(lines) and lines[j].startswith("_atom_site."):
                hdr.append(lines[j].strip())
                j += 1
            if hdr:
                cols = hdr
                while j < len(lines) and lines[j].strip() and not lines[j].startswith(("loop_", "_", "#")):
                    rows.append(lines[j].split())
                    j += 1
                break
        i += 1
    if not cols:
        raise ValueError("no _atom_site loop found in mmCIF text")
    ix = {c.split(".", 1)[1]: k for k, c in enumerate(cols)}
    need = ("label_atom_id", "label_comp_id", "Cartn_x", "Cartn_y", "Cartn_z", "B_iso_or_equiv")
    if any(n not in ix for n in need):
        raise ValueError("mmCIF _atom_site loop lacks required columns")
    seq_col = "label_seq_id" if "label_seq_id" in ix else "auth_seq_id"
    asym_col = "label_asym_id" if "label_asym_id" in ix else None
    resids, seq, xyz, bf = [], [], [], []
    asym0 = None
    for r in rows:
        if len(r) < len(cols) or r[ix["label_atom_id"]].strip('"') != "CA":
            continue
        if "label_alt_id" in ix and r[ix["label_alt_id"]] not in (".", "A", "?"):
            continue
        if asym_col:
            asym0 = r[ix[asym_col]] if asym0 is None else asym0
            if r[ix[asym_col]] != asym0:
                break
        aa = AA3.get(r[ix["label_comp_id"]].upper())
        if aa is None:
            continue
        resids.append(int(r[ix[seq_col]]))
        seq.append(aa)
        xyz.append((float(r[ix["Cartn_x"]]), float(r[ix["Cartn_y"]]), float(r[ix["Cartn_z"]])))
        bf.append(float(r[ix["B_iso_or_equiv"]]))
    if not xyz:
        raise ValueError("no Cα atoms found in mmCIF text")
    return CaTrace(resids, "".join(seq), np.array(xyz, float), np.array(bf, float))


# ---------------------------------------------------------------------------- geometry
def kabsch(mobile: np.ndarray, target: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Rotation ``R`` and translation ``t`` minimising RMSD of ``mobile @ R.T + t`` onto ``target``."""
    _need_numpy()
    pc, qc = mobile.mean(0), target.mean(0)
    h = (mobile - pc).T @ (target - qc)
    u, _, vt = np.linalg.svd(h)
    d = np.sign(np.linalg.det(vt.T @ u.T)) or 1.0
    r = vt.T @ np.diag([1.0, 1.0, d]) @ u.T
    return r, qc - pc @ r.T


def rmsd_after_superposition(mobile: np.ndarray, target: np.ndarray) -> float:
    r, t = kabsch(mobile, target)
    diff = mobile @ r.T + t - target
    return float(np.sqrt((diff ** 2).sum(1).mean()))


def _d0(length: int) -> float:
    return 0.5 if length <= 21 else max(0.5, 1.24 * (length - 15) ** (1.0 / 3.0) - 1.8)


def tm_score(model: np.ndarray, reference: np.ndarray, *, max_iter: int = 20) -> float:
    """TM-score of ``model`` vs ``reference`` (1:1 correspondence, normalised by reference length)."""
    _need_numpy()
    n = len(reference)
    if len(model) != n:
        raise ValueError("model and reference must have the same number of residues")
    if n < 3:
        return 0.0
    d0 = _d0(n)
    d0_search = min(max(d0, 4.5), 8.0)

    def score_of(r: np.ndarray, t: np.ndarray) -> tuple[float, np.ndarray]:
        d = np.linalg.norm(model @ r.T + t - reference, axis=1)
        return float((1.0 / (1.0 + (d / d0) ** 2)).sum() / n), d

    best = 0.0
    frag_lens = sorted({n, max(4, n // 2), max(4, n // 4), 8}, reverse=True)
    for fl in frag_lens:
        fl = min(fl, n)
        step = max(1, fl // 2) if fl < n else n
        for s in range(0, n - fl + 1, step):
            idx = np.arange(s, s + fl)
            for _ in range(max_iter):
                if len(idx) < 3:
                    break
                r, t = kabsch(model[idx], reference[idx])
                sc, d = score_of(r, t)
                best = max(best, sc)
                cut = d0_search
                sel = np.where(d < cut)[0]
                while len(sel) < 3 and cut < 1e3:
                    cut += 0.5
                    sel = np.where(d < cut)[0]
                if len(sel) == len(idx) and np.all(sel == idx):
                    break
                idx = sel
    return min(1.0, best)


def lddt_ca(model: np.ndarray, reference: np.ndarray, *, cutoff: float = 15.0,
            thresholds: tuple[float, ...] = (0.5, 1.0, 2.0, 4.0)) -> float:
    """Superposition-free lDDT on Cα atoms (reference defines the neighbour pairs)."""
    _need_numpy()
    n = len(reference)
    if len(model) != n:
        raise ValueError("model and reference must have the same number of residues")
    dr = np.linalg.norm(reference[:, None] - reference[None], axis=-1)
    dm = np.linalg.norm(model[:, None] - model[None], axis=-1)
    mask = (dr < cutoff) & ~np.eye(n, dtype=bool)
    diff = np.abs(dr - dm)
    per_residue = []
    for i in range(n):
        m = mask[i]
        if not m.any():
            continue
        per_residue.append(np.mean([(diff[i][m] < t).mean() for t in thresholds]))
    return float(np.mean(per_residue)) if per_residue else 0.0


def plddt_summary(bfactors: np.ndarray) -> dict:
    """Mean pLDDT on a 0-100 scale (rescales if the file stores 0-1) plus confidence-band fractions."""
    _need_numpy()
    b = np.asarray(bfactors, float)
    scale_100 = b.max() > 1.0
    v = b if scale_100 else b * 100.0
    return {
        "mean_plddt": float(v.mean()),
        "frac_very_high_gt90": float((v > 90).mean()),
        "frac_confident_70_90": float(((v > 70) & (v <= 90)).mean()),
        "frac_low_50_70": float(((v > 50) & (v <= 70)).mean()),
        "frac_very_low_le50": float((v <= 50).mean()),
        "scale_detected": "0-100" if scale_100 else "0-1 (rescaled x100)",
    }
