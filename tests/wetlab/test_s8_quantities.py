"""S8 reagent quantities: an optional `quantities` list on each step that has a robot session (name, microlitres, source "S9 placeholder plan").

They are computed lazily from the S9 plans (`with_quantities`), never in `build_protocol`, which stays pure. A step with no robot session keeps
no quantities. Every number follows from `RobotParams`, whose values are all placeholders, and the markdown says so.
"""

from __future__ import annotations

import math
import shutil
from pathlib import Path

import pytest

from vaxforge import s8_protocol
from vaxforge import s9_sessions
from vaxforge.artifacts import RunStore
from vaxforge.config import Config
from vaxforge.s8_protocol import Candidate, Protocol, StepKind, build_protocol, plan_protocol, render_markdown, step_quantities, with_quantities
from vaxforge.s9_sessions import BUILDERS, HUMAN_ONLY, RobotParams, plan_sessions

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
CAND = Candidate(candidate_id="c1", protein_name="fHbp", rank=1, length_aa=274)
SOURCE = "S9 placeholder plan"


def copy_run(dest: Path) -> RunStore:
    shutil.copytree(FIXTURE, dest)
    return RunStore(dest, cache_dir=dest.parent / "cache")


def cfg(**streamc) -> Config:
    return Config(dict(streamc=streamc))


@pytest.fixture(scope="module")
def base() -> Protocol:
    return build_protocol(CAND)


@pytest.fixture(scope="module")
def plans(base):
    return plan_sessions(base, RobotParams())


@pytest.fixture(scope="module")
def quantified(base, plans) -> Protocol:
    return with_quantities(base, plans=plans)


def step(p: Protocol, kind: StepKind):
    return next(s for s in p.steps if s.kind is kind)


def qmap(s) -> dict:
    return {q.name: q.microlitres for q in s.quantities}


# =========================================================================== build_protocol stays pure; the quantities come from the plans


def test_build_protocol_never_plans_sessions_and_leaves_every_step_without_quantities(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("build_protocol must not build S9 plans")

    monkeypatch.setattr(s9_sessions, "plan_sessions", boom)
    p = build_protocol(CAND)
    assert all(s.quantities == [] and s.narrative is None and s.narrative_source is None for s in p.steps) and p.cost is None


def test_with_quantities_returns_a_copy_and_the_original_is_untouched(base, quantified):
    assert quantified is not base and all(s.quantities == [] for s in base.steps)
    assert [s.model_copy(update={"quantities": []}) for s in quantified.steps] == base.steps  # nothing but the quantities differs


def test_steps_with_a_robot_session_list_the_reagents_the_plan_loads_and_the_others_list_none(base, quantified, plans):
    sessions_of = {sid: [p for p in plans if sid in p.covers] for sid in (s.step_id for s in base.steps)}
    for s in quantified.steps:
        if s.kind in BUILDERS:
            assert len(sessions_of[s.step_id]) == len(BUILDERS[s.kind]) and s.quantities, s.step_id
            expected: dict[str, float] = {}
            for plan in sessions_of[s.step_id]:
                for liq in plan.liquids:
                    if liq.source == "robot_loaded":
                        expected[liq.reagent] = expected.get(liq.reagent, 0) + liq.volume_ul
            assert qmap(s) == expected and list(qmap(s)) == list(expected), s.step_id  # same names, same sums, same (deck) order
            assert all(q.source == SOURCE and q.microlitres > 0 for q in s.quantities)
        else:
            assert s.kind in HUMAN_ONLY and s.quantities == [], s.step_id  # gene synthesis, THP-1 culture: nothing invented
    assert {StepKind.GENE_SYNTHESIS, StepKind.THP1_CULTURE} == {s.kind for s in quantified.steps if not s.quantities}


def test_the_plasmid_assembly_volumes_follow_from_robotparams_by_hand(quantified):
    p = RobotParams()
    shared = p.assembly_vector_ul + p.assembly_buffer_ul + p.assembly_enzyme_ul
    water_drawn = (p.assembly_total_ul - shared - p.assembly_insert_ul) + (p.assembly_total_ul - shared)  # candidate well + no-insert control well
    dead = p.dead_volume_ul["tubes"]
    got = qmap(step(quantified, StepKind.PLASMID_ASSEMBLY))
    assert got == {
        "nuclease-free water": math.ceil(water_drawn + dead),
        "assembly buffer (10x)": math.ceil(2 * p.assembly_buffer_ul + dead),  # both reactions
        "linearised expression vector": math.ceil(2 * p.assembly_vector_ul + dead),
        "insert (synthesised gene fragment)": math.ceil(p.assembly_insert_ul + dead),  # the candidate reaction only
        "assembly enzyme mix": math.ceil(2 * p.assembly_enzyme_ul + dead),
    }


def test_elisa_has_two_sessions_and_both_are_summed_into_its_one_step(base, plans):
    elisa = step(base, StepKind.ELISA)
    sessions = [p for p in plans if elisa.step_id in p.covers]
    assert [p.session_id.split("_", 1)[1] for p in sessions] == ["elisa_setup", "elisa_develop"]
    got = qmap(step(with_quantities(base, plans=plans), StepKind.ELISA))
    assert {"assay diluent", "wash buffer", "detection antibody", "streptavidin-HRP", "substrate", "stop solution"} <= set(got)
    doubled = step_quantities(plans + plans)  # the same reagent in two sessions of one step is added
    assert {q.name: q.microlitres for q in doubled[elisa.step_id]} == {k: 2 * v for k, v in got.items()}


def test_quantities_are_whole_numbers_where_the_plan_gives_whole_numbers_and_json_has_exactly_three_keys(quantified):
    s = step(quantified, StepKind.LYSIS)
    assert [q.model_dump() for q in s.quantities] == [{"name": "lysis buffer", "microlitres": s.quantities[0].microlitres, "source": SOURCE}]
    assert isinstance(s.quantities[0].microlitres, int) and set(s.quantities[0].model_dump()) == {"name", "microlitres", "source"}


def test_plan_protocol_defaults_to_the_placeholder_params_and_is_deterministic(base, plans):
    assert [p.plan_hash() for p in plan_protocol(base)] == [p.plan_hash() for p in plans] == [p.plan_hash() for p in plan_protocol(base, RobotParams())]


# =========================================================================== the stage


def test_the_stage_artifact_carries_the_quantities_and_params_say_what_they_came_from(tmp_path):
    st = copy_run(tmp_path / "run")
    art = s8_protocol.run(st, cfg(proteins=["fhbp", "nada"], top_k=2))
    for entry in art.payload["candidates"]:
        proto = Protocol.model_validate(entry["protocol"])
        assert {s.kind for s in proto.steps if not s.quantities} == {StepKind.GENE_SYNTHESIS, StepKind.THP1_CULTURE}
        assert [s.model_dump(mode="json") for s in proto.steps] == entry["protocol"]["steps"]
        assert with_quantities(build_protocol(Candidate(candidate_id=entry["candidate_id"], protein_name=entry["protein_name"], rank=entry["rank"])),
                               plans=None).steps[1].quantities == proto.steps[1].quantities  # the same numbers as a direct call
    raw = art.payload["candidates"][0]["protocol"]["steps"][1]["quantities"][0]
    assert set(raw) == {"name", "microlitres", "source"} and raw["source"] == SOURCE
    assert art.params["robot_params"] == RobotParams().model_dump(mode="json") and st.verify() == []


def test_changing_robot_params_changes_the_quantities_and_the_digest(tmp_path):
    st = copy_run(tmp_path / "run")
    a = s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1))
    b = s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, robot_params={"assembly_vector_ul": 3}))
    va = qmap(step(Protocol.model_validate(a.payload["candidates"][0]["protocol"]), StepKind.PLASMID_ASSEMBLY))["linearised expression vector"]
    vb = qmap(step(Protocol.model_validate(b.payload["candidates"][0]["protocol"]), StepKind.PLASMID_ASSEMBLY))["linearised expression vector"]
    p = RobotParams()
    assert (va, vb) == (math.ceil(2 * p.assembly_vector_ul + 30), math.ceil(2 * 3 + 30)) and a.digest != b.digest
    assert s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1)).digest == a.digest  # and back: deterministic


def test_when_the_plan_cannot_be_built_no_step_gets_a_quantity(tmp_path):
    st = copy_run(tmp_path / "run")
    art = s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, robot_params={"assembly_total_ul": 5}))
    assert art.status == "ok" and all(s["quantities"] == [] for s in art.payload["candidates"][0]["protocol"]["steps"])


# =========================================================================== render_markdown


def test_markdown_lists_the_quantities_as_placeholders_before_the_controls(base, quantified):
    md = render_markdown(quantified)
    heading = "## Reagent quantities (PLACEHOLDERS from the S9 plan)"
    assert md.count(heading) == 1 and md.index("| s13 |") < md.index(heading) < md.index("## Controls and gates")
    section = md.split(heading)[1].split("## Controls and gates")[0]
    assert f"Source: {SOURCE}" in section and "NOT validated for any kit" in section and "streamc.robot_params" in section
    assert "a step with no robot session has no quantities" in section
    for s in quantified.steps:
        assert (f"- **{s.step_id} {s.title}**: " in section) == bool(s.quantities), s.step_id
    assert "- **s05 Small-scale expression and induction**: inducer stock " in section and " uL; vehicle (same solvent, no inducer) " in section
    assert len([line for line in md.splitlines() if line.startswith("| s")]) == len(base.steps)  # no quantity line looks like a step row
    assert md == render_markdown(quantified)


def test_a_protocol_without_quantities_renders_exactly_as_before(base):
    md = render_markdown(base)
    assert "Reagent quantities" not in md and "PLACEHOLDERS" not in md
