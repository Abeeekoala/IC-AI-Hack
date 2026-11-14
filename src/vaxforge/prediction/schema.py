"""Reading the upstream S2 annotation artifact.

Stream A has not landed its ``runs/<run_id>/`` contract yet, so this loader is deliberately
tolerant: it accepts several plausible shapes and field aliases, normalises them into
``Protein`` objects, and raises ``ContractError`` with a precise message when something
required is missing. When A's real schema lands, adapt ``_ALIASES`` here and nowhere else.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping

from .errors import ContractError
from .fasta import clean_sequence

# Canonical localisation labels (PSORTb 3.0 vocabulary) + explicit Unknown.
CANONICAL_LOCALISATION = {
    "cytoplasmic": "Cytoplasmic",
    "cytoplasmicmembrane": "CytoplasmicMembrane",
    "innermembrane": "CytoplasmicMembrane",
    "periplasmic": "Periplasmic",
    "outermembrane": "OuterMembrane",
    "extracellular": "Extracellular",
    "secreted": "Extracellular",
    "cellwall": "CellWall",
    "multiple": "Multiple",
    "multiplelocalizationsites": "Multiple",
    "unknown": "Unknown",
}


def normalise_localisation(raw: Any) -> tuple[str, bool]:
    """Return ``(canonical_label, recognised)``. Missing/empty -> ('Unknown', True).

    'Unknown' is a first-class value (README S2): it is never folded into 'not surface'.
    """
    if raw is None:
        return "Unknown", True
    key = re.sub(r"[^a-z]", "", str(raw).lower())
    if not key:
        return "Unknown", True
    if key in CANONICAL_LOCALISATION:
        return CANONICAL_LOCALISATION[key], True
    return "Unknown", False


@dataclass
class Localisation:
    label: str = "Unknown"
    raw: str | None = None
    score: float | None = None
    source: str = "S2"
    recognised: bool = True
    status: str | None = None          # Stream A: predicted | psortb_unknown | not_run | not_applicable | fixture_stand_in
    reason: str | None = None


@dataclass
class Virulence:
    hit_id: str | None = None
    identity: float | None = None   # 0..1
    coverage: float | None = None   # 0..1
    evalue: float | None = None
    source: str = "VFDB"


@dataclass
class Protein:
    protein_id: str
    sequence: str
    name: str = ""
    gene: str = ""
    taxid: int | None = None
    accession: str | None = None       # UniProt accession (Stream A: protein_id may be 'sp|ACC|ENTRY')
    localisation: Localisation = field(default_factory=Localisation)
    virulence: Virulence | None = None
    extra: dict = field(default_factory=dict)

    @property
    def length(self) -> int:
        return len(self.sequence)


@dataclass
class S2Data:
    proteins: list[Protein]
    meta: dict
    path: Path | None = None

    def by_id(self) -> dict[str, Protein]:
        return {p.protein_id: p for p in self.proteins}


_ALIASES = {
    "protein_id": ("protein_id", "accession", "uniprot_id", "uniprot", "id", "entry"),
    "sequence": ("sequence", "seq", "aa_sequence"),
    "name": ("name", "protein_name", "description", "product"),
    "gene": ("gene", "gene_name"),
    "taxid": ("taxid", "tax_id", "organism_id", "ncbi_taxid"),
    "localisation": ("localisation", "localization", "psortb", "subcellular_location", "subcellular_localisation"),
    "virulence": ("virulence", "vfdb", "vf", "virulence_factor"),
}
_LOC_LABEL = ("label", "prediction", "final_localization", "localization", "localisation", "location", "call")
_LOC_SCORE = ("score", "final_score", "confidence", "probability")
_VF_ID = ("hit", "hit_id", "vfdb_id", "subject", "sseqid", "vf_id", "id")
_VF_IDENT = ("identity", "pident", "percent_identity")
_VF_COV = ("coverage", "qcov", "qcovs", "query_coverage", "cov")
_VF_EVAL = ("evalue", "e_value", "expect")


def _pick(d: Mapping, names: Iterable[str]) -> Any:
    for n in names:
        if n in d and d[n] is not None:
            return d[n]
    return None


def _frac(x: Any) -> float | None:
    if x is None or x == "":
        return None
    v = float(x)
    return v / 100.0 if v > 1.5 else v


def _parse_localisation(raw: Any) -> Localisation:
    if isinstance(raw, Mapping):
        lab = _pick(raw, _LOC_LABEL)
        score = _pick(raw, _LOC_SCORE)
        src = raw.get("source", "S2")
    else:
        lab, score, src = raw, None, "S2"
    label, ok = normalise_localisation(lab)
    status = reason = None
    if isinstance(raw, Mapping):
        status = raw.get("status")
        reason = raw.get("reason")
        if status in ("not_run", "not_applicable", "missing_from_psortb_output"):
            label, ok = "Unknown", True          # nothing was predicted: explicit Unknown, never a made-up label
    return Localisation(label=label, raw=None if lab is None else str(lab),
                        score=None if score is None else float(score), source=str(src), recognised=ok,
                        status=None if status is None else str(status), reason=None if reason is None else str(reason))


def _parse_virulence(raw: Any) -> Virulence | None:
    if raw is None or raw is False:
        return None
    if isinstance(raw, Mapping):
        if raw.get("status") in ("not_run", "not_applicable"):
            return None                                   # Stream A: not looked up != looked up and found nothing
        if isinstance(raw.get("best_hit"), Mapping):      # Stream A nests the hit and spells percentages explicitly
            bh = raw["best_hit"]
            idp, cvp = bh.get("identity_pct"), bh.get("query_coverage_pct")
            ev = bh.get("evalue")
            return Virulence(hit_id=None if bh.get("vfdb_id") is None else str(bh["vfdb_id"]),
                             identity=None if idp is None else float(idp) / 100.0,
                             coverage=None if cvp is None else float(cvp) / 100.0,
                             evalue=None if ev is None else float(ev), source="VFDB")
        if raw.get("status") == "no_hit":
            return None
        ident, cov = _frac(_pick(raw, _VF_IDENT)), _frac(_pick(raw, _VF_COV))
        ev = _pick(raw, _VF_EVAL)
        hid = _pick(raw, _VF_ID)
        if ident is None and cov is None and ev is None and hid is None:
            return None
        return Virulence(hit_id=None if hid is None else str(hid), identity=ident, coverage=cov,
                         evalue=None if ev is None else float(ev), source=str(raw.get("source", "VFDB")))
    return None  # a bare boolean carries no identity/coverage/E-value; the README requires all three


def _records(doc: Any) -> tuple[list[Mapping], dict]:
    meta: dict = {}
    if isinstance(doc, list):
        return doc, meta
    if not isinstance(doc, Mapping):
        raise ContractError("S2 artifact must be a JSON object or array")
    meta = {k: v for k, v in doc.items() if k not in ("payload", "proteins", "annotations", "records", "rows")}
    node: Any = doc
    if isinstance(doc.get("payload"), Mapping):
        node = doc["payload"]
        meta = {**meta, **{k: v for k, v in node.items() if k not in ("proteins", "annotations", "records", "rows")}}
    for key in ("proteins", "annotations", "records", "rows"):
        if isinstance(node.get(key), list):
            return node[key], meta
    raise ContractError("S2 artifact has no list under 'proteins' / 'annotations' / 'records' / 'rows' "
                        "(top level or inside 'payload')")


def _is_stream_a_s2(doc: Any) -> bool:
    return (isinstance(doc, Mapping) and str(doc.get("schema", "")).startswith("vaxforge/s2_annotate")
            and isinstance(doc.get("payload", {}).get("proteome"), Mapping))


def _resolve_ref(ref: Mapping, run_dir: Path, cache_dir: Path) -> Path:
    base = cache_dir if ref.get("root") == "cache" else run_dir
    return Path(base) / ref["path"]


def _gram_to_organism_type(organism: Mapping) -> str | None:
    """Stream A's S0 classification -> the Vaxign-ML organism type, or None when it is not a clean Gram+/-/virus."""
    if organism.get("domain") == "virus":
        return "virus"
    return {"negative": "gram-", "positive": "gram+"}.get(str(organism.get("gram_stain")))


def _load_stream_a(p: Path, doc: Mapping, cache_dir: str | Path | None) -> S2Data:
    """Stream A's S2 (docs/ARTIFACT_CONTRACT.md): records carry no sequence; sequences live in ``payload.proteome.file``."""
    from .fasta import read_fasta, clean_sequence
    if doc.get("status") != "ok":
        raise ContractError(f"{p.name}: status is {doc.get('status')!r}, not 'ok' (Stream A rule: require_ok)")
    run_dir = p.parent
    cache = Path(cache_dir) if cache_dir else Path("cache")
    pl = doc["payload"]
    fasta_path = _resolve_ref(pl["proteome"]["file"], run_dir, cache)
    if not fasta_path.exists():
        raise ContractError(f"{p.name}: proteome FASTA not found at {fasta_path}")
    seqs = {h: clean_sequence(s) for h, _, s in read_fasta(fasta_path)}

    def sibling(name: str) -> Mapping:
        f = run_dir / name
        return json.loads(f.read_text()) if f.exists() else {}
    s0, s1 = sibling("s0_taxonomy.json"), sibling("s1_acquire.json")
    s0p, s1p = s0.get("payload", {}), s1.get("payload", {})
    taxon, organism = s0p.get("taxon") or {}, s0p.get("organism") or pl.get("organism") or {}
    proteins: list[Protein] = []
    problems: list[str] = []
    for i, r in enumerate(pl["proteins"]):
        pid = r.get("protein_id")
        if pid not in seqs:
            problems.append(f"record {i}: protein_id {pid!r} not found in {fasta_path.name}")
            continue
        proteins.append(Protein(
            protein_id=pid, sequence=seqs[pid], name=str(r.get("description") or ""), gene=str(r.get("gene") or ""),
            taxid=taxon.get("taxid"), accession=r.get("accession"),
            localisation=_parse_localisation(r.get("localisation")),
            virulence=_parse_virulence(r.get("virulence")),
            extra={"entry_name": r.get("entry_name"), "accessibility": (r.get("localisation") or {}).get("accessibility")}))
    if problems:
        raise ContractError(f"{p.name}: " + "; ".join(problems[:5]))
    loc_counts: dict[str, int] = {}
    for q in proteins:
        loc_counts[q.localisation.status or "unspecified"] = loc_counts.get(q.localisation.status or "unspecified", 0) + 1
    meta: dict = {
        "taxid": taxon.get("taxid") or s1p.get("taxid"), "species_taxid": taxon.get("species_taxid"),
        "scientific_name": taxon.get("scientific_name"), "lineage": [x.get("name") for x in taxon.get("lineage", []) if isinstance(x, Mapping)],
        "organism": dict(organism), "proteome_id": pl["proteome"].get("upid"),
        "localisation_status_counts": loc_counts,
        "stream_a": {"s2_digest": doc.get("digest"), "run_id": doc.get("run_id"),
                     "s1_resources": {k: s1p.get("reference_dbs", {}).get(k, {}).get("status") for k in ("human_proteome", "vfdb", "protegen", "iedb")},
                     "protegen_target_file": s1p.get("reference_dbs", {}).get("protegen", {}).get("target_file")},
    }
    ot = _gram_to_organism_type(organism)
    if ot:
        meta["organism_type"] = ot
    return S2Data(proteins=proteins, meta=meta, path=p)


def load_s2_annotations(path: str | Path, cache_dir: str | Path | None = None) -> S2Data:
    p = Path(path)
    if not p.exists():
        raise ContractError(f"S2 annotation artifact not found: {p}")
    doc = json.loads(p.read_text())
    if _is_stream_a_s2(doc):
        return _load_stream_a(p, doc, cache_dir)
    recs, meta = _records(doc)
    proteins: list[Protein] = []
    seen: set[str] = set()
    problems: list[str] = []
    for i, r in enumerate(recs):
        if not isinstance(r, Mapping):
            problems.append(f"record {i} is not an object")
            continue
        pid = _pick(r, _ALIASES["protein_id"])
        seq = _pick(r, _ALIASES["sequence"])
        if pid is None or not seq:
            problems.append(f"record {i}: needs a protein id (one of {_ALIASES['protein_id']}) and a sequence")
            continue
        pid = str(pid)
        if pid in seen:
            problems.append(f"duplicate protein_id {pid!r}")
            continue
        seen.add(pid)
        tax = _pick(r, _ALIASES["taxid"])
        proteins.append(Protein(
            protein_id=pid,
            sequence=clean_sequence(str(seq)),
            name=str(_pick(r, _ALIASES["name"]) or ""),
            gene=str(_pick(r, _ALIASES["gene"]) or ""),
            taxid=int(tax) if tax not in (None, "") else None,
            localisation=_parse_localisation(_pick(r, _ALIASES["localisation"])),
            virulence=_parse_virulence(_pick(r, _ALIASES["virulence"])),
            extra={k: v for k, v in r.items()
                   if k not in {a for names in _ALIASES.values() for a in names}},
        ))
    if problems:
        shown = "; ".join(problems[:5]) + (f" (+{len(problems) - 5} more)" if len(problems) > 5 else "")
        raise ContractError(f"{p.name}: {shown}")
    if not proteins:
        raise ContractError(f"{p.name}: no proteins")
    return S2Data(proteins=proteins, meta=meta, path=p)
