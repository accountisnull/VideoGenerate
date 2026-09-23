"""内容服务配置入口；模型凭据仍由既有模型适配读取。"""

import re
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from app.runtime import ROOT, environment_values

from .retrieval.config import TrendingSettings


class ContentSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    database: Path
    asset_root: Path
    token: SecretStr | None = None
    provider: Literal["disabled", "bailian"] = "disabled"
    rules_path: Path | None = None
    engine: Literal["http", "deepagents"] = "http"
    similarity_enabled: bool = False
    similarity_python: Path | None = None
    similarity_timeout_seconds: float = 150
    trending: TrendingSettings = Field(default_factory=TrendingSettings)

    @field_validator("similarity_timeout_seconds")
    @classmethod
    def positive_timeout(cls, value: float) -> float:
        import math
        if not math.isfinite(value) or value <= 0:
            raise ValueError("同质化调用预算必须为有限正数")
        return value

    @field_validator("token")
    @classmethod
    def token_format(cls, value: SecretStr | None) -> SecretStr | None:
        if value is not None and not re.fullmatch(
            r"[A-Za-z0-9._~+/-]+=*", value.get_secret_value()
        ):
            raise ValueError("服务令牌格式无效")
        return value


def load_settings() -> ContentSettings:
    values = environment_values()

    def path(name: str, default: str) -> Path:
        raw = values.get(name, default)
        if not isinstance(raw, str) or not raw.strip():
            raise ValueError(f"{name} 不能为空")
        value = Path(raw)
        return (value if value.is_absolute() else ROOT / value).resolve()

    token = values.get("CONTENT_SERVICE_TOKEN", "")
    enabled = values.get("CONTENT_SIMILARITY_ENABLED", "false").lower()
    if enabled not in ("true", "false"):
        raise ValueError("CONTENT_SIMILARITY_ENABLED 必须为 true 或 false")
    return ContentSettings(
        database=path("CONTENT_DATABASE_PATH", "data/content/content.db"),
        asset_root=path("CONTENT_ASSET_ROOT", "data/assets"),
        token=token or None,
        provider=values.get("CONTENT_PROVIDER", "disabled"),
        trending=TrendingSettings.from_values(values),
        engine=values.get("CONTENT_ENGINE", "http"),
        similarity_enabled=enabled == "true",
        similarity_python=path("CONTENT_SIMILARITY_PYTHON", "backend/.venv/Scripts/python.exe"),
        similarity_timeout_seconds=values.get("CONTENT_SIMILARITY_TIMEOUT_SECONDS", "150"),
        rules_path=path("CONTENT_RULES_PATH", "")
        if values.get("CONTENT_RULES_PATH")
        else None,
    )
