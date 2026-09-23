"""内容成员间传递的不可变输入、审核结果与运行记录。"""

from typing import Annotated, Literal
from uuid import UUID

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    field_validator,
    model_validator,
)

from app.contracts import ContentResult
from app.contracts.common import Text, Topic


class InternalModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, revalidate_instances="always"
    )


class RunContext(InternalModel):
    task_id: UUID
    job_id: UUID
    topic: Topic


class Draft(InternalModel):
    script: Text
    title: Text
    tags: tuple[Text, ...]

    @property
    def char_count(self) -> int:
        """正文的非空白字符数，包含标点；不接受模型自报数量。"""
        return sum(not char.isspace() for char in self.script)

    @field_validator("tags")
    @classmethod
    def normalize_tags(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        if any("#" in value for value in values):
            raise ValueError("标签不能包含 #")
        return tuple(dict.fromkeys(values))


class Material(InternalModel):
    title: Text
    source: Text
    url: Text | None
    snippet: str = ""


FailureCode = Literal[
    "UPSTREAM_FAILED", "CALL_TIMEOUT", "INVALID_OUTPUT", "INTERNAL_ERROR"
]


class CapabilityFailure(InternalModel):
    capability: Text
    code: FailureCode


class RetrievalResult(InternalModel):
    materials: tuple[Material, ...]
    failures: tuple[CapabilityFailure, ...] = ()


class RetrievalRecord(InternalModel):
    capability: Text
    output: RetrievalResult


class Issue(InternalModel):
    field: Literal["script", "title", "tags", "content"]
    code: Text
    message: Text
    suggestion: Text | None = Field(default=None, exclude_if=lambda value: value is None)


class AuditMetadata(InternalModel):
    """由程序记录版本与耗时，不接受模型自报。"""

    rule_version: Text
    rules_sha256: Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
    model_version: Text
    prompt_version: Text
    elapsed_ms: Annotated[int, Field(strict=True, ge=0)]


class ReviewDecision(InternalModel):
    passed: StrictBool
    issues: tuple[Issue, ...]
    audit: AuditMetadata | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode="after")
    def consistent(self) -> "ReviewDecision":
        if self.passed == bool(self.issues):
            raise ValueError("通过时问题必须为空，拒绝时必须说明问题")
        return self


class ReviewRecord(InternalModel):
    capability: Text
    decision: ReviewDecision | None
    failure: CapabilityFailure | None

    @model_validator(mode="after")
    def one_result(self) -> "ReviewRecord":
        if (self.decision is None) == (self.failure is None):
            raise ValueError("审核必须返回决定或调用失败之一")
        return self


RoundNumber = Annotated[int, Field(strict=True, ge=1, le=2)]


class GenerationInput(InternalModel):
    context: RunContext
    round_number: RoundNumber
    materials: tuple[Material, ...]
    previous_draft: Draft | None
    feedback: tuple[ReviewRecord, ...]
    instructions: Text | None = None


class ReviewInput(InternalModel):
    context: RunContext
    round_number: RoundNumber
    draft: Draft


class RoundRecord(InternalModel):
    round_number: RoundNumber
    draft: Draft
    reviews: tuple[ReviewRecord, ...]


class RunFailure(InternalModel):
    code: Literal["CONTENT_REJECTED", "GENERATION_FAILED", "REVIEW_FAILED"]
    causes: tuple[CapabilityFailure, ...] = ()


class RunOutcome(InternalModel):
    context: RunContext
    result: ContentResult | None
    error: RunFailure | None
    retrievals: tuple[RetrievalRecord, ...]
    rounds: tuple[RoundRecord, ...]

    @model_validator(mode="after")
    def one_outcome(self) -> "RunOutcome":
        if (self.result is None) == (self.error is None):
            raise ValueError("运行必须返回成功内容或错误之一")
        return self
