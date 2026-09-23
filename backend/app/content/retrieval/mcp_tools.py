"""向人员 2 的 search-mcp 注册热点工具，遵守团队 v1 结构化协议。"""
from typing import Annotated, Any, Protocol
from uuid import UUID

from pydantic import Field

from app.content.models import RunContext

from .agent import TrendingAgent
from .config import TrendingSettings
from .service import TrendingService

Limit = Annotated[int, Field(strict=True, ge=1, le=50)]

class ToolRegistry(Protocol):
    def tool(self, *, name: str) -> Any: ...

def register_trending_tools(server: ToolRegistry, service: TrendingService | None = None) -> None:
    async def call(context: RunContext, call_id: UUID, limit: int, platforms: list[str]) -> dict[str, Any]:
        RunContext.model_validate(context)
        backend = service if service is not None else TrendingService(TrendingSettings.load())
        response = await backend.fetch(platforms, limit)
        return {"schema_version": "1.0", "data": TrendingAgent.to_retrieval(response).model_dump(mode="json")}

    @server.tool(name="trending_fetch")
    async def trending_fetch(context: RunContext, call_id: UUID, limit: Limit = 20) -> dict[str, Any]:
        config = service.settings if service is not None else TrendingSettings.load()
        return await call(context, call_id, limit, list(config.platforms))

    @server.tool(name="fetch-weibo-trending")
    async def fetch_weibo_trending(context: RunContext, call_id: UUID, limit: Limit = 50) -> dict[str, Any]:
        return await call(context, call_id, limit, ["weibo"])

    @server.tool(name="fetch-baidu-trending")
    async def fetch_baidu_trending(context: RunContext, call_id: UUID, limit: Limit = 50) -> dict[str, Any]:
        return await call(context, call_id, limit, ["baidu"])

    @server.tool(name="fetch-douyin-trending")
    async def fetch_douyin_trending(context: RunContext, call_id: UUID, limit: Limit = 50) -> dict[str, Any]:
        return await call(context, call_id, limit, ["douyin"])
