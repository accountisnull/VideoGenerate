"""热点配置通过项目统一 environment_values 读取，不在导入时访问环境。"""

import math
from typing import Annotated, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app import runtime

Platform = Literal["weibo", "baidu", "douyin"]


class TrendingSettings(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    enabled: bool = False
    platforms: tuple[Platform, ...] = ("weibo", "baidu", "douyin")
    limit: Annotated[int, Field(strict=True, ge=1, le=50)] = 20
    timeout_seconds: float = 8
    weibo_url: str = "https://uapis.cn/api/v1/misc/hotboard?type=weibo"
    baidu_url: str = "https://top.baidu.com/board?tab=realtime"
    douyin_url: str = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"

    @field_validator("platforms")
    @classmethod
    def validate_platforms(cls, values: tuple[Platform, ...]) -> tuple[Platform, ...]:
        if not values:
            raise ValueError("热点平台不能为空")
        return tuple(dict.fromkeys(values))

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0:
            raise ValueError("热点超时必须是有限正数")
        return value

    @field_validator("weibo_url", "baidu_url", "douyin_url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        parts = urlsplit(value)
        if (parts.scheme != "https" or not parts.hostname or parts.username
                or parts.password or parts.fragment or any(c.isspace() for c in value)):
            raise ValueError("热点来源必须是无内嵌凭据的 HTTPS 地址")
        return value

    @classmethod
    def from_values(cls, values: dict) -> "TrendingSettings":
        enabled = values.get("CONTENT_TRENDING_ENABLED", "false")
        if not isinstance(enabled, str) or enabled.lower() not in {"true", "false"}:
            raise ValueError("CONTENT_TRENDING_ENABLED 必须为 true 或 false")
        raw = values.get("TRENDING_PLATFORMS", "weibo,baidu,douyin")
        if not isinstance(raw, str):
            raise TypeError("TRENDING_PLATFORMS 必须是平台列表")
        overrides = {
            f"{p}_url": values[f"TRENDING_{p.upper()}_URL"]
            for p in ("weibo", "baidu", "douyin") if f"TRENDING_{p.upper()}_URL" in values
        }
        return cls(
            enabled=enabled.lower() == "true",
            platforms=tuple(p.strip() for p in raw.split(",")),
            limit=int(values.get("TRENDING_LIMIT", "20")),
            timeout_seconds=values.get("TRENDING_TIMEOUT_SECONDS", "8"),
            **overrides,
        )

    @classmethod
    def load(cls) -> "TrendingSettings":
        return cls.from_values(runtime.environment_values())
