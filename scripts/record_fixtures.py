"""Record live NCBI/UniProt responses used by the S0 tests (run once, online).

    python scripts/record_fixtures.py

Writes tests/data/http_s0/*.json|*.body.  Tests then replay these offline.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vaxforge.config import Config  # noqa: E402
from vaxforge.s0_taxonomy import resolve_pathogen  # noqa: E402
from vaxforge.testing import RecordingTransport  # noqa: E402

OUT = ROOT / "tests" / "data" / "http_s0"
QUERIES = [
    "Neisseria meningitidis",
    "Neisseria",
    "Clostridium difficile",
    "N. meningitidis",
    "Staph aureus",
    "Neiseria meningitidis",
    "SARS-CoV-2",
    "E. coli",
    "meningococcus",
    "zzqx notanorganism",
]


def main() -> None:
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        cfg = Config({"paths": {"cache_dir": tmp}, "taxonomy": {"fuzzy": True}}, root=Path(tmp))
        transport = RecordingTransport(OUT)
        for q in QUERIES:
            res, _ = resolve_pathogen(cfg, q, transport=transport)
            print(f"{q!r:32} -> {res.status:17} {res.reason or '':18} {res.taxon['scientific_name'] if res.taxon else len(res.candidates)}")
        # explicit taxid path
        res, _ = resolve_pathogen(cfg, None, taxid=487, transport=transport)
        print("taxid 487 ->", res.status)


if __name__ == "__main__":
    main()
