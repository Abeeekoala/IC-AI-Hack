"""Stream B against Stream A's landed contract (docs/ARTIFACT_CONTRACT.md) using a vendored copy of their fixture."""
import b_fixtures  # adds src to sys.path
from b_fixtures import cfg_fixture, freq_file_fixture, run_dir_fixture

_FIXTURES = (cfg_fixture, freq_file_fixture, run_dir_fixture)   # pytest registers fixtures found as module attributes (names set in b_fixtures)
_ = b_fixtures
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from b_fakes import FakeBepi, FakeBinder, FakeDisco, FakeFolder, FakeGate, FakeScores, MiniStore, helix_coords, write_pdb_ca
from vaxforge import s3_rank, s4_filter, s5_epitopes, s7_structure
from vaxforge.prediction import artifacts as art
from vaxforge.prediction import structure_utils
from vaxforge.prediction.config import ConfigError, coerce_config, load_config
from vaxforge.prediction.errors import ContractError, ToolUnavailable
from vaxforge.prediction.schema import load_s2_annotations

FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "nmb_fixture"          # Stream A's committed S0-S2 run


@pytest.fixture
def a_run(tmp_path):
    d = tmp_path / "runs" / "nmb_fixture"
    shutil.copytree(FIXTURE, d)
    return d


@pytest.fixture
def a_cfg(tmp_path):
    return load_config(overrides={"refdata": {"cache_dir": str(tmp_path / "cache"), "offline": True}, "s3": {"min_candidates": 10},
                                  "s7": {"top_k": 3}})


def test_adapter_reads_stream_a_s2_with_sequences_from_the_fasta_file(a_run):
    d = load_s2_annotations(a_run / "s2_annotate.json")
    raw = {r["protein_id"]: r for r in json.loads((a_run / "s2_annotate.json").read_text())["payload"]["proteins"]}
    assert len(d.proteins) == 40 == len(raw)
    for p in d.proteins:
        assert len(p.sequence) == raw[p.protein_id]["length"]                      # sequence really came from data/proteome.faa
        assert p.accession == raw[p.protein_id]["accession"] == p.protein_id.split("|")[1]
    assert d.meta["organism_type"] == "gram-" and d.meta["species_taxid"] == 487 and d.meta["taxid"] == 487
    assert d.meta["localisation_status_counts"] == {"fixture_stand_in": 40}
    by = d.by_id()
    nhba, fhbp = by["sp|Q7DD37|NHBA_NEIMB"], by["sp|Q9JXV4|FHBP_NEIMB"]
    assert nhba.virulence is None                                                  # "no_hit" -> no usable virulence evidence
    bh = raw["sp|Q9JXV4|FHBP_NEIMB"]["virulence"]["best_hit"]
    assert fhbp.virulence.identity == pytest.approx(bh["identity_pct"] / 100) and fhbp.virulence.coverage == pytest.approx(bh["query_coverage_pct"] / 100)
    assert fhbp.virulence.evalue == bh["evalue"]


def test_stream_a_s2_that_is_not_ok_is_refused(a_run):
    f = a_run / "s2_annotate.json"
    doc = json.loads(f.read_text())
    doc["status"] = "failed"
    f.write_text(json.dumps(doc))
    with pytest.raises(ContractError, match="not 'ok'"):
        load_s2_annotations(f)


def test_localisation_not_run_is_unknown_even_if_a_label_is_present(a_run):
    f = a_run / "s2_annotate.json"
    doc = json.loads(f.read_text())
    doc["payload"]["proteins"][0]["localisation"].update({"status": "not_run", "label": "Cytoplasmic", "reason": "no PSORTb"})
    f.write_text(json.dumps(doc))
    p = load_s2_annotations(f).proteins[0]
    assert p.localisation.label == "Unknown" and p.localisation.status == "not_run" and p.localisation.reason == "no PSORTb"


def test_s3_on_the_stream_a_fixture_keeps_accession_and_flags_stand_in_localisation(a_run, a_cfg):
    s3_rank.run(a_run, a_cfg, backend=FakeScores())
    env = art.read_artifact(a_run / "s3_rank.json")
    c = env["payload"]["candidates"][0]
    assert c["accession"] == c["protein_id"].split("|")[1] and "sequence" in c
    assert any("fixture stand-in" in w for w in env["warnings"])
    assert env["payload"]["context"]["organism_type"] == "gram-"
    assert env["inputs"][0]["artifact_id"].startswith("sha256:")                    # Stream A's digest is picked up


def test_stage_writes_through_a_runstore_like_object(a_run, a_cfg):
    store = MiniStore(a_run)
    out = s3_rank.run(store, a_cfg, backend=FakeScores())
    assert store.writes == ["s3"] and out == store.artifact_path("s3")
    a = store.read_artifact("s3")
    assert a.status == "ok" and list(a.inputs) == ["s2"]                           # DEFAULT_INPUTS chain
    assert set(a.payload) >= {"candidates", "ranking", "leakage_audit"}
    assert all(isinstance(v, str) for v in a.__dict__["_e"]["producer"]["tools"].values())      # tools: name -> version string
    # S4 reads S3 (+ S1) from the store and writes via the store too
    s4_filter.run(store, a_cfg, gates={g: FakeGate(g) for g in s4_filter.GATES})
    assert store.writes == ["s3", "s4"] and list(store.read_artifact("s4").inputs) == ["s3", "s1"]


def test_store_artifacts_are_reused_when_params_and_inputs_are_unchanged(a_run, a_cfg):
    cfg = load_config(overrides={"refdata": {"cache_dir": str(a_run), "offline": True}, "s3": {"backend": "subtractive_filter"}})
    store = MiniStore(a_run)
    s3_rank.run(store, cfg)
    s3_rank.run(store, cfg)
    assert store.writes == ["s3"]                                                  # second call reused the artifact
    s3_rank.run(store, load_config(overrides={"refdata": {"cache_dir": str(a_run), "offline": True},
                                              "s3": {"backend": "subtractive_filter", "max_candidates": 7}}))
    assert store.writes == ["s3", "s3"]                                            # changed params -> recomputed


class FakeStreamAConfig:
    """Shape of vaxforge.config.Config: .raw, .cache_dir, .offline."""
    def __init__(self, raw, cache_dir, offline):
        self.raw, self.cache_dir, self.offline = raw, Path(cache_dir), offline


def test_coerce_config_maps_stream_a_sections():
    raw = {"pipeline": {"top_k": 7}, "thresholds": {"protegenicity": [0.9, 0.85], "allergenicity": 0.5, "toxicity": 0.8, "conservancy_identity": 0.8},
           "epitopes": {"bcell_tool": "bepipred-3.0", "mhc_class_ii_weight": 0.3}, "structure": {"engine": "boltz-2", "refolder": ["esmfold"]},
           "paths": {"cache_dir": "cache"}, "network": {"offline": False}, "taxonomy": {"fuzzy": True}, "acquire": {"proteome": {}},
           "tools": {"docker": "docker", "blast_image": "ncbi/blast:2.16.0", "blast_bin_dir": None}}
    cfg = coerce_config(FakeStreamAConfig(raw, "/abs/cache", True))
    assert cfg["refdata"]["cache_dir"] == "/abs/cache" and cfg["refdata"]["offline"] is True and cfg["run"]["top_k"] == 7
    assert cfg["thresholds"]["allergenicity"] == 0.5 and cfg["tools"]["blast_docker_image"] == "ncbi/blast:2.16.0"
    assert "taxonomy" not in cfg and cfg["s3"]["backend"] == "auto"               # their sections ignored, our defaults kept
    bad = dict(raw, structure={"engine": "alphafold3", "refolder": ["esmfold"]})
    with pytest.raises(ConfigError, match="boltz-2"):                              # project rules still enforced on their config
        coerce_config(FakeStreamAConfig(bad, "/abs/cache", False))
    assert coerce_config(None)["s7"]["top_k"] is None


def test_sidecar_files_are_stream_a_file_refs_that_verify(a_run, a_cfg, tmp_path):
    """Their `vaxforge verify` hashes every {root, path, sha256} object: ours must point at real files with correct hashes."""
    import hashlib
    s3_rank.run(a_run, a_cfg, backend=FakeScores())
    s4_filter.run(a_run, a_cfg, gates={g: FakeGate(g) for g in s4_filter.GATES})
    cands = art.read_artifact(a_run / "s4_filter.json")["payload"]["candidates"]
    by_acc = {c["accession"]: c for c in cands}
    s5_epitopes.run(a_run, a_cfg, class_i=FakeBinder("I"), class_ii=FakeBinder("II"), bcell=FakeBepi(), discotope=FakeDisco(),
                    structure_lookup=lambda acc: write_pdb_ca(tmp_path / f"{acc}.pdb", by_acc[acc]["sequence"], helix_coords(len(by_acc[acc]["sequence"])), 90.0))
    refs = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("root") in ("run", "cache") and "sha256" in o and isinstance(o.get("path"), str):
                refs.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)
    walk(art.read_artifact(a_run / "s5_epitopes.json")["payload"])
    assert refs and all(r["root"] == "run" for r in refs)
    for r in refs:
        f = a_run / r["path"]
        assert f.is_file() and hashlib.sha256(f.read_bytes()).hexdigest() == r["sha256"] and f.stat().st_size == r["bytes"]
    conf = art.read_artifact(a_run / "s5_epitopes.json")["payload"]["candidates"][0]["bcell"]["conformational"]
    assert conf["status"] == "ran"                                                 # AlphaFold lookup used the UniProt accession, not 'sp|ACC|ENTRY'


# ------------------------------------------------------------------------ numpy must not be a hard import
def test_stage_modules_import_without_numpy():
    code = ("import sys; sys.modules['numpy'] = None; sys.path.insert(0, %r);"
            "import vaxforge.s3_rank, vaxforge.s4_filter, vaxforge.s5_epitopes, vaxforge.s6_coverage, vaxforge.s7_structure; print('ok')"
            % str(Path(__file__).resolve().parents[2] / "src"))
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr[-500:]


def test_structure_functions_report_missing_numpy_clearly(monkeypatch, tmp_path):
    monkeypatch.setattr(structure_utils, "np", None)
    with pytest.raises(ToolUnavailable, match="numpy"):
        structure_utils.parse_structure_ca(tmp_path / "x.pdb")
    with pytest.raises(ToolUnavailable, match="numpy"):
        structure_utils.plddt_summary([1.0])


def test_s5_and_s7_degrade_instead_of_crashing_without_numpy(a_run, a_cfg, tmp_path, monkeypatch):
    from test_b_s5_s6_s7 import REFOLDERS
    s3_rank.run(a_run, a_cfg, backend=FakeScores())
    s4_filter.run(a_run, a_cfg, gates={g: FakeGate(g) for g in s4_filter.GATES})
    cands = art.read_artifact(a_run / "s4_filter.json")["payload"]["candidates"]
    by_acc = {c["accession"]: c for c in cands}
    pdb = lambda acc: write_pdb_ca(tmp_path / f"{acc}.pdb", by_acc[acc]["sequence"], helix_coords(len(by_acc[acc]["sequence"])), 90.0)   # noqa: E731
    monkeypatch.setattr(structure_utils, "np", None)
    s5_epitopes.run(a_run, a_cfg, class_i=FakeBinder("I"), class_ii=FakeBinder("II"), bcell=FakeBepi(), discotope=FakeDisco(), structure_lookup=pdb)
    conf = art.read_artifact(a_run / "s5_epitopes.json")["payload"]["candidates"][0]["bcell"]["conformational"]
    assert conf["status"] == "skipped" and "numpy" in conf["reason"]
    from vaxforge import s6_coverage
    from vaxforge.prediction.popcov import FrequencyTable
    from b_fakes import FREQ_TSV
    (tmp_path / "f.tsv").write_text(FREQ_TSV)
    s6_coverage.run(a_run, a_cfg, table=FrequencyTable.from_tsv(tmp_path / "f.tsv"))
    s7_structure.run(a_run, a_cfg, folder=FakeFolder(), refolders=REFOLDERS(), discotope=FakeDisco())
    top = [c for c in art.read_artifact(a_run / "s7_structure.json")["payload"]["candidates"] if c["in_top_k"]]
    assert top and all(c["structure"]["status"] == "failed" and "numpy" in c["structure"]["reason"] for c in top)


# ------------------------------------------------- behaviours added for the merge into the team repo
def test_esmfold_is_local_by_default_and_the_web_service_is_opt_in(monkeypatch):
    import importlib.util
    cfg = load_config()
    assert cfg["s7"]["esmfold"]["mode"] == "local"                                  # no sequence leaves the machine by default
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *a, **k: None if name == "transformers" else real(name, *a, **k))
    runner, why = s7_structure.resolve_refolders(cfg)["esmfold"]
    assert runner is None and "s7.esmfold.mode: api" in why and "third-party" in why
    api = load_config(overrides={"s7": {"esmfold": {"mode": "api"}}})
    assert s7_structure.resolve_refolders(api)["esmfold"][0].mode == "api"          # explicit opt-in still works
    api_offline = load_config(overrides={"s7": {"esmfold": {"mode": "api"}}, "refdata": {"offline": True}})
    assert s7_structure.resolve_refolders(api_offline)["esmfold"][0] is None


def test_load_config_reads_the_team_config_yaml_layout(tmp_path):
    f = tmp_path / "config.yaml"
    f.write_text("pipeline:\n  top_k: 5\npaths:\n  cache_dir: /data/cache\nnetwork:\n  offline: true\n"
                 "thresholds:\n  protegenicity: [0.9, 0.85]\n  allergenicity: 0.5\ntools:\n  docker: docker\n  blast_image: ncbi/blast:2.16.0\n"
                 "taxonomy:\n  fuzzy: true\ns4:\n  conservancy:\n    on_fail: flag\n")
    cfg = load_config(f)
    assert cfg["run"]["top_k"] == 5 and cfg["refdata"]["cache_dir"] == "/data/cache" and cfg["refdata"]["offline"] is True
    assert cfg["tools"]["blast_docker_image"] == "ncbi/blast:2.16.0" and cfg["thresholds"]["allergenicity"] == 0.5
    assert cfg["s4"]["conservancy"]["on_fail"] == "flag" and cfg["s4"]["conservancy"]["enabled"] is True      # section merged, siblings kept
    assert "taxonomy" not in cfg


def test_stage_command_writes_through_the_runstore_for_a_stream_a_run_directory(a_run, tmp_path, monkeypatch):
    import types
    created = []

    class Spy(MiniStore):
        def __init__(self, *a, **k):
            super().__init__(*a, **k)
            created.append(self)
    monkeypatch.setitem(sys.modules, "vaxforge.artifacts", types.SimpleNamespace(RunStore=Spy))
    rc = s3_rank.main(["--run-dir", str(a_run), "--set", 's3.backend="subtractive_filter"', "--set", f'refdata.cache_dir="{tmp_path / "c"}"'])
    assert rc == 0 and created and created[0].writes == ["s3"]                        # went through the store, not our own writer
    env = json.loads((a_run / "s3_rank.json").read_text())
    assert env["schema"] == "vaxforge/s3_rank/v1" and env["status"] == "ok" and "artifact_id" not in env


def test_stage_command_on_a_plain_directory_or_without_runstore_keeps_our_own_envelope(a_run, tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "vaxforge.artifacts", None)                        # `from vaxforge.artifacts import RunStore` -> ImportError
    assert s3_rank.main(["--run-dir", str(a_run), "--set", 's3.backend="subtractive_filter"', "--set", f'refdata.cache_dir="{tmp_path / "c"}"']) == 0
    assert json.loads((a_run / "s3_rank.json").read_text())["artifact_id"].startswith("sha256:")
