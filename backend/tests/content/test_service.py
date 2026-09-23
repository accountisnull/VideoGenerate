"""内容服务受理、恢复和真实编排接线测试，模型调用全部使用替身。"""

import asyncio
import json
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.content.api import create_app
from app.content.interfaces import CapabilityError
from app.content.models import Draft, ReviewDecision
from app.content.orchestrator import ContentOrchestrator
from app.content.settings import ContentSettings
from app.content.storage import ContentStore, StoreError
from app.content.storage.ownership import ServiceOwnership
from app.contracts import ContentJob, ContentRequest


class Generator:
    def __init__(self):
        self.calls = []
        self.started = threading.Event()
        self.release = threading.Event()
        self.release.set()

    async def generate(self, request):
        self.calls.append(request)
        self.started.set()
        while not self.release.is_set():
            await asyncio.sleep(0.005)
        return Draft(script="真实接口的测试替身稿件", title="测试标题", tags=("测试",))


class Reviewer:
    async def review(self, request):
        return ReviewDecision(passed=True, issues=())


def orchestrator(generator=None, audit=None):
    return ContentOrchestrator(
        generator=generator or Generator(),
        keywords=Reviewer(),
        content_review=audit or Reviewer(),
        call_timeout_seconds=2,
    )


@pytest.fixture
def settings(tmp_path):
    return ContentSettings(
        database=tmp_path / "content.db", asset_root=tmp_path / "assets"
    )


@pytest.fixture
def store(settings):
    value = ContentStore(settings.database)
    value.initialize()
    return value


def payload(**kwargs):
    return {"task_id": str(uuid4()), "topic": "绿萝养护", **kwargs}


def headers(key=None):
    return {"Idempotency-Key": str(key or uuid4())}


def wait_terminal(client, location):
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        response = client.get(location)
        assert response.status_code == 200
        job = ContentJob.model_validate(response.json())
        if job.root.state in ("succeeded", "failed"):
            return response.json()
        time.sleep(0.01)
    pytest.fail("内容作业未按时结束")


def test_submit_async_busy_replay_and_restart(settings):
    gen = Generator()
    gen.release.clear()
    app = create_app(settings, orchestrator(gen))
    body, key = payload(), headers()
    try:
        with TestClient(app) as client:
            assert client.get("/v1/content/health").status_code == 200
            assert client.get("/v1/content/capabilities").json()["resources"] == []
            created = client.post("/v1/content/jobs", json=body, headers=key)
            assert created.status_code == 202
            assert created.json()["state"] == "queued"
            assert created.json()["result"] is None and created.json()["error"] is None
            assert gen.started.wait(1)
            assert (
                client.post("/v1/content/jobs", json=body, headers=key).status_code
                == 200
            )
            busy = client.post("/v1/content/jobs", json=payload(), headers=headers())
            assert busy.status_code == 409 and busy.headers["Retry-After"] == "2"
            conflict = client.post(
                "/v1/content/jobs", json={**body, "topic": "不同主题"}, headers=key
            )
            assert conflict.json()["error"]["code"] == "IDEMPOTENCY_CONFLICT"
            gen.release.set()
            final = wait_terminal(client, created.headers["Location"])
            assert final["state"] == "succeeded"
            assert len(gen.calls) == 1
    finally:
        gen.release.set()
    # 不具备生成能力时，仍能查询或准确重放已有结果。
    with TestClient(create_app(settings)) as client:
        replay = client.post("/v1/content/jobs", json=body, headers=key)
        assert replay.status_code == 200 and replay.json() == final
        assert client.get("/v1/content/health").status_code == 503
        assert (
            client.post(
                "/v1/content/jobs", json=payload(), headers=headers()
            ).status_code
            == 503
        )
    with sqlite3.connect(settings.database) as db:
        calls = db.execute(
            "SELECT stage,input_json,output_json,finished_at FROM content_calls"
        ).fetchall()
    assert len(calls) == 3
    assert all(row[2] and row[3] for row in calls)
    assert json.loads(calls[0][1])["context"]["topic"] == body["topic"]


def test_concurrent_idempotency_is_atomic(store):
    request, key = ContentRequest.model_validate(payload()), uuid4()
    with ThreadPoolExecutor(max_workers=5) as pool:
        results = list(
            pool.map(
                lambda _: store.submit(request, key, "caller", ready=True), range(5)
            )
        )
    assert sum(created for _, created in results) == 1
    assert len({job.root.job_id for job, _ in results}) == 1
    assert store.submit(request, key, "caller", ready=False)[1] is False


def test_interrupted_job_is_failed_without_replaying_call(store, settings):
    body, key = payload(), uuid4()
    job, _ = store.submit(
        ContentRequest.model_validate(body), key, "local-worker", ready=True
    )
    store.claim()
    store.begin_call(job.root.job_id, "generation_1", "generator", "{}")
    gen = Generator()
    with TestClient(create_app(settings, orchestrator(gen))) as client:
        found = client.get(f"/v1/content/jobs/{job.root.job_id}").json()
        assert found["state"] == "failed"
        assert found["error"]["code"] == "EXECUTION_INTERRUPTED"
        assert (
            client.post("/v1/content/jobs", json=body, headers=headers(key)).json()
            == found
        )
        assert not gen.calls
    with pytest.raises(StoreError, match="INVALID_TRANSITION"):
        store.fail_running(job.root.job_id)


def test_queued_job_is_safe_to_start_on_restart(store, settings):
    job, _ = store.submit(
        ContentRequest.model_validate(payload()), uuid4(), "local-worker", ready=True
    )
    gen = Generator()
    with TestClient(create_app(settings, orchestrator(gen))) as client:
        assert (
            wait_terminal(client, f"/v1/content/jobs/{job.root.job_id}")["state"]
            == "succeeded"
        )
    assert len(gen.calls) == 1


def test_revision_validates_source_and_preserves_original(settings):
    gen = Generator()
    with TestClient(create_app(settings, orchestrator(gen))) as client:
        body = payload()
        first = client.post("/v1/content/jobs", json=body, headers=headers())
        final = wait_terminal(client, first.headers["Location"])
        revision = {"source_job_id": final["job_id"], "instructions": "补充浇水建议"}
        invalid = client.post(
            "/v1/content/jobs", json=payload(revision=revision), headers=headers()
        )
        assert invalid.status_code == 422
        revised = client.post(
            "/v1/content/jobs", json={**body, "revision": revision}, headers=headers()
        )
        assert revised.status_code == 202
        assert (
            wait_terminal(client, revised.headers["Location"])["state"] == "succeeded"
        )
        assert revised.json()["job_id"] != final["job_id"]
        assert client.get(first.headers["Location"]).json() == final
    assert gen.calls[-1].previous_draft.script == final["result"]["script"]
    assert gen.calls[-1].instructions == revision["instructions"]


@pytest.mark.parametrize(
    "raw", ["{", '{"topic":NaN}', '{"topic":"a","topic":"b"}', b"\xff"]
)
def test_invalid_json_is_sanitized(settings, raw):
    with TestClient(create_app(settings)) as client:
        response = client.post("/v1/content/jobs", content=raw, headers=headers())
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_JSON"
        assert response.headers["X-Request-ID"] == response.json()["request_id"]


def test_auth_and_input_errors_do_not_leak(settings):
    secured = settings.model_copy(update={"token": None})
    secured = ContentSettings(**{**secured.model_dump(), "token": "test-secret"})
    with TestClient(create_app(secured)) as client:
        for path in ("health", "capabilities", "jobs/" + str(uuid4())):
            assert client.get("/v1/content/" + path).status_code == 401
        auth = {"Authorization": "Bearer test-secret"}
        assert client.get("/v1/content/capabilities", headers=auth).status_code == 200
        assert (
            client.post("/v1/content/jobs", json=payload(), headers=auth).status_code
            == 422
        )
        invalid = client.post(
            "/v1/content/jobs",
            json=payload(secret="sensitive"),
            headers={**auth, **headers()},
        )
        assert invalid.status_code == 422 and "sensitive" not in invalid.text
        assert (
            client.get("/v1/content/jobs/not-a-uuid", headers=auth).status_code == 422
        )
        assert (
            client.get("/v1/content/jobs/" + str(uuid4()), headers=auth).status_code
            == 404
        )
        request_id = str(uuid4())
        response = client.get(
            "/v1/content/capabilities", headers={**auth, "X-Request-ID": request_id}
        )
        assert response.headers["X-Request-ID"] == request_id
        assert (
            client.get(
                "/v1/content/health", headers={**auth, "X-Request-ID": "bad"}
            ).status_code
            == 422
        )


def test_provider_failure_is_business_failure(settings):
    class FailingAudit:
        async def review(self, request):
            raise CapabilityError()

    with TestClient(create_app(settings, orchestrator(audit=FailingAudit()))) as client:
        response = client.post("/v1/content/jobs", json=payload(), headers=headers())
        final = wait_terminal(client, response.headers["Location"])
        assert final["state"] == "failed"
        assert final["result"] is None
        assert final["error"]["code"] == "UPSTREAM_FAILED"


def test_storage_failure_before_call_prevents_provider_execution(
    store, settings, monkeypatch
):
    gen = Generator()
    app = create_app(settings, orchestrator(gen))

    def fail(*args):
        raise OSError("sensitive path")

    monkeypatch.setattr(app.state.store, "begin_call", fail)
    with TestClient(app) as client:
        response = client.post("/v1/content/jobs", json=payload(), headers=headers())
        final = wait_terminal(client, response.headers["Location"])
        assert final["state"] == "failed" and final["error"]["code"] == "INTERNAL_ERROR"
        assert "sensitive" not in json.dumps(final)
        assert not gen.calls


def test_schema_refuses_foreign_or_future_database(tmp_path):
    foreign = tmp_path / "foreign.db"
    with sqlite3.connect(foreign) as db:
        db.execute("CREATE TABLE other(value TEXT)")
    with pytest.raises(StoreError):
        ContentStore(foreign).initialize()
    future = tmp_path / "future.db"
    store = ContentStore(future)
    store.initialize()
    with sqlite3.connect(future) as db:
        db.execute("PRAGMA user_version=99")
    with pytest.raises(StoreError):
        store.initialize()


def test_second_instance_cannot_interrupt_live_service(settings):
    owner = ServiceOwnership(settings.database)
    owner.acquire()
    try:
        with (
            pytest.raises(RuntimeError, match="已有服务占用"),
            TestClient(create_app(settings)),
        ):
            pass
    finally:
        owner.release()
    with TestClient(create_app(settings)) as client:
        assert client.get("/v1/content/capabilities").status_code == 200


def test_result_journal_failure_stops_before_review(settings, monkeypatch):
    audit_calls = []

    class CountingReviewer:
        async def review(self, request):
            audit_calls.append(request)
            return ReviewDecision(passed=True, issues=())

    gen = Generator()
    app = create_app(settings, orchestrator(gen, CountingReviewer()))

    def fail(*args):
        raise OSError("结果记录失败")

    monkeypatch.setattr(app.state.store, "finish_call", fail)
    with TestClient(app) as client:
        response = client.post("/v1/content/jobs", json=payload(), headers=headers())
        final = wait_terminal(client, response.headers["Location"])
        assert final["state"] == "failed"
        assert not audit_calls
        assert len(gen.calls) == 1


def test_caller_scope_and_normalized_replay(store):
    body = payload()
    key = uuid4()
    first, _ = store.submit(
        ContentRequest.model_validate(body), key, "first", ready=True
    )
    normalized = ContentRequest.model_validate({**body, "topic": "  绿萝养护  "})
    replay, created = store.submit(normalized, key, "first", ready=True)
    assert not created and replay == first
    with pytest.raises(StoreError, match="JOB_NOT_FOUND"):
        store.get(first.root.job_id, "second")
    with pytest.raises(StoreError, match="SERVICE_BUSY"):
        store.submit(normalized, key, "second", ready=True)


def test_environment_paths_and_token_validation(monkeypatch):
    from app.content import settings as config

    monkeypatch.setattr(
        config,
        "environment_values",
        lambda: {
            "CONTENT_DATABASE_PATH": "data/test-content/job.db",
            "CONTENT_ASSET_ROOT": "data/shared-assets",
            "CONTENT_SERVICE_TOKEN": "abc-123",
        },
    )
    loaded = config.load_settings()
    assert loaded.database == (config.ROOT / "data/test-content/job.db").resolve()
    assert loaded.asset_root == (config.ROOT / "data/shared-assets").resolve()
    assert loaded.token.get_secret_value() == "abc-123"
    monkeypatch.setattr(
        config, "environment_values", lambda: {"CONTENT_DATABASE_PATH": " "}
    )
    with pytest.raises(ValueError):
        config.load_settings()
    monkeypatch.setattr(
        config, "environment_values", lambda: {"CONTENT_SERVICE_TOKEN": "bad\nsecret"}
    )
    with pytest.raises(ValueError):
        config.load_settings()
