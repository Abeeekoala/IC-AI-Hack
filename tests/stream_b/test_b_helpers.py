"""Unit tests for fasta / hla / blast / netmhc / bepipred / discotope / refdata helpers."""
import b_fixtures  # adds src to sys.path
from b_fixtures import cfg_fixture, freq_file_fixture, run_dir_fixture

_FIXTURES = (cfg_fixture, freq_file_fixture, run_dir_fixture)   # pytest registers fixtures found as module attributes (names set in b_fixtures)
_ = b_fixtures
import gzip
import json
import textwrap

import pytest

from vaxforge.prediction import blast, hla
from vaxforge.prediction.bepipred import (CSV_HEADER, BepiPredRunner, assemble_scores, call_linear_epitopes,
                                          parse_raw_output, top_fraction_mean)
from vaxforge.prediction.config import ConfigError, load_config
from vaxforge.prediction.discotope import parse_discotope_csv, summarise
from vaxforge.prediction.errors import ToolFailed
from vaxforge.prediction.fasta import chunk_sequence, merge_chunk_scores, read_fasta, sanitise, write_fasta
from vaxforge.prediction.netmhc import NetMHCRunner, parse_netmhc_output
from vaxforge.prediction.refdata import Fetched, RefData, load_peptide_set
from vaxforge.prediction.schema import ContractError, load_s2_annotations, normalise_localisation


# ----------------------------------------------------------------------------- fasta
def test_fasta_roundtrip(tmp_path):
    p = write_fasta(tmp_path / "x.fa", [("A", "MKV" * 40), ("B", "GG")])
    assert read_fasta(p) == [("A", "", "MKV" * 40), ("B", "", "GG")]


def test_sanitise_is_position_preserving_and_counted():
    s = sanitise("mkuxo*")
    assert s.sequence == "MKCAK" and len(s.sequence) == 5 and s.n_replaced == 3
    assert sanitise("MKV").n_replaced == 0


@pytest.mark.parametrize("n", [1, 999, 1000, 1001, 2500, 4321])
def test_chunks_cover_every_residue(n):
    seq = "A" * n
    chunks = chunk_sequence(seq, 1000, 100)
    covered = set()
    for off, sub in chunks:
        assert len(sub) <= 1000
        covered |= set(range(off, off + len(sub)))
    assert covered == set(range(n))


def test_long_protein_is_actually_chunked_beyond_1023():
    # README gotcha: BepiPred truncates > 1,023 aa; a 2,400-residue surface protein must be windowed.
    assert len(chunk_sequence("A" * 2400, 1000, 100)) >= 3


def test_merge_prefers_central_window_and_flags_gaps():
    # two windows over 6 residues: window 0 covers 0-3, window 1 covers 2-5. residues 2,3 overlap.
    merged = merge_chunk_scores(6, [(0, [1, 1, 1, 1]), (2, [2, 2, 2, 2])])
    assert merged == [1, 1, 1, 2, 2, 2]            # residue 2 is more central in w0 (dist 1 vs 0); residue 3 in w1
    assert merge_chunk_scores(5, [(0, [1, 1, 1])])[3:] == [None, None]


# ------------------------------------------------------------------------------- hla
@pytest.mark.parametrize("raw,canon", [
    ("HLA-A0201", "HLA-A*02:01"), ("HLA-A02:01", "HLA-A*02:01"), ("A*02:01:01:02", "HLA-A*02:01"),
    ("DRB1_0101", "HLA-DRB1*01:01"), ("HLA-DRB1*01:01", "HLA-DRB1*01:01"), ("DRB3_0202", "HLA-DRB3*02:02"),
    ("HLA-DQA10501-DQB10201", "HLA-DQA1*05:01/DQB1*02:01"), ("HLA-DQA1*05:01/DQB1*02:01", "HLA-DQA1*05:01/DQB1*02:01"),
    ("HLA-DPA10103-DPB10201", "HLA-DPA1*01:03/DPB1*02:01"), ("HLA-DQB1*02:01", "HLA-DQB1*02:01"),
])
def test_normalise_allele(raw, canon):
    assert hla.normalise_allele(raw) == canon


def test_tool_spellings_roundtrip():
    assert hla.to_netmhcpan("HLA-B*07:02") == "HLA-B07:02"
    assert hla.to_netmhciipan("HLA-DRB1*01:01") == "DRB1_0101"
    assert hla.to_netmhciipan("HLA-DQA1*05:01/DQB1*02:01") == "HLA-DQA10501-DQB10201"
    for a in hla.REFERENCE_PANEL_CLASS_I:
        assert hla.normalise_allele(hla.to_netmhcpan(a)) == a
    for a in hla.REFERENCE_PANEL_CLASS_II:
        assert hla.normalise_allele(hla.to_netmhciipan(a)) == a


def test_panels_and_classes():
    assert len(hla.REFERENCE_PANEL_CLASS_I) == 27 and len(hla.REFERENCE_PANEL_CLASS_II) == 26
    assert {hla.allele_class(a) for a in hla.REFERENCE_PANEL_CLASS_I} == {"I"}
    assert {hla.allele_class(a) for a in hla.REFERENCE_PANEL_CLASS_II} == {"II"}
    assert hla.allele_locus("HLA-DQA1*05:01/DQB1*02:01") == "HLA-DQB1"
    assert hla.beta_chain_allele("HLA-DQA1*05:01/DQB1*02:01") == "HLA-DQB1*02:01"
    with pytest.raises(ValueError):
        hla.resolve_panel(["HLA-A*02:01"], "II")


# ----------------------------------------------------------------------------- blast
def _hsp(q, s, ident, qs, qe, qlen=100, bits=50.0, qseq="", sseq=""):
    return blast.Hsp(q, s, ident, qe - qs + 1, qlen, 300, qs, qe, 1, qe - qs + 1, 1e-10, bits, qseq, sseq)


def test_parse_blast_tab_and_union_coverage():
    text = "q1\ts1\t80.0\t30\t100\t300\t1\t30\t5\t34\t1e-9\t60.0\tAAAA\tAAAA\nq1\ts1\t70.0\t30\t100\t300\t21\t50\t60\t89\t1e-5\t40.0\t\t\n"
    hs = blast.parse_blast_tab(text)
    assert len(hs) == 2
    summ = blast.summarise_by_subject(hs)["q1"]["s1"]
    assert summ.identity == pytest.approx(0.80) and summ.coverage == pytest.approx(0.50)   # union of 1-30 and 21-50
    assert summ.evalue == 1e-9


def test_shared_exact_kmers_respects_gaps_and_positions():
    h = _hsp("q", "s", 90, 11, 22, qseq="ACDEFGHIK-LMNP", sseq="ACDEFGHIKQLMNP")
    # query residues: A11 C12 D13 E14 F15 G16 H17 I18 K19 (gap) L20 M21 N22 P23 ... qend not important here
    k9 = blast.shared_exact_kmers([h], 9)
    assert k9 == {"ACDEFGHIK": [11]}                  # run broken by the query gap; no 9-mer spans it
    k4 = blast.shared_exact_kmers([h], 4)
    assert "LMNP" in k4 and k4["LMNP"] == [20]


def test_blast_runner_reports_unavailable_instead_of_passing(monkeypatch):
    monkeypatch.setattr(blast, "resolve_executable", lambda *a, **k: None)
    r = blast.BlastRunner("local")
    assert not r.available()
    with pytest.raises(Exception) as e:
        r.blastp("q.fa", "db")
    assert "unavailable" in str(e.value).lower()


# ---------------------------------------------------------------------------- netmhc
LOCAL_I = textwrap.dedent("""\
    # NetMHCpan version 4.1
    # Input is in FSA format
    ---------------------------------------------------------------------------------------------------------------------------
     Pos         MHC        Peptide      Core Of Gp Gl Ip Il        Icore        Identity     Score_EL %Rank_EL  Score_BA %Rank_BA  Aff(nM)  BindLevel
    ---------------------------------------------------------------------------------------------------------------------------
       0 HLA-A*02:01      SLLMWITQC    SLLMWITQC  0  0  0  0  0    SLLMWITQC        P00000    0.4561   0.310   0.5700   0.411   180.2   <= SB
       1 HLA-A*02:01      LLMWITQCF    LLMWITQCF  0  0  0  0  0    LLMWITQCF        P00000    0.0100   1.900   0.4000   3.100  1500.0   <= WB
       2 HLA-A*02:01      LMWITQCFL    LMWITQCFL  0  0  0  0  0    LMWITQCFL        P00000    0.0001  40.000   0.1000  50.000 30000.0
    Protein P00000. Allele HLA-A*02:01. Number of high binders 1. Number of weak binders 1. Number of peptides 3
    """)

WEB_I = textwrap.dedent("""\
     Pos HLA Peptide Core Of Gp Gl Ip Il Icore Identity Score_EL Rnk_EL BindLevel
     15 HLA-A*03:01 HQAAMQMLK HQAAMQMLK 0 0 0 0 0 HQAAMQMLK Gag_180_209 0.6594640 0.259 <= SB
    """)

LOCAL_II = textwrap.dedent("""\
     Pos         MHC        Peptide Of Core Core_Rel Inverted Identity Score_EL %Rank_EL Exp_Bind BindLevel
       3  DRB1_0101  AAAKLLWFFQQMNST  3 LLWFFQQMN 0.900 0 P00001 0.8000 0.700 NA <= SB
       4  HLA-DQA10501-DQB10201  AAKLLWFFQQMNSTV  2 KLLWFFQQM 0.800 0 P00001 0.1000 4.100 NA <= WB
    """)


def test_parse_netmhcpan_local_format():
    rows = parse_netmhc_output(LOCAL_I, {"P00000": "REALID"})
    assert [r.start for r in rows] == [1, 2, 3]                       # Pos is 0-based in the tool, 1-based here
    assert rows[0].protein_id == "REALID" and rows[0].allele == "HLA-A*02:01"
    assert rows[0].rank == pytest.approx(0.31) and rows[0].bind_level == "SB" and rows[1].bind_level == "WB"
    assert rows[2].bind_level is None and rows[0].end == 9


def test_parse_netmhcpan_web_header_variant():
    r = parse_netmhc_output(WEB_I)[0]
    assert (r.allele, r.rank, r.peptide, r.start) == ("HLA-A*03:01", 0.259, "HQAAMQMLK", 16)


def test_parse_netmhciipan_normalises_class_ii_names():
    rows = parse_netmhc_output(LOCAL_II, {"P00001": "X"})
    assert rows[0].allele == "HLA-DRB1*01:01" and rows[0].core == "LLWFFQQMN"
    assert rows[1].allele == "HLA-DQA1*05:01/DQB1*02:01" and rows[1].rank == pytest.approx(4.1)


def test_netmhc_runner_detects_silently_dropped_allele(tmp_path):
    script = tmp_path / "fake_netmhcpan.py"
    script.write_text("print('''" + LOCAL_I.replace("'''", "") + "''')\n")
    runner = NetMHCRunner("netmhcpan", ["python3", str(script)])
    rows = runner.predict({"X": "SLLMWITQCFL"}, ["HLA-A*02:01"], [9], max_rank=2.0)
    assert len(rows) == 2 and all(r.protein_id == "X" for r in rows)       # surrogate id mapped back, rank filter applied
    with pytest.raises(ToolFailed, match="no rows"):
        runner.predict({"X": "SLLMWITQCFL"}, ["HLA-A*02:01", "HLA-B*07:02"], [9])


# -------------------------------------------------------------------------- bepipred
def _raw_csv(entries):
    lines = [",".join(CSV_HEADER)]
    for acc, seq, scores in entries:
        lines += [f"{acc},{aa},{s}, {s}" for aa, s in zip(seq, scores)]
    return "\n".join(lines)


def test_bepipred_parse_and_truncation_detection():
    seq = "ACDEFGHIKL"
    ok = parse_raw_output(_raw_csv([("P00000c000", seq, [0.1] * 10)]))
    chunk_ids = {"X": [("P00000c000", 0, seq)]}
    res = assemble_scores({"X": seq}, ok, chunk_size=1000, chunk_overlap=100, chunk_ids=chunk_ids)
    assert len(res["X"].scores) == 10
    short = parse_raw_output(_raw_csv([("P00000c000", seq[:7], [0.1] * 7)]))      # tool silently wrote 7 rows
    with pytest.raises(ToolFailed, match="truncated"):
        assemble_scores({"X": seq}, short, chunk_size=1000, chunk_overlap=100, chunk_ids=chunk_ids)
    with pytest.raises(ToolFailed, match="header"):
        parse_raw_output("a,b,c\n1,2,3")


def test_bepipred_runner_chunks_long_sequences_via_fake_cli(tmp_path):
    cli = tmp_path / "bepipred3_CLI.py"
    cli.write_text(textwrap.dedent('''
        import argparse, pathlib
        ap = argparse.ArgumentParser(); ap.add_argument("-i"); ap.add_argument("-o"); ap.add_argument("-pred")
        ap.add_argument("-t"); ap.add_argument("-esm_dir"); a = ap.parse_args()
        out = pathlib.Path(a.o); out.mkdir(parents=True, exist_ok=True)
        acc, seq, rows = None, "", []
        def flush():
            for i, aa in enumerate(seq):
                rows.append(f"{acc},{aa},{(i % 10) / 10},{(i % 10) / 10}")
        for line in open(a.i):
            line = line.strip()
            if line.startswith(">"):
                if acc: flush()
                acc, seq = line[1:], ""
            else:
                seq += line
        flush()
        (out / "raw_output.csv").write_text("Accession,Residue,BepiPred-3.0 score,BepiPred-3.0 linear epitope score\\n" + "\\n".join(rows))
    '''))
    long_seq = ("ACDEFGHIKLMNPQRSTVWY" * 120)[:2300]
    runner = BepiPredRunner(["python3", str(cli)])
    res = runner.predict({"LONG": long_seq, "SHORT": "ACDEFGHIKL"}, chunk_size=1000, chunk_overlap=100)
    assert len(res["LONG"].scores) == 2300 and res["LONG"].n_chunks == 3       # nothing lost from the C-terminus
    assert len(res["SHORT"].scores) == 10 and res["SHORT"].n_chunks == 1


def test_call_linear_epitopes_merge_and_min_len():
    scores = [0.0] * 3 + [0.5] * 4 + [0.0] + [0.5] * 3 + [0.0] * 5 + [0.5] * 2 + [0.0] * 3
    seq = "A" * len(scores)
    regs = call_linear_epitopes(seq, scores, threshold=0.1512, min_len=5, merge_gap=1)
    assert [(r.start, r.end) for r in regs] == [(4, 11)]                       # 4+gap+3 merged; trailing 2-run dropped
    assert call_linear_epitopes(seq, scores, threshold=0.1512, min_len=5, merge_gap=0) == []
    assert top_fraction_mean([0.1, 0.2, 0.3, 0.4, 0.5], 0.2) == pytest.approx(0.5)


# -------------------------------------------------------------------------- discotope
def test_discotope_parse_and_summarise():
    csv_text = "pdb,res_id,residue,DiscoTope-3.0_score,rsa,pLDDTs,length,alphafold_struc_flag\n" + \
        "\n".join(f"x_A,{i},A,{0.95 if 3 <= i <= 5 else 0.1},0.5,90,10,1" for i in range(1, 11))
    res = parse_discotope_csv(csv_text)
    s = summarise(res, 0.90)
    assert s["epitope_segments"] == [(3, 5)] and s["n_epitope_residues"] == 3 and s["threshold_source"] == "score>=0.9"
    with_flag = csv_text.replace("alphafold_struc_flag", "epitope").replace(",1\n", ",False\n")
    assert summarise(parse_discotope_csv(with_flag), 0.9)["threshold_source"] == "tool_boolean"


def test_discotope_runner_passes_models_dir_and_cwd_because_the_tool_default_is_relative(tmp_path):
    # RUN for real: without --models_dir the tool dies with "Path models/ does not exist!" outside its checkout
    root = tmp_path / "DiscoTope-3.0"
    (root / "discotope3").mkdir(parents=True)
    (root / "models").mkdir()
    script = root / "discotope3" / "main.py"
    script.write_text(textwrap.dedent('''
        import argparse, pathlib, sys
        ap = argparse.ArgumentParser(); ap.add_argument("--pdb_or_zip_file"); ap.add_argument("--struc_type"); ap.add_argument("--out_dir")
        ap.add_argument("--models_dir", default="models/"); a = ap.parse_args()
        if not pathlib.Path(a.models_dir).exists():
            ap.error(f"Path {a.models_dir} does not exist!")
        out = pathlib.Path(a.out_dir) / "X"; out.mkdir(parents=True)
        (out / "X_A_discotope3.csv").write_text("pdb,chain,res_id,residue,DiscoTope-3.0_score,calibrated_score,epitope,rsa,pLDDTs,length,alphafold_struc_flag\\n"
                                                "X_A,A,1,M,0.2,0.5,True,1.3,38.2,1,1\\n")
    '''))
    pdb = tmp_path / "x.pdb"
    pdb.write_text("ATOM\n")
    res = __import__("vaxforge.prediction.discotope", fromlist=["x"]).DiscoTopeRunner(["python3", str(script)]).predict(pdb)
    assert len(res) == 1 and res[0].epitope is True and res[0].calibrated == 0.5


# ----------------------------------------------------------------------------- config
def test_config_rules_are_enforced():
    with pytest.raises(ConfigError, match="bepipred-3.0"):
        load_config(overrides={"epitopes": {"bcell_tool": "bepipred-2.0"}})
    with pytest.raises(ConfigError, match="boltz-2"):
        load_config(overrides={"structure": {"engine": "alphafold3"}})
    with pytest.raises(ConfigError, match="two thresholds"):
        load_config(overrides={"thresholds": {"protegenicity": [0.9]}})
    assert load_config()["thresholds"]["protegenicity"] == [0.90, 0.85]


# ------------------------------------------------------------------------------ schema
def test_localisation_unknown_is_explicit_not_collapsed():
    assert normalise_localisation("unknown") == ("Unknown", True)
    assert normalise_localisation(None) == ("Unknown", True)
    assert normalise_localisation("Outer Membrane") == ("OuterMembrane", True)
    assert normalise_localisation("weird label") == ("Unknown", False)


def test_s2_loader_is_tolerant_and_precise_about_errors(tmp_path):
    doc = [{"accession": "P1", "seq": "mkv*", "psortb": "Extracellular", "vfdb": {"pident": 55, "qcov": 90, "evalue": 1e-8}},
           {"id": "P2", "sequence": "GGG", "localization": {"prediction": "Cytoplasmic", "score": 8.1}}]
    p = tmp_path / "s2.json"
    p.write_text(json.dumps(doc))
    d = load_s2_annotations(p)
    p1 = d.by_id()["P1"]
    assert p1.sequence == "MKV" and p1.localisation.label == "Extracellular"
    assert p1.virulence.identity == pytest.approx(0.55) and p1.virulence.coverage == pytest.approx(0.90)
    p.write_text(json.dumps([{"protein_id": "P1"}, {"protein_id": "P1", "sequence": "A"}]))
    with pytest.raises(ContractError, match="needs a protein id"):
        load_s2_annotations(p)


# ------------------------------------------------------------------------------ refdata
def test_refdata_is_cache_first_and_gunzips(tmp_path):
    calls = []

    def fetch(url):
        calls.append(url)
        return Fetched(gzip.compress(b">sp|P1|X_HUMAN x\nMKV\n"), {"x-uniprot-release": "2026_04"})

    rd = RefData(tmp_path, fetch=fetch)
    p1 = rd.human_proteome()
    assert p1.read_text().startswith(">sp|P1") and len(calls) == 1
    assert "reviewed%3Atrue" in calls[0] or "reviewed:true" in calls[0]
    p2 = rd.human_proteome()
    assert p2 == p1 and len(calls) == 1                         # served from cache
    meta = json.loads(p1.with_suffix(p1.suffix + ".meta.json").read_text())
    assert meta["uniprot_release"] == "2026_04" and rd.provenance


def test_refdata_offline_refuses_network(tmp_path):
    from vaxforge.prediction.errors import ToolUnavailable
    with pytest.raises(ToolUnavailable, match="offline"):
        RefData(tmp_path, offline=True).human_proteome()


def test_species_proteomes_excludes_target_and_is_deterministic(tmp_path):
    tsv = ("Proteome Id\tOrganism\tOrganism Id\tProtein count\n"
           "UP1\tstrain A\t11\t1900\nUP2\tstrain B\t12\t2000\nUP3\tstrain C\t13\t2000\n")
    rd = RefData(tmp_path, fetch=lambda u: Fetched(tsv.encode(), {}))
    refs = rd.species_proteomes(487, exclude_upids=("UP3",), limit=2)
    assert [r.upid for r in refs] == ["UP2", "UP1"]


def test_load_peptide_set_plain_and_iedb_csv(tmp_path):
    plain = tmp_path / "lig.txt"
    plain.write_text("SIINFEKL\nGILGFVFTL\nnot a peptide 1\n")
    assert load_peptide_set(plain) == {"SIINFEKL", "GILGFVFTL"}
    csv_text = ("Reference,Epitope,Epitope,Host,Assay\nIEDB IRI,IEDB IRI,Name,Name,Qualitative Measure\n"
                "r1,e1,AAAAAAAAA,Homo sapiens (human),Positive\n"
                "r2,e2,CCCCCCCCC,Mus musculus (mouse),Positive\n"
                "r3,e3,DDDDDDDDD,Homo sapiens (human),Negative\n")
    p = tmp_path / "mhc_ligand_full.csv"
    p.write_text(csv_text)
    assert load_peptide_set(p) == {"AAAAAAAAA"}


def test_config_snippet_for_the_merge_matches_the_built_in_defaults():
    """docs/config.stream_b.yaml is pasted into the team config.yaml; it must equal DEFAULTS (minus the keys shared with Stream A)."""
    import importlib.util
    from pathlib import Path
    import yaml
    from vaxforge.prediction.config import DEFAULTS, deep_merge
    root = Path(__file__).resolve().parents[2]
    spec = importlib.util.spec_from_file_location("mk", root / "scripts" / "stream_b_make_config_snippet.py")
    mk = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mk)
    on_disk = (root / "docs" / "config.stream_b.yaml").read_text()
    assert on_disk == mk.render(), "docs/config.stream_b.yaml is stale: run python scripts/stream_b_make_config_snippet.py"
    merged = deep_merge({"refdata": {"cache_dir": "cache", "offline": False}, "tools": {"docker": "docker"}}, yaml.safe_load(on_disk))
    for k in ("refdata", "tools", "s3", "s4", "s5", "s6", "s7"):
        assert merged[k] == DEFAULTS[k], k
