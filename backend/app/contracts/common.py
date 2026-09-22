"""HTTP v1 公共字段、错误与模块作业响应。"""

import math
from datetime import datetime, timedelta
from typing import Annotated, Generic, Literal, TypeVar
from uuid import UUID, uuid4

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    RootModel,
    StrictBool,
    StringConstraints,
    model_validator,
)

Text = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
Topic = Annotated[Text, Field(max_length=2000)]
PositiveInt = Annotated[int, Field(strict=True, gt=0)]
NonNegativeInt = Annotated[int, Field(strict=True, ge=0)]
Sha256 = Annotated[str, StringConstraints(strict=True, pattern=r"^[0-9a-f]{64}$")]
FrameRate = Annotated[str, StringConstraints(strict=True, pattern=r"^[1-9][0-9]*/[1-9][0-9]*$")]
RelativePath = Annotated[
    str,
    StringConstraints(
        strict=True,
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[^/\\:\x00-\x1f]+(/[^/\\:\x00-\x1f]+)*$",
    ),
]


def validate_path(value: str) -> str:
    if any(part in (".", "..") for part in value.split("/")):
        raise ValueError("资产路径不能包含 . 或 .. 路径段")
    return value


AssetPath = Annotated[RelativePath, AfterValidator(validate_path)]


def validate_timestamp_input(value):
    if not isinstance(value, (str, datetime)):
        # Pydantic 需要 ValueError 才能将输入类型问题汇总为 ValidationError。
        raise ValueError("时间必须为 UTC RFC 3339 字符串或带时区的 datetime")  # noqa: TRY004
    if isinstance(value, str) and ("T" not in value or not value.endswith(("Z", "+00:00"))):
        raise ValueError("时间必须使用 UTC RFC 3339 格式")
    return value


def validate_utc(value: datetime) -> datetime:
    if value.utcoffset() != timedelta(0):
        raise ValueError("时间必须包含 UTC 时区")
    return value


UtcTime = Annotated[
    datetime, BeforeValidator(validate_timestamp_input), AfterValidator(validate_utc),
]


def validate_finite_json(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("JSON 不允许 NaN 或 Infinity")
    if isinstance(value, dict):
        for item in value.values():
            validate_finite_json(item)
    elif isinstance(value, list):
        for item in value:
            validate_finite_json(item)
    return value


JsonObject = Annotated[dict[str, JsonValue], AfterValidator(validate_finite_json)]


class ResponseModel(BaseModel):
    """客户端忽略新增响应字段，保持向前兼容。"""

    model_config = ConfigDict(extra="ignore", allow_inf_nan=False)


class RequestModel(ResponseModel):
    """请求拒绝未知字段，避免拼写错误静默生效。"""

    model_config = ConfigDict(extra="forbid")


class CreateHeaders(RequestModel):
    """由 HTTP 层按大小写无关的名称提取这两个请求头后校验。"""

    idempotency_key: UUID = Field(alias="Idempotency-Key")
    request_id: UUID = Field(alias="X-Request-ID", default_factory=uuid4)


class AssetRef(ResponseModel):
    path: AssetPath
    sha256: Sha256
    size_bytes: PositiveInt
    media_type: Text


class RequestAssetRef(AssetRef):
    model_config = ConfigDict(extra="forbid")


class Error(ResponseModel):
    code: Annotated[Text, Field(pattern=r"^[A-Z][A-Z0-9_]*$")]
    message: Text
    retryable: StrictBool
    details: JsonObject


class ErrorResponse(ResponseModel):
    request_id: UUID
    error: Error


class JobBase(ResponseModel):
    job_id: UUID
    task_id: UUID
    created_at: UtcTime
    updated_at: UtcTime

    @model_validator(mode="after")
    def chronological(self):
        if self.updated_at < self.created_at:
            raise ValueError("更新时间不能早于创建时间")
        return self


class PendingJob(JobBase):
    state: Literal["queued", "running"]
    result: None
    error: None


Result = TypeVar("Result", bound=ResponseModel)


class SucceededJob(JobBase, Generic[Result]):
    state: Literal["succeeded"]
    result: Result
    error: None


class FailedJob(JobBase):
    state: Literal["failed"]
    result: None
    error: Error


class JobResponse(RootModel[
    Annotated[PendingJob | SucceededJob[Result] | FailedJob, Field(discriminator="state")]
], Generic[Result]):
    """直接序列化为作业对象，不增加 root 包装字段。"""


class HealthResponse(ResponseModel):
    service: Literal["content", "speech", "video"]
    api_version: Literal["1.0"]
    ready: Literal[True]
