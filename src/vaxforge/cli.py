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
import importlib.util
import json
import os
import re
import shutil
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
def parse_override(item: str) -> tuple[str, object]:
    """``KEY=VALUE`` from ``--set``: the value is read as JSON when it parses (true, 3, "text", [1, 2]), else kept as text."""
    key, sep, raw = item.partition("=")
    if not sep or not key.strip():
        raise ConfigError(f"--set expects KEY=VALUE, for example --set s3.backend=scores_file (got {item!r})")
    try:
        value: object = json.loads(raw)
    except ValueError:
        value = raw
    return key.strip(), value


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
    for item in getattr(args, "overrides", None) or []:         # --set KEY=VALUE, applied last so it wins
        key, value = parse_override(item)
        cfg.set(key, value)
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


S2_OK_STATUSES = ("ok", "not_applicable")


def degraded_by_stage(store: RunStore) -> dict[str, list[str]]:
    """``payload.degraded`` (a list of short reason strings; stream B writes it for S3-S7) of every later stage that has one."""
    found: dict[str, list[str]] = {}
    for stage in store.stages_present():
        if stage in CORE_STAGES:
            continue
        try:
            payload = store.read_artifact(stage).payload
        except ContractError:
            continue
        reasons = payload.get("degraded") if isinstance(payload, dict) else None
        if isinstance(reasons, list) and reasons:
            found[stage] = [str(r) for r in reasons]
    return found


def closing_line(stages: list[str], s2_art: Artifact | None, degraded: dict[str, list[str]] | None = None) -> str:
    """Last line of `run`: names what ran and says so when the run was degraded.

    Degraded means: S2 virulence or localisation did not run (any status other than ok / not_applicable), or a later
    stage wrote a non-empty ``payload.degraded`` (``degraded`` maps stage -> reasons). The reasons are sentences, so the
    line only counts them per stage; ``cmd_run`` prints the sentences themselves above it.
    """
    parts: list[str] = []
    if s2_art is not None:
        summary = s2_art.payload.get("summary", {})
        by_status: dict[str, list[str]] = {}
        for label in ("virulence", "localisation"):
            status = summary.get(label, {}).get("run_status")
            if status and status not in S2_OK_STATUSES:
                by_status.setdefault(str(status), []).append(label)
        for status, labels in by_status.items():
            parts.append(f"S2 {' and '.join(labels)} {status}")
    for stage, reasons in (degraded or {}).items():
        parts.append(f"{stage.upper()} ({len(reasons)} reason{'s' if len(reasons) != 1 else ''})")
    note = f"degraded: {'; '.join(parts)}; " if parts else ""
    return f"done. artifacts: {', '.join(stages)}  ({note}verify: ok)"


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
    res = fn(store, cfg)
    # some stages return the artifact's Path rather than the Artifact: read it back
    return "ran", res if isinstance(res, Artifact) else store.read_artifact(stage)


def cmd_run(args: argparse.Namespace) -> int:
    cfg = load_from_args(args)
    wanted = [s.strip() for s in args.stages.split(",")] if args.stages else list(STAGE_ORDER)
    bad = [s for s in wanted if s not in STAGES]
    if bad:
        raise ConfigError(f"unknown stage(s) {bad}; known: {', '.join(STAGE_ORDER)}")
    has_target = bool(args.pathogen) or args.taxid is not None
    if "s0" in wanted and not has_target:
        raise ConfigError("give --pathogen NAME or --taxid N (only S0 needs it: with --stages that leave s0 out, "
                          "--out RUN_DIR alone runs the later stages on an existing run folder)")
    if args.out:
        out = Path(args.out)
    elif has_target:
        out = cfg.runs_dir / slugify(args.pathogen or f"taxid_{args.taxid}")
    else:
        raise ConfigError("give --out RUN_DIR: without --pathogen or --taxid there is no folder name to derive")
    store = RunStore(out, run_id=args.run_id, cache_dir=cfg.cache_dir)
    store.snapshot_config(cfg)
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
        if art.status != "ok" and art.warnings:
            say(f"        reason: {art.warnings[0]}")
        if art.status != "ok":
            return EXIT_ERROR
    problems = store.verify()
    if problems:
        say("\nintegrity problems:")
        for p in problems:
            say(f"  - {p}")
        return EXIT_ERROR
    s2_art = a2 if "s2" in wanted else (store.read_artifact("s2") if store.has("s2") else None)
    degraded = degraded_by_stage(store)
    if degraded:
        say("\ndegraded (why):")
        for stage, reasons in degraded.items():
            for reason in reasons:
                say(f"  {stage.upper()}: {reason}")
    say()
    say(closing_line(store.stages_present(), s2_art, degraded))
    return EXIT_OK


DEMO_STAGES = "s3,s4,s5,s6,s7,s8,s9,s9plus,s9_5,s10,s11,s12"
DEFAULT_DEMO_SCORES = Path(__file__).resolve().parents[2] / "docs" / "examples" / "vaxign_ml_scores.nmb_fixture.tsv"   # stream B, real Vaxign-ML


def cmd_demo_offline(args: argparse.Namespace) -> int:
    """Offline demo: copy the committed fixture (S0-S2 of N. meningitidis) and run S3-S12 on the copy, no network."""
    src = Path(args.fixture) if args.fixture else Path(__file__).resolve().parents[2] / "fixtures" / "nmb_fixture"
    if not (src / "manifest.json").is_file():
        raise ConfigError(f"fixture not found at {src} (expected a run folder with manifest.json; pass --fixture DIR)")
    out = Path(args.out)
    if out.exists() and any(out.iterdir()):
        if not args.force:
            raise ConfigError(f"{out} is not empty: pass --force to replace it, or choose another --out")
        shutil.rmtree(out)
    shutil.copytree(src, out, dirs_exist_ok=True)
    say(f"copied the committed fixture {src} -> {out}")
    say("  S0-S2 are the committed fixture, not a live run; its S2 localisation is a stand-in, not PSORTb (see fixtures/nmb_fixture/FIXTURE.md)")
    argv = ["run", "--out", str(out), "--stages", DEMO_STAGES, "--offline", "--non-interactive"]
    for flag, value in (("--config", args.config), ("--cache-dir", args.cache_dir)):
        if value:
            argv += [flag, str(value)]
    # Nothing may start a container or leave the machine in an "offline" demo: S3 would otherwise try the Vaxign-ML Docker
    # image first (backend auto), and S7's Boltz would ask the MSA server (msa: server) for the sequences.
    if args.rule_based and args.scores_file:
        raise ConfigError("--rule-based and --scores-file exclude each other")
    scores: Path | None = None
    if not args.rule_based:
        scores = Path(args.scores_file).resolve() if args.scores_file else (DEFAULT_DEMO_SCORES if DEFAULT_DEMO_SCORES.is_file() else None)
    if scores is not None:
        if not scores.is_file():
            raise ConfigError(f"scores file not found: {scores}")
        argv += ["--set", "s3.backend=scores_file", "--set", f"s3.scores_file={json.dumps(str(scores))}"]
        say(f"  S3 ranks with the precomputed scores in {scores}")
        if scores == DEFAULT_DEMO_SCORES:
            say("  note: these are real Vaxign-ML scores from the stock model, which was trained on all of Protegen and so saw the Bexsero antigens "
                "(holdout_enforced is false and S3 prints a LEAKAGE RISK warning): the Bexsero recall on this run is not a validation")
    else:
        argv += ["--set", "s3.backend=subtractive_filter"]
        say("  S3 uses the rule-based fallback ranker (no protegenicity term)")
    argv += ["--set", "s7.boltz.msa=empty"]
    for item in args.overrides:
        argv += ["--set", item]
    return main(argv)


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


def pipeline_tool_checks(cfg: Config) -> list[tuple[str, str, str]]:
    """One advisory line per external tool the later stages (S3-S12) use: ``(label, text, level)``.

    This only says found / not found (and, for the Docker image, whether the daemon and image are there). It never claims
    that a tool works: nothing is run, so a broken install still shows as found.
    """
    try:
        from .prediction.tools import resolve_executable
    except ImportError:                                       # stream B's package is not installed: plain PATH lookup
        def resolve_executable(cmd, default=None):            # type: ignore[misc]
            name = str(cmd or default or "").split()
            found = shutil.which(name[0]) if name else None
            return [found, *name[1:]] if found else None

    rows: list[tuple[str, str, str]] = []

    # S9: the Opentrons simulator runs in its own interpreter (Opentrons 7.5.0 needs Python 3.10)
    env = os.environ.get("VAXFORGE_OT_PYTHON")
    if env:
        if Path(env).is_file():
            rows.append(("Opentrons (S9)", f"VAXFORGE_OT_PYTHON = {env} (file exists; not probed)", "ok"))
        else:
            rows.append(("Opentrons (S9)", f"VAXFORGE_OT_PYTHON points at a file that does not exist: {env}", "warn"))
    else:
        here = Path(__file__).resolve()
        found_py = next((str(p) for root in (Path.cwd(), here.parents[2]) for venv in (".venv-ot", ".venv-wetlab")
                         for p in (root / venv / "Scripts" / "python.exe", root / venv / "bin" / "python") if p.is_file()), None)
        if found_py:
            rows.append(("Opentrons (S9)", f"interpreter found at {found_py} (not probed)", "ok"))
        else:
            rows.append(("Opentrons (S9)", "no interpreter found: S9 cannot simulate its code "
                                           "(set VAXFORGE_OT_PYTHON; tools/setup-ot-venv.sh makes one)", "warn"))

    # S3: Vaxign-ML runs from a Docker image
    docker = tools.find_executable(str(cfg.get("tools.docker", "docker")))
    image = str(cfg.get("s3.vaxign_ml.docker_image", "e4ong1031/vaxign-ml:latest"))
    state, detail = tools.docker_state(docker, image)
    rows.append(("Vaxign-ML (S3)", f"{image}: {detail}" + ("" if state == "ok" else "; S3 falls back to the rule-based ranking"),
                 "ok" if state == "ok" else "warn"))

    # S4-S7: command-line tools; None as the default means "must be set in config.yaml"
    for label, key, default, hint in (
            ("AlgPred2 (S4)", "tools.algpred2", "algpred2", "pip install algpred2"),
            ("ToxinPred2 (S4)", "tools.toxinpred2", "toxinpred2", "pip install toxinpred2"),
            ("NetMHCpan (S5)", "tools.netmhcpan", "netMHCpan", "licensed download; set tools.netmhcpan"),
            ("NetMHCIIpan (S5)", "tools.netmhciipan", "netMHCIIpan", "licensed download; set tools.netmhciipan"),
            ("BepiPred3 (S5)", "tools.bepipred3", None, "set tools.bepipred3 in config.yaml"),
            ("DiscoTope3 (S5)", "tools.discotope3", None, "set tools.discotope3 in config.yaml; S7 uses it too"),
            ("Boltz (S7)", "tools.boltz", "boltz", "pip install boltz")):
        argv = resolve_executable(cfg.get(key), default)
        if argv:
            rows.append((label, f"found: {argv[0]}", "ok"))
        else:
            rows.append((label, f"not found ({hint}); that analysis is skipped or degraded", "warn"))
    if importlib.util.find_spec("mhcflurry") is not None:
        rows.append(("MHCflurry (S5)", "python package found", "ok"))
    else:
        rows.append(("MHCflurry (S5)", "python package not found (pip install mhcflurry); class I falls back or is skipped", "warn"))
    return rows


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
    say("  later stages S3-S12 (advisory: found / not found only; this does not test that a tool works):")
    for label, text, level in pipeline_tool_checks(cfg):
        line(label, text, level)
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
    common.add_argument("--set", dest="overrides", action="append", default=[], metavar="KEY=VALUE",
                        help="override one config value for this run, e.g. --set s3.backend=scores_file "
                             "(the value is read as JSON when it parses, else as text; repeatable)")

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
    r.add_argument("--stages", help="comma-separated subset, e.g. s0,s1,s2 (default: every stage that is installed). "
                                    "Without s0 in the list, --pathogen is not needed: --out names an existing run folder")
    r.add_argument("--non-interactive", action="store_true", help="never prompt; ambiguity exits with code 2")
    add_s1_s2_flags(r)
    r.set_defaults(func=cmd_run)

    dm = sub.add_parser("demo-offline", parents=[common],
                        help="offline demo: copy the committed fixture (S0-S2) and run S3-S12 on the copy, no network")
    dm.add_argument("--out", default="runs/demo", help="folder for the copy (default: runs/demo)")
    dm.add_argument("--fixture", help="a run folder to copy instead of fixtures/nmb_fixture")
    dm.add_argument("--scores-file", help="precomputed protegenicity scores for S3 (sets s3.backend=scores_file and s3.scores_file); "
                                          "default: docs/examples/vaxign_ml_scores.nmb_fixture.tsv when it exists")
    dm.add_argument("--rule-based", action="store_true", help="rank S3 with the rule-based fallback instead of a scores file")
    dm.add_argument("--force", action="store_true", help="replace --out when it already has files")
    dm.set_defaults(func=cmd_demo_offline)

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
