"""Tests for the S9.5 corpus builder. Inline fixtures, a fake simulator runner, no network,
no opentrons install needed."""

import json
import stat
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))
from vaxforge import s9_5_corpus as corpus  # noqa: E402


# ---------------------------------------------------------------- fixtures

# Four transfers with different sources, volumes and destinations: no structural family rule fires.
DIVERSE_STEPS = [
    'p300.transfer(30, src["A1"], plate["B2"])',
    'p300.transfer(40, src["A2"], plate["C3"], new_tip="always", mix_after=(3, 20))',
    'p300.transfer(15, src["B1"], plate["D4"])',
    'p300.transfer(25, src["B2"], plate["E5"])',
]
POUR_STEPS = [f'p300.transfer(50, src["A1"], plate["{w}"])' for w in ("A1", "A2", "A3", "A4", "A5")]
# the same plate fill written as aspirate/dispense pairs, and as one volume from several sources
PAIR_STEPS = [line for w in ("A1", "A2", "A3") for line in (f'p300.aspirate(100, src["A1"])', f'p300.dispense(100, plate["{w}"])')]
ONE_VOLUME_STEPS = [f'p300.transfer(20, src["{s}"], plate["{s}"])' for s in ("A1", "A2", "A3", "A4")]
TEMP_MODULE = 'temp = protocol.load_module("temperature module gen2", 4)'


def make(name="Sample cherry picking", *, head="", setup="", steps=None, api="2.13", extra_meta="", requirements=""):
    """A small OT-2 protocol as text (never executed by these tests)."""
    steps = DIVERSE_STEPS if steps is None else steps
    body = "\n".join("    " + line for item in setup.splitlines() + steps for line in item.splitlines())
    return f'''{head}
from opentrons import protocol_api

metadata = {{
    "apiLevel": "{api}",
    "protocolName": "{name}",
{extra_meta}}}
{requirements}

def run(protocol: protocol_api.ProtocolContext):
    tips = protocol.load_labware("opentrons_96_tiprack_300ul", 1)
    src = protocol.load_labware("opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap", 2, label="sample rack")
    plate = protocol.load_labware("nest_96_wellplate_200ul_flat", 3)
    p300 = protocol.load_instrument("p300_single_gen2", "left", tip_racks=[tips])
{body}
'''


def spec_of(text, rel="x.py"):
    spec = corpus.analyse_protocol(text, rel)
    assert spec is not None
    return spec


def write(root, rel, text):
    p = Path(root) / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


class FakeRunner:
    """Stands in for opentrons_simulate. rules: (substring of the path, (rc, output) or an exception)."""

    def __init__(self, rules=()):
        self.rules, self.calls = list(rules), []

    def __call__(self, argv, *, env, cwd, timeout):
        self.calls.append({"argv": list(argv), "env": dict(env), "cwd": cwd, "timeout": timeout})
        for needle, result in self.rules:
            if needle in argv[-1]:
                if isinstance(result, Exception):
                    raise result
                return result
        return 0, ""


# ---------------------------------------------------------------- spec and prompt

def test_spec_reads_deck_pipettes_and_ordered_steps():
    spec = spec_of(make(setup='mod = protocol.load_module("temperature module gen2", 4)',
                        steps=["mod.set_temperature(37)", 'p300.transfer(30, src["A1"], plate["B2"])', "protocol.delay(minutes=5)"]))
    assert spec["api_level"] == "2.13" and spec["robot_type"] == "OT-2"
    assert [lw["name"] for lw in spec["labware"]] == ["opentrons_96_tiprack_300ul", "opentrons_24_tuberack_eppendorf_1.5ml_safelock_snapcap", "nest_96_wellplate_200ul_flat"]
    assert [lw["slot"] for lw in spec["labware"]] == ["1", "2", "3"]
    assert spec["labware"][1]["label"] == "sample rack"
    assert spec["pipettes"] == [{"var": "p300", "name": "p300_single_gen2", "mount": "left", "tip_racks": ["tips"]}]
    assert spec["modules"][0]["name"] == "temperature module gen2"
    assert [s["kind"] for s in spec["steps"]] == ["set_temperature", "transfer", "delay"]
    assert spec["steps"][0]["text"] == "Set the temperature module gen2 to 37 C"
    assert spec["steps"][2]["text"] == "Wait 5 minutes"
    assert spec["liquid_steps"] == 1


def test_labware_on_a_module_or_adapter_has_no_slot_and_module_commands_keep_their_argument():
    text = make(setup='hs = protocol.load_module("heaterShakerModuleV1", 4)\nadapter = hs.load_adapter("opentrons_96_pcr_adapter")\nholder = adapter.load_labware("nest_96_wellplate_100ul_pcr_full_skirt")',
                steps=["hs.set_and_wait_for_shake_speed(1200)", 'p300.transfer(30, src["A1"], holder["B2"])'])
    spec = spec_of(text)
    assert [(lw["name"], lw["slot"], lw["on"]) for lw in spec["labware"][3:]] == [("opentrons_96_pcr_adapter", None, "hs"), ("nest_96_wellplate_100ul_pcr_full_skirt", None, "adapter")]
    assert spec["steps"][0]["text"] == "Module command: set and wait for shake speed (heaterShakerModuleV1, 1200)"
    prompt = corpus.build_prompt(spec)
    assert "nest_96_wellplate_100ul_pcr_full_skirt on adapter (variable holder)" in prompt and "slot None" not in prompt


def test_a_licence_header_docstring_is_not_a_description():
    licence = '"""Copyright (c) 2024 Example Corp. All rights reserved.\nLicensed under the Apache License, Version 2.0.\nUnless required by applicable law, software is distributed AS IS,\nWITHOUT WARRANTIES OR CONDITIONS OF ANY KIND.\nPlates the samples in duplicate for the assay.\n"""\n'
    spec = spec_of(licence + make())
    assert spec["description"] == "Plates the samples in duplicate for the assay."
    only_licence = spec_of('"""Copyright 2024 Example Corp. Licensed under the Apache License, Version 2.0."""\n' + make())
    assert only_licence["description"] is None and only_licence["has_description"] is False


def test_leading_code_comments_are_not_description():
    spec = spec_of(make(head="# import utils  # had to comment this out\n# Moves the picked samples to the assay plate.\n"))
    assert spec["description"] == "Moves the picked samples to the assay plate."


def test_requirements_dict_and_robot_type():
    assert spec_of(make(requirements='requirements = {"robotType": "OT-2", "apiLevel": "2.15"}'))["api_level"] == "2.15"
    assert spec_of(make(extra_meta='    "robotType": "Flex",\n'))["robot_type"] == "Flex"
    assert spec_of(make())["robot_type"] == "OT-2"  # unspecified means OT-2


def test_non_protocols_are_rejected():
    assert corpus.analyse_protocol("x = 1\n") is None
    assert corpus.analyse_protocol('apiLevel = "2.13"\ndef run(x):\n    return x\n') is None  # no metadata dict
    assert corpus.analyse_protocol("<<<<<<< HEAD\nmetadata = {'apiLevel': '2.13'}\ndef run(p): pass\n") is None  # does not parse


def test_prompt_is_deterministic_and_states_the_spec():
    text = make(extra_meta='    "description": "Moves picked samples from the rack into the assay plate.",\n    "author": "Ada <ada@example.org>",\n',
                setup="vol = 30", steps=['p300.transfer(vol + 10, src["A1"], plate["B2"])'])
    prompt = corpus.build_prompt(spec_of(text))
    assert prompt == corpus.build_prompt(spec_of(text))
    assert "API version 2.13" in prompt
    assert "Protocol name: Sample cherry picking" in prompt
    assert "Moves picked samples from the rack into the assay plate." in prompt
    assert "nest_96_wellplate_200ul_flat in slot 3" in prompt
    assert "p300_single_gen2 on the left mount, tip racks: tips" in prompt
    assert "1. Transfer 40 µL from A1 of src to B2 of plate" in prompt  # vol + 10 resolved from the constant
    assert "ada@example.org" not in prompt and "Ada" not in prompt


def test_prompt_lists_the_named_values_its_steps_refer_to():
    text = make(setup="volumes = [10, 20, 30]\nunused = 5", steps=['for i in range(3):\n    p300.transfer(volumes[i], src["A1"], plate.rows()[i])'])
    prompt = corpus.build_prompt(spec_of(text))
    assert "Values the steps refer to:\n- volumes = [10, 20, 30]" in prompt
    assert "unused" not in prompt


def test_prompt_marks_loops_merges_repeats_and_names_rows():
    text = make(steps=['for i in range(3):\n    p300.transfer(10, src["A1"], plate.rows()[i])',
                       'p300.transfer(5, src["A2"], plate["A1"])', 'p300.transfer(5, src["A2"], plate["A1"])'])
    prompt = corpus.build_prompt(spec_of(text))
    assert "[inside: for i in range(3)]" in prompt
    assert "(x2)" in prompt
    assert "to row A of plate" in corpus.build_prompt(spec_of(make(steps=['p300.transfer(10, src["A1"], plate.rows()[0])'])))


def test_leading_comments_become_the_description_without_licence_or_author_lines():
    head = "# Copyright (c) 2020 Someone\n# Licensed under the MIT License\n# Dilutes the sample plate in two steps.\n# Author: someone@example.org\n"
    spec = spec_of(make(head=head))
    assert "Dilutes the sample plate in two steps." in spec["description"]
    assert not any(w in spec["description"] for w in ("Copyright", "MIT", "@"))
    assert spec["has_description"] is True
    assert spec_of(make())["has_description"] is False


def test_get_values_and_runtime_parameters_are_flagged():
    spec = spec_of(make(setup='[n] = get_values("n")  # noqa: F821'))
    assert spec["uses_get_values"] is True and spec["uses_runtime_parameters"] is False


# ---------------------------------------------------------------- family labels

FAMILY_CASES = [
    ("plain", make(), ("none", False)),
    ("golden gate", make("Golden Gate assembly of four parts"), ("golden-gate-assembly", False)),
    ("colony pcr", make(head="# Colony PCR screening: master mix, then colony template\n"), ("colony-pcr-screening", False)),
    ("heat shock", make(head="# heat shock the cells\n"), ("ecoli-heat-shock-transformation", False)),
    ("ampure", make(head="# AMPure XP cleanup\n", setup='mag = protocol.load_module("magnetic module gen2", 4)'), ("ampure-bead-cleanup", False)),
    ("rna extraction", make("Viral RNA extraction"), ("opentrons-rna-extraction", False)),
    ("a1 to a12", make(head="# add 100 uL of liquid to wells A1->A12\n"), ("a1-a12-100ul", False)),
    ("weak keyword only", make(setup='pcr_plate = protocol.load_labware("nest_96_wellplate_100ul_pcr_full_skirt", 5)'), ("colony-pcr-screening", True)),
    ("one source one volume", make(steps=POUR_STEPS), ("a1-a12-100ul", True)),
    ("aspirate/dispense pairs", make(steps=PAIR_STEPS), ("a1-a12-100ul", True)),
    ("one volume, several sources", make(steps=ONE_VOLUME_STEPS), ("a1-a12-100ul", True)),
    ("tiny protocol", make(steps=['p300.transfer(30, src["A1"], plate["B2"])']), ("a1-a12-100ul", True)),
    ("42 C step alone", make(setup=TEMP_MODULE, steps=["temp.set_temperature(42)"] + DIVERSE_STEPS), ("ecoli-heat-shock-transformation", True)),
]


@pytest.mark.parametrize("text,expected", [c[1:] for c in FAMILY_CASES], ids=[c[0] for c in FAMILY_CASES])
def test_family_label(text, expected):
    label = corpus.classify_family(spec_of(text))
    assert (label.family, label.doubt) == expected, label.reasons


def test_doubt_always_names_a_family_and_clean_means_none():
    for _, text, _ in FAMILY_CASES:
        label = corpus.classify_family(spec_of(text))
        assert (label.family == "none") == (label.reasons == []) and (label.family in corpus.HELDOUT_FAMILIES or label.family == "none")
        assert not (label.doubt and label.family == "none")


def test_magnetic_module_alone_is_a_doubt_between_the_two_bead_families():
    label = corpus.classify_family(spec_of(make(setup='mag = protocol.load_module("magdeck", 4)', steps=["mag.engage()"] + DIVERSE_STEPS)))
    assert label.doubt is True
    assert label.family in ("ampure-bead-cleanup", "opentrons-rna-extraction")


def test_two_strong_families_is_a_doubt_that_names_one_of_them():
    label = corpus.classify_family(spec_of(make("Golden Gate assembly then heat shock transformation")))
    assert label.doubt is True
    assert label.family in ("golden-gate-assembly", "ecoli-heat-shock-transformation")


def test_labware_load_names_do_not_trigger_keywords():
    # a PCR plate chosen by catalogue name only does not make a PCR protocol
    label = corpus.classify_family(spec_of(make(setup='dest = protocol.load_labware("biorad_96_wellplate_200ul_pcr", 5)')))
    assert (label.family, label.doubt) == ("none", False)


def test_path_words_count_as_signals():
    spec = spec_of(make(), rel="Exp802.05 Wastewater Extraction/run.py")
    assert corpus.classify_family(spec).family == "opentrons-rna-extraction"


def test_heldout_families_are_the_seven_text2wetlab_families():
    assert len(corpus.HELDOUT_FAMILIES) == 7 and len(set(corpus.HELDOUT_FAMILIES)) == 7


# ---------------------------------------------------------------- simulator gate

def test_simulate_file_pass_fail_and_timeout():
    ok = corpus.simulate_file("a.py", simulator="/x/opentrons_simulate", runner=FakeRunner())
    assert ok == {"passed": True, "status": "pass", "detail": ""}
    bad = corpus.simulate_file("b.py", simulator="/x/opentrons_simulate", runner=FakeRunner([("b.py", (1, "Traceback...\nNameError: name 'get_values' is not defined\n"))]))
    assert bad["passed"] is False and bad["status"] == "fail" and "get_values" in bad["detail"]
    slow = corpus.simulate_file("c.py", simulator="/x/opentrons_simulate", timeout=5, runner=FakeRunner([("c.py", TimeoutError())]))
    assert slow["passed"] is False and slow["status"] == "timeout"


def test_failure_detail_skips_asyncio_cleanup_noise():
    out = "Traceback (most recent call last):\n  File \"x\", line 1\nopentrons.errors.ExceptionInProtocolError: FileNotFoundError [line 84]: Labware \"x\" not found\nCaught exception: {'message': 'Task was destroyed but it is pending!'}\n"
    res = corpus.simulate_file("d.py", simulator="/x/opentrons_simulate", runner=FakeRunner([("d.py", (1, out))]))
    assert res["detail"].startswith("opentrons.errors.ExceptionInProtocolError: FileNotFoundError")


def test_simulator_runs_with_a_scrubbed_environment(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-not-real")
    monkeypatch.setenv("GH_TOKEN", "gho_not_real")
    runner = FakeRunner()
    corpus.simulate_file("/data/p.py", simulator="/venv/bin/opentrons_simulate", timeout=7, runner=runner)
    (call,) = runner.calls
    assert call["argv"] == ["/venv/bin/opentrons_simulate", "-o", "nothing", "/data/p.py"]
    assert set(call["env"]) == {"PATH", "HOME", "PYTHONIOENCODING", "PYTHONDONTWRITEBYTECODE"}
    assert call["env"]["PATH"].startswith("/venv/bin")
    assert call["cwd"] == call["env"]["HOME"] and call["timeout"] == 7
    assert "sk-not-real" not in json.dumps(call["env"]) and "gho_not_real" not in json.dumps(call["env"])


def test_subprocess_runner_hides_secrets_and_returns_output(monkeypatch, tmp_path):
    monkeypatch.setenv("S95_FAKE_SECRET", "hunter2")
    code = "import os; print('secret=' + str(os.environ.get('S95_FAKE_SECRET')))"
    rc, out = corpus.subprocess_runner([sys.executable, "-c", code], env=corpus.scrubbed_env(sys.executable, str(tmp_path)), cwd=str(tmp_path), timeout=60)
    assert rc == 0 and "secret=None" in out


def test_subprocess_runner_times_out(tmp_path):
    started = time.time()
    with pytest.raises(TimeoutError):
        corpus.subprocess_runner([sys.executable, "-c", "import time; time.sleep(60)"], env=corpus.scrubbed_env(sys.executable, str(tmp_path)), cwd=str(tmp_path), timeout=0.5)
    assert time.time() - started < 20


def test_simulate_candidates_sets_flags_in_place(tmp_path):
    write(tmp_path, "ok.py", make())
    write(tmp_path, "bad_one.py", make("Another cherry picking").replace("(30,", "(31,"))
    cands = list(corpus.walk_candidates(tmp_path, "src"))
    assert [c.record["simulate_pass"] for c in cands] == [None, None]
    corpus.simulate_candidates(cands, simulator="/x/sim", workers=3, runner=FakeRunner([("bad_one", (1, "boom"))]))
    assert {c.info["path"]: c.record["simulate_pass"] for c in cands} == {"bad_one.py": False, "ok.py": True}
    assert [c.info["simulate"]["detail"] for c in cands] == ["boom", ""]


# ---------------------------------------------------------------- walking and records

def build_tree(root):
    write(root, "a_plain.py", make())
    write(root, "b_flex.py", make(extra_meta='    "robotType": "Flex",\n', api="2.19"))
    write(root, "c_broken.py", "metadata = {'apiLevel': '2.13'}\n<<<<<<< HEAD\ndef run(protocol):\n    pass\n")
    write(root, "d_notproto.py", 'apiLevel = "2.13"\ndef run(x):\n    return x\n')
    write(root, "e_dup.py", make() + "\n# a trailing comment that normalising removes\n")
    write(root, "f_noliquid.py", make(steps=["protocol.comment('nothing to do')"]))
    write(root, "g_prov.py", make("From the library", extra_meta='    "source": "Protocol Library",\n'))
    write(root, ".git/hook.py", make("Hidden"))
    write(root, "sub/h_ok.py", make("Second cherry picking", steps=[
        'p300.transfer(12, src["C1"], plate["F6"])', 'p300.transfer(14, src["C2"], plate["F7"])',
        'p300.transfer(16, src["C3"], plate["F8"])', 'p300.transfer(18, src["C4"], plate["F9"])']))
    return Path(root)


def test_walk_yields_records_in_the_manifest_shape_and_reports_skips(tmp_path):
    build_tree(tmp_path)
    skipped = []
    cands = list(corpus.walk_candidates(tmp_path, "owner/repo@abc123", skipped=skipped))
    assert [c.record["id"] for c in cands] == ["owner/repo@abc123:a_plain.py", "owner/repo@abc123:sub/h_ok.py"]
    rec = cands[0].record
    assert tuple(rec) == corpus.RECORD_KEYS
    assert rec["source"] == "owner/repo@abc123" and rec["code"] == (tmp_path / "a_plain.py").read_text()
    assert rec["refused_by_base"] is None and rec["simulate_pass"] is None
    assert rec["heldout_family"] == "none" and rec["family_doubt"] is False
    assert rec["prompt"].startswith("Write an Opentrons OT-2 Python protocol")
    assert {s["path"]: s["reason"].split(":")[0] for s in skipped} == {
        "b_flex.py": "flex", "c_broken.py": "unparseable", "d_notproto.py": "not-a-protocol", "e_dup.py": "duplicate-of",
        "f_noliquid.py": "no-liquid-handling", "g_prov.py": "provenance"}
    assert {"path": "e_dup.py", "reason": "duplicate-of:owner/repo@abc123:a_plain.py"} in skipped


def test_a_relative_root_still_gives_the_simulator_an_absolute_path(tmp_path, monkeypatch):
    write(tmp_path, "repo/p.py", make())
    monkeypatch.chdir(tmp_path)
    cands = list(corpus.walk_candidates("repo", "s"))
    runner = FakeRunner()
    corpus.simulate_candidates(cands, simulator="/x/sim", runner=runner)
    assert Path(runner.calls[0]["argv"][-1]).is_absolute() and Path(runner.calls[0]["argv"][-1]).exists()
    assert cands[0].record["id"] == "s:p.py"


def test_one_pathological_file_does_not_stop_the_build(tmp_path, monkeypatch):
    write(tmp_path, "boom.py", make("Explodes"))
    write(tmp_path, "fine.py", make("Fine"))
    real = corpus.analyse_protocol

    def flaky(text, relpath=""):
        if "Explodes" in text:
            raise RuntimeError("unexpected node")
        return real(text, relpath)

    monkeypatch.setattr(corpus, "analyse_protocol", flaky)
    skipped = []
    cands = list(corpus.walk_candidates(tmp_path, "s", skipped=skipped))
    assert [c.info["path"] for c in cands] == ["fine.py"]
    assert skipped == [{"path": "boom.py", "reason": "analysis-error:RuntimeError"}]


OTHER_PROTOCOL = '''from opentrons import protocol_api

metadata = {"apiLevel": "2.14", "protocolName": "Reagent addition to a strip"}


def run(ctx: protocol_api.ProtocolContext):
    racks = [ctx.load_labware("opentrons_96_tiprack_20ul", s) for s in (4, 5)]
    strip = ctx.load_labware("biorad_96_wellplate_200ul_pcr", 6)
    stock = ctx.load_labware("opentrons_15_tuberack_falcon_15ml_conical", 8)
    left = ctx.load_instrument("p20_single_gen2", "left", tip_racks=racks)
    left.transfer(5, stock["A1"], strip["A1"], new_tip="always")
    left.transfer(7, stock["A2"], strip["B1"], new_tip="always")
    left.transfer(9, stock["A3"], strip["C1"], new_tip="always")
    left.transfer(11, stock["B1"], strip["D1"], new_tip="always")
'''


def test_files_copied_from_a_reference_set_are_kept_out(tmp_path):
    held_out = make("Held-out solution")
    write(tmp_path / "benchmark", "tasks/one/solution/protocol.py", held_out)
    write(tmp_path / "repo", "exact_copy.py", held_out)
    write(tmp_path / "repo", "reformatted.py", held_out.replace("\n    ", "\n      ") + "\n# my notes\n")
    write(tmp_path / "repo", "one_line_changed.py", held_out.replace('plate["B2"]', 'plate["B3"]'))
    write(tmp_path / "repo", "unrelated.py", OTHER_PROTOCOL)
    skipped = []
    cands = list(corpus.walk_candidates(tmp_path / "repo", "s", skipped=skipped, avoid=corpus.Reference([tmp_path / "benchmark"])))
    assert [c.info["path"] for c in cands] == ["unrelated.py"]
    assert {s["path"]: s["reason"] for s in skipped} == {
        "exact_copy.py": "matches-reference:benchmark/tasks/one/solution/protocol.py",
        "reformatted.py": "matches-reference:benchmark/tasks/one/solution/protocol.py",
        "one_line_changed.py": "matches-reference:benchmark/tasks/one/solution/protocol.py"}


def test_cli_avoid_flag_skips_reference_copies(tmp_path):
    write(tmp_path / "benchmark", "protocol.py", make("Held-out solution"))
    write(tmp_path / "repo", "copy.py", make("Held-out solution"))
    write(tmp_path / "repo", "fine.py", OTHER_PROTOCOL)
    out = tmp_path / "c.jsonl"
    assert corpus.main([str(tmp_path / "repo"), "--source", "s", "--out", str(out), "--simulator", "none", "--avoid", str(tmp_path / "benchmark")]) == 0
    assert [json.loads(l)["id"] for l in out.read_text(encoding="utf-8").splitlines()] == ["s:fine.py"]


def test_a_shared_seen_dict_drops_duplicates_across_sources(tmp_path):
    write(tmp_path / "one", "p.py", make())
    write(tmp_path / "two", "copy.py", make() + "\n# same code, other repository\n")
    seen, skipped = {}, []
    first = list(corpus.walk_candidates(tmp_path / "one", "a/one@1", seen=seen, skipped=skipped))
    second = list(corpus.walk_candidates(tmp_path / "two", "b/two@2", seen=seen, skipped=skipped))
    assert len(first) == 1 and second == []
    assert skipped == [{"path": "copy.py", "reason": "duplicate-of:a/one@1:p.py"}]


def test_walk_exclude_glob_and_symlinks(tmp_path):
    root = tmp_path / "root"
    write(root, "keep.py", make())
    write(root, "machine_output/x.py", make("Generated", steps=['p300.transfer(1, src["A1"], plate["A1"])', 'p300.transfer(2, src["A2"], plate["A2"])',
                                                               'p300.transfer(3, src["A3"], plate["A3"])', 'p300.transfer(4, src["A4"], plate["A4"])']))
    outside = write(tmp_path, "outside.py", make("Outside"))
    (root / "link.py").symlink_to(outside)
    skipped = []
    cands = list(corpus.walk_candidates(root, "s", exclude=["machine_output/*"], skipped=skipped))
    assert [c.info["path"] for c in cands] == ["keep.py"]
    assert {"path": "machine_output/x.py", "reason": "excluded-by-glob"} in skipped
    assert all(s["path"] != "link.py" for s in skipped)


# ---------------------------------------------------------------- funnel and CLI

def test_funnel_counts_survivors_and_independent_protocols(tmp_path):
    variant = make().replace("(30,", "(31,").replace("(40,", "(41,").replace("(15,", "(16,").replace("(25,", "(26,")
    write(tmp_path, "one.py", make())
    write(tmp_path, "two_same_structure.py", variant)  # same procedure, different numbers
    write(tmp_path, "three.py", make("Cherry picking by loop", steps=[
        'for w in ("A1", "A2", "A3", "A4"):\n    p300.transfer(10, src[w], plate[w])',
        'p300.transfer(5, src["B1"], plate["B1"])', 'p300.transfer(6, src["B2"], plate["B2"])', 'p300.transfer(7, src["B3"], plate["B3"])']))
    write(tmp_path, "four_golden_gate.py", make("Golden Gate assembly"))
    skipped = []
    cands = list(corpus.walk_candidates(tmp_path, "s", skipped=skipped))
    corpus.simulate_candidates(cands, simulator="/x/sim", runner=FakeRunner([("three", (1, "fail"))]))
    f = corpus.funnel(cands, skipped)
    assert f["candidates"] == 4 and f["heldout_flagged"] == 1 and f["family_doubt"] == 0
    assert f["simulate_pass"] == 3 and f["simulate_fail"] == 1
    assert f["no_family_flag"] == 3 and f["survivors"] == 2  # one.py and two_same_structure.py
    assert f["independent_survivors"] == 1  # they share one code skeleton


def test_cli_writes_jsonl_and_report(tmp_path, capsys):
    root = build_tree(tmp_path / "repo")
    sim = tmp_path / "fake_simulate"
    sim.write_text("#!/bin/sh\n")
    sim.chmod(sim.stat().st_mode | stat.S_IXUSR)
    out, report = tmp_path / "candidates.jsonl", tmp_path / "report.json"
    runner = FakeRunner([("h_ok", (1, "bad labware"))])
    rc = corpus.main([str(root), "--source", "owner/repo@abc", "--out", str(out), "--report", str(report), "--simulator", str(sim), "--workers", "2"], runner=runner)
    assert rc == 0
    rows = [json.loads(l) for l in out.read_text(encoding="utf-8").splitlines()]
    assert [list(r) for r in rows] == [list(corpus.RECORD_KEYS)] * 2
    assert {r["id"].split(":")[1]: r["simulate_pass"] for r in rows} == {"a_plain.py": True, "sub/h_ok.py": False}
    data = json.loads(report.read_text(encoding="utf-8"))
    assert data["funnel"]["candidates"] == 2 and data["funnel"]["skipped_by_reason"]["flex"] == 1
    assert all(call["env"].get("HOME") for call in runner.calls)
    assert '"survivors"' in capsys.readouterr().out


def test_cli_without_a_simulator_leaves_simulate_pass_null(tmp_path):
    root = build_tree(tmp_path / "repo")
    out = tmp_path / "c.jsonl"
    assert corpus.main([str(root), "--source", "s", "--out", str(out), "--simulator", "none"]) == 0
    assert {json.loads(l)["simulate_pass"] for l in out.read_text(encoding="utf-8").splitlines()} == {None}


def test_cli_rejects_a_simulator_that_is_not_executable(tmp_path):
    assert corpus.main([str(tmp_path), "--source", "s", "--out", str(tmp_path / "o.jsonl"), "--simulator", str(tmp_path / "nope")]) == 2
