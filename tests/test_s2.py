"""S2 annotation: real BLAST+ against a tiny VFDB, PSORTb faked (no Docker on the dev machine)."""
from __future__ import annotations

import csv
import shutil
from pathlib import Path

import pytest

from vaxforge import s1_acquire as S1
from vaxforge import s2_annotate as S2
from vaxforge.artifacts import ContractError, RunStore
from vaxforge.cache import Cache
from vaxforge.fasta import read_fasta
from vaxforge.tools import BlastRunner

from conftest import make_config
from fakes import FakeRunner, NoBlastRunner, build_server, write_s0

needs_blast = pytest.mark.skipif(not (shutil.which("blastp") and shutil.which("makeblastdb")), reason="BLAST+ not installed")

PSORTB_LABELS = ["OuterMembrane", "Unknown (This protein may have multiple localization sites.)", "Cytoplasmic"]


def fake_psortb(calls: list):
    def fn(query: Path, args: list, key: str) -> str:
        calls.append(" ".join(args))                 # e.g. "-n" or "-p --x-skip-localization=Cellwall"
        ids = [r.id for r in read_fasta(query)]
        scores = ["10.00", "2.00", "9.97"]
        return "SeqID\tLocalization\tScore\n" + "".join(f"{i}\t{l}\t{s}\n" for i, l, s in zip(ids, PSORTB_LABELS, scores))
    return fn


def prepare(store, cfg, tmp_path, **s0kw):
    write_s0(store, **s0kw)
    art = S1.run(store, cfg, transport=build_server(), runner=FakeRunner(tmp_path / "bdb"))
    assert art.status == "ok", art.warnings
    return art


def tsv_rows(store, art):
    with open(store.resolve(art.payload["tsv"]), encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t"))


# ------------------------------------------------------------------ the full happy path
@needs_blast
def test_s2_full_annotation(store, cfg, tmp_path):
    prepare(store, cfg, tmp_path)
    calls: list = []
    art = S2.run(store, cfg, psortb_fn=fake_psortb(calls))
    assert art.status == "ok", art.warnings
    prots = {p["accession"]: p for p in art.payload["proteins"]}
    assert len(prots) == 3 and calls == ["-n"]                         # Gram-negative flag came from S0

    p1, p2, p3 = (prots[a] for a in ("Q100001", "Q200001", "Q300001"))
    v1 = p1["virulence"]
    assert v1["status"] == "hit" and v1["evidence"] == "strong" and v1["is_virulence_associated"] is True
    b = v1["best_hit"]
    assert b["vfdb_id"] == "VFG000001" and b["gene"] == "plc" and b["factor"] == "Phospholipase C"
    assert b["category"] == "Exotoxin" and b["factor_id"] == "VF0470" and b["species"].startswith("Pseudomonas")
    assert b["identity_pct"] == 100.0 and b["query_coverage_pct"] == 100.0 and b["evalue"] < 1e-100   # all three recorded
    assert p2["virulence"]["best_hit"]["gene"] == "hap"
    assert p3["virulence"] == {"status": "no_hit", "evidence": "none", "is_virulence_associated": False,
                               "best_hit": None, "n_hits": 0, "hits": []}

    assert p1["localisation"]["label"] == "OuterMembrane" and p1["localisation"]["accessibility"] == "accessible"
    # Unknown stays Unknown - never collapsed into "not surface"
    l2 = p2["localisation"]
    assert l2["label"] == "Unknown" and l2["status"] == "psortb_unknown" and l2["multiple_sites"] is True
    assert l2["accessibility"] == "unknown"
    assert p3["localisation"]["accessibility"] == "inaccessible" and p3["localisation"]["score"] == pytest.approx(9.97)

    s = art.payload["summary"]
    assert s["n_proteins"] == 3 and s["virulence"]["n_associated"] == 2 and s["localisation"]["n_unknown"] == 1
    assert s["localisation"]["by_accessibility"] == {"accessible": 1, "unknown": 1, "inaccessible": 1}
    assert "NOT 'not surface'" in s["localisation"]["note"]

    rows = tsv_rows(store, art)
    assert list(rows[0].keys()) == S2.TSV_COLUMNS and len(rows) == 3
    assert rows[0]["vf_best_gene"] == "plc" and rows[1]["loc_label"] == "Unknown" and rows[1]["accessibility"] == "unknown"
    assert rows[2]["vf_status"] == "no_hit" and rows[2]["vf_best_id"] == ""

    assert art.inputs.keys() == {"s0", "s1"} and store.verify() == []
    assert art.payload["thresholds"]["max_evalue"] == pytest.approx(1e-5)
    assert art.producer["tools"]["blastp"] and art.params["gram_flag"] == "-n"


@needs_blast
def test_s2_second_run_uses_cache_for_blast_and_psortb(store, cfg, tmp_path):
    prepare(store, cfg, tmp_path)
    calls: list = []
    S2.run(store, cfg, psortb_fn=fake_psortb(calls))
    art = S2.run(store, cfg, psortb_fn=fake_psortb(calls))
    assert calls == ["-n"]                                   # PSORTb ran once only
    assert art.volatile["virulence_cache_hit"] is True and art.volatile["localisation_cache_hit"] is True
    assert art.payload["proteins"][1]["localisation"]["label"] == "Unknown"        # survives the cache round trip


@needs_blast
def test_thresholds_change_the_call_but_keep_the_raw_numbers(store, cfg, tmp_path):
    prepare(store, cfg, tmp_path)
    cfg.set("annotate.vfdb.min_identity", 101.0)               # impossible -> nothing "associated"
    art = S2.run(store, cfg, psortb_fn=fake_psortb([]))
    v = art.payload["proteins"][0]["virulence"]
    assert v["is_virulence_associated"] is False and v["evidence"] == "weak" and v["status"] == "hit"
    assert v["best_hit"]["identity_pct"] == 100.0              # evidence is still there
    assert art.payload["thresholds"]["min_identity"] == 101.0


@needs_blast
def test_blast_works_from_a_non_ascii_cache_dir(tmp_path):
    """BLAST+ on Windows cannot open non-ASCII paths; BlastRunner must hide that."""
    root = tmp_path / "数据" / "项目"
    cfg = make_config(root)
    st = RunStore(root / "runs" / "t", cache_dir=root / "cache")
    prepare(st, cfg, tmp_path)
    art = S2.run(st, cfg, psortb_fn=fake_psortb([]))
    assert art.payload["summary"]["virulence"]["n_associated"] == 2


# ------------------------------------------------------------------ explicit non-results
def test_no_psortb_is_not_run_not_unknown(store, cfg, tmp_path, monkeypatch):
    monkeypatch.setattr(S2, "find_executable", lambda *a, **k: None)
    prepare(store, cfg, tmp_path)
    art = S2.run(store, cfg, runner=NoBlastRunner())           # also no BLAST here
    loc = art.payload["proteins"][0]["localisation"]
    assert loc["status"] == "not_run" and loc["label"] == "Unknown" and loc["accessibility"] == "unknown"
    assert "Docker" in loc["reason"] and "--psortb-results" in loc["reason"]
    assert art.payload["summary"]["localisation"]["run_status"] == "not_run"
    assert any("localisation not run" in w for w in art.warnings)
    # the same Unknown label, but distinguishable from PSORTb answering Unknown
    assert loc["status"] != "psortb_unknown"


def test_virulence_not_run_when_blast_missing(store, cfg, tmp_path, monkeypatch):
    prepare(store, cfg, tmp_path)

    art = S2.run(store, cfg, runner=NoBlastRunner(), psortb_fn=fake_psortb([]))
    v = art.payload["proteins"][0]["virulence"]
    assert v["status"] == "not_run" and v["is_virulence_associated"] is None        # not False: we did not look
    assert any("BLAST+ not found" in w for w in art.warnings)
    assert art.payload["tools"]["virulence"]["status"] == "not_run"


def test_virus_is_not_applicable_for_both_annotations(store, cfg, tmp_path):
    prepare(store, cfg, tmp_path, domain="virus", name="Severe acute respiratory syndrome coronavirus 2", gram="not_applicable")
    art = S2.run(store, cfg)
    p = art.payload["proteins"][0]
    assert p["virulence"]["status"] == "not_applicable" and p["localisation"]["status"] == "not_applicable"
    assert art.warnings == []                                         # not applicable is not a problem
    assert "bacterial" in art.payload["tools"]["virulence"]["reason"]


def test_advanced_gram_classes_use_the_flags_psortb_documents(store, cfg, tmp_path):
    """PSORTb USAGE: Mycoplasma-type organisms -> -p --x-skip-localization=Cellwall; Mycobacterium-type -> -n."""
    prepare(store, cfg, tmp_path, gram="negative_without_outer_membrane")
    calls: list = []
    art = S2.run(store, cfg, runner=NoBlastRunner(), psortb_fn=fake_psortb(calls))
    assert calls == ["-p --x-skip-localization=Cellwall"]
    assert art.params["gram_flag"] == "-p" and art.params["gram_extra_args"] == ["--x-skip-localization=Cellwall"]
    art2 = S2.run(store, cfg, runner=NoBlastRunner(), psortb_fn=fake_psortb(calls), gram="positive_with_outer_membrane")
    assert calls[-1] == "-n" and art2.params["gram_extra_args"] == []          # the --gram override beats the lineage rule
    assert art2.payload["proteins"][0]["localisation"]["label"] == "OuterMembrane"


def test_psortb_docker_command_follows_the_documented_wrapper(monkeypatch, cfg, tmp_path):
    monkeypatch.setattr(S2, "find_executable", lambda name, *a, **k: "docker" if name == "docker" else None)
    runner = S2.PsortbRunner(cfg, tmp_path)
    assert runner.mode == "docker"
    out = tmp_path / "out"
    cmd = runner.command(["-p", "--x-skip-localization=Cellwall"], out)
    assert cmd[:3] == ["docker", "run", "--rm"]
    assert f"{out}:/tmp/results" in cmd                                                   # results folder mounted where the wrapper puts it
    assert cmd[cmd.index("--entrypoint") + 1] == "/usr/local/psortb/bin/psort"            # explicit: the wrapper passes the binary path itself
    assert cmd[cmd.index(runner.image) + 1:] == ["-p", "--x-skip-localization=Cellwall", "-i", "/tmp/results/input.fasta", "-o", "terse"]
    assert "-r" not in cmd          # for the real `psort`, -r means PSORT_ROOT; only the wrapper script reuses it as a results dir


def test_psortb_command_template_override(monkeypatch, cfg, tmp_path):
    monkeypatch.setattr(S2, "find_executable", lambda name, *a, **k: "docker" if name == "docker" else None)
    cfg.set("annotate.psortb.command", ["{docker}", "run", "{image}", "{args}", "--custom", "{out}"])
    runner = S2.PsortbRunner(cfg, tmp_path)
    assert runner.command(["-n"], tmp_path / "o") == ["docker", "run", runner.image, "-n", "--custom", str(tmp_path / "o")]


def test_psortb_runner_reads_the_result_file_the_container_writes(monkeypatch, cfg, tmp_path):
    import types
    from vaxforge.fasta import FastaRecord, write_fasta
    monkeypatch.setattr(S2, "find_executable", lambda name, *a, **k: "docker" if name == "docker" else None)
    seen = {}

    def fake_run_command(cmd, cwd=None, timeout=None, input_text=None):
        seen["cmd"] = cmd
        out = Path(cmd[cmd.index("-v") + 1].split(":/tmp/results")[0])
        ids = [r.id for r in read_fasta(out / "input.fasta")]              # the input must be inside the mounted folder
        (out / "psortb.log").write_text("this is not a result table", encoding="utf-8")
        (out / "20260101T000000_psortb_gramneg.txt").write_text(
            "SeqID\tLocalization\tScore\n" + "".join(f"{i}\tExtracellular\t9.9\n" for i in ids), encoding="utf-8")
        return types.SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr(S2, "run_command", fake_run_command)
    from vaxforge.fasta import FastaRecord, write_fasta
    fasta = tmp_path / "q.faa"
    write_fasta([FastaRecord("q0000001", "MKT" * 20)], fasta)
    text = S2.PsortbRunner(cfg, tmp_path).run(fasta, ["-n"], "k1")
    assert S2.parse_psortb(text)["q0000001"]["label"] == "Extracellular"      # newest file that PARSES, not just the newest file
    assert "--entrypoint" in seen["cmd"]


def test_psortb_runner_fails_clearly_when_no_result_file_appears(monkeypatch, cfg, tmp_path):
    import types
    from vaxforge.fasta import FastaRecord, write_fasta
    from vaxforge.tools import ToolError
    monkeypatch.setattr(S2, "find_executable", lambda name, *a, **k: "docker" if name == "docker" else None)
    monkeypatch.setattr(S2, "run_command", lambda *a, **k: types.SimpleNamespace(stdout="", stderr="", returncode=0))
    fasta = tmp_path / "q.faa"
    write_fasta([FastaRecord("q0000001", "MKT" * 20)], fasta)
    with pytest.raises(ToolError, match="no result file"):
        S2.PsortbRunner(cfg, tmp_path).run(fasta, ["-n"], "k2")


def test_native_psort_takes_the_fasta_positionally_and_stdout_is_the_result(monkeypatch, cfg, tmp_path):
    import types
    from vaxforge.fasta import FastaRecord, write_fasta
    monkeypatch.setattr(S2, "find_executable", lambda name, *a, **k: "/usr/bin/psort" if name == "psort" else None)
    captured = {}

    def fake_run_command(cmd, cwd=None, timeout=None, input_text=None):
        captured["cmd"] = cmd
        return types.SimpleNamespace(stdout="q0000001\tCytoplasmic\t9.0\n", stderr="", returncode=0)

    monkeypatch.setattr(S2, "run_command", fake_run_command)
    fasta = tmp_path / "q.faa"
    write_fasta([FastaRecord("q0000001", "MKT" * 20)], fasta)
    runner = S2.PsortbRunner(cfg, tmp_path)
    assert runner.mode == "native"
    text = runner.run(fasta, ["-n"], "k3")
    assert captured["cmd"][:4] == ["/usr/bin/psort", "-n", "-o", "terse"] and captured["cmd"][-1].endswith("input.fasta")
    assert S2.parse_psortb(text)["q0000001"]["label"] == "Cytoplasmic"


def test_psortb_mode_off_survives_the_yaml_boolean_quirk(store, cfg, tmp_path):
    prepare(store, cfg, tmp_path)
    cfg.set("annotate.psortb.mode", False)            # what PyYAML (YAML 1.1) makes of an unquoted `off`
    calls: list = []
    art = S2.run(store, cfg, runner=NoBlastRunner(), psortb_fn=fake_psortb(calls))
    loc = art.payload["proteins"][0]["localisation"]
    assert calls == [] and loc["status"] == "not_run" and "'off'" in loc["reason"]


def test_unknown_gram_requires_override(store, cfg, tmp_path):
    prepare(store, cfg, tmp_path, gram="unknown")
    calls: list = []
    art = S2.run(store, cfg, runner=NoBlastRunner(), psortb_fn=fake_psortb(calls))
    assert art.payload["proteins"][0]["localisation"]["status"] == "not_run" and calls == []
    assert any("Gram stain unknown" in w for w in art.warnings)
    art2 = S2.run(store, cfg, runner=NoBlastRunner(), psortb_fn=fake_psortb(calls), gram="positive")
    assert calls == ["-p"] and art2.payload["proteins"][0]["localisation"]["label"] == "OuterMembrane"
    with pytest.raises(ContractError, match="Gram"):
        S2.run(store, cfg, runner=NoBlastRunner(), gram="purple")


def test_import_psortb_results_by_original_ids(store, cfg, tmp_path):
    prepare(store, cfg, tmp_path)
    s1 = store.read_artifact("s1").payload
    ids = [r.id for r in read_fasta(store.resolve(s1["proteome"]["file"]))]
    f = tmp_path / "psortb_web.txt"
    f.write_text(f"SeqID\tLocalization\tScore\n{ids[0]}\tExtracellular\t9.5\n{ids[1]}\tPeriplasmic\t8.0\n", encoding="utf-8")
    art = S2.run(store, cfg, runner=NoBlastRunner(), psortb_results=f)
    labs = [p["localisation"] for p in art.payload["proteins"]]
    assert [x["label"] for x in labs[:2]] == ["Extracellular", "Periplasmic"] and labs[0]["source"] == "import:psortb_web.txt"
    assert labs[2]["status"] == "missing_from_psortb_output" and labs[2]["label"] == "Unknown"
    assert any("absent from the PSORTb output" in w for w in art.warnings)


def test_psortb_failure_is_reported_not_raised(store, cfg, tmp_path):
    from vaxforge.tools import ToolError
    prepare(store, cfg, tmp_path)

    def boom(*a):
        raise ToolError("docker exploded")

    art = S2.run(store, cfg, runner=NoBlastRunner(), psortb_fn=boom)
    assert art.status == "ok" and art.payload["proteins"][0]["localisation"]["status"] == "not_run"
    assert any("docker exploded" in w for w in art.warnings)


def test_requires_upstream_ok_and_intact_proteome(store, cfg, tmp_path):
    with pytest.raises(ContractError, match="s0 artifact not found"):
        S2.run(store, cfg)
    prepare(store, cfg, tmp_path)
    s1 = store.read_artifact("s1").payload
    store.resolve(s1["proteome"]["file"]).write_text(">x\nMK\n", encoding="utf-8")       # tamper with the proteome
    with pytest.raises(ContractError, match="not usable"):
        S2.run(store, cfg, runner=NoBlastRunner())


# ------------------------------------------------------------------ parsers
def test_parse_psortb_terse_normal_and_labels():
    terse = ("SeqID\tLocalization\tScore\n"
             "a\tCytoplasmicMembrane\t7.88\n"
             "b\tUnknown (This protein may have multiple localization sites.)\t2.00\n"
             "c\tUnknown\t0.0\n"
             "d\tCell Wall\t9.1\n")
    r = S2.parse_psortb(terse)
    assert r["a"] == {"label": "CytoplasmicMembrane", "score": 7.88, "multiple_sites": False, "raw_label": "CytoplasmicMembrane"}
    assert r["b"]["label"] == "Unknown" and r["b"]["multiple_sites"] is True
    assert r["c"]["multiple_sites"] is False and r["d"]["label"] == "Cellwall"

    normal = ("SeqID: p0000001\nAnalysis Report:\n  CMSVM-   Unknown\nLocalization Scores:\n   Cytoplasmic  2.00\n"
              "Final Prediction:\n   OuterMembrane   9.93\n\nSeqID: p0000002\nFinal Prediction:\n   Unknown   2.00\n")
    n = S2.parse_psortb(normal)
    assert n["p0000001"]["label"] == "OuterMembrane" and n["p0000001"]["score"] == 9.93 and n["p0000002"]["label"] == "Unknown"
    assert S2.parse_psortb("") == {} and S2.parse_psortb("garbage line without structure") == {}


def test_parse_vfdb_header_variants():
    h = "VFG037176(gb|WP_001081735) (plc1) phospholipase C [Phospholipase C (VF0470) - Exotoxin (VFC0235)] [Acinetobacter baumannii ACICU]"
    d = S2.parse_vfdb_header(h)
    assert (d["vfdb_id"], d["gene"], d["product"], d["factor"], d["factor_id"], d["category"], d["category_id"]) == (
        "VFG037176", "plc1", "phospholipase C", "Phospholipase C", "VF0470", "Exotoxin", "VFC0235")
    assert d["species"] == "Acinetobacter baumannii ACICU"
    no_gene = S2.parse_vfdb_header("VFG000009(gb|NP_9) hypothetical [Thing (VF0001) - Cat (VFC0001)] [Some species]")
    assert no_gene["gene"] is None and no_gene["product"] == "hypothetical"
    weird = S2.parse_vfdb_header("VFG000010(gb|NP_10) something totally different")
    assert weird["vfdb_id"] == "VFG000010" and weird["factor"] is None and weird["raw"].startswith("VFG000010")


def test_blast_row_parser_rejects_garbage():
    with pytest.raises(ValueError):
        S2.parse_blast_rows("only\ttwo")
    assert S2.parse_blast_rows("") == []
