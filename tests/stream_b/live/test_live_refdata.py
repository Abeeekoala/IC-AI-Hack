"""Network checks against the real UniProt / AlphaFold DB endpoints (small responses only).

Opt-in (real network): VAXFORGE_LIVE=1 pytest tests/stream_b/live
"""
import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "src"))
from vaxforge.prediction.refdata import RefData   # noqa: E402

pytestmark = pytest.mark.skipif(not os.environ.get("VAXFORGE_LIVE"), reason="network test; set VAXFORGE_LIVE=1")


def test_uniprot_lists_other_nmeningitidis_strains(tmp_path):
    refs = RefData(tmp_path).species_proteomes(487, limit=5)
    assert len(refs) >= 2 and all(r.upid.startswith("UP") and r.protein_count for r in refs)


def test_alphafold_db_api_shape(tmp_path):
    rd = RefData(tmp_path)
    entries = json.loads(rd._get("https://alphafold.ebi.ac.uk/api/prediction/P69905").body)
    assert entries[0]["pdbUrl"].startswith("https://alphafold.ebi.ac.uk/")
