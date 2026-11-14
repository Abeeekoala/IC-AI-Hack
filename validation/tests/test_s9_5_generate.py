"""Tests for the S9.5 generation client. A scripted fake transport stands in for the endpoint, and an autouse fixture makes
any attempt to open a real connection fail the test."""
import ast
import hashlib
import http.client
import io
import json
import ssl
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402
from vaxforge import s9_5_generate as gen  # noqa: E402

KEY = "endpoint-key-NOT-a-secret-0123456789"
ENDPOINT = "https://gen.example.com/v1"
ARMS = {"base": "Qwen/Qwen3.5-9B", "finetune": "finetune"}
DECODING = {"max_tokens": 4096}
TASKS = [
    {"task_id": "colony-pcr", "family": "colony-pcr", "split": "heldout", "prompt": "Write the colony PCR protocol."},
    {"task_id": "golden-gate", "family": "golden-gate", "split": "heldout", "prompt": "Write the Golden Gate protocol."},
    {"task_id": "probe-1", "family": "colony-pcr", "split": "probe", "prompt": "Probe prompt one."},
]
PAIRS = [(t["prompt"], m) for t in TASKS for m in ARMS.values()]  # the order requests must arrive in
CODE = "```python\nprint('ok')\n```"


def now():
    return datetime(2026, 11, 14, 17, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _no_key_no_network(monkeypatch):
    monkeypatch.delenv(gen.KEY_ENV, raising=False)

    def no_connection(*a, **k):
        raise AssertionError("a test tried to open a real connection")
    monkeypatch.setattr(http.client, "HTTPSConnection", no_connection)
    monkeypatch.setattr(http.client, "HTTPConnection", no_connection)


def answer(text=CODE, finish="stop", usage=(11, 22)):
    return (200, {"id": "c1", "choices": [{"index": 0, "message": {"role": "assistant", "content": text},
                                           "finish_reason": finish}],
                  "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1]}})


class Fake:
    """Answers from a script, then from `default`, and records every request. A step is (status, payload[, headers]), an
    exception to raise, or a function of the recorded request returning either."""

    def __init__(self, *script, default=None):
        self.script, self.default, self.calls = list(script), default, []

    def __call__(self, method, url, headers, body, timeout):
        request = {"method": method, "url": url, "headers": dict(headers), "json": json.loads(body), "timeout": timeout}
        self.calls.append(request)
        step = self.script.pop(0) if self.script else self.default
        if callable(step):
            step = step(request)
        if isinstance(step, Exception):
            raise step
        status, payload, *rest = step
        if not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
        return gen.Response(status, rest[0] if rest else {}, io.BytesIO(payload))

    def asked(self):
        return [(c["json"]["messages"][0]["content"], c["json"]["model"]) for c in self.calls]


def go(out, fake, *, tasks=TASKS, arms=ARMS, decoding=DECODING, endpoint=ENDPOINT, **kw):
    kw.setdefault("sleep", lambda seconds: None)
    return gen.generate(tasks, arms, endpoint, decoding, out, transport=fake, now=now, **kw)


def rows(out):
    return [json.loads(line) for line in (out / "generations.jsonl").read_text().splitlines()]


def manifest(out):
    return json.loads((out / "generation_manifest.json").read_text())


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


# ---- the request ----------------------------------------------------------------------------------------------------

def test_each_pair_is_one_chat_completion_carrying_the_decoding_block_and_only_the_user_prompt(tmp_path):
    fake = Fake(default=answer())
    decoding = {"max_tokens": 4096, "top_p": 0.95, "chat_template_kwargs": {"enable_thinking": False}}
    summary = go(tmp_path / "out", fake, tasks=TASKS[:1], decoding=decoding)
    assert summary == {"recorded": 2, "skipped": 0, "rows": 2}
    first = fake.calls[0]
    assert (first["method"], first["url"]) == ("POST", "https://gen.example.com/v1/chat/completions")
    assert first["json"] == {"model": "Qwen/Qwen3.5-9B", "messages": [{"role": "user", "content": TASKS[0]["prompt"]}],
                             "stream": False, "temperature": 0, "max_tokens": 4096, "top_p": 0.95,
                             "chat_template_kwargs": {"enable_thinking": False}}
    assert first["headers"]["Content-Type"] == "application/json" and "Authorization" not in first["headers"]
    assert fake.calls[1]["json"]["model"] == "finetune" and first["timeout"] == 1800.0


def test_tasks_are_walked_in_order_with_every_arm_inside_each_task(tmp_path):
    fake = Fake(default=answer())
    go(tmp_path / "out", fake)
    assert fake.asked() == PAIRS


def test_the_timeout_is_passed_to_the_transport(tmp_path):
    fake = Fake(default=answer())
    go(tmp_path / "out", fake, tasks=TASKS[:1], arms={"base": "m"}, timeout=42.0)
    assert fake.calls[0]["timeout"] == 42.0


def test_a_row_records_the_answer_the_code_the_provenance_and_the_code_file_matches(tmp_path):
    out = tmp_path / "out"
    text = "Here it is:\n```python\nprint('hi')\n```\nDone."
    go(out, Fake(default=answer(text, usage=(11, 22))), tasks=TASKS[:1], arms={"base": "Qwen/Qwen3.5-9B"})
    assert rows(out) == [{
        "task_id": "colony-pcr", "family": "colony-pcr", "split": "heldout", "arm": "base",
        "served_model": "Qwen/Qwen3.5-9B", "prompt_sha256": sha(TASKS[0]["prompt"]), "text": text,
        "code": "print('hi')\n", "finish_reason": "stop", "prompt_tokens": 11, "completion_tokens": 22, "attempts": 1,
        "started_at_utc": "2026-11-14T17:00:00Z"}]
    assert (out / "code" / "base" / "colony-pcr.py").read_text() == "print('hi')\n"


def test_every_pair_gets_a_row_and_a_code_file_in_its_arm_folder(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()))
    assert sorted((r["task_id"], r["arm"]) for r in rows(out)) == sorted((t["task_id"], a) for t in TASKS for a in ARMS)
    assert sorted(p.relative_to(out).as_posix() for p in (out / "code").rglob("*.py")) == sorted(
        f"code/{a}/{t['task_id']}.py" for t in TASKS for a in ARMS)


def test_an_answer_with_no_code_gets_a_row_but_no_file(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer("I can't help with that request.")), tasks=TASKS[:1], arms={"base": "m"})
    assert rows(out)[0]["code"] is None and rows(out)[0]["text"] == "I can't help with that request."
    assert list((out / "code").rglob("*")) == []


def test_a_truncated_answer_is_recorded_as_length_with_its_partial_code_and_is_not_asked_again(tmp_path):
    out = tmp_path / "out"
    fake = Fake(default=answer("```python\nx = 1\ny = ", finish="length"))
    go(out, fake, tasks=TASKS[:1], arms={"base": "m"})
    row = rows(out)[0]
    assert row["finish_reason"] == "length" and row["code"] == "x = 1\ny = \n" and "refus" not in json.dumps(row)
    assert (out / "code" / "base" / "colony-pcr.py").read_text() == "x = 1\ny = \n" and len(fake.calls) == 1


def test_an_openai_style_refusal_field_becomes_the_text_when_content_is_null(tmp_path):
    out = tmp_path / "out"
    reply = (200, {"choices": [{"message": {"role": "assistant", "content": None, "refusal": "I can't assist."},
                                "finish_reason": "stop"}]})
    go(out, Fake(default=reply), tasks=TASKS[:1], arms={"base": "m"})
    row = rows(out)[0]
    assert row["text"] == "I can't assist." and row["code"] is None
    assert row["prompt_tokens"] is None and row["completion_tokens"] is None  # no usage block in the reply


@pytest.mark.parametrize("message", [{"content": ["part"]}, {"content": 5}, {"content": {"x": 1}}])
def test_content_that_is_not_text_is_an_error_and_nothing_is_recorded(tmp_path, message):
    with pytest.raises(gen.GenerationError):
        go(tmp_path / "out", Fake(default=(200, {"choices": [{"message": message, "finish_reason": "stop"}]})),
           tasks=TASKS[:1], arms={"base": "m"})
    assert not (tmp_path / "out" / "generations.jsonl").exists()


@pytest.mark.parametrize("message", [{}, {"content": None}, {"content": None, "reasoning_content": "thought all of it"}])
def test_an_answer_with_no_content_is_recorded_as_empty_text_for_example_reasoning_cut_off_by_max_tokens(tmp_path, message):
    out = tmp_path / "out"
    go(out, Fake(default=(200, {"choices": [{"message": message, "finish_reason": "length"}]})), tasks=TASKS[:1],
       arms={"base": "m"})
    row = rows(out)[0]
    assert row["text"] == "" and row["code"] is None and row["finish_reason"] == "length"


# ---- pass@1: never re-roll ------------------------------------------------------------------------------------------

def test_a_second_call_on_a_finished_directory_sends_nothing(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()))
    before = (out / "generations.jsonl").read_bytes()
    again = Fake()
    assert go(out, again) == {"recorded": 0, "skipped": 6, "rows": 6}
    assert again.calls == [] and (out / "generations.jsonl").read_bytes() == before


def test_a_stopped_run_resumes_at_the_first_unrecorded_pair_and_asks_for_nothing_twice(tmp_path):
    out = tmp_path / "out"
    first = Fake(answer(), answer(), answer(), (500, b"server fell over"))
    with pytest.raises(gen.GenerationError) as caught:
        go(out, first)
    assert caught.value.status == 500 and len(rows(out)) == 3 and len(first.calls) == 4
    second = Fake(default=answer())
    assert go(out, second) == {"recorded": 3, "skipped": 3, "rows": 6}
    assert second.asked() == PAIRS[3:] and len(rows(out)) == 6


@pytest.mark.parametrize("status", [400, 401, 404, 408, 429, 500, 502, 503, 504])
def test_an_error_status_is_a_reply_so_it_stops_the_run_and_is_never_retried(tmp_path, status):
    fake = Fake((status, b"nope"), default=answer())
    with pytest.raises(gen.GenerationError) as caught:
        go(tmp_path / "out", fake)
    assert caught.value.status == status and len(fake.calls) == 1
    assert not (tmp_path / "out" / "generations.jsonl").exists()


@pytest.mark.parametrize("reply", [(200, b"not json"), (200, b""), (200, {"choices": []}), (200, {}), (200, [1]),
                                   (200, {"choices": ["x"]}), (200, {"choices": [{"message": "x"}]}),
                                   (200, {"choices": [{"message": None}]})])
def test_a_reply_that_is_not_a_completion_is_not_retried(tmp_path, reply):
    fake = Fake(reply, default=answer())
    with pytest.raises(gen.GenerationError):
        go(tmp_path / "out", fake)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("failure", [ConnectionResetError("reset"), ConnectionRefusedError("refused"),
                                     TimeoutError("timed out"), http.client.RemoteDisconnected("closed"),
                                     http.client.IncompleteRead(b"part")])
def test_a_request_that_got_no_response_is_retried_with_backoff_and_the_row_counts_the_attempts(tmp_path, failure):
    out, naps = tmp_path / "out", []
    fake = Fake(failure, TimeoutError("again"), answer())
    go(out, fake, tasks=TASKS[:1], arms={"base": "m"}, sleep=naps.append)
    assert len(fake.calls) == 3 and naps == [2.0, 4.0] and rows(out)[0]["attempts"] == 3
    assert len(rows(out)) == 1


def test_an_unexpected_exception_from_the_transport_is_not_a_missing_response_and_is_not_retried(tmp_path):
    fake = Fake(RuntimeError("bug in the transport"), default=answer())
    with pytest.raises(RuntimeError):
        go(tmp_path / "out", fake)
    assert len(fake.calls) == 1


def test_the_row_is_recorded_before_the_code_file_so_a_crash_between_them_never_causes_a_re_roll(tmp_path, monkeypatch):
    out = tmp_path / "out"
    real = gen._write_code

    def crash(*args, **kwargs):
        raise KeyboardInterrupt("killed between the row and the code file")
    monkeypatch.setattr(gen, "_write_code", crash)
    with pytest.raises(KeyboardInterrupt):
        go(out, Fake(default=answer()), tasks=TASKS[:1], arms={"base": "m"})
    assert len(rows(out)) == 1 and not (out / "code" / "base" / "colony-pcr.py").exists()
    monkeypatch.setattr(gen, "_write_code", real)
    again = Fake()
    go(out, again, tasks=TASKS[:1], arms={"base": "m"})
    assert again.calls == [] and (out / "code" / "base" / "colony-pcr.py").read_text() == "print('ok')\n"


def test_a_pair_that_never_gets_a_response_stops_the_run_after_max_attempts_and_records_nothing(tmp_path):
    fake = Fake(default=ConnectionRefusedError("refused"))
    with pytest.raises(gen.GenerationError, match="no response after 3 attempt"):
        go(tmp_path / "out", fake, max_attempts=3)
    assert len(fake.calls) == 3 and not (tmp_path / "out" / "generations.jsonl").exists()


def test_max_attempts_timeout_and_backoff_are_checked_before_anything_is_created(tmp_path):
    for override in ({"max_attempts": 0}, {"max_attempts": -1}, {"max_attempts": 1.5}, {"max_attempts": True},
                     {"max_attempts": "3"}, {"timeout": 0}, {"timeout": -1}, {"timeout": "5"}, {"timeout": None},
                     {"timeout": float("nan")}, {"timeout": True}, {"backoff": -1}, {"backoff": "1"}, {"backoff": None}):
        fake = Fake()
        with pytest.raises(ValueError):
            go(tmp_path / "out", fake, **override)
        assert fake.calls == [] and not (tmp_path / "out").exists()
    go(tmp_path / "ok", Fake(default=answer()), tasks=TASKS[:1], arms={"base": "m"}, timeout=30, backoff=0, max_attempts=1)


def test_a_redirect_is_an_error_and_is_not_followed(tmp_path):
    fake = Fake((302, b"", {"location": "https://evil.example/steal"}), default=answer())
    with pytest.raises(gen.GenerationError, match="redirects are not followed") as caught:
        go(tmp_path / "out", fake)
    assert caught.value.status == 302 and len(fake.calls) == 1
    assert all("evil.example" not in c["url"] for c in fake.calls)


def test_a_certificate_failure_stops_with_the_fix_and_is_not_retried(tmp_path):
    fake = Fake(ssl.SSLCertVerificationError(1, "unable to get local issuer certificate"), default=answer())
    with pytest.raises(gen.GenerationError, match="certifi"):
        go(tmp_path / "out", fake)
    assert len(fake.calls) == 1


# ---- resuming safely ------------------------------------------------------------------------------------------------

def test_a_recorded_pair_whose_prompt_changed_is_an_error_not_a_silent_skip(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()), tasks=TASKS[:1])
    changed = [{**TASKS[0], "prompt": "A different prompt."}]
    fake = Fake(default=answer())
    with pytest.raises(gen.GenerationError, match="another split or prompt"):
        go(out, fake, tasks=changed)
    assert fake.calls == []


def test_the_same_task_id_in_another_split_is_an_error(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()), tasks=TASKS[:1])
    moved = [{**TASKS[0], "split": "probe"}]
    with pytest.raises(gen.GenerationError, match="another split or prompt"):
        go(out, Fake(default=answer()), tasks=moved)


def test_the_code_file_is_rebuilt_from_the_row_when_it_is_missing_but_never_overwritten(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()), tasks=TASKS[:1], arms={"base": "m"})
    target = out / "code" / "base" / "colony-pcr.py"
    target.unlink()
    again = Fake()
    go(out, again, tasks=TASKS[:1], arms={"base": "m"})
    assert target.read_text() == "print('ok')\n" and again.calls == []
    target.write_text("# edited by hand\n")
    go(out, again, tasks=TASKS[:1], arms={"base": "m"})
    assert target.read_text() == "# edited by hand\n" and again.calls == []


def test_two_rows_for_one_pair_corrupt_json_and_a_missing_manifest_are_errors(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()), tasks=TASKS[:1])
    path = out / "generations.jsonl"
    good = path.read_text()
    path.write_text(good + good.splitlines()[0] + "\n")
    with pytest.raises(gen.GenerationError, match="two rows"):
        go(out, Fake(), tasks=TASKS[:1])
    path.write_text(good + "{ not json\n")
    with pytest.raises(gen.GenerationError, match="line 3"):
        go(out, Fake(), tasks=TASKS[:1])
    path.write_text(good + '{"task_id": "x"}\n')
    with pytest.raises(gen.GenerationError, match="not a generation row"):
        go(out, Fake(), tasks=TASKS[:1])
    path.write_text(good)
    (out / "generation_manifest.json").unlink()
    with pytest.raises(gen.GenerationError, match="without the recorded decoding"):
        go(out, Fake(), tasks=TASKS[:1])


@pytest.mark.parametrize("content", ["{ not json", "[]", '{"schema_version": 2}', '{"schema_version": 1, "decoding": {}}'])
def test_a_manifest_this_module_did_not_write_is_refused(tmp_path, content):
    out = tmp_path / "out"
    out.mkdir()
    (out / "generation_manifest.json").write_text(content)
    fake = Fake()
    with pytest.raises(gen.GenerationError):
        go(out, fake)
    assert fake.calls == []


def test_arms_can_be_run_in_separate_calls_against_separate_servers_into_one_directory(tmp_path):
    out = tmp_path / "out"
    base, tuned = Fake(default=answer("```python\n# base\n```")), Fake(default=answer("```python\n# tuned\n```"))
    go(out, base, arms={"base": "Qwen/Qwen3.5-9B"}, endpoint="http://localhost:8001/v1",
       extra={"base_model_revision": "abc123"})
    go(out, tuned, arms={"finetune": "finetune"}, endpoint="http://127.0.0.1:8002/v1",
       extra={"adapter_sha256": "f" * 64})
    assert [c["url"] for c in base.calls] == ["http://localhost:8001/v1/chat/completions"] * 3
    assert [c["url"] for c in tuned.calls] == ["http://127.0.0.1:8002/v1/chat/completions"] * 3
    record = manifest(out)
    assert record["arms"] == {"base": {"served_model": "Qwen/Qwen3.5-9B", "endpoint_hosts": ["localhost:8001"]},
                              "finetune": {"served_model": "finetune", "endpoint_hosts": ["127.0.0.1:8002"]}}
    assert record["extra"] == {"base_model_revision": "abc123", "adapter_sha256": "f" * 64}
    nothing = Fake()
    assert go(out, nothing) == {"recorded": 0, "skipped": 6, "rows": 6} and nothing.calls == []


# ---- decoding -------------------------------------------------------------------------------------------------------

def test_only_temperature_has_a_default_and_every_field_is_written_to_the_manifest(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()), tasks=TASKS[:1], arms={"base": "m"})
    assert manifest(out)["decoding"] == {"temperature": 0, "max_tokens": 4096}
    full = {"temperature": 0.6, "max_tokens": 8192, "top_p": 0.95, "top_k": 20, "min_p": 0.0, "presence_penalty": 0.0,
            "frequency_penalty": 0.0, "repetition_penalty": 1.0, "seed": 7, "stop": ["</s>"],
            "chat_template_kwargs": {"enable_thinking": False, "reasoning_effort": "low"}}
    other = tmp_path / "other"
    fake = Fake(default=answer())
    go(other, fake, tasks=TASKS[:1], arms={"base": "m"}, decoding=full)
    assert manifest(other)["decoding"] == full
    assert {k: v for k, v in fake.calls[0]["json"].items() if k not in ("model", "messages", "stream")} == full


@pytest.mark.parametrize("decoding", [
    {}, {"temperature": 0.2}, {"max_tokens": None}, {"max_tokens": 0}, {"max_tokens": 1.5}, {"max_tokens": True},
    {"max_tokens": "10"}, {"max_tokens": 10, "max_token": 10}, {"max_tokens": 10, "n": 2},
    {"max_tokens": 10, "logit_bias": {}}, {"max_tokens": 10, "temperature": -1}, {"max_tokens": 10, "temperature": True},
    {"max_tokens": 10, "temperature": "0.1"}, {"max_tokens": 10, "temperature": float("nan")},
    {"max_tokens": 10, "top_p": 0}, {"max_tokens": 10, "top_p": 1.1}, {"max_tokens": 10, "top_k": 1.5},
    {"max_tokens": 10, "top_k": -2}, {"max_tokens": 10, "min_p": 2}, {"max_tokens": 10, "presence_penalty": 3},
    {"max_tokens": 10, "repetition_penalty": 0}, {"max_tokens": 10, "seed": 1.5}, {"max_tokens": 10, "seed": True},
    {"max_tokens": 10, "stop": []}, {"max_tokens": 10, "stop": ["a"] * 5}, {"max_tokens": 10, "stop": [""]},
    {"max_tokens": 10, "stop": "x"}, {"max_tokens": 10, "chat_template_kwargs": "x"},
    {"max_tokens": 10, "chat_template_kwargs": {"a": [1]}}, {"max_tokens": 10, "chat_template_kwargs": {1: 2}},
    {"max_tokens": 10, "chat_template_kwargs": {"a": float("nan")}}, None, [("max_tokens", 1)],
])
def test_a_bad_decoding_block_is_refused_before_anything_is_created_or_sent(tmp_path, decoding):
    fake = Fake()
    with pytest.raises(ValueError):
        go(tmp_path / "out", fake, decoding=decoding)
    assert fake.calls == [] and not (tmp_path / "out").exists()


def test_resuming_with_a_different_decoding_block_is_an_error_and_sends_nothing(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()), tasks=TASKS[:1])
    for changed in ({"max_tokens": 2048}, {"max_tokens": 4096, "temperature": 0.7}, {"max_tokens": 4096, "seed": 1}):
        fake = Fake(default=answer())
        with pytest.raises(gen.GenerationError, match="decoding block differs"):
            go(out, fake, decoding=changed)
        assert fake.calls == []
    assert len(rows(out)) == 2 and manifest(out)["decoding"] == {"temperature": 0, "max_tokens": 4096}


def test_an_equal_block_written_differently_is_the_same_block(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()), tasks=TASKS[:1])
    again = Fake()
    go(out, again, tasks=TASKS[:1], decoding={"temperature": 0.0, "max_tokens": 4096})
    assert again.calls == []


# ---- the manifest ---------------------------------------------------------------------------------------------------

def test_the_manifest_is_written_before_the_first_request_with_arms_hosts_and_extra(tmp_path):
    out = tmp_path / "out"
    with pytest.raises(gen.GenerationError):
        go(out, Fake((503, b"busy")), extra={"base_model_revision": "abc123", "adapter": {"rank": 16}})
    assert manifest(out) == {
        "schema_version": 1, "created_at_utc": "2026-11-14T17:00:00Z", "updated_at_utc": "2026-11-14T17:00:00Z",
        "decoding": {"temperature": 0, "max_tokens": 4096},
        "arms": {"base": {"served_model": "Qwen/Qwen3.5-9B", "endpoint_hosts": ["gen.example.com"]},
                 "finetune": {"served_model": "finetune", "endpoint_hosts": ["gen.example.com"]}},
        "extra": {"base_model_revision": "abc123", "adapter": {"rank": 16}}}


def test_recorded_provenance_never_changes_but_can_grow_and_a_new_host_is_added(tmp_path):
    out = tmp_path / "out"
    go(out, Fake(default=answer()), tasks=TASKS[:1], extra={"revision": "abc"})
    go(out, Fake(default=answer()), tasks=TASKS[:1], extra={"revision": "abc", "server": "vllm 0.20"},
       endpoint="https://other.example.com/v1")
    record = manifest(out)
    assert record["extra"] == {"revision": "abc", "server": "vllm 0.20"}
    assert record["arms"]["base"]["endpoint_hosts"] == ["gen.example.com", "other.example.com"]
    assert record["created_at_utc"] == "2026-11-14T17:00:00Z"
    with pytest.raises(gen.GenerationError, match="extra"):
        go(out, Fake(), tasks=TASKS[:1], extra={"revision": "def"})
    with pytest.raises(gen.GenerationError, match="served model"):
        go(out, Fake(), tasks=TASKS[:1], arms={"base": "Qwen/Qwen3.5-4B", "finetune": "finetune"})


@pytest.mark.parametrize("extra", ["text", [1], {"x": {1, 2}}, {"x": object()}])
def test_extra_must_be_a_json_dict(tmp_path, extra):
    with pytest.raises(ValueError):
        go(tmp_path / "out", Fake(), extra=extra)


# ---- inputs ---------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("url, base, label", [
    ("https://gen.example.com/v1", "https://gen.example.com/v1", "gen.example.com"),
    ("https://gen.example.com/v1/", "https://gen.example.com/v1", "gen.example.com"),
    ("https://gen.example.com:8443/v1", "https://gen.example.com:8443/v1", "gen.example.com:8443"),
    ("https://gen.example.com/v1/chat/completions", "https://gen.example.com/v1", "gen.example.com"),
    ("http://localhost:8000/v1", "http://localhost:8000/v1", "localhost:8000"),
    ("http://LOCALHOST:8000/v1", "http://localhost:8000/v1", "localhost:8000"),
    ("http://127.0.0.1:8000/v1", "http://127.0.0.1:8000/v1", "127.0.0.1:8000"),
    ("http://[::1]:8000/v1", "http://[::1]:8000/v1", "[::1]:8000"),
    ("https://gen.example.com", "https://gen.example.com", "gen.example.com")])
def test_valid_endpoints_are_normalised(url, base, label):
    assert gen._endpoint(url) == (base, label)


@pytest.mark.parametrize("url", [
    "http://gen.example.com/v1", "http://10.0.0.5:8000/v1", "http://localhost.evil.com/v1",
    "http://127.0.0.1.evil.com/v1", "http://127.0.0.2:8000/v1", "http://0.0.0.0:8000/v1", "ftp://localhost/v1",
    "https://user:pw@gen.example.com/v1", "https://user@gen.example.com/v1", "https://gen.example.com/v1?x=1",
    "https://gen.example.com/v1#frag", "gen.example.com/v1", "", None, 5, "https://", "http://localhost:notaport/v1"])
def test_other_endpoints_are_refused_before_anything_is_created_or_sent(tmp_path, url):
    fake = Fake()
    with pytest.raises(ValueError):
        go(tmp_path / "out", fake, endpoint=url)
    assert fake.calls == [] and not (tmp_path / "out").exists()


def test_a_loopback_ipv6_endpoint_is_requested_with_brackets(tmp_path):
    fake = Fake(default=answer())
    go(tmp_path / "out", fake, tasks=TASKS[:1], arms={"base": "m"}, endpoint="http://[::1]:8000/v1")
    assert fake.calls[0]["url"] == "http://[::1]:8000/v1/chat/completions"


@pytest.mark.parametrize("task", [
    {"task_id": "../x"}, {"task_id": "a/b"}, {"task_id": ""}, {"task_id": ".hidden"}, {"task_id": "a b"},
    {"task_id": None}, {"task_id": 5}, {"split": "held-out"}, {"split": None}, {"prompt": ""}, {"prompt": "  "},
    {"prompt": None}, {"family": 5}, {"family": None}, {"family": ""}])
def test_a_bad_task_is_refused_before_anything_is_created_or_sent(tmp_path, task):
    fake = Fake()
    with pytest.raises(ValueError):
        go(tmp_path / "out", fake, tasks=[{**TASKS[0], **task}])
    assert fake.calls == [] and not (tmp_path / "out").exists()


def test_tasks_that_are_not_dicts_or_repeat_an_id_are_refused(tmp_path):
    for tasks in (["x"], [TASKS[0], TASKS[0]], [TASKS[0], {**TASKS[1], "task_id": TASKS[0]["task_id"]}]):
        with pytest.raises(ValueError):
            go(tmp_path / "out", Fake(), tasks=tasks)


def test_family_is_optional_outside_heldout_and_probe(tmp_path):
    fake = Fake(default=answer())
    candidate = {"task_id": "c1", "split": "candidate", "prompt": "p"}
    go(tmp_path / "out", fake, tasks=[candidate], arms={"base": "m"})
    assert rows(tmp_path / "out")[0]["family"] is None


@pytest.mark.parametrize("arms", [{}, None, [], {"Base": "m"}, {"../x": "m"}, {"": "m"}, {"base": ""}, {"base": "a b"},
                                  {"base": None}, {"base": "m", "x y": "m"}])
def test_bad_arms_are_refused(tmp_path, arms):
    with pytest.raises(ValueError):
        go(tmp_path / "out", Fake(), arms=arms)


def test_load_tasks_reads_jsonl_skips_blank_lines_and_names_a_bad_line(tmp_path):
    path = tmp_path / "tasks.jsonl"
    path.write_text("\n".join(json.dumps(t) for t in TASKS[:2]) + "\n\n")
    assert gen.load_tasks(path) == [{**t, "family": t["family"]} for t in TASKS[:2]]
    path.write_text(json.dumps(TASKS[0]) + "\n{ nope\n")
    with pytest.raises(ValueError, match="line 2"):
        gen.load_tasks(path)
    path.write_text(json.dumps({**TASKS[0], "split": "other"}) + "\n")
    with pytest.raises(ValueError, match="split"):
        gen.load_tasks(path)


# ---- the key --------------------------------------------------------------------------------------------------------

def test_the_key_goes_in_the_header_of_every_request_to_the_endpoint_host_only(tmp_path, monkeypatch):
    monkeypatch.setenv(gen.KEY_ENV, KEY)
    fake = Fake(default=answer())
    go(tmp_path / "out", fake)
    assert {c["headers"]["Authorization"] for c in fake.calls} == {"Bearer " + KEY}
    assert {c["url"].split("/")[2] for c in fake.calls} == {"gen.example.com"}


def test_the_key_is_read_for_each_request_not_once(tmp_path, monkeypatch):
    monkeypatch.setenv(gen.KEY_ENV, KEY)

    def swap(request):
        monkeypatch.setenv(gen.KEY_ENV, "second-key-0000000000000000")
        return answer()
    fake = Fake(swap, default=answer())
    go(tmp_path / "out", fake, tasks=TASKS[:1], arms={"base": "m", "finetune": "m2"})
    assert [c["headers"]["Authorization"] for c in fake.calls] == ["Bearer " + KEY, "Bearer second-key-0000000000000000"]


def test_without_a_key_no_authorization_header_is_sent(tmp_path):
    fake = Fake(default=answer())
    go(tmp_path / "out", fake)
    assert all("Authorization" not in c["headers"] for c in fake.calls)


def test_the_key_never_reaches_a_file_a_return_value_an_error_or_the_module(tmp_path, monkeypatch):
    monkeypatch.setenv(gen.KEY_ENV, KEY)
    out = tmp_path / "out"
    summary = go(out, Fake(default=answer(f"```python\n# no key here\n```")), extra={"revision": "abc"})
    assert KEY not in json.dumps(summary)
    assert all(KEY not in p.read_text() for p in out.rglob("*") if p.is_file())
    assert KEY not in repr(vars(gen))
    with pytest.raises(gen.GenerationError) as caught:
        go(tmp_path / "other", Fake((500, f"server echoed {KEY} back".encode())))
    assert KEY not in str(caught.value) and "[REDACTED]" in str(caught.value)


def test_a_key_pasted_into_extra_is_refused_and_nothing_is_written_or_sent(tmp_path, monkeypatch):
    monkeypatch.setenv(gen.KEY_ENV, KEY)
    fake = Fake()
    with pytest.raises(gen.GenerationError, match="must not appear in the manifest"):
        go(tmp_path / "out", fake, extra={"note": f"key is {KEY}"})
    assert fake.calls == [] and not (tmp_path / "out" / "generation_manifest.json").exists()


# ---- extract_code ---------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text, expected", [
    ("```python\nprint(1)\n```", "print(1)\n"),
    ("Here:\n```python\nx = 1\n```\nDone", "x = 1\n"),
    ("```py\nx=1\n```", "x=1\n"),
    ("```Python3\nx=1\n```", "x=1\n"),
    ("```\nx=1\n```", "x=1\n"),
    ("```python title='a'\nx=1\n```", "x=1\n"),
    ("~~~python\nx=1\n~~~", "x=1\n"),
    ("  ```python\nx=1\n  ```", "x=1\n"),
    ("```python\r\nx=1\r\n```\r\n", "x=1\n"),
    ("```python\nx = 1\ny = 2", "x = 1\ny = 2\n"),
    ("```python\nx = 1\n", "x = 1\n"),
    ("```bash\npip install x\n```\n```python\nx=1\n```", "x=1\n"),
    ("```python\n```\n```python\nx=1\n```", "x=1\n"),
    ("```python\nx=1\n```\n```python\ny=2\n```", "x=1\n"),
    ("````python\nprint('```')\n```\nmore\n````", "print('```')\n```\nmore\n"),
    ("```python\n\n\nx=1\n\n```", "x=1\n"),
    ("draft\n```python\nbad = 1\n```\n</think>\n\n```python\ngood = 1\n```", "good = 1\n"),
    ("reasoning with no opener </think>```python\nx=1\n```", "x=1\n"),
    ("from opentrons import protocol_api\n\ndef run(protocol):\n    pass\n", "from opentrons import protocol_api\n\ndef run(protocol):\n    pass\n"),
    ("import os\n", "import os\n"),
])
def test_extract_code_finds_the_code(text, expected):
    assert gen.extract_code(text) == expected


@pytest.mark.parametrize("text", [
    "", "   ", "I cannot help with that.", "Sorry", "No.", "x = 1", "```bash\nls\n```", "```text\nhello\n```",
    "```python\n```", "```python\n   \n```", "<think>\nhmm ```python\nbad = 1\n```", "<think>never closed",
    "Here is the protocol: from opentrons import protocol_api", "def run(", "~~~bash\nimport os\n", None, 5,
    ["```python\nx=1\n```"]])
def test_extract_code_gives_none_when_there_is_no_code(text):
    assert gen.extract_code(text) is None


# ---- the default transport (connections are faked; nothing leaves the machine) ---------------------------------------

class FakeConnection:
    instances = []

    def __init__(self, host, port, timeout=None, context=None):
        self.args = {"host": host, "port": port, "timeout": timeout, "context": context}
        self.requested, self.closed, self.fail = [], False, None
        FakeConnection.instances.append(self)

    def request(self, method, target, body=None, headers=None):
        if self.fail:
            raise self.fail
        self.requested.append({"method": method, "target": target, "body": body, "headers": headers})

    def getresponse(self):
        outer = self

        class Reply:
            status = 200

            def getheaders(self):
                return [("Content-Type", "application/json"), ("X-Thing", "1")]

            def read(self, n=None):
                return b'{"ok": true}'

            def close(self):
                outer.closed = True
        return Reply()

    def close(self):
        self.closed = True


@pytest.fixture
def connections(monkeypatch):
    FakeConnection.instances = []
    monkeypatch.setattr(http.client, "HTTPSConnection", FakeConnection)
    monkeypatch.setattr(http.client, "HTTPConnection", FakeConnection)
    return FakeConnection.instances


def test_the_default_transport_uses_a_verifying_tls_context_for_https_and_lowercases_header_names(connections):
    resp = gen._http("POST", "https://gen.example.com:8443/v1/chat/completions?x=1", {"A": "b"}, b"{}", 5)
    conn = connections[0]
    assert (conn.args["host"], conn.args["port"], conn.args["timeout"]) == ("gen.example.com", 8443, 5)
    assert conn.args["context"].verify_mode == ssl.CERT_REQUIRED and conn.args["context"].check_hostname is True
    assert conn.requested == [{"method": "POST", "target": "/v1/chat/completions?x=1", "body": b"{}", "headers": {"A": "b"}}]
    assert resp.status == 200 and resp.headers == {"content-type": "application/json", "x-thing": "1"}
    assert resp.read() == b'{"ok": true}'
    resp.close()
    assert conn.closed is True


def test_the_default_transport_speaks_plain_http_only_to_loopback(connections):
    for host, expected in (("localhost", "localhost"), ("127.0.0.1", "127.0.0.1"), ("[::1]", "::1")):
        gen._http("POST", f"http://{host}:8000/v1/chat/completions", {}, b"{}", 5)
        assert connections[-1].args["host"] == expected and connections[-1].args["port"] == 8000
        assert connections[-1].args["context"] is None
    before = len(connections)
    for url in ("http://gen.example.com/v1/x", "http://localhost.evil.com/x", "ftp://localhost/x", "https:///x"):
        with pytest.raises(gen.GenerationError):
            gen._http("POST", url, {}, b"{}", 5)
    assert len(connections) == before


def test_the_default_transport_closes_the_connection_when_the_request_fails(connections, monkeypatch):
    def broken(self, *a, **k):
        raise ConnectionResetError("reset")
    monkeypatch.setattr(FakeConnection, "request", broken)
    with pytest.raises(ConnectionResetError):
        gen._http("POST", "https://gen.example.com/v1/x", {}, b"{}", 5)
    assert connections[0].closed is True


def test_a_generate_call_through_the_default_transport_reaches_the_connection_layer(connections, tmp_path):
    class Answering(FakeConnection):
        def getresponse(self):
            reply = super().getresponse()
            reply.read = lambda n=None: json.dumps(answer()[1]).encode()
            return reply
    http.client.HTTPConnection = Answering  # restored by the fixture's monkeypatch
    gen.generate(TASKS[:1], {"base": "m"}, "http://localhost:8000/v1", DECODING, tmp_path / "out", now=now)
    assert connections[-1].requested[0]["target"] == "/v1/chat/completions"
    assert rows(tmp_path / "out")[0]["code"] == "print('ok')\n"


def test_the_tls_context_keeps_certificate_and_hostname_checks_on():
    ctx = gen._ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


# ---- the module itself ----------------------------------------------------------------------------------------------

def _imports_and_calls():
    tree = ast.parse(Path(gen.__file__).read_text())
    modules = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    return modules, calls


def test_the_module_neither_prints_nor_logs():
    modules, calls = _imports_and_calls()
    assert "print" not in calls and "logging" not in modules and "warnings" not in modules


def test_the_module_uses_only_the_standard_library():
    stdlib = getattr(sys, "stdlib_module_names", None)
    if stdlib is None:
        pytest.skip("sys.stdlib_module_names needs Python 3.10 or newer")
    assert _imports_and_calls()[0] - set(stdlib) <= {"certifi"}  # optional CA bundle, imported inside try/except ImportError


def test_nothing_is_printed_while_a_run_goes(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv(gen.KEY_ENV, KEY)
    go(tmp_path / "out", Fake(default=answer()))
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


# ---- workers: requests in flight ------------------------------------------------------------------------------------

def test_workers_put_that_many_requests_in_flight_at_once(tmp_path):
    gate = threading.Barrier(2, timeout=10)  # passes only when a second request is in flight too

    def both_at_once(request):
        gate.wait()
        return answer()
    fake = Fake(default=both_at_once)
    summary = go(tmp_path / "out", fake, tasks=TASKS[:1], workers=2)  # one task, two arms: two requests
    assert summary == {"recorded": 2, "skipped": 0, "rows": 2}
    assert sorted(r["arm"] for r in rows(tmp_path / "out")) == ["base", "finetune"]


def test_no_more_requests_than_workers_are_ever_in_flight_and_every_pair_is_recorded_once(tmp_path):
    lock, state = threading.Lock(), {"now": 0, "most": 0}

    def slow(request):
        with lock:
            state["now"] += 1
            state["most"] = max(state["most"], state["now"])
        time.sleep(0.02)
        with lock:
            state["now"] -= 1
        return answer()
    summary = go(tmp_path / "out", Fake(default=slow), workers=2)
    assert summary == {"recorded": 6, "skipped": 0, "rows": 6}
    assert 1 <= state["most"] <= 2
    assert sorted((r["task_id"], r["arm"]) for r in rows(tmp_path / "out")) == sorted(
        (t["task_id"], a) for t in TASKS for a in ARMS)


def test_a_failed_request_stops_new_ones_but_the_one_in_flight_is_still_recorded(tmp_path):
    release = threading.Timer(0.3, lambda: None)  # replaced below; the second request waits for this event
    event = threading.Event()
    release = threading.Timer(0.3, event.set)
    release.start()

    def by_pair(request):
        prompt, model = request["json"]["messages"][0]["content"], request["json"]["model"]
        if (prompt, model) == PAIRS[0]:
            return (500, {"error": "boom"})
        assert event.wait(10)  # held until the first request's failure has been seen
        return answer()
    fake = Fake(default=by_pair)
    with pytest.raises(gen.GenerationError, match="HTTP 500"):
        go(tmp_path / "out", fake, workers=2)
    assert len(fake.calls) == 2  # the third pair was never asked for
    assert [(r["task_id"], r["arm"]) for r in rows(tmp_path / "out")] == [("colony-pcr", "finetune")]
    resumed = go(tmp_path / "out", Fake(default=answer()), workers=2)  # the failed pair is asked for again, the rest are not repeated
    assert resumed["recorded"] == 5 and resumed["skipped"] == 1


@pytest.mark.parametrize("workers", [0, -1, 1.5, True, "2", None])
def test_workers_must_be_a_positive_integer(tmp_path, workers):
    with pytest.raises(ValueError, match="workers"):
        go(tmp_path / "out", Fake(default=answer()), workers=workers)
    assert not (tmp_path / "out" / "generations.jsonl").exists()
