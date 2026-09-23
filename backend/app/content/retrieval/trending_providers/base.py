"""统一 HTTP 超时、体积限制和外部响应检查。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

import httpx

from ..trending import ProviderResult


class InvalidPayload(ValueError):
    """上游没有遵守已验证的数据结构（包括验证码、空响应）。"""


class HttpProvider(ABC):
    platform: str
    source_id: str
    source_version = "1"
    default_url: str

    def __init__(self, client: httpx.AsyncClient, url: str | None = None):
        self.client = client
        self.url = url or self.default_url

    @abstractmethod
    def parse(self, body: bytes) -> list[dict[str, Any]]:
        """解析为 title/source_url/hot_score，维持源榜单顺序。"""

    async def fetch(self, limit: int, timeout: float) -> ProviderResult:
        try:
            async with self.client.stream("GET", self.url, timeout=timeout) as response:
                response.raise_for_status()
                chunks = bytearray()
                async for chunk in response.aiter_bytes():
                    chunks.extend(chunk)
                    if len(chunks) > 3_000_000:
                        raise InvalidPayload("response_too_large")
            items = self.parse(bytes(chunks))
            return ProviderResult(self.platform, items=items[:limit])
        except httpx.TimeoutException:
            code = "timeout"
        except httpx.HTTPStatusError as exc:
            code = {401: "auth_required", 403: "access_denied", 429: "rate_limited"}.get(
                exc.response.status_code, "http_error"
            )
        except httpx.RequestError:
            code = "network_error"
        except (ValueError, KeyError, TypeError, UnicodeError):
            code = "invalid_payload"
        return ProviderResult(self.platform, status="failed", reason_code=code)


def rows_to_items(rows: Any, title: str, score: str, url: str | None) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise InvalidPayload("Expected list")
    return [
        {
            "title": row.get(title),
            "hot_score": row.get(score),
            "source_url": row.get(url) if url else None,
        }
        if isinstance(row, dict)
        else {}
        for row in rows
    ]
