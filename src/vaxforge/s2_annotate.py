"""S2 - virulence and localisation annotation of the whole proteome.

Two labels per protein (README S2):

* **Virulence association** - blastp against VFDB.  Identity, query coverage and
  E-value are all recorded (not just a boolean), plus the VFDB gene / factor /
  category of the best hit.  ``is_virulence_associated`` applies *configurable*
  thresholds (``annotate.vfdb.*``); those defaults are assumptions, not values
  from the README, and are written into the artifact.
* **Subcellular localisation** - PSORTb 3.0.  Surface-exposed and secreted
  proteins are reachable by antibody; cytoplasmic ones generally are not.

``Unknown`` is an explicit value.  PSORTb often cannot decide, and collapsing
"unknown" into "not surface" would silently discard candidates and inflate
apparent precision.  Likewise *not run* (no PSORTb available, Gram stain
unknown, not a bacterium) is kept distinct from PSORTb *returning* Unknown, via
``localisation.status``.
"""
from __future__ import annotations

import csv
import io
import re
import shutil
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any, Callable

from .artifacts import Artifact, ContractError, RunStore
from .cache import Cache, atomic_write_text, cache_from_config
from .config import Config
from .fasta import FastaRecord, parse_uniprot_header, read_fasta, write_fasta
from .tools import BlastRunner, ToolError, ascii_safe_dir, find_executable, run_command

BLAST_FIELDS = "qseqid sseqid pident length qlen slen qstart qend sstart send evalue bitscore qcovs qcovhsp stitle"
BLAST_COLS = BLAST_FIELDS.split()

PSORTB_LABELS = {"Cytoplasmic", "CytoplasmicMembrane", "Periplasmic", "OuterMembrane", "Extracellular", "Cellwall"}
DEFAULT_ACCESSIBILITY = {
    "Extracellular": "accessible", "OuterMembrane": "accessible", "Cellwall": "accessible",
    "CytoplasmicMembrane": "partial", "Periplasmic": "inaccessible", "Cytoplasmic": "inaccessible", "Unknown": "unknown",
}
# --gram settings -> (PSORTb flag, extra arguments).  The two "advanced" classes follow PSORTb's own USAGE notes.
GRAM_SETTINGS = {
    "negative": ("-n", []),
    "positive": ("-p", []),
    "archaea": ("-a", []),
    "positive_with_outer_membrane": ("-n", []),                                         # Mycobacterium, Deinococcus
    "negative_without_outer_membrane": ("-p", ["--x-skip-localization=Cellwall"]),      # Mycoplasma and other Tenericutes
}

TSV_COLUMNS = [
    "protein_id", "accession", "entry_name", "gene", "description", "length",
    "vf_status", "vf_evidence", "vf_is_associated", "vf_best_id", "vf_best_gene", "vf_best_product",
    "vf_best_factor", "vf_best_category", "vf_best_species", "vf_identity_pct", "vf_query_cov_pct",
    "vf_evalue", "vf_bitscore", "vf_n_hits",
    "loc_status", "loc_label", "loc_score", "loc_multiple_sites", "accessibility",
]


# ----------------------------------------------------------------------------
# VFDB
# ----------------------------------------------------------------------------
_VF_FACTOR_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<vfid>VF\d+)\)\s*-\s*(?P<cat>.*?)\s*\((?P<catid>VFC\d+)\)\s*$")
_VFDB_RE = re.compile(
    r"^(?P<id>VFG\d+)\((?P<db>[^|]*)\|(?P<acc>[^)]*)\)\s*(?:\((?P<gene>[^)]*)\)\s*)?(?P<product>.*?)"
    r"\s*\[(?P<factor>[^\]]*\)[^\]]*)\]\s*\[(?P<species>[^\]]*)\]\s*$"
)


def parse_vfdb_header(header: str) -> dict:
    """Parse a VFDB deflne; unparseable ones keep the raw text so nothing is lost."""
    h = header.strip()
    out: dict[str, Any] = {"vfdb_id": h.split("(", 1)[0].split()[0] if h else "", "gene": None, "product": None,
                           "factor": None, "factor_id": None, "category": None, "category_id": None, "species": None,
                           "raw": h}
    m = _VFDB_RE.match(h)
    if not m:
        return out
    out.update({"vfdb_id": m.group("id"), "gene": (m.group("gene") or "").strip() or None,
                "product": m.group("product").strip() or None, "species": m.group("species").strip() or None})
    fm = _VF_FACTOR_RE.match(m.group("factor").strip())
    if fm:
        out.update({"factor": fm.group("name").strip() or None, "factor_id": fm.group("vfid"),
                    "category": fm.group("cat").strip() or None, "category_id": fm.group("catid")})
    else:
        out["factor"] = m.group("factor").strip()
    return out


def build_vfdb_index(fasta: Path) -> dict[str, dict]:
    """first-token id -> parsed metadata (BLAST reports ``sseqid`` as the first token)."""
    return {rec.id: parse_vfdb_header(rec.header) for rec in read_fasta(fasta)}


def parse_blast_rows(text: str) -> list[dict]:
    rows = []
    for line in text.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t", len(BLAST_COLS) - 1)
        if len(parts) < len(BLAST_COLS) - 1:
            raise ValueError(f"unexpected BLAST output line: {line[:120]!r}")
        d = dict(zip(BLAST_COLS, parts))
        rows.append({
            "qseqid": d["qseqid"], "sseqid": d["sseqid"], "pident": float(d["pident"]), "length": int(d["length"]),
            "qlen": int(d["qlen"]), "slen": int(d["slen"]), "evalue": float(d["evalue"]), "bitscore": float(d["bitscore"]),
            "qcovs": float(d["qcovs"]), "qcovhsp": float(d["qcovhsp"]), "stitle": d.get("stitle", ""),
        })
    return rows


def _passes(h: dict, th: dict) -> bool:
    return h["evalue"] <= th["max_evalue"] and h["pident"] >= th["min_identity"] and h["qcovs"] >= th["min_query_coverage"]


def evaluate_virulence(hits: list[dict], index: dict[str, dict], th: dict) -> dict:
    """Turn a protein's BLAST rows into the ``virulence`` block (blast was run)."""
    if not hits:
        return {"status": "no_hit", "evidence": "none", "is_virulence_associated": False, "best_hit": None,
                "n_hits": 0, "hits": []}
    hits = sorted(hits, key=lambda h: (h["evalue"], -h["bitscore"]))
    passing = [h for h in hits if _passes(h, th)]
    best = max(passing or hits, key=lambda h: h["bitscore"])
    assoc = bool(passing)
    if assoc and best["pident"] >= th["strong_identity"] and best["qcovs"] >= th["strong_query_coverage"]:
        evidence = "strong"
    elif assoc:
        evidence = "moderate"
    else:
        evidence = "weak"

    def view(h: dict) -> dict:
        meta = index.get(h["sseqid"]) or parse_vfdb_header(h["stitle"])
        return {
            "vfdb_id": meta["vfdb_id"], "gene": meta["gene"], "product": meta["product"], "factor": meta["factor"],
            "factor_id": meta["factor_id"], "category": meta["category"], "category_id": meta["category_id"],
            "species": meta["species"],
            "identity_pct": round(h["pident"], 2), "query_coverage_pct": round(h["qcovs"], 1),
            "subject_coverage_pct": round(100.0 * h["length"] / h["slen"], 1) if h["slen"] else None,
            "evalue": h["evalue"], "bitscore": h["bitscore"], "alignment_length": h["length"],
        }

    return {"status": "hit", "evidence": evidence, "is_virulence_associated": assoc, "best_hit": view(best),
            "n_hits": len(hits), "hits": [view(h) for h in hits[: int(th.get("max_hits", 5))]]}


def virulence_thresholds(config: Config) -> dict:
    g = lambda k, d: float(config.get(f"annotate.vfdb.{k}", d))
    return {
        "blast_evalue": g("blast_evalue", 1.0e-3), "max_evalue": g("max_evalue", 1.0e-5),
        "min_identity": g("min_identity", 30.0), "min_query_coverage": g("min_query_coverage", 50.0),
        "strong_identity": g("strong_identity", 50.0), "strong_query_coverage": g("strong_query_coverage", 80.0),
        "max_hits": int(config.get("annotate.vfdb.max_hits", 5)),
        "note": "ASSUMED defaults - the README specifies none; adjust annotate.vfdb.* and report them",
    }


# ----------------------------------------------------------------------------
# PSORTb
# ----------------------------------------------------------------------------
_TERSE_RE = re.compile(r"^(?P<id>\S+)\s+(?P<label>\S.*?)\s+(?P<score>-?\d+(?:\.\d+)?)\s*$")


def normalise_psortb_label(label: str) -> tuple[str, bool]:
    """-> (label, multiple_sites).  ``Unknown (This protein may have multiple localization sites.)`` -> Unknown, True."""
    clean = label.strip()
    if clean.lower().startswith("unknown"):
        return "Unknown", "multiple" in clean.lower()
    key = re.sub(r"[\s_-]+", "", clean).lower()
    for known in PSORTB_LABELS:
        if known.lower() == key:
            return known, False
    return clean, False


def parse_psortb(text: str) -> dict[str, dict]:
    """Parse PSORTb output (terse TSV, or the 'normal' per-sequence report) into id -> result."""
    results: dict[str, dict] = {}
    if re.search(r"^\s*SeqID:", text, re.M):                       # "normal" / "long" format
        for block in re.split(r"^\s*SeqID:\s*", text, flags=re.M)[1:]:
            sid = block.split(None, 1)[0] if block.strip() else ""
            fm = re.search(r"Final Prediction:\s*\n\s*(.+)", block)
            if not sid or not fm:
                continue
            line = fm.group(1).strip()
            m = re.match(r"^(?P<label>.*?)\s+(?P<score>-?\d+(?:\.\d+)?)$", line)
            label, score = (m.group("label"), float(m.group("score"))) if m else (line, None)
            norm, multi = normalise_psortb_label(label)
            results[sid] = {"label": norm, "score": score, "multiple_sites": multi, "raw_label": label}
        return results
    for line in text.splitlines():
        if not line.strip() or line.lower().startswith("seqid") or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 3:
            sid, label, score_s = parts[0].strip(), parts[1].strip(), parts[2].strip()
        else:
            m = _TERSE_RE.match(line)
            if not m:
                continue
            sid, label, score_s = m.group("id"), m.group("label"), m.group("score")
        try:
            score = float(score_s)
        except ValueError:
            score = None
        norm, multi = normalise_psortb_label(label)
        results[sid] = {"label": norm, "score": score, "multiple_sites": multi, "raw_label": label}
    return results


def psortb_mode(config: Config) -> str:
    """``annotate.psortb.mode`` as a string.  PyYAML (YAML 1.1) reads an unquoted ``off`` as boolean False."""
    value = config.get("annotate.psortb.mode", "auto")
    return "off" if value is False else str(value)


class PsortbRunner:
    """Run PSORTb 3.0 through the brinkmanlab Docker image, or a native ``psort`` install.

    The Docker call is taken from the image project's own wrapper script
    (github.com/brinkmanlab/psortb_commandline_docker, file ``psortb``): the results folder is mounted at
    ``/tmp/results``, the input FASTA sits inside it, and the container's ``/usr/local/psortb/bin/psort`` runs with
    ``-n|-p|-a -i /tmp/results/<file> -o terse``.  ``-r`` is deliberately NOT passed: for ``psort`` itself it means
    PSORT_ROOT, and only the wrapper script reuses it as a results directory.  The entrypoint is overridden
    explicitly because the wrapper passes the binary path itself.  Result files appear in the mounted folder.

    Written from that documentation; NOT yet run against the real image (the dev machine has no Docker).
    ``annotate.psortb.command`` can replace the whole command (``{docker} {image} {out} {flag}``; an element
    that is exactly ``{args}`` expands to the Gram flag plus any extra arguments).
    """

    PSORT_BIN = "/usr/local/psortb/bin/psort"
    CONTAINER_DIR = "/tmp/results"

    def __init__(self, config: Config, cache_dir: Path):
        self.config = config
        self.cache_dir = Path(cache_dir)
        mode = psortb_mode(config)
        # the Debian/Ubuntu `psortb` package installs the binary as `psort`
        self.native = find_executable("psort") if mode in ("auto", "native") else None
        self.docker = find_executable(str(config.get("tools.docker", "docker"))) if mode in ("auto", "docker") else None
        self.image = str(config.get("annotate.psortb.image", "brinkmanlab/psortb_commandline:1.0.2"))
        self.mode = "native" if self.native else "docker" if self.docker else "missing"
        self.timeout = float(config.get("annotate.psortb.timeout_s", 7200))

    @property
    def available(self) -> bool:
        return self.mode != "missing"

    def command(self, args: list[str], out_dir: Path) -> list[str]:
        """The ``docker run`` command line; ``args`` = Gram flag plus any extra arguments."""
        template = self.config.get("annotate.psortb.command")
        if template:
            cmd: list[str] = []
            for item in template:
                if str(item) == "{args}":
                    cmd.extend(args)
                else:
                    cmd.append(str(item).format(docker=self.docker or "docker", image=self.image, out=str(out_dir),
                                                flag=args[0] if args else ""))
            return cmd
        return [self.docker or "docker", "run", "--rm", "-v", f"{out_dir}:{self.CONTAINER_DIR}", "-e", f"MOUNT={out_dir}",
                "--entrypoint", self.PSORT_BIN, self.image, *args,
                "-i", f"{self.CONTAINER_DIR}/input.fasta", "-o", "terse"]

    def run(self, fasta: Path, args: list[str], key: str) -> str:
        if not self.available:
            raise ToolError("PSORTb is not available (no native `psort`, no Docker)")
        work = ascii_safe_dir(self.cache_dir / "psortb" / key, f"psortb-{key}")
        if self.mode == "native":                        # `psort` prints the result table to stdout
            inp = work / "input.fasta"
            shutil.copyfile(fasta, inp)
            return run_command([self.native, *args, "-o", "terse", str(inp)], timeout=self.timeout).stdout
        out = work / "out"
        out.mkdir(exist_ok=True)
        shutil.copyfile(fasta, out / "input.fasta")      # the container reads its input from the mounted folder
        run_command(self.command(args, out), timeout=self.timeout)
        files = sorted((p for p in out.rglob("*") if p.is_file() and p.name != "input.fasta"),
                       key=lambda p: p.stat().st_mtime, reverse=True)
        if not files:
            raise ToolError(f"PSORTb ran but wrote no result file into {out}")
        for path in files:                               # newest file that actually parses as PSORTb output
            text = path.read_text(encoding="utf-8", errors="replace")
            if parse_psortb(text):
                return text
        raise ToolError(f"PSORTb wrote {[p.name for p in files][:3]} into {out}, but none parses as PSORTb output; "
                        f"see docker/README.md to check the real format")


# ----------------------------------------------------------------------------
# stage
# ----------------------------------------------------------------------------
def _sanitise_queries(records: list[FastaRecord], path: Path) -> dict[str, str]:
    """Write ``q0000001``-style ids (robust against '|', parentheses...) and return id -> protein_id."""
    mapping = {f"q{i:07d}": r.id for i, r in enumerate(records, 1)}
    write_fasta((FastaRecord(qid, r.seq) for qid, r in zip(mapping, records)), path)
    return mapping


def run_virulence(config: Config, store: RunStore, cache: Cache, s1: dict, runner: BlastRunner,
                  records: list[FastaRecord], proteome_sha: str, organism: dict, warnings: list[str]) -> tuple[dict, dict]:
    """-> (per-protein virulence blocks, run info)."""
    th = virulence_thresholds(config)
    base_info = {"status": "not_run", "reason": None, "tool": "blastp", "database": "VFDB"}

    def not_run(reason: str, status: str = "not_run") -> tuple[dict, dict]:
        block = {"status": status, "evidence": "n/a", "is_virulence_associated": None, "best_hit": None, "n_hits": 0, "hits": []}
        if status == "not_run":
            warnings.append(f"virulence annotation not run: {reason}")
        return {r.id: block for r in records}, {**base_info, "status": status, "reason": reason}

    if not organism.get("virulence_db_applicable"):
        return not_run(f"VFDB is a bacterial virulence database; organism domain is {organism.get('domain')}", "not_applicable")
    vf = s1["reference_dbs"].get("vfdb", {})
    if vf.get("status") not in ("ok", "cached"):
        return not_run(f"VFDB unavailable from S1 ({vf.get('status')}: {vf.get('reason', 'no detail')})")
    if not runner.available:
        return not_run("BLAST+ not found (native or Docker)")
    ref = vf.get("cache_file")
    if not ref:
        return not_run("S1 artifact has no VFDB cache file")
    vfdb_fasta = store.resolve(ref, cache.root)
    if not vfdb_fasta.is_file():
        return not_run(f"VFDB FASTA missing from cache: {vfdb_fasta}")
    prefix = runner.db_prefix("vfdb", ref["sha256"])
    if not runner.db_exists(prefix):
        try:
            runner.make_db(vfdb_fasta, "vfdb", ref["sha256"], title="VFDB")
        except ToolError as exc:
            return not_run(f"could not build the VFDB BLAST database: {exc}")

    key = cache.key("vfdb_blast", proteome_sha, ref["sha256"], th["blast_evalue"], th["max_hits"], runner.version)
    cached = cache.path("derived", "vfdb_blast", f"{key}.tsv")
    hit = cache.usable(cached)
    if hit:
        text = cached.read_text(encoding="utf-8")
    else:
        cache.require(cached, "VFDB BLAST results")
        with tempfile.TemporaryDirectory(prefix="vaxforge_q_") as tmp:
            q = Path(tmp) / "q.faa"
            mapping = _sanitise_queries(records, q)
            try:
                text = runner.blastp(q, prefix, f"6 {BLAST_FIELDS}", evalue=th["blast_evalue"],
                                     max_target_seqs=max(th["max_hits"], 5), threads=int(config.get("annotate.vfdb.threads", 0)))
            except ToolError as exc:
                return not_run(f"blastp failed: {exc}")
        # translate query ids back to protein ids before caching
        lines = []
        for line in text.splitlines():
            qid, _, rest = line.partition("\t")
            lines.append(f"{mapping.get(qid, qid)}\t{rest}")
        text = "\n".join(lines) + ("\n" if lines else "")
        cache.put_bytes(cached, text.encode("utf-8"), {"proteome_sha256": proteome_sha, "vfdb_sha256": ref["sha256"]})
    index = build_vfdb_index(vfdb_fasta)
    by_query: dict[str, list[dict]] = {}
    for row in parse_blast_rows(text):
        by_query.setdefault(row["qseqid"], []).append(row)
    blocks = {r.id: evaluate_virulence(by_query.get(r.id, []), index, th) for r in records}
    info = {**base_info, "status": "ok", "reason": None, "blast_version": runner.version, "cache_hit": hit,
            "vfdb_set": vf.get("set"), "vfdb_n_sequences": vf.get("n_sequences"), "vfdb_sha256": ref["sha256"]}
    return blocks, info


def _accessibility(label: str, mapping: dict) -> str:
    return mapping.get(label, "unknown")


def run_localisation(config: Config, store: RunStore, cache: Cache, records: list[FastaRecord], proteome_sha: str,
                     organism: dict, warnings: list[str], psortb_results: str | Path | None, gram: str | None,
                     psortb_fn: Callable[[Path, list[str], str], str] | None) -> tuple[dict, dict]:
    """-> (per-protein localisation blocks, run info).  ``psortb_fn(query_fasta, [flag, *extra], cache_key) -> text``."""
    amap = {**DEFAULT_ACCESSIBILITY, **(config.get("annotate.accessibility") or {})}
    mode = psortb_mode(config)
    info: dict[str, Any] = {"status": "not_run", "reason": None, "tool": "PSORTb 3.0", "mode": mode, "gram_flag": None}

    def uniform(status: str, reason: str, loud: bool) -> tuple[dict, dict]:
        if loud:
            warnings.append(f"localisation not run: {reason}")
        block = {"status": status, "label": "Unknown", "score": None, "multiple_sites": False,
                 "accessibility": "unknown", "reason": reason}
        return {r.id: dict(block) for r in records}, {**info, "status": status, "reason": reason}

    if not organism.get("localisation_applicable"):
        return uniform("not_applicable", f"PSORTb predicts bacterial/archaeal localisation; organism domain is {organism.get('domain')}", False)
    if mode == "off":
        return uniform("not_run", "annotate.psortb.mode is 'off'", True)

    gram_choice = (gram or str(config.get("annotate.psortb.gram", "auto"))).lower()
    if gram_choice == "auto":
        flag = organism.get("psortb_flag")
        extra = list(organism.get("psortb_extra_args") or [])
        gram_basis = organism.get("gram_basis")
    elif gram_choice in GRAM_SETTINGS:
        flag, extra = GRAM_SETTINGS[gram_choice][0], list(GRAM_SETTINGS[gram_choice][1])
        gram_basis = f"user override: {gram_choice}"
    else:
        raise ContractError(f"unknown Gram setting {gram_choice!r}; use auto, {', '.join(GRAM_SETTINGS)}")
    info["gram_flag"], info["gram_extra_args"], info["gram_basis"] = flag, extra, gram_basis

    results: dict[str, dict] | None = None
    ids = {r.id for r in records}
    source = None
    import_file = psortb_results or config.get("annotate.psortb.results_file")
    if import_file:
        text = Path(import_file).read_text(encoding="utf-8", errors="replace")
        raw = parse_psortb(text)
        results = {}
        by_first_token = {r.header.split()[0]: r.id for r in records}
        by_header = {r.header: r.id for r in records}
        for sid, res in raw.items():
            pid = sid if sid in ids else by_first_token.get(sid.split()[0]) or by_header.get(sid)
            if pid:
                results[pid] = res
        if not results:
            return uniform("not_run", f"none of the {len(raw)} ids in {import_file} match this proteome", True)
        source = f"import:{Path(import_file).name}"
    else:
        if flag is None:
            return uniform("not_run", f"Gram stain unknown ({gram_basis}); re-run with --gram negative|positive|archaea", True)
        args = [flag, *extra]
        key = cache.key("psortb", proteome_sha, args, config.get("annotate.psortb.image"))
        cached = cache.path("derived", "psortb", f"{key}.txt")
        runner = PsortbRunner(config, cache.root)
        if cache.usable(cached):
            text, info["cache_hit"] = cached.read_text(encoding="utf-8"), True
            source = "psortb(cached)"
        elif psortb_fn is None and not runner.available:
            return uniform("not_run", "PSORTb is not installed and Docker is unavailable. Install Docker and run "
                           "`docker pull " + runner.image + "`, or run PSORTb elsewhere and pass --psortb-results FILE", True)
        elif cache.offline:
            # offline never starts a container (an image pull would touch the network): record why instead of aborting the run
            return uniform("not_run", "offline mode: PSORTb results are not cached; run once online with Docker, "
                           "or pass --psortb-results FILE", True)
        else:
            with tempfile.TemporaryDirectory(prefix="vaxforge_p_") as tmp:
                q = Path(tmp) / "input.fasta"
                mapping = _sanitise_queries(records, q)
                try:
                    text = (psortb_fn or runner.run)(q, args, key)
                except ToolError as exc:
                    return uniform("not_run", f"PSORTb failed: {exc}", True)
            parsed_raw = parse_psortb(text)
            if not parsed_raw:
                return uniform("not_run", "PSORTb output could not be parsed (unexpected format); see annotate.psortb.command", True)
            # store results keyed by real protein ids
            text = "\n".join(f"{mapping.get(sid, sid)}\t{r['raw_label']}\t{'' if r['score'] is None else r['score']}"
                             for sid, r in parsed_raw.items()) + "\n"
            cache.put_bytes(cached, text.encode("utf-8"), {"proteome_sha256": proteome_sha, "psortb_args": args})
            info["cache_hit"] = False
            source = f"psortb({runner.mode})" if psortb_fn is None else "psortb(injected)"
        results = parse_psortb(text)

    blocks: dict[str, dict] = {}
    missing = 0
    for r in records:
        res = results.get(r.id)
        if res is None:
            missing += 1
            blocks[r.id] = {"status": "missing_from_psortb_output", "label": "Unknown", "score": None,
                            "multiple_sites": False, "accessibility": "unknown", "source": source}
            continue
        label = res["label"]
        status = "psortb_unknown" if label == "Unknown" else "predicted"
        blocks[r.id] = {"status": status, "label": label, "score": res["score"], "multiple_sites": res["multiple_sites"],
                        "accessibility": _accessibility(label, amap), "source": source}
        if label not in PSORTB_LABELS and label != "Unknown":
            blocks[r.id]["note"] = f"unrecognised PSORTb label {label!r}"
    if missing:
        warnings.append(f"{missing} of {len(records)} proteins were absent from the PSORTb output (kept as Unknown)")
    info.update({"status": "ok", "source": source, "n_missing": missing})
    return blocks, info


def protein_row(p: dict) -> dict:
    """One flat TSV row from a protein block of the artifact."""
    v, loc = p["virulence"], p["localisation"]
    best = v.get("best_hit") or {}
    return {
        "protein_id": p["protein_id"], "accession": p["accession"], "entry_name": p["entry_name"], "gene": p["gene"],
        "description": p["description"], "length": p["length"],
        "vf_status": v["status"], "vf_evidence": v["evidence"], "vf_is_associated": v["is_virulence_associated"],
        "vf_best_id": best.get("vfdb_id"), "vf_best_gene": best.get("gene"), "vf_best_product": best.get("product"),
        "vf_best_factor": best.get("factor"), "vf_best_category": best.get("category"), "vf_best_species": best.get("species"),
        "vf_identity_pct": best.get("identity_pct"), "vf_query_cov_pct": best.get("query_coverage_pct"),
        "vf_evalue": best.get("evalue"), "vf_bitscore": best.get("bitscore"), "vf_n_hits": v["n_hits"],
        "loc_status": loc["status"], "loc_label": loc["label"], "loc_score": loc.get("score"),
        "loc_multiple_sites": loc.get("multiple_sites"), "accessibility": loc["accessibility"],
    }


def build_summary(proteins: list[dict], virulence_status: str, localisation_status: str) -> dict:
    return {
        "n_proteins": len(proteins),
        "virulence": {
            "run_status": virulence_status,
            "by_status": dict(Counter(p["virulence"]["status"] for p in proteins)),
            "by_evidence": dict(Counter(p["virulence"]["evidence"] for p in proteins)),
            "n_associated": sum(1 for p in proteins if p["virulence"]["is_virulence_associated"]),
        },
        "localisation": {
            "run_status": localisation_status,
            "by_status": dict(Counter(p["localisation"]["status"] for p in proteins)),
            "by_label": dict(Counter(p["localisation"]["label"] for p in proteins)),
            "by_accessibility": dict(Counter(p["localisation"]["accessibility"] for p in proteins)),
            "n_unknown": sum(1 for p in proteins if p["localisation"]["label"] == "Unknown"),
            "note": "Unknown is kept as its own value; it is NOT 'not surface'. Downstream ranking must not drop it silently.",
        },
    }


def write_tsv(path: Path, rows: list[dict]) -> None:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=TSV_COLUMNS, delimiter="\t", lineterminator="\n")
    w.writeheader()
    for row in rows:
        w.writerow({k: ("" if row[k] is None else row[k]) for k in TSV_COLUMNS})
    atomic_write_text(path, buf.getvalue())


def run(store: RunStore, config: Config, *, cache: Cache | None = None, runner: BlastRunner | None = None,
        psortb_results: str | Path | None = None, gram: str | None = None,
        psortb_fn: Callable[[Path, list[str], str], str] | None = None) -> Artifact:
    """S2: write ``s2_annotate.json`` (+ ``data/s2_annotations.tsv``)."""
    cache = cache or cache_from_config(config)
    store.cache_dir = cache.root
    s0 = store.read_artifact("s0").require_ok().payload
    s1_art = store.read_artifact("s1").require_ok()
    s1 = s1_art.payload
    organism = s0["organism"]
    runner = runner or BlastRunner(config, cache.root)

    pref = s1["proteome"]["file"]
    problem = store.verify_ref(pref)
    if problem:
        raise ContractError(f"S1 proteome file is not usable ({problem}); re-run S1")
    proteome_path = store.resolve(pref)
    records = list(read_fasta(proteome_path))
    proteome_sha = pref["sha256"]
    warnings: list[str] = []

    vblocks, vinfo = run_virulence(config, store, cache, s1, runner, records, proteome_sha, organism, warnings)
    lblocks, linfo = run_localisation(config, store, cache, records, proteome_sha, organism, warnings, psortb_results, gram, psortb_fn)
    # how the answer was obtained (cache hit or fresh run) is not part of what was computed
    volatile = {"virulence_cache_hit": vinfo.pop("cache_hit", None), "localisation_cache_hit": linfo.pop("cache_hit", None)}

    proteins = []
    for rec in records:
        meta = parse_uniprot_header(rec.header)
        proteins.append({"protein_id": rec.id, "accession": meta["accession"], "entry_name": meta["entry_name"],
                         "gene": meta["gene"], "description": meta["description"], "length": len(rec.seq),
                         "virulence": vblocks[rec.id], "localisation": lblocks[rec.id]})
    tsv = store.data_path("s2_annotations.tsv")
    write_tsv(tsv, [protein_row(p) for p in proteins])
    summary = build_summary(proteins, vinfo["status"], linfo["status"])
    payload = {
        "proteome": {"file": pref, "n_proteins": len(proteins), "upid": s1["proteome"].get("upid")},
        "organism": {"domain": organism["domain"], "gram_stain": organism["gram_stain"]},
        "thresholds": virulence_thresholds(config),
        "tools": {"virulence": vinfo, "localisation": linfo},
        "summary": summary,
        "columns": TSV_COLUMNS,
        "tsv": store.run_ref(tsv, records=len(proteins)),
        "proteins": proteins,
    }
    tools = {"blastp": runner.version if runner.available and vinfo["status"] == "ok" else "not_run",
             "psortb": linfo.get("source") or linfo["status"]}
    return store.write_artifact(
        "s2", payload,
        params={"virulence": virulence_thresholds(config), "psortb_mode": linfo.get("mode"), "gram_flag": linfo.get("gram_flag"),
                "gram_extra_args": linfo.get("gram_extra_args"),
                "accessibility": {**DEFAULT_ACCESSIBILITY, **(config.get("annotate.accessibility") or {})}},
        tools=tools, warnings=warnings, volatile=volatile)
