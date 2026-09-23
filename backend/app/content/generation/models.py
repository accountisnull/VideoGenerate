"""人员 5 的内部契约；不包含 review_passed 或公共作业状态。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    computed_field,
    field_validator,
    model_validator,
)

NonEmpty = Annotated[str, Field(min_length=1)]
Step = Literal["writing", "revision", "review"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)


class Material(Contract):
    id: Annotated[str, Field(min_length=1, max_length=100)]
    title: Annotated[str, Field(min_length=1, max_length=500)]
    content: Annotated[str, Field(min_length=1, max_length=8000)]
    source_url: Annotated[str, Field(min_length=1, max_length=2000)] | None = None


class ReviewIssue(Contract):
    type: Annotated[str, Field(min_length=1, max_length=100)]
    detail: Annotated[str, Field(min_length=1, max_length=2000)]


class Draft(Contract):
    # 原稿允许超出本期长度，才能接收“将旧稿缩短”的修订请求。
    script: Annotated[str, Field(min_length=1, max_length=20000)]
    title: Annotated[str, Field(min_length=1, max_length=200)]
    tags: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=20)
    material_ids: list[Annotated[str, Field(min_length=1, max_length=100)]] = Field(max_length=20)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values):
        tags = [value.replace("#", "").replace("＃", "").strip() for value in values]
        if any(not tag for tag in tags):
            raise ValueError("标签去除井号后不能为空")
        return list(dict.fromkeys(tags))

    @field_validator("material_ids")
    @classmethod
    def unique_material_ids(cls, values):
        return list(dict.fromkeys(values))


class SourceVersion(Contract):
    version_id: UUID
    draft: Draft


class GenerationRequest(Contract):
    topic: Annotated[str, Field(min_length=1, max_length=1000)]
    source_script: Annotated[str, Field(min_length=1, max_length=20000)] | None = None
    materials: list[Material] = Field(default_factory=list, max_length=20)

    @field_validator("materials")
    @classmethod
    def unique_materials(cls, values):
        if len({item.id for item in values}) != len(values):
            raise ValueError("素材 id 不能重复")
        return values


class RevisionRequest(GenerationRequest):
    previous: SourceVersion
    review_issues: list[ReviewIssue] = Field(default_factory=list, max_length=30)
    revision_request: Annotated[str, Field(min_length=1, max_length=4000)] | None = None

    @model_validator(mode="after")
    def require_feedback_and_sources(self):
        if not self.review_issues and not self.revision_request:
            raise ValueError("修订必须提供审核问题或明确修改意见")
        available = {item.id for item in self.materials}
        if not set(self.previous.draft.material_ids).issubset(available):
            raise ValueError("修订时必须提供原稿引用的素材，不能将旧引用视为已核实来源")
        return self


class ModelCallRecord(Contract):
    step: Step
    model: NonEmpty
    started_at: datetime
    elapsed_ms: int = Field(ge=0)
    call_count: int = Field(ge=0, le=1)
    status: Literal["succeeded", "failed", "timeout"]


class GenerationResult(Contract):
    version_id: UUID
    source_version_id: UUID | None
    draft: Draft
    # 修订交接包含前后快照；编排层持久化此结果即可保留版本证据。
    previous: SourceVersion | None
    model_call: ModelCallRecord

    @computed_field
    @property
    def char_count(self) -> int:
        return len("".join(self.draft.script.split()))
