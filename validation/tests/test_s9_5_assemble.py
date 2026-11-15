import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "src")]
spec = importlib.util.spec_from_file_location("s9_5_assemble", ROOT / "scripts" / "s9_5_assemble.py")
asm = importlib.util.module_from_spec(spec)
spec.loader.exec_module(asm)

HELD = {"colony-pcr"}


def row(i, **kw):
    c = {"id": i, "source": "opentrons-apache", "prompt": f"p {i}", "code": f"c {i}", "heldout_family": "none",
         "family_doubt": False, "assigned_by": "agent-review", "refused_by_base": False, "simulate_pass": True}
    c.update(kw)
    return c


def synth(kind, n, **kw):
    return row(f"vaxforge-synthetic:{kind}-s9_5-x-{n}", source="vaxforge-synthetic", origin="synthetic", assigned_by="rule", **kw)


def test_synthetic_rows_are_drawn_evenly_by_kind_and_only_as_many_as_needed():
    real = [row(f"r{i}") for i in range(6)] + [row("held", heldout_family="colony-pcr")]  # 6 real rows count, the held-out one does not
    pool = [synth("a", i) for i in range(10)] + [synth("b", i) for i in range(10)] + [synth("c", i) for i in range(2)]
    cands, rep = asm.assemble(real, pool, rows=15, heldout_families=HELD, seed="s")
    assert rep["real_included"] == 6 and rep["synthetic_chosen"] == 9 and rep["total"] == 15 and rep["meets_rows"]
    # round-robin a, b, c: kind c runs out after 2 rows, so the 9 rows split 4 / 3 / 2
    assert rep["synthetic_per_kind"] == {"vaxforge-synthetic:a": 4, "vaxforge-synthetic:b": 3, "vaxforge-synthetic:c": 2}
    assert len(cands) == len(real) + 9 and rep["real_share"] == round(6 / 15, 4)


def test_ineligible_synthetic_rows_are_never_drawn_and_a_shortfall_is_reported():
    real = [row("r0")]
    pool = [synth("a", 0), synth("a", 1, refused_by_base=True), synth("a", 2, simulate_pass=False)]
    cands, rep = asm.assemble(real, pool, rows=5, heldout_families=HELD, seed="s")
    assert rep["synthetic_chosen"] == 1 and rep["total"] == 2 and not rep["meets_rows"]


def test_the_draw_is_repeatable_for_a_seed():
    real = [row("r0")]
    pool = [synth(k, i) for k in "abc" for i in range(8)]
    one = asm.assemble(real, pool, rows=10, heldout_families=HELD, seed="x")[0]
    two = asm.assemble(real, pool, rows=10, heldout_families=HELD, seed="x")[0]
    assert [c["id"] for c in one] == [c["id"] for c in two]
