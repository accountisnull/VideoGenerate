"""外部工具不能绕过审核入库，也不能把错误包装成通过。"""

import asyncio
import hashlib
import json
from uuid import uuid4

from qdrant_client import QdrantClient

from app.content.adapters import ToolAdapter
from app.content.mcp_server import create_server
from app.content.review.similarity import SimilarityService
from app.content.similarity_models import IndexProfile
from app.content.storage.vector_index import QdrantIndex


class Embedding:
    def embed(self, script):
        return [1.0, 0.0]


def test_tools_enforce_approval_and_return_useful_feedback():
    profile = IndexProfile(model_id="test", model_revision="test", dimension=2, index_version="test")
    client = QdrantClient(location=":memory:")
    try:
        index = QdrantIndex(client, "test", profile)
        index.initialize()
        adapter = ToolAdapter(SimilarityService(Embedding(), index, profile))
        request = {"script": "测试稿", "job_id": str(uuid4()), "content_version_id": str(uuid4())}
        invalid = adapter.call("store", request)
        assert not invalid["success"] and invalid["data"] is None
        assert client.count("test").count == 0
        approval = {"approval_id": str(uuid4()), "job_id": request["job_id"],
                    "content_version_id": request["content_version_id"],
                    "script_sha256": hashlib.sha256("测试稿".encode()).hexdigest(),
                    "checks": {"keywords": True, "content_audit": True, "similarity": True}}
        assert adapter.call("store", request | {"approval": approval})["data"]["saved"]
        other = request | {"job_id": str(uuid4()), "content_version_id": str(uuid4())}
        result = adapter.call("detect", other)
        assert result["success"] and result["data"]["passed"] is False
        assert result["data"]["matches"][0]["script_excerpt"] == "测试稿"
        assert adapter.call("reconcile", request)["data"]["saved"]
        index.client.delete_collection("test")
        failure = adapter.call("detect", other)
        assert failure["success"] is False
        assert failure["error"]["code"] == "INDEX_NOT_READY"
    finally:
        client.close()


def test_mcp_tools_registration_and_call_contract():
    async def scenario():
        profile = IndexProfile(model_id="test", model_revision="test", dimension=2, index_version="test")
        client = QdrantClient(location=":memory:")
        try:
            index = QdrantIndex(client, "test", profile)
            index.initialize()
            server = create_server(ToolAdapter(SimilarityService(Embedding(), index, profile)))
            names = {tool.name for tool in await server.list_tools()}
            assert {"embed", "vector-search", "vector-insert", "detect", "reconcile"} <= names
            response = await server.call_tool("vector-insert", {"request": {
                "script": "未审核稿", "job_id": str(uuid4()), "content_version_id": str(uuid4()),
            }})
            structured = json.loads(response[0].text)
            assert structured["success"] is False
            assert client.count("test").count == 0
            found = await server.call_tool("vector-search", {
                "vector": [1.0, 0.0], "content_version_id": str(uuid4()),
                "profile": profile.model_dump(),
            })
            assert json.loads(found[0].text)["data"]["matches"] == []
            mismatch = await server.call_tool("vector-search", {
                "vector": [1.0, 0.0], "content_version_id": str(uuid4()),
                "profile": profile.model_dump() | {"model_revision": "different"},
            })
            assert json.loads(mismatch[0].text)["error"]["code"] == "INDEX_INCOMPATIBLE"
        finally:
            client.close()
    asyncio.run(scenario())
