"""Config reader, cache, HTTP client, FASTA helpers and the artifact contract."""
from __future__ import annotations

import json

import pytest

from vaxforge import artifacts as A
from vaxforge.cache import Cache, CacheMiss, atomic_write_bytes, sha256_file
from vaxforge.config import Config, ConfigError, load_config, mini_yaml_load
from vaxforge.fasta import FastaError, FastaRecord, parse_uniprot_header, read_fasta, validate_protein_fasta, write_fasta
from vaxforge.http import HttpClient, HttpError, OfflineError
from vaxforge.testing import FakeTransport

from conftest import ROOT


# ------------------------------------------------------------------ config
def test_mini_yaml_matches_expectations():
    text = """
# comment
a: 1
b:
  c: [0.9, 0.85]   # trailing
  d: "x # not a comment"
  e: null
  f: {}
  g:
    - one
    - 2
h: true
i: 1.0e-5
"""
    d = mini_yaml_load(text)
    assert d == {"a": 1, "b": {"c": [0.9, 0.85], "d": "x # not a comment", "e": None, "f": {}, "g": ["one", 2]},
                 "h": True, "i": 1.0e-5}


def test_repo_config_yaml_parses_with_all_streams_sections():
    cfg = load_config(ROOT / "config.yaml")
    for sec in ("thresholds", "epitopes", "structure", "finetune", "reproducibility", "paths", "network", "taxonomy",
                "acquire", "annotate", "tools"):
        assert cfg.section(sec), sec
    assert cfg.get("thresholds.protegenicity") == [0.9, 0.85]
    assert cfg.get("annotate.vfdb.max_evalue") == pytest.approx(1e-5)
    assert cfg.get("reproducibility.seed") == 20261114
    # stream D's S9.5 block (README section 5): numbers stay numbers, the two unpinned model ids stay None (not the string "null")
    assert cfg.get("finetune.eval_tasks") == 11 and cfg.get("finetune.seed") == 20261114
    assert cfg.raw["finetune"]["base_model_id"] is None and cfg.raw["finetune"]["model_id"] is None
    assert cfg.get("nope.nope", "dflt") == "dflt"


def test_config_digest_ignores_paths_network_and_secrets():
    a = Config({"thresholds": {"x": 1}, "network": {"ncbi": {"api_key": "SECRET"}}, "paths": {"cache_dir": "/a"}})
    b = Config({"thresholds": {"x": 1}, "network": {"ncbi": {"api_key": "OTHER"}}, "paths": {"cache_dir": "/b"}})
    c = Config({"thresholds": {"x": 2}})
    assert a.digest() == b.digest() != c.digest()
    assert "SECRET" not in json.dumps(a.redacted())


def test_both_yaml_readers_agree_on_the_repo_config():
    """Teammates will have PyYAML; this machine did not. The two readers must produce the same configuration."""
    yaml = pytest.importorskip("yaml")
    text = (ROOT / "config.yaml").read_text(encoding="utf-8")
    assert mini_yaml_load(text) == yaml.safe_load(text)


def test_missing_explicit_config_raises(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.yaml")


# ------------------------------------------------------------------ cache + http
def test_atomic_write_and_hash(tmp_path):
    p = tmp_path / "sub" / "f.bin"
    atomic_write_bytes(p, b"hello")
    assert p.read_bytes() == b"hello"
    assert sha256_file(p) == "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
    assert not list(p.parent.glob("*.tmp"))


def test_cache_offline_miss_and_hit(tmp_path):
    c = Cache(tmp_path, offline=True)
    p = c.path("a", "b.txt")
    with pytest.raises(CacheMiss):
        c.require(p, "thing")
    c.put_bytes(p, b"x", {"url": "u"})
    assert c.usable(p) and c.read_meta(p)["url"] == "u"


def test_http_retries_then_succeeds_and_rate_limits():
    calls = {"n": 0}

    def flaky(url, headers, timeout):
        calls["n"] += 1
        if calls["n"] < 3:
            raise HttpError(503, url)
        return FakeTransport([("", 200, {}, b"ok")])(url, headers, timeout)

    slept: list[float] = []
    client = HttpClient(transport=flaky, retries=3, backoff=1.0, sleep=slept.append)
    assert client.get("https://example.org/x").body == b"ok"
    assert calls["n"] == 3 and slept == [1.0, 2.0]


def test_http_404_is_not_retried_and_offline_refuses():
    t = FakeTransport()
    client = HttpClient(transport=t, retries=3, sleep=lambda s: None)
    with pytest.raises(HttpError) as ei:
        client.get("https://example.org/missing")
    assert ei.value.status == 404 and len(t.calls) == 1
    with pytest.raises(OfflineError):
        HttpClient(offline=True).get("https://example.org/x")


def test_download_streams_and_detects_truncation(tmp_path):
    t = FakeTransport([("/ok", 200, {"content-length": "5"}, b"hello"), ("/bad", 200, {"content-length": "9"}, b"hello")])
    client = HttpClient(transport=t, sleep=lambda s: None)
    res = client.download("https://x/ok", tmp_path / "ok.bin")
    assert res.bytes == 5 and (tmp_path / "ok.bin").read_bytes() == b"hello"
    with pytest.raises(HttpError):
        client.download("https://x/bad", tmp_path / "bad.bin")
    assert not (tmp_path / "bad.bin").exists() and not list(tmp_path.glob("*.part"))


# ------------------------------------------------------------------ fasta
UNIPROT_HDR = "sp|Q9K0U9|FHBP_NEIMB Factor H-binding protein OS=Neisseria meningitidis serogroup B (strain ATCC BAA-335 / MC58) OX=122586 GN=fHbp PE=1 SV=1"


def test_parse_uniprot_header():
    h = parse_uniprot_header(UNIPROT_HDR)
    assert h["accession"] == "Q9K0U9" and h["entry_name"] == "FHBP_NEIMB" and h["gene"] == "fHbp"
    assert h["taxid"] == 122586 and h["reviewed"] is True and h["description"] == "Factor H-binding protein"
    assert parse_uniprot_header("custom1 some protein")["accession"] == "custom1"


def test_fasta_roundtrip_and_validation(tmp_path):
    p = tmp_path / "x.faa"
    write_fasta([FastaRecord("a desc", "MKT" * 40), FastaRecord("b", "MAAA")], p)
    recs = list(read_fasta(p))
    assert [r.id for r in recs] == ["a", "b"] and recs[0].seq == "MKT" * 40 and recs[0].description == "desc"
    assert validate_protein_fasta(p)["records"] == 2
    write_fasta([FastaRecord("a", "MK"), FastaRecord("a", "MK")], tmp_path / "dup.faa")
    with pytest.raises(FastaError, match="duplicate"):
        validate_protein_fasta(tmp_path / "dup.faa")
    write_fasta([FastaRecord("n1", "ACGTACGTAC" * 3 + "N" * 5 + "U" * 2 + "-")], tmp_path / "nt.faa")
    with pytest.raises(FastaError, match="non-protein"):
        validate_protein_fasta(tmp_path / "nt.faa")


# ------------------------------------------------------------------ artifact contract
def _s0_payload():
    return {"query": "q", "resolution": {}, "taxon": {}, "organism": {}, "candidates": []}


def test_artifact_roundtrip_digest_and_tamper_detection(store):
    a = store.write_artifact("s0", _s0_payload(), params={"p": 1}, tools={"t": "1"})
    assert a.digest.startswith("sha256:") and store.manifest_path.is_file()
    back = store.read_artifact("s0")
    assert back.payload["query"] == "q" and back.data["schema"] == "vaxforge/s0_taxonomy/v1"
    path = store.artifact_path("s0")
    path.write_text(path.read_text(encoding="utf-8").replace('"query": "q"', '"query": "tampered"'), encoding="utf-8")
    with pytest.raises(A.ContractError, match="digest mismatch"):
        store.read_artifact("s0")


def test_digest_is_independent_of_timestamp_and_run_id(tmp_path):
    s1 = A.RunStore(tmp_path / "r1")
    s2 = A.RunStore(tmp_path / "r2")
    a = s1.write_artifact("s0", _s0_payload(), params={"p": 1})
    b = s2.write_artifact("s0", _s0_payload(), params={"p": 1})
    assert a.digest == b.digest and a.data["run_id"] != b.data["run_id"]
    c = s2.write_artifact("s0", {**_s0_payload(), "query": "other"}, params={"p": 1})
    assert c.digest != a.digest


def test_missing_required_keys_and_bad_status_and_nan(store):
    with pytest.raises(A.ContractError, match="missing required keys"):
        store.write_artifact("s0", {"query": "q"})
    with pytest.raises(A.ContractError, match="status"):
        store.write_artifact("s0", _s0_payload(), status="weird")
    with pytest.raises(A.ContractError, match="NaN"):
        store.write_artifact("s0", {**_s0_payload(), "x": float("nan")})
    # non-ok statuses skip the key check so "needs_user_input" artifacts can be written
    store.write_artifact("s0", {"query": "q"}, status="needs_user_input")


def test_chain_staleness_and_file_refs(store, tmp_path):
    store.write_artifact("s0", _s0_payload())
    f = store.data_path("proteome.faa")
    f.write_text(">a\nMK\n", encoding="utf-8")
    s1_payload = {"taxid": 1, "proteome": {"file": store.run_ref(f)}, "comparison_proteome": {}, "reference_dbs": {},
                  "blast_dbs": {}, "resource_status": {}}
    store.write_artifact("s1", s1_payload)
    assert store.verify() == []
    # re-run upstream with different content -> downstream is stale
    store.write_artifact("s0", {**_s0_payload(), "query": "changed"})
    problems = store.verify()
    assert any("STALE" in p and p.startswith("s1") for p in problems)
    # corrupt a referenced file
    f.write_text(">a\nMKK\n", encoding="utf-8")
    assert any("hash mismatch" in p for p in store.verify())


def test_registry_matches_the_readme_stage_flow_including_s9_5(store):
    """README flow: S9 -> S9.5 -> S10 (stream D's fine-tuned codegen, added after the registry was first written)."""
    order = A.STAGE_ORDER
    assert order.index("s9") < order.index("s9_5") < order.index("s10")
    assert A.STAGES["s9_5"] == "s9_5_finetune" and A.DEFAULT_INPUTS["s9_5"] == ["s8", "s9"]
    store.write_artifact("s8", {"protocol": []}, inputs=[])
    store.write_artifact("s9", {"code": []}, inputs=["s8"])
    art = store.write_artifact("s9_5", {"win_rate": None}, inputs=["s8", "s9"])
    assert art.data["schema"] == "vaxforge/s9_5_finetune/v1" and set(art.inputs) == {"s8", "s9"}
    assert store.artifact_path("s9_5").name == "s9_5_finetune.json" and store.verify() == []


def test_writing_downstream_requires_upstream(store):
    with pytest.raises(A.ContractError, match="upstream artifact s0 is missing"):
        store.write_artifact("s1", {"taxid": 1, "proteome": {}, "comparison_proteome": {}, "reference_dbs": {},
                                    "blast_dbs": {}, "resource_status": {}})
