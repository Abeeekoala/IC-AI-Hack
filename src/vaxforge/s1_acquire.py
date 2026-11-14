"""S1 - data acquisition (cache-first): taxid -> reference proteome + reference databases.

Looks in the local cache first, downloads only on a miss, persists, then
returns - so a demo is independent of conference wifi once ``warm-cache`` has
been run.  Beyond what S2 needs (proteome, VFDB) this stage also stages what
the *other* streams read, so nobody has to download anything themselves:

=====================  ==============================================  ==========
resource               used by                                         required
=====================  ==============================================  ==========
proteome               S2 (and everything after)                       yes
vfdb (+ BLAST db)      S2 virulence                                    no (bacteria)
comparison_proteome    S4 conservancy (second strain, BLAST db)        no
human_proteome         S4 autoimmunity screen (BLAST db)               no
protegen (+ target)    stream D validation (LOPOV, Bexsero case)       no
iedb                   S4 / S5 (huge; opt-in, manual drop-in allowed)  no
afnd                   S6 - obtained inside IEDB PopCover, not here    -
=====================  ==============================================  ==========

An optional resource that cannot be obtained never fails the run, but it is
never silent either: it appears in ``resource_status`` with its reason and in
the artifact warnings.
"""
from __future__ import annotations

import gzip
import re
import shutil
import urllib.parse
from pathlib import Path
from typing import Any, Callable

from .artifacts import Artifact, RunStore
from .cache import Cache, CacheMiss, atomic_write_bytes, cache_from_config, sha256_file, utcnow_iso
from .config import Config
from .fasta import FastaError, FastaRecord, count_records, read_fasta, validate_protein_fasta, write_fasta
from .http import HttpClient, HttpError, OfflineError, client_from_config
from .s0_taxonomy import build_services, require_resolved
from .tools import BlastRunner, ToolError
from .uniprot import UniProtClient, UniProtError

OPTIONAL_ERRORS = (HttpError, OfflineError, CacheMiss, UniProtError, FastaError, ToolError, OSError, ValueError)

# Used when config.yaml does not override them (so a trimmed config still works).
DEFAULT_VFDB_URLS = {
    "A": "http://www.mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz",
    "B": "http://www.mgc.ac.cn/VFs/Down/VFDB_setB_pro.fas.gz",
}
DEFAULT_PROTEGEN_INDEX = "https://violinet.org/protegen/download/index.php"
DEFAULT_IEDB_URL = "https://www.iedb.org/downloader.php?file_name=doc/mhc_ligand_full_single_file.zip"
_PROTEGEN_NAME_CLASSES = {"synonym", "genbank synonym", "common name", "genbank common name", "acronym", "equivalent name"}


class AcquisitionError(Exception):
    """The one resource S1 cannot continue without (the target proteome) is unavailable."""


# ----------------------------------------------------------------------------
# proteome selection
# ----------------------------------------------------------------------------
def _busco(p: dict) -> float:
    return float(((p.get("proteomeCompletenessReport") or {}).get("buscoReport") or {}).get("score") or 0)


def _rank_key(p: dict) -> tuple:
    ptype = (p.get("proteomeType") or "").lower()
    return (
        ptype == "reference proteome",
        ptype == "representative proteome",
        (p.get("genomeAssembly") or {}).get("level") == "full",
        _busco(p),
        int(p.get("proteinCount") or 0),
    )


def select_primary_proteome(uni: UniProtClient, taxid: int, species_taxid: int | None, upid: str | None) -> tuple[dict, dict]:
    """Return (UniProt proteome record, selection info) for the pathogen."""
    if upid and upid.lower() != "auto":
        return uni.get_proteome(upid), {"method": "pinned", "from_taxid": None, "candidates_considered": 1}
    tried: list[int] = []
    for tid in dict.fromkeys([t for t in (taxid, species_taxid) if t]):
        tried.append(tid)
        refs = uni.search_proteomes(f"taxonomy_id:{tid} AND reference:true", size=10)
        if refs:
            best = max(refs, key=_rank_key)
            return best, {"method": "reference_proteome", "from_taxid": tid, "candidates_considered": len(refs)}
    for tid in tried:
        others = uni.search_proteomes(f"taxonomy_id:{tid}", size=100)
        if others:
            best = max(others, key=_rank_key)
            return best, {"method": "best_available_non_reference", "from_taxid": tid, "candidates_considered": len(others),
                          "note": "no UniProt reference proteome exists for this taxon; chose by assembly level, BUSCO, size"}
    raise AcquisitionError(
        f"UniProt has no proteome for taxid(s) {tried}. Supply your own protein FASTA with --proteome-fasta, "
        f"or pick a related taxon with --taxid."
    )


def _has_entries(p: dict) -> bool:
    stats = p.get("proteomeStatistics") or {}
    return sum(int(v or 0) for v in stats.values()) > 0


def rank_comparison_proteomes(uni: UniProtClient, primary: dict, species_taxid: int | None, taxid: int,
                              upid: str | None, limit: int = 6) -> tuple[list[dict], str]:
    """Other strains of the same species for S4 conservancy, best first.

    Some UniProt proteomes list a protein count but have no UniProtKB entries to download, so the
    caller tries them in order until one yields sequences.  Returns (candidates, reason-or-why).
    """
    if upid and upid.lower() != "auto":
        return [uni.get_proteome(upid)], "pinned"
    tid = species_taxid or taxid
    pool = [p for p in uni.search_proteomes(f"taxonomy_id:{tid}", size=100)
            if p["id"] != primary["id"] and p["taxonomy"]["taxonId"] != primary["taxonomy"]["taxonId"]]
    floor = 0.6 * int(primary.get("proteinCount") or 0)
    pool = [p for p in pool if int(p.get("proteinCount") or 0) >= floor]
    if not pool:
        return [], (f"no other proteome of taxid {tid} with at least 60% of the primary's "
                    f"{primary.get('proteinCount')} proteins; S4 conservancy cannot be assessed")
    pool.sort(key=lambda p: (_has_entries(p), _rank_key(p)[2], _busco(p), int(p.get("proteinCount") or 0)), reverse=True)
    return pool[:limit], "auto: other strain of the same species ranked by UniProtKB entries, assembly level, BUSCO, size"


def _proteome_summary(p: dict) -> dict:
    return {
        "upid": p["id"],
        "name": p["taxonomy"]["scientificName"],
        "taxid": p["taxonomy"]["taxonId"],
        "strain": p.get("strain"),
        "proteome_type": p.get("proteomeType"),
        "genome_assembly": (p.get("genomeAssembly") or {}).get("assemblyId"),
        "declared_protein_count": p.get("proteinCount"),
        "busco_score": _busco(p) or None,
    }


# ----------------------------------------------------------------------------
# resources
# ----------------------------------------------------------------------------
def _copy_if_changed(src: Path, dst: Path) -> None:
    if dst.is_file() and dst.stat().st_size == src.stat().st_size and sha256_file(dst) == sha256_file(src):
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src, dst)


def acquire_uniprot_proteome(uni: UniProtClient, cache: Cache, store: RunStore, rec: dict, run_name: str | None,
                             what: str) -> dict:
    upid = rec["id"]
    cache_file = cache.path("uniprot", "proteomes", f"{upid}.faa")
    got = uni.fetch_proteome_fasta(upid, cache_file, what)
    stats = validate_protein_fasta(cache_file)
    entry: dict[str, Any] = {
        "status": "cached" if got["cache_hit"] else "ok",
        **_proteome_summary(rec),
        "n_sequences": stats["records"],
        "release": got["release"],
        "source_url": got["source_url"],
        "sequence_source": got.get("source", "uniprotkb"),
        "retrieved_at": got["retrieved_at"],
        "stats": {k: stats[k] for k in ("records", "residues", "min_length", "max_length")},
        "cache_file": store.cache_ref(cache_file),
    }
    if entry["sequence_source"] == "uniparc":
        entry["note"] = ("sequences from UniParc (this proteome has no UniProtKB entries): ids are UPI accessions "
                         "and headers carry no protein names")
    declared = rec.get("proteinCount")
    if declared and abs(stats["records"] - int(declared)) > max(2, 0.01 * int(declared)):
        entry["count_mismatch"] = f"UniProt declares {declared} proteins but the FASTA stream returned {stats['records']}"
    if run_name:
        dst = store.data_path(run_name)
        _copy_if_changed(cache_file, dst)
        entry["file"] = store.run_ref(dst, records=stats["records"])
    return entry


def acquire_custom_proteome(store: RunStore, fasta: Path) -> dict:
    stats = validate_protein_fasta(fasta)
    dst = store.data_path("proteome.faa")
    _copy_if_changed(Path(fasta), dst)
    return {
        "status": "ok", "upid": None, "name": Path(fasta).stem, "taxid": None, "strain": None,
        "proteome_type": "user-supplied FASTA", "genome_assembly": None, "declared_protein_count": None,
        "busco_score": None, "n_sequences": stats["records"], "release": None, "source_url": str(fasta),
        "retrieved_at": utcnow_iso(),
        "stats": {k: stats[k] for k in ("records", "residues", "min_length", "max_length")},
        "file": store.run_ref(dst, records=stats["records"]),
    }


def acquire_vfdb(http: HttpClient, cache: Cache, store: RunStore, config: Config) -> dict:
    vset = str(config.get("acquire.vfdb.set", "A")).upper()
    url = config.get(f"acquire.vfdb.url_{vset}") or DEFAULT_VFDB_URLS.get(vset)
    if not url:
        raise ValueError(f"VFDB set {vset!r} is not known; use A or B (or set acquire.vfdb.url_{vset})")
    gz = cache.path("vfdb", f"VFDB_set{vset}_pro.fas.gz")
    fas = cache.path("vfdb", f"VFDB_set{vset}_pro.fas")
    hit = cache.usable(fas) and bool(cache.read_meta(fas))
    if not hit:
        cache.require(fas, f"VFDB set {vset}")
        res = http.download(url, gz)
        with gzip.open(gz, "rb") as fh:
            atomic_write_bytes(fas, fh.read())
        n = count_records(fas)
        if n == 0:
            fas.unlink(missing_ok=True)
            raise FastaError(f"VFDB download from {url} contained no sequences")
        cache.write_meta(fas, {"url": url, "retrieved_at": utcnow_iso(), "sha256": sha256_file(fas), "n_sequences": n,
                               "last_modified": res.headers.get("last-modified"), "gz_sha256": res.sha256, "set": vset})
    meta = cache.read_meta(fas)
    return {
        "status": "cached" if hit else "ok",
        "set": vset, "source_url": meta.get("url", url), "retrieved_at": meta.get("retrieved_at"),
        "version": meta.get("last_modified"), "n_sequences": meta.get("n_sequences"),
        "cache_file": store.cache_ref(fas, records=meta.get("n_sequences")),
    }


def _protegen_link(html: str, category: str) -> str:
    links = re.findall(r'href="(protegen-%s-[0-9.]+-[0-9-]+\.faa)"' % re.escape(category), html)
    if not links:
        raise ValueError(f"no protegen-{category}-*.faa link found on the Protegen download page (layout changed?)")
    return sorted(set(links))[-1]


def protegen_target_names(taxon: dict) -> list[str]:
    names = {taxon.get("species_name"), taxon.get("scientific_name")}
    for o in taxon.get("other_names", []):
        if o.get("class") in _PROTEGEN_NAME_CLASSES:
            names.add(re.sub(r"\s*\([^)]*\b\d{4}\b[^)]*\).*$", "", o["name"]).strip())
    return sorted(n for n in names if n and len(n) >= 4)


def protegen_matches(faa: Path, names: list[str]) -> list[FastaRecord]:
    low = [n.lower() for n in names]
    out: list[FastaRecord] = []
    for rec in read_fasta(faa):
        brackets = re.findall(r"\[([^\]]+)\]", rec.header)
        organism = (brackets[-1] if brackets else rec.header).lower()
        if any(n in organism for n in low):
            out.append(rec)
    return out


def acquire_protegen(http: HttpClient, cache: Cache, store: RunStore, config: Config, taxon: dict) -> dict:
    category = str(config.get("acquire.protegen.category", "all"))
    index_url = str(config.get("acquire.protegen.index_url") or DEFAULT_PROTEGEN_INDEX)
    index = cache.path("protegen", "download_index.html")
    if not cache.usable(index):
        cache.require(index, "Protegen download index")
        cache.put_bytes(index, http.get(index_url).body, {"url": index_url})
    link = _protegen_link(index.read_text(encoding="utf-8", errors="replace"), category)
    url = urllib.parse.urljoin(index_url, link)
    faa = cache.path("protegen", link)
    hit = cache.usable(faa) and bool(cache.read_meta(faa))
    if not hit:
        cache.require(faa, f"Protegen {link}")
        res = http.download(url, faa)
        cache.write_meta(faa, {"url": url, "retrieved_at": utcnow_iso(), "sha256": res.sha256,
                               "n_sequences": count_records(faa)})
    meta = cache.read_meta(faa)
    names = protegen_target_names(taxon)
    matches = protegen_matches(faa, names)
    entry: dict[str, Any] = {
        "status": "cached" if hit else "ok",
        "category": category, "source_url": meta.get("url", url), "retrieved_at": meta.get("retrieved_at"),
        "version": re.sub(r"^protegen-[a-z_]+-|\.faa$", "", link),
        "n_sequences": meta.get("n_sequences"),
        "cache_file": store.cache_ref(faa, records=meta.get("n_sequences")),
        "target_names_searched": names,
        "target_matches": len(matches),
        "redistribution": "licence not stated by Protegen - keep in the gitignored cache/runs folders, contact the authors before sharing",
        "note": "Stream D must hold these target-pathogen entries out of any training set before the Bexsero/LOPOV run.",
    }
    if matches:
        dst = store.data_path("protegen_target.faa")
        write_fasta(matches, dst)
        entry["target_file"] = store.run_ref(dst, records=len(matches))
    return entry


def acquire_human(uni: UniProtClient, cache: Cache, store: RunStore, config: Config) -> dict:
    upid = str(config.get("acquire.human.upid", "UP000005640"))
    reviewed = bool(config.get("acquire.human.reviewed_only", True))
    query = f"(proteome:{upid})" + (" AND (reviewed:true)" if reviewed else "")
    path = cache.path("uniprot", "proteomes", f"{upid}{'_reviewed' if reviewed else ''}.faa")
    got = uni.fetch_fasta(query, path, f"human proteome {upid}")
    stats = validate_protein_fasta(path)
    return {
        "status": "cached" if got["cache_hit"] else "ok",
        "upid": upid, "reviewed_only": reviewed, "n_sequences": stats["records"], "release": got["release"],
        "source_url": got["source_url"], "retrieved_at": got["retrieved_at"],
        "cache_file": store.cache_ref(path, records=stats["records"]),
        "note": "Swiss-Prot human proteins only" if reviewed else "full human reference proteome (large)",
    }


def acquire_iedb(http: HttpClient, cache: Cache, store: RunStore, config: Config) -> dict:
    manual_dir = cache.path("manual", "iedb")
    manual = sorted(p for p in manual_dir.glob("*") if p.is_file() and not p.name.endswith(".meta.json")) if manual_dir.is_dir() else []
    if manual:
        f = manual[0]
        return {"status": "cached", "source": "manual drop-in", "retrieved_at": None,
                "cache_file": store.cache_ref(f), "note": "user-supplied IEDB export"}
    url = str(config.get("acquire.iedb.url") or DEFAULT_IEDB_URL)
    dest = cache.path("iedb", Path(urllib.parse.urlsplit(url).query.split("=")[-1] or "iedb_export.zip").name)
    if cache.usable(dest) and cache.read_meta(dest):
        return {"status": "cached", "source_url": url, "retrieved_at": cache.read_meta(dest).get("retrieved_at"),
                "cache_file": store.cache_ref(dest)}
    cache.require(dest, "IEDB export")
    try:
        res = http.download(url, dest)
        head = dest.read_bytes()[:4]
        if head[:2] != b"PK":
            dest.unlink(missing_ok=True)
            raise ValueError("IEDB did not return a zip file (the site may require an interactive download)")
        cache.write_meta(dest, {"url": url, "retrieved_at": utcnow_iso(), "sha256": res.sha256})
        return {"status": "ok", "source_url": url, "retrieved_at": cache.read_meta(dest)["retrieved_at"],
                "cache_file": store.cache_ref(dest)}
    except OPTIONAL_ERRORS as exc:
        return {"status": "manual_required", "reason": str(exc),
                "how_to": f"download the IEDB export manually from https://www.iedb.org/database_export_v3.php and put it in {manual_dir}"}


# ----------------------------------------------------------------------------
# stage entrypoint
# ----------------------------------------------------------------------------
def _norm(volatile: dict, name: str, entry: dict) -> dict:
    """Keep the hashed payload about *what data we have*; move *how/when we got it* to ``volatile``.

    Statuses say what is available (``ok``); whether it came from cache and when it was fetched are provenance,
    not content - the file hashes already identify the data - so a ``--refresh`` of unchanged data keeps the digest.
    """
    st = entry.get("status")
    if st in ("ok", "cached", "built"):
        volatile["cache_hits"][name] = st == "cached"
        entry["status"] = "ok"
    if entry.get("retrieved_at"):
        volatile["retrieved_at"][name] = entry.pop("retrieved_at")
    return entry


def build_blast_dbs(runner: BlastRunner, store: RunStore, cache: Cache, sources: dict[str, dict | None],
                    warnings: list[str]) -> dict:
    out: dict[str, dict] = {}
    for name, entry in sources.items():
        if not entry or entry.get("status") not in ("ok", "cached") or not (entry.get("cache_file") or entry.get("file")):
            out[name] = {"status": "skipped", "reason": "source FASTA not available"}
            continue
        ref = entry.get("cache_file") or entry["file"]
        path = store.resolve(ref, cache.root)
        try:
            built = runner.make_db(path, name, ref["sha256"], title=name)
            out[name] = {"status": built["status"], "name": name, "fasta_sha256": ref["sha256"],
                         "prefix_hint": built["prefix"], "blast_version": runner.version,
                         "how_to_open": "BlastRunner(config).db_prefix(name, fasta_sha256)"}
        except ToolError as exc:
            out[name] = {"status": "unavailable", "reason": str(exc)}
            warnings.append(f"BLAST database for {name} not built: {exc}")
    return out


def run(store: RunStore, config: Config, *, http: HttpClient | None = None, cache: Cache | None = None,
        transport=None, runner: BlastRunner | None = None, proteome_fasta: str | Path | None = None,
        proteome_upid: str | None = None) -> Artifact:
    http, cache = build_services(config, transport, http, cache)
    store.cache_dir = cache.root
    runner = runner or BlastRunner(config, cache.root)
    s0 = require_resolved(store.read_artifact("s0"))
    taxon, organism = s0["taxon"], s0["organism"]
    taxid, species_taxid = int(taxon["taxid"]), taxon.get("species_taxid")
    uni = UniProtClient(http, cache)

    warnings: list[str] = []
    status: dict[str, str] = {}
    hits: dict[str, bool] = {}               # resource -> served from cache?  (volatile, not hashed)
    stamps: dict[str, str] = {}              # resource -> when it was fetched (volatile, not hashed)
    db_prefixes: dict[str, str] = {}         # machine-specific BLAST db locations (volatile)
    volatile = {"cache_hits": hits, "retrieved_at": stamps, "offline": bool(cache.offline),
                "blast_db_prefixes": db_prefixes}
    reference: dict[str, Any] = {}
    selection: dict[str, Any] = {}
    custom = proteome_fasta or config.get("acquire.proteome.fasta")
    upid = proteome_upid or config.get("acquire.proteome.upid", "auto")

    def finish(payload_status: str, proteome_entry: dict) -> Artifact:
        payload = {
            "taxid": taxid, "species_taxid": species_taxid, "scientific_name": taxon["scientific_name"],
            "proteome": proteome_entry, "comparison_proteome": comparison, "reference_dbs": reference,
            "blast_dbs": blast_dbs, "resource_status": status, "proteome_selection": selection,
        }
        return store.write_artifact(
            "s1", payload,
            params={"taxid": taxid, "species_taxid": species_taxid, "proteome_upid": upid, "custom_fasta": str(custom) if custom else None,
                    "comparison": config.get("acquire.comparison"), "vfdb": config.get("acquire.vfdb"),
                    "protegen": {"enabled": config.get("acquire.protegen.enabled"), "category": config.get("acquire.protegen.category")},
                    "human": config.get("acquire.human"), "iedb_enabled": config.get("acquire.iedb.enabled"),
                    "blastdb": config.get("acquire.blastdb")},
            tools={"blast": runner.version if runner.available else "unavailable",
                   "uniprot_release": proteome_entry.get("release") or "n/a"},
            status=payload_status, warnings=warnings,
            volatile=volatile)

    comparison: dict[str, Any] = {"status": "skipped", "reason": "not attempted"}
    blast_dbs: dict[str, Any] = {}

    # --- target proteome (the only fatal resource) ---------------------------------------
    try:
        if custom:
            primary = acquire_custom_proteome(store, Path(custom))
            selection.update({"method": "user_supplied_fasta"})
            primary_rec = None
        else:
            rec, sel = select_primary_proteome(uni, taxid, species_taxid, upid)
            selection.update(sel)
            primary = acquire_uniprot_proteome(uni, cache, store, rec, "proteome.faa", f"proteome {rec['id']}")
            primary_rec = rec
    except (AcquisitionError, UniProtError, FastaError, HttpError, OfflineError, CacheMiss, OSError) as exc:
        msg = f"target proteome unavailable: {exc}"
        warnings.insert(0, msg)
        status["proteome"] = "unavailable"
        return finish("failed", {"status": "unavailable", "reason": str(exc)})
    status["proteome"] = _norm(volatile, "proteome", primary)["status"]
    if primary.get("count_mismatch"):
        warnings.append(primary["count_mismatch"])

    # --- comparison strain ------------------------------------------------------------------------
    if not config.get("acquire.comparison.enabled", True):
        comparison = {"status": "disabled"}
    elif primary_rec is None:
        comparison = {"status": "skipped", "reason": "custom proteome FASTA: no species to pick a second strain from"}
    else:
        try:
            ranked, why = rank_comparison_proteomes(uni, primary_rec, species_taxid, taxid, config.get("acquire.comparison.upid", "auto"))
            comparison = {"status": "unavailable", "reason": why}
            skipped: list[str] = []
            for crec in ranked:
                try:
                    comparison = acquire_uniprot_proteome(uni, cache, store, crec, "comparison_proteome.faa",
                                                          f"comparison proteome {crec['id']}")
                    comparison["selection_reason"] = why
                    if skipped:
                        comparison["skipped_candidates"] = skipped
                    break
                except (UniProtError, FastaError) as exc:     # e.g. a proteome with no downloadable entries
                    skipped.append(f"{crec['id']}: {exc}")
            else:
                if ranked:
                    comparison = {"status": "unavailable", "reason": "none of the candidate proteomes could be downloaded",
                                  "skipped_candidates": skipped}
            if comparison["status"] == "unavailable":
                warnings.append(f"comparison proteome: {comparison['reason']}")
        except OPTIONAL_ERRORS as exc:
            comparison = {"status": "unavailable", "reason": str(exc)}
            warnings.append(f"comparison proteome unavailable: {exc}")
    status["comparison_proteome"] = _norm(volatile, "comparison_proteome", comparison)["status"]

    # --- reference databases ---------------------------------------------------------------------------
    def attempt(name: str, enabled: bool, fn: Callable[[], dict], applicable: bool = True, why_not: str = "") -> None:
        if not enabled:
            reference[name] = {"status": "disabled"}
        elif not applicable:
            reference[name] = {"status": "skipped_not_applicable", "reason": why_not}
        else:
            try:
                reference[name] = fn()
            except OPTIONAL_ERRORS as exc:
                reference[name] = {"status": "unavailable", "reason": str(exc)}
                warnings.append(f"{name} unavailable: {exc}")
        status[name] = _norm(volatile, name, reference[name])["status"]

    attempt("vfdb", bool(config.get("acquire.vfdb.enabled", True)), lambda: acquire_vfdb(http, cache, store, config),
            applicable=bool(organism.get("virulence_db_applicable")),
            why_not=f"VFDB is a bacterial virulence database; organism domain is {organism.get('domain')}")
    attempt("protegen", bool(config.get("acquire.protegen.enabled", True)),
            lambda: acquire_protegen(http, cache, store, config, taxon))
    attempt("human_proteome", bool(config.get("acquire.human.enabled", True)), lambda: acquire_human(uni, cache, store, config))
    attempt("iedb", bool(config.get("acquire.iedb.enabled", False)), lambda: acquire_iedb(http, cache, store, config))
    reference["afnd"] = {"status": "deferred_to_s6",
                         "note": "HLA allele frequencies come from IEDB Population Coverage inside S6 (stream B); nothing is downloaded here"}
    status["afnd"] = "deferred_to_s6"

    # --- BLAST databases ----------------------------------------------------------------------------------
    if config.get("acquire.blastdb", True):
        if not runner.available:
            warnings.append("BLAST+ not found (native or Docker): no BLAST databases built; S2 virulence annotation cannot run")
        blast_dbs = build_blast_dbs(runner, store, cache, {
            "vfdb": reference.get("vfdb"), "human_proteome": reference.get("human_proteome"),
            "comparison_proteome": comparison, "proteome": primary}, warnings)
    for name, entry in blast_dbs.items():
        if entry.get("prefix_hint"):
            db_prefixes[name] = entry.pop("prefix_hint")
        status[f"blastdb:{name}"] = _norm(volatile, f"blastdb:{name}", entry)["status"]

    return finish("ok", primary)


def warm_cache(config: Config, pathogens: list[str], transport=None, runner: BlastRunner | None = None) -> list[dict]:
    """Prefetch S0 + S1 for each pathogen so a demo can run offline.  Returns one result row each."""
    from . import s0_taxonomy

    rows: list[dict] = []
    cache = cache_from_config(config)
    cache.offline = False                    # warming only makes sense online
    http = client_from_config(config, transport)
    http.offline = False
    for name in pathogens:
        slug = re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_").lower() or "pathogen"
        store = RunStore(config.cache_dir / "_warm_runs" / slug, cache_dir=config.cache_dir)
        row: dict[str, Any] = {"pathogen": name}
        try:
            a0 = s0_taxonomy.run(store, config, pathogen=name, http=http, cache=cache)
            row["s0"] = a0.status
            if a0.status != "ok":
                row["note"] = a0.payload["resolution"]["message"]
                rows.append(row)
                continue
            a1 = run(store, config, http=http, cache=cache, runner=runner)
            row["s1"] = a1.status
            row["resources"] = a1.payload["resource_status"]
            row["warnings"] = a1.warnings
        except Exception as exc:  # keep warming the other pathogens
            row["error"] = f"{type(exc).__name__}: {exc}"
        rows.append(row)
    return rows
