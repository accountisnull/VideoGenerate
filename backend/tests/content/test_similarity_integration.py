"""主管审核、向量入库和取消恢复；使用临时真库与确定性向量。"""

import asyncio
import json
import sys
from pathlib import Path
from uuid import uuid4

import pytest
from qdrant_client import QdrantClient

from app.content.adapters import ToolAdapter
from app.content.executor import ContentExecutor
from app.content.models import Draft, ReviewDecision
from app.content.orchestrator import ContentOrchestrator
from app.content.providers.embedding import LocalEmbedding
from app.content.review.similarity import SimilarityService
from app.content.similarity_agent import SimilarityAgent, vector_mcp_session
from app.content.similarity_models import IndexProfile
from app.content.storage import ContentStore
from app.content.storage.vector_index import QdrantIndex
from app.contracts import ContentRequest


class Embedding:
    async def aembed(self, script):
        return [1.0, 0.0]


class Generate:
    async def generate(self, request):
        return Draft(script="测试合格正文", title="标题", tags=())


class Review:
    async def review(self, request):
        return ReviewDecision(passed=True, issues=())


@pytest.mark.parametrize("lost_response", [False, True])
def test_executor_stores_after_approval_and_reconciles_without_regeneration(tmp_path, lost_response):
    async def scenario():
        client = QdrantClient(location=":memory:")
        profile = IndexProfile(model_id="test", model_revision="r1", dimension=2, index_version="v1")
        index = QdrantIndex(client, "test", profile)
        index.initialize()
        adapter = ToolAdapter(SimilarityService(Embedding(), index, profile))
        calls = []

        async def call(action, request):
            calls.append(action)
            response = await adapter.acall(action, request)
            if action == "store" and lost_response:
                raise TimeoutError()
            return response

        vector = SimilarityAgent(call)
        engine = ContentOrchestrator(generator=Generate(), keywords=Review(), content_review=Review(),
                                     additional_reviews={"similarity": vector}, call_timeout_seconds=2)
        engine.vector_agent = vector
        store = ContentStore(tmp_path / "content.db")
        store.initialize()
        job, _ = store.submit(ContentRequest(task_id=uuid4(), topic="主题"), uuid4(), "test", ready=True)
        executor = ContentExecutor(store, engine)
        task = asyncio.create_task(executor.run())
        try:
            async with asyncio.timeout(5):
                while store.get(job.root.job_id, "test").root.state not in ("succeeded", "failed"):
                    await asyncio.sleep(.01)
        finally:
            executor.stop.set()
            executor.wake.set()
            await task
        final = store.get(job.root.job_id, "test").root
        assert final.state == ("failed" if lost_response else "succeeded")
        assert client.count("test").count == 1
        assert calls == ["detect", "store"]
        pending = store.pending_vector_calls()
        assert bool(pending) == lost_response
        if lost_response:
            intent = json.loads(pending[0]["input_json"])
            assert intent["request"]["approval"]["checks"] == {
                "keywords": True, "content_audit": True, "similarity": True,
            }
            restored = ContentExecutor(store, engine)
            restored.stop.set()
            await restored.run()
            assert calls == ["detect", "store", "reconcile"]
            assert not store.pending_vector_calls()
            assert store.get(job.root.job_id, "test").root.state == "failed"
        client.close()
    asyncio.run(scenario())


def test_cancel_embedding_kills_and_reaps_real_child(monkeypatch, tmp_path):
    async def scenario():
        real_spawn = asyncio.create_subprocess_exec
        spawned = []

        async def spawn(*args, **kwargs):
            child = await real_spawn(sys.executable, "-c", "import time; time.sleep(30)",
                stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
            spawned.append(child)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", spawn)
        profile = IndexProfile(model_id="test", model_revision="r", dimension=2, index_version="v")
        embed = LocalEmbedding(tmp_path, profile)
        task = asyncio.create_task(embed.aembed("正文"))
        async with asyncio.timeout(3):
            while not spawned:
                await asyncio.sleep(.01)
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert spawned[0].returncode is not None
    asyncio.run(scenario())


def test_mcp_tool_cancel_does_not_reach_index_write():
    from app.content.mcp_server import create_server

    async def scenario():
        entered = asyncio.Event()
        stopped = asyncio.Event()

        class Adapter:
            async def acall(self, action, request):
                entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    stopped.set()

        task = asyncio.create_task(create_server(Adapter()).call_tool("detect", {"request": {}}))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert stopped.is_set()
    asyncio.run(scenario())


def test_real_mcp_client_reconciles_and_preserves_profile(tmp_path, monkeypatch):
    profile = IndexProfile(model_id="BAAI/bge-m3", model_revision="local-sha256:" + "a" * 64,
                           dimension=1024, index_version="v1")
    db = tmp_path / "qdrant"
    client = QdrantClient(path=str(db))
    QdrantIndex(client, "test", profile).initialize()
    client.close()
    for key, value in {
        "MODEL_PATH":str(tmp_path / "unused-model"), "MODEL_REVISION":profile.model_revision,
        "INDEX_VERSION":"v1", "QDRANT_PATH":str(db), "QDRANT_URL":"", "COLLECTION":"test",
    }.items():
        monkeypatch.setenv("SIMILARITY_"+key, value)

    async def scenario():
        async with vector_mcp_session(Path(sys.executable), Path.cwd(), 10) as agent:
            data = await agent.invoke("reconcile", {
                "script":"正文", "job_id":str(uuid4()), "content_version_id":str(uuid4()),
                "expected_profile":profile.model_dump(mode="json"),
            })
            assert data["saved"] is False
    asyncio.run(scenario())


def test_http_lifespan_owns_real_mcp_and_model_failure_blocks_job(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    from app.content.api import create_app
    from app.content.settings import ContentSettings

    profile = IndexProfile(model_id="BAAI/bge-m3", model_revision="local-sha256:" + "b" * 64,
                           dimension=1024, index_version="v1")
    path = tmp_path / "index"
    client = QdrantClient(path=str(path))
    QdrantIndex(client, "test", profile).initialize()
    client.close()
    for name, value in {"MODEL_PATH":str(tmp_path / "missing"), "MODEL_REVISION":profile.model_revision,
                        "INDEX_VERSION":"v1", "QDRANT_PATH":str(path), "QDRANT_URL":"", "COLLECTION":"test"}.items():
        monkeypatch.setenv("SIMILARITY_" + name, value)
    settings = ContentSettings(database=tmp_path / "content.db", asset_root=tmp_path / "assets",
                               similarity_enabled=True, similarity_python=Path(sys.executable))
    engine = ContentOrchestrator(generator=Generate(), keywords=Review(), content_review=Review(),
                                 call_timeout_seconds=2)
    with TestClient(create_app(settings, engine)) as http:
        response = http.post("/v1/content/jobs", json={"task_id":str(uuid4()), "topic":"主题"},
                             headers={"Idempotency-Key":str(uuid4())})
        assert response.status_code == 202
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            job = http.get(response.headers["Location"]).json()
            if job["state"] in ("failed", "succeeded"):
                break
            time.sleep(.01)
        assert job["state"] == "failed" and job["result"] is None
    # lifespan 关闭后索引独占锁必须已经释放。
    client = QdrantClient(path=str(path))
    assert client.count("test").count == 0
    client.close()


def test_malformed_similarity_cannot_claim_passed():
    from pydantic import ValidationError

    from app.content.similarity_models import DetectionResult

    with pytest.raises(ValidationError):
        DetectionResult.model_validate({
            "passed":True, "reason":"BELOW_THRESHOLD", "max_similarity":0.99,
            "matches":[], "profile":{"model_id":"test", "model_revision":"r", "dimension":2, "index_version":"v"},
        })


def test_cancel_during_embedding_never_writes_and_profile_change_rejected():
    from app.content.similarity_models import Approval, StoreRequest, script_digest

    async def scenario():
        entered = asyncio.Event()

        class Waiting:
            async def aembed(self, script):
                entered.set()
                await asyncio.Event().wait()

        client = QdrantClient(location=":memory:")
        profile = IndexProfile(model_id="test", model_revision="r", dimension=2, index_version="v")
        index = QdrantIndex(client, "test", profile)
        index.initialize()
        adapter = ToolAdapter(SimilarityService(Waiting(), index, profile))
        job, version = uuid4(), uuid4()
        request = StoreRequest(script="正文", job_id=job, content_version_id=version,
            approval=Approval(approval_id=uuid4(), job_id=job, content_version_id=version,
                script_sha256=script_digest("正文"), checks={"keywords":True,"content_audit":True,"similarity":True}))
        task = asyncio.create_task(adapter.acall("store", request.model_dump(mode="json")))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert client.count("test").count == 0
        changed = profile.model_copy(update={"index_version":"different"})
        result = await adapter.acall("store", {
            **request.model_dump(mode="json"), "expected_profile":changed.model_dump(mode="json"),
        })
        assert result["error"]["code"] == "INDEX_INCOMPATIBLE"
        assert client.count("test").count == 0
        client.close()
    asyncio.run(scenario())
