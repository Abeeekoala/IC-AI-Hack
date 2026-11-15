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


def test_a_failed_plain_run_and_its_passing_rerun_merge_into_the_passing_copy():
    plain = row("x", simulate_pass=False)
    rerun = row("x", simulate_pass=True, custom_labware=True)
    merged, n, tightened = asm.merge_duplicates([row("a"), plain, rerun, row("b")])
    assert [c["id"] for c in merged] == ["a", "x", "b"] and n == 1 and tightened == 0
    assert merged[1]["simulate_pass"] is True and merged[1]["custom_labware"] is True


def test_a_merge_never_loosens_a_label():
    plain = row("x", simulate_pass=False, heldout_family="colony-pcr", assigned_by="agent-review")
    rerun = row("x", simulate_pass=True, heldout_family="none", assigned_by="agent-review")
    (kept,), _, tightened = asm.merge_duplicates([plain, rerun])
    assert kept["heldout_family"] == "colony-pcr" and tightened == 1  # the held-out family sticks, so the manifest excludes it
    doubt = row("y", simulate_pass=False, family_doubt=True)
    (kept,), _, tightened = asm.merge_duplicates([doubt, row("y", simulate_pass=True)])
    assert kept["family_doubt"] is True and tightened == 1


def test_two_copies_that_differ_in_code_are_an_error():
    import pytest
    with pytest.raises(ValueError, match="different code"):
        asm.merge_duplicates([row("x"), {**row("x"), "code": "other"}])


def test_assemble_counts_a_merged_pair_once_and_reports_it():
    real = [row("x", simulate_pass=False), row("x", simulate_pass=True), row("r1")]
    cands, rep = asm.assemble(real, [synth("a", i) for i in range(5)], rows=6, heldout_families=HELD, seed="s")
    assert rep["duplicate_ids_merged"] == 1 and rep["real_included"] == 2 and rep["total"] == 6
    assert len({c["id"] for c in cands}) == len(cands)
