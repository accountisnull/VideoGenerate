"""多平台热点素材聚合。

这个模块只负责把供应商返回的数据转换成统一的素材结构并聚合。
供应商的网络调用放在 ``trending_providers`` 中，因而更换供应商不会影响编排层。
"""

from __future__ import annotations

import asyncio
import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol
from urllib.parse import urlsplit

SUPPORTED_PLATFORMS = ("weibo", "baidu", "douyin")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class TrendingItem:
    """交给内容生成层的统一条目。``None`` 表示供应商没有提供字段。"""

    title: str
    platform: str
    source_url: str | None
    hot_score: int | float | None
    fetched_at: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ProviderResult:
    platform: str
    items: list[Mapping[str, Any]] = field(default_factory=list)
    status: str = "ok"
    reason_code: str | None = None
    error: str | None = None
    fetched_at: str = field(default_factory=utc_now)


class TrendingProvider(Protocol):
    platform: str

    async def fetch(self, limit: int, timeout: float) -> ProviderResult:
        """按平台内热度顺序返回原始条目。"""


def _first(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        value = mapping.get(key)
        if value is not None and value != "":
            return value
    return None


def normalize_item(raw: Mapping[str, Any], platform: str, fetched_at: str) -> TrendingItem | None:
    """校验并规范化一个外部条目；缺少标题的条目不可用于素材，直接丢弃。"""

    title_value = _first(raw, "title", "word", "keyword", "query", "name")
    if not isinstance(title_value, str) or not title_value.strip():
        return None
    score = _first(raw, "hot_score", "hotScore", "hot", "score", "num", "heat")
    url = _first(raw, "source_url", "sourceUrl", "url", "link")
    # 数字字符串可用，未知单位、布尔值、非有限数和负值一律明确为空。
    if isinstance(score, str):
        try:
            score = float(score)
        except ValueError:
            score = None
    try:
        valid_score = (
            not isinstance(score, bool)
            and isinstance(score, (int, float))
            and math.isfinite(score)
            and score >= 0
        )
    except OverflowError:
        valid_score = False
    if not valid_score:
        score = None
    if isinstance(score, float) and score.is_integer():
        score = int(score)
    # 只接受来源实际给出的完整 HTTP(S) 链接，不修补、不合成。
    try:
        parsed = urlsplit(url.strip()) if isinstance(url, str) else None
        valid_url = bool(
            parsed
            and parsed.scheme in {"http", "https"}
            and parsed.hostname
            and not parsed.username
            and not parsed.password
        )
    except ValueError:
        valid_url = False
    return TrendingItem(
        title=title_value.strip(),
        platform=platform,
        source_url=url.strip() if valid_url else None,
        hot_score=score,
        fetched_at=fetched_at,
    )


def _deduplicate(items: Sequence[TrendingItem]) -> list[TrendingItem]:
    """仅按首尾空白清理后的标题精确去重，保留合并顺序中的第一条。"""

    seen: set[str] = set()
    result: list[TrendingItem] = []
    for item in items:
        key = item.title.strip()
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _round_robin(platform_items: Mapping[str, Sequence[TrendingItem]]) -> list[TrendingItem]:
    """按请求的平台顺序轮转，避免把不同平台的热度当成同一尺度排序。"""

    result: list[TrendingItem] = []
    max_len = max((len(items) for items in platform_items.values()), default=0)
    platforms = list(platform_items)
    for index in range(max_len):
        for platform in platforms:
            items = platform_items[platform]
            if index < len(items):
                result.append(items[index])
    return result


class TrendingAggregator:
    """并行获取、规范化、轮转合并和精确去重。"""

    def __init__(self, providers: Mapping[str, TrendingProvider], default_timeout: float = 8.0):
        self.providers = dict(providers)
        self.default_timeout = default_timeout

    async def fetch(
        self,
        platforms: Sequence[str],
        limit: int,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 50:
            raise ValueError("limit 必须是 1～50 的整数")
        if (
            not isinstance(platforms, (list, tuple))
            or not platforms
            or any(not isinstance(p, str) or p not in SUPPORTED_PLATFORMS for p in platforms)
        ):
            raise ValueError("platforms 必须是由 weibo、baidu、douyin 组成的非空列表")
        requested = list(dict.fromkeys(platforms))

        timeout_value = self.default_timeout if timeout is None else timeout
        if (
            isinstance(timeout_value, bool)
            or not isinstance(timeout_value, (int, float))
            or not math.isfinite(timeout_value)
            or timeout_value <= 0
        ):
            raise ValueError("timeout 必须大于 0")

        async def one(platform: str) -> ProviderResult:
            provider = self.providers.get(platform)
            if provider is None:
                return ProviderResult(
                    platform, status="failed", reason_code="provider_not_configured"
                )
            try:
                # 多抓一些以便去重后补位；最终输出仍严格遵守 limit。
                result = await asyncio.wait_for(
                    provider.fetch(max(50, limit), timeout_value), timeout_value
                )
                if (
                    not isinstance(result, ProviderResult)
                    or result.platform != platform
                    or not isinstance(result.items, list)
                    or result.status not in {"ok", "failed"}
                ):
                    return ProviderResult(platform, status="failed", reason_code="invalid_payload")
            except TimeoutError:
                return ProviderResult(platform, status="failed", reason_code="timeout")
            # 平台隔离边界必须兜住未知适配器错误；取消仍传播，异常原文不外泄。
            except Exception:  # noqa: BLE001
                return ProviderResult(platform, status="failed", reason_code="provider_error")
            return result

        provider_results = await asyncio.gather(*(one(platform) for platform in requested))
        grouped: dict[str, list[TrendingItem]] = {}
        statuses: list[dict[str, Any]] = []
        for result in provider_results:
            fetched_at = result.fetched_at
            normalized: list[TrendingItem] = []
            invalid_count = 0
            for raw in result.items if result.status == "ok" else []:
                if not isinstance(raw, Mapping):
                    invalid_count += 1
                    continue
                item = normalize_item(raw, result.platform, fetched_at)
                if item is None:
                    invalid_count += 1
                else:
                    normalized.append(item)
            if invalid_count and result.status == "ok":
                result.status = "degraded" if normalized else "failed"
                result.reason_code = "invalid_items_dropped" if normalized else "invalid_payload"
            elif result.status == "ok" and not normalized:
                result.status = "empty"
            grouped[result.platform] = normalized
            provider = self.providers.get(result.platform)
            statuses.append(
                {
                    "platform": result.platform,
                    "status": result.status,
                    "reason_code": result.reason_code,
                    "error": result.error,
                    "item_count": len(normalized),
                    "invalid_count": invalid_count,
                    "fetched_at": fetched_at,
                    "source_id": getattr(provider, "source_id", None),
                    "source_version": getattr(provider, "source_version", None),
                }
            )

        data = [item.to_dict() for item in _deduplicate(_round_robin(grouped))[:limit]]
        successful = [
            status for status in statuses if status["status"] in {"ok", "empty", "degraded"}
        ]
        if not successful:
            return {
                "success": False,
                "data": [],
                "platforms": statuses,
                "error": "所有热点来源均不可用",
                "reason_code": "all_platforms_failed",
                "degraded": True,
            }
        return {
            "success": True,
            "data": data,
            "platforms": statuses,
            "degraded": any(status["status"] in {"failed", "degraded"} for status in statuses),
            "error": None,
            "reason_code": None,
        }


async def fetch_trending(
    platforms: Sequence[str],
    limit: int,
    providers: Mapping[str, TrendingProvider],
    *,
    timeout: float = 8.0,
) -> dict[str, Any]:
    """稳定的业务入口，供编排层或 MCP 适配层调用。"""

    return await TrendingAggregator(providers, timeout).fetch(platforms, limit)
