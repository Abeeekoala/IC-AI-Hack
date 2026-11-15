"""S5 epitopes, S6 coverage, S7 structure, and the whole S3->S7 chain on synthetic inputs."""
import b_fixtures  # adds src to sys.path
from b_fixtures import cfg_fixture, freq_file_fixture, run_dir_fixture

_FIXTURES = (cfg_fixture, freq_file_fixture, run_dir_fixture)   # pytest registers fixtures found as module attributes (names set in b_fixtures)
_ = b_fixtures
import json

import pytest

from b_fakes import (FakeBepi, FakeBinder, FakeDisco, FakeFolder, FakeGate, FakeRefolder, FakeScores, helix_coords,
                   write_pdb_ca)
from vaxforge import s3_rank, s4_filter, s5_epitopes, s6_coverage, s7_structure
from vaxforge.prediction import artifacts as art
from vaxforge.prediction.config import deep_merge
from vaxforge.prediction.netmhc import Binding, MHCflurryRunner
from vaxforge.prediction.popcov import FrequencyTable
from vaxforge.prediction.discotope import DiscoTopeResidue
from vaxforge.prediction.structure_utils import parse_structure_ca, plddt_scale_factor, plddt_summary


def load(run_dir, stage):
    return art.read_artifact(run_dir / f"{stage}.json")


def upto_s4(run_dir, cfg):
    s3_rank.run(run_dir, cfg, backend=FakeScores())
    s4_filter.run(run_dir, cfg, gates={g: FakeGate(g) for g in s4_filter.GATES})


def pdb_lookup_factory(tmp_path):
    def lookup(pid):
        seq = [c for c in load_cache["cands"] if c["protein_id"] == pid][0]["sequence"]
        return write_pdb_ca(tmp_path / f"{pid}.pdb", seq, helix_coords(len(seq)), 90.0)
    return lookup


load_cache: dict = {}


def run_s5(run_dir, cfg, tmp_path, **over):
    load_cache["cands"] = load(run_dir, "s4_filter")["payload"]["candidates"]
    kw = dict(class_i=FakeBinder("I"), class_ii=FakeBinder("II"), bcell=FakeBepi(), discotope=FakeDisco(),
              structure_lookup=pdb_lookup_factory(tmp_path))
    kw.update(over)
    return s5_epitopes.run(run_dir, cfg, **kw)


# ============================================================================= S5 units
def _b(pid, start, pep, allele, core, rank):
    return Binding(pid, start, pep, allele, core, rank, 0.5, None)


def test_collapse_groups_by_core_so_overlapping_lengths_are_one_epitope():
    bs = [_b("P", 5, "ACDEFGHIK", "HLA-A*02:01", "ACDEFGHIK", 0.3), _b("P", 5, "ACDEFGHIKL", "HLA-A*02:01", "ACDEFGHIK", 0.9),
          _b("P", 5, "ACDEFGHIK", "HLA-B*07:02", "ACDEFGHIK", 1.5), _b("P", 20, "KLMNPQRST", "HLA-A*02:01", "KLMNPQRST", 3.0)]
    ep = s5_epitopes.collapse_bindings(bs, strong=0.5, weak=2.0, mhc_class="I")
    assert len(ep) == 1 and ep[0]["core"] == "ACDEFGHIK"                      # rank 3.0 > weak is not a binder
    e = ep[0]
    assert e["n_alleles"] == 2 and e["n_strong_alleles"] == 1 and e["best_rank"] == 0.3
    assert e["allele_ranks"]["HLA-A*02:01"] == 0.3                            # best rank per allele across lengths


def test_select_epitopes_skips_near_duplicates_and_needs_min_alleles():
    def ep(core_start, n, rank):
        return {"core_start": core_start, "core_end": core_start + 8, "n_alleles": n, "best_rank": rank, "start": core_start}
    pool = [ep(10, 4, 0.1), ep(12, 3, 0.2), ep(40, 2, 0.1), ep(70, 1, 0.01)]
    sel = s5_epitopes.select_epitopes(pool, max_n=5, min_alleles=2)
    assert [e["core_start"] for e in sel] == [10, 40]                         # 12 overlaps 10 by 7; 70 has 1 allele


def test_tcell_score_is_monotone_and_never_saturates():
    # real MHCflurry data gave every candidate 1.0 under a capped score; this one must keep candidates ordered
    assert s5_epitopes.tcell_score(0, 300, 5.0) == 0.0
    assert s5_epitopes.tcell_score(15, 300, 5.0) == pytest.approx(0.5)             # d = 5 per 100 aa == half_density
    vals = [s5_epitopes.tcell_score(n, 300, 5.0) for n in (1, 5, 15, 60, 600)]
    assert vals == sorted(vals) and len(set(vals)) == 5 and vals[-1] < 1.0


def test_random_expectation_matches_binomial_closed_form():
    # k=0 -> always satisfied; 1 allele, 1 length, k=1 -> exactly the per-window probability
    assert s5_epitopes.expected_random_per_100aa(27, 0.005, 4, 0) == pytest.approx(100.0)
    assert s5_epitopes.expected_random_per_100aa(1, 0.005, 1, 1) == pytest.approx(0.5)
    e3 = s5_epitopes.expected_random_per_100aa(27, 0.005, 4, 3)
    assert 1.0 < e3 < 3.0 and s5_epitopes.expected_random_per_100aa(27, 0.005, 4, 4) < e3


def test_backends_without_a_core_group_lengths_by_start_instead_of_inflating_epitopes():
    # MHCflurry reports no core: 8-,9-,10-,11-mers from the same start must be ONE epitope (was 4x inflated on real data)
    bs = [Binding("P", 5, "A" * L, "HLA-A*02:01", "A" * L, 0.2 + 0.1 * L / 10, 0.5, None, group="5") for L in (8, 9, 10, 11)]
    bs += [Binding("P", 5, "A" * 9, "HLA-B*07:02", "A" * 9, 0.3, 0.5, None, group="5"), Binding("P", 6, "C" * 9, "HLA-A*02:01", "C" * 9, 0.4, 0.5, None, group="6")]
    ep = s5_epitopes.collapse_bindings(bs, strong=0.5, weak=2.0, mhc_class="I")
    assert len(ep) == 2
    first = [e for e in ep if e["start"] == 5][0]
    assert first["n_alleles"] == 2 and first["epitope_id"].startswith("P|I|5:") and first["best_rank"] == pytest.approx(0.28)


# ============================================================================= S5 stage
def test_s5_class_ii_is_down_weighted_in_the_combined_score(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)
    run_s5(run_dir, cfg, tmp_path)
    pl = load(run_dir, "s5_epitopes")["payload"]
    c = pl["candidates"][0]
    s1, s2 = c["tcell"]["class_i"]["score"], c["tcell"]["class_ii"]["score"]
    w = cfg["epitopes"]["mhc_class_ii_weight"]
    assert w == 0.3 and c["tcell"]["score"] == pytest.approx((s1 + w * s2) / (1 + w))
    assert c["tcell"]["score_basis"] == "class_i + class_ii(weighted)"
    assert pl["analysis_status"]["class_ii"]["weight"] == 0.3


def test_s5_epitope_set_and_sidecars(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)
    run_s5(run_dir, cfg, tmp_path)
    env = load(run_dir, "s5_epitopes")
    pl = env["payload"]
    ts = pl["epitope_set"]["t_cell"]
    assert ts and all({"epitope_id", "protein_id", "class", "peptide", "alleles", "n_alleles"} <= set(e) for e in ts)
    assert {e["class"] for e in ts} == {"I", "II"}
    per_cand_limit = cfg["s5"]["select"]["max_class_i"] + cfg["s5"]["select"]["max_class_ii"]
    for c in pl["candidates"]:
        assert len(c["tcell"]["class_i"]["selected"]) <= cfg["s5"]["select"]["max_class_i"]
        assert sum(1 for e in ts if e["protein_id"] == c["protein_id"]) <= per_cand_limit
        ref = c["tcell"]["all_epitopes_file"]                                    # Stream A file reference object
        assert ref["root"] == "run" and ref["path"].startswith("data/s5/") and len(ref["sha256"]) == 64 and ref["bytes"] > 0
        import hashlib
        side = run_dir / ref["path"]
        assert side.exists() and hashlib.sha256(side.read_bytes()).hexdigest() == ref["sha256"]
        assert (run_dir / c["bcell"]["linear"]["per_residue_file"]["path"]).exists()
    assert pl["upstream"]["s3_leakage_audit"] is not None and "limits" in pl


def test_s5_passes_the_rank_cutoff_and_panel_to_the_binder(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)
    bi, b2 = FakeBinder("I"), FakeBinder("II")
    run_s5(run_dir, cfg, tmp_path, class_i=bi, class_ii=b2)
    assert bi.calls[0]["max_rank"] == 2.0 and bi.calls[0]["lengths"] == [8, 9, 10, 11] and len(bi.calls[0]["alleles"]) == 6
    assert b2.calls[0]["max_rank"] == 5.0 and b2.calls[0]["lengths"] == [15]


def test_s5_without_any_predictor_marks_everything_unavailable_and_invents_nothing(run_dir, cfg, tmp_path, monkeypatch):
    upto_s4(run_dir, cfg)
    monkeypatch.setattr(s5_epitopes, "resolve_executable", lambda *a, **k: None)
    monkeypatch.setattr(MHCflurryRunner, "installed", staticmethod(lambda: False))
    s5_epitopes.run(run_dir, cfg)
    env = load(run_dir, "s5_epitopes")
    st = env["payload"]["analysis_status"]
    assert {k: v["status"] for k, v in st.items()} == {"class_i": "unavailable", "class_ii": "unavailable",
                                                     "bcell_linear": "unavailable", "conformational": "unavailable"}
    assert any("BepiPred-2.0 is never used" in w for w in env["warnings"])
    c = env["payload"]["candidates"][0]
    assert c["tcell"]["score"] is None and c["bcell"]["linear_score"] is None and env["payload"]["epitope_set"]["t_cell"] == []


def test_s5_class_ii_missing_falls_back_to_class_i_only_with_a_warning(run_dir, cfg, tmp_path, monkeypatch):
    upto_s4(run_dir, cfg)
    monkeypatch.setattr(s5_epitopes, "resolve_class_ii", lambda c: (None, "netMHCIIpan not found"))
    run_s5(run_dir, cfg, tmp_path, class_ii=None)
    env = load(run_dir, "s5_epitopes")
    c = env["payload"]["candidates"][0]
    assert c["tcell"]["score_basis"] == "class_i_only" and c["tcell"]["score"] == c["tcell"]["class_i"]["score"]
    assert any("class II" in w for w in env["warnings"])


def test_s5_alternative_class_i_backend_is_labelled_not_passed_off_as_netmhcpan(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)
    alt = FakeBinder("I")
    alt.fidelity, alt.name = "alternative", "MHCflurry"
    run_s5(run_dir, cfg, tmp_path, class_i=alt)
    env = load(run_dir, "s5_epitopes")
    assert env["payload"]["analysis_status"]["class_i"]["fidelity"] == "alternative"
    assert any("ALTERNATIVE" in w and "not NetMHCpan" in w for w in env["warnings"])


def test_s5_conformational_requires_identical_sequence(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)
    cands = load(run_dir, "s4_filter")["payload"]["candidates"]
    wrong = cands[1]["protein_id"]
    good_lookup = pdb_lookup_factory(tmp_path)
    load_cache["cands"] = cands

    def lookup(pid):
        if pid == wrong:
            seq = "M" * len(cands[1]["sequence"])                                # AFDB model of a different sequence version
            return write_pdb_ca(tmp_path / "wrong.pdb", seq, helix_coords(len(seq)), 90.0)
        if pid == cands[2]["protein_id"]:
            return None                                                         # no AFDB model
        return good_lookup(pid)

    s5_epitopes.run(run_dir, cfg, class_i=FakeBinder("I"), class_ii=FakeBinder("II"), bcell=FakeBepi(),
                    discotope=FakeDisco(), structure_lookup=lookup)
    conf = {c["protein_id"]: c["bcell"]["conformational"] for c in load(run_dir, "s5_epitopes")["payload"]["candidates"]}
    assert conf[wrong]["status"] == "skipped" and "differs" in conf[wrong]["reason"]
    assert conf[cands[2]["protein_id"]]["reason"].startswith("no AlphaFold DB model")
    ok = conf[cands[0]["protein_id"]]
    assert ok["status"] == "ran" and ok["n_epitope_residues"] == 10 and "epitope_residues" not in ok


def test_s5_non_uniprot_ids_are_not_sent_to_alphafold_db(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)

    class Boom:
        def afdb_structure(self, acc):
            raise AssertionError("must not be called for SYN### ids")
    s5_epitopes.run(run_dir, cfg, class_i=FakeBinder("I"), class_ii=FakeBinder("II"), bcell=FakeBepi(), discotope=FakeDisco(),
                    refdata=Boom())
    conf = load(run_dir, "s5_epitopes")["payload"]["candidates"][0]["bcell"]["conformational"]
    assert conf["status"] == "skipped" and "no UniProt accession" in conf["reason"]


def test_s5_long_candidate_is_chunked_by_the_real_runner_contract(run_dir, cfg, tmp_path):
    # FakeBepi reports n_chunks from chunk_size/overlap; the stage must pass the configured windowing through
    upto_s4(run_dir, cfg)
    seen = {}

    class Spy(FakeBepi):
        def predict(self, seqs, *, chunk_size, chunk_overlap, **kw):
            seen.update(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
            return super().predict(seqs, chunk_size=chunk_size, chunk_overlap=chunk_overlap)
    run_s5(run_dir, cfg, tmp_path, bcell=Spy())
    assert seen == {"chunk_size": 1000, "chunk_overlap": 100}


# ============================================================================= S6
def s6_inputs(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)
    run_s5(run_dir, cfg, tmp_path)


def test_s6_reports_coverage_hits_pc90_fairness_and_limits(run_dir, cfg, tmp_path, freq_file):
    s6_inputs(run_dir, cfg, tmp_path)
    cfg6 = deep_merge(cfg, {"refdata": {"afnd_frequencies": str(freq_file)}})
    s6_coverage.run(run_dir, cfg6)
    env = load(run_dir, "s6_coverage")
    pl = env["payload"]
    assert pl["analysis"]["status"] == "ran" and pl["analysis"]["populations"] == ["World", "Europe", "East Asia", "West Africa"]
    w = pl["set_level"]["by_population"]["World"]["combined"]
    assert {"coverage", "average_hits", "pc90", "sample_size"} <= set(w) and 0 <= w["coverage"] <= 1
    assert pl["set_level"]["by_population"]["Europe"]["combined"]["sample_size"] is None          # not invented
    fr = pl["set_level"]["fairness"]
    assert fr["available"] and fr["reference_population"] == "Europe" and {r["population"] for r in fr["rows"]} >= {"East Asia", "West Africa"}
    assert (run_dir / pl["set_level"]["iedb_crosscheck_file"]["file"]["path"]).exists()
    assert any("linkage disequilibrium" in l for l in pl["limits"]) and any("presentable" in l for l in pl["limits"])
    c = pl["candidates"][0]["coverage"]
    assert 0 <= c["score"] <= 1 and c["score_population"] == "World" and set(c["by_population"]) == set(pl["analysis"]["populations"])
    assert "top" not in pl["candidates"][0]["tcell"]["class_i"]                                   # bulky lists stay in sidecars


def test_s6_fairness_is_reported_for_several_budgets_and_flags_sparse_data(run_dir, cfg, tmp_path, freq_file):
    s6_inputs(run_dir, cfg, tmp_path)
    s6_coverage.run(run_dir, deep_merge(cfg, {"refdata": {"afnd_frequencies": str(freq_file)}, "s6": {"fairness": {"budgets": [1, 2, 3]}}}))
    env = load(run_dir, "s6_coverage")
    by_b = env["payload"]["set_level"]["fairness_by_budget"]
    assert set(by_b) == {"1", "2", "3"} and all(r["coverage_lost"] >= 0 for b in by_b.values() for r in b["rows"])
    assert env["payload"]["set_level"]["by_population"]["World"]["combined"]["min_locus_frequency_mass"] is not None
    assert any("sparse allele-frequency data" in w for w in env["warnings"])          # the toy table sums to ~0.3 per locus


def test_s6_candidate_coverage_is_consistent_with_direct_computation(run_dir, cfg, tmp_path, freq_file):
    from vaxforge.prediction.popcov import coverage_stats
    s6_inputs(run_dir, cfg, tmp_path)
    s6_coverage.run(run_dir, deep_merge(cfg, {"refdata": {"afnd_frequencies": str(freq_file)}}))
    pl = load(run_dir, "s6_coverage")["payload"]
    by_id = {e["epitope_id"]: e for e in pl["epitope_set"]["t_cell"]}
    c = pl["candidates"][0]
    sel = c["tcell"]["class_i"]["selected"] + c["tcell"]["class_ii"]["selected"]
    direct = coverage_stats({i: by_id[i]["alleles"] for i in sel}, FrequencyTable.from_tsv(freq_file), "World", "combined")
    assert c["coverage"]["score"] == pytest.approx(direct.coverage)


def test_s6_without_a_frequency_table_is_unavailable_not_zero(run_dir, cfg, tmp_path):
    s6_inputs(run_dir, cfg, tmp_path)
    s6_coverage.run(run_dir, cfg)
    env = load(run_dir, "s6_coverage")
    assert env["payload"]["analysis"]["status"] == "unavailable"
    assert all(c["coverage"]["score"] is None for c in env["payload"]["candidates"])
    assert any("population coverage did not run" in w for w in env["warnings"])


def test_s6_flags_alleles_without_frequency_data(run_dir, cfg, tmp_path, freq_file):
    s6_inputs(run_dir, cfg, tmp_path)
    s6_coverage.run(run_dir, deep_merge(cfg, {"refdata": {"afnd_frequencies": str(freq_file)}}))
    env = load(run_dir, "s6_coverage")
    # DQA1*03:01/DQB1*03:02 maps through its beta chain; every panel allele is in the toy table, so nothing is unmapped
    assert not any("no frequency data" in w for w in env["warnings"])
    trimmed = freq_file.read_text().replace("I\tHLA-A\tHLA-A*02:01\t0.15\t0.25\t0.10\t0.10\n", "")
    f2 = freq_file.with_name("freq2.tsv")
    f2.write_text(trimmed)
    s6_coverage.run(run_dir, deep_merge(cfg, {"refdata": {"afnd_frequencies": str(f2)}}), force=True)
    assert any("no frequency data" in w and "HLA-A*02:01" in w for w in load(run_dir, "s6_coverage")["warnings"])


# ============================================================================= S7
def s7_inputs(run_dir, cfg, tmp_path, freq_file):
    s6_inputs(run_dir, cfg, tmp_path)
    s6_coverage.run(run_dir, deep_merge(cfg, {"refdata": {"afnd_frequencies": str(freq_file)}}))


REFOLDERS = lambda: {"esmfold": FakeRefolder("esmfold", 0.4), "af2-no-msa": FakeRefolder("af2_no_msa", 1.2)}


def test_s7_top_k_structure_dual_validator_and_final_ranking(run_dir, cfg, tmp_path, freq_file):
    s7_inputs(run_dir, cfg, tmp_path, freq_file)
    s7_structure.run(run_dir, cfg, folder=FakeFolder(), refolders=REFOLDERS(), discotope=FakeDisco())
    env = load(run_dir, "s7_structure")
    pl = env["payload"]
    cands = pl["candidates"]
    assert pl["top_k"] == 4 and len(pl["final_candidates"]) == 4
    assert [c["rank"] for c in cands] == list(range(1, len(cands) + 1))
    top = [c for c in cands if c["in_top_k"]]
    assert [c["protein_id"] for c in top] == pl["final_candidates"] and all(c["rank_basis"] == "structure_informed" for c in top)
    assert all(c["structure"]["status"] == "not_selected" for c in cands if not c["in_top_k"])
    s = top[0]["structure"]
    assert s["status"] == "ran" and s["mean_plddt"] == pytest.approx(85.0) and s["sequence_matches_input"]
    v = s["validators"]
    assert v["esmfold"]["lddt_ca_vs_boltz"] > v["af2-no-msa"]["lddt_ca_vs_boltz"]           # less noise -> closer to Boltz-2
    assert s["validator_agreement"]["n_validators"] == 2 and s["validator_agreement"]["spread"] > 0
    assert s["validator_agreement"]["value"] == pytest.approx((v["esmfold"]["lddt_ca_vs_boltz"] + v["af2-no-msa"]["lddt_ca_vs_boltz"]) / 2, abs=1e-3)
    assert 0 < s["structure_term"] <= 1 and "structure" in top[0]["scores"]["terms_used"]
    assert s["conformational_epitopes"]["status"] == "ran" and "fraction_epitope_residues_below_floor" in s["conformational_epitopes"]
    assert (run_dir / s["model_file"]["path"]).exists() and parse_structure_ca(run_dir / s["model_file"]["path"]).sequence == top[0]["sequence"]
    assert s["model_file"]["root"] == "run" and len(s["model_file"]["sha256"]) == 64 and v["esmfold"]["model_file"]["path"].endswith("esmfold.pdb")
    finals = [c["scores"]["final"] for c in top]
    assert finals == sorted(finals, reverse=True) and all(c["scores"]["final"] is None for c in cands if not c["in_top_k"])
    assert pl["analysis"]["affinity"]["status"] == "not_implemented" and pl["analysis"]["docking"]["status"] == "not_implemented"
    assert pl["analysis"]["folding"]["seed_controlled"] is False and pl["epitope_set"]["t_cell"]


def test_s7_degrades_to_sequence_only_when_boltz_is_missing(run_dir, cfg, tmp_path, freq_file, monkeypatch):
    s7_inputs(run_dir, cfg, tmp_path, freq_file)
    monkeypatch.setattr(s7_structure, "resolve_boltz", lambda c: (None, "boltz not found"))
    s7_structure.run(run_dir, cfg, refolders=REFOLDERS())
    env = load(run_dir, "s7_structure")
    pl = env["payload"]
    assert pl["analysis"]["degraded_to_sequence_only"] and any("DEGRADED" in w for w in env["warnings"])
    assert all(c["structure"]["status"] in ("unavailable", "not_selected") for c in pl["candidates"])
    assert all(c["rank_basis"] == "pre_structure" for c in pl["candidates"])
    assert len(pl["final_candidates"]) == 4 and all("structure" not in c["scores"]["terms_used"] for c in pl["candidates"])


def test_s7_a_failed_fold_does_not_lose_the_candidate(run_dir, cfg, tmp_path, freq_file):
    s7_inputs(run_dir, cfg, tmp_path, freq_file)
    s7_structure.run(run_dir, cfg, folder=FakeFolder(), refolders=REFOLDERS(), discotope=FakeDisco())
    victim = load(run_dir, "s7_structure")["payload"]["final_candidates"][0]
    s7_structure.run(run_dir, cfg, folder=FakeFolder(fail={victim}), refolders=REFOLDERS(), discotope=FakeDisco())
    pl = load(run_dir, "s7_structure")["payload"]
    rec = {c["protein_id"]: c for c in pl["candidates"]}[victim]
    assert rec["structure"]["status"] == "failed" and "fake boltz failure" in rec["structure"]["reason"]
    assert "structure" in rec["scores"]["terms_missing"] and victim in pl["final_candidates"]


def test_s7_single_validator_is_called_out(run_dir, cfg, tmp_path, freq_file):
    s7_inputs(run_dir, cfg, tmp_path, freq_file)
    s7_structure.run(run_dir, cfg, folder=FakeFolder(), refolders={"esmfold": FakeRefolder("esmfold", 0.4)}, discotope=FakeDisco())
    env = load(run_dir, "s7_structure")
    st = env["payload"]["candidates"][0]["structure"]
    assert st["validator_agreement"]["note"] == "single validator only" and st["validator_agreement"]["spread"] is None


def test_s7_flags_off_machine_servers(run_dir, cfg, tmp_path, freq_file):
    s7_inputs(run_dir, cfg, tmp_path, freq_file)
    f = FakeFolder()
    f.sends_sequence_off_machine = True
    s7_structure.run(run_dir, cfg, folder=f, refolders=REFOLDERS(), discotope=FakeDisco())
    env = load(run_dir, "s7_structure")
    assert any("sent to third-party servers" in w and "ColabFold" in w for w in env["warnings"])
    assert any(t.get("sends_sequence_off_machine") for t in env["tools"])


class RecordingDisco:
    """DiscoTope stand-in that behaves like the real tool: its ``pLDDTs`` column is the B-factor column of the PDB it is
    given (measured on a real Boltz-2 model: ratio 1.0), and it records what it received. ``FakeDisco`` ignores its input,
    which is why a x100 scaling bug in S7 went unnoticed."""
    name = "DiscoTope-3.0"

    def __init__(self):
        self.received_bfactors = []

    def predict(self, structure, *, struc_type="alphafold"):
        from pathlib import Path
        bf = [float(ln[60:66]) for ln in Path(structure).read_text().splitlines() if ln.startswith("ATOM")]
        self.received_bfactors.append(bf)
        return [DiscoTopeResidue(i + 1, "A", 0.95 if 5 <= i < 15 else 0.2, 0.5, b, 5 <= i < 15) for i, b in enumerate(bf)]


def test_plddt_scale_factor_is_exact_not_a_prefix_match_on_the_label():
    import numpy as np
    assert plddt_scale_factor(np.array([22.6, 36.3, 59.9])) == 1.0                  # 0-100 file: untouched
    assert plddt_scale_factor(np.array([0.2, 0.5, 0.9])) == 100.0                  # 0-1 file: rescaled
    assert plddt_summary(np.array([22.6, 59.9]))["scale_detected"] == "0-100"
    assert plddt_summary(np.array([0.2, 0.9]))["scale_detected"].startswith("0-1 (rescaled")
    assert "0-100".startswith("0-1")                                                 # the trap: the two labels share a prefix


@pytest.mark.parametrize("stored, handed_to_disco, below_floor_fraction", [
    (30.0, 30.0, 1.0),      # low-confidence 0-100 model: DiscoTope must see 30, and every epitope residue is below the floor of 70
    (85.0, 85.0, 0.0),      # confident 0-100 model: DiscoTope must see 85 (not 8500), and none is below the floor
    (0.30, 30.0, 1.0),      # model that stores 0-1: rewritten x100 for DiscoTope, floor compared on the 0-100 scale
])
def test_s7_gives_discotope_unscaled_plddt_and_the_floor_check_sees_low_confidence(run_dir, cfg, tmp_path, freq_file,
                                                                                   stored, handed_to_disco, below_floor_fraction):
    s7_inputs(run_dir, cfg, tmp_path, freq_file)
    dt = RecordingDisco()
    s7_structure.run(run_dir, cfg, folder=FakeFolder(plddt=stored), refolders=REFOLDERS(), discotope=dt)
    assert dt.received_bfactors and all(b == pytest.approx(handed_to_disco, abs=0.01) for bf in dt.received_bfactors for b in bf)
    top = [c for c in load(run_dir, "s7_structure")["payload"]["candidates"] if c["in_top_k"]]
    for c in top:
        ce = c["structure"]["conformational_epitopes"]
        assert ce["status"] == "ran" and ce["n_epitope_residues"] > 0
        assert ce["mean_plddt_input"] == pytest.approx(handed_to_disco, abs=0.01)         # was exactly 100x mean_plddt
        assert ce["mean_plddt_input"] == pytest.approx(c["structure"]["mean_plddt"], abs=0.01)
        assert ce["fraction_epitope_residues_below_floor"] == pytest.approx(below_floor_fraction)
        assert ce["n_epitope_residues_below_floor"] == round(below_floor_fraction * ce["n_epitope_residues"])


def test_s7_scoring_uses_percentiles_so_scales_do_not_dominate():
    g = {"a": {"s3": 0.99, "bcell": 0.11}, "b": {"s3": 0.98, "bcell": 0.35}, "c": {"s3": 0.10, "bcell": 0.12}}
    out = s7_structure.score_group(g, {"s3": 1.0, "bcell": 1.0})
    assert out["a"]["terms_percentile"]["s3"] == 1.0 and out["b"]["terms_percentile"]["bcell"] == 1.0
    assert out["b"]["score"] > out["a"]["score"] > out["c"]["score"]
    assert out["a"]["terms_raw"]["bcell"] == 0.11                                               # raw values kept


# ============================================================================= whole chain
def test_full_chain_provenance_is_content_addressed_and_deterministic(run_dir, cfg, tmp_path, freq_file):
    s7_inputs(run_dir, cfg, tmp_path, freq_file)
    s7_structure.run(run_dir, cfg, folder=FakeFolder(), refolders=REFOLDERS(), discotope=FakeDisco())
    stages = ["s3_rank", "s4_filter", "s5_epitopes", "s6_coverage", "s7_structure"]
    envs = [load(run_dir, s) for s in stages]
    for prev, nxt in zip(envs, envs[1:]):
        assert nxt["inputs"][0]["stage"] == prev["stage"] and nxt["inputs"][0]["artifact_id"] == prev["artifact_id"]
    assert envs[0]["inputs"][0]["stage"] == "s2_annotate"
    for e in envs:
        assert e["schema"] == f"vaxforge.{e['stage']}/1" and e["artifact_id"].startswith("sha256:") and e["run_id"] == "synthetic"
        json.dumps(e, allow_nan=False)                                                        # no NaN/Inf leaks
    first = envs[-1]["artifact_id"]
    s7_structure.run(run_dir, cfg, folder=FakeFolder(), refolders=REFOLDERS(), discotope=FakeDisco())
    assert load(run_dir, "s7_structure")["artifact_id"] == first                              # same inputs -> same id
    assert all(isinstance(e["payload"]["context"], dict) for e in envs)


def test_cli_entrypoints_run_with_overrides(run_dir, tmp_path, freq_file, capsys):
    s = s3_rank.main(["--run-dir", str(run_dir), "--set", 's3.backend="subtractive_filter"',
                      "--set", 's3.max_candidates=5', "--set", f'refdata.cache_dir="{tmp_path / "c"}"'])
    assert s == 0 and (run_dir / "s3_rank.json").exists()
    assert len(load(run_dir, "s3_rank")["payload"]["candidates"]) == 5
    assert s4_filter.main(["--run-dir", str(tmp_path / "nope")]) == 2                        # contract error -> exit code 2, no traceback
