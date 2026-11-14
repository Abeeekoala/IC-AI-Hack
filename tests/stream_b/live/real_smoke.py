"""Run S3 -> S5 (and S6/S7 if inputs exist) on REAL N. meningitidis MC58 data with whatever real tools are installed.

Not a pytest test (needs network + installed tools). Usage, from the repo root:

    .venv/bin/python tests/stream_b/live/real_smoke.py [--stages s3,s4,s5,s6] [--n-random 32] [--freq path/to/table.tsv]

Tool locations assumed by this script (override with env vars):
    BLAST+      .envs/blast/bin           (conda: conda create -p .envs/blast -c conda-forge -c bioconda blast)
    AlgPred2    .venvs/algpred2/bin/algpred2   (needs scikit-learn 1.2.x + numpy<2, so its own env)
    ToxinPred2  .venv/bin/toxinpred2
    MHCflurry   importable from the running interpreter
S2 here is synthesised from the UniProt proteome (localisation 'Unknown', no VFDB) because stream A's S2 does not exist yet;
the ranking is therefore not meaningful - this only exercises the S4-S7 wrappers on real sequences.
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
os.environ["PATH"] = os.pathsep.join([str(ROOT / ".envs/blast/bin"), str(ROOT / ".venv/bin"), os.environ["PATH"]])

from vaxforge import s3_rank, s4_filter, s5_epitopes, s6_coverage, s7_structure      # noqa: E402
from vaxforge.prediction.config import load_config                                    # noqa: E402
from vaxforge.prediction.fasta import read_fasta                                      # noqa: E402
from vaxforge.prediction.refdata import RefData                                       # noqa: E402

BEXSERO = {"Q9JXV4": "fHbp", "Q9JXK7": "NadA", "Q7DD37": "NHBA"}
OTHERS = {"P0DH58": "PorA", "P42385": "GroEL", "P64027": "EF-Tu", "Q9JXQ0": "AtpA", "Q9JYI8": "FkpA"}


def build_s2(run_dir: Path, n_random: int, rd: RefData, max_len: int | None = None) -> Path:
    fa = read_fasta(rd.proteome_fasta("UP000000425"))
    by_acc = {h.split("|")[1] if "|" in h else h: (h, d, s) for h, d, s in fa}
    pick = list(BEXSERO) + list(OTHERS)
    rest = sorted(a for a in set(by_acc) - set(pick) if max_len is None or len(by_acc[a][2]) <= max_len)
    pick += random.Random(1).sample(rest, n_random)
    if max_len is not None:                                   # keep Boltz-2 on CPU tractable
        pick = [a for a in pick if len(by_acc[a][2]) <= max_len]
    recs = []
    for acc in pick:
        _, desc, seq = by_acc[acc]
        recs.append({"protein_id": acc, "name": desc.split(" OS=")[0], "taxid": 122586, "sequence": seq,
                     "localisation": {"label": "Unknown"}})
    doc = {"schema": "vaxforge.s2_annotate/1", "stage": "s2_annotate", "artifact_id": "sha256:real-smoke-s2",
           "payload": {"taxid": 122586, "species_taxid": 487, "organism": "Neisseria meningitidis MC58",
                       "lineage": ["Bacteria", "Pseudomonadota", "Betaproteobacteria"], "proteins": recs,
                       "note": "synthesised from UniProt UP000000425; localisation Unknown; no VFDB"}}
    p = run_dir / "s2_annotate.json"
    p.write_text(json.dumps(doc))
    return p


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="s3,s4,s5")
    ap.add_argument("--n-random", type=int, default=32)
    ap.add_argument("--run-dir", default=str(ROOT / "runs" / "real_mc58"))
    ap.add_argument("--freq", default=None)
    ap.add_argument("--ligands", default=None)
    ap.add_argument("--conservancy-on-fail", default="reject", choices=["reject", "flag"])
    ap.add_argument("--max-len", type=int, default=None, help="only keep proteins up to this length in the synthesised S2")
    ap.add_argument("--top-k", type=int, default=None)
    ap.add_argument("--boltz-msa", default="empty", choices=["empty", "server"], help="'server' sends sequences to the ColabFold MSA server")
    ap.add_argument("--offline", action="store_true", help="refdata.offline: no network, so ESMFold-API validators are unavailable")
    a = ap.parse_args()
    run_dir = Path(a.run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    cfg = load_config(overrides={
        "refdata": {"cache_dir": str(ROOT / "cache"), "afnd_frequencies": a.freq, "iedb_human_ligands": a.ligands, "offline": a.offline},
        "tools": {"algpred2": str(ROOT / ".venvs/algpred2/bin/algpred2"), "toxinpred2": str(ROOT / ".venv/bin/toxinpred2"),
                  "blast_threads": 4,
                  # optional heavy tools: used only if the env exists (see docs/TOOLS_SETUP.md section 0)
                  **({"bepipred3": f"{ROOT}/.venvs/bepipred/bin/python {ROOT}/.tools/BepiPred-3.0/bepipred3_CLI.py"}
                     if (ROOT / ".tools/BepiPred-3.0/bepipred3_CLI.py").exists() else {}),
                  **({"boltz": str(ROOT / ".venvs/boltz/bin/boltz")} if (ROOT / ".venvs/boltz/bin/boltz").exists() else {})},
        "s3": {"min_candidates": 60, "max_candidates": 150},
        "s4": {"conservancy": {"on_fail": a.conservancy_on_fail}},
        "s5": {"bcell": {"esm_dir": str(ROOT / "cache" / "bepipred_esm")}},
        "s7": {"top_k": a.top_k, "boltz": {"msa": a.boltz_msa, "recycling_steps": 1, "sampling_steps": 50, "max_residues": 400}}})
    rd = RefData(cfg["refdata"]["cache_dir"])
    stages = a.stages.split(",")
    if "s3" in stages:
        build_s2(run_dir, a.n_random, rd, a.max_len)
        print("S3 ->", s3_rank.run(run_dir, cfg, force=True))
    if "s4" in stages:
        print("S4 ->", s4_filter.run(run_dir, cfg, refdata=rd, force=True))
    if "s5" in stages:
        print("S5 ->", s5_epitopes.run(run_dir, cfg, force=True))
    if "s6" in stages:
        print("S6 ->", s6_coverage.run(run_dir, cfg, force=True))
    if "s7" in stages:
        print("S7 ->", s7_structure.run(run_dir, cfg, force=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
