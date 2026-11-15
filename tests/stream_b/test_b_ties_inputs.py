"""End-to-end audit items for stream B (board post 6887964689): ties are marked, not hidden (W1); declared inputs make
`verify` flag S7 when S6 is re-run (W4); a scores file can declare that its model did not hold out the target; the committed
real Vaxign-ML scores for fixtures/nmb_fixture load through S3."""
import b_fixtures  # adds src to sys.path
from b_fixtures import cfg_fixture, freq_file_fixture, run_dir_fixture

_FIXTURES = (cfg_fixture, freq_file_fixture, run_dir_fixture)   # pytest registers fixtures found as module attributes (names set in b_fixtures)
_ = b_fixtures
import json
import shutil
from pathlib import Path

import pytest

from b_fakes import FakeBepi, FakeBinder, FakeDisco, FakeFolder, FakeGate, FakeScores, helix_coords, write_pdb_ca
from test_b_s5_s6_s7 import REFOLDERS, load_cache, pdb_lookup_factory
from vaxforge import s3_rank, s4_filter, s5_epitopes, s6_coverage, s7_structure
from vaxforge.prediction import artifacts as art
from vaxforge.prediction.config import deep_merge
from vaxforge.prediction.scoring import groups_split_by_cut, tie_groups, tie_key_round, tie_summary

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
SCORES = ROOT / "docs" / "examples" / "vaxign_ml_scores.nmb_fixture.tsv"


def load(run_dir, stage):
    return art.read_artifact(run_dir / f"{stage}.json")


def s2_ids(run_dir):
    return [p["protein_id"] for p in json.loads((run_dir / "s2_annotate.json").read_text())["payload"]["proteins"]]


def accession_lookup(tmp_path):
    """S5 asks for a structure by accession on Stream A's real fixture (protein_id is 'sp|ACC|NAME'); the shared helper matches protein_id."""
    def lookup(acc):
        c = next(c for c in load_cache["cands"] if acc in (c.get("accession"), c["protein_id"]))
        return write_pdb_ca(tmp_path / f"{acc}.pdb", c["sequence"], helix_coords(len(c["sequence"])), 90.0)
    return lookup


def upto_s6(run_dir, cfg, tmp_path, freq_file, scores=None):
    s3_rank.run(run_dir, cfg, backend=FakeScores(scores))
    s4_filter.run(run_dir, cfg, gates={g: FakeGate(g) for g in s4_filter.GATES})
    load_cache["cands"] = load(run_dir, "s4_filter")["payload"]["candidates"]
    s5_epitopes.run(run_dir, cfg, class_i=FakeBinder("I"), class_ii=FakeBinder("II"), bcell=FakeBepi(), discotope=FakeDisco(),
                    structure_lookup=pdb_lookup_factory(tmp_path))
    s6_coverage.run(run_dir, deep_merge(cfg, {"refdata": {"afnd_frequencies": str(freq_file)}}))


# ============================================================================= W1: ties
def test_tie_helpers_use_competition_rank_and_only_flag_real_ties():
    g = tie_groups([(1.0,), (1.0,), (1.0,), (0.5,), (0.2,), (0.2,)])
    assert [x and (x["size"], x["competition_rank"]) for x in g] == [(3, 1), (3, 1), (3, 1), None, (2, 5), (2, 5)]
    assert tie_groups([("a",), ("b",)]) == [None, None]
    assert tie_groups([1, 1, 2], start=5)[0] == {"size": 2, "competition_rank": 5}
    assert tie_summary(g) == {"n_groups": 2, "n_tied_candidates": 5, "largest_group": {"size": 3, "competition_rank": 1}}
    assert groups_split_by_cut(g, [True, True, False, False, True, True]) == [{"size": 3, "competition_rank": 1, "kept": 2}]
    assert groups_split_by_cut(g, [True, True, True, False, False, False]) == []         # a cut between groups splits nothing
    assert tie_key_round(0.1 + 0.2) == tie_key_round(0.3) and tie_key_round(None) is None


def test_s3_marks_ties_keeps_the_ordinal_rank_and_calls_out_a_cut_inside_a_tie(run_dir, cfg):
    cfg3 = deep_merge(cfg, {"s3": {"min_candidates": 3, "max_candidates": 3}})
    s3_rank.run(run_dir, cfg3, backend=FakeScores({pid: 0.9 for pid in s2_ids(run_dir)}), force=True)
    env = load(run_dir, "s3_rank")
    pl = env["payload"]
    ranking = pl["ranking"]
    assert [r["rank"] for r in ranking] == list(range(1, len(ranking) + 1))              # still the ordinal position
    groups: dict = {}
    for r in ranking:
        if r["tie_group"]:
            groups.setdefault(r["tie_group"]["competition_rank"], []).append(r)
    assert groups, "equal scores must produce tie groups"
    for cr, members in groups.items():
        assert min(m["rank"] for m in members) == cr and all(m["tie_group"]["size"] == len(members) for m in members)
        assert len({(m["composite_score"], m["protegenicity"], m["terms"]["localisation"]) for m in members}) == 1
    assert pl["ties"]["n_groups"] == len(groups) and pl["ties"]["n_tied_candidates"] == sum(map(len, groups.values()))
    assert any("tied with another one" in w for w in env["warnings"])
    kept = {r["protein_id"] for r in pl["candidates"]}
    split = [cr for cr, ms in groups.items() if 0 < sum(m["protein_id"] in kept for m in ms) < len(ms)]
    assert len(kept) == 3 and split                                                      # the cut of 3 falls inside a tie here
    assert any("cut falls inside a tie" in w and f"rank {split[0]}" in w for w in env["warnings"])
    # distinct scores: no tie fields set, no tie warnings
    s3_rank.run(run_dir, cfg, backend=FakeScores(), force=True)
    env2 = load(run_dir, "s3_rank")
    assert all(r["tie_group"] is None for r in env2["payload"]["ranking"]) and env2["payload"]["ties"]["n_groups"] == 0
    assert not any("tied" in w for w in env2["warnings"])


def test_s7_marks_ties_and_warns_when_the_top_k_cut_splits_one(run_dir, cfg, tmp_path, freq_file, monkeypatch):
    upto_s6(run_dir, cfg, tmp_path, freq_file, scores={pid: 0.9 for pid in s2_ids(run_dir)})
    cands = load(run_dir, "s6_coverage")["payload"]["candidates"]
    pos = [(c["upstream"]["s3"]["tie_group"] or {}).get("competition_rank") for c in cands]
    assert any(pos), "S3's tie groups must reach S7's input (S4 -> S5 -> S6 carry them)"
    i = next(i for i in range(1, len(pos) - 1) if pos[i] and pos[i] == pos[i + 1] and pos[i - 1] != pos[i])   # a cut inside a tie group
    orig = s7_structure.score_group

    def flat(group, weights):                       # every candidate gets the same score: the scores cannot tell them apart
        out = orig(group, weights)
        for v in out.values():
            v["score"] = 0.5
        return out

    monkeypatch.setattr(s7_structure, "score_group", flat)
    s7_structure.run(run_dir, deep_merge(cfg, {"s7": {"top_k": i + 1}}), folder=FakeFolder(), refolders=REFOLDERS(),
                     discotope=FakeDisco(), force=True)
    env = load(run_dir, "s7_structure")
    pl = env["payload"]
    assert [c["rank"] for c in pl["candidates"]] == list(range(1, len(pl["candidates"]) + 1))      # ordinal, unchanged
    top = [c for c in pl["candidates"] if c["in_top_k"]]
    assert len(top) == i + 1 and all(c["tie_group"] == {"size": i + 1, "competition_rank": 1} for c in top)
    assert pl["ties"]["top_k"]["largest_group"] == {"size": i + 1, "competition_rank": 1}
    assert any(f"the top-{i + 1} cut falls inside a tie" in w for w in env["warnings"])
    assert any("are tied on the final score" in w for w in env["warnings"])


# ============================================================================= W4: declared inputs (real RunStore)
def test_s7_declares_s6_and_verify_flags_it_stale_when_s6_is_rerun(tmp_path, cfg, freq_file):
    from vaxforge.artifacts import RunStore
    d = tmp_path / "runs" / "nmb"
    shutil.copytree(FIXTURE, d)
    store = RunStore(d, cache_dir=tmp_path / "cache")
    s3_rank.run(store, cfg, backend=FakeScores())
    s4_filter.run(store, cfg, gates={g: FakeGate(g) for g in s4_filter.GATES})
    load_cache["cands"] = store.read_artifact("s4").payload["candidates"]
    s5_epitopes.run(store, cfg, class_i=FakeBinder("I"), class_ii=FakeBinder("II"), bcell=FakeBepi(), discotope=FakeDisco(),
                    structure_lookup=accession_lookup(tmp_path))
    cfg6 = deep_merge(cfg, {"refdata": {"afnd_frequencies": str(freq_file)}})
    s6_coverage.run(store, cfg6)
    s7_structure.run(store, cfg, folder=FakeFolder(), refolders=REFOLDERS(), discotope=FakeDisco())
    assert [sorted(store.read_artifact(s).inputs) for s in ("s3", "s4", "s5", "s6", "s7")] == [["s2"], ["s1", "s3"], ["s4"], ["s5"], ["s6"]]
    assert not any("STALE" in p for p in store.verify())
    s6_coverage.run(store, deep_merge(cfg6, {"s6": {"fairness": {"budget_k": 2}}}), force=True)      # S6 changes
    problems = store.verify()
    assert any(p.startswith("s7: STALE") for p in problems), problems


# ============================================================================= scores file
def test_scores_file_can_declare_that_its_model_did_not_hold_out_the_target(run_dir, cfg, tmp_path):
    f = tmp_path / "scores.tsv"
    f.write_text("protein_id\tscore\n" + "".join(f"{pid}\t{50 + i}\n" for i, pid in enumerate(s2_ids(run_dir))))
    meta = tmp_path / "scores.tsv.meta.json"
    cfg_f = deep_merge(cfg, {"s3": {"backend": "scores_file", "scores_file": str(f)}})
    meta.write_text(json.dumps({"model": "Vaxign-ML test", "scale": 100, "holdout_enforced": False}))
    s3_rank.run(run_dir, cfg_f, force=True)
    env = load(run_dir, "s3_rank")
    assert env["payload"]["leakage_audit"]["holdout_enforced"] is False and any("LEAKAGE RISK" in w for w in env["warnings"])
    meta.write_text(json.dumps({"model": "x", "scale": 100}))                              # no declaration: unknown, never True
    s3_rank.run(run_dir, cfg_f, force=True)
    env = load(run_dir, "s3_rank")
    assert env["payload"]["leakage_audit"]["holdout_enforced"] is None and not any("LEAKAGE RISK" in w for w in env["warnings"])


def test_the_committed_real_vaxign_ml_scores_cover_the_whole_fixture_and_run_through_s3(tmp_path, cfg):
    d = tmp_path / "runs" / "nmb"
    shutil.copytree(FIXTURE, d)
    rows = [ln.split("\t") for ln in SCORES.read_text().splitlines()[1:]]
    assert {r[0] for r in rows} == set(s2_ids(d)) and len(rows) == 40
    meta = json.loads(SCORES.with_name(SCORES.name + ".meta.json").read_text())
    assert meta["scale"] == 100 and meta["holdout_enforced"] is False and "sha256:" in meta["version"]
    s3_rank.run(d, deep_merge(cfg, {"s3": {"backend": "scores_file", "scores_file": str(SCORES)}}))
    env = load(d, "s3_rank")
    pl = env["payload"]
    assert pl["ranker"]["backend"] == "scores_file" and pl["counts"]["n_scored"] == 40
    assert pl["leakage_audit"]["holdout_enforced"] is False and any("LEAKAGE RISK" in w for w in env["warnings"])
    assert pl["ranking"][0]["protein_id"] == max(rows, key=lambda r: float(r[1]))[0]
    assert pl["ranking"][0]["protegenicity"] == pytest.approx(max(float(r[1]) for r in rows) / 100)


# ----------------------------------------------------------------------------- S4 flag mode (variable antigens are kept, not rejected)
def test_s4_cli_gate_flags_instead_of_rejecting_when_on_fail_is_flag(monkeypatch):
    from vaxforge import s4_filter

    def fake_run_cmd(argv, **kw):
        out = argv[argv.index("-o") + 1]
        open(out, "w").write("ID,Sequence,ML_Score,Prediction\nP1,AAAA,0.60,Allergen\nP2,CCCC,0.10,Non-Allergen\n")
    monkeypatch.setattr(s4_filter, "run_cmd", fake_run_cmd)
    cands = [{"protein_id": "P1", "sequence": "AAAA"}, {"protein_id": "P2", "sequence": "CCCC"}]
    hard = s4_filter.CliScoreGate("allergenicity", "AlgPred 2.0", ["x"], 0.45, 1).evaluate(cands)
    soft = s4_filter.CliScoreGate("allergenicity", "AlgPred 2.0", ["x"], 0.45, 1, on_fail="flag").evaluate(cands)
    assert (hard["P1"].status, hard["P2"].status) == ("reject", "pass")
    assert (soft["P1"].status, soft["P2"].status) == ("flag", "pass")
    assert "0.600" in soft["P1"].reason                              # the score that triggered the flag stays visible
