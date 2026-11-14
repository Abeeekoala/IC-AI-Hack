"""S9 session library (plans only; no simulator needed)."""

import pytest
from pydantic import ValidationError

from vaxforge.s8_protocol import Candidate, ProtocolConfig, StepKind, build_protocol
from vaxforge.s9_plan import PlanError
from vaxforge.s9_sessions import (
    BUILDERS,
    DEFAULT_CONDITIONS,
    HUMAN_ONLY,
    RobotParams,
    build_assembly,
    build_elisa_develop,
    build_induction,
    build_stimulation,
    condition_wells,
    plan_sessions,
    seq_wells,
)

CAND = Candidate(candidate_id="c1", protein_name="fHbp", rank=1)


def proto(readouts=None):
    cfg = ProtocolConfig(readouts=readouts or [StepKind.ELISA, StepKind.RT_QPCR, StepKind.FACS])
    return build_protocol(CAND, cfg)


def step(p, kind):
    return next(s for s in p.steps if s.kind is kind)


def test_every_robot_assisted_step_gets_its_sessions():
    p = proto()
    plans = plan_sessions(p)
    covered = {sid for pl in plans for sid in pl.covers}
    for s in p.steps:
        if s.kind in HUMAN_ONLY:
            assert s.step_id not in covered, f"{s.kind} must stay a human step"
        else:
            assert s.step_id in covered, f"{s.kind} has no robot session"
    assert [pl.session_id for pl in plans].count("s12_elisa_setup") == 1
    assert {pl.session_id for pl in plans} >= {"s12_elisa_setup", "s12_elisa_develop"}


def test_session_ids_start_with_their_step_id_and_are_unique():
    plans = plan_sessions(proto())
    ids = [pl.session_id for pl in plans]
    assert len(ids) == len(set(ids))
    for pl in plans:
        assert pl.session_id.startswith(pl.covers[0])


def test_every_session_hands_off_to_a_human_and_declares_its_liquids():
    for pl in plan_sessions(proto()):
        assert any(a.op == "pause" and a.text.startswith("HUMAN [") for a in pl.actions), pl.session_id
        assert pl.liquids or pl.session_id.endswith("lysis"), pl.session_id  # lysis starts from dry pellets
        for a in pl.actions:
            if a.op == "pause":
                assert a.step in pl.covers


def test_human_only_kinds_have_no_builder():
    assert not (set(BUILDERS) & set(HUMAN_ONLY))
    assert StepKind.GENE_SYNTHESIS in HUMAN_ONLY and StepKind.THP1_CULTURE in HUMAN_ONLY


def test_stimulation_layout_covers_every_s8_control():
    p = proto()
    pl = build_stimulation(p, step(p, StepKind.ANTIGEN_STIMULATION), RobotParams())
    layout = next(iter(pl.layouts.values()))
    text = " ".join(layout).lower()
    for kw in ("vehicle", "lps positive", "endotoxin-matched", "polymyxin", "mock"):
        assert kw in text
    cw = condition_wells(RobotParams())
    assert len(cw) == len(DEFAULT_CONDITIONS) and all(len(ws) == 3 for ws in cw.values())
    assert cw["vehicle / buffer only"] == ["A1", "A2", "A3"]


def test_dropping_an_s8_control_from_the_layout_is_refused():
    p = proto()
    conditions = [c for c in DEFAULT_CONDITIONS if "polymyxin" not in c.lower()]
    with pytest.raises(PlanError, match="polymyxin"):
        build_stimulation(p, step(p, StepKind.ANTIGEN_STIMULATION), RobotParams(conditions=conditions))


def test_parameter_validation():
    with pytest.raises(ValidationError):
        RobotParams(conditions=[f"c{i}" for i in range(9)])  # 8 plate rows at most
    with pytest.raises(ValidationError):
        RobotParams(replicates=4)
    with pytest.raises(ValidationError):
        RobotParams(qpcr_targets=["a", "b", "c", "d"])


def test_impossible_parameters_fail_loudly_not_silently():
    p = proto()
    with pytest.raises(PlanError, match="negative volume"):
        build_assembly(p, step(p, StepKind.PLASMID_ASSEMBLY), RobotParams(assembly_total_ul=5.0))
    with pytest.raises(PlanError, match="below the p20 minimum"):
        build_induction(p, step(p, StepKind.EXPRESSION_INDUCTION), RobotParams(culture_ul=10.0))


def test_elisa_develop_tip_budget_is_explicit_and_bounded():
    p = proto()
    pl = build_elisa_develop(p, step(p, StepKind.ELISA), RobotParams())
    racks = len(pl.pipette_by_key("p300m").tipracks)
    assert pl.tips_needed["p300m"] > 50 and racks <= 7
    # asking for more washes than the deck has tip-rack slots for is an error, not a silent truncation
    with pytest.raises(PlanError, match="no free deck slot"):
        build_elisa_develop(p, step(p, StepKind.ELISA), RobotParams(elisa_wash_cycles=5, elisa_std_columns=2))


def test_seq_wells_is_column_major():
    assert seq_wells(10) == ["A1", "B1", "C1", "D1", "E1", "F1", "G1", "H1", "A2", "B2"]


def test_pause_text_names_what_the_robot_cannot_do():
    p = proto()
    text = " ".join(a.text for pl in plan_sessions(p) for a in pl.actions if a.op == "pause").lower()
    for word in ("incubat", "centrifug", "plating", "reader", "sonicat", "thermocycl"):
        assert word in text
