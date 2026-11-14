"""S8 narratives: a plain-language paragraph per step, from that step's structured fields only (`streamc.s8_backend: template | llm`).

`template` is deterministic. `llm` asks a model (a FAKE client here, injected; nothing leaves the machine) and accepts its text only if it adds no
number, unit or control that the step lacks, is not empty, and is not too long. A refusal, an error or a rejected reply falls back to the template
text with a warning and never crashes. The structured fields (steps, controls, gates, numbers) are the source of truth and are never changed.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from vaxforge import s8_narrative, s8_protocol
from vaxforge.artifacts import RunStore
from vaxforge.config import Config, ConfigError
from vaxforge.s8_narrative import MAX_CHARS, MAX_TOKENS, SYSTEM_PROMPT, check_narrative, step_facts, template_narrative
from vaxforge.s8_protocol import Candidate, Protocol, ProtocolConfig, build_protocol, render_markdown, with_quantities

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = ROOT / "fixtures" / "nmb_fixture"
CAND = Candidate(candidate_id="c1", protein_name="fHbp", rank=1, length_aa=274)
MODEL = "fake-model-1"
STRUCTURED = ("step_id", "kind", "title", "automation", "automation_note", "reagents", "controls", "depends_on", "gate", "est_duration_days", "quantities")


def copy_run(dest: Path) -> RunStore:
    shutil.copytree(FIXTURE, dest)
    return RunStore(dest, cache_dir=dest.parent / "cache")


def cfg(**streamc) -> Config:
    return Config(dict(streamc=streamc))


class FakeClient:
    """The Anthropic SDK's shape: `.messages.create(**kw)` -> a message with `.content` text blocks and `.stop_reason`. Records every call."""

    def __init__(self, reply):
        self.reply, self.calls = reply, []
        self.messages = SimpleNamespace(create=self._create)

    def _create(self, **kw):
        self.calls.append(kw)
        out = self.reply(kw["messages"][0]["content"])
        if isinstance(out, Exception):
            raise out
        return message(out) if isinstance(out, str) else out


def message(text: str, stop: str = "end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop)


def fact(facts: str, label: str) -> str:
    return next(line.partition(": ")[2] for line in facts.splitlines() if line.startswith(label))


def faithful(facts: str) -> str:
    """A reply that restates only what the facts say."""
    return f"{fact(facts, 'title')}. Plan for {fact(facts, 'coarse duration estimate')}."


def only(step_id: str, bad: str):
    """A reply function: `bad` for step `step_id`, a faithful text for every other step."""
    return lambda facts: bad if facts.startswith(f"step id: {step_id}\n") else faithful(facts)


def stage(tmp_path, reply, *, proteins=("fhbp",), model=MODEL, **extra):
    st = copy_run(tmp_path / "run")
    client = FakeClient(reply)
    art = s8_protocol.run(st, cfg(proteins=list(proteins), top_k=len(proteins), s8_backend="llm", s8_model=model, **extra), client=client)
    return SimpleNamespace(store=st, art=art, client=client, steps=art.payload["candidates"][0]["protocol"]["steps"])


def structured(steps) -> list:
    """The structured fields of steps given as dicts (a stage payload) or Step objects."""
    get = (lambda s, k: s[k]) if isinstance(steps[0], dict) else (lambda s, k: getattr(s, k))
    out = []
    for s in steps:
        row = {k: get(s, k) for k in STRUCTURED}
        row["quantities"] = [q if isinstance(q, dict) else q.model_dump() for q in row["quantities"]]
        row["kind"] = getattr(row["kind"], "value", row["kind"])
        row["automation"] = getattr(row["automation"], "value", row["automation"])
        row["est_duration_days"] = list(row["est_duration_days"]) if row["est_duration_days"] else None
        out.append(row)
    return out


@pytest.fixture(scope="module")
def quantified() -> Protocol:
    return with_quantities(build_protocol(CAND))


def step_of(p: Protocol, step_id: str):
    return next(s for s in p.steps if s.step_id == step_id)


# =========================================================================== the template backend


def test_the_template_is_deterministic_and_built_from_the_fields_only(quantified):
    for s in quantified.steps:
        text = template_narrative(s)
        assert text == template_narrative(s) and text.startswith(f"{s.title}.") and s.automation_note in text, s.step_id
        assert {"full": "The robot can do this step.", "partial": "The robot does part of this step", "manual": "A person does this step"}[s.automation.value] in text
        assert all(c in text for c in s.controls) and all(r in text for r in s.reagents) and all(d in text for d in s.depends_on), s.step_id
        lo, hi = s.est_duration_days
        assert f"{lo:g}-{hi:g} days (not measured)" in text and (" uL" not in text) and len(text) < MAX_CHARS, s.step_id  # volumes live in `quantities`; it is short
        assert ("control" in text.lower()) == ("control" in step_facts(s).lower()), s.step_id  # a step whose facts name no control is not given one
        assert check_narrative(text, s, {c for o in quantified.steps for c in o.controls} - set(s.controls)) == [], s.step_id  # it passes its own check


def test_the_template_quotes_a_short_gate_and_points_to_a_long_one(quantified):
    lal = template_narrative(step_of(quantified, "s09"))
    assert f"Gate before the next step: {step_of(quantified, 's09').gate}" in lal and "NOT YET SET" in lal
    s01 = template_narrative(step_of(quantified, "s01"))
    assert "Gate before the next step:" not in s01 and "its full text is in the gate field of this step" in s01 and "s12_biosafety" not in s01
    assert len(s01) < 400


def test_without_a_setting_the_stage_uses_the_template_for_every_step_and_is_deterministic(tmp_path):
    st = copy_run(tmp_path / "run")
    c = cfg(proteins=["fhbp", "nada"], top_k=2)
    art = s8_protocol.run(st, c)
    assert art.params["s8_backend"] == "template" and art.params["s8_model"] is None
    n = sum(len(e["protocol"]["steps"]) for e in art.payload["candidates"])
    assert art.payload["narratives"] == {"backend": "template", "model": None, "n_steps": n, "n_llm": 0, "n_template": n}
    for entry in art.payload["candidates"]:
        for s in entry["protocol"]["steps"]:
            assert s["narrative_source"] == "template" and s["narrative"] and s["narrative"].startswith(s["title"])
    assert s8_protocol.run(st, c).digest == art.digest and art.warnings == [w for w in art.warnings if "narrative" not in w.lower()]


# =========================================================================== the llm backend: accepted


def test_an_accepted_reply_becomes_the_narrative_and_is_labelled_with_the_model(tmp_path):
    r = stage(tmp_path, faithful)
    assert len(r.steps) == 13 and len(r.client.calls) == 13
    for s in r.steps:
        assert s["narrative"] == f"{s['title']}. Plan for {s['est_duration_days'][0]:g}-{s['est_duration_days'][1]:g} days." and s["narrative_source"] == f"llm:{MODEL}"
    assert r.art.payload["narratives"] == {"backend": "llm", "model": MODEL, "n_steps": 13, "n_llm": 13, "n_template": 0}
    assert r.art.params["s8_backend"] == "llm" and r.art.params["s8_model"] == MODEL
    assert not any("narrative" in w.lower() for w in r.art.warnings) and r.art.status == "ok" and r.store.verify() == []


def test_the_model_is_shown_only_that_steps_facts_and_the_rules(tmp_path):
    r = stage(tmp_path, faithful)
    proto = Protocol.model_validate(r.art.payload["candidates"][0]["protocol"])
    assert [c["messages"][0]["content"] for c in r.client.calls] == [step_facts(s) for s in proto.steps]  # exactly the facts, one step per call
    first = r.client.calls[0]
    assert first["model"] == MODEL and first["system"] == SYSTEM_PROMPT and first["max_tokens"] == MAX_TOKENS and first["messages"][0]["role"] == "user"
    for rule in ("ONLY the facts", f"at most {MAX_CHARS} characters", "Do not mention any number, unit, control", "no markdown"):
        assert rule in SYSTEM_PROMPT
    for call in r.client.calls:  # nothing about any other step reaches the model: the facts hold one step id
        assert call["messages"][0]["content"].count("step id: ") == 1


def test_the_same_facts_are_asked_once_across_candidates(tmp_path):
    r = stage(tmp_path, faithful, proteins=("fhbp", "nada"))
    steps = [s for e in r.art.payload["candidates"] for s in Protocol.model_validate(e["protocol"]).steps]
    assert len(steps) == 26 and len(r.client.calls) == len({step_facts(s) for s in steps}) == 14  # only the gene-synthesis title differs by protein
    assert all(s.narrative_source == f"llm:{MODEL}" for s in steps) and r.art.payload["narratives"]["n_llm"] == 26


def test_structured_fields_are_identical_with_either_backend_and_to_the_template_build(tmp_path):
    llm = stage(tmp_path / "a", faithful)
    plain = s8_protocol.run(copy_run(tmp_path / "b" / "run"), cfg(proteins=["fhbp"], top_k=1))
    a, b = llm.art.payload["candidates"][0]["protocol"], plain.payload["candidates"][0]["protocol"]
    assert structured(a["steps"]) == structured(b["steps"])
    assert [s["narrative"] for s in a["steps"]] != [s["narrative"] for s in b["steps"]]  # only the narratives differ
    for key in ("falsifier", "does_not_measure", "config", "cost", "cost_gbp_range", "estimate_basis", "candidate_id", "protein_name"):
        assert a[key] == b[key], key
    first = s8_protocol.load_inputs_from_store(llm.store, cfg(proteins=["fhbp"])).candidates[0]
    base = with_quantities(build_protocol(first))
    assert structured(a["steps"]) == structured(base.steps)  # and to the protocol built by rule, quantities included


# =========================================================================== the llm backend: rejected, refused, failed -> the template text + a warning


def template_of(r, step_id: str) -> str:
    proto = Protocol.model_validate(r.art.payload["candidates"][0]["protocol"])
    return template_narrative(step_of(proto, step_id))


@pytest.mark.parametrize("step_id, bad, why", [
    ("s05", "Small-scale expression and induction takes 14 days.", "a number that is not in the step: 14"),
    ("s05", "Add 250 uL of inducer to each well.", "a number that is not in the step: 250"),
    ("s05", "Small-scale expression and induction. Allow a few minutes.", "a unit that is not in the step: min"),
    ("s05", "Small-scale expression and induction. Allow some hours.", "a unit that is not in the step: h"),
    ("s05", "Small-scale expression and induction takes twenty days.", "spells out a number that is not in the step: twenty"),
    ("s05", "Small-scale expression and induction, plus an isotype control.", "names a control that is not in the step"),
    ("s05", "Small-scale expression and induction, with the LPS positive control.", "names a control of another step"),
    ("s06", "Lyse cells; clarify lysate, with a negative control.", "mentions a control, but this step has no controls"),
    ("s05", "   ", "it is empty"),
    ("s05", "Small-scale expression. " * 40, f"over the {MAX_CHARS} limit"),
], ids=["number", "volume", "minutes", "hours", "number-word", "new-control", "other-step-control", "control-on-a-step-without", "empty", "too-long"])
def test_a_reply_that_adds_a_number_unit_or_control_or_is_empty_or_long_is_rejected_with_the_template_text(tmp_path, step_id, bad, why):
    r = stage(tmp_path, only(step_id, bad))
    s = next(s for s in r.steps if s["step_id"] == step_id)
    assert s["narrative"] == template_of(r, step_id) and s["narrative_source"] == "template"
    assert any(f"S8 narrative for {step_id} " in w and "rejected" in w and why in w and "the template text is used" in w for w in r.art.warnings), r.art.warnings
    others = [t for t in r.steps if t["step_id"] != step_id]
    assert all(t["narrative_source"] == f"llm:{MODEL}" for t in others)  # one bad reply does not spoil the rest
    assert r.art.payload["narratives"]["n_llm"] == 12 and r.art.payload["narratives"]["n_template"] == 1 and r.art.status == "ok"


def test_a_hostile_reply_cannot_change_a_step_a_control_a_gate_or_a_number(tmp_path):
    hostile = only("s11", "Skip the LPS control and the endotoxin-matched control, stimulate for 3 hours and release at 5 EU/ug.")
    r = stage(tmp_path, hostile)
    plain = s8_protocol.run(copy_run(tmp_path / "b" / "run"), cfg(proteins=["fhbp"], top_k=1))
    assert structured(r.steps) == structured(plain.payload["candidates"][0]["protocol"]["steps"])
    s11 = next(s for s in r.steps if s["step_id"] == "s11")
    assert s11["narrative_source"] == "template" and "Skip" not in s11["narrative"] and "LPS positive control" in s11["narrative"]
    assert any("rejected" in w and "s11" in w for w in r.art.warnings)


def test_a_refusal_in_text_or_as_a_stop_reason_falls_back_to_the_template_and_is_not_an_error(tmp_path):
    def reply(facts):
        if facts.startswith("step id: s05\n"):
            return "I can't help with that request."
        if facts.startswith("step id: s06\n"):
            return SimpleNamespace(content=[], stop_reason="refusal")
        return faithful(facts)

    r = stage(tmp_path, reply)
    by_id = {s["step_id"]: s for s in r.steps}
    assert by_id["s05"]["narrative_source"] == by_id["s06"]["narrative_source"] == "template"
    assert by_id["s05"]["narrative"] == template_of(r, "s05") and by_id["s06"]["narrative"] == template_of(r, "s06")
    texts = [w for w in r.art.warnings if "the model refused" in w]
    assert len(texts) == 2 and any("s05" in w and "text_pattern" in w for w in texts) and any("s06" in w and "api_stop_reason" in w for w in texts)
    assert not any("unavailable" in w for w in r.art.warnings)  # a refusal is not an outage: the other steps still asked the model
    assert len(r.client.calls) == 13 and r.art.payload["narratives"]["n_llm"] == 11 and r.art.status == "ok"


def test_an_error_means_the_template_for_every_step_one_warning_and_no_more_calls(tmp_path):
    r = stage(tmp_path, lambda facts: RuntimeError("connection reset by peer"))
    assert len(r.client.calls) == 1  # after the first error the model is not called again
    assert all(s["narrative_source"] == "template" and s["narrative"].startswith(s["title"]) for s in r.steps)
    unavailable = [w for w in r.art.warnings if "unavailable" in w]
    assert len(unavailable) == 1 and "RuntimeError: connection reset by peer" in unavailable[0] and "every later step" in unavailable[0]
    assert r.art.status == "ok" and r.art.payload["narratives"] == {"backend": "llm", "model": MODEL, "n_steps": 13, "n_llm": 0, "n_template": 13}


def test_an_error_that_reads_like_a_refusal_is_a_refusal_not_an_outage(tmp_path):
    r = stage(tmp_path, only("s05", RuntimeError("AgentSafetyRefusalError: blocked")))
    s05 = next(s for s in r.steps if s["step_id"] == "s05")
    assert s05["narrative_source"] == "template" and any("the model refused (harness_error" in w and "s05" in w for w in r.art.warnings)
    assert len(r.client.calls) == 13 and not any("unavailable" in w for w in r.art.warnings)


def test_a_reply_cut_off_by_the_token_limit_is_rejected(tmp_path):
    r = stage(tmp_path, only("s05", message("Small-scale expression and induction is the step where", stop="max_tokens")))
    s05 = next(s for s in r.steps if s["step_id"] == "s05")
    assert s05["narrative_source"] == "template" and any("s05" in w and "cut off by the token limit" in w for w in r.art.warnings)


def test_a_client_that_cannot_be_created_is_one_warning_and_the_template(tmp_path, monkeypatch):
    def no_client(self):
        raise RuntimeError("no key")

    monkeypatch.setattr(s8_narrative.NarrativeLLM, "_client", no_client)
    st = copy_run(tmp_path / "run")
    art = s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, s8_backend="llm", s8_model=MODEL))
    assert art.status == "ok" and all(s["narrative_source"] == "template" for s in art.payload["candidates"][0]["protocol"]["steps"])
    assert [w for w in art.warnings if "unavailable" in w] == ["S8 narrative model unavailable (could not create the model client: RuntimeError); the template text is used for this and every later step"]


@pytest.mark.skipif(bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN")),
                    reason="a model credential is set here, so this test could make a real network call; it is only meant for the no-credential case")
def test_with_no_client_and_no_credential_the_stage_still_writes_template_narratives(tmp_path):
    art = s8_protocol.run(copy_run(tmp_path / "run"), cfg(proteins=["fhbp"], top_k=1, s8_backend="llm", s8_model=MODEL))  # anthropic absent or no key
    assert art.status == "ok" and all(s["narrative_source"] == "template" for s in art.payload["candidates"][0]["protocol"]["steps"])
    assert any("unavailable" in w for w in art.warnings)


# =========================================================================== the check itself


def test_numbers_must_come_from_the_step_but_wording_and_unit_spellings_may_differ(quantified):
    s05 = step_of(quantified, "s05")
    inducer = next(q for q in s05.quantities if q.name == "inducer stock")
    base = f"{s05.title}. Use {inducer.microlitres:g} uL of the inducer stock"
    assert check_narrative(base + ", 1-2 days.", s05) == []
    assert check_narrative(base.replace("uL", "µL") + ", 1 to 2 days.", s05) == []  # micro sign
    assert check_narrative(base.replace("uL", "microlitres") + ", about 2 days.", s05) == []  # spelled-out unit, same unit
    assert check_narrative(f"{s05.title} (s05) follows s04.", s05) == []  # step ids are in the step
    assert check_narrative(base.replace("uL", "mL") + ".", s05) == ["it contains a unit that is not in the step: ml"]
    assert check_narrative(base + ", 3 days.", s05) == ["it contains a number that is not in the step: 3"]
    assert check_narrative(base + ", 1.5 days.", s05) == ["it contains a number that is not in the step: 1.5"]
    assert check_narrative(base + " in step s07.", s05) == ["it contains a number that is not in the step: 7"]


def test_a_control_must_be_one_of_the_steps_own(quantified):
    s11 = step_of(quantified, "s11")
    others = {c for s in quantified.steps for c in s.controls} - set(s11.controls)
    assert check_narrative("Stimulate the cells with the LPS positive control and the vehicle/buffer only control.", s11, others) == []
    assert check_narrative("Stimulate the cells, with an isotype control.", s11, others) == ["it names a control that is not in the step ('isotype control')"]
    assert check_narrative("Stimulate the cells with a negative control.", s11, others) == ["it names a control that is not in the step ('negative control')"]
    assert check_narrative("Stimulate the cells with LPS positive control.", s11, others) == []  # the word before 'control' is the step's own, wherever it stands
    assert check_narrative("Stimulate the cells with a control.", s11, others) == [] and check_narrative("Run each control in turn.", s11, others) == []
    assert any("another step" in p and "buffer blank" in p for p in check_narrative("Stimulate the cells next to the buffer blank.", s11, others))
    assert check_narrative("Stimulate the cells; the controls are listed in the table.", s11, others) == []  # no new qualifier, no new control
    assert check_narrative("Stimulate the temperature-controlled cells.", step_of(quantified, "s06"), others) == []  # 'controlled' is not 'control'


def test_a_step_without_controls_cannot_be_given_one(quantified):
    s06 = step_of(quantified, "s06")
    assert s06.controls == [] and check_narrative("Lyse the cells without any positive control.", s06) == ["it mentions a control, but this step has no controls"]
    assert check_narrative("Lyse the cells and clarify the lysate.", s06) == []


def test_empty_and_overlong_texts_are_rejected_and_the_limit_is_not_a_cut(quantified):
    s05 = step_of(quantified, "s05")
    assert check_narrative("", s05) == check_narrative(" \n ", s05) == ["it is empty"]
    ok = "Small-scale expression and induction. " * 16
    assert len(ok) > MAX_CHARS and check_narrative(ok, s05) == [f"it is {len(' '.join(ok.split()))} characters, over the {MAX_CHARS} limit"]
    assert check_narrative("Small-scale expression and induction. " * 10, s05) == []  # under the limit


# =========================================================================== settings


def test_the_backend_setting_is_checked_and_the_model_default_is_s9s(monkeypatch):
    assert s8_narrative.settings_from(None) == ("template", None) and s8_narrative.settings_from(cfg()) == ("template", None)
    assert s8_narrative.settings_from(cfg(s8_backend="template", s8_model="ignored")) == ("template", None)  # a model is only recorded for llm
    assert s8_narrative.settings_from(cfg(s8_backend=" LLM ", s8_model=" m-1 ")) == ("llm", "m-1")
    monkeypatch.setenv("VAXFORGE_CODEGEN_MODEL", "env-model")
    assert s8_narrative.settings_from(cfg(s8_backend="llm")) == ("llm", "env-model")
    monkeypatch.delenv("VAXFORGE_CODEGEN_MODEL")
    from vaxforge.s9_llm import DEFAULT_MODEL

    assert s8_narrative.settings_from(cfg(s8_backend="llm")) == ("llm", DEFAULT_MODEL)
    for bad in (dict(s8_backend="gpt"), dict(s8_backend="llm", s8_model=5), dict(s8_backend="llm", s8_model="  ")):
        with pytest.raises(ConfigError):
            s8_narrative.settings_from(cfg(**bad))


def test_a_bad_backend_stops_the_stage_before_anything_is_written(tmp_path):
    st = copy_run(tmp_path / "run")
    with pytest.raises(ConfigError, match="streamc.s8_backend"):
        s8_protocol.run(st, cfg(proteins=["fhbp"], s8_backend="both"))
    assert not st.has("s8")


def test_the_llm_digest_differs_from_the_template_digest_and_follows_the_text(tmp_path):
    st = copy_run(tmp_path / "run")
    template = s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1))
    a = s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, s8_backend="llm", s8_model=MODEL), client=FakeClient(faithful))
    b = s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, s8_backend="llm", s8_model=MODEL), client=FakeClient(lambda f: faithful(f).replace("Plan for", "Expect")))
    assert len({template.digest, a.digest, b.digest}) == 3
    assert s8_protocol.run(st, cfg(proteins=["fhbp"], top_k=1, s8_backend="llm", s8_model=MODEL), client=FakeClient(faithful)).digest == a.digest  # same text, same bytes


def test_an_llm_run_with_nothing_to_work_on_makes_no_call_and_says_so(tmp_path):
    client = FakeClient(faithful)
    art = s8_protocol.run(copy_run(tmp_path / "run"), cfg(s8_backend="llm", s8_model=MODEL), client=client)
    assert art.payload["status"] == "not_run" and art.payload["narratives"] is None and client.calls == []


# =========================================================================== render_markdown labels the source


def test_markdown_labels_each_narrative_with_its_source(tmp_path):
    r = stage(tmp_path, only("s05", "Small-scale expression and induction takes 14 days."))  # s05 is rejected: template; the rest are the model's
    proto = Protocol.model_validate(r.art.payload["candidates"][0]["protocol"])
    md = render_markdown(proto)
    heading = "## Step narratives"
    assert md.count(heading) == 1 and md.index("| s13 |") < md.index(heading) < md.index("## Controls and gates")
    section = md.split(heading)[1].split("## Reagent quantities")[0]
    assert "source of truth" in section and "llm:<model>" in section
    for s in proto.steps:
        label = "template" if s.step_id == "s05" else f"llm:{MODEL}"
        assert f"- **{s.step_id} {s.title}** (narrative source: {label}): {s.narrative}" in section, s.step_id
    assert md == render_markdown(proto) and len([line for line in md.splitlines() if line.startswith("| s")]) == len(proto.steps)


def test_markdown_labels_template_narratives_and_a_bare_protocol_has_no_narrative_section(tmp_path):
    art = s8_protocol.run(copy_run(tmp_path / "run"), cfg(proteins=["fhbp"], top_k=1))
    md = render_markdown(Protocol.model_validate(art.payload["candidates"][0]["protocol"]))
    assert md.count("(narrative source: template)") == 13 and "llm:" not in md.split("## Step narratives")[1].split("## Reagent quantities")[0].replace("`llm:<model>`", "")
    assert "## Step narratives" not in render_markdown(build_protocol(CAND, ProtocolConfig()))
