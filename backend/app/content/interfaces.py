"""能力接口由各成员实现，供应商细节不进入主管流程。"""

from typing import Protocol, TypeVar
from uuid import UUID

from .models import (
    Draft,
    GenerationInput,
    InternalModel,
    RetrievalResult,
    ReviewDecision,
    ReviewInput,
    RunContext,
)

Output = TypeVar("Output", bound=InternalModel)


class TextCompletion(Protocol):
    async def complete(
        self, system: str, data: str, schema: type[Output]
    ) -> Output: ...


class CapabilityError(Exception):
    """适配层确认的上游故障；原始响应和密钥不得传入此异常。"""

    def __init__(self) -> None:
        super().__init__("内容能力调用失败")


class InvalidCapabilityOutput(CapabilityError):
    """上游输出不完整或无法通过结构校验。"""


class Retriever(Protocol):
    async def retrieve(self, context: RunContext) -> RetrievalResult: ...


class Generator(Protocol):
    async def generate(self, request: GenerationInput) -> Draft: ...


class Reviewer(Protocol):
    async def review(self, request: ReviewInput) -> ReviewDecision: ...


class CallJournal(Protocol):
    async def begin(
        self, context: RunContext, stage: str, capability: str, input_json: str
    ) -> UUID: ...

    async def finish(self, call_id: UUID, output_json: str) -> None: ...
