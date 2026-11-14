"""Tests for the S9.5 Adaption platform client. A scripted fake transport stands in for the network, and an autouse
fixture makes any attempt to open a real connection fail the test."""
import ast
import hashlib
import http.client
import inspect
import io
import json
import ssl
import sys
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

import pytest  # noqa: E402
from vaxforge import s9_5_adaption as ad  # noqa: E402

KEY = "test-key-NOT-a-secret-0123456789"
API = "https://api.prod.adaptionlabs.ai"
DATA = b'{"prompt": "p1", "completion": "c1"}\n{"prompt": "p2", "completion": "c2"}\n'
SHA = hashlib.sha256(DATA).hexdigest()
RUN = {"id": "run_1", "dataset_id": "ds_private", "model": "Qwen/Qwen3.5-9B", "status": "succeeded",
       "created_at": "2026-11-14T10:00:00.000Z", "completed_at": "2026-11-14T12:30:00+01:00",
       "iterations_completed": 3, "max_iterations": 3, "target_win_rate": 0.7, "best_win_rate": 0.74,
       "training_method": "instruction", "best_hyperparams": {"n_epochs": 2}, "column_mapping": {"prompt": "prompt"},
       "download_available": True, "error": None, "created_by_user_id": "user_42", "organization_id": "org_7"}
CREATED = {"dataset_id": "ds_9", "status": "pending",
           "upload_instructions": {"url": "https://storage.example.com/b/k?X-Amz-Signature=SIG", "method": "PUT",
                                   "s3_key": "k"}}


@pytest.fixture(autouse=True)
def _key_and_no_network(monkeypatch):
    monkeypatch.setenv(ad.KEY_ENV, KEY)

    def no_connection(*a, **k):
        raise AssertionError("a test tried to open a real connection")
    monkeypatch.setattr(http.client, "HTTPSConnection", no_connection)


class Fake:
    """Replies in order and records every request. A reply is (status, payload[, headers]) or an exception to raise."""

    def __init__(self, *replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, method, url, headers, body, timeout):
        self.calls.append({"method": method, "url": url, "headers": dict(headers), "body": body})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        status, payload, *rest = reply
        if not isinstance(payload, bytes):
            payload = json.dumps(payload).encode()
        return ad.Response(status, rest[0] if rest else {}, io.BytesIO(payload))


class Clock:
    def __init__(self):
        self.t, self.naps = 0.0, []

    def now(self):
        return self.t

    def sleep(self, seconds):
        self.naps.append(seconds)
        self.t += seconds


def run_kw(**kw):
    return {"model": "Qwen/Qwen3.5-9B", "max_iterations": 3, "target_win_rate": 0.7, **kw}


def training_file(tmp_path, name="rows.jsonl"):
    p = tmp_path / name
    p.write_bytes(DATA)
    return p


def without_auth(headers):
    return {k: v for k, v in headers.items() if k != "Authorization"}


# ---- read-only calls ------------------------------------------------------------------------------------------------

def test_list_models_sends_the_key_in_the_header_and_returns_only_the_models():
    fake = Fake((200, {"models": [{"id": "m1"}]}))
    assert ad.list_models(transport=fake) == [{"id": "m1"}]
    call = fake.calls[0]
    assert (call["method"], call["url"], call["body"]) == ("GET", API + "/api/v1/autoscientist/models", None)
    assert call["headers"]["Authorization"] == "Bearer " + KEY


def test_a_models_response_without_a_list_is_an_error():
    with pytest.raises(ad.AdaptionError):
        ad.list_models(transport=Fake((200, {})))


def test_a_missing_key_fails_before_anything_is_sent(monkeypatch):
    monkeypatch.delenv(ad.KEY_ENV)
    fake = Fake()
    with pytest.raises(ad.AdaptionError, match="not set"):
        ad.get_run("run_1", transport=fake)
    assert fake.calls == []


def test_get_run_and_dataset_status_use_the_documented_paths():
    fake = Fake((200, RUN), (200, {"status": "running"}))
    assert ad.get_run("run_1", transport=fake) == RUN
    ad.get_dataset_status("ds_1", transport=fake)
    assert [c["url"] for c in fake.calls] == [API + "/api/v1/autoscientist/run_1", API + "/api/v1/datasets/ds_1/status"]


@pytest.mark.parametrize("bad", ["", "a/b", "../x", "a b", "a?b", None, 5])
def test_ids_that_could_change_the_path_are_refused(bad):
    fake = Fake()
    for call in (ad.get_run, ad.get_dataset_status):
        with pytest.raises(ValueError):
            call(bad, transport=fake)
    assert fake.calls == []


# ---- dry run, confirm and guard -------------------------------------------------------------------------------------

def test_create_run_is_a_dry_run_by_default_and_the_key_is_redacted():
    fake = Fake()
    dry = ad.create_run("ds_1", transport=fake, **run_kw())
    assert isinstance(dry, ad.DryRun) and fake.calls == []
    req = dry.requests[0]
    assert (req["method"], req["url"]) == ("POST", API + "/api/v1/autoscientist")
    assert req["headers"]["Authorization"] == "Bearer [REDACTED]"
    assert KEY not in json.dumps(dry.to_dict()) and dry.to_dict()["dry_run"] is True


def test_a_dry_run_does_not_need_or_read_the_key(monkeypatch):
    monkeypatch.delenv(ad.KEY_ENV)
    assert isinstance(ad.create_run("ds_1", **run_kw()), ad.DryRun)


@pytest.mark.parametrize("confirm", ["yes", 1, "true", None])
def test_only_a_literal_true_counts_as_confirmation(confirm):
    fake = Fake()
    assert isinstance(ad.create_run("ds_1", confirm=confirm, guard=lambda: None, transport=fake, **run_kw()), ad.DryRun)
    assert fake.calls == []


def test_confirm_without_a_guard_is_refused_and_sends_nothing():
    fake = Fake()
    for guard in (None, "not callable"):
        with pytest.raises(ad.GuardRequired):
            ad.create_run("ds_1", confirm=True, guard=guard, transport=fake, **run_kw())
    assert fake.calls == []


def test_a_guard_that_raises_or_returns_false_blocks_the_send():
    class NotAllowed(Exception):
        pass

    def refuse():
        raise NotAllowed("leakage check not run")
    fake = Fake()
    with pytest.raises(NotAllowed):
        ad.create_run("ds_1", confirm=True, guard=refuse, transport=fake, **run_kw())
    with pytest.raises(ad.GuardRequired, match="returned False"):
        ad.create_run("ds_1", confirm=True, guard=lambda: False, transport=fake, **run_kw())
    assert fake.calls == []


def test_a_passing_guard_runs_once_and_before_the_single_send():
    order, fake = [], Fake((200, RUN))

    def send(*args):
        order.append("send")
        return fake(*args)
    run = ad.create_run("ds_1", confirm=True, guard=lambda: order.append("guard"), transport=send, **run_kw())
    assert order == ["guard", "send"] and run["id"] == "run_1"
    assert fake.calls[0]["headers"]["Authorization"] == "Bearer " + KEY


def test_the_dry_run_shows_the_request_that_is_really_sent():
    dry = ad.create_run("ds_1", transport=Fake(), **run_kw(idempotency_key="k-1")).requests[0]
    fake = Fake((200, RUN))
    ad.create_run("ds_1", confirm=True, guard=lambda: None, transport=fake, **run_kw(idempotency_key="k-1"))
    sent = fake.calls[0]
    assert (sent["method"], sent["url"], json.loads(sent["body"])) == (dry["method"], dry["url"], dry["body"])
    assert without_auth(sent["headers"]) == without_auth(dry["headers"])


# ---- the run payload ------------------------------------------------------------------------------------------------

def test_the_run_payload_is_sft_only_with_no_augmentation_no_seed_and_no_overrides():
    body = ad.create_run("ds_1", transport=Fake(), **run_kw()).requests[0]["body"]
    assert body == {"dataset_id": "ds_1", "model": "Qwen/Qwen3.5-9B", "training_method": "instruction",
                    "max_iterations": 3, "target_win_rate": 0.7}
    params = inspect.signature(ad.create_run).parameters
    assert not [p for p in params if "augment" in p or "seed" in p or p in ("hyperparams", "voucher", "training_method")]


def test_optional_fields_appear_only_when_given():
    body = ad.create_run("ds_1", transport=Fake(), **run_kw(
        data_format="chat", column_mapping={"prompt": "p", "completion": "c"}, idempotency_key="k:1")).requests[0]["body"]
    assert body["data_format"] == "chat" and body["column_mapping"] == {"prompt": "p", "completion": "c"}
    assert body["idempotency_key"] == "k:1"  # the key is sent as given, not URL-quoted


@pytest.mark.parametrize("override", [
    {"max_iterations": 0}, {"max_iterations": 6}, {"max_iterations": True}, {"max_iterations": 2.5},
    {"max_iterations": "3"}, {"target_win_rate": 0.5}, {"target_win_rate": 0.4}, {"target_win_rate": 1.01},
    {"target_win_rate": "0.7"}, {"target_win_rate": True}, {"target_win_rate": float("nan")},
    {"model": ""}, {"model": "../x"}, {"model": "a b"}, {"model": None},
    {"data_format": "alpaca"}, {"column_mapping": {"prompt": "p"}}, {"column_mapping": {"prompt": "p", "completion": ""}},
    {"column_mapping": {"prompt": "p", "completion": "c", "chosen": "x"}}, {"idempotency_key": "a b"},
])
def test_bad_run_arguments_are_refused(override):
    with pytest.raises(ValueError):
        ad.create_run("ds_1", **{**run_kw(), **override})


def test_the_boundary_values_the_docs_allow_are_accepted():
    for iterations, target in ((1, 0.51), (5, 1), (5, 1.0)):
        ad.create_run("ds_1", **run_kw(max_iterations=iterations, target_win_rate=target))


def test_a_run_needs_a_safe_dataset_id():
    with pytest.raises(ValueError):
        ad.create_run("ds/../x", **run_kw())


# ---- upload ---------------------------------------------------------------------------------------------------------

def test_the_upload_dry_run_lists_all_three_requests_and_sends_nothing(tmp_path):
    fake = Fake()
    create, put, complete = ad.upload_training_file(training_file(tmp_path), transport=fake).requests
    assert fake.calls == []
    assert create["body"] == {"source": {"name": "rows", "file_format": "jsonl", "processing_mode": "raw",
                                         "column_mapping": {"prompt": "prompt", "completion": "completion"}}}
    assert put["method"] == "PUT" and "Authorization" not in put["headers"]
    assert put["headers"]["Content-Length"] == str(len(DATA)) and SHA in put["body"]
    assert complete["body"] == {"file_size_bytes": len(DATA), "sha256": SHA}
    assert create["headers"]["Authorization"] == complete["headers"]["Authorization"] == "Bearer [REDACTED]"


def test_a_confirmed_upload_creates_puts_and_completes_in_order_and_keeps_the_key_off_the_storage_host(tmp_path):
    order = []
    fake = Fake((200, CREATED), (200, b""), (200, {"dataset_id": "ds_9", "status": "processing"}))

    def send(*args):
        order.append("send")
        return fake(*args)
    out = ad.upload_training_file(training_file(tmp_path), name="my-rows", confirm=True,
                                  guard=lambda: order.append("guard"), transport=send)
    assert order == ["guard", "send", "send", "send"]
    create, put, complete = fake.calls
    assert [(c["method"], urllib.parse.urlsplit(c["url"]).hostname) for c in fake.calls] == [
        ("POST", "api.prod.adaptionlabs.ai"), ("PUT", "storage.example.com"), ("POST", "api.prod.adaptionlabs.ai")]
    assert json.loads(create["body"])["source"]["name"] == "my-rows"
    assert "Authorization" not in put["headers"] and put["body"] == DATA
    assert put["url"] == CREATED["upload_instructions"]["url"]
    assert complete["url"] == API + "/api/v1/datasets/ds_9/upload/complete"
    assert json.loads(complete["body"]) == {"file_size_bytes": len(DATA), "sha256": SHA}
    assert out == {"dataset_id": "ds_9", "status": "processing", "file_sha256": SHA, "file_size_bytes": len(DATA)}


def test_a_failed_storage_put_names_the_dataset_hides_the_signed_url_and_deletes_nothing(tmp_path):
    fake = Fake((200, CREATED), (403, b"<Error>SignatureDoesNotMatch</Error>"))
    with pytest.raises(ad.AdaptionError) as caught:
        ad.upload_training_file(training_file(tmp_path), confirm=True, guard=lambda: None, transport=fake)
    assert "ds_9" in str(caught.value) and "SIG" not in str(caught.value) and caught.value.status == 403
    assert [c["method"] for c in fake.calls] == ["POST", "PUT"]


def test_upload_instructions_that_are_not_an_https_put_move_no_bytes(tmp_path):
    for inst in ({"url": "http://storage.example.com/k", "method": "PUT"},
                 {"url": "https://storage.example.com/k", "method": "POST"}, {}):
        fake = Fake((200, {"dataset_id": "ds_9", "upload_instructions": inst}))
        with pytest.raises(ad.AdaptionError):
            ad.upload_training_file(training_file(tmp_path), confirm=True, guard=lambda: None, transport=fake)
        assert len(fake.calls) == 1


def test_an_unsafe_dataset_id_in_the_response_moves_no_bytes(tmp_path):
    fake = Fake((200, {**CREATED, "dataset_id": "../x"}))
    with pytest.raises(ValueError):
        ad.upload_training_file(training_file(tmp_path), confirm=True, guard=lambda: None, transport=fake)
    assert len(fake.calls) == 1


@pytest.mark.parametrize("name, mapping", [
    ("rows.txt", None), ("rows.pdf", None), ("rows", None),
    ("rows.jsonl", {"prompt": "p"}), ("rows.jsonl", {"prompt": "p", "completion": "c", "chosen": "x"}),
    ("rows.jsonl", {"prompt": "p", "completion": ""}), ("rows.jsonl", {"prompt": "p", "rejected": "r"}),
])
def test_raw_upload_needs_a_tabular_file_and_an_sft_column_mapping(tmp_path, name, mapping):
    with pytest.raises(ValueError):
        ad.upload_training_file(training_file(tmp_path, name), column_mapping=mapping, transport=Fake())


def test_other_tabular_formats_and_custom_columns_are_described_correctly(tmp_path):
    body = ad.upload_training_file(training_file(tmp_path, "rows.PARQUET"),
                                   column_mapping={"prompt": "instr", "completion": "code"}).requests[0]["body"]
    assert body["source"]["file_format"] == "parquet"
    assert body["source"]["column_mapping"] == {"prompt": "instr", "completion": "code"}


# ---- waiting --------------------------------------------------------------------------------------------------------

def test_wait_for_run_backs_off_from_10_to_60_seconds_and_returns_the_terminal_run():
    clock = Clock()
    states = ("pending", "running", "running", "running", "running", "succeeded")
    fake = Fake(*[(200, {"id": "run_1", "status": s}) for s in states])
    run = ad.wait_for_run("run_1", transport=fake, sleep=clock.sleep, clock=clock.now)
    assert run["status"] == "succeeded" and clock.naps == [10, 20, 40, 60, 60]


@pytest.mark.parametrize("status", ["failed", "cancelled"])
def test_wait_for_run_returns_failed_and_cancelled_runs_for_the_caller_to_judge(status):
    fake = Fake((200, {"id": "run_1", "status": status}))
    assert ad.wait_for_run("run_1", transport=fake, sleep=Clock().sleep)["status"] == status


def test_wait_for_run_retries_gateway_errors_and_dropped_connections_but_not_auth_errors():
    clock = Clock()
    fake = Fake((503, b"busy"), ConnectionResetError("reset"), (200, {"id": "run_1", "status": "succeeded"}))
    assert ad.wait_for_run("run_1", transport=fake, sleep=clock.sleep, clock=clock.now)["status"] == "succeeded"
    with pytest.raises(ad.AdaptionError) as caught:
        ad.wait_for_run("run_1", transport=Fake((401, {"error": "bad key"})), sleep=clock.sleep, clock=clock.now)
    assert caught.value.status == 401


def test_wait_for_run_times_out_and_says_the_run_may_still_be_going():
    clock = Clock()
    fake = Fake(*[(200, {"id": "run_1", "status": "running"})] * 20)
    with pytest.raises(ad.RunTimeout, match="may still be running") as caught:
        ad.wait_for_run("run_1", timeout=100, transport=fake, sleep=clock.sleep, clock=clock.now)
    assert isinstance(caught.value, TimeoutError)


def test_wait_for_dataset_waits_for_a_row_count_and_raises_on_failure_or_awaiting_input():
    clock = Clock()
    fake = Fake((200, {"status": "running", "row_count": None}), (200, {"status": "succeeded", "row_count": None}),
                (200, {"status": "succeeded", "row_count": 1200}))
    assert ad.wait_for_dataset("ds_1", transport=fake, sleep=clock.sleep, clock=clock.now)["row_count"] == 1200
    with pytest.raises(ad.AdaptionError, match="bad column"):
        ad.wait_for_dataset("ds_1", sleep=clock.sleep, clock=clock.now,
                            transport=Fake((200, {"status": "failed", "error_data": {"message": "bad column"}})))
    with pytest.raises(ad.AdaptionError, match="awaiting_input"):
        ad.wait_for_dataset("ds_1", sleep=clock.sleep, clock=clock.now, transport=Fake((200, {"status": "awaiting_input"})))


# ---- checkpoint download --------------------------------------------------------------------------------------------

def test_download_streams_to_a_file_and_returns_its_sha256(tmp_path):
    payload = bytes(range(256)) * 400
    fake = Fake((200, payload, {"content-length": str(len(payload))}))
    dest = tmp_path / "scratch" / "best.tgz"
    assert ad.download_checkpoint("run_1", dest, chunk_size=1000, transport=fake) == hashlib.sha256(payload).hexdigest()
    call = fake.calls[0]
    assert (call["method"], call["url"]) == ("GET", API + "/api/v1/autoscientist/run_1/download")
    assert call["headers"]["Accept"] == "application/gzip"
    assert dest.read_bytes() == payload
    assert list(dest.parent.iterdir()) == [dest]  # no .part left behind and nothing extracted


def test_download_never_overwrites_an_existing_file(tmp_path):
    dest = tmp_path / "best.tgz"
    dest.write_bytes(b"keep me")
    fake = Fake()
    with pytest.raises(FileExistsError):
        ad.download_checkpoint("run_1", dest, transport=fake)
    assert dest.read_bytes() == b"keep me" and fake.calls == []


def test_a_truncated_download_leaves_neither_the_file_nor_a_partial(tmp_path):
    dest = tmp_path / "best.tgz"
    with pytest.raises(ad.AdaptionError, match="truncated"):
        ad.download_checkpoint("run_1", dest, transport=Fake((200, b"x" * 50, {"content-length": "100"})))
    assert list(tmp_path.iterdir()) == []


def test_a_download_error_leaves_no_files_and_does_not_echo_the_key(tmp_path):
    dest = tmp_path / "best.tgz"
    with pytest.raises(ad.AdaptionError) as caught:
        ad.download_checkpoint("run_1", dest, transport=Fake((404, f"no such run for {KEY}".encode())))
    assert caught.value.status == 404 and KEY not in str(caught.value) and "[REDACTED]" in str(caught.value)
    assert list(tmp_path.iterdir()) == []


def test_a_stale_part_file_is_not_deleted_or_overwritten(tmp_path):
    dest = tmp_path / "best.tgz"
    part = tmp_path / "best.tgz.part"
    part.write_bytes(b"someone else's")
    with pytest.raises(FileExistsError):
        ad.download_checkpoint("run_1", dest, transport=Fake((200, b"abc")))
    assert part.read_bytes() == b"someone else's"


# ---- evidence -------------------------------------------------------------------------------------------------------

def test_record_evidence_copies_only_the_documented_fields(tmp_path):
    path = ad.record_evidence(RUN, tmp_path / "out", checkpoint_sha256="a" * 64,
                              now=datetime(2026, 11, 14, 13, 0, tzinfo=timezone.utc))
    assert path == tmp_path / "out" / "adaption_evidence.json"
    text = path.read_text()
    assert json.loads(text) == {
        "run_id": "run_1", "status": "succeeded", "training_method": "instruction", "base_model_id": "Qwen/Qwen3.5-9B",
        "iterations_completed": 3, "max_iterations": 3, "best_win_rate": 0.74, "target_win_rate": 0.7,
        "target_reached": True, "checkpoint_sha256": "a" * 64,
        "created_at_utc": "2026-11-14T10:00:00Z", "completed_at_utc": "2026-11-14T11:30:00Z",
        "recorded_at_utc": "2026-11-14T13:00:00Z", "caveat": ad.CAVEAT}
    for private in ("ds_private", "user_42", "org_7", KEY, "n_epochs"):
        assert private not in text


def test_record_evidence_says_null_when_nothing_was_downloaded_and_records_failed_runs(tmp_path):
    failed = {"id": "run_2", "status": "failed", "model": "m", "error": "boom with ds_private", "best_win_rate": None,
              "target_win_rate": 0.8, "training_method": "instruction"}
    record = json.loads(ad.record_evidence(failed, tmp_path).read_text())
    assert record["status"] == "failed" and record["checkpoint_sha256"] is None and record["target_reached"] is None
    assert record["completed_at_utc"] is None and "boom" not in json.dumps(record)


def test_target_reached_compares_best_with_target(tmp_path):
    below = {**RUN, "best_win_rate": 0.6}
    assert json.loads(ad.record_evidence(below, tmp_path).read_text())["target_reached"] is False


@pytest.mark.parametrize("run, sha", [(None, None), ({}, None), ({"id": "r"}, None), ({"status": "failed"}, None),
                                       (RUN, "A" * 64), (RUN, "a" * 63), (RUN, "not a hash")])
def test_record_evidence_refuses_things_that_are_not_runs_and_bad_hashes(tmp_path, run, sha):
    with pytest.raises(ValueError):
        ad.record_evidence(run, tmp_path, checkpoint_sha256=sha)
    assert list(tmp_path.iterdir()) == []


def test_record_evidence_replaces_the_file_and_leaves_no_temp_file(tmp_path):
    ad.record_evidence({**RUN, "status": "running"}, tmp_path)
    ad.record_evidence(RUN, tmp_path)
    assert [p.name for p in tmp_path.iterdir()] == ["adaption_evidence.json"]
    assert json.loads((tmp_path / "adaption_evidence.json").read_text())["status"] == "succeeded"


# ---- the key and the connection -------------------------------------------------------------------------------------

def test_error_messages_never_contain_the_key():
    fake = Fake((400, json.dumps({"detail": f"bad request from {KEY}"}).encode()))
    with pytest.raises(ad.AdaptionError) as caught:
        ad.get_run("run_1", transport=fake)
    assert KEY not in str(caught.value) and "[REDACTED]" in str(caught.value) and caught.value.status == 400


def test_the_key_is_only_ever_attached_to_the_api_host():
    fake = Fake()
    with pytest.raises(ad.AdaptionError, match="another host"):
        ad._do(ad._req("GET", "", url="https://evil.example.com/x"), fake, 5)
    assert fake.calls == []


def test_nothing_is_printed_while_calls_run(tmp_path, capsys):
    ad.list_models(transport=Fake((200, {"models": []})))
    ad.create_run("ds_1", confirm=True, guard=lambda: None, transport=Fake((200, RUN)), **run_kw())
    ad.record_evidence(RUN, tmp_path)
    out = capsys.readouterr()
    assert out.out == "" and out.err == ""


def test_the_module_neither_prints_nor_logs_and_uses_only_the_standard_library():
    tree = ast.parse(Path(ad.__file__).read_text())
    calls = {n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    modules = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
    modules |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
    assert "print" not in calls and "logging" not in modules and "warnings" not in modules
    assert modules - set(sys.stdlib_module_names) <= {"certifi"}  # optional CA bundle, imported inside try/except ImportError


def test_the_default_transport_refuses_plain_http_before_connecting():
    with pytest.raises(ad.AdaptionError, match="non-https"):
        ad._http("GET", "http://api.prod.adaptionlabs.ai/x", {}, None, 5)  # the autouse fixture fails on any connection


def test_the_tls_context_keeps_certificate_and_hostname_checks_on():
    ctx = ad._ssl_context()
    assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True


def test_a_certificate_failure_is_reported_with_the_fix():
    with pytest.raises(ad.AdaptionError, match="certifi"):
        ad.list_models(transport=Fake(ssl.SSLCertVerificationError(1, "unable to get local issuer certificate")))
