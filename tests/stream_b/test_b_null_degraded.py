"""Audit items for stream B, part 2 (board post 6887964689): an analysis that never ran reports null, not 0 / [] / an empty file (W2),
and every stage S3-S7 carries `payload.degraded`, a list of short reason strings that is empty when nothing is degraded (F1)."""
import b_fixtures  # adds src to sys.path
from b_fixtures import cfg_fixture, freq_file_fixture, run_dir_fixture

_FIXTURES = (cfg_fixture, freq_file_fixture, run_dir_fixture)   # pytest registers fixtures found as module attributes (names set in b_fixtures)
_ = b_fixtures

from b_fakes import FakeBinder, FakeDisco, FakeFolder, FakeGate, FakeScores
from test_b_s5_s6_s7 import REFOLDERS, load, run_s5, s7_inputs, upto_s4
from vaxforge import s3_rank, s4_filter, s5_epitopes, s6_coverage, s7_structure
from vaxforge.prediction.config import deep_merge
from vaxforge.prediction.netmhc import MHCflurryRunner


class EmptyBinder(FakeBinder):
    """A predictor that RAN and found no binders: that is a real zero, unlike a predictor that never ran."""

    def predict(self, seqs, panel, lengths, **kw):
        return []


# ============================================================================= W2
def test_s5_reports_null_not_zero_and_writes_no_empty_file_when_nothing_ran(run_dir, cfg, monkeypatch):
    upto_s4(run_dir, cfg)
    monkeypatch.setattr(s5_epitopes, "resolve_executable", lambda *a, **k: None)
    monkeypatch.setattr(MHCflurryRunner, "installed", staticmethod(lambda: False))
    s5_epitopes.run(run_dir, cfg)
    pl = load(run_dir, "s5_epitopes")["payload"]
    assert pl["candidates"]
    for c in pl["candidates"]:
        for k in ("class_i", "class_ii"):
            blk = c["tcell"][k]
            assert all(blk[f] is None for f in ("n_epitopes", "n_promiscuous", "promiscuous_per_100aa", "score", "top", "selected")), blk
        assert c["tcell"]["all_epitopes_file"] is None
    assert not list((run_dir / "data" / "s5").glob("*_tcell.tsv"))                 # no header-only file for a prediction that never ran
    assert len(pl["degraded"]) == 4 and all(d.startswith("S5 ") and "did not run" in d for d in pl["degraded"])


def test_s5_a_predictor_that_ran_and_found_nothing_still_reports_a_real_zero(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)
    run_s5(run_dir, cfg, tmp_path, class_i=EmptyBinder("I"), class_ii=EmptyBinder("II"))
    pl = load(run_dir, "s5_epitopes")["payload"]
    for c in pl["candidates"]:
        for k in ("class_i", "class_ii"):
            assert c["tcell"][k]["n_epitopes"] == 0 and c["tcell"][k]["selected"] == [] and c["tcell"][k]["top"] == []
        assert c["tcell"]["all_epitopes_file"] is not None                          # ran: the (empty) result file is real
    assert pl["degraded"] == []


def test_s6_still_runs_when_one_t_cell_class_did_not_run(run_dir, cfg, tmp_path, freq_file, monkeypatch):
    upto_s4(run_dir, cfg)
    monkeypatch.setattr(s5_epitopes, "resolve_class_ii", lambda c: (None, "netMHCIIpan not found"))
    run_s5(run_dir, cfg, tmp_path, class_ii=None)
    assert load(run_dir, "s5_epitopes")["payload"]["candidates"][0]["tcell"]["class_ii"]["selected"] is None
    s6_coverage.run(run_dir, deep_merge(cfg, {"refdata": {"afnd_frequencies": str(freq_file)}}))        # used to be list(None) -> TypeError
    pl = load(run_dir, "s6_coverage")["payload"]
    assert pl["analysis"]["status"] == "ran" and pl["degraded"] == []


# ============================================================================= F1
def test_s3_and_s4_degraded_lists_the_fallback_ranker_and_the_gates_that_did_not_run(run_dir, cfg):
    s3_rank.run(run_dir, cfg, backend=FakeScores(), force=True)
    assert load(run_dir, "s3_rank")["payload"]["degraded"] == []                      # key always present, empty when fine
    s3_rank.run(run_dir, deep_merge(cfg, {"s3": {"backend": "subtractive_filter"}}), force=True)
    d = load(run_dir, "s3_rank")["payload"]["degraded"]
    assert len(d) == 1 and "rule-based fallback" in d[0] and "no protegenicity" in d[0]
    s3_rank.run(run_dir, cfg, backend=FakeScores(), force=True)
    s4_filter.run(run_dir, cfg, gates={g: FakeGate(g) for g in s4_filter.GATES}, force=True)
    assert load(run_dir, "s4_filter")["payload"]["degraded"] == []
    gates = {g: FakeGate(g) for g in s4_filter.GATES}
    gates["allergenicity"] = "unavailable: AlgPred2 not found"
    s4_filter.run(run_dir, cfg, gates=gates, force=True)
    pl = load(run_dir, "s4_filter")["payload"]
    assert pl["incomplete_screening"] is True and len(pl["degraded"]) == 1 and "'allergenicity' did not run" in pl["degraded"][0]


def test_s6_degraded_when_no_coverage_could_be_computed(run_dir, cfg, tmp_path):
    upto_s4(run_dir, cfg)
    run_s5(run_dir, cfg, tmp_path)
    s6_coverage.run(run_dir, deep_merge(cfg, {"refdata": {"afnd_frequencies": str(tmp_path / "missing.tsv")}}), force=True)
    pl = load(run_dir, "s6_coverage")["payload"]
    assert pl["analysis"]["status"] == "unavailable"
    assert len(pl["degraded"]) == 1 and pl["degraded"][0].startswith("S6 population coverage did not run")


def test_s7_degraded_names_what_is_missing(run_dir, cfg, tmp_path, freq_file, monkeypatch):
    s7_inputs(run_dir, cfg, tmp_path, freq_file)
    s7_structure.run(run_dir, cfg, folder=FakeFolder(), refolders=REFOLDERS(), discotope=FakeDisco(), force=True)
    assert load(run_dir, "s7_structure")["payload"]["degraded"] == []
    one = {"esmfold": REFOLDERS()["esmfold"], "af2-no-msa": None}                    # one validator missing
    s7_structure.run(run_dir, cfg, folder=FakeFolder(), refolders=one, discotope=FakeDisco(), force=True)
    d = load(run_dir, "s7_structure")["payload"]["degraded"]
    assert len(d) == 1 and "af2-no-msa" in d[0] and "single validator" in d[0]
    monkeypatch.setattr(s7_structure, "resolve_discotope", lambda cfg: None)
    s7_structure.run(run_dir, cfg, folder=FakeFolder(), refolders=REFOLDERS(), force=True)
    d = load(run_dir, "s7_structure")["payload"]["degraded"]
    assert len(d) == 1 and "DiscoTope-3.0 unavailable" in d[0]
    monkeypatch.setattr(s7_structure, "resolve_boltz", lambda cfg: (None, "boltz not found on PATH"))
    s7_structure.run(run_dir, cfg, force=True)
    d = load(run_dir, "s7_structure")["payload"]["degraded"]
    assert len(d) == 1 and d[0].startswith("S7 did not fold any structure") and "boltz not found" in d[0]
