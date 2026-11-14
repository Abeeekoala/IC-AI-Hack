"""S0 - taxonomy resolution: pathogen name -> one unambiguous NCBI taxid.

Two failure modes are handled explicitly instead of silently (README S0):

* **Synonymy** - many bacteria have been renamed; old names are resolved
  through NCBI synonyms and the artifact records *which* name matched and
  whether it differs from the current scientific name.
* **Ambiguity** - "Neisseria" is a genus, not a species.  A name that is too
  broad, matches several taxa, or is only a fuzzy/spelling match is returned as
  a candidate list for the user to confirm.  The first hit is never taken
  silently, and a failed lookup returns the nearest suggestions.

Matching tiers (each cached; later tiers run only if earlier ones find nothing)::

    1 exact       esearch  "<name>"[All Names]                      (NCBI, authoritative)
    2 alias       small curated table (meningococcus, TB, MRSA ...) + config taxonomy.aliases
    3 abbrev      "N. meningitidis", "Staph aureus" -> expand against a list of pathogen genera
    4 fuzzy       UniProt word-level taxonomy search, verified through NCBI efetch and scored locally
    5 spelling    NCBI espell correction -> suggestions only, never auto-accepted

The output also classifies the organism (domain, Gram stain) because S2 needs
it: VFDB is bacterial, and PSORTb needs a Gram flag.
"""
from __future__ import annotations

import difflib
import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Callable

from .artifacts import Artifact, RunStore
from .cache import Cache, CacheMiss, cache_from_config
from .config import Config
from .http import HttpClient, HttpError, client_from_config
from .uniprot import UniProtClient

NCBI_BASE = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/"

SPECIES_OR_BELOW_RANKS = {
    "species", "subspecies", "strain", "isolate", "serotype", "serogroup", "biotype", "pathovar",
    "forma specialis", "varietas", "forma", "genotype", "subvariety", "morph",
}
# OtherNames classes that are not names a user would type
_IGNORED_NAME_CLASSES = {"type material", "authority"}
# Classes meaning "an older / alternative scientific name" (an acronym or common name is not a renaming)
SYNONYM_CLASSES = {"synonym", "genbank synonym", "equivalent name", "includes", "in-part", "anamorph",
                   "genbank anamorph", "teleomorph", "misnomer", "misspelling"}

# --- curated knowledge (kept deliberately small; extend via config taxonomy.aliases) ---
BUILTIN_ALIASES = {
    "meningococcus": "Neisseria meningitidis",
    "gonococcus": "Neisseria gonorrhoeae",
    "pneumococcus": "Streptococcus pneumoniae",
    "staph aureus": "Staphylococcus aureus",
    "mrsa": "Staphylococcus aureus",
    "gbs": "Streptococcus agalactiae",
    "tb": "Mycobacterium tuberculosis",
    "c diff": "Clostridioides difficile",
    "c difficile": "Clostridioides difficile",
    "hib": "Haemophilus influenzae",
    "covid-19": "Severe acute respiratory syndrome coronavirus 2",
    "covid 19": "Severe acute respiratory syndrome coronavirus 2",
    "sars cov 2": "Severe acute respiratory syndrome coronavirus 2",
    "sars-cov2": "Severe acute respiratory syndrome coronavirus 2",
    "plague": "Yersinia pestis",
    "anthrax": "Bacillus anthracis",
    "cholera": "Vibrio cholerae",
}
KNOWN_GENERA = (
    "Acinetobacter Actinomyces Anaplasma Aspergillus Bacillus Bacteroides Bartonella Bifidobacterium Bordetella "
    "Borrelia Brucella Burkholderia Campylobacter Candida Chlamydia Chlamydophila Clostridioides Clostridium "
    "Corynebacterium Coxiella Cryptococcus Cryptosporidium Ehrlichia Enterobacter Enterococcus Escherichia "
    "Francisella Fusobacterium Giardia Haemophilus Helicobacter Klebsiella Lactobacillus Legionella Leishmania "
    "Leptospira Listeria Moraxella Mycobacterium Mycoplasma Neisseria Nocardia Pasteurella Plasmodium Porphyromonas "
    "Prevotella Proteus Pseudomonas Rickettsia Salmonella Schistosoma Serratia Shigella Staphylococcus "
    "Stenotrophomonas Streptococcus Toxoplasma Treponema Trypanosoma Vibrio Yersinia"
).split()

# --- Gram-stain heuristics (lineage-based; documented, overridable via --gram) ---
_NEGATIVE = {
    "pseudomonadota", "proteobacteria", "bacteroidota", "bacteroidetes", "fusobacteriota", "fusobacteria",
    "spirochaetota", "spirochaetes", "chlamydiota", "chlamydiae", "campylobacterota", "planctomycetota",
    "planctomycetes", "verrucomicrobiota", "verrucomicrobia", "cyanobacteriota", "cyanobacteria", "acidobacteriota",
    "acidobacteria", "aquificota", "aquificae", "thermotogota", "thermotogae", "negativicutes", "synergistota",
    "deferribacterota", "fibrobacterota", "gemmatimonadota", "nitrospirota", "chlorobiota", "elusimicrobiota",
    "bdellovibrionota", "lentisphaerota", "myxococcota", "thermodesulfobacteriota", "desulfobacterota",
}
_POSITIVE = {"bacillota", "firmicutes", "actinomycetota", "actinobacteria"}
# PSORTb's own USAGE notes for organisms whose Gram stain does not match their cell envelope:
#   "Positive with outer membrane" (Deinococcus, Mycobacterium, Veillonellaceae)  -> run with -n
#   "Negative without outer membrane" (Tenericutes, e.g. Mycoplasma)              -> run with -p --x-skip-localization=Cellwall
# (Veillonellaceae are Negativicutes, already covered by _NEGATIVE.)  Mycobacteriaceae covers the genera split off Mycobacterium.
_POSITIVE_WITH_OM = {"mycobacteriaceae", "deinococcota", "deinococcus-thermus"}
_NEGATIVE_NO_OM = {"mollicutes", "mycoplasmatota", "tenericutes"}
SKIP_CELLWALL = "--x-skip-localization=Cellwall"


class TaxonomyError(Exception):
    pass


class NeedsUserInput(Exception):
    """Raised by :func:`require_resolved` when S0 could not pick a single taxon."""

    def __init__(self, artifact: Artifact):
        self.artifact = artifact
        res = artifact.payload.get("resolution", {})
        super().__init__(res.get("message") or "taxonomy resolution needs user input")


# ----------------------------------------------------------------------------
# name utilities
# ----------------------------------------------------------------------------
_AUTHORITY_RE = re.compile(r"\s*\([^)]*\b\d{4}\b[^)]*\).*$")


def strip_authority(name: str) -> str:
    """'Clostridium difficile (Hall and O'Toole 1935) Prevot 1938' -> 'Clostridium difficile'."""
    name = name.strip().strip('"')
    return _AUTHORITY_RE.sub("", name).strip().strip('"')


def norm_name(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).lower().replace('"', " ")
    text = re.sub(r"[^\w\s.\-/]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_abbreviation_of(short: str, full: str) -> bool:
    s = short.rstrip(".")
    return bool(s) and len(s) < len(full) and full.startswith(s)


def score_name(query: str, name: str) -> float:
    """Similarity in [0, 1] between a normalised query and an NCBI name."""
    q = norm_name(query)
    n = norm_name(strip_authority(name))
    if not q or not n:
        return 0.0
    if q == n:
        return 1.0
    qt, nt = q.split(), n.split()
    if len(qt) == len(nt) and len(qt) >= 2 and _is_abbreviation_of(qt[0], nt[0]) and qt[1:] == nt[1:]:
        return 0.9
    if n.startswith(q + " ") or q.startswith(n + " "):
        return 0.75
    return round(0.7 * difflib.SequenceMatcher(None, q, n).ratio(), 4)


def best_name_match(query: str, taxon: dict) -> tuple[float, str, str]:
    """Best (score, matched_name, class) over the scientific name and every other name."""
    best = (score_name(query, taxon["scientific_name"]), taxon["scientific_name"], "scientific name")
    for other in taxon.get("other_names", []):
        if other["class"] in _IGNORED_NAME_CLASSES:
            continue
        s = score_name(query, other["name"])
        if s > best[0]:
            best = (s, other["name"], other["class"])
    return best


# ----------------------------------------------------------------------------
# organism classification
# ----------------------------------------------------------------------------
def classify_organism(taxon: dict) -> dict:
    names = [(e["name"], e["rank"]) for e in taxon.get("lineage", [])] + [(taxon["scientific_name"], taxon["rank"])]
    lowered = [n.lower() for n, _ in names]
    division = (taxon.get("division") or "").lower()
    if "bacteria" in lowered:
        domain = "bacteria"
    elif "archaea" in lowered:
        domain = "archaea"
    elif "eukaryota" in lowered:
        domain = "eukaryote"
    elif "viruses" in lowered or "viroids" in lowered or division in ("viruses", "phages"):
        domain = "virus"
    else:
        domain = "unknown"

    gram, basis = "not_applicable", f"domain is {domain}; Gram stain / PSORTb apply to bacteria and archaea only"
    flag: str | None = None
    extra: list[str] = []
    if domain == "archaea":
        gram, flag, basis = "archaea", "-a", "domain Archaea -> PSORTb archaeal mode"
    elif domain == "bacteria":
        gram, basis = "unknown", "no lineage rule matched; set --gram negative|positive to override"
        for name in reversed([n for n, _ in names]):  # most specific first
            low = name.lower()
            if low in _NEGATIVE_NO_OM:
                gram, flag, extra = "negative_without_outer_membrane", "-p", [SKIP_CELLWALL]
                basis = f"lineage taxon {name}: Gram-negative without an outer membrane -> PSORTb {flag} {SKIP_CELLWALL}"
                break
            if low in _POSITIVE_WITH_OM:
                gram, flag = "positive_with_outer_membrane", "-n"
                basis = f"lineage taxon {name}: Gram-positive with an outer membrane -> PSORTb {flag}"
                break
            if low in _NEGATIVE:
                gram, flag, basis = "negative", "-n", f"lineage taxon {name} -> Gram-negative"
                break
            if low in _POSITIVE:
                gram, flag, basis = "positive", "-p", f"lineage taxon {name} -> Gram-positive"
                break
    return {
        "domain": domain,
        "gram_stain": gram,
        "gram_basis": basis,
        "psortb_flag": flag,
        "psortb_extra_args": extra,
        "virulence_db_applicable": domain == "bacteria",   # VFDB is a bacterial database
        "localisation_applicable": domain in ("bacteria", "archaea"),
    }


# ----------------------------------------------------------------------------
# NCBI E-utilities client (cache-first)
# ----------------------------------------------------------------------------
class NcbiClient:
    def __init__(self, http: HttpClient, cache: Cache, tool: str = "vaxforge", email: str | None = None,
                 api_key: str | None = None):
        self.http, self.cache = http, cache
        self.common = {"tool": tool}
        if email:
            self.common["email"] = email
        if api_key:
            self.common["api_key"] = api_key

    def _call(self, endpoint: str, params: dict, ext: str) -> bytes:
        key = self.cache.key(endpoint, sorted(params.items()))
        path = self.cache.path("ncbi", endpoint.replace(".fcgi", ""), f"{key}.{ext}")
        if self.cache.usable(path):
            return path.read_bytes()
        self.cache.require(path, f"NCBI {endpoint} {params}")
        resp = self.http.get(NCBI_BASE + endpoint, params={**params, **self.common})
        body = resp.body
        if ext == "json" and b'"error"' in body[:300]:
            raise TaxonomyError(f"NCBI returned an error: {body[:200].decode('utf-8', 'replace')}")
        self.cache.put_bytes(path, body, {"url": resp.url.split("?")[0], "params": params})
        return body

    def esearch(self, term: str, retmax: int = 20) -> tuple[int, list[int]]:
        import json
        body = self._call("esearch.fcgi", {"db": "taxonomy", "term": term, "retmax": retmax, "retmode": "json"}, "json")
        result = json.loads(body.decode("utf-8")).get("esearchresult", {})
        return int(result.get("count", 0)), [int(i) for i in result.get("idlist", [])]

    def espell(self, term: str) -> str | None:
        body = self._call("espell.fcgi", {"db": "taxonomy", "term": term}, "xml")
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            return None
        corrected = (root.findtext("CorrectedQuery") or "").strip()
        return corrected or None

    def efetch(self, ids: list[int]) -> list[dict]:
        """Taxon records for ``ids`` (unknown ids are omitted); each taxon is cached on its own."""
        out: dict[int, dict] = {}
        missing: list[int] = []
        for i in dict.fromkeys(ids):
            path = self.cache.path("ncbi", "taxon", f"{i}.xml")
            if self.cache.usable(path):
                out[i] = parse_taxa_xml(path.read_bytes())[0]
            else:
                missing.append(i)
        for start in range(0, len(missing), 50):
            batch = missing[start:start + 50]
            for i in batch:
                self.cache.require(self.cache.path("ncbi", "taxon", f"{i}.xml"), f"NCBI taxonomy record {i}")
            resp = self.http.get(NCBI_BASE + "efetch.fcgi",
                                 params={"db": "taxonomy", "id": ",".join(map(str, batch)), "retmode": "xml", **self.common})
            root = ET.fromstring(resp.body)
            for tax in root.findall("Taxon"):
                tid = int(tax.findtext("TaxId"))
                blob = b"<TaxaSet>" + ET.tostring(tax) + b"</TaxaSet>"
                self.cache.put_bytes(self.cache.path("ncbi", "taxon", f"{tid}.xml"), blob, {"taxid": tid})
                out[tid] = parse_taxa_xml(blob)[0]
        return [out[i] for i in dict.fromkeys(ids) if i in out]

    def count_subtree(self, taxid: int) -> int:
        count, _ = self.esearch(f"txid{taxid}[Subtree]", retmax=0)
        return count


_NAME_TAG_CLASS = {
    "Synonym": "synonym", "GenbankSynonym": "genbank synonym", "CommonName": "common name",
    "GenbankCommonName": "genbank common name", "Acronym": "acronym", "GenbankAcronym": "genbank acronym",
    "EquivalentName": "equivalent name", "Anamorph": "anamorph", "GenbankAnamorph": "genbank anamorph",
    "Teleomorph": "teleomorph", "Includes": "includes", "Inpart": "in-part", "Misnomer": "misnomer",
    "Misspelling": "misspelling",
}


def parse_taxa_xml(xml: bytes) -> list[dict]:
    root = ET.fromstring(xml)
    taxa = []
    for t in root.findall("Taxon"):
        others: list[dict] = []
        on = t.find("OtherNames")
        if on is not None:
            for child in on:
                if child.tag == "Name":
                    cls = (child.findtext("ClassCDE") or "").strip().lower()
                    name = (child.findtext("DispName") or "").strip()
                elif child.tag in _NAME_TAG_CLASS:
                    cls, name = _NAME_TAG_CLASS[child.tag], (child.text or "").strip()
                else:
                    continue
                if name:
                    others.append({"name": name, "class": cls})
        lineage = [
            {"taxid": int(e.findtext("TaxId")), "name": e.findtext("ScientificName") or "", "rank": e.findtext("Rank") or "no rank"}
            for e in t.findall("LineageEx/Taxon")
        ]
        taxa.append({
            "taxid": int(t.findtext("TaxId")),
            "scientific_name": (t.findtext("ScientificName") or "").strip(),
            "rank": (t.findtext("Rank") or "no rank").strip(),
            "division": (t.findtext("Division") or "").strip(),
            "parent_taxid": int(t.findtext("ParentTaxId")) if t.findtext("ParentTaxId") else None,
            "lineage": lineage,
            "other_names": others,
        })
    return taxa


# ----------------------------------------------------------------------------
# resolution
# ----------------------------------------------------------------------------
@dataclass
class Resolution:
    status: str                      # resolved | needs_user_input | not_found
    reason: str | None               # ambiguous | rank_too_broad | fuzzy_match | spelling_suggestion | not_found
    method: str                      # exact | alias | abbreviation | fuzzy | spelling | taxid | user_choice
    query: str
    message: str
    taxon: dict | None = None        # full record of the selected taxon (resolved only)
    candidates: list[dict] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    match: dict | None = None        # {score, matched_name, matched_name_class}


def species_of(taxon: dict) -> tuple[int | None, str | None]:
    if taxon["rank"] == "species":
        return taxon["taxid"], taxon["scientific_name"]
    for e in reversed(taxon.get("lineage", [])):
        if e["rank"] == "species":
            return e["taxid"], e["name"]
    return None, None


def at_or_below_species(taxon: dict) -> bool:
    return taxon["rank"] in SPECIES_OR_BELOW_RANKS or species_of(taxon)[0] is not None


def candidate_view(taxon: dict, query: str | None, source: str, **extra) -> dict:
    sp_id, sp_name = species_of(taxon)
    view = {
        "taxid": taxon["taxid"],
        "scientific_name": taxon["scientific_name"],
        "rank": taxon["rank"],
        "division": taxon["division"],
        "species_taxid": sp_id,
        "species_name": sp_name,
        "at_or_below_species": at_or_below_species(taxon),
        "source": source,
    }
    if query:
        score, name, cls = best_name_match(query, taxon)
        view.update({"match_score": round(score, 4), "matched_name": name, "matched_name_class": cls})
    view.update(extra)
    return view


class Resolver:
    def __init__(self, ncbi: NcbiClient, uniprot: UniProtClient | None, config: Config):
        self.ncbi, self.uniprot, self.config = ncbi, uniprot, config
        self.retmax = int(config.get("taxonomy.search_retmax", 20))
        self.max_children = int(config.get("taxonomy.genus_candidates", 15))
        self.fuzzy = bool(config.get("taxonomy.fuzzy", True))
        self.aliases = {norm_name(k): v for k, v in {**BUILTIN_ALIASES, **(config.get("taxonomy.aliases") or {})}.items()}

    # -- public --------------------------------------------------------------
    def resolve(self, query: str | int | None = None, taxid: int | None = None, method_override: str | None = None) -> Resolution:
        if taxid is None and isinstance(query, int):
            taxid, query = query, str(query)
        if taxid is None and query is not None:
            m = re.fullmatch(r"\s*(?:tax(?:on)?id[:\s]*)?(\d+)\s*", str(query), re.I)
            if m:
                taxid = int(m.group(1))
        q = (str(query) if query is not None else str(taxid)).strip()
        if not q:
            raise TaxonomyError("empty pathogen name")

        if taxid is not None:
            return self._resolve_taxid(taxid, q, method_override or "taxid")
        return self._resolve_name(q)

    # -- by taxid -------------------------------------------------------------
    def _resolve_taxid(self, taxid: int, q: str, method: str) -> Resolution:
        recs = self.ncbi.efetch([taxid])
        if not recs:
            return Resolution("not_found", "not_found", method, q, f"NCBI Taxonomy has no record for taxid {taxid}.")
        tax = recs[0]
        if not at_or_below_species(tax):
            return self._too_broad(tax, q, method)
        return self._finish_resolved(tax, q, method, match=None)

    # -- by name ---------------------------------------------------------------
    def _resolve_name(self, q: str) -> Resolution:
        # 1. exact
        ids = self._exact_ids([q])
        if ids:
            return self._decide(ids, q, "exact")
        # 2. alias
        alias_target = self.aliases.get(norm_name(q))
        if alias_target:
            ids = self._exact_ids([alias_target])
            if ids:
                res = self._decide(ids, q, "alias")
                res.notes.append(f"'{q}' is a curated alias for '{alias_target}'.")
                return res
        # 3. abbreviation
        expansions = self._abbreviation_expansions(q)
        if expansions:
            ids = self._exact_ids(expansions)
            if ids:
                res = self._decide(ids, q, "abbreviation")
                res.notes.append("Abbreviated genus expanded against a list of pathogen genera: " + ", ".join(expansions))
                return res
        # 4. fuzzy (UniProt word search verified through NCBI)
        if self.fuzzy and self.uniprot is not None:
            cands = self._fuzzy_candidates(q)
            if cands:
                return self._decide_fuzzy(cands, q)
        # 5. spelling
        return self._spelling_or_not_found(q)

    def _exact_ids(self, names: list[str]) -> list[int]:
        term = " OR ".join(f'"{n}"[All Names]' for n in names)
        _, ids = self.ncbi.esearch(term, self.retmax)
        return ids

    def _abbreviation_expansions(self, q: str) -> list[str]:
        toks = norm_name(q).split()
        if len(toks) < 2:
            return []
        first = toks[0].rstrip(".")
        if not (1 <= len(first) <= 6) or not first.isalpha():
            return []
        rest = " ".join(toks[1:])
        out = [f"{g} {rest}" for g in KNOWN_GENERA if g.lower().startswith(first) and g.lower() != first]
        return out[:8]

    def _fuzzy_candidates(self, q: str) -> list[tuple[dict, float, str, str]]:
        try:
            hits = self.uniprot.search_taxonomy(q, size=25)
        except CacheMiss:
            raise
        except Exception:  # the fuzzy tier is best-effort; fall through to spelling suggestions
            return []
        ids = [int(h["taxonId"]) for h in hits]
        recs = self.ncbi.efetch(ids[:25]) if ids else []
        # lift strains to their species, verify against NCBI
        species_ids: list[int] = []
        for r in recs:
            sp_id, _ = species_of(r)
            species_ids.append(sp_id if sp_id else r["taxid"])
        species_recs = self.ncbi.efetch(list(dict.fromkeys(species_ids))) if species_ids else []
        scored = []
        for r in species_recs:
            s, name, cls = best_name_match(q, r)
            if s >= 0.55:
                scored.append((r, s, name, cls))
        scored.sort(key=lambda t: -t[1])
        return scored[:10]

    def _spelling_or_not_found(self, q: str) -> Resolution:
        suggestions: list[dict] = []
        corrected = None
        try:
            corrected = self.ncbi.espell(q)
        except CacheMiss:
            raise
        except Exception:
            corrected = None
        if corrected and norm_name(corrected) != norm_name(q):
            ids = self._exact_ids([corrected])
            for rec in self.ncbi.efetch(ids):
                suggestions.append(candidate_view(rec, corrected, "ncbi_spelling_suggestion"))
        if suggestions:
            return Resolution("needs_user_input", "spelling_suggestion", "spelling", q,
                              f"No taxon is named '{q}'. Did you mean '{corrected}'? Confirm one of the suggestions.",
                              candidates=suggestions)
        return Resolution("not_found", "not_found", "none", q,
                          f"No taxon matches '{q}'. Try the full scientific name, or pass --taxid.",
                          candidates=[])

    # -- decisions --------------------------------------------------------------------
    def _decide(self, ids: list[int], q: str, method: str) -> Resolution:
        recs = self.ncbi.efetch(ids)
        if not recs:
            return Resolution("not_found", "not_found", method, q, f"NCBI returned ids {ids} but no records.")
        cands = [candidate_view(r, q, f"ncbi_{method}") for r in recs]
        if len(recs) == 1:
            tax = recs[0]
            if not at_or_below_species(tax):
                return self._too_broad(tax, q, method)
            return self._finish_resolved(tax, q, method, match=cands[0])
        specific = [c for c in cands if c["at_or_below_species"]]
        cands.sort(key=lambda c: (-c.get("match_score", 0), not c["at_or_below_species"]))
        return Resolution("needs_user_input", "ambiguous", method, q,
                          f"'{q}' matches {len(recs)} taxa ({len(specific)} at species level or below); choose one.",
                          candidates=cands)

    def _decide_fuzzy(self, scored: list[tuple[dict, float, str, str]], q: str) -> Resolution:
        cands = [candidate_view(r, q, "fuzzy_uniprot+ncbi") for r, *_ in scored]
        exact = [(r, s, n, c) for r, s, n, c in scored if s >= 0.999]
        if len(exact) == 1 and exact[0][0]["scientific_name"] and at_or_below_species(exact[0][0]):
            # old/synonym name that matches exactly one species -> that is synonymy, not a guess
            res = self._finish_resolved(exact[0][0], q, "fuzzy", match=cands[0])
            res.notes.append("Matched through a non-current name; confirm this is the intended organism.")
            return res
        return Resolution("needs_user_input", "fuzzy_match", "fuzzy", q,
                          f"No exact taxonomy match for '{q}'; these are the closest taxa. Choose one.",
                          candidates=cands)

    def _too_broad(self, tax: dict, q: str, method: str) -> Resolution:
        children = self._broad_children(tax)
        total = children.pop("_total", len(children.get("items", [])))
        items = children["items"]
        msg = (f"'{q}' resolves to {tax['scientific_name']} (taxid {tax['taxid']}, rank {tax['rank']}), which is too "
               f"broad to pick a proteome. Choose a species"
               + (f" (showing {len(items)} of {total})." if items else "; none could be listed - give a species name or --taxid."))
        return Resolution("needs_user_input", "rank_too_broad", method, q, msg, candidates=items,
                          notes=[f"Broad taxon: {tax['scientific_name']} ({tax['rank']}), taxid {tax['taxid']}"])

    def _broad_children(self, tax: dict) -> dict:
        items: list[dict] = []
        total = 0
        if self.uniprot is not None:
            try:
                protos = self.uniprot.search_proteomes(f"taxonomy_id:{tax['taxid']} AND reference:true", size=100)
            except CacheMiss:
                raise
            except Exception:
                protos = []
            seen: dict[int, dict] = {}
            for p in protos:
                sp = next((e for e in reversed(p.get("taxonLineage", [])) if e.get("rank") == "species"), None)
                sid = sp["taxonId"] if sp else p["taxonomy"]["taxonId"]
                sname = sp["scientificName"] if sp else p["taxonomy"]["scientificName"]
                seen.setdefault(sid, {
                    "taxid": sid, "scientific_name": sname, "rank": "species", "division": tax.get("division", ""),
                    "species_taxid": sid, "species_name": sname, "at_or_below_species": True,
                    "source": "uniprot_reference_proteomes", "uniprot_reference_proteome": p["id"],
                })
            ordered = sorted(seen.values(), key=lambda c: c["scientific_name"].lower())
            total = len(ordered)
            items = ordered[: self.max_children]
        if not items:  # NCBI fallback: newest species under the taxon
            count, ids = self.ncbi.esearch(f"txid{tax['taxid']}[Subtree] AND species[Rank]", self.max_children)
            total = count
            items = [candidate_view(r, None, "ncbi_subtree") for r in self.ncbi.efetch(ids)]
        return {"items": items, "_total": total}

    def _finish_resolved(self, tax: dict, q: str, method: str, match: dict | None) -> Resolution:
        if match is None:
            score, name, cls = best_name_match(q, tax)
            match = {"match_score": score, "matched_name": name, "matched_name_class": cls}
        notes: list[str] = []
        sp_id, sp_name = species_of(tax)
        sci = tax["scientific_name"]
        if (norm_name(strip_authority(match.get("matched_name", sci))) != norm_name(sci)
                and (match.get("match_score") or 0) >= 0.999 and method not in ("taxid", "user_choice")):
            notes.append(f"'{q}' matched {match.get('matched_name_class')} '{strip_authority(match['matched_name'])}'; "
                         f"the current scientific name is '{sci}'.")
        if tax["rank"] == "species":
            try:
                n = self.ncbi.count_subtree(tax["taxid"]) - 1
            except CacheMiss:
                raise
            except Exception:
                n = None
            if n and n > 0:
                notes.append(f"{sci} has {n} taxa below species level (strains etc.); S1 uses the UniProt reference "
                             f"proteome as the representative. Pass --taxid to pin a specific strain.")
        return Resolution("resolved", None, method, q, f"Resolved '{q}' to {sci} (taxid {tax['taxid']}, {tax['rank']}).",
                          taxon=tax, candidates=[candidate_view(tax, q, f"ncbi_{method}")], notes=notes,
                          match={"score": match.get("match_score"), "matched_name": match.get("matched_name"),
                                 "matched_name_class": match.get("matched_name_class")})


# ----------------------------------------------------------------------------
# stage entrypoint
# ----------------------------------------------------------------------------
def build_services(config: Config, transport=None, http: HttpClient | None = None, cache: Cache | None = None):
    http = http or client_from_config(config, transport)
    cache = cache or cache_from_config(config)
    return http, cache


def _resolution_payload(res: Resolution, original_query: str, chooser_used: bool) -> dict:
    tax = res.taxon
    payload: dict = {
        "query": original_query,
        "resolution": {
            "status": res.status,
            "reason": res.reason,
            "method": res.method,
            "message": res.message,
            "notes": res.notes,
            "user_confirmed": chooser_used,
            "matched_name": (res.match or {}).get("matched_name"),
            "matched_name_class": (res.match or {}).get("matched_name_class"),
            "match_score": (res.match or {}).get("score"),
        },
        "taxon": None,
        "organism": None,
        "candidates": res.candidates,
    }
    if tax is not None:
        sp_id, sp_name = species_of(tax)
        sci = tax["scientific_name"]
        matched = payload["resolution"]["matched_name"]
        payload["resolution"]["synonym_matched"] = bool(
            matched and norm_name(strip_authority(matched)) != norm_name(sci)
            and (payload["resolution"]["match_score"] or 0) >= 0.999
            and payload["resolution"]["matched_name_class"] in SYNONYM_CLASSES
        )
        payload["taxon"] = {
            "taxid": tax["taxid"],
            "scientific_name": sci,
            "rank": tax["rank"],
            "division": tax["division"],
            "species_taxid": sp_id,
            "species_name": sp_name,
            "lineage": tax["lineage"],
            "other_names": [o for o in tax["other_names"] if o["class"] not in _IGNORED_NAME_CLASSES],
        }
        payload["organism"] = classify_organism(tax)
    return payload


def resolve_pathogen(config: Config, pathogen: str | None = None, taxid: int | None = None,
                     chooser: Callable[[Resolution], int | None] | None = None,
                     http: HttpClient | None = None, cache: Cache | None = None, transport=None) -> tuple[Resolution, bool]:
    """Resolve without writing an artifact. Returns (resolution, user_confirmed)."""
    http, cache = build_services(config, transport, http, cache)
    ncbi_cfg = config.get("network.ncbi") or {}
    ncbi = NcbiClient(http, cache, tool=ncbi_cfg.get("tool") or "vaxforge", email=ncbi_cfg.get("email"),
                      api_key=ncbi_cfg.get("api_key"))
    uni = UniProtClient(http, cache)
    resolver = Resolver(ncbi, uni, config)
    if pathogen is None and taxid is None:
        raise TaxonomyError("give a pathogen name or --taxid")
    res = resolver.resolve(pathogen, taxid=taxid)
    confirmed = False
    for _ in range(3):
        if res.status == "resolved" or chooser is None or res.status == "not_found":
            break
        choice = chooser(res)
        if choice is None:
            break
        confirmed = True
        original = res.query
        res = resolver.resolve(taxid=int(choice), query=original, method_override="user_choice")
        res.query = original
    return res, confirmed


def run(store: RunStore, config: Config, pathogen: str | None = None, taxid: int | None = None,
        chooser: Callable[[Resolution], int | None] | None = None, http: HttpClient | None = None,
        cache: Cache | None = None, transport=None) -> Artifact:
    """S0: write ``s0_taxonomy.json``. Status is ``ok`` only when exactly one taxon was selected."""
    res, confirmed = resolve_pathogen(config, pathogen, taxid, chooser, http, cache, transport)
    original = pathogen if pathogen is not None else str(taxid)
    payload = _resolution_payload(res, original, confirmed)
    status = {"resolved": "ok", "needs_user_input": "needs_user_input"}.get(res.status, "failed")
    warnings = list(res.notes)
    if status != "ok":
        warnings.insert(0, res.message)
    return store.write_artifact(
        "s0", payload,
        params={"pathogen": pathogen, "taxid": taxid, "fuzzy": bool(config.get("taxonomy.fuzzy", True)),
                "aliases_extra": sorted((config.get("taxonomy.aliases") or {}).items())},
        tools={"ncbi-eutils": "taxonomy", "uniprot-rest": "taxonomy+proteomes"},
        status=status, warnings=warnings,
    )


def require_resolved(artifact: Artifact) -> dict:
    """Return the S0 payload, or raise :class:`NeedsUserInput` if no single taxon was chosen."""
    if artifact.status != "ok":
        raise NeedsUserInput(artifact)
    return artifact.payload


def format_candidates(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates, 1):
        extra = f"  matched '{c['matched_name']}' ({c['matched_name_class']})" if c.get("matched_name") else ""
        lines.append(f"  {i:>2}. taxid {c['taxid']:<9} {c['scientific_name']}  [{c['rank']}]{extra}")
    return "\n".join(lines)
