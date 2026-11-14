"""Shared fakes: a tiny UniProt/VFDB/Protegen 'server' and a BLAST stand-in."""
from __future__ import annotations

import gzip
import json
import urllib.parse
from pathlib import Path

from vaxforge.testing import FakeTransport

SEQ_A = "MKKTAIAIAVALAGFATVAQAAPKDNTWYTGAKLGWSQYHDTGFINNNGPTHENQLGAGAFGGYQVNPYVGFEMGYDWLGRMPYKGSVENGAYKAQGVQLTAKLGYPITDDLDIYTRLGGMVWRADTKSNVYGKNHDTGVSPVFAGGVEYAITPEIATRLEYQWTNNIGDAHTIGTRPDNGMLSLGVSYRFGQEDAAPVVAPAPAPAPEVQTKHFTLKSDVLFNFNKATLKPEGQAALDQLYSQLSNLDPKDGSVVVLGYTDRIGSDAYNQGLSERRAQSVVDYLVAKGIPAGKISARGMGESNPVTGNTCDNVKQRAALIDCLAPDRRVEIEVKGIKDVVTQPQA"
SEQ_B = "MNKIYSIKYSAATGGWVAVSELTRTHSKRASATVKTAVLATLLFATVQASANNEEQEEDLYLDPVQRTVAVLIVNSDKEGTGEKEKVEENSDWAVYFNEKGVLTAREITLKIDEKGDKKLKNLEQLAKELEEKVNGVEAIANQSAEIVETKLASGEGLKTNGQEDHYEWAE"
SEQ_C = "MSKIAVLVGSLRKDSLNRKLAQAVAELAPEGMEVSIVQLEDLPLYNEDVETSGEPAAVKELKAQIEAADGVLIVTPEYNYSIPGVLKNAIDWASRPYGENVWNHKPVAIVSVSPGGLGGARAQYHLRQVLVFLNMPALQGPEVFVNAIQDKFDDEGNLKNEATRELLTRFLTTLNTWINKL"


def fasta(*records: tuple[str, str]) -> str:
    return "".join(f">{h}\n{s}\n" for h, s in records)


def uniprot_hdr(acc: str, entry: str, desc: str, org: str, ox: int, gene: str | None = None) -> str:
    return f"sp|{acc}|{entry} {desc} OS={org} OX={ox}" + (f" GN={gene}" if gene else "") + " PE=1 SV=1"


def proteome_json(upid: str, taxid: int, name: str, ptype: str, n: int, busco: int = 98, level: str = "full", species: int = 487):
    return {
        "id": upid, "proteomeType": ptype, "strain": "S-" + upid, "proteinCount": n,
        "taxonomy": {"scientificName": name, "taxonId": taxid},
        "genomeAssembly": {"assemblyId": "GCA_" + upid[2:], "level": level},
        "proteomeCompletenessReport": {"buscoReport": {"score": busco}},
        "taxonLineage": [{"scientificName": "Neisseria meningitidis", "taxonId": species, "rank": "species"}],
    }


PRIMARY = proteome_json("UP000000001", 122586, "Neisseria meningitidis serogroup B (strain MC58)", "Reference proteome", 3)
SECOND = proteome_json("UP000000002", 122587, "Neisseria meningitidis serogroup A (strain Z2491)", "Non Reference proteome", 3)
FRAGMENT = proteome_json("UP000000003", 999, "Neisseria meningitidis fragment", "Non Reference proteome", 1, busco=10, level="contig")

VFDB_FASTA = fasta(
    ("VFG000001(gb|NP_1) (plc) phospholipase C [Phospholipase C (VF0470) - Exotoxin (VFC0235)] [Pseudomonas aeruginosa PAO1]", SEQ_A),
    ("VFG000002(gb|NP_2) (hap) adhesin [Hap adhesin (VF0100) - Adherence (VFC0001)] [Neisseria meningitidis MC58]", SEQ_B),
)
PROTEGEN_INDEX = ('<html><a href="protegen-all-4.0-2019-01-09.faa">fasta</a> <a href="protegen-bacterium-4.0-2019-01-09.faa">fasta</a></html>')
PROTEGEN_FAA = fasta(
    ("Protegen: 1|VO: VO_1|PMID: 1|NP_274117.1 adhesin [Neisseria meningitidis MC58]", SEQ_B),
    ("Protegen: 2|VO: VO_2|NP_000002.1 lethal factor [Bacillus anthracis str. Ames]", SEQ_C),
)


def build_server(primary=PRIMARY, others=(SECOND, FRAGMENT), with_vfdb=True, with_protegen=True, with_human=True,
                 species=487) -> FakeTransport:
    t = FakeTransport()

    def q(u: str) -> str:
        return urllib.parse.unquote_plus(u)

    # proteome search
    t.add(lambda u: "/proteomes/search" in u and "reference:true" in q(u),
          json.dumps({"results": [primary] if primary else []}))
    t.add(lambda u: "/proteomes/search" in u and f"taxonomy_id:{species}" in q(u),
          json.dumps({"results": ([primary] if primary else []) + list(others)}))
    t.add(lambda u: "/proteomes/search" in u, json.dumps({"results": []}))
    # fasta streams
    hdr = lambda n: uniprot_hdr(f"Q{n}00001", f"P{n}_NEIMB", f"Protein {n}", "Neisseria meningitidis", 122586)
    t.add(lambda u: "/uniprotkb/stream" in u and "proteome:UP000000001" in q(u), fasta((hdr(1), SEQ_A), (hdr(2), SEQ_B), (hdr(3), SEQ_C)),
          headers={"x-uniprot-release": "2026_04"})
    t.add(lambda u: "/uniprotkb/stream" in u and "proteome:UP000000002" in q(u), fasta((hdr(4), SEQ_A), (hdr(5), SEQ_B), (hdr(6), SEQ_C)),
          headers={"x-uniprot-release": "2026_04"})
    if with_human:
        t.add(lambda u: "/uniprotkb/stream" in u and "UP000005640" in q(u),
              fasta((uniprot_hdr("P00001", "H1_HUMAN", "Human protein", "Homo sapiens", 9606), SEQ_C)), headers={"x-uniprot-release": "2026_04"})
    if with_vfdb:
        t.add("VFDB_setA_pro.fas.gz", gzip.compress(VFDB_FASTA.encode()), headers={"last-modified": "Mon, 01 Jan 2026 00:00:00 GMT"})
    if with_protegen:
        t.add("download/index.php", PROTEGEN_INDEX)
        t.add("protegen-all-4.0-2019-01-09.faa", PROTEGEN_FAA)
    return t


class FakeRunner:
    """BLAST stand-in for S1 tests (records the databases it was asked to build)."""
    available = True
    version = "9.9.9+"

    def __init__(self, tmp: Path):
        self.tmp, self.built = tmp, []

    def make_db(self, fasta, name, sha, title=None):
        self.built.append(name)
        return {"status": "built", "prefix": str(self.tmp / f"{name}-{sha[:12]}" / name)}

    def describe(self):
        return {"mode": "fake"}


GRAM_TO_FLAGS = {
    "negative": ("-n", []), "positive": ("-p", []), "archaea": ("-a", []),
    "positive_with_outer_membrane": ("-n", []),
    "negative_without_outer_membrane": ("-p", ["--x-skip-localization=Cellwall"]),
}


class NoBlastRunner:
    """A machine without BLAST+ (native or Docker)."""
    available = False
    version = None

    def describe(self):
        return {"mode": "missing"}


def write_s0(store, domain="bacteria", taxid=122586, species_taxid=487, name="Neisseria meningitidis", gram="negative"):
    from vaxforge.s0_taxonomy import classify_organism  # noqa: F401  (documenting the shape we mimic)
    payload = {
        "query": name,
        "resolution": {"status": "resolved", "reason": None, "method": "exact", "message": "ok", "notes": []},
        "taxon": {"taxid": taxid, "scientific_name": name, "rank": "species", "division": "Bacteria",
                  "species_taxid": species_taxid, "species_name": name, "lineage": [],
                  "other_names": [{"name": "meningococcus", "class": "common name"}]},
        "organism": {"domain": domain, "gram_stain": gram, "gram_basis": "test", "psortb_flag": GRAM_TO_FLAGS.get(gram, (None, []))[0],
                     "psortb_extra_args": list(GRAM_TO_FLAGS.get(gram, (None, []))[1]),
                     "virulence_db_applicable": domain == "bacteria", "localisation_applicable": domain in ("bacteria", "archaea")},
        "candidates": [],
    }
    return store.write_artifact("s0", payload, params={"pathogen": name})
