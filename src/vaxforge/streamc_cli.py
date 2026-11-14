"""Stream C command line: S8 protocol -> S9 robot code (+ S9+ animation) -> S10 animal study.

    python -m vaxforge.streamc_cli run runs/nmb --proteins fhbp,nada,nhba --out out/stream_c --top-k 3
    python -m vaxforge.streamc_cli compile-protocol out/stream_c/candidates/sp_q9jxv4_fhbp_neimb/protocol.json --out out
    python -m vaxforge.streamc_cli compile-protocol runs/nmb                 # a run folder: writes the s9 artifact
    python -m vaxforge.streamc_cli animal-study runs/nmb --proteins fhbp,nada --species hla-transgenic --power 0.8

`run_dir` is either a CONTRACT run folder (it holds `s2_annotate.json`: candidates come from stream B's S7 final order when the folder
has S3-S7 artifacts, else from the S2 proteins you name with `--proteins`, see streamc_adapter) or a folder of plain JSON
(`candidates.json` + `pathogen.json`).

The last two mirror the README quick-start (`vaxforge compile-protocol`, `vaxforge animal-study`): the same
handlers (`cmd_compile_protocol`, `cmd_animal_study`) are registered in the `vaxforge` command by
`s9_robot.register_cli` and `s10_animal.register_cli`. On a run folder they run the S9 / S10 STAGE and write the
stage artifact; on plain files they behave as before.

Output of `run`:
    <out>/stream_c_summary.md|json
    <out>/animal_study/animal_study.md|json                  one design for the whole shortlist
    <out>/candidates/<id>/protocol.json|md                   S8   (<id>: unique, see streamc_models.candidate_dir_names)
    <out>/candidates/<id>/robot/<backend>/*.py|setup.md|sim.json|grade.json   S9
    <out>/candidates/<id>/animation/...                      S9+
    <out>/candidates/<id>/robot_report.md, workflow.md

Exit code: 0 only if every S9 session passed AND the S10 design validated. UNVERIFIED (no
simulator) is not a pass. 2 = unusable input, or nothing to do (a stage that wrote `ok` + `payload.status: "not_run"`).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from vaxforge import s9_robot, s10_animal
from vaxforge.artifacts import Artifact, ContractError, RunStore
from vaxforge.config import ConfigError, load_config
from vaxforge.s8_protocol import (
    READOUTS,
    Protocol,
    ProtocolConfig,
    build_protocol,
    render_markdown as render_protocol_md,
    to_json,
    validate_protocol,
)
from vaxforge.s9_llm import CodegenBackend
from vaxforge.s9_robot import ProtocolInvalid, backends_from_choice, compile_protocol
from vaxforge.s9_sessions import RobotParams
from vaxforge.s10_animal import (
    Route,
    Species,
    StudyConfig,
    StudyGoal,
    design_study,
    outstanding_human_inputs,
    render_markdown as render_study_md,
    validate_design,
)
from vaxforge.s10_animal import to_json as study_json
from vaxforge.streamc_adapter import RunInputs, load_inputs, load_inputs_from_store
from vaxforge.streamc_models import candidate_dir_names

SPECIES_FLAGS = {
    "hla-transgenic": (Species.MOUSE_HLA_TRANSGENIC, StudyGoal.HLA_EPITOPE_VALIDATION),
    "mouse": (Species.MOUSE_WILD_TYPE, StudyGoal.BASIC_IMMUNOGENICITY),
    "wild-type": (Species.MOUSE_WILD_TYPE, StudyGoal.BASIC_IMMUNOGENICITY),
    "rabbit": (Species.RABBIT, StudyGoal.POLYCLONAL_SERA),
}
DEFAULT_TOP_K = 3


def _rel(base: Path, p: Path) -> str:
    """Summary paths are relative to the output directory (portable; no local usernames)."""
    return p.resolve().relative_to(base.resolve()).as_posix()


def _safe(s: str) -> str:
    """Directory name for ONE id. Use `candidate_dir_names` for several: it keeps names that collide apart."""
    return candidate_dir_names([s])[0]


def _cell(x: object) -> str:
    """A markdown table cell: ids such as `sp|Q9JXV4|FHBP_NEIMB` contain pipes."""
    return str(x).replace("|", "\\|").replace("\n", " ")


def _input_error(message: object) -> int:
    """Report unusable input on stderr as one line (without pydantic's doc URL) and return the exit code 2."""
    text = " ".join(str(message).split()).split(" For further information")[0]
    print(f"input error: {text}", file=sys.stderr)
    return 2


def study_config_from_args(a: argparse.Namespace) -> StudyConfig:
    base: dict[str, Any] = {}
    if getattr(a, "study_config", None):
        base = json.loads(Path(a.study_config).read_text(encoding="utf-8"))
    if getattr(a, "species", None):
        species, goal = SPECIES_FLAGS[a.species]
        base["species"] = species
        base.setdefault("goal", goal)
    if getattr(a, "goal", None):
        base["goal"] = StudyGoal(a.goal)
    if getattr(a, "power", None) is not None:
        base["power"] = a.power
    if getattr(a, "adjuvant", None):
        base["adjuvant"] = a.adjuvant
    if getattr(a, "route", None):
        base["route"] = Route(a.route)
    if getattr(a, "hla_lines", None):
        base["hla_transgenic_lines"] = [x.strip() for x in a.hla_lines.split(",") if x.strip()]
    lv = getattr(a, "licensed_vaccine", None)
    if lv in ("yes", "no"):
        base["licensed_vaccine_exists"] = lv == "yes"
    return StudyConfig(**base)


# --------------------------------------------------------------------------- run


class CandidateOutcome(BaseModel):
    candidate_id: str
    protein_name: str
    protocol_file: str
    s9_tally: dict[str, dict[str, int]] = Field(default_factory=dict)  # backend -> outcome -> n
    s9_all_pass: bool = False
    robot_report: str | None = None


class StreamCSummary(BaseModel):
    pathogen: str
    candidates: list[CandidateOutcome]
    study_file: str | None = None
    study_errors: list[str] = Field(default_factory=list)
    study_human_inputs: int = 0
    warnings: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)
    ok: bool = False


def run_stream_c(
    inputs: RunInputs,
    out_dir: str | Path,
    *,
    top_k: int = 3,
    backends: list[CodegenBackend] | None = None,
    simulate_runs: bool = True,
    ot_python: str | None = None,
    robot_params: RobotParams | None = None,
    protocol_config: ProtocolConfig | None = None,
    study_config: StudyConfig | None = None,
    s12_status: str = "pending",
    animate: bool = True,
) -> StreamCSummary:
    out = Path(out_dir)
    shortlist = inputs.candidates[:top_k]
    # One folder per candidate, unique even when ids differ only in case or punctuation (`NadA` / `nada`, `P1/2` / `P1_2`):
    # the first keeps the plain name, later ones get -2, -3 in rank order, so none can overwrite another.
    dir_names = candidate_dir_names([c.candidate_id for c in shortlist])
    outcomes: list[CandidateOutcome] = []
    for cand, dir_name in zip(shortlist, dir_names):
        cdir = out / "candidates" / dir_name
        proto = build_protocol(cand, protocol_config)
        problems = validate_protocol(proto)
        if problems:
            raise ProtocolInvalid(f"{cand.candidate_id}: " + "; ".join(problems))
        cdir.mkdir(parents=True, exist_ok=True)
        (cdir / "protocol.json").write_text(to_json(proto), encoding="utf-8")
        (cdir / "protocol.md").write_text(render_protocol_md(proto), encoding="utf-8")
        rep = compile_protocol(proto, cdir, params=robot_params, backends=backends, simulate_runs=simulate_runs,
                               ot_python=ot_python, s12_status=s12_status, animate=animate)  # type: ignore[arg-type]
        outcomes.append(CandidateOutcome(
            candidate_id=cand.candidate_id, protein_name=cand.protein_name, protocol_file=_rel(out, cdir / "protocol.json"),
            s9_tally={r.label: r.tally for r in rep.runs}, s9_all_pass=all(r.all_pass for r in rep.runs),
            robot_report=_rel(out, cdir / "robot_report.md")))

    design = design_study(shortlist, inputs.pathogen, study_config)
    study_errors = validate_design(design)
    sdir = out / "animal_study"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / "animal_study.json").write_text(study_json(design), encoding="utf-8")
    (sdir / "animal_study.md").write_text(render_study_md(design), encoding="utf-8")

    summary = StreamCSummary(
        pathogen=inputs.pathogen.name, candidates=outcomes, study_file=_rel(out, sdir / "animal_study.md"), study_errors=study_errors,
        study_human_inputs=len(outstanding_human_inputs(design)), warnings=inputs.warnings, assumptions=inputs.assumptions,
        ok=all(o.s9_all_pass for o in outcomes) and not study_errors)
    (out / "stream_c_summary.json").write_text(summary.model_dump_json(indent=1), encoding="utf-8")
    (out / "stream_c_summary.md").write_text(render_summary(summary), encoding="utf-8")
    return summary


def render_summary(s: StreamCSummary) -> str:
    L = [f"# Stream C summary - {s.pathogen}", "",
         "S8 protocols -> S9 robot code (+S9+ animation) -> S10 animal study design. Everything here is a DESIGN: no assay results exist, "
         "every volume/time is a placeholder, and a robot PASS means the code simulated and broke none of the checked rules.", "",
         "| Candidate | S9 outcome per backend | Report |", "|---|---|---|"]
    for c in s.candidates:
        L.append(f"| {_cell(c.candidate_id)} ({_cell(c.protein_name)}) | {_cell(c.s9_tally)} | {_cell(c.robot_report)} |")
    L += ["", f"Animal study design: `{s.study_file}` - {len(s.study_errors)} validation error(s), {s.study_human_inputs} item(s) needing human input."]
    if s.study_errors:
        L += [f"- {e}" for e in s.study_errors]
    if s.warnings:
        L += ["", "## Input warnings", ""] + [f"- {w}" for w in s.warnings]
    L += ["", "## Assumptions about upstream data (verify when A/B's artifacts land)", ""] + [f"- {a}" for a in s.assumptions]
    L += ["", f"**Overall: {'OK' if s.ok else 'NOT OK'}**", ""]
    return "\n".join(L)


# --------------------------------------------------------------------------- CLI


def _backends(choice: str, model: str | None) -> list[CodegenBackend]:
    return backends_from_choice(choice, model)


def add_study_flags(p: argparse.ArgumentParser) -> None:
    p.add_argument("--species", choices=sorted(SPECIES_FLAGS))
    p.add_argument("--goal", choices=[g.value for g in StudyGoal])
    p.add_argument("--power", type=float)
    p.add_argument("--adjuvant")
    p.add_argument("--route", choices=[r.value for r in Route])
    p.add_argument("--hla-lines", help="comma-separated HLA-transgenic line names")
    p.add_argument("--licensed-vaccine", choices=["yes", "no", "unknown"], default="unknown")
    p.add_argument("--study-config", help="JSON file with any StudyConfig fields")
    p.add_argument("--arms", help="accepted for README compatibility; arm count is derived from the candidates (ignored)")


def add_input_args(p: argparse.ArgumentParser) -> None:
    """`run_dir`, `--candidates`, `--pathogen`, `--proteins`. (`--config` comes from `vaxforge`'s common parser or `main`.)"""
    p.add_argument("run_dir", nargs="?", help="a run folder (contract artifacts: name the proteins with --proteins or config "
                                              "streamc.proteins) or a folder holding candidates.json (+ pathogen.json)")
    p.add_argument("--candidates", help="explicit candidates JSON")
    p.add_argument("--pathogen", help="explicit pathogen JSON")
    p.add_argument("--proteins", help="comma-separated gene / entry name / accession / protein_id from the run's S2 table "
                                      "(run folders without S3-S7 artifacts only: you choose and the order is the rank; "
                                      "with S3-S7 artifacts the candidates are S7's final order and this is ignored)")


def add_animal_study_args(p: argparse.ArgumentParser) -> None:
    """Every flag of `animal-study`; shared by this module's own parser and `s10_animal.register_cli`."""
    add_input_args(p)
    add_study_flags(p)
    p.add_argument("--out", default=None, help="write animal_study.md and .json here (a run folder also keeps them under data/s10)")
    p.add_argument("--top-k", type=int, default=None, help=f"candidates to include (default: config streamc.top_k, else {DEFAULT_TOP_K})")


def report_stage(art: Artifact) -> int:
    """Print what a stage wrote and return its exit code.

    A normal `ok` is 0. `ok` with `payload.status == "not_run"` is 2 (nothing to do or nothing simulated: the reasons are printed, the exit
    code is the old "needs user input" one for these explicit commands, while `vaxforge run` sees an `ok` artifact and goes on). `ok` with
    `payload.status == "refused"` is 1, and so is `failed`.
    """
    n = len(art.warnings)
    pl = art.payload if isinstance(art.payload, dict) else {}
    state = pl.get("status") if art.status == "ok" else None
    print(f"{art.stage}: {art.status}" + (f", payload.status {state}" if state else "") + (f" ({n} warning(s))" if n else ""))
    if art.status == "ok":
        summary = pl.get("summary") if isinstance(pl.get("summary"), dict) else {}
        if art.stage == "s9" and summary.get("n_sessions"):
            print(f"  sessions: {summary['outcomes']}; files under data/s9/<candidate>/<backend>/")
        elif art.stage == "s10" and isinstance(pl.get("study_file"), dict):
            print(f"  study: {pl['study_file']['path']}; {pl['outstanding_human_inputs']} item(s) need human input")
    if art.status != "ok" or state:
        for w in art.warnings[:5]:
            print(f"  ! {w}", file=sys.stderr)
    if art.status != "ok":
        return 1
    return {None: 0, "not_run": 2}.get(state, 1)


def _is_contract_run(a: argparse.Namespace) -> bool:
    """A folder with an S2 artifact is a contract run; explicit --candidates always means the plain-file route."""
    run_dir = getattr(a, "run_dir", None)
    return bool(run_dir) and not getattr(a, "candidates", None) and (Path(run_dir) / "s2_annotate.json").is_file()


def load_cli_inputs(a: argparse.Namespace) -> tuple[RunInputs, int]:
    """(inputs, how many candidates to use) for the contract route or the plain-file route."""
    top_k = getattr(a, "top_k", None)
    if _is_contract_run(a):
        cfg = load_config(getattr(a, "config", None))
        store = RunStore(a.run_dir, cache_dir=cfg.cache_dir)
        inputs = load_inputs_from_store(store, cfg, proteins=getattr(a, "proteins", None), top_k=top_k)
        return inputs, len(inputs.candidates)
    inputs = load_inputs(a.run_dir, candidates_path=a.candidates, pathogen_path=a.pathogen)
    return inputs, (DEFAULT_TOP_K if top_k is None else top_k)


def compile_protocol_file(path: str | Path, out: str | Path | None, *, backend: str = "template", model: str | None = None,
                          simulate: bool = True, ot_python: str | None = None) -> int:
    """S9 for ONE bare S8 protocol.json (not a run folder): write the sessions under `out`."""
    if not out:
        return _input_error("--out is required when compiling a bare protocol.json (a run folder keeps its files under data/s9)")
    try:
        proto = Protocol.model_validate_json(Path(path).read_text(encoding="utf-8"))
    except OSError as exc:
        return _input_error(f"cannot read {path}: {exc.strerror or exc}")
    except ValueError as exc:  # JSON syntax or schema; pydantic's ValidationError is a ValueError
        return _input_error(f"{path} is not a valid S8 protocol: {exc}")
    try:
        rep = compile_protocol(proto, out, backends=_backends(backend, model), simulate_runs=simulate, ot_python=ot_python)
    except ProtocolInvalid as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    for run in rep.runs:
        print(f"[{run.label}] {run.tally}")
    return 0 if all(x.all_pass for x in rep.runs) else 1


def cmd_compile_protocol(a: argparse.Namespace) -> int:
    """`compile-protocol SOURCE`: a run folder (or its s8_protocol.json) runs the S9 stage; a bare protocol.json is compiled to --out."""
    src = Path(a.source)
    if src.is_dir() or src.name == RunStore.artifact_name("s8"):
        run_dir = src if src.is_dir() else src.parent
        cfg = load_config(getattr(a, "config", None))
        if getattr(a, "backend", None):
            cfg.set("streamc.backend", a.backend)
        if getattr(a, "model", None):
            cfg.set("streamc.model", a.model)
        if getattr(a, "no_simulate", False):
            cfg.set("streamc.simulate_runs", False)
        if getattr(a, "ot_python", None):
            cfg.set("streamc.ot_python", a.ot_python)
        if getattr(a, "s12", None):
            cfg.set("streamc.s12_status", a.s12)
        if getattr(a, "out", None):
            print(f"note: --out is ignored for a run folder; files are under {run_dir / 'data' / 's9'}", file=sys.stderr)
        store = RunStore(run_dir, cache_dir=cfg.cache_dir)
        return report_stage(s9_robot.run(store, cfg))
    return compile_protocol_file(src, getattr(a, "out", None), backend=getattr(a, "backend", None) or "template",
                                 model=getattr(a, "model", None), simulate=not getattr(a, "no_simulate", False),
                                 ot_python=getattr(a, "ot_python", None))


def _study_config_or_error(a: argparse.Namespace) -> StudyConfig | int:
    try:
        return study_config_from_args(a)
    except OSError as exc:
        return _input_error(f"cannot read --study-config {a.study_config}: {exc.strerror or exc}")
    except ValueError as exc:  # bad JSON, or a value outside StudyConfig's limits (e.g. --power 80 instead of 0.8)
        return _input_error(f"invalid study settings: {exc}")


def cmd_animal_study(a: argparse.Namespace) -> int:
    """`animal-study RUN_DIR`: a run folder runs the S10 stage (writes the s10 artifact); plain candidate files print the design."""
    top_k = getattr(a, "top_k", None)
    if top_k is not None and top_k < 1:  # 0 would build nothing; a negative slice would silently drop the last candidate
        return _input_error(f"--top-k must be at least 1 (got {top_k})")
    if _is_contract_run(a):
        study_cfg = _study_config_or_error(a)
        if isinstance(study_cfg, int):
            return study_cfg
        cfg = load_config(getattr(a, "config", None))
        given = study_cfg.model_dump(mode="json", exclude_unset=True)  # only what a flag or --study-config set
        if given:
            base = cfg.get("streamc.study")
            cfg.set("streamc.study", {**(base if isinstance(base, dict) else {}), **given})
        if getattr(a, "proteins", None):
            cfg.set("streamc.proteins", a.proteins)
        if top_k is not None:
            cfg.set("streamc.top_k", top_k)
        store = RunStore(a.run_dir, cache_dir=cfg.cache_dir)
        art = s10_animal.run(store, cfg)
        if a.out and art.payload.get("design"):  # a not_run artifact has design: None
            d = Path(a.out)
            d.mkdir(parents=True, exist_ok=True)
            (d / "animal_study.md").write_text(store.resolve(art.payload["study_file"]).read_text(encoding="utf-8"), encoding="utf-8")
            (d / "animal_study.json").write_text(json.dumps(art.payload["design"], indent=2), encoding="utf-8")
            print(f"wrote {d / 'animal_study.md'}")
        return report_stage(art)

    try:
        inputs = load_inputs(a.run_dir, candidates_path=a.candidates, pathogen_path=a.pathogen)
    except (FileNotFoundError, ValueError) as exc:
        return _input_error(exc)
    study_cfg = _study_config_or_error(a)
    if isinstance(study_cfg, int):
        return study_cfg
    design = design_study(inputs.candidates[:(DEFAULT_TOP_K if top_k is None else top_k)], inputs.pathogen, study_cfg)
    errs = validate_design(design)
    text = render_study_md(design)
    if a.out:
        d = Path(a.out)
        d.mkdir(parents=True, exist_ok=True)
        (d / "animal_study.md").write_text(text, encoding="utf-8")
        (d / "animal_study.json").write_text(study_json(design), encoding="utf-8")
        print(f"wrote {d / 'animal_study.md'}")
    else:
        print(text)
    for e in errs:
        print(f"validation: {e}", file=sys.stderr)
    return 1 if errs else 0


def cmd_run(a: argparse.Namespace) -> int:
    """`run`: S8 -> S9 -> S9+ -> S10 for the shortlist, written as a plain tree under --out (not as stage artifacts)."""
    try:
        inputs, top_k = load_cli_inputs(a)
    except (FileNotFoundError, ValueError) as exc:  # includes ProteinSelectionError
        return _input_error(exc)
    study_cfg = _study_config_or_error(a)
    if isinstance(study_cfg, int):
        return study_cfg
    names = [x.strip() for x in a.readouts.split(",") if x.strip()]
    if any(x not in READOUTS for x in names):
        return _input_error(f"unknown --readouts {[x for x in names if x not in READOUTS]}; choose from {sorted(READOUTS)}")
    pcfg = ProtocolConfig(readouts=[READOUTS[x] for x in names], endotoxin_limit_eu_per_ug=a.endotoxin_limit)
    try:
        params = RobotParams.model_validate_json(Path(a.robot_params).read_text(encoding="utf-8")) if a.robot_params else None
    except OSError as exc:
        return _input_error(f"cannot read --robot-params {a.robot_params}: {exc.strerror or exc}")
    except ValueError as exc:
        return _input_error(f"{a.robot_params} is not valid RobotParams JSON: {exc}")
    try:
        summary = run_stream_c(inputs, a.out, top_k=top_k, backends=_backends(a.backend, a.model), simulate_runs=not a.no_simulate,
                               ot_python=a.ot_python, robot_params=params, protocol_config=pcfg, study_config=study_cfg,
                               s12_status=a.s12, animate=not a.no_animation)
    except ProtocolInvalid as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    for c_ in summary.candidates:
        print(f"{c_.candidate_id}: S9 {c_.s9_tally}")
    print(f"S10: {len(summary.study_errors)} validation error(s); summary -> {Path(a.out) / 'stream_c_summary.md'}")
    return 0 if summary.ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="streamc", description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="S8 -> S9 -> S9+ -> S10 for the top-k candidates")
    add_input_args(r)
    add_study_flags(r)
    r.add_argument("--config", help="config.yaml (streamc.* settings and paths) for a run folder")
    r.add_argument("--out", required=True)
    r.add_argument("--top-k", type=int, default=None, help=f"default {DEFAULT_TOP_K}")
    r.add_argument("--backend", choices=["template", "llm", "both"], default="template")
    r.add_argument("--model")
    r.add_argument("--no-simulate", action="store_true")
    r.add_argument("--no-animation", action="store_true")
    r.add_argument("--ot-python")
    r.add_argument("--readouts", default="elisa,rt_qpcr", help="comma list of: " + ",".join(READOUTS))
    r.add_argument("--endotoxin-limit", type=float, help="EU/ug release limit for the LAL gate (no default: a lab decision)")
    r.add_argument("--robot-params", help="JSON file overriding RobotParams")
    r.add_argument("--s12", choices=["pending", "cleared"], default="pending")

    c = sub.add_parser("compile-protocol", help="S9 only: a run folder (writes the s9 artifact) or one S8 protocol.json")
    c.add_argument("source", help="a run folder or its s8_protocol.json, or a bare protocol.json (needs --out)")
    c.add_argument("--out", help="output folder for a bare protocol.json")
    c.add_argument("--target", default="opentrons-ot2", choices=["opentrons-ot2"])
    c.add_argument("--backend", choices=["template", "llm", "both"], default=None, help="default: config streamc.backend, else template")
    c.add_argument("--model")
    c.add_argument("--no-simulate", action="store_true")
    c.add_argument("--ot-python")
    c.add_argument("--s12", choices=["pending", "cleared"], default=None)
    c.add_argument("--config", help="config.yaml (streamc.* settings and paths) for a run folder")

    s = sub.add_parser("animal-study", help="S10 only")
    add_animal_study_args(s)
    s.add_argument("--config", help="config.yaml (streamc.* settings and paths) for a run folder")

    a = ap.parse_args(argv)
    if getattr(a, "top_k", None) is not None and a.top_k < 1:  # 0 would build nothing; a negative slice would silently drop the last candidate
        return _input_error(f"--top-k must be at least 1 (got {a.top_k})")
    try:
        if a.cmd == "compile-protocol":
            return cmd_compile_protocol(a)
        if a.cmd == "animal-study":
            return cmd_animal_study(a)
        return cmd_run(a)
    except (ContractError, ConfigError) as exc:  # a run folder that is not usable, or a bad streamc.* setting
        return _input_error(exc)


if __name__ == "__main__":
    sys.exit(main())
