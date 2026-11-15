"""S8 order list for synthesis: `data/s8/order.fasta`, the format stream D's S12 gate reads (`check ORDER.fasta --run RUN_DIR`).

The file holds the PROTEIN sequence of every shortlisted candidate that has one (header = candidate id, protocol order); the vendor
back-translates. A candidate with no sequence is listed in `payload.order_fasta_missing` with the reason and is never given one.
Every test works on a copy of fixtures/nmb_fixture under `tmp_path`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from b_tools_guard import needs_toolless_machine

from vaxforge import s8_protocol, s12_biosafety
from vaxforge.artifacts import RunStore
from vaxforge.config import Config
from vaxforge.fasta import PROTEIN_ALPHABET, read_fasta
from vaxforge.s8_protocol import (
    GENE_SYNTHESIS_GATE,
    MISSING_SEQUENCE_REASON,
    ORDER_FASTA_REL,
    S12_COMMAND,
    Candidate,
    StepKind,
    build_protocol,
    order_list,
)

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
FHBP, NADA, NHBA = "sp|Q9JXV4|FHBP_NEIMB", "sp|Q9JXK7|NADA1_NEIMB", "sp|Q7DD37|NHBA_NEIMB"
SEQ = {r.id: r.seq for r in read_fasta(FIXTURE / "data" / "proteome.faa")}


def copy_run(dest: Path) -> RunStore:
    shutil.copytree(FIXTURE, dest)
    return RunStore(dest, cache_dir=dest.parent / "cache")


def cfg(**streamc) -> Config:
    return Config(dict(streamc=streamc))


def expected_fasta(ids) -> bytes:
    """The order list written out independently of the code under test: `>id`, then the sequence in lines of 60, LF only."""
    lines: list[str] = []
    for cid in ids:
        lines.append(">" + cid)
        lines += [SEQ[cid][i:i + 60] for i in range(0, len(SEQ[cid]), 60)]
    return ("\n".join(lines) + "\n").encode("utf-8")


@pytest.fixture
def store(tmp_path):
    return copy_run(tmp_path / "run")


# =========================================================================== content, hash, bytes


def test_the_stage_writes_the_protein_order_list_in_protocol_order_with_a_file_ref(store):
    art = s8_protocol.run(store, cfg(proteins=["fhbp", "nada", "nhba"], top_k=3))
    ref = art.payload["order_fasta"]
    ids = [c["candidate_id"] for c in art.payload["candidates"]]
    assert ids == [FHBP, NADA, NHBA]
    data = (store.run_dir / "data" / "s8" / "order.fasta").read_bytes()
    assert data == expected_fasta(ids)
    assert ref == {"root": "run", "path": ORDER_FASTA_REL, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data), "records": 3}
    assert ref["path"] == "data/s8/order.fasta" and art.payload["order_fasta_missing"] == []
    records = list(read_fasta(store.resolve(ref)))
    assert [r.id for r in records] == ids and [r.header for r in records] == ids  # header = the candidate id, nothing else
    for r in records:
        assert r.seq == SEQ[r.id] and set(r.seq) <= PROTEIN_ALPHABET  # protein, not DNA; the real sequence, not a made-up one


def test_the_ref_resolves_verifies_and_a_tampered_file_is_caught(store):
    art = s8_protocol.run(store, cfg(proteins=["fhbp"], top_k=1))
    assert store.verify_ref(art.payload["order_fasta"]) is None and store.verify() == []
    path = store.run_dir / "data" / "s8" / "order.fasta"
    path.write_bytes(path.read_bytes() + b">EXTRA\nMKV\n")
    assert any("hash mismatch" in p for p in store.verify())


def test_the_bytes_are_deterministic_and_use_lf_only(tmp_path):
    a, b = copy_run(tmp_path / "a"), copy_run(tmp_path / "b")
    c = cfg(proteins=["fhbp", "nada"], top_k=2)
    art_a, art_b = s8_protocol.run(a, c), s8_protocol.run(b, c)
    raw_a = (a.run_dir / "data" / "s8" / "order.fasta").read_bytes()
    assert raw_a == (b.run_dir / "data" / "s8" / "order.fasta").read_bytes()
    assert b"\r" not in raw_a and raw_a.endswith(b"\n") and not raw_a.endswith(b"\n\n")
    assert art_a.payload["order_fasta"] == art_b.payload["order_fasta"] and art_a.digest == art_b.digest
    assert s8_protocol.run(a, c).digest == art_a.digest  # a re-run changes nothing


def test_no_path_or_timestamp_enters_the_hashed_order_fields(store):
    art = s8_protocol.run(store, cfg(proteins=["fhbp"], top_k=1))
    text = repr([art.payload["order_fasta"], art.payload["order_fasta_missing"], art.warnings])
    assert str(store.run_dir) not in text and "Users" not in text and not re.search(r"\d{4}-\d\d-\d\dT", text)


# =========================================================================== a candidate without a sequence is listed, never invented


def _without_sequence(monkeypatch, *positions):
    """S8 over the real fixture, but the candidates at `positions` carry no sequence (as an S7 record without one would)."""
    real = s8_protocol.load_inputs_from_store

    def load(*args, **kwargs):
        inputs = real(*args, **kwargs)
        for i in positions:
            inputs.candidates[i] = inputs.candidates[i].model_copy(update={"sequence": None})
        return inputs

    monkeypatch.setattr(s8_protocol, "load_inputs_from_store", load)


def test_a_candidate_without_a_sequence_is_listed_with_the_reason_and_left_out(store, monkeypatch):
    _without_sequence(monkeypatch, 1)
    art = s8_protocol.run(store, cfg(proteins=["fhbp", "nada", "nhba"], top_k=3))
    assert art.status == "ok"
    assert art.payload["order_fasta_missing"] == [{"candidate_id": NADA, "reason": MISSING_SEQUENCE_REASON}]
    assert (store.run_dir / "data" / "s8" / "order.fasta").read_bytes() == expected_fasta([FHBP, NHBA])
    assert art.payload["order_fasta"]["records"] == 2
    assert [c["candidate_id"] for c in art.payload["candidates"]] == [FHBP, NADA, NHBA]  # the protocol itself is still written
    assert any(NADA in w and "order list" in w for w in art.warnings)
    assert "invent" in MISSING_SEQUENCE_REASON


def test_when_no_candidate_has_a_sequence_no_file_is_written_and_the_ref_is_none(store, monkeypatch):
    _without_sequence(monkeypatch, 0, 1)
    art = s8_protocol.run(store, cfg(proteins=["fhbp", "nada"], top_k=2))
    assert art.status == "ok" and art.payload["order_fasta"] is None
    assert [m["candidate_id"] for m in art.payload["order_fasta_missing"]] == [FHBP, NADA]
    assert not (store.run_dir / "data" / "s8").exists()  # an empty FASTA is not written: S12 refuses an order list with no sequences
    assert any("order list not written" in w for w in art.warnings) and store.verify() == []


def test_order_list_unit_leaves_out_missing_sequences_bad_ids_and_repeats_without_inventing():
    cands = [
        Candidate(candidate_id="A1", protein_name="a", rank=1, sequence="MKV LAA\nGG"),  # whitespace inside a sequence is dropped
        Candidate(candidate_id="B2", protein_name="b", rank=2, sequence=None),
        Candidate(candidate_id="C3", protein_name="c", rank=3, sequence=""),
        Candidate(candidate_id="has space", protein_name="d", rank=4, sequence="MKV"),
        Candidate(candidate_id="A1", protein_name="e", rank=5, sequence="MKV"),
        Candidate(candidate_id="E5", protein_name="f", rank=6, sequence="mkv"),  # case is left as it is: S12 upper-cases when it digests
    ]
    records, missing = order_list(cands)
    assert [(r.header, r.seq) for r in records] == [("A1", "MKVLAAGG"), ("E5", "mkv")]
    assert [m["candidate_id"] for m in missing] == ["B2", "C3", "has space", "A1"]
    assert missing[0]["reason"] == missing[1]["reason"] == MISSING_SEQUENCE_REASON
    assert "whitespace" in missing[2]["reason"] and "unique" in missing[3]["reason"]


# =========================================================================== a re-run replaces the list, and "nothing to do" leaves none behind


def test_a_rerun_replaces_the_order_list_and_a_not_run_stage_removes_it(store):
    s8_protocol.run(store, cfg(proteins=["fhbp", "nada", "nhba"], top_k=3))
    path = store.run_dir / "data" / "s8" / "order.fasta"
    assert len(list(read_fasta(path))) == 3
    art = s8_protocol.run(store, cfg(proteins=["nada"], top_k=1))
    assert [r.id for r in read_fasta(path)] == [NADA] and art.payload["order_fasta"]["records"] == 1
    gone = s8_protocol.run(store, cfg())  # nothing named: S8 does not run, and the old list must not survive its artifact
    assert gone.status == "ok" and gone.payload["status"] == "not_run"
    assert gone.payload["order_fasta"] is None and gone.payload["order_fasta_missing"] == [] and gone.payload["candidates"] == []
    assert not path.exists() and store.verify() == []


# =========================================================================== the S12 command in the gene-synthesis gate


def test_the_gene_synthesis_gate_names_the_exact_s12_command():
    gate = next(s for s in build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1)).steps if s.kind is StepKind.GENE_SYNTHESIS).gate
    assert gate == GENE_SYNTHESIS_GATE and f"`{S12_COMMAND}`" in gate
    assert gate.startswith("Sequence cleared by S12 biosecurity screening before ordering.")
    assert S12_COMMAND == "python -m vaxforge.s12_biosafety check RUN_DIR/data/s8/order.fasta --run RUN_DIR --screening PROVIDER_RECORD.json"
    assert "exit code 0 (cleared)" in gate and "2 = blocked, 3 = not screened" in gate


def test_the_stage_carries_that_gate_into_the_artifact(store):
    art = s8_protocol.run(store, cfg(proteins=["fhbp"], top_k=1))
    steps = art.payload["candidates"][0]["protocol"]["steps"]
    assert next(s for s in steps if s["kind"] == "gene_synthesis")["gate"] == GENE_SYNTHESIS_GATE


# =========================================================================== the real S12, through its own CLI, on a real chain
#
# B's real stages (no tool, no network: the chain is degraded but valid), then OUR S8, then `python -m vaxforge.s12_biosafety check ...`.
# `vaxforge biosafety check` is not registered in cli.EXTENSION_MODULES yet, so the module form (the one S8's gate text names) is the one tested.

# `needs_toolless_machine` (b_tools_guard.py, shared with test_streamc_upstream.py) skips only when a tool is USABLE: a `docker` executable
# whose daemon is stopped does not count.


def _module(name: str, *args) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "src")
    return subprocess.run([sys.executable, "-m", name, *map(str, args)], capture_output=True, text=True, cwd=ROOT, env=env, timeout=300)


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    """B's S3-S7 on a copy of the fixture, then S8 on it (S7 route). Tests copy `run_dir` before they let S12 write into it."""
    base = tmp_path_factory.mktemp("s12chain")
    st = copy_run(base / "run")
    for module, extra in (("s3_rank", ()), ("s4_filter", ()), ("s5_epitopes", ()), ("s6_coverage", ()), ("s7_structure", ("--set", "s7.top_k=4"))):
        p = _module(f"vaxforge.{module}", "--run-dir", st.run_dir, "--set", "refdata.offline=true",
                    "--set", f"refdata.cache_dir={json.dumps((base / 'cache').as_posix())}", *extra)
        assert p.returncode == 0, f"{module}: {p.stdout}{p.stderr}"
    art = s8_protocol.run(RunStore(st.run_dir, cache_dir=base / "cache"), cfg(top_k=3))
    assert art.status == "ok" and art.payload["selection"]["source"] == "s7_final_ranking"
    return SimpleNamespace(run_dir=st.run_dir, s8=art, base=base)


def _s12_on_a_copy(chain, name: str, *extra_args, order: bytes | None = None) -> tuple[subprocess.CompletedProcess, dict | None, Path]:
    """Copy the run, optionally replace the order list, run the documented S12 command on it: (process, s12 payload or None, the copy)."""
    run = chain.base / name
    shutil.copytree(chain.run_dir, run)
    fasta = run / "data" / "s8" / "order.fasta"
    if order is not None:
        fasta.write_bytes(order)
    p = _module("vaxforge.s12_biosafety", "check", fasta, "--run", run, *extra_args)
    art = run / "s12_biosafety.json"
    return p, (json.loads(art.read_text(encoding="utf-8"))["payload"] if art.is_file() else None), run


@needs_toolless_machine
def test_s12_reads_the_order_list_and_matches_every_ordered_sequence_to_an_s4_survivor(chain):
    ids = [c["candidate_id"] for c in chain.s8.payload["candidates"]]
    ref = chain.s8.payload["order_fasta"]
    p, s12, _ = _s12_on_a_copy(chain, "plain")
    assert p.returncode == s12_biosafety.EXIT[s12["state"]], p.stdout + p.stderr  # the CLI exits by the state, not with a read error
    assert s12["state"] in ("blocked", "not_screened")  # never cleared: no provider screening record was supplied
    assert s12["order"]["status"] == "supplied" and s12["order"]["n_sequences"] == len(ids) == ref["records"]
    assert s12["order"]["file"]["sha256"] == ref["sha256"] and s12["order"]["file"]["bytes"] == ref["bytes"]  # S12 hashed the file S8 referenced
    assert [r["id"] for r in s12["sequences"]] == ids
    for row, cand in zip(s12["sequences"], chain.s8.payload["candidates"]):
        assert row["local_checks"]["selected_candidate"]["result"] == "pass", row  # an S4 survivor: the sequence is the one S4 holds
        assert row["local_checks"]["sequence_sanity"]["result"] == "pass" and row["length"] == cand["length_aa"]
        assert row["digest"] == "sha256:" + cand["sequence_sha256"] == s12_biosafety.sequence_digest(SEQ[row["id"]])  # S8's hash IS S12's digest
        assert row["provider_verdict"] == "not_screened"


@needs_toolless_machine
def test_s12_says_so_when_an_ordered_sequence_is_not_an_s4_survivor(chain):
    raw = (chain.run_dir / "data" / "s8" / "order.fasta").read_bytes().decode("utf-8")
    lines = raw.split("\n")
    first_residues = lines[1]
    lines[1] = ("A" if first_residues[0] != "A" else "G") + first_residues[1:]  # one residue changed in the first record
    p, s12, _ = _s12_on_a_copy(chain, "altered", order="\n".join(lines).encode("utf-8"))
    assert p.returncode == 2 and s12["state"] == "blocked"
    first, others = s12["sequences"][0], s12["sequences"][1:]
    assert first["local_checks"]["selected_candidate"] == {"result": "fail", "detail": "no S4 candidate has this sequence"}
    assert all(r["local_checks"]["selected_candidate"]["result"] == "pass" for r in others)
    assert any("no S4 candidate has this sequence" in r for r in s12["reasons"])


@needs_toolless_machine
def test_the_command_named_in_the_gate_is_the_one_s12_accepts_including_the_screening_record(chain):
    gate = next(s for s in chain.s8.payload["candidates"][0]["protocol"]["steps"] if s["kind"] == "gene_synthesis")["gate"]
    command = re.search(r"`([^`]+)`", gate).group(1)
    assert command == S12_COMMAND
    argv = shlex.split(command)
    assert argv[:4] == ["python", "-m", "vaxforge.s12_biosafety", "check"] and argv[4].startswith("RUN_DIR/") and argv[5] == "--run"
    run = chain.base / "documented"
    shutil.copytree(chain.run_dir, run)
    record = chain.base / "provider_record.json"  # the format s12_biosafety documents; a test stand-in, not a real screening
    record.write_text(json.dumps({"provider": "TEST", "tool": "test-stand-in", "tool_version": "0", "date": "2000-01-01", "results": [
        {"digest": s12_biosafety.sequence_digest(SEQ[c["candidate_id"]]), "verdict": "pass"} for c in chain.s8.payload["candidates"]]}), encoding="utf-8")
    substituted = [a.replace("RUN_DIR", str(run)).replace("PROVIDER_RECORD.json", str(record)) for a in argv[3:]]  # "check", the list, --run, ...
    p = _module(argv[2], *substituted)  # `python -m <module>`: run with this interpreter
    s12 = json.loads((run / "s12_biosafety.json").read_text(encoding="utf-8"))["payload"]
    assert p.returncode == s12_biosafety.EXIT[s12["state"]], p.stdout + p.stderr
    assert s12["provider_record"]["status"] == "supplied" and s12["provider_record"]["covers_every_ordered_sequence"] is True
    assert all(r["provider_verdict"] == "pass" for r in s12["sequences"])  # the record was read and matched on S8's own digests


def test_s12_cannot_check_a_stand_in_order_list_and_says_why(tmp_path):
    """No S3-S7 means no S4 survivors: the S2 stand-in's order list has nothing to be matched against, and S12 refuses in words."""
    st = copy_run(tmp_path / "run")
    art = s8_protocol.run(st, cfg(proteins=["fhbp", "nada"], top_k=2))
    assert art.payload["order_fasta"]["records"] == 2 and art.payload["selection"]["source"] == "s2_stand_in"
    p = _module("vaxforge.s12_biosafety", "check", st.run_dir / "data" / "s8" / "order.fasta", "--run", st.run_dir)
    assert p.returncode == 1 and "s4 artifact not found" in p.stderr and "run that stage first" in p.stderr
    assert not (st.run_dir / "s12_biosafety.json").exists()
