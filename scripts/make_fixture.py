"""Build the committed fixture run  fixtures/nmb_fixture  from a real run (runs/nmb).

    python scripts/make_fixture.py            # needs a finished `vaxforge run` in runs/nmb and network for step 3

The fixture is a *projection* of real S0/S1/S2 artifacts onto ~40 proteins (the Bexsero antigens fHbp, NadA,
NHBA and PorA, the strongest VFDB hits, and a seeded random sample), written through the normal RunStore API so
every digest and the upstream chain are valid.  It lets streams B, C and D build against realistic artifacts
without downloading anything or running BLAST/PSORTb.

What is real: taxonomy, proteome sequences, UniProt metadata, the BLAST virulence annotation.
What is NOT: PSORTb was not available, so localisation labels are a clearly flagged stand-in derived from
UniProt's subcellular-location annotation (status ``fixture_stand_in``).  Protegen-derived files are left out
(licence not stated).
"""
from __future__ import annotations

import copy
import random
import shutil
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from vaxforge import s2_annotate as S2  # noqa: E402
from vaxforge.artifacts import RunStore  # noqa: E402
from vaxforge.config import load_config  # noqa: E402
from vaxforge.fasta import FastaRecord, read_fasta, write_fasta  # noqa: E402
from vaxforge.http import client_from_config  # noqa: E402

SRC = ROOT / "runs" / "nmb"
DST = ROOT / "fixtures" / "nmb_fixture"          # deliberately NOT under a folder called "runs/" (gitignored in the team repo)
MUST_HAVE = ["Q9JXV4", "Q9JXK7", "Q7DD37", "P0DH58"]      # fHbp, NadA, NHBA, PorA (the Bexsero-relevant ones present in MC58)
N_TOTAL, N_STRONG, SEED = 40, 8, 20261114
STANDIN = "fixture_stand_in:uniprot_subcellular_location"


def map_subcell(text: str) -> str:
    t = (text or "").lower()
    if "outer membrane" in t:
        return "OuterMembrane"
    if "secreted" in t or "extracellular" in t:
        return "Extracellular"
    if "periplasm" in t:
        return "Periplasmic"
    if "membrane" in t:
        return "CytoplasmicMembrane"
    if "cytoplasm" in t:
        return "Cytoplasmic"
    return "Unknown"


def strip_refs(obj, keep=()):
    """Remove file references (fixtures must not point into a cache or carry Protegen data)."""
    if isinstance(obj, dict):
        out = {}
        dropped = False
        for k, v in obj.items():
            if isinstance(v, dict) and v.get("root") in ("run", "cache") and "sha256" in v and k not in keep:
                dropped = True
                continue
            out[k] = strip_refs(v, keep)
        if dropped:
            out["omitted_from_fixture"] = True
        return out
    if isinstance(obj, list):
        return [strip_refs(v, keep) for v in obj]
    return obj


def main() -> None:
    cfg = load_config(ROOT / "config.yaml")
    src = RunStore(SRC, cache_dir=cfg.cache_dir)
    for st in ("s0", "s1", "s2"):
        if not src.has(st):
            sys.exit(f"{SRC} has no {st} artifact - run `vaxforge run --pathogen \"Neisseria meningitidis\" --out runs/nmb` first")
    a0, a1, a2 = (src.read_artifact(s) for s in ("s0", "s1", "s2"))
    proteins = a2.payload["proteins"]
    by_acc = {p["accession"]: p for p in proteins}

    chosen = [by_acc[a] for a in MUST_HAVE if a in by_acc]
    strong = sorted((p for p in proteins if p["virulence"]["evidence"] == "strong" and p not in chosen),
                    key=lambda p: -p["virulence"]["best_hit"]["bitscore"])[:N_STRONG]
    chosen += strong
    rest = [p for p in proteins if p not in chosen]
    random.Random(SEED).shuffle(rest)
    chosen += rest[: N_TOTAL - len(chosen)]
    keep_ids = {p["protein_id"] for p in chosen}
    chosen = [p for p in proteins if p["protein_id"] in keep_ids]               # original proteome order
    print(f"selected {len(chosen)} of {len(proteins)} proteins; Bexsero-relevant present: "
          f"{[a for a in MUST_HAVE if a in by_acc]}")

    if DST.exists():
        shutil.rmtree(DST)
    dst = RunStore(DST, run_id="nmb_fixture")
    shutil.copyfile(SRC / "config.resolved.json", DST / "config.resolved.json")

    # ---- S0: unchanged
    dst.write_artifact("s0", copy.deepcopy(a0.payload), params=a0.params, tools=a0.producer["tools"],
                       status=a0.status, warnings=a0.warnings)

    # ---- S1: same record, proteome file replaced by the subset; cache-bound references dropped
    full = src.resolve(a1.payload["proteome"]["file"])
    sub_path = dst.data_path("proteome.faa")
    recs = [r for r in read_fasta(full) if r.id in keep_ids]
    write_fasta(recs, sub_path)
    p1 = strip_refs(copy.deepcopy(a1.payload), keep=("file",))
    p1["proteome"]["file"] = dst.run_ref(sub_path, records=len(recs))
    p1["proteome"]["n_sequences"] = len(recs)
    p1["proteome"]["stats"] = {"records": len(recs), "residues": sum(len(r.seq) for r in recs),
                               "min_length": min(len(r.seq) for r in recs), "max_length": max(len(r.seq) for r in recs)}
    p1["proteome"]["fixture_subset"] = {"of_n_sequences": a1.payload["proteome"]["n_sequences"],
                                        "selection": f"{len(MUST_HAVE)} Bexsero-relevant + {N_STRONG} strongest VFDB hits + seeded random"}
    p1["comparison_proteome"].pop("file", None)
    dst.write_artifact("s1", p1, params={**a1.params, "fixture_subset_of": "runs/nmb"}, tools=a1.producer["tools"],
                       status=a1.status, warnings=a1.warnings)

    # ---- S2: BLAST results are the real ones; localisation is a flagged stand-in
    http = client_from_config(cfg)
    accs = ",".join(p["accession"] for p in chosen)
    url = "https://rest.uniprot.org/uniprotkb/accessions"
    tsv = http.get(url, params={"accessions": accs, "fields": "accession,cc_subcellular_location", "format": "tsv"}).text()
    subcell = {}
    for line in tsv.splitlines()[1:]:
        acc, _, loc = line.partition("\t")
        subcell[acc.strip()] = loc
    amap = {**S2.DEFAULT_ACCESSIBILITY, **(cfg.get("annotate.accessibility") or {})}
    new = copy.deepcopy(chosen)
    for p in new:
        label = map_subcell(subcell.get(p["accession"], ""))
        p["localisation"] = {"status": "fixture_stand_in", "label": label, "score": None, "multiple_sites": False,
                             "accessibility": amap.get(label, "unknown"), "source": STANDIN}
    tools = copy.deepcopy(a2.payload["tools"])
    tools["localisation"] = {"status": "ok", "tool": "NOT PSORTb", "source": STANDIN, "reason": None,
                             "note": "FIXTURE ONLY: labels derived from UniProt subcellular-location text"}
    out_tsv = dst.data_path("s2_annotations.tsv")
    S2.write_tsv(out_tsv, [S2.protein_row(p) for p in new])
    p2 = {
        "proteome": {"file": p1["proteome"]["file"], "n_proteins": len(new), "upid": a2.payload["proteome"].get("upid")},
        "organism": a2.payload["organism"], "thresholds": a2.payload["thresholds"], "tools": tools,
        "summary": S2.build_summary(new, a2.payload["summary"]["virulence"]["run_status"], "ok"),
        "columns": S2.TSV_COLUMNS, "tsv": dst.run_ref(out_tsv, records=len(new)), "proteins": new,
    }
    warnings = [w for w in a2.warnings if not w.startswith("localisation not run")]
    warnings.insert(0, "FIXTURE: localisation values are stand-ins from UniProt subcellular-location text, NOT PSORTb output")
    dst.write_artifact("s2", p2, params={**a2.params, "fixture_subset_of": "runs/nmb"}, tools=a2.producer["tools"],
                       warnings=warnings)

    (DST / "FIXTURE.md").write_text(
        "# nmb_fixture\n\n"
        "A 40-protein projection of a real `vaxforge run --pathogen \"Neisseria meningitidis\"` (S0-S2), for building and testing\n"
        "downstream stages without downloads, BLAST or PSORTb.  Written with the normal `RunStore` API, so\n"
        "`vaxforge verify fixtures/nmb_fixture` passes.\n\n"
        "| Part | Real? |\n|---|---|\n"
        "| taxonomy (S0), proteome sequences and UniProt metadata (S1) | real |\n"
        "| virulence annotation (S2: identity / coverage / E-value vs VFDB set A) | real BLAST+ output |\n"
        "| localisation labels | **stand-in** from UniProt subcellular-location text (`status: fixture_stand_in`), NOT PSORTb |\n"
        "| Protegen data | omitted (licence not stated) |\n\n"
        "Proteins: fHbp (Q9JXV4), NadA (Q9JXK7), NHBA (Q7DD37), PorA (P0DH58), the 8 strongest VFDB hits, and a seeded random sample.\n"
        "Note NHBA has no VFDB hit and NadA only a moderate one - useful edge cases for ranking and for stream D's Bexsero test.\n",
        encoding="utf-8")
    problems = dst.verify()
    print("fixture written to", DST, "| verify:", problems or "OK")
    print("localisation stand-in labels:", S2.build_summary(new, "ok", "ok")["localisation"]["by_label"])


if __name__ == "__main__":
    main()
