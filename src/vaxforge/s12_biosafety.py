"""S12: the biosecurity gate (README S12 and S8).

Any sequence that would be sent to a synthesis provider passes through this node first. It reads an order list
(a protein FASTA the user supplies) and the run's S2 and S4 artifacts, and writes ``s12_biosafety.json``. It makes
no network call, contacts no provider and imports no other stream's module.

What is computed here (local checks, per ordered sequence, each reported on its own):

  selected_candidate  the sequence's digest equals the digest of a candidate in S4's ``candidates`` (the survivors),
                      so nothing arbitrary leaves through this gate
  sequence_sanity     the 20 standard amino acids only, and 1 to MAX_LENGTH residues (MAX_LENGTH is a sanity
                      bound chosen here, not a provider or regulatory limit)
  virulence_flag      S2's ``virulence.is_virulence_associated`` for the matched candidate is not true
  toxin_flag          S4's ``gates.toxicity.status`` for the matched candidate is ``pass``

A check that could not be evaluated (flag not computed, protein absent, no matching candidate) is reported as
``not_evaluated`` and counts against the sequence. ``not_applicable`` (S2: the virulence database does not apply
to the organism) does not.

THESE LOCAL CHECKS ARE NOT A SYNTHESIS-SCREENING RESULT AND NEVER SUBSTITUTE FOR ONE. They do not look at what a
sequence is, only at whether the pipeline selected it and recorded no flag for it. Screening is done by a
synthesis provider or a screening tool outside this repository. The user supplies its outcome as a JSON file:

  {"provider": "...", "tool": "...", "tool_version": "...", "date": "...",
   "results": [{"digest": "sha256:<hex>", "verdict": "pass" | "flag" | "error"}, ...]}

``digest`` is ``sequence_digest()`` of the exact sequence that was screened (the artifact lists each ordered
sequence's digest). This module checks that the file is well formed and covers every ordered digest and records
its sha256. It cannot verify that the screening took place or that the file is authentic; it says so in the artifact.

Gate state (``payload.state``):

  blocked       a local check failed or was not evaluated, or a provider verdict is ``flag`` or ``error``
  not_screened  the local checks pass but no provider record covers every ordered sequence (no record at all
                is this state, never ``cleared``); also the state when no order list was supplied
  cleared       the local checks pass and a provider record covers every ordered sequence with only ``pass``

The stage's envelope status is always ``ok``: ``blocked`` is a result, not a failure of the stage. The CLI exits
non-zero unless the state is ``cleared``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

from .artifacts import Artifact, ContractError, RunStore
from .cache import sha256_bytes, sha256_file
from .config import ConfigError
from .fasta import PROTEIN_ALPHABET, FastaError, read_fasta

STAGE = "s12"
MAX_LENGTH = 10000
VERDICTS = ("pass", "flag", "error")
EXIT = {"cleared": 0, "blocked": 2, "not_screened": 3}  # 1 is an error, as everywhere in vaxforge.cli
_SEVERITY = {"pass": 0, "not_applicable": 1, "not_evaluated": 2, "fail": 3}
LIMITS = [
    "The local checks are not a synthesis-screening result and never substitute for one.",
    "No provider or network was contacted. The provider record is supplied by the user; this module checks that it "
    "covers every ordered sequence and records its sha256, but cannot verify that the screening happened or that "
    "the record is authentic.",
    "Only whole S4 survivor sequences can match. A peptide or a nucleotide sequence does not match a candidate and is blocked.",
]


def sequence_digest(sequence: str) -> str:
    """``sha256:<hex>`` of the sequence with whitespace removed and upper-cased."""
    return "sha256:" + hashlib.sha256("".join(sequence.split()).upper().encode("utf-8")).hexdigest()


def _canonical(digest: str) -> str:
    return "sha256:" + digest.strip().lower().removeprefix("sha256:")


def read_order(path) -> list[dict]:
    """The order list: ``[{"id", "sequence", "digest"}]`` in file order. Raises ValueError for an empty list or a
    missing or repeated id."""
    order, seen = [], set()
    for rec in read_fasta(path):
        if not rec.id or rec.id in seen:
            raise ValueError(f"{path}: every record needs a unique id (problem at {rec.id or 'an empty header'!r})")
        seen.add(rec.id)
        order.append({"id": rec.id, "sequence": rec.seq.upper(), "digest": sequence_digest(rec.seq)})
    if not order:
        raise ValueError(f"{path}: the order list has no sequences")
    return order


def read_screening(path) -> dict:
    """A provider record (see the module docstring), validated. Raises ValueError if it is not well formed."""
    raw = Path(path).read_bytes()
    doc = json.loads(raw)
    if not isinstance(doc, dict):
        raise ValueError(f"{path}: expected a JSON object")
    for key in ("provider", "tool", "tool_version", "date"):
        if not isinstance(doc.get(key), str) or not doc[key].strip():
            raise ValueError(f"{path}: {key!r} is missing or empty")
    results = doc.get("results")
    if not isinstance(results, list) or not results:
        raise ValueError(f"{path}: 'results' must be a non-empty list")
    verdicts = {}
    for i, r in enumerate(results):
        if not isinstance(r, dict) or not isinstance(r.get("digest"), str) or r.get("verdict") not in VERDICTS:
            raise ValueError(f"{path}: results[{i}] needs a 'digest' and a 'verdict' of {', '.join(VERDICTS)}")
        digest = _canonical(r["digest"])
        if digest in verdicts:
            raise ValueError(f"{path}: {digest} is listed twice")
        verdicts[digest] = r["verdict"]
    return {"provider": doc["provider"].strip(), "tool": doc["tool"].strip(), "tool_version": doc["tool_version"].strip(),
            "date": doc["date"].strip(), "verdicts": verdicts,
            "file": {"name": Path(path).name, "sha256": sha256_bytes(raw), "bytes": len(raw)}}


def _check(result: str, detail: str) -> dict:
    return {"result": result, "detail": detail}


def _sanity(sequence: str) -> dict:
    bad = sorted(set(sequence) - PROTEIN_ALPHABET)
    if not sequence:
        return _check("fail", "empty sequence")
    if bad:
        return _check("fail", "residues outside the 20 standard amino acids: " + " ".join(bad[:8]))
    if len(sequence) > MAX_LENGTH:
        return _check("fail", f"{len(sequence)} residues is over the {MAX_LENGTH} sanity bound")
    return _check("pass", f"{len(sequence)} residues, standard amino acids only")


def _virulence(block: dict | None) -> tuple[str, str]:
    if block is None:
        return "not_evaluated", "the protein is absent from the S2 artifact"
    flag = block.get("is_virulence_associated")
    if flag is True:
        return "fail", f"S2 records it as virulence-associated (evidence {block.get('evidence')})"
    if flag is False:
        return "pass", "S2 compared it with VFDB and found no qualifying hit"
    if block.get("status") == "not_applicable":
        return "not_applicable", "S2: the virulence database does not apply to this organism"
    return "not_evaluated", f"S2 computed no virulence flag (status {block.get('status')!r})"


def _toxin(gates: dict) -> tuple[str, str]:
    gate = gates.get("toxicity") or {}
    status = gate.get("status")
    if status == "pass":
        return "pass", "S4 toxicity gate: pass"
    if status in ("flag", "reject"):
        return "fail", f"S4 toxicity gate: {status} ({gate.get('reason')})"
    return "not_evaluated", f"S4 recorded no toxicity result for it (status {status!r})"


def _flag_checks(matches: list[dict], s2_virulence: dict) -> dict:
    """virulence_flag and toxin_flag over every candidate that has this sequence; the worst result wins."""
    if not matches:
        return {name: _check("not_evaluated", "no S4 candidate has this sequence, so no flag could be read")
                for name in ("virulence_flag", "toxin_flag")}
    per_check = {"virulence_flag": [(c["protein_id"], *_virulence(s2_virulence.get(c["protein_id"]))) for c in matches],
                 "toxin_flag": [(c["protein_id"], *_toxin(c.get("gates") or {})) for c in matches]}
    worst = {name: max(results, key=lambda r: _SEVERITY[r[1]]) for name, results in per_check.items()}
    return {name: _check(result, f"{pid}: {detail}") for name, (pid, result, detail) in worst.items()}


def evaluate(order: list[dict], candidates: list[dict], s2_proteins: list[dict], record: dict | None) -> dict:
    """The gate's result as pure data: ``{"state", "reasons", "sequences"}``. ``order`` is empty when none was supplied."""
    by_digest: dict[str, list[dict]] = {}
    for c in candidates:
        by_digest.setdefault(sequence_digest(c["sequence"]), []).append(c)
    s2_virulence = {p["protein_id"]: p.get("virulence") for p in s2_proteins}
    rows, blockers = [], []
    for o in order:
        matches = by_digest.get(o["digest"], [])
        checks = {"selected_candidate": (_check("pass", "matches S4 candidate " + ", ".join(c["protein_id"] for c in matches))
                                         if matches else _check("fail", "no S4 candidate has this sequence")),
                  "sequence_sanity": _sanity(o["sequence"]), **_flag_checks(matches, s2_virulence)}
        verdict = record["verdicts"].get(o["digest"], "not_screened") if record else "not_screened"
        rows.append({"id": o["id"], "digest": o["digest"], "length": len(o["sequence"]), "local_checks": checks,
                     "provider_verdict": verdict})
        blockers += [f"{o['id']}: {name}: {c['detail']}" for name, c in checks.items() if c["result"] in ("fail", "not_evaluated")]
        if verdict in ("flag", "error"):
            blockers.append(f"{o['id']}: the provider record gives verdict {verdict}")
    uncovered = [r["id"] for r in rows if r["provider_verdict"] == "not_screened"]
    if not rows:
        reasons = ["no order list was supplied: nothing was checked and nothing was screened"]
    elif record is None:
        reasons = ["no provider screening record was supplied, so no sequence is screened"]
    elif uncovered:
        reasons = [f"the provider record does not cover {len(uncovered)} of {len(rows)} ordered sequences (first: {uncovered[0]})"]
    else:
        reasons = []
    if blockers:
        state = "blocked"
    elif rows and not uncovered:
        state = "cleared"
        reasons = [f"all {len(rows)} ordered sequences passed the local checks and {record['provider']} / {record['tool']} "
                   f"{record['tool_version']} ({record['date']}) lists each as pass; the record is user-supplied and unverified"]
    else:
        state = "not_screened"
    return {"state": state, "reasons": blockers + reasons if state == "blocked" else reasons, "sequences": rows}


def check(store: RunStore, order_path=None, screening_path=None) -> Artifact:
    """Read S2 and S4, evaluate the order list and write ``s12_biosafety.json``. Raises ContractError if S2 or S4
    is missing or not ``ok``, ValueError (or FastaError) if the order list or the provider record is unusable."""
    s2 = store.read_artifact("s2").require_ok()
    s4 = store.read_artifact("s4").require_ok()
    order = read_order(order_path) if order_path else []
    record = read_screening(screening_path) if screening_path else None
    result = evaluate(order, s4.payload["candidates"], s2.payload["proteins"], record)
    payload = {
        "state": result["state"],
        "reasons": result["reasons"],
        "order": ({"status": "supplied", "file": {"name": Path(order_path).name, "sha256": sha256_file(order_path),
                                                  "bytes": Path(order_path).stat().st_size}, "n_sequences": len(order)}
                  if order_path else {"status": "not_supplied"}),
        "provider_record": ({"status": "not_supplied"} if record is None else
                            {"status": "supplied", **{k: record[k] for k in ("provider", "tool", "tool_version", "date", "file")},
                             "n_results": len(record["verdicts"]),
                             "covers_every_ordered_sequence": all(r["provider_verdict"] != "not_screened" for r in result["sequences"]),
                             "authenticity": "not verified: the record is supplied by the user"}),
        "sequences": result["sequences"],
        "limits": LIMITS,
    }
    warnings = [f"S12 {payload['state']}: {r}" for r in payload["reasons"]] if payload["state"] != "cleared" else []
    return store.write_artifact(
        STAGE, payload, inputs=["s2", "s4"], status="ok", warnings=warnings,
        params={"selection": "s4 candidates (the survivors)", "alphabet": "".join(sorted(PROTEIN_ALPHABET)),
                "max_length": MAX_LENGTH, "digest": "sha256 of the sequence, upper-case, whitespace removed"},
        volatile={"order_path": str(order_path) if order_path else None,
                  "screening_path": str(screening_path) if screening_path else None})


def run(store: RunStore, config) -> Artifact:
    """The `vaxforge run` entrypoint. The order list and the provider record come from the config keys
    ``biosafety.order`` and ``biosafety.screening``; with no order list the artifact says so and the state is
    ``not_screened``."""
    order, screening = config.get("biosafety.order"), config.get("biosafety.screening")
    if screening and not order:
        raise ConfigError("biosafety.screening is set but biosafety.order is not")
    try:
        return check(store, order, screening)
    except (FastaError, ValueError, OSError) as e:
        raise ConfigError(f"S12: {e}") from None


def _report(art: Artifact) -> None:
    print(f"S12 biosecurity gate: {art.payload['state']}")
    for r in art.payload["reasons"]:
        print(f"  - {r}")
    print(f"  artifact: {art.path}")


def _cmd_check(args) -> int:
    run_dir = Path(args.run)
    try:
        if not run_dir.is_dir():
            raise ValueError(f"{run_dir} is not a run directory")
        art = check(RunStore(run_dir), args.order, args.screening)
    except (ContractError, FastaError, ValueError, OSError) as e:
        print(f"s12_biosafety: {e}", file=sys.stderr)
        return 1
    _report(art)
    return EXIT[art.payload["state"]]


def _add_commands(parser) -> None:
    sub = parser.add_subparsers(dest="biosafety_command", required=True)
    p = sub.add_parser("check", help="check an order list against the run, and a provider screening record if given")
    p.add_argument("order", help="protein FASTA of the sequences that would be sent to a synthesis provider")
    p.add_argument("--run", required=True, help="run directory holding the S2 and S4 artifacts")
    p.add_argument("--screening", help="the provider screening record (JSON; format in vaxforge.s12_biosafety)")
    p.set_defaults(func=_cmd_check)


def register_cli(subparsers, common):
    """`vaxforge biosafety check ORDER.fasta --run RUN_DIR [--screening FILE]`.
    Exit code: 0 cleared | 1 error | 2 blocked | 3 not_screened."""
    _add_commands(subparsers.add_parser("biosafety", parents=[common], help="S12 biosecurity gate"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="vaxforge biosafety", description="S12 biosecurity gate")
    _add_commands(ap)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
