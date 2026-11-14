import json

from vaxforge.s8_protocol import (
    AUTOMATABILITY,
    NOT_MEASURED,
    Candidate,
    ProtocolConfig,
    StepKind,
    build_protocol,
    render_markdown,
    to_json,
    validate_protocol,
)

CAND = Candidate(candidate_id="c1", protein_name="fHbp", rank=1, length_aa=274)


def test_default_protocol_is_valid():
    assert validate_protocol(build_protocol(CAND)) == []


def test_falsifier_and_limits_present():
    p = build_protocol(CAND)
    assert p.falsifier.strip()
    assert "adaptive immunity" in p.does_not_measure
    assert p.cost_gbp_range is None  # unsourced: must not be invented


def test_endotoxin_and_lps_control_precede_thp1_stimulation():
    p = build_protocol(CAND)
    order = [s.kind for s in p.steps]
    stim = order.index(StepKind.ANTIGEN_STIMULATION)
    assert order.index(StepKind.ENDOTOXIN_REMOVAL) < stim
    assert order.index(StepKind.LAL_ASSAY) < stim
    controls = " ".join(p.steps[stim].controls)
    assert "LPS positive control" in controls and "endotoxin-matched" in controls


def test_reordering_is_caught():
    p = build_protocol(CAND)
    kinds = [s.kind for s in p.steps]
    lal = p.steps.pop(kinds.index(StepKind.LAL_ASSAY))
    p.steps.append(lal)  # LAL now after stimulation
    errs = validate_protocol(p)
    assert any("lal_assay must precede" in e for e in errs)


def test_missing_lps_control_is_caught():
    p = build_protocol(CAND)
    stim = next(s for s in p.steps if s.kind is StepKind.ANTIGEN_STIMULATION)
    stim.controls = ["vehicle/buffer only"]
    errs = validate_protocol(p)
    assert any("no LPS control" in e for e in errs)


def test_blank_falsifier_is_caught():
    p = build_protocol(CAND)
    p.falsifier = "  "
    assert "missing falsification condition" in validate_protocol(p)


def test_automation_matches_matrix():
    for s in build_protocol(CAND).steps:
        assert s.automation == AUTOMATABILITY[s.kind][0]


def test_unset_endotoxin_limit_is_flagged_not_defaulted():
    p = build_protocol(CAND)
    lal = next(s for s in p.steps if s.kind is StepKind.LAL_ASSAY)
    assert "NOT YET SET" in lal.gate
    p2 = build_protocol(CAND, ProtocolConfig(endotoxin_limit_eu_per_ug=0.5))
    assert "0.5" in next(s for s in p2.steps if s.kind is StepKind.LAL_ASSAY).gate


def test_json_roundtrip():
    data = json.loads(to_json(build_protocol(CAND)))
    assert data["candidate_id"] == "c1" and data["steps"][0]["step_id"] == "s01"


# --------------------------------------------------------------------------- render_markdown (the reviewer's view)

# Who owns each step, taken from the README automatability matrix (not from AUTOMATABILITY, which is what is under test).
README_OWNER = {
    StepKind.GENE_SYNTHESIS: "HUMAN",  # outsourced to a vendor
    StepKind.PLASMID_ASSEMBLY: "ROBOT",
    StepKind.TRANSFORMATION: "ROBOT-PARTIAL",  # plating is manual
    StepKind.LAL_ASSAY: "ROBOT-PARTIAL",  # reading needs a plate reader
    StepKind.THP1_CULTURE: "HUMAN",  # CO2 incubator, sterile handling
    StepKind.ANTIGEN_STIMULATION: "ROBOT",
    StepKind.ELISA: "ROBOT-PARTIAL",
    StepKind.RT_QPCR: "ROBOT-PARTIAL",
}


def test_markdown_has_a_title_a_design_only_notice_and_one_table_row_per_step():
    p = build_protocol(CAND)
    md = render_markdown(p)
    assert md.splitlines()[0] == "# Wet-lab protocol - fHbp (c1)"
    assert "Design only. No assay results are simulated or predicted." in md
    rows = [line for line in md.splitlines() if line.startswith("| s")]
    assert len(rows) == len(p.steps) == 13
    for step, row in zip(p.steps, rows):
        assert row.startswith(f"| {step.step_id} | {step.title} | ") and row.endswith(f" | {step.automation_note} |")


def test_markdown_names_the_owner_of_each_step_as_the_readme_matrix_does():
    p = build_protocol(CAND)
    md = render_markdown(p)
    seen = set()
    for step in p.steps:
        if step.kind in README_OWNER:
            row = next(line for line in md.splitlines() if line.startswith(f"| {step.step_id} |"))
            assert f" | {README_OWNER[step.kind]} | " in row, (step.kind, row)
            seen.add(step.kind)
    assert seen == set(README_OWNER)  # every kind in the table above occurs in the default protocol


def test_markdown_lists_every_control_and_gate_under_its_step():
    p = build_protocol(CAND)
    md = render_markdown(p)
    section = md.split("## Controls and gates")[1].split("## Falsification condition")[0]
    for s in p.steps:
        for c in s.controls:
            assert f"- control: {c}" in section, (s.step_id, c)
        if s.gate:
            assert f"- gate: {s.gate}" in section, s.step_id
        assert (f"**{s.step_id} {s.title}**" in section) == bool(s.controls or s.gate), s.step_id  # nothing to list: not listed
    assert "- control: LPS positive control" in section and "- control: endotoxin-matched LPS control" in section


def test_markdown_states_the_falsifier_and_what_the_cascade_does_not_measure():
    p = build_protocol(CAND)
    md = render_markdown(p)
    assert md.index("## Controls and gates") < md.index("## Falsification condition") < md.index("## What this cascade does NOT measure")
    falsifier = md.split("## Falsification condition")[1].split("## What this cascade does NOT measure")[0]
    assert p.falsifier in falsifier
    tail = md.split("## What this cascade does NOT measure")[1]
    assert all(f"- {x}" in tail for x in NOT_MEASURED)
    assert f"_{p.estimate_basis}_" in tail  # durations are estimates and cost is unsourced, said in the document itself


def test_markdown_flags_an_unset_endotoxin_limit_and_shows_a_set_one():
    assert "NOT YET SET" in render_markdown(build_protocol(CAND))
    md = render_markdown(build_protocol(CAND, ProtocolConfig(endotoxin_limit_eu_per_ug=0.5)))
    assert "<= 0.5 EU/ug" in md and "NOT YET SET" not in md


def test_markdown_follows_the_readouts_in_the_config():
    md = render_markdown(build_protocol(CAND, ProtocolConfig(readouts=[StepKind.FACS])))
    assert "FACS readout" in md and "ELISA readout" not in md and "RT QPCR readout" not in md
    default = render_markdown(build_protocol(CAND))
    assert "ELISA readout" in default and "RT QPCR readout" in default and "FACS readout" not in default


def test_markdown_is_deterministic():
    assert render_markdown(build_protocol(CAND)) == render_markdown(build_protocol(CAND))
