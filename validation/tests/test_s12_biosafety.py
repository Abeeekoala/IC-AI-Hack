"""S12 biosecurity gate: the state is never stronger than what was obtained.

Every sequence here is made up (random residues from a seeded generator); no real or hazardous sequence is used.
"""
import copy
import hashlib
import json
import random
import socket
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(ROOT / "validation"), str(ROOT / "src")]

import pytest  # noqa: E402
from vaxforge import cli  # noqa: E402
from vaxforge import s12_biosafety as s12  # noqa: E402
from vaxforge.artifacts import ContractError, RunStore  # noqa: E402
from vaxforge.config import Config, ConfigError  # noqa: E402

AA = "ACDEFGHIKLMNPQRSTVWY"
VIR_FALSE = {"status": "no_hit", "is_virulence_associated": False, "evidence": "none"}
VIR_TRUE = {"status": "hit", "is_virulence_associated": True, "evidence": "strong"}
VIR_NOT_RUN = {"status": "not_run", "is_virulence_associated": None, "evidence": "n/a"}
VIR_NOT_APPLICABLE = {"status": "not_applicable", "is_virulence_associated": None, "evidence": "n/a"}


def seq(name):
    rng = random.Random(name)
    return "M" + "".join(rng.choice(AA) for _ in range(40))


def test_the_made_up_sequences_differ():
    assert len({seq(n) for n in PROTEINS}) == len(PROTEINS)


def digest(name):
    return s12.sequence_digest(seq(name))


# protein id -> (S2 virulence block, S4 toxicity status or None for "the gate left no result")
PROTEINS = {
    "clean1": (VIR_FALSE, "pass"), "clean2": (VIR_FALSE, "pass"),
    "virulent": (VIR_TRUE, "pass"), "toxic": (VIR_FALSE, "flag"), "rescued_toxic": (VIR_FALSE, "reject"),
    "no_toxin_result": (VIR_FALSE, None), "toxin_unavailable": (VIR_FALSE, "unavailable"),
    "virulence_not_run": (VIR_NOT_RUN, "pass"), "virulence_n_a": (VIR_NOT_APPLICABLE, "pass"),
}


def make_store(tmp_path, proteins=PROTEINS, extra_candidates=()):
    store = RunStore(tmp_path / "runs" / "t")
    s2 = {"proteome": {}, "thresholds": {}, "tools": {}, "summary": {}, "columns": [],
          "proteins": [{"protein_id": p, "virulence": v} for p, (v, _) in proteins.items()]}
    candidates = [{"protein_id": p, "sequence": seq(p),
                   "gates": {} if t is None else {"toxicity": {"status": t, "reason": "made up"}}}
                  for p, (_, t) in proteins.items()] + list(extra_candidates)
    store.write_artifact("s2", s2, inputs=[], status="ok")
    store.write_artifact("s4", {"candidates": candidates, "rejected": []}, inputs=[], status="ok")
    return store


def write_order(tmp_path, records):
    """records: {id: sequence}"""
    path = tmp_path / "order.fasta"
    path.write_text("".join(f">{i} made up\n{s}\n" for i, s in records.items()), encoding="utf-8")
    return path


def write_record(tmp_path, verdicts, **overrides):
    """verdicts: {digest: verdict}"""
    doc = {"provider": "Example provider", "tool": "example-screen", "tool_version": "1.2.3", "date": "2026-11-14",
           "results": [{"digest": d, "verdict": v} for d, v in verdicts.items()]}
    doc.update(overrides)
    path = tmp_path / "screening.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def cleared_inputs(tmp_path, names=("clean1", "clean2")):
    return write_order(tmp_path, {n: seq(n) for n in names}), write_record(tmp_path, {digest(n): "pass" for n in names})


def local(art, name, check):
    row = next(r for r in art.payload["sequences"] if r["id"] == name)
    return row["local_checks"][check]["result"]


# ---------------------------------------------------------------- the three states
def test_local_checks_and_a_pass_for_every_sequence_clear_the_gate(tmp_path):
    store = make_store(tmp_path)
    order, record = cleared_inputs(tmp_path)
    art = s12.check(store, order, record)
    assert art.payload["state"] == "cleared" and art.status == "ok" and art.warnings == []
    assert all(r["provider_verdict"] == "pass" for r in art.payload["sequences"])
    assert art.payload["provider_record"]["covers_every_ordered_sequence"] is True
    assert art.inputs.keys() == {"s2", "s4"} and store.verify() == []


def test_no_provider_record_is_not_screened_never_cleared(tmp_path):
    store = make_store(tmp_path)
    order, _ = cleared_inputs(tmp_path)
    art = s12.check(store, order)
    assert art.payload["state"] == "not_screened"
    assert art.payload["provider_record"] == {"status": "not_supplied"}
    assert all(r["provider_verdict"] == "not_screened" for r in art.payload["sequences"])
    assert all(c["result"] == "pass" for r in art.payload["sequences"] for c in r["local_checks"].values())  # the local checks alone clear nothing
    assert "no provider screening record" in art.payload["reasons"][0] and art.warnings


def test_a_record_that_misses_one_ordered_sequence_is_not_screened(tmp_path):
    store = make_store(tmp_path)
    order = write_order(tmp_path, {n: seq(n) for n in ("clean1", "clean2")})
    art = s12.check(store, order, write_record(tmp_path, {digest("clean1"): "pass", digest("virulent"): "pass"}))
    assert art.payload["state"] == "not_screened"
    assert art.payload["provider_record"]["covers_every_ordered_sequence"] is False
    assert [r["provider_verdict"] for r in art.payload["sequences"]] == ["pass", "not_screened"]
    assert "does not cover 1 of 2" in art.payload["reasons"][0]


def test_no_order_list_is_not_screened_and_checks_nothing(tmp_path):
    art = s12.check(make_store(tmp_path))
    assert art.payload["state"] == "not_screened" and art.payload["order"] == {"status": "not_supplied"}
    assert art.payload["sequences"] == [] and "no order list" in art.payload["reasons"][0]


@pytest.mark.parametrize("verdict", ["flag", "error"])
def test_a_flag_or_error_verdict_blocks_even_when_the_rest_pass(tmp_path, verdict):
    store = make_store(tmp_path)
    order = write_order(tmp_path, {n: seq(n) for n in ("clean1", "clean2")})
    art = s12.check(store, order, write_record(tmp_path, {digest("clean1"): "pass", digest("clean2"): verdict}))
    assert art.payload["state"] == "blocked"
    assert f"clean2: the provider record gives verdict {verdict}" in art.payload["reasons"]


def test_a_flag_verdict_blocks_even_when_the_record_covers_only_that_sequence(tmp_path):
    store = make_store(tmp_path)
    order = write_order(tmp_path, {n: seq(n) for n in ("clean1", "clean2")})
    assert s12.check(store, order, write_record(tmp_path, {digest("clean1"): "flag"})).payload["state"] == "blocked"


# ---------------------------------------------------------------- local checks, each reported separately
def test_a_sequence_the_pipeline_did_not_select_is_blocked_even_with_a_pass_verdict(tmp_path):
    store = make_store(tmp_path)
    stranger = seq("not in the pipeline")
    order = write_order(tmp_path, {"clean1": seq("clean1"), "stranger": stranger})
    art = s12.check(store, order, write_record(tmp_path, {digest("clean1"): "pass", s12.sequence_digest(stranger): "pass"}))
    assert art.payload["state"] == "blocked"
    assert local(art, "stranger", "selected_candidate") == "fail" and local(art, "clean1", "selected_candidate") == "pass"
    # with no candidate to read, the flags were not read: reported as such, not as a pass
    assert local(art, "stranger", "virulence_flag") == "not_evaluated" and local(art, "stranger", "toxin_flag") == "not_evaluated"


def test_a_virulence_associated_candidate_is_blocked(tmp_path):
    art = s12.check(make_store(tmp_path), write_order(tmp_path, {"virulent": seq("virulent")}),
                    write_record(tmp_path, {digest("virulent"): "pass"}))
    assert art.payload["state"] == "blocked"
    assert local(art, "virulent", "virulence_flag") == "fail" and local(art, "virulent", "toxin_flag") == "pass"
    assert "virulence-associated (evidence strong)" in art.payload["reasons"][0]


@pytest.mark.parametrize("name,result", [("toxic", "fail"), ("rescued_toxic", "fail"),
                                         ("no_toxin_result", "not_evaluated"), ("toxin_unavailable", "not_evaluated")])
def test_a_toxin_flag_or_a_missing_toxicity_result_blocks(tmp_path, name, result):
    art = s12.check(make_store(tmp_path), write_order(tmp_path, {name: seq(name)}), write_record(tmp_path, {digest(name): "pass"}))
    assert art.payload["state"] == "blocked" and local(art, name, "toxin_flag") == result
    assert local(art, name, "virulence_flag") == "pass"


def test_a_virulence_flag_that_was_not_computed_blocks_but_not_applicable_does_not(tmp_path):
    store = make_store(tmp_path)
    record = write_record(tmp_path, {digest("virulence_not_run"): "pass", digest("virulence_n_a"): "pass"})
    not_run = s12.check(store, write_order(tmp_path, {"virulence_not_run": seq("virulence_not_run")}), record)
    assert not_run.payload["state"] == "blocked" and local(not_run, "virulence_not_run", "virulence_flag") == "not_evaluated"
    n_a = s12.check(store, write_order(tmp_path, {"virulence_n_a": seq("virulence_n_a")}), record)
    assert n_a.payload["state"] == "cleared" and local(n_a, "virulence_n_a", "virulence_flag") == "not_applicable"


def test_a_candidate_missing_from_s2_has_no_virulence_flag_and_is_blocked(tmp_path):
    store = make_store(tmp_path, extra_candidates=[{"protein_id": "only_in_s4", "sequence": seq("only_in_s4"),
                                                    "gates": {"toxicity": {"status": "pass"}}}])
    art = s12.check(store, write_order(tmp_path, {"only_in_s4": seq("only_in_s4")}), write_record(tmp_path, {digest("only_in_s4"): "pass"}))
    assert art.payload["state"] == "blocked" and local(art, "only_in_s4", "virulence_flag") == "not_evaluated"


def test_when_two_candidates_share_a_sequence_the_worse_flag_counts(tmp_path):
    twin = {"protein_id": "twin", "sequence": seq("clean1"), "gates": {"toxicity": {"status": "flag", "reason": "made up"}}}
    store = make_store(tmp_path, extra_candidates=[twin])
    art = s12.check(store, write_order(tmp_path, {"clean1": seq("clean1")}), write_record(tmp_path, {digest("clean1"): "pass"}))
    assert art.payload["state"] == "blocked" and local(art, "clean1", "toxin_flag") == "fail"
    assert "twin" in art.payload["sequences"][0]["local_checks"]["toxin_flag"]["detail"]


@pytest.mark.parametrize("sequence,why", [("MKT*", "*"), ("MKTXAB", "B X"), ("MK TA", " "), ("", "empty")])
def test_sequence_sanity_rejects_bad_residues_and_empty_sequences(tmp_path, sequence, why):
    result = s12._sanity(sequence)
    assert result["result"] == "fail" and why in result["detail"]


def test_sequence_sanity_rejects_an_over_long_sequence_and_accepts_a_normal_one(tmp_path, monkeypatch):
    monkeypatch.setattr(s12, "MAX_LENGTH", 20)
    assert s12._sanity("A" * 21)["result"] == "fail"
    assert s12._sanity("A" * 20)["result"] == "pass"


def test_a_lower_case_order_matches_the_candidate_and_passes(tmp_path):
    store = make_store(tmp_path)
    order = write_order(tmp_path, {"clean1": seq("clean1").lower()})
    art = s12.check(store, order, write_record(tmp_path, {digest("clean1"): "pass"}))
    assert art.payload["state"] == "cleared"


def test_a_nucleotide_order_does_not_match_a_protein_candidate(tmp_path):
    art = s12.check(make_store(tmp_path), write_order(tmp_path, {"gene": "ATGGCGCGTAAA"}))
    assert art.payload["state"] == "blocked" and local(art, "gene", "selected_candidate") == "fail"


# ---------------------------------------------------------------- the provider record
def test_the_record_and_order_hashes_and_fields_are_recorded(tmp_path):
    store = make_store(tmp_path)
    order, record = cleared_inputs(tmp_path)
    art = s12.check(store, order, record)
    rec = art.payload["provider_record"]
    assert rec["file"]["sha256"] == hashlib.sha256(record.read_bytes()).hexdigest() and rec["file"]["bytes"] == record.stat().st_size
    assert (rec["provider"], rec["tool"], rec["tool_version"], rec["date"]) == ("Example provider", "example-screen", "1.2.3", "2026-11-14")
    assert rec["n_results"] == 2 and "not verified" in rec["authenticity"]
    assert art.payload["order"]["file"]["sha256"] == hashlib.sha256(order.read_bytes()).hexdigest()
    assert art.payload["order"]["n_sequences"] == 2
    assert art.volatile["screening_path"] == str(record)


def test_digests_in_the_record_may_omit_the_prefix_and_use_upper_case(tmp_path):
    store = make_store(tmp_path)
    bare = digest("clean1").removeprefix("sha256:").upper()
    art = s12.check(store, write_order(tmp_path, {"clean1": seq("clean1")}), write_record(tmp_path, {bare: "pass"}))
    assert art.payload["state"] == "cleared"


def test_sequence_digest_ignores_case_and_whitespace_only():
    assert s12.sequence_digest("ac d\ne") == s12.sequence_digest("ACDE") != s12.sequence_digest("ACDF")


@pytest.mark.parametrize("break_it", [
    lambda d: d.pop("provider"), lambda d: d.update(tool_version=" "), lambda d: d.pop("date"),
    lambda d: d.update(results=[]), lambda d: d.pop("results"), lambda d: d["results"].append({"digest": "x", "verdict": "ok"}),
    lambda d: d["results"].append({"verdict": "pass"}), lambda d: d["results"].append(copy.deepcopy(d["results"][0])),
])
def test_a_malformed_record_is_an_error_not_a_state(tmp_path, break_it):
    doc = {"provider": "p", "tool": "t", "tool_version": "1", "date": "2026-11-14", "results": [{"digest": "sha256:ab", "verdict": "pass"}]}
    break_it(doc)
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        s12.read_screening(path)
    path.write_text("[1, 2]", encoding="utf-8")
    with pytest.raises(ValueError, match="JSON object"):
        s12.read_screening(path)
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(ValueError):
        s12.read_screening(path)


def test_an_empty_or_ambiguous_order_list_is_an_error(tmp_path):
    empty = tmp_path / "empty.fasta"
    empty.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="no sequences"):
        s12.read_order(empty)
    dup = tmp_path / "dup.fasta"
    dup.write_text(">a\nMKT\n>a\nMKV\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unique id"):
        s12.read_order(dup)


# ---------------------------------------------------------------- the artifact and its inputs
def test_the_artifact_says_plainly_what_the_local_checks_are_not(tmp_path):
    art = s12.check(make_store(tmp_path), write_order(tmp_path, {"clean1": seq("clean1")}))
    limits = " ".join(art.payload["limits"])
    assert "not a synthesis-screening result and never substitute for one" in limits
    assert "No provider or network was contacted" in limits
    assert "NOT A SYNTHESIS-SCREENING RESULT" in s12.__doc__


@pytest.mark.parametrize("stage", ["s2", "s4"])
def test_a_missing_upstream_artifact_is_a_contract_error(tmp_path, stage):
    store = make_store(tmp_path)
    store.artifact_path(stage).unlink()
    with pytest.raises(ContractError, match=f"{stage} artifact not found"):
        s12.check(store, write_order(tmp_path, {"clean1": seq("clean1")}))


def test_an_upstream_artifact_that_is_not_ok_is_refused(tmp_path):
    store = make_store(tmp_path)
    store.write_artifact("s4", {}, inputs=[], status="failed", warnings=["S4 broke"])
    with pytest.raises(ContractError, match="status 'failed'"):
        s12.check(store, write_order(tmp_path, {"clean1": seq("clean1")}))


def test_checking_makes_no_network_connection(tmp_path, monkeypatch):
    def refuse(*a, **k):
        raise AssertionError("S12 must not open a socket")
    monkeypatch.setattr(socket, "socket", refuse)
    monkeypatch.setattr(socket, "create_connection", refuse)
    order, record = cleared_inputs(tmp_path)
    assert s12.check(make_store(tmp_path), order, record).payload["state"] == "cleared"


# ---------------------------------------------------------------- vaxforge run
def cfg(tmp_path, **biosafety):
    return Config({"biosafety": biosafety}, root=tmp_path) if biosafety else Config({}, root=tmp_path)


def test_the_run_entrypoint_with_no_order_writes_an_ok_not_screened_artifact(tmp_path):
    store = make_store(tmp_path)
    outcome, art = cli.run_extra_stage("s12", store, cfg(tmp_path))
    assert outcome == "ran" and art.status == "ok" and art.stage == "s12"
    assert art.payload["state"] == "not_screened" and art.payload["order"]["status"] == "not_supplied"
    assert store.verify() == []


def test_the_run_entrypoint_reads_the_order_and_record_from_config(tmp_path):
    order, record = cleared_inputs(tmp_path)
    art = s12.run(make_store(tmp_path), cfg(tmp_path, order=str(order), screening=str(record)))
    assert art.payload["state"] == "cleared"


def test_the_run_entrypoint_turns_unusable_input_into_a_config_error(tmp_path):
    store = make_store(tmp_path)
    order, record = cleared_inputs(tmp_path)
    with pytest.raises(ConfigError, match="biosafety.order is not"):
        s12.run(store, cfg(tmp_path, screening=str(record)))
    with pytest.raises(ConfigError, match="S12"):
        s12.run(store, cfg(tmp_path, order=str(tmp_path / "missing.fasta")))
    with pytest.raises(ConfigError, match="S12"):
        s12.run(store, cfg(tmp_path, order=str(order), screening=str(tmp_path / "missing.json")))


# ---------------------------------------------------------------- the command line
@pytest.fixture
def vaxforge(monkeypatch):
    """`vaxforge biosafety ...` through the real parser. cli.py (stream A) does not list s12_biosafety yet."""
    monkeypatch.setattr(cli, "EXTENSION_MODULES", [*cli.EXTENSION_MODULES, "s12_biosafety"])
    return cli.main


def run_dir(store):
    return str(store.run_dir)


def test_exit_code_is_zero_only_when_cleared(tmp_path, vaxforge, capsys):
    store = make_store(tmp_path)
    order, record = cleared_inputs(tmp_path)
    assert vaxforge(["biosafety", "check", str(order), "--run", run_dir(store), "--screening", str(record)]) == 0
    assert "S12 biosecurity gate: cleared" in capsys.readouterr().out
    assert vaxforge(["biosafety", "check", str(order), "--run", run_dir(store)]) == 3
    assert "S12 biosecurity gate: not_screened" in capsys.readouterr().out
    flagged = write_record(tmp_path, {digest("clean1"): "pass", digest("clean2"): "flag"})
    assert vaxforge(["biosafety", "check", str(order), "--run", run_dir(store), "--screening", str(flagged)]) == 2
    assert "S12 biosecurity gate: blocked" in capsys.readouterr().out
    assert json.loads(store.artifact_path("s12").read_text())["payload"]["state"] == "blocked"  # the last result is what is on disk


def test_unusable_input_exits_one_with_a_message(tmp_path, vaxforge, capsys):
    store = make_store(tmp_path)
    order, record = cleared_inputs(tmp_path)
    ghost = tmp_path / "no_such_run"
    assert vaxforge(["biosafety", "check", str(order), "--run", str(ghost)]) == 1
    assert not ghost.exists() and "not a run directory" in capsys.readouterr().err
    assert vaxforge(["biosafety", "check", str(tmp_path / "missing.fasta"), "--run", run_dir(store)]) == 1
    assert vaxforge(["biosafety", "check", str(order), "--run", run_dir(store), "--screening", str(order)]) == 1
    assert capsys.readouterr().err.count("s12_biosafety:") == 2
    bare = RunStore(tmp_path / "runs" / "bare")
    assert vaxforge(["biosafety", "check", str(order), "--run", str(bare.run_dir)]) == 1
    assert "s2 artifact not found" in capsys.readouterr().err
    assert not bare.has("s12")


def test_the_module_runs_on_its_own_before_cli_lists_it(tmp_path, capsys):
    store = make_store(tmp_path)
    order, record = cleared_inputs(tmp_path)
    assert s12.main(["check", str(order), "--run", run_dir(store), "--screening", str(record)]) == 0
    assert s12.main(["check", str(order), "--run", run_dir(store)]) == 3
    capsys.readouterr()
