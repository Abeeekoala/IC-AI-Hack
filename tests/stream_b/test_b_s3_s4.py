"""S3 ranking and S4 gates."""
import b_fixtures  # adds src to sys.path
from b_fixtures import cfg_fixture, freq_file_fixture, run_dir_fixture

_FIXTURES = (cfg_fixture, freq_file_fixture, run_dir_fixture)   # pytest registers fixtures found as module attributes (names set in b_fixtures)
_ = b_fixtures
import json
import textwrap
from pathlib import Path

import pytest

from b_fakes import FakeGate, FakeScores
from vaxforge import s3_rank, s4_filter
from vaxforge.prediction import artifacts as art
from vaxforge.prediction.blast import BlastRunner, Hsp
from vaxforge.prediction.errors import ConfigError, ToolFailed, ToolUnavailable
from vaxforge.prediction.schema import Protein
from vaxforge.s4_filter import ConservancyGate, HumanHomologyGate, parse_predictor_csv


def load(run_dir, stage):
    return art.read_artifact(run_dir / f"{stage}.json")


# ============================================================================= S3
def test_s3_reports_both_thresholds_keeps_unknown_and_never_states_an_auc(run_dir, cfg):
    s3_rank.run(run_dir, cfg, backend=FakeScores())
    env = load(run_dir, "s3_rank")
    pl = env["payload"]
    assert set(pl["counts"]["threshold_summary"]) == {"0.90", "0.85"}
    assert pl["counts"]["threshold_summary"]["0.85"]["n_pass"] >= pl["counts"]["threshold_summary"]["0.90"]["n_pass"]
    assert pl["counts"]["fold_enrichment"] is None                          # computed only by the validation harness
    assert "auc" not in json.dumps(env).lower().replace("fold_enrichment", "")
    unknown = [r for r in pl["ranking"] if r["evidence"]["localisation"]["label"] == "Unknown"]
    assert unknown and all(r["evidence"]["localisation"]["is_unknown"] and r["evidence"]["localisation"]["prior"] == 0.5 for r in unknown)
    assert [r["rank"] for r in pl["ranking"]] == list(range(1, len(pl["ranking"]) + 1))
    assert all(r["passes_thresholds"].keys() == {"0.90", "0.85"} for r in pl["ranking"])


def test_s3_candidates_carry_sequences_and_respect_threshold_plus_padding(run_dir, cfg):
    s3_rank.run(run_dir, cfg, backend=FakeScores())
    pl = load(run_dir, "s3_rank")["payload"]
    cands = pl["candidates"]
    assert all("sequence" in c and c["selected"] for c in cands)
    above = [r for r in pl["ranking"] if r["protegenicity"] is not None and r["protegenicity"] >= 0.85]
    assert len(cands) == max(len(above), 4)                                  # min_candidates=4 in the test cfg
    assert {c["protein_id"] for c in above} <= {c["protein_id"] for c in cands}
    reasons = {c["selected_reason"] for c in cands}
    assert reasons <= {"protegenicity>=0.85", "rank_padding"}


def test_s3_weights_can_be_re_tuned_by_the_validation_loop(run_dir, cfg):
    s3_rank.run(run_dir, cfg, backend=FakeScores(), force=True)
    order_a = [r["protein_id"] for r in load(run_dir, "s3_rank")["payload"]["ranking"]]
    from vaxforge.prediction.config import deep_merge
    cfg2 = deep_merge(cfg, {"s3": {"weights": {"protegenicity": 0.0, "localisation": 1.0, "virulence": 0.0}}})
    s3_rank.run(run_dir, cfg2, backend=FakeScores(), force=True)
    ranking_b = load(run_dir, "s3_rank")["payload"]["ranking"]
    assert [r["protein_id"] for r in ranking_b] != order_a
    assert ranking_b[0]["evidence"]["localisation"]["prior"] == 1.0         # extracellular / outer-membrane first
    assert ranking_b[0]["protegenicity"] is not None                        # raw evidence still recorded at weight 0


def test_s3_leakage_audit_flags_pretrained_model(run_dir, cfg):
    s3_rank.run(run_dir, cfg, backend=FakeScores(holdout_enforced=False))
    env = load(run_dir, "s3_rank")
    assert env["payload"]["leakage_audit"]["holdout_enforced"] is False
    assert any("LEAKAGE RISK" in w for w in env["warnings"])
    assert env["payload"]["leakage_audit"]["target_taxid"] == 487


def test_s3_rule_based_fallback_is_labelled_and_has_no_protegenicity(run_dir, cfg):
    from vaxforge.prediction.config import deep_merge
    cfg2 = deep_merge(cfg, {"s3": {"backend": "subtractive_filter", "max_candidates": 6}})
    s3_rank.run(run_dir, cfg2)
    env = load(run_dir, "s3_rank")
    pl = env["payload"]
    assert pl["ranker"]["backend"] == "subtractive_filter" and "NOT a protegenicity" in pl["ranker"]["score_semantics"]
    assert all(r["protegenicity"] is None for r in pl["ranking"])
    assert env["tools"][0]["fidelity"] == "fallback" and any("RULE-BASED FALLBACK" in w for w in env["warnings"])
    assert pl["counts"]["threshold_summary"]["0.90"]["available"] is False
    assert len(pl["candidates"]) == 6 and pl["leakage_audit"]["holdout_enforced"] is True


def test_s3_auto_falls_back_when_docker_is_absent(run_dir, cfg, monkeypatch):
    monkeypatch.setattr(s3_rank, "docker_available", lambda *_: False)
    s3_rank.run(run_dir, cfg)                                                # backend auto, Docker 'down'
    env = load(run_dir, "s3_rank")
    assert env["payload"]["ranker"]["backend"] == "subtractive_filter"
    assert any("Vaxign-ML unavailable" in w for w in env["warnings"])


def test_s3_explicit_vaxign_without_docker_is_an_error_not_a_silent_fallback(run_dir, cfg, monkeypatch):
    from vaxforge.prediction.config import deep_merge
    monkeypatch.setattr(s3_rank, "docker_available", lambda *_: False)
    with pytest.raises(ToolUnavailable):
        s3_rank.run(run_dir, deep_merge(cfg, {"s3": {"backend": "vaxign_ml"}}))


def test_s3_artifact_id_is_deterministic_and_chains_to_s2(run_dir, cfg):
    s3_rank.run(run_dir, cfg, backend=FakeScores(), force=True)
    a = load(run_dir, "s3_rank")
    s3_rank.run(run_dir, cfg, backend=FakeScores(), force=True)
    b = load(run_dir, "s3_rank")
    assert a["artifact_id"] == b["artifact_id"] and a["artifact_id"].startswith("sha256:")
    assert a["inputs"][0]["artifact_id"] == "sha256:fake-s2"


def test_s3_reuses_identical_artifact_without_recomputing(run_dir, cfg, monkeypatch):
    from vaxforge.prediction.config import deep_merge
    cfg2 = deep_merge(cfg, {"s3": {"backend": "subtractive_filter"}})
    p1 = s3_rank.run(run_dir, cfg2)
    mtime = p1.stat().st_mtime_ns
    p2 = s3_rank.run(run_dir, cfg2)
    assert p1 == p2 and p2.stat().st_mtime_ns == mtime


@pytest.mark.parametrize("meta,expected", [
    ({"lineage": ["Bacteria", "Pseudomonadota"]}, "gram-"),
    ({"lineage": "cellular organisms; Bacteria; Bacillota"}, "gram+"),
    ({"lineage": [{"scientific_name": "Viruses"}]}, "virus"),
    ({"organism_type": "gram-"}, "gram-"),
])
def test_organism_type_inference(meta, expected):
    assert s3_rank.infer_organism_type("auto", meta) == expected


@pytest.mark.parametrize("meta", [{}, {"lineage": ["Bacteria", "Mycobacteriaceae", "Actinomycetota"]}, {"lineage": ["Archaea"]}])
def test_organism_type_refuses_to_guess(meta):
    with pytest.raises(ConfigError):
        s3_rank.infer_organism_type("auto", meta)


def test_vaxign_result_parser_converts_percentile_and_validates_header():
    scores, raw = s3_rank.parse_vaxign_result("sample\tprediction\tprotegenicity\nA\t1.000000\t93.500000\nB\t0.000000\t12.000000\n")
    assert scores == {"A": 0.935, "B": 0.12} and raw["A"] == 93.5
    with pytest.raises(ToolFailed, match="header"):
        s3_rank.parse_vaxign_result("id\tscore\nA\t0.9\n")
    with pytest.raises(ToolFailed, match="percentile"):
        s3_rank.parse_vaxign_result("sample\tprediction\tprotegenicity\nA\t1\t0.93\n".replace("0.93", "930"))


def _protein(i):
    from b_fakes import synth_seq
    return Protein(f"SYN{i:03d}", synth_seq(i, 80))


def test_vaxign_docker_run_command_cache_and_swallowed_exception(tmp_path, monkeypatch):
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        out = Path(cmd[cmd.index("-o") + 1])
        if kw.get("_fail"):
            pass
        from vaxforge.prediction.tools import CmdResult
        if state["mode"] == "ok":
            (out / "input.result.tsv").write_text("sample\tprediction\tprotegenicity\nSYN001\t1\t95.0\nSYN002\t0\t40.0\n")
            return CmdResult(cmd, 0, "", "")
        return CmdResult(cmd, 0, "(<class 'ValueError'>, ValueError('boom'), <traceback object>)", "")   # exit 0, no file

    state = {"mode": "ok"}
    monkeypatch.setattr(s3_rank, "docker_available", lambda *_: True)
    monkeypatch.setattr(s3_rank, "run_cmd", fake_run)
    monkeypatch.setattr(s3_rank, "_docker_digest", lambda *a: "e4ong1031/vaxign-ml@sha256:abc")
    b = s3_rank.VaxignMLBackend(image="e4ong1031/vaxign-ml:latest", cache_dir=tmp_path / "cache", processes=2)
    res = b.score([_protein(1), _protein(2), _protein(3)], "gram-", tmp_path)
    cmd = calls[0]
    assert cmd[:3] == ["docker", "run", "--rm"] and "e4ong1031/vaxign-ml:latest" in cmd
    assert cmd[cmd.index("python3.6"):cmd.index("python3.6") + 2] == ["python3.6", "VaxignML.py"]
    assert cmd[cmd.index("-t") + 1] == "gram-" and cmd[cmd.index("-p") + 1] == "2"
    assert any(tok.endswith(":/tmp/results") for tok in cmd)                  # PSORTB mount exactly as VaxignML.sh
    assert res.scores["SYN001"] == pytest.approx(0.95) and res.raw["SYN001"] == 95.0
    assert any("no score for 1 proteins" in w for w in res.warnings)          # SYN003 missing -> surfaced
    assert res.leakage["holdout_enforced"] is False and res.tool["version"].endswith("abc")
    hit = b.score([_protein(1), _protein(2), _protein(3)], "gram-", tmp_path)  # second call: cache hit
    assert len(calls) == 1 and hit.tool["cache_hit"] is True
    assert hit.tool["version"].endswith("abc")                                # provenance (image digest) survives a cache hit
    state["mode"] = "swallowed"
    with pytest.raises(ToolFailed, match="no result file"):
        s3_rank.VaxignMLBackend(image="x", cache_dir=tmp_path / "cache2").score([_protein(1)], "gram-", tmp_path)


def test_scores_file_backend_and_declared_holdout(tmp_path, run_dir, cfg):
    f = tmp_path / "lopov_scores.tsv"
    f.write_text("protein_id\tscore\n" + "".join(f"SYN{i:03d}\t{90 - 5 * i}\n" for i in range(1, 13)))
    (tmp_path / "lopov_scores.tsv.meta.json").write_text(json.dumps({"scale": 100, "holdout_taxids": [487], "model": "vaxign-ml LOPOV retrain"}))
    from vaxforge.prediction.config import deep_merge
    s3_rank.run(run_dir, deep_merge(cfg, {"s3": {"backend": "scores_file", "scores_file": str(f)}}), force=True)
    env = load(run_dir, "s3_rank")
    assert env["payload"]["leakage_audit"]["declared_holdout_taxids"] == [487]
    assert env["payload"]["ranking"][0]["protegenicity"] == pytest.approx(0.85)
    (tmp_path / "bad.tsv").write_text("SYN001\t93\n")
    with pytest.raises(Exception, match="outside"):
        s3_rank.ScoresFileBackend(tmp_path / "bad.tsv").score([_protein(1)], "gram-", tmp_path)


# ============================================================================= S4
@pytest.fixture
def s3_done(run_dir, cfg):
    s3_rank.run(run_dir, cfg, backend=FakeScores())
    return run_dir


def all_pass(**over):
    return {g: FakeGate(g, **over.get(g, {})) for g in s4_filter.GATES}


def test_s4_rejections_are_logged_with_evidence_and_recoverable(s3_done, cfg):
    s4_filter.run(s3_done, cfg, gates=all_pass(allergenicity={"reject": {"SYN002"}}, toxicity={"reject": {"SYN002", "SYN003"}},
                                              human_homology={"flag": {"SYN001"}}))
    pl = load(s3_done, "s4_filter")["payload"]
    rej = {r["protein_id"]: r for r in pl["rejected"]}
    assert set(rej) == {"SYN002", "SYN003"} and rej["SYN002"]["rejected_by"] == ["allergenicity", "toxicity"]
    assert rej["SYN002"]["recoverable"] and rej["SYN002"]["gates"]["toxicity"]["reason"]
    assert rej["SYN002"]["sequence"] and rej["SYN002"]["s3"]["rank"]          # full evidence chain kept
    surv = {c["protein_id"]: c for c in pl["candidates"]}
    assert "SYN001" in surv and surv["SYN001"]["flags"] == ["human_homology"]
    assert pl["counts"]["n_input"] == pl["counts"]["n_survivors"] + pl["counts"]["n_rejected"]
    assert [f["gate"] for f in pl["funnel"]] == ["input", *s4_filter.GATES]
    assert pl["funnel"][-1]["remaining_after_gate"] == pl["counts"]["n_survivors"]
    ranks = [c["s3"]["rank"] for c in pl["candidates"]]
    assert ranks == sorted(ranks)                                            # S3 order preserved


def test_s4_rescue_overrides_a_rejection_and_says_so(s3_done, cfg):
    from vaxforge.prediction.config import deep_merge
    cfg2 = deep_merge(cfg, {"s4": {"rescue": ["SYN002"]}})
    s4_filter.run(s3_done, cfg2, gates=all_pass(toxicity={"reject": {"SYN002"}}))
    pl = load(s3_done, "s4_filter")["payload"]
    kept = {c["protein_id"]: c for c in pl["candidates"]}
    assert kept["SYN002"]["rescued_despite"] == ["toxicity"] and pl["counts"]["n_rescued"] == 1 and not pl["rejected"]


def test_s4_unavailable_gate_is_never_a_pass(s3_done, cfg):
    s4_filter.run(s3_done, cfg, gates=all_pass(allergenicity={"unavailable": True}))
    env = load(s3_done, "s4_filter")
    pl = env["payload"]
    assert pl["incomplete_screening"] and pl["unavailable_gates"] == ["allergenicity"]
    assert pl["gates"]["allergenicity"]["status"] == "unavailable"
    assert all("allergenicity" in c["gates_unavailable"] and "allergenicity" not in c["gates"] for c in pl["candidates"])
    assert any("INCOMPLETE SCREENING" in w for w in env["warnings"])


def test_s4_on_unavailable_fail_raises(s3_done, cfg):
    from vaxforge.prediction.config import deep_merge
    with pytest.raises(ToolUnavailable, match="allergenicity"):
        s4_filter.run(s3_done, deep_merge(cfg, {"s4": {"on_unavailable": "fail"}}), gates=all_pass(allergenicity={"unavailable": True}))


def test_s4_with_no_tools_installed_marks_every_enabled_gate_unavailable(s3_done, cfg, monkeypatch):
    monkeypatch.setattr(s4_filter, "resolve_executable", lambda *a, **k: None)
    monkeypatch.setattr(BlastRunner, "available", lambda self: False)
    s4_filter.run(s3_done, cfg)
    pl = load(s3_done, "s4_filter")["payload"]
    assert set(pl["unavailable_gates"]) == set(s4_filter.GATES) and pl["counts"]["n_rejected"] == 0


# ---- individual gates ---------------------------------------------------------
class FakeBlast(BlastRunner):
    def __init__(self, hsps_by_call):
        super().__init__("local")
        self.calls, self._hsps = [], list(hsps_by_call)

    def available(self):
        return True

    def version(self):
        return "fake-blast"

    def makeblastdb(self, fasta, db_prefix):
        return Path(db_prefix)

    def blastp(self, query_fasta, db_prefix, *, evalue=1e-3, max_target_seqs=5, timeout=0):
        self.calls.append(Path(query_fasta).read_text())
        return self._hsps.pop(0)


def _hsp(q, s, ident, qs, qe, qlen, qseq, sseq, bits=100.0):
    return Hsp(q, s, ident, qe - qs + 1, qlen, 500, qs, qe, 1, qe - qs + 1, 1e-40, bits, qseq, sseq)


CANDS = [{"protein_id": "A", "sequence": "M" * 100}, {"protein_id": "B", "sequence": "K" * 100}, {"protein_id": "C", "sequence": "G" * 100}]
HH_CFG = {"evalue": 1e-3, "min_identity": 0.35, "min_coverage": 0.5, "shared_peptide_length": 9, "ligand_recheck": True}


def test_human_gate_rejects_on_known_self_ligand_overlap(tmp_path):
    shared = "SIINFEKLQ"
    hsps = [_hsp("A", "H1", 80.0, 1, 60, 100, "X" * 5 + shared + "Y" * 46, "Z" * 5 + shared + "W" * 46),   # shares the 9-mer
            _hsp("B", "H2", 45.0, 1, 70, 100, "AAAAAAAAAA" + "C" * 60, "DDDDDDDDDD" + "E" * 60)]           # homologous, no shared 9-mer
    (tmp_path / "h.fa").write_text(">h\nA\n")
    gate = HumanHomologyGate(FakeBlast([hsps]), tmp_path / "h.fa", tmp_path, HH_CFG, ligands={"SIINFEKLQ", "OTHERPEPT"})
    res = gate.evaluate(CANDS)
    assert res["A"].status == "reject" and res["A"].metrics["known_human_mhc_ligand_overlap"] == [shared]
    assert res["B"].status == "flag" and res["B"].metrics["homologous"]            # homology alone is not enough once the re-check ran
    assert res["C"].status == "pass"
    assert gate.thresholds()["ligand_recheck"] is True


def test_human_gate_without_ligand_set_rejects_homologs_but_says_it_may_be_over_aggressive(tmp_path):
    hsps = [_hsp("B", "H2", 45.0, 1, 70, 100, "A" * 70, "C" * 70)]
    (tmp_path / "h.fa").write_text(">h\nA\n")
    gate = HumanHomologyGate(FakeBlast([hsps]), tmp_path / "h.fa", tmp_path, HH_CFG, ligands=None)
    r = gate.evaluate(CANDS)["B"]
    assert r.status == "reject" and "may be over-aggressive" in r.reason and r.metrics["ligand_recheck"] == "unavailable"


def test_conservancy_gate_fraction_of_strains(tmp_path):
    cfg = {"evalue": 1e-5, "min_coverage": 0.8, "min_fraction_strains": 1.0}
    s1 = [_hsp("A", "x", 95.0, 1, 100, 100, "", ""), _hsp("B", "x", 60.0, 1, 100, 100, "", "")]     # B diverged; C absent
    s2 = [_hsp("A", "y", 90.0, 1, 100, 100, "", ""), _hsp("B", "y", 99.0, 1, 100, 100, "", "")]
    for n in ("s1.fa", "s2.fa"):
        (tmp_path / n).write_text(">s\nA\n")
    gate = ConservancyGate(FakeBlast([s1, s2]), [("strain1", tmp_path / "s1.fa"), ("strain2", tmp_path / "s2.fa")], tmp_path,
                           identity=0.80, cfg=cfg)
    r = gate.evaluate(CANDS)
    assert r["A"].status == "pass" and r["B"].status == "reject" and r["C"].status == "reject"
    assert r["B"].metrics["fraction_strains_conserved"] == 0.5
    with pytest.raises(ToolUnavailable):
        ConservancyGate(FakeBlast([]), [], tmp_path, identity=0.8, cfg=cfg).evaluate(CANDS)


def test_conservancy_on_fail_flag_keeps_strain_variable_antigens(tmp_path):
    # real data (2026-11-14): fHbp is ~71-74% identical in other strains and NadA is absent -> 'reject' removes Bexsero antigens
    cfg = {"evalue": 1e-5, "min_coverage": 0.8, "min_fraction_strains": 1.0, "on_fail": "flag"}
    (tmp_path / "s1.fa").write_text(">s\nA\n")
    s1 = [_hsp("A", "x", 72.0, 1, 100, 100, "", "")]                                       # variant group, 72% identity
    gate = ConservancyGate(FakeBlast([s1]), [("strain1", tmp_path / "s1.fa")], tmp_path, identity=0.80, cfg=cfg)
    r = gate.evaluate(CANDS)
    assert r["A"].status == "flag" and r["C"].status == "flag"                             # variant / absent: flagged, not dropped
    assert gate.thresholds()["on_fail"] == "flag"


def test_predictor_csv_parsing_picks_the_right_score_column():
    assert parse_predictor_csv("# ID,Sequence,Prediction,Score\nA,MKV,Allergen,0.61\nB,GGG,Non-Allergen,0.12\n") == {"A": 0.61, "B": 0.12}
    hybrid = "# ID,Sequence,ML Score,BLAST Score,MERCI Score,Hybrid Score,Prediction\nA,MKV,0.5,0.0,0.0,0.77,Toxin\n"
    assert parse_predictor_csv(hybrid) == {"A": 0.77}
    with pytest.raises(ToolFailed):
        parse_predictor_csv("id,sequence\nA,MKV\n")


def test_predictor_csv_parsing_matches_the_real_headers_seen_on_2026_11_14():
    toxin = "ID,Sequence,ML_Score,Prediction\nQ7DD37,MFKRSV,0.159,Non-Toxin\nQ9JXV4,MNRTAF,0.357,Non-Toxin\n"
    assert parse_predictor_csv(toxin) == {"Q7DD37": 0.159, "Q9JXV4": 0.357}
    hybrid = ("\n\nPlease cite: AlgPred2.0\n\nSubject,ML Score,MERCI Score,BLAST Score,Hybrid Score,Prediction\n"
              "Q9JXK7,0.596,0.5,0,1.096,Allergen\n")
    assert parse_predictor_csv(hybrid) == {"Q9JXK7": 1.096}                      # hybrid score, not the ML score; banner skipped


def test_cli_score_gate_end_to_end_with_fake_tool(tmp_path):
    script = tmp_path / "fake_algpred.py"
    script.write_text(textwrap.dedent('''
        import argparse
        ap = argparse.ArgumentParser(); [ap.add_argument(f) for f in ("-i", "-o", "-m", "-t", "-d")]; a = ap.parse_args()
        ids = [l[1:].strip() for l in open(a.i) if l.startswith(">")]
        open(a.o, "w").write("# ID,Sequence,Prediction,Score\\n" + "\\n".join(f"{i},X,P,{0.9 if i == 'B' else 0.1}" for i in ids if i != "C"))
    '''))
    gate = s4_filter.CliScoreGate("allergenicity", "AlgPred 2.0", ["python3", str(script)], 0.45, 1)
    r = gate.evaluate(CANDS)
    assert r["A"].status == "pass" and r["B"].status == "reject" and r["B"].metrics["score"] == 0.9
    assert r["C"].status == "unavailable"                                    # tool skipped it -> not a silent pass
