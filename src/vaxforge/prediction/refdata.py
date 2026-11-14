"""Cache-first reference data needed by S4/S5 that the README's S1 table does not list.

README S1 fetches the target proteome, VFDB, Protegen and IEDB, but S4 also needs a *human*
proteome (autoimmunity gate) and *other-strain* proteomes (conservancy gate), and S5 wants
AlphaFold DB structures for DiscoTope-3.0. If stream A's S1 later provides these, point
``refdata.human_proteome`` / ``refdata.conservancy_proteomes`` at them and this module is bypassed.

Verified endpoints (live, 2026-11-14):
  * UniProt proteome search: ``rest.uniprot.org/proteomes/search?query=taxonomy_id:<taxid>&format=tsv&fields=upid,organism,organism_id,protein_count``
    (``taxonomy_id`` matches descendants, so a species taxid returns its strains)
  * UniProt FASTA stream:   ``rest.uniprot.org/uniprotkb/stream?query=proteome:<UPID>&format=fasta&compressed=true``
  * AlphaFold DB:           ``alphafold.ebi.ac.uk/api/prediction/<accession>`` -> JSON list with ``pdbUrl``
The BLAST URL API is deliberately NOT used (README S1 warning).
"""
from __future__ import annotations

import collections
import gzip
import hashlib
import io
import json
import pickle
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Sequence

from .errors import ToolFailed, ToolUnavailable

IEDB_POPCOV_URL = "https://downloads.iedb.org/tools/population/LATEST/IEDB_Population_Coverage-3.0.2.tar.gz"
UA = "vaxforge-prediction-core/0.1 (research pipeline)"     # deliberately generic: no personal details


@dataclass
class Fetched:
    body: bytes
    headers: dict


Fetcher = Callable[[str], Fetched]


def http_fetch(url: str, *, timeout: float = 180, retries: int = 4) -> Fetched:
    """GET with exponential back-off on 429 / 5xx."""
    delay = 2.0
    last: Exception | None = None
    for attempt in range(retries + 1):
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return Fetched(r.read(), {k.lower(): v for k, v in r.headers.items()})
        except urllib.error.HTTPError as e:
            if e.code == 404:
                raise
            last = e
            if e.code not in (429, 500, 502, 503, 504) or attempt == retries:
                raise
        except (urllib.error.URLError, TimeoutError) as e:
            last = e
            if attempt == retries:
                raise
        time.sleep(delay)
        delay *= 2
    raise ToolFailed(f"GET {url} failed: {last}")


def _maybe_gunzip(body: bytes) -> bytes:
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


@dataclass
class ProteomeRef:
    upid: str
    organism: str
    organism_id: int | None
    protein_count: int | None


class RefData:
    def __init__(self, cache_dir: str | Path = "cache", *, offline: bool = False, fetch: Fetcher | None = None):
        self.root = Path(cache_dir) / "refdata"
        self.offline = offline
        self._fetch = fetch or http_fetch
        self.provenance: list[dict] = []     # what was fetched / reused; stages copy this into the artifact

    # -- helpers --------------------------------------------------------------------
    def _get(self, url: str) -> Fetched:
        if self.offline:
            raise ToolUnavailable(f"offline mode: refusing to fetch {url}")
        return self._fetch(url)

    def _store(self, dest: Path, body: bytes, url: str, headers: dict, *, expect_fasta: bool = False) -> Path:
        if expect_fasta and b">" not in body[:4096]:
            # UniProt answers HTTP 200 with an empty body for proteomes that have no UniProtKB entries.
            # Caching that would turn every downstream BLAST into "nothing is conserved / no homolog".
            raise ToolFailed(f"{url} returned no FASTA records; refusing to cache an empty file")
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        tmp.write_bytes(body)
        tmp.replace(dest)
        meta = {"url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                "sha256": hashlib.sha256(body).hexdigest(), "uniprot_release": headers.get("x-uniprot-release")}
        dest.with_suffix(dest.suffix + ".meta.json").write_text(json.dumps(meta, indent=2))
        self.provenance.append({"path": str(dest), **meta})
        return dest

    # -- human proteome -------------------------------------------------------------
    def human_proteome(self, path: str | Path | None = None, *, reviewed_only: bool = True) -> Path:
        if path:
            p = Path(path)
            if not p.exists():
                raise ToolUnavailable(f"configured human proteome not found: {p}")
            return p
        dest = self.root / ("human_UP000005640_swissprot.fasta" if reviewed_only else "human_UP000005640_all.fasta")
        if dest.exists():
            return dest
        q = "(proteome:UP000005640)" + (" AND (reviewed:true)" if reviewed_only else "")
        url = ("https://rest.uniprot.org/uniprotkb/stream?query=" + urllib.parse.quote(q) +
               "&format=fasta&compressed=true")
        f = self._get(url)
        return self._store(dest, _maybe_gunzip(f.body), url, f.headers, expect_fasta=True)

    # -- other strains --------------------------------------------------------------
    def species_proteomes(self, species_taxid: int, *, exclude_upids: tuple[str, ...] = (), limit: int = 2) -> list[ProteomeRef]:
        listing = self.root / f"species_{species_taxid}_proteomes.tsv"       # cached so offline demos keep working
        if listing.exists():
            rows = listing.read_text().strip().splitlines()[1:]
        else:
            url = ("https://rest.uniprot.org/proteomes/search?query=" + urllib.parse.quote(f"taxonomy_id:{species_taxid}") +
                   "&format=tsv&size=50&fields=upid,organism,organism_id,protein_count")
            body = _maybe_gunzip(self._get(url).body)
            listing.parent.mkdir(parents=True, exist_ok=True)
            listing.write_bytes(body)
            rows = body.decode().strip().splitlines()[1:]
        refs: list[ProteomeRef] = []
        for r in rows:
            c = r.split("\t")
            if len(c) >= 4 and c[0] not in exclude_upids:
                refs.append(ProteomeRef(c[0], c[1], int(c[2]) if c[2].isdigit() else None,
                                        int(c[3]) if c[3].isdigit() else None))
        refs.sort(key=lambda x: (-(x.protein_count or 0), x.upid))     # most complete first, deterministic
        return refs[:limit]

    def proteome_fasta(self, upid: str) -> Path:
        """FASTA for a UniProt proteome.

        Verified live (2026-11-14): only *Reference* proteomes have UniProtKB entries; the many
        "Non Reference proteome" strains return an EMPTY 200 response from ``uniprotkb/stream``. Their
        sequences are still available from UniParc (``uniparc/stream?query=upid:<UPID>``), which is
        used as the fallback (header ids are UPI accessions, which is fine for BLAST).
        """
        dest = self.root / f"proteome_{upid}.fasta"
        if dest.exists():
            return dest
        kb = ("https://rest.uniprot.org/uniprotkb/stream?query=" + urllib.parse.quote(f"proteome:{upid}") +
              "&format=fasta&compressed=true")
        f = self._get(kb)
        body = _maybe_gunzip(f.body)
        url = kb
        if b">" not in body[:4096]:
            url = ("https://rest.uniprot.org/uniparc/stream?query=" + urllib.parse.quote(f"upid:{upid}") +
                   "&format=fasta&compressed=true")
            f = self._get(url)
            body = _maybe_gunzip(f.body)
        return self._store(dest, body, url, f.headers, expect_fasta=True)

    def strain_proteomes(self, species_taxid: int, *, exclude_upids: tuple[str, ...] = (),
                         exclude_organism_ids: tuple[int, ...] = (), n: int = 2) -> list[tuple[str, Path]]:
        """Up to ``n`` other-strain proteomes ``[(label, fasta_path)]`` that actually yield sequences."""
        out: list[tuple[str, Path]] = []
        for r in self.species_proteomes(species_taxid, exclude_upids=exclude_upids, limit=n + 12):
            if r.organism_id in exclude_organism_ids:
                continue
            try:
                out.append((f"{r.upid} {r.organism}", self.proteome_fasta(r.upid)))
            except ToolFailed:
                self.provenance.append({"skipped_proteome": r.upid, "reason": "no sequences in UniProtKB or UniParc"})
                continue
            if len(out) >= n:
                break
        return out

    # -- HLA allele frequencies ----------------------------------------------------------
    def afnd_table(self, populations: Sequence[str], *, url: str = IEDB_POPCOV_URL) -> Path:
        """IEDB-format allele-frequency TSV for ``populations`` (cache-first).

        Source: the frequency data bundled with the IEDB Population Coverage tool (derived from the
        Allele Frequency Net Database). Only the data pickle is read out of the tarball, in memory, with a
        restricted unpickler; nothing from the tarball is extracted to disk or executed.
        Sample sizes are not in that data set, so none are reported.
        """
        tag = hashlib.sha1("|".join(populations).encode()).hexdigest()[:8]
        dest = self.root / "afnd" / f"iedb_popcov_{tag}.tsv"
        if dest.exists():
            return dest
        f = self._get(url)
        with tarfile.open(fileobj=io.BytesIO(f.body), mode="r:gz") as tf:
            member = next((m for m in tf.getmembers() if m.name.endswith("population_genotype_map.p")), None)
            if member is None or tf.extractfile(member) is None:
                raise ToolFailed("population_genotype_map.p not found in the IEDB Population Coverage archive")
            data = tf.extractfile(member).read()
        popcov = read_iedb_popcov_pickle(data)
        write_user_population_tsv(popcov, populations, dest)
        meta = {"url": url, "fetched_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "populations": list(populations),
                "archive_sha256": hashlib.sha256(f.body).hexdigest(), "tsv_sha256": hashlib.sha256(dest.read_bytes()).hexdigest(),
                "note": "frequencies from the IEDB Population Coverage 3.0.2 bundle (AFND-derived); no sample sizes available"}
        dest.with_suffix(".tsv.meta.json").write_text(json.dumps(meta, indent=2))
        self.provenance.append({"path": str(dest), **meta})
        return dest

    # -- AlphaFold DB ---------------------------------------------------------------
    def afdb_structure(self, accession: str) -> Path | None:
        """PDB path for a UniProt accession, or ``None`` if AlphaFold DB has no model for it."""
        dest = self.root / "afdb" / f"AF-{accession}.pdb"
        if dest.exists():
            return dest
        try:
            api = self._get(f"https://alphafold.ebi.ac.uk/api/prediction/{urllib.parse.quote(accession)}")
            entries = json.loads(api.body)
            pdb_url = entries[0]["pdbUrl"]
            if not str(pdb_url).startswith("https://alphafold.ebi.ac.uk/"):
                raise ToolFailed(f"unexpected AlphaFold DB file URL host: {pdb_url}")
            f = self._get(pdb_url)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            raise
        return self._store(dest, f.body, pdb_url, f.headers)


# ------------------------------------------------------------------ HLA frequencies (AFND via IEDB)
class _OrderedDictOnly(pickle.Unpickler):
    """The IEDB data pickle only needs ``collections.OrderedDict`` (verified by static opcode scan);
    refuse every other global so a tampered download cannot execute code."""

    def find_class(self, module, name):
        if (module, name) == ("collections", "OrderedDict"):
            return collections.OrderedDict
        raise pickle.UnpicklingError(f"blocked global {module}.{name} in IEDB frequency pickle")


def read_iedb_popcov_pickle(data: bytes) -> dict:
    """``population_genotype_map.p`` -> ``{'I'|'II': {population: {locus: [(allele, freq), ...]}}}`` (restricted unpickle)."""
    try:
        obj = _OrderedDictOnly(io.BytesIO(data)).load()
    except pickle.UnpicklingError as e:
        raise ToolFailed(str(e)) from e
    if not isinstance(obj, dict) or set(obj) != {"I", "II"}:
        raise ToolFailed("IEDB frequency pickle has an unexpected structure")
    return obj


def write_user_population_tsv(popcov: dict, populations: Sequence[str], path: Path) -> Path:
    """Write the IEDB *user population* TSV (MHC class | MHC locus | MHC allele | <populations...>)."""
    missing = [pp for pp in populations if not any(pp in popcov[c] for c in popcov)]
    if missing:
        raise ToolFailed(f"populations not in the IEDB data: {missing}")
    rows: dict[tuple[str, str, str], dict[str, float]] = {}
    for cls_key in ("I", "II"):
        for pop in populations:
            for locus, lst in popcov[cls_key].get(pop, {}).items():
                for allele, freq in lst:
                    rows.setdefault((cls_key, locus, allele), {})[pop] = float(freq)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["\t".join(["MHC class", "MHC locus", "MHC allele", *populations])]
    for (c, l, a), freqs in sorted(rows.items()):
        lines.append("\t".join([c, l, a, *[("" if pop not in freqs else repr(freqs[pop])) for pop in populations]]))
    path.write_text("\n".join(lines) + "\n")
    return path


# -------------------------------------------------------------------------- IEDB ligands
def load_peptide_set(path: str | Path) -> set[str]:
    """Load known human MHC ligand peptides.

    Accepts (a) a plain text file with one peptide per line, or (b) an IEDB ``mhc_ligand_full.csv``
    export (two header rows). The CSV branch is best-effort and UNVERIFIED against a real export
    in this repo: it looks for a column whose merged header mentions 'epitope' + 'name'
    (peptide), and, when present, keeps only rows whose 'host' column says Homo sapiens and
    whose 'qualitative'/'measure' column starts with 'Positive'. If it fails, pre-extract
    peptides to a one-per-line text file.
    """
    import csv

    p = Path(path)
    text = p.read_text()
    first = text.splitlines()[0] if text else ""
    if "," not in first and "\t" not in first:
        return {ln.strip().upper() for ln in text.splitlines() if ln.strip().isalpha()}
    rows = list(csv.reader(text.splitlines()))
    if len(rows) < 3:
        return set()
    merged = [f"{a} {b}".strip().lower() for a, b in zip(rows[0], rows[1])]
    pep_col = next((i for i, h in enumerate(merged) if "epitope" in h and "name" in h), None)
    if pep_col is None:
        raise ToolFailed(f"{p.name}: cannot find an 'Epitope Name' column; pre-extract peptides to a text file")
    host_col = next((i for i, h in enumerate(merged) if "host" in h and "name" in h), None)
    qual_col = next((i for i, h in enumerate(merged) if "qualitative" in h or "measure" in h), None)
    out = set()
    for r in rows[2:]:
        if len(r) <= pep_col:
            continue
        pep = r[pep_col].strip().upper()
        if not pep.isalpha():
            continue
        if host_col is not None and len(r) > host_col and "homo sapiens" not in r[host_col].lower():
            continue
        if qual_col is not None and len(r) > qual_col and not r[qual_col].strip().lower().startswith("positive"):
            continue
        out.add(pep)
    return out
