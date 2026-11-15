"""Command-line entrypoint (``vaxforge ...`` / ``python -m vaxforge ...``).  Owned by stream A.

Stage modules written by other streams plug in without editing this file:

* **Pipeline stages S3-S12.**  ``vaxforge run`` imports ``vaxforge.<module>`` for each later stage
  (see ``artifacts.STAGES``).  A module that is not installed yet is reported and skipped; one that
  exists must expose ``run(store: RunStore, config: Config) -> Artifact``.
* **Extra sub-commands** (``validate``, ``compile-protocol``, ``animal-study`` ...).  Any of those
  modules - or ``vaxforge.validate_cli`` - may define ``register_cli(subparsers, common)``, which is
  called while the parser is built.

Exit codes: 0 ok | 1 error | 2 needs user input or nothing matched | 3 required data unavailable.
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Callable

from . import __version__
from . import s0_taxonomy as s0
from . import s1_acquire as s1
from . import s2_annotate as s2
from . import tools
from .artifacts import STAGE_ORDER, STAGES, Artifact, ContractError, RunStore
from .cache import CacheMiss
from .config import Config, ConfigError, load_config
from .http import HttpError, OfflineError
from .tools import BlastRunner, ToolError

EXIT_OK, EXIT_ERROR, EXIT_INPUT, EXIT_DATA = 0, 1, 2, 3
CORE_STAGES = ("s0", "s1", "s2")
EXTENSION_MODULES = ["s8_protocol", "s9_robot", "s9plus_animate", "s9_5_finetune", "s10_animal", "validate_cli",
                     "s11_prereg", "s12_biosafety", "report"]


def say(msg: str = "") -> None:
    print(msg, flush=True)


def slugify(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", text).strip("_").lower() or "run"


# ----------------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------------
def load_from_args(args: argparse.Namespace) -> Config:
    cfg = load_config(getattr(args, "config", None))
    if getattr(args, "offline", False):
        cfg.set("network.offline", True)
    if getattr(args, "refresh", False):
        cfg.set("cache.refresh", True)
    if getattr(args, "top_k", None) is not None:
        cfg.set("pipeline.top_k", int(args.top_k))
    if getattr(args, "cache_dir", None):
        cfg.set("paths.cache_dir", args.cache_dir)
    return cfg


def interactive_chooser(res: s0.Resolution) -> int | None:
    say(f"\n{res.message}")
    for n in res.notes:
        say(f"  note: {n}")
    if not res.candidates:
        return None
    say(s0.format_candidates(res.candidates))
    while True:
        try:
            answer = input("Choose a number or enter a taxid (blank to abort): ").strip()
        except EOFError:
            return None
        if not answer:
            return None
        if answer.isdigit():
            n = int(answer)
            if 1 <= n <= len(res.candidates):
                return int(res.candidates[n - 1]["taxid"])
            if n > len(res.candidates):
                return n                        # treated as a taxid
        say("  please enter one of the listed numbers or a taxid")


def get_chooser(args: argparse.Namespace) -> Callable[[s0.Resolution], int | None] | None:
    if getattr(args, "non_interactive", False) or not sys.stdin or not sys.stdin.isatty():
        return None
    return interactive_chooser


def print_warnings(art: Artifact) -> None:
    for w in art.warnings:
        say(f"  ! {w}")


def report_s0(art: Artifact) -> None:
    p = art.payload
    res = p["resolution"]
    if art.status == "ok":
        t, o = p["taxon"], p["organism"]
        how = res["method"] + (", confirmed by user" if res.get("user_confirmed") else "")
        say(f"S0  resolved: {t['scientific_name']}  (taxid {t['taxid']}, {t['rank']}; species taxid {t['species_taxid']})  [{how}]")
        if res.get("synonym_matched"):
            say(f"    matched the non-current name '{s0.strip_authority(res['matched_name'])}'")
        gram = o["gram_stain"] if o["domain"] in ("bacteria", "archaea") else "n/a"
        say(f"    organism: {o['domain']}, Gram stain: {gram}  ({o['gram_basis']})")
        for n in res.get("notes", []):
            say(f"    note: {n}")
    else:
        say(f"S0  NOT RESOLVED: {res['message']}")
        if p["candidates"]:
            say(s0.format_candidates(p["candidates"]))
            say("    re-run with --taxid <number> to choose one (or run interactively).")


def report_s1(art: Artifact) -> None:
    p = art.payload
    if art.status != "ok":
        say(f"S1  FAILED: {art.warnings[0] if art.warnings else 'see artifact'}")
        return
    prim = p["proteome"]
    cached = art.volatile.get("cache_hits", {})
    tag = lambda n: " (from cache)" if cached.get(n) else ""
    say(f"S1  proteome {prim.get('upid') or prim['name']}: {prim['n_sequences']} proteins "
        f"({prim['proteome_type']}, UniProt {prim.get('release') or 'n/a'}){tag('proteome')}")
    for name, st in p["resource_status"].items():
        if name == "proteome":
            continue
        say(f"    {name:<28} {st}{tag(name)}")
    print_warnings(art)


def report_s2(art: Artifact) -> None:
    s = art.payload["summary"]
    v, loc = s["virulence"], s["localisation"]
    say(f"S2  annotated {s['n_proteins']} proteins")
    say(f"    virulence ({v['run_status']}): {v['n_associated']} associated with VFDB; evidence {v['by_evidence']}")
    say(f"    localisation ({loc['run_status']}): {loc['by_label']}")
    say(f"    accessibility: {loc['by_accessibility']}   ('Unknown' kept explicit: {loc['n_unknown']})")
    say(f"    table: {art.payload['tsv']['path']}")
    print_warnings(art)


# ----------------------------------------------------------------------------
# commands
# ----------------------------------------------------------------------------
def run_extra_stage(stage: str, store: RunStore, cfg: Config) -> tuple[str, Artifact | None]:
    modname = f"vaxforge.{STAGES[stage]}"
    try:
        mod = importlib.import_module(modname)
    except ModuleNotFoundError as exc:
        if exc.name == modname:
            return "not installed", None
        raise
    fn = getattr(mod, "run", None)
    if not callable(fn):
        return "module has no run(store, config)", None
    return "ran", fn(store, cfg)


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_from_args(args)
    if not args.pathogen and args.taxid is None:
        raise ConfigError("give --pathogen NAME or --taxid N")
    out = Path(args.out) if args.out else cfg.runs_dir / slugify(args.pathogen or f"taxid_{args.taxid}")
    store = RunStore(out, run_id=args.run_id, cache_dir=cfg.cache_dir)
    store.snapshot_config(cfg)
    wanted = [s.strip() for s in args.stages.split(",")] if args.stages else list(STAGE_ORDER)
    bad = [s for s in wanted if s not in STAGES]
    if bad:
        raise ConfigError(f"unknown stage(s) {bad}; known: {', '.join(STAGE_ORDER)}")
    say(f"run dir: {out}   (offline={cfg.offline})")

    if "s0" in wanted:
        a0 = s0.run(store, cfg, pathogen=args.pathogen, taxid=args.taxid, chooser=get_chooser(args))
        report_s0(a0)
        if a0.status != "ok":
            return EXIT_INPUT
    if "s1" in wanted:
        a1 = s1.run(store, cfg, proteome_fasta=args.proteome_fasta, proteome_upid=args.proteome_upid)
        report_s1(a1)
        if a1.status != "ok":
            return EXIT_DATA
    if "s2" in wanted:
        a2 = s2.run(store, cfg, psortb_results=args.psortb_results, gram=args.gram)
        report_s2(a2)
    for stage in wanted:
        if stage in CORE_STAGES:
            continue
        outcome, art = run_extra_stage(stage, store, cfg)
        if art is None:
            say(f"{stage:<8}{outcome} - skipped")
            continue
        say(f"{stage:<8}{art.status}" + (f"  ({len(art.warnings)} warning(s))" if art.warnings else ""))
        if art.status != "ok":
            return EXIT_ERROR
    problems = store.verify()
    if problems:
        say("\nintegrity problems:")
        for p in problems:
            say(f"  - {p}")
        return EXIT_ERROR
    say(f"\ndone. artifacts: {', '.join(store.stages_present())}  (verify: ok)")
    return EXIT_OK


def cmd_resolve(args: argparse.Namespace) -> int:
    cfg = load_from_args(args)
    res, confirmed = s0.resolve_pathogen(cfg, args.name, args.taxid, chooser=get_chooser(args))
    if args.json:
        payload = s0._resolution_payload(res, args.name or str(args.taxid), confirmed)
        say(json.dumps(payload, indent=2, ensure_ascii=False))
    else:
        say(res.message)
        for n in res.notes:
            say(f"  note: {n}")
        if res.candidates and res.status != "resolved":
            say(s0.format_candidates(res.candidates))
        if res.taxon:
            org = s0.classify_organism(res.taxon)
            say(f"  taxid {res.taxon['taxid']} | {res.taxon['rank']} | {org['domain']} | Gram: {org['gram_stain']}")
    return EXIT_OK if res.status == "resolved" else EXIT_INPUT


def _stage_cmd(stage: str) -> Callable[[argparse.Namespace], int]:
    def cmd(args: argparse.Namespace) -> int:
        cfg = load_from_args(args)
        store = RunStore(args.run_dir, cache_dir=cfg.cache_dir)
        if stage == "s1":
            art = s1.run(store, cfg, proteome_fasta=args.proteome_fasta, proteome_upid=args.proteome_upid)
            report_s1(art)
            return EXIT_OK if art.status == "ok" else EXIT_DATA
        art = s2.run(store, cfg, psortb_results=args.psortb_results, gram=args.gram)
        report_s2(art)
        return EXIT_OK
    return cmd


def cmd_warm_cache(args: argparse.Namespace) -> int:
    cfg = load_from_args(args)
    names = [n.strip() for chunk in args.pathogens for n in chunk.split(",") if n.strip()]
    if not names:
        raise ConfigError('give --pathogens "Neisseria meningitidis,SARS-CoV-2"')
    say(f"warming cache at {cfg.cache_dir} for {len(names)} pathogen(s) ...")
    rows = s1.warm_cache(cfg, names)
    failed = 0
    for r in rows:
        if r.get("error"):
            say(f"  {r['pathogen']}: ERROR {r['error']}")
            failed += 1
        elif r.get("s0") != "ok":
            say(f"  {r['pathogen']}: not resolved - {r.get('note')}  (use the full species name)")
            failed += 1
        else:
            bad = {k: v for k, v in r["resources"].items() if v in ("unavailable", "manual_required")}
            say(f"  {r['pathogen']}: S1 {r['s1']}" + (f"   problems: {bad}" if bad else "   all resources ready"))
            failed += r["s1"] != "ok"
    say("\nNow `--offline` runs for these pathogens need no network.")
    return EXIT_OK if not failed else EXIT_DATA


def cmd_verify(args: argparse.Namespace) -> int:
    cfg = load_from_args(args)
    store = RunStore(args.run_dir, cache_dir=cfg.cache_dir)
    present = store.stages_present()
    if not present:
        say(f"no artifacts found in {args.run_dir}")
        return EXIT_ERROR
    problems = store.verify()
    say(f"{args.run_dir}: stages {', '.join(present)}")
    for p in problems:
        say(f"  PROBLEM: {p}")
    say("OK - digests, upstream chain and referenced files are consistent" if not problems else f"{len(problems)} problem(s)")
    return EXIT_OK if not problems else EXIT_ERROR


def cmd_show(args: argparse.Namespace) -> int:
    cfg = load_from_args(args)
    store = RunStore(args.run_dir, cache_dir=cfg.cache_dir)
    manifest = store.read_manifest()
    say(f"run {manifest.get('run_id')}  (vaxforge {manifest.get('vaxforge_version')}, config {str(manifest.get('config_digest'))[:19]})")
    for stage in store.stages_present():
        m = manifest.get("stages", {}).get(stage, {})
        say(f"  {stage:<7}{m.get('status', '?'):<18}{m.get('digest', '')[:19]}  {m.get('finished_at', '')}")
    for stage, fn in (("s0", report_s0), ("s1", report_s1), ("s2", report_s2)):
        if store.has(stage):
            say()
            fn(store.read_artifact(stage))
    return EXIT_OK


def cmd_doctor(args: argparse.Namespace) -> int:
    cfg = load_from_args(args)
    broken = False
    degraded = False

    def line(label: str, value: str, level: str = "ok") -> None:
        """level: ok | warn (works, but degraded or advisory) | fail (broken)."""
        nonlocal broken, degraded
        broken |= level == "fail"
        degraded |= level == "warn"
        tag = {"ok": "ok", "warn": "..", "fail": "!!"}[level]
        say(f"  [{tag}] {label:<18}{value}")

    say(f"vaxforge {__version__} on Python {sys.version.split()[0]}   ([ok] fine, [..] works but degraded, [!!] broken)")
    line("config", str(cfg.source or "(none found - using defaults)"), "ok" if cfg.source else "warn")
    try:
        import yaml  # noqa: F401
        line("yaml", "PyYAML installed")
    except Exception:
        line("yaml", "PyYAML missing - using the built-in reader (fine for config.yaml)")
    cache = cfg.cache_dir
    try:
        cache.mkdir(parents=True, exist_ok=True)
        probe = cache / ".write_test"
        probe.write_text("x")
        probe.unlink()
        line("cache dir", str(cache))
    except OSError as exc:
        line("cache dir", f"{cache} not writable: {exc}", "fail")
    if not str(cache.resolve()).isascii():
        line("non-ASCII path", "BLAST+ cannot open these; BLAST databases go to an ASCII-only folder automatically")
    runner = BlastRunner(cfg, cache)
    d = runner.describe()
    if runner.available and d["mode"] == "docker":
        st, detail = tools.docker_state(runner.docker, runner.image)
        line("BLAST+", f"docker {d.get('version') or ''} {runner.image}: {detail}".replace("  ", " "),
             "ok" if st == "ok" else "warn")
    elif runner.available:
        line("BLAST+", f"{d['mode']} {d.get('version') or ''} {d.get('blastp') or d.get('image') or ''}".strip())
    else:
        line("BLAST+", "not found: S2 virulence annotation will be 'not_run' (install BLAST+ or Docker)", "warn")
    psort = s2.PsortbRunner(cfg, cache)
    if psort.available and psort.mode == "docker":
        st, detail = tools.docker_state(psort.docker, psort.image)
        line("PSORTb", f"docker ({psort.image}): {detail}", "ok" if st == "ok" else "warn")
    elif psort.available:
        line("PSORTb", f"{psort.mode} ({psort.image})")
    else:
        line("PSORTb", "not found: localisation will be 'not_run' (never silently 'unknown') until Docker + the PSORTb "
                       "image are available, or you pass --psortb-results FILE", "warn")
    if cfg.get("network.ncbi.email"):
        line("NCBI email", "set")
    else:
        line("NCBI email", "not set (export NCBI_EMAIL=... - NCBI asks for one; it is optional)", "warn")
    line("NCBI api key", "set (10 req/s)" if cfg.get("network.ncbi.api_key") else "not set (3 req/s is enough for S0)")
    if args.online:
        from .http import client_from_config
        client = client_from_config(cfg)
        for name, url in (("NCBI E-utilities", "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/einfo.fcgi?db=taxonomy"),
                          ("UniProt REST", "https://rest.uniprot.org/proteomes/UP000000425?format=json"),
                          ("VFDB", "http://www.mgc.ac.cn/VFs/Down/VFDB_setA_pro.fas.gz")):
            try:
                client.timeout = 20
                (client.get if "gz" not in url else _head(client))(url)
                line(name, "reachable")
            except Exception as exc:
                line(name, f"unreachable: {exc}", "fail")
    say("")
    say("BROKEN - fix the [!!] items first" if broken else
        ("OK, with the [..] items above degrading some output" if degraded else "all good"))
    return EXIT_ERROR if broken else EXIT_OK


def _head(client):
    def fetch(url):
        # only the first bytes - never download the whole file just to test connectivity
        return client.get(url, headers={"Range": "bytes=0-255"})
    return fetch


# ----------------------------------------------------------------------------
# parser
# ----------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--config", help="path to config.yaml (default: searched upward from the current folder)")
    common.add_argument("--offline", action="store_true", help="never touch the network; use the cache only")
    common.add_argument("--refresh", action="store_true", help="ignore cached downloads and fetch again")
    common.add_argument("--cache-dir", help="override paths.cache_dir")

    p = argparse.ArgumentParser(prog="vaxforge", description="VaxForge - stream A: S0 taxonomy, S1 acquisition, S2 annotation")
    p.add_argument("--version", action="version", version=f"vaxforge {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    def add_s1_s2_flags(sp: argparse.ArgumentParser, s1_flags: bool = True, s2_flags: bool = True) -> None:
        if s1_flags:
            sp.add_argument("--proteome-fasta", help="use your own protein FASTA instead of a UniProt proteome")
            sp.add_argument("--proteome-upid", help="pin a UniProt proteome id (e.g. UP000000425)")
        if s2_flags:
            sp.add_argument("--psortb-results", help="PSORTb output file from a run elsewhere (terse or normal format)")
            sp.add_argument("--gram", choices=list(s2.GRAM_SETTINGS),
                            help="override the Gram class inferred from taxonomy (the two '*_outer_membrane' classes follow PSORTb's own advice)")

    r = sub.add_parser("run", parents=[common], help="run S0-S2 (and any installed later stage)")
    r.add_argument("--pathogen", help='plain-language name, e.g. "Neisseria meningitidis"')
    r.add_argument("--taxid", type=int, help="NCBI taxid (skips name matching)")
    r.add_argument("--out", help="run directory (default: runs/<pathogen>)")
    r.add_argument("--run-id", help="run id recorded in artifacts (default: the folder name)")
    r.add_argument("--top-k", type=int, help="candidates carried into S7 (config pipeline.top_k)")
    r.add_argument("--stages", help="comma-separated subset, e.g. s0,s1,s2 (default: every stage that is installed)")
    r.add_argument("--non-interactive", action="store_true", help="never prompt; ambiguity exits with code 2")
    add_s1_s2_flags(r)
    r.set_defaults(func=cmd_run)

    rs = sub.add_parser("resolve", parents=[common], help="S0 only: resolve a name to a taxid (writes nothing)")
    rs.add_argument("name", nargs="?")
    rs.add_argument("--taxid", type=int)
    rs.add_argument("--json", action="store_true")
    rs.add_argument("--non-interactive", action="store_true")
    rs.set_defaults(func=cmd_resolve)

    for stage, helptext in (("acquire", "S1 only, on an existing run folder"), ("annotate", "S2 only, on an existing run folder")):
        sp = sub.add_parser(stage, parents=[common], help=helptext)
        sp.add_argument("run_dir")
        add_s1_s2_flags(sp, s1_flags=(stage == "acquire"), s2_flags=(stage == "annotate"))
        sp.set_defaults(func=_stage_cmd("s1" if stage == "acquire" else "s2"))

    w = sub.add_parser("warm-cache", parents=[common], help="prefetch S0+S1 data so a demo can run offline")
    w.add_argument("--pathogens", action="append", default=[], help='comma-separated names; may be repeated')
    w.set_defaults(func=cmd_warm_cache)

    v = sub.add_parser("verify", parents=[common], help="check a run folder's digests, upstream chain and file hashes")
    v.add_argument("run_dir")
    v.set_defaults(func=cmd_verify)

    sh = sub.add_parser("show", parents=[common], help="summarise a run folder")
    sh.add_argument("run_dir")
    sh.set_defaults(func=cmd_show)

    d = sub.add_parser("doctor", parents=[common], help="check tools, cache and (with --online) network access")
    d.add_argument("--online", action="store_true")
    d.set_defaults(func=cmd_doctor)

    # sub-commands contributed by other streams
    for name in dict.fromkeys(EXTENSION_MODULES):  # a module listed twice must register once
        modname = f"vaxforge.{name}"
        try:
            mod = importlib.import_module(modname)
        except ModuleNotFoundError as exc:
            if exc.name == modname:
                continue
            raise
        reg = getattr(mod, "register_cli", None)
        if callable(reg):
            reg(sub, common)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")      # never crash on a console that cannot show a character
        except Exception:
            pass
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        return 130
    except s0.NeedsUserInput as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_INPUT
    except (CacheMiss, OfflineError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_DATA
    except (ConfigError, ContractError, s0.TaxonomyError, ToolError, HttpError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
