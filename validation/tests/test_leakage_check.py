import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "src")]

import pytest  # noqa: E402
import leakage_check as lc  # noqa: E402
from vaxforge import s9_5_finetune as ft  # noqa: E402

HELD = {"colony-pcr"}

# Long enough to produce well over MIN_SHINGLES shingles.
INSTRUCTION = ("Distribute 100 ul of the master mix from well A1 of the reservoir into every well of the "
               "first PCR plate column by column using a fresh tip for each column and then seal the plate")
REFERENCE = ("def run(protocol): plate = protocol.load_labware('nest_96_wellplate_100ul_pcr_full_skirt', 1) "
             "pipette = protocol.load_instrument('p300_multi_gen2', 'left') pipette.transfer(100, plate['A1'], plate['A2'])")
UNRELATED = ("Mix the competent cells gently with the plasmid, rest them on ice for half an hour, then move the "
             "tube to the heat block for exactly forty seconds before returning it to ice")


def cand(i, prompt=UNRELATED, code="def run(protocol): pass  # " + "unrelated words " * 12, **kw):
    c = {"id": i, "source": "opentrons-apache", "prompt": prompt, "code": code, "heldout_family": "none",
         "family_doubt": False, "refused_by_base": False, "simulate_pass": True}
    c.update(kw)
    return c


def build(cands):
    return ft.build_manifest(cands, HELD, min_rows=1)


HELDOUT = {"tasks/colony/instruction.md": INSTRUCTION, "tasks/colony/solution.py": REFERENCE}


def violations(cands, heldout=HELDOUT, manifest=None, **kw):
    return lc.check(manifest or build(cands), cands, heldout, **kw)["violations"]


def test_unrelated_training_data_is_clean():
    assert violations([cand("a")]) == []


def test_a_near_duplicate_of_a_heldout_instruction_is_caught():
    reworded = INSTRUCTION.replace("100 ul", "120 ul").replace("seal the plate", "cover the plate")
    v = violations([cand("a", prompt=reworded)])
    assert len(v) == 1 and "prompt overlaps held-out tasks/colony/instruction.md" in v[0]


def test_a_near_duplicate_of_a_reference_solution_is_caught_even_when_embedded_in_longer_code():
    padded = "# helper\n" + "x = 1\n" * 40 + REFERENCE + "\n" + "y = 2\n" * 40
    v = violations([cand("a", code=padded)])
    assert any("code overlaps held-out tasks/colony/solution.py" in line for line in v)


def test_a_fragment_of_a_long_heldout_text_is_caught():
    long_ref = REFERENCE + " " + UNRELATED + " " + INSTRUCTION
    fragment = INSTRUCTION  # the training prompt is a piece of the held-out file
    v = violations([cand("a", prompt=fragment)], heldout={"long.md": long_ref})
    assert len(v) == 1 and "long.md" in v[0]


def test_short_heldout_texts_are_skipped_and_listed_not_trusted():
    r = lc.check(build([cand("a")]), [cand("a")], {**HELDOUT, "tiny.md": "too short"})
    assert r["skipped_short_heldout"] == ["tiny.md"] and r["violations"] == []


def test_an_empty_comparison_refuses_to_pass():
    with pytest.raises(ValueError, match="refusing to report a clean result"):
        lc.check(build([cand("a")]), [cand("a")], {"tiny.md": "too short"})
    with pytest.raises(ValueError, match="refusing"):
        lc.check(build([cand("a")]), [cand("a")], {})


def test_manifest_rule_violations_are_caught_even_if_the_builder_missed_them():
    cs = [cand("a")]
    m = build(cs)
    m["included"][0]["source"] = "protocols.io"
    m["included"][0]["heldout_family"] = "colony-pcr"
    m["dropped_for_refusal"] = ["a"]
    v = violations(cs, manifest=m)
    assert any("excluded from training" in x for x in v)
    assert any("not 'none'" in x for x in v)
    assert any("refusal probe" in x for x in v)


def test_a_pair_edited_after_the_manifest_is_caught():
    cs = [cand("a")]
    m = build(cs)
    cs[0]["prompt"] = UNRELATED + " and then something else entirely"
    assert any("prompt changed since the manifest" in x for x in violations(cs, manifest=m))


def test_a_pair_missing_from_the_candidates_is_caught():
    m = build([cand("a")])
    assert any("not in the candidates file" in x for x in violations([], manifest=m))


def test_cli_exit_codes(tmp_path, capsys):
    held = tmp_path / "held"
    held.mkdir()
    (held / "instruction.md").write_text(INSTRUCTION, encoding="utf-8")
    (held / "solution.py").write_text(REFERENCE, encoding="utf-8")
    clean, dirty = [cand("a")], [cand("b", prompt=INSTRUCTION)]
    for name, cs, code in (("clean", clean, 0), ("dirty", dirty, 2)):
        cfile, mfile = tmp_path / f"{name}.jsonl", tmp_path / f"{name}.json"
        cfile.write_text("\n".join(json.dumps(c) for c in cs) + "\n", encoding="utf-8")
        mfile.write_text(json.dumps(build(cs)), encoding="utf-8")
        assert lc.main([str(mfile), str(cfile), "--heldout-dir", str(held)]) == code
    assert "1 violation(s)" in capsys.readouterr().out
    assert lc.main([str(tmp_path / "missing.json"), str(cfile), "--heldout-dir", str(held)]) == 1
    empty = tmp_path / "empty"
    empty.mkdir()
    assert lc.main([str(mfile), str(cfile), "--heldout-dir", str(empty)]) == 1
