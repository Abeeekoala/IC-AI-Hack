"""UniProt REST client (proteomes, taxonomy, FASTA streaming) with cache-first JSON."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from .cache import Cache, CacheMiss, atomic_write_text, utcnow_iso
from .fasta import count_records
from .http import HttpClient, HttpError, build_url

BASE = "https://rest.uniprot.org"


class UniProtError(Exception):
    pass


class UniProtClient:
    def __init__(self, http: HttpClient, cache: Cache, base: str = BASE):
        self.http = http
        self.cache = cache
        self.base = base.rstrip("/")

    # -- cached JSON ----------------------------------------------------------
    def _json(self, endpoint: str, params: Mapping[str, Any], subdir: str) -> tuple[Any, dict, bool]:
        """GET ``endpoint`` as JSON; returns (data, headers-of-interest, cache_hit)."""
        url = build_url(self.base + endpoint, params)
        path = self.cache.path("uniprot", "json", subdir, self.cache.key(url) + ".json")
        if self.cache.usable(path):
            meta = self.cache.read_meta(path)
            return json.loads(path.read_text(encoding="utf-8")), {"release": meta.get("release"), "total": meta.get("total")}, True
        self.cache.require(path, f"UniProt query {url}")
        resp = self.http.get(self.base + endpoint, params=params)
        info = {"release": resp.headers.get("x-uniprot-release"), "total": resp.headers.get("x-total-results")}
        self.cache.put_bytes(path, resp.body, {"url": url, **info})
        return resp.json(), info, False

    # -- proteomes -----------------------------------------------------------------
    def search_proteomes(self, query: str, size: int = 50) -> list[dict]:
        data, _, _ = self._json("/proteomes/search", {"query": query, "format": "json", "size": size}, "proteomes_search")
        return list(data.get("results", []))

    def get_proteome(self, upid: str) -> dict:
        try:
            data, _, _ = self._json(f"/proteomes/{upid}", {"format": "json"}, "proteome")
        except HttpError as exc:
            if exc.status in (400, 404):
                raise UniProtError(f"UniProt has no proteome {upid!r}") from exc
            raise
        return data

    # -- taxonomy -------------------------------------------------------------------------
    def search_taxonomy(self, query: str, size: int = 25) -> list[dict]:
        data, _, _ = self._json("/taxonomy/search", {"query": query, "format": "json", "size": size}, "taxonomy_search")
        return list(data.get("results", []))

    # -- FASTA --------------------------------------------------------------------------------
    def fetch_fasta(self, query: str, dest: Path, what: str, endpoint: str = "uniprotkb") -> dict:
        """Cache-first download of ``/<endpoint>/stream`` FASTA for ``query`` (endpoint: uniprotkb | uniparc).

        Returns ``{"cache_hit", "release", "source_url", "retrieved_at", "n_sequences", "source"}``.
        """
        path = f"/{endpoint}/stream"
        url = build_url(self.base + path, {"query": query, "format": "fasta"})
        dest = Path(dest)
        if self.cache.usable(dest):
            meta = self.cache.read_meta(dest)
            n = meta.get("n_sequences")
            if n is None:
                n = count_records(dest)
            return {"cache_hit": True, "release": meta.get("release"), "source_url": meta.get("url", url),
                    "retrieved_at": meta.get("retrieved_at"), "n_sequences": n, "source": meta.get("source", endpoint)}
        self.cache.require(dest, what)
        res = self.http.download(self.base + path, dest, params={"query": query, "format": "fasta"})
        n = count_records(dest)
        if n == 0:
            dest.unlink(missing_ok=True)
            raise UniProtError(f"{endpoint} returned no sequences for {query!r}")
        meta = {"url": url, "query": query, "release": res.headers.get("x-uniprot-release"), "source": endpoint,
                "retrieved_at": utcnow_iso(), "sha256": res.sha256, "bytes": res.bytes, "n_sequences": n}
        self.cache.write_meta(dest, meta)
        return {"cache_hit": False, "release": meta["release"], "source_url": url,
                "retrieved_at": meta["retrieved_at"], "n_sequences": n, "source": endpoint}

    def fetch_proteome_fasta(self, upid: str, dest: Path, what: str) -> dict:
        """Sequences of proteome ``upid``: UniProtKB first, then UniParc.

        UniProt keeps only reference proteomes in UniProtKB; the other strains' proteomes (declared protein
        count > 0, no UniProtKB entries) live in UniParc.  UniParc FASTA headers are ``>UPI... status=active``
        - sequences only, no protein names.
        """
        try:
            return self.fetch_fasta(f"(proteome:{upid})", dest, what)
        except UniProtError:
            pass
        try:
            return self.fetch_fasta(f"upid:{upid}", dest, what, endpoint="uniparc")
        except HttpError as exc:
            if exc.status in (400, 404):
                raise UniProtError(f"proteome {upid} has no sequences in UniProtKB or UniParc") from exc
            raise
