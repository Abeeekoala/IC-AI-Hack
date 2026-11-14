"""DiscoTope-3.0 (conformational B-cell epitopes) runner and output parsing.

RUN for real (2026-11-14, AlphaFold DB model of fHbp): ``main.py --pdb_or_zip_file X.pdb --struc_type
alphafold --out_dir DIR`` writes ``DIR/<pdb>/<pdb>_<chain>_discotope3.csv`` with header
``pdb,chain,res_id,residue,DiscoTope-3.0_score,calibrated_score,epitope,rsa,pLDDTs,length,alphafold_struc_flag``
(the README's sample header omits ``chain``, ``calibrated_score`` and ``epitope``). The ``epitope`` boolean is
used; if a version omits it, ``calibrated_score`` (else the raw score) is thresholded instead.
The README states the code is released under a Creative Commons licence - check LICENSE for
non-commercial terms before any for-profit use.
"""
from __future__ import annotations

import csv
import io
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Sequence

from .errors import ToolFailed
from .tools import run_cmd


@dataclass
class DiscoTopeResidue:
    res_id: int
    residue: str
    score: float
    rsa: float | None
    plddt: float | None
    epitope: bool | None
    calibrated: float | None = None      # 'calibrated_score' column (the tool's epitope boolean thresholds this, default 0.90)


def parse_discotope_csv(text: str) -> list[DiscoTopeResidue]:
    rdr = csv.DictReader(io.StringIO(text.strip()))
    if not rdr.fieldnames:
        raise ToolFailed("empty DiscoTope-3.0 CSV")
    names = {n.lower(): n for n in rdr.fieldnames}
    score_col = next((names[k] for k in names if "discotope" in k and "score" in k), None)
    if "res_id" not in names or score_col is None:
        raise ToolFailed(f"unexpected DiscoTope-3.0 header: {rdr.fieldnames}")
    epi_col = next((names[k] for k in names if "epitope" in k and "score" not in k), None)
    cal_col = names.get("calibrated_score")
    rsa_col = names.get("rsa")
    pl_col = names.get("plddts") or names.get("plddt")
    out = []
    for row in rdr:
        epi = None
        if epi_col is not None and row.get(epi_col, "") != "":
            epi = str(row[epi_col]).strip().lower() in ("true", "1", "yes")
        out.append(DiscoTopeResidue(
            res_id=int(row[names["res_id"]]), residue=row.get(names.get("residue", ""), "") or "",
            score=float(row[score_col]),
            rsa=float(row[rsa_col]) if rsa_col and row.get(rsa_col) not in (None, "") else None,
            plddt=float(row[pl_col]) if pl_col and row.get(pl_col) not in (None, "") else None,
            epitope=epi, calibrated=float(row[cal_col]) if cal_col and row.get(cal_col) not in (None, "") else None))
    return out


def summarise(residues: Sequence[DiscoTopeResidue], threshold: float) -> dict:
    """Per-structure summary; uses the tool's boolean when present, else ``score >= threshold``."""
    flags = [(r.epitope if r.epitope is not None else (r.calibrated if r.calibrated is not None else r.score) >= threshold)
             for r in residues]
    ids = [r.res_id for r, f in zip(residues, flags) if f]
    segments: list[list[int]] = []
    for rid in ids:
        if segments and rid == segments[-1][1] + 1:
            segments[-1][1] = rid
        else:
            segments.append([rid, rid])
    n = len(residues)
    return {
        "n_residues": n,
        "n_epitope_residues": len(ids),
        "epitope_fraction": (len(ids) / n) if n else 0.0,
        "mean_score": (sum(r.score for r in residues) / n) if n else 0.0,
        "epitope_residues": ids,
        "epitope_segments": [tuple(s) for s in segments],
        "threshold_source": ("tool_boolean" if any(r.epitope is not None for r in residues)
                             else f"{'calibrated_score' if any(r.calibrated is not None for r in residues) else 'score'}>={threshold}"),
        "mean_plddt_input": (sum(r.plddt for r in residues if r.plddt is not None) /
                             max(1, sum(1 for r in residues if r.plddt is not None))) if residues else None,
    }


class DiscoTopeRunner:
    name = "DiscoTope-3.0"
    fidelity = "reference"

    def __init__(self, argv_prefix: Sequence[str], *, timeout: float = 3600, models_dir: str | Path | None = None):
        self.argv, self.timeout = list(argv_prefix), timeout
        # RUN for real: the tool's default --models_dir is the RELATIVE path 'models/', so it fails ("Path models/ does
        # not exist!") unless launched from inside its checkout. Derive the checkout from the script path and pass it explicitly.
        script = next((Path(a) for a in reversed(self.argv) if str(a).endswith("main.py")), None)
        root = script.resolve().parent.parent if script is not None and script.exists() else None
        self.models_dir = Path(models_dir) if models_dir else (root / "models" if root and (root / "models").exists() else None)
        self.cwd = root

    def predict(self, structure: str | Path, *, struc_type: str = "alphafold") -> list[DiscoTopeResidue]:
        structure = Path(structure).resolve()
        with tempfile.TemporaryDirectory(prefix="vf_discotope_") as td:
            args = self.argv + ["--pdb_or_zip_file", str(structure), "--struc_type", struc_type, "--out_dir", td]
            if self.models_dir is not None and "--models_dir" not in self.argv:
                args += ["--models_dir", str(self.models_dir)]
            run_cmd(args, cwd=self.cwd, timeout=self.timeout, what="DiscoTope-3.0")
            csvs = sorted(Path(td).rglob("*discotope3.csv"))
            if not csvs:
                raise ToolFailed("DiscoTope-3.0 wrote no *_discotope3.csv output")
            return parse_discotope_csv(csvs[0].read_text())
