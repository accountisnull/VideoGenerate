"""内容模块内部契约；不改变现有 HTTP v1 模型。"""

import hashlib
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator

from app.contracts.common import Sha256, Text


def script_digest(script: str) -> str:
    return hashlib.sha256(script.encode("utf-8")).hexdigest()


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, allow_inf_nan=False)


class IndexProfile(Model):
    model_id: Text
    model_revision: Text
    dimension: int = Field(strict=True, gt=0)
    index_version: Text
    pooling: Literal["CLS"] = "CLS"
    normalization: Literal["L2"] = "L2"
    distance: Literal["Cosine"] = "Cosine"
    preprocessing: Literal["raw-no-instruction-v1"] = "raw-no-instruction-v1"


class DetectRequest(Model):
    script: str = Field(strict=True, min_length=1)
    job_id: UUID
    content_version_id: UUID

    @field_validator("script")
    @classmethod
    def nonempty_script(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("口播稿不能为空")
        return value


class Approval(Model):
    """可信编排方提供的审核快照；不作为公网鉴权凭据。"""

    approval_id: UUID
    job_id: UUID
    content_version_id: UUID
    script_sha256: Sha256
    checks: dict[str, StrictBool]


class StoreRequest(DetectRequest):
    approval: Approval


class StoredRecord(DetectRequest):
    script_sha256: Sha256
    profile: IndexProfile
    approval: Approval


class Match(Model):
    content_version_id: UUID
    job_id: UUID
    score: float = Field(ge=-1.000001, le=1.000001)
    script_excerpt: str


class DetectionResult(Model):
    passed: bool
    reason: Literal["NO_HISTORY_MATCH", "BELOW_THRESHOLD", "SIMILARITY_BLOCKED"]
    max_similarity: float | None
    threshold: float = 0.85
    matches: list[Match]
    profile: IndexProfile


class StoreResult(Model):
    saved: bool
    point_id: UUID
    content_version_id: UUID
    index_version: str


class ReconcileResult(StoreResult):
    pass
