"""真实成员接口、主管接线、配置与 MCP SDK；来源及模型用替身隔离。"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from mcp import ClientSession
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session

from app.content.agents import ContentAgents, assemble_agents
from app.content.models import (
    Draft,
    Material,
    RetrievalResult,
    ReviewDecision,
    RunContext,
)
from app.content.retrieval.agent import TrendingAgent
from app.content.retrieval.config import TrendingSettings
from app.content.retrieval.mcp_tools import register_trending_tools
from app.content.retrieval.schema import SCHEMA_PATH
from app.content.retrieval.service import TrendingService

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def context():
    return RunContext(task_id=uuid4(), job_id=uuid4(), topic="用户原始主题")


def source(request):
    if request.url.host == "uapis.cn":
        return httpx.Response(200, json={"type": "weibo", "list": [
            {"title": "合成热点", "hot_value": "120", "url": "https://example.com/item"},
        ]})
    if request.url.host == "top.baidu.com":
        return httpx.Response(200, text='<!--s-data:{"data":{"cards":['
                             '{"component":"hotList","content":[]}]}}-->')
    return httpx.Response(403)


def agent(handler=source):
    settings = TrendingSettings()
    return TrendingAgent(TrendingService(settings, transport=httpx.MockTransport(handler)), settings)


async def test_actual_agent_preserves_metadata_and_partial_failures():
    member = agent()
    result = await member.retrieve(context())
    assert len(member.skill_sha256) == 64
    assert "name: trending-fetch" in member.skill_text
    item = result.materials[0]
    assert item.platform == "weibo" and item.hot_score == 120
    assert item.url == "https://example.com/item" and item.fetched_at.tzinfo
    assert [s.status for s in result.source_statuses] == ["ok", "empty", "failed"]
    assert result.source_statuses[2].reason_code == "access_denied"
    assert result.failures[0].capability == "trending:douyin"
    assert result.failures[0].code == "UPSTREAM_FAILED"
    assert RetrievalResult.model_validate_json(result.model_dump_json()) == result


@pytest.mark.parametrize("all_failed", [False, True])
async def test_real_orchestrator_consumes_agent_and_never_replaces_topic(all_failed):
    calls = []

    class Generator:
        async def generate(self, request):
            calls.append(request)
            return Draft(script="合成测试稿", title="测试标题", tags=())

    class Reviewer:
        async def review(self, request):
            return ReviewDecision(passed=True, issues=())

    member = agent(lambda req: httpx.Response(503)) if all_failed else agent()
    engine = assemble_agents(ContentAgents(
        generation=Generator(), audit=Reviewer(), keywords=Reviewer(), trending=member,
    ), call_timeout_seconds=1)
    ctx = context()
    outcome = await engine.run(ctx)
    assert outcome.result is not None
    assert calls[0].context == ctx
    assert len(calls[0].materials) == (0 if all_failed else 1)
    assert len(outcome.retrievals[0].output.source_statuses) == 3
    assert len(outcome.retrievals[0].output.failures) == (3 if all_failed else 1)


async def test_cancellation_closes_all_provider_requests():
    entered, active = asyncio.Event(), set()

    async def pending(request):
        active.add(request.url.host)
        if len(active) == 3:
            entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            active.remove(request.url.host)

    task = asyncio.create_task(agent(pending).retrieve(context()))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not active


@pytest.mark.parametrize("key,value", [
    ("CONTENT_TRENDING_ENABLED", "maybe"), ("TRENDING_LIMIT", "0"),
    ("TRENDING_LIMIT", "51"), ("TRENDING_TIMEOUT_SECONDS", "nan"),
    ("TRENDING_PLATFORMS", ""), ("TRENDING_PLATFORMS", "unknown"),
    ("TRENDING_WEIBO_URL", "http://example.com"),
    ("TRENDING_WEIBO_URL", "https://user:password@example.com"),
])
def test_invalid_config_rejected(key, value):
    with pytest.raises(ValueError):
        TrendingSettings.from_values({key: value})


def test_config_uses_existing_entrypoint_and_old_materials_remain_valid(monkeypatch):
    from app.content import settings
    monkeypatch.setattr(settings, "environment_values", lambda: {
        "CONTENT_TRENDING_ENABLED": "true", "TRENDING_PLATFORMS": "douyin,weibo,douyin",
        "TRENDING_LIMIT": "3",
    })
    config = settings.load_settings()
    assert config.trending.enabled and config.trending.limit == 3
    assert config.trending.platforms == ("douyin", "weibo")
    old = RetrievalResult(materials=(Material(title="旧搜索结果", source="search", url=None),))
    assert old.source_statuses == () and old.materials[0].platform is None


async def test_mcp_protocol_initialize_list_call_structured_content():
    server = FastMCP("search-mcp-test")
    register_trending_tools(server, agent().service)
    # SDK 内存传输执行完整 MCP 协议；生产 HTTP 服务生命周期仍由人员 2 提供。
    async with create_connected_server_and_client_session(server._mcp_server) as session:
        assert isinstance(session, ClientSession)
        listing = await session.list_tools()
        assert {t.name for t in listing.tools} == {
            "trending_fetch", "fetch-weibo-trending", "fetch-baidu-trending", "fetch-douyin-trending",
        }
        args = {"context": context().model_dump(mode="json"), "call_id": str(uuid4()), "limit": 3}
        result = await session.call_tool("trending_fetch", args)
        assert not result.isError
        assert result.structuredContent["schema_version"] == "1.0"
        data = RetrievalResult.model_validate(result.structuredContent["data"])
        assert len(data.materials) == 1 and data.failures
        for p in ("weibo", "baidu", "douyin"):
            result = await session.call_tool(f"fetch-{p}-trending", args)
            parsed = RetrievalResult.model_validate(result.structuredContent["data"])
            assert len(parsed.source_statuses) == 1
            assert parsed.source_statuses[0].platform == p
        invalid = await session.call_tool("trending_fetch", {**args, "limit": 51})
        assert invalid.isError
        invalid = await session.call_tool("trending_fetch", {"limit": 3})
        assert invalid.isError


async def test_generated_schema_matches_and_validates_actual_result():
    from jsonschema import Draft202012Validator, FormatChecker
    schema = json.loads(SCHEMA_PATH.read_text("utf-8"))
    expected = RetrievalResult.model_json_schema(mode="validation")
    expected["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    assert schema == expected
    result = await agent().retrieve(context())
    Draft202012Validator(schema, format_checker=FormatChecker()).validate(result.model_dump(mode="json"))


def test_bootstrap_enables_trending_without_overwriting_injected_member(monkeypatch, tmp_path):
    from app.content import bootstrap
    from app.content.settings import ContentSettings
    root = Path(__file__).resolve().parents[3]
    settings = ContentSettings(
        database=tmp_path / "test.db", asset_root=tmp_path,
        provider="bailian", rules_path=root / "config/rules/content.example.json",
        trending=TrendingSettings(enabled=True),
    )
    monkeypatch.setattr(bootstrap.runtime, "text_model_configuration", lambda step: {
        "base_url": "https://example.com/v1", "model": "test", "api_key": "test-key",
        "timeout_seconds": 60, "api_key_env": "TEST_KEY",
    })
    member = agent()
    monkeypatch.setattr(bootstrap, "create_trending_agent", lambda config: member)
    engine = bootstrap.build_orchestrator(settings)
    assert engine.retrievers["trending"] is member
    other = agent()
    engine = bootstrap.build_orchestrator(settings, retrievers={"trending": other})
    assert engine.retrievers["trending"] is other
