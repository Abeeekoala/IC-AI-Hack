"""s9_5_synth: seeded synthetic training pairs.

The simulation test needs Python 3.10 with opentrons 7.5.0:
    VAXFORGE_OT_PYTHON=/path/to/venv/bin/python pytest validation/tests/test_s9_5_synth.py
"""
import ast
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "src")]

import pytest  # noqa: E402
from vaxforge import s9_5_synth as syn  # noqa: E402

FORBIDDEN = re.compile(r"pcr|primer|master ?mix|magnet|thermocycl|colony|heat ?shock|golden|rna|elut|ethanol|bead|transform",
                       re.I)


def rows(n=60, seed="t"):
    return syn.generate(seed, n)[0]


def test_the_same_seed_gives_the_same_rows_and_another_seed_different_ones():
    assert rows(25) == rows(25)
    assert [r["code"] for r in rows(25)] != [r["code"] for r in rows(25, "other")]


def test_every_row_has_the_corpus_schema_and_unrun_flags():
    for r in rows(30):
        assert {"id", "source", "prompt", "code", "heldout_family", "family_doubt", "refused_by_base", "simulate_pass"} <= set(r)
        assert r["source"] == syn.SOURCE and r["origin"] == "synthetic" and r["assigned_by"] == "rule"
        assert r["refused_by_base"] is None and r["simulate_pass"] is None  # never filled in here: nothing was run


def test_ids_prompts_and_code_are_unique():
    rs = rows(80)
    assert len({r["id"] for r in rs}) == len({r["prompt"] for r in rs}) == len({r["code"] for r in rs}) == 80


def test_no_row_has_the_shape_or_vocabulary_of_a_heldout_family():
    for r in rows(120):
        assert r["heldout_family"] == "none" and r["family_doubt"] is False
        words = re.sub(r"[\w.]*_pcr\w*", "", r["prompt"] + r["code"])  # a labware load name is not a procedure
        assert not FORBIDDEN.search(words), r["id"]


def test_the_temperature_module_never_goes_near_the_heat_shock_temperature():
    for r in rows(120):
        for t in re.findall(r"set_temperature\((\d+)\)", r["code"]):
            assert not 41 <= int(t) <= 43


def test_the_code_parses_and_defines_the_run_function_with_a_supported_api_level():
    for r in rows(60):
        tree = ast.parse(r["code"])
        assert [n.name for n in tree.body if isinstance(n, ast.FunctionDef)] == ["run"]
        assert re.search(r'"apiLevel": "2\.(13|14|15)"', r["code"])


def test_every_step_of_the_prompt_has_a_matching_operation():
    for i in range(40):
        p = syn.build("t", i)
        listed = re.findall(r"^\d+\. ", p.prompt(), re.M)
        assert len(listed) == len(p.ops)


def test_every_volume_fits_the_pipette_that_uses_it_and_there_are_at_most_two_pipettes():
    for i in range(120):
        p = syn.build("t", i)
        assert len(p.pipettes()) <= 2
        for op in p.ops:
            if op[0] == "transfer":
                lo, hi, _ = syn.PIPETTES[syn._pipette_for(op[1])]
                assert lo <= op[1] <= hi
                if op[8]:
                    assert op[8][1] <= hi  # the mix volume fits the same pipette
            if op[0] == "mix":
                assert op[4] <= syn.PIPETTES[syn._pipette_for(op[4])][1]


def test_no_pipette_needs_more_tips_than_one_rack_holds():
    for i in range(300):
        p = syn.build("t", i)
        used = {}
        for op in p.ops:
            if op[0] == "transfer":
                name, n = syn._pipette_for(op[1]), (len(op[5]) if op[7] == "always" else 1)
            elif op[0] == "mix":
                name, n = syn._pipette_for(op[4]), len(op[2])
            else:
                continue
            used[name] = used.get(name, 0) + n
        assert all(n <= 96 for n in used.values()), (p.kind, used)


def test_a_kind_the_labeller_always_rejects_makes_generate_fail_loudly(monkeypatch):
    monkeypatch.setattr(syn, "record", lambda seed, index: None)
    with pytest.raises(RuntimeError, match="too narrow"):
        syn.generate("t", 3)


@pytest.mark.skipif(not os.environ.get("VAXFORGE_OT_PYTHON"), reason="needs VAXFORGE_OT_PYTHON (opentrons 7.5.0)")
def test_generated_protocols_simulate_on_opentrons_7_5_0(tmp_path):
    from vaxforge.s9_5_corpus import simulate_file
    sim = Path(os.environ["VAXFORGE_OT_PYTHON"]).parent / "opentrons_simulate"
    for r in rows(20, "sim"):
        f = tmp_path / (r["id"].split(":")[1] + ".py")
        f.write_text(r["code"], encoding="utf-8")
        assert simulate_file(f, simulator=str(sim))["passed"], r["id"]
