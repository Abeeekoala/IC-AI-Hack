"""Offline probe (no network, no model call): list the S8 steps and S9 sessions the evaluation will use."""
from vaxforge.s8_protocol import Candidate, ProtocolConfig, StepKind, build_protocol, validate_protocol
from vaxforge.s9_sessions import plan_sessions, HUMAN_ONLY
from vaxforge.s9_llm import CodegenRequest, build_prompt, SYSTEM_PROMPT
from vaxforge.s9_robot import _step_info

proto = build_protocol(Candidate(candidate_id="c1", protein_name="fHbp", rank=1),
                       ProtocolConfig(readouts=[StepKind.ELISA, StepKind.RT_QPCR, StepKind.FACS]))
print("validate:", validate_protocol(proto))
print("steps:", [(s.step_id, s.kind.value, s.automation.value) for s in proto.steps])
plans = plan_sessions(proto)
print("n sessions:", len(plans))
info = _step_info(proto)
header = dict(Candidate="c1 (fHbp)", Parameters="x", Biosecurity="y")
for p in plans:
    req = CodegenRequest(session_id=p.session_id, plan=p, step_info={sid: info[sid] for sid in p.covers}, header=header)
    print(p.session_id, "| covers", p.covers, "| actions", len(p.actions), "| liquids", len(p.liquids),
          "| tips", sum(p.tips_needed.values()), "| prompt chars", len(build_prompt(req)))
print("system prompt chars:", len(SYSTEM_PROMPT))
robot_steps = [s for s in proto.steps if s.kind not in HUMAN_ONLY]
print("robot-assisted steps:", len(robot_steps), [s.step_id for s in robot_steps])
print("automation values:", sorted(set(s.model_dump(mode="json")["automation"] for s in robot_steps)))
