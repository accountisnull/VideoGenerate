"""对接现有 Retriever 协议；原始主题只在 RunContext 中传递。"""

import hashlib
from pathlib import Path
from typing import Protocol

from app.content.models import (
    CapabilityFailure,
    Material,
    RetrievalResult,
    RetrievalSourceStatus,
    RunContext,
)

from .config import TrendingSettings
from .service import TrendingService

SKILL_PATH = Path(__file__).resolve().parents[3] / "skills/trending-fetch/SKILL.md"


class TrendingBackend(Protocol):
    async def fetch(self, platforms: list[str] | tuple[str, ...], limit: int) -> dict: ...


class TrendingAgent:
    def __init__(self, service: TrendingBackend, settings: TrendingSettings) -> None:
        self.service = service
        self.settings = settings
        # 确定性 Agent 无须模型；载入声明供宿主追踪版本，执行规则由测试验证。
        self.skill_text = SKILL_PATH.read_text(encoding="utf-8")
        if "name: trending-fetch" not in self.skill_text:
            raise ValueError("热点 Skill 声明缺失")
        self.skill_sha256 = hashlib.sha256(self.skill_text.encode("utf-8")).hexdigest()

    async def retrieve(self, context: RunContext) -> RetrievalResult:
        RunContext.model_validate(context)
        response = await self.service.fetch(self.settings.platforms, self.settings.limit)
        return self.to_retrieval(response)

    @staticmethod
    def to_retrieval(response: dict) -> RetrievalResult:
        statuses = tuple(RetrievalSourceStatus.model_validate(s) for s in response["platforms"])
        failure_codes = {"timeout": "CALL_TIMEOUT", "invalid_payload": "INVALID_OUTPUT",
                         "invalid_items_dropped": "INVALID_OUTPUT", "provider_error": "INTERNAL_ERROR"}
        return RetrievalResult(
            materials=tuple(Material(
                title=item["title"], source=item["platform"], url=item["source_url"],
                platform=item["platform"], hot_score=item["hot_score"], fetched_at=item["fetched_at"],
            ) for item in response["data"]),
            failures=tuple(CapabilityFailure(
                capability=f"trending:{s.platform}",
                code=failure_codes.get(s.reason_code, "UPSTREAM_FAILED"),
            ) for s in statuses if s.status in {"failed", "degraded"}),
            source_statuses=statuses,
        )


def create_trending_agent(settings: TrendingSettings | None = None) -> TrendingAgent:
    config = settings if settings is not None else TrendingSettings.load()
    return TrendingAgent(TrendingService(config), config)
