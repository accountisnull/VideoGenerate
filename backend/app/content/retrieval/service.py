"""配置注入的异步热点服务；每次调用独立释放 HTTP 客户端。"""
from collections.abc import Sequence
from typing import Any

import httpx

from .config import TrendingSettings
from .trending import fetch_trending
from .trending_providers import create_providers


class TrendingService:
    def __init__(self, settings: TrendingSettings, *, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.settings = settings
        self.transport = transport

    async def fetch(self, platforms: Sequence[str], limit: int) -> dict[str, Any]:
        urls = {p: getattr(self.settings, f"{p}_url") for p in ("weibo", "baidu", "douyin")}
        async with httpx.AsyncClient(
            headers={"User-Agent": "Mozilla/5.0 (compatible; TrendingRetrieval/0.1)"},
            transport=self.transport, follow_redirects=False,
        ) as client:
            return await fetch_trending(platforms, limit, create_providers(client, urls), timeout=self.settings.timeout_seconds)

async def get_trending(platforms: Sequence[str] = ("weibo", "baidu", "douyin"), limit: int = 20) -> dict[str, Any]:
    return await TrendingService(TrendingSettings.load()).fetch(platforms, limit)
