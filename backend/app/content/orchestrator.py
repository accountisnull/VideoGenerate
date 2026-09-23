"""固定流程执行生成和审核，不承担 HTTP 作业持久化。"""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping, Sequence
from time import monotonic
from typing import TypeVar

from pydantic import ValidationError

from app.contracts import ContentResult

from .interfaces import (
    CallJournal,
    CapabilityError,
    Generator,
    InvalidCapabilityOutput,
    Retriever,
    Reviewer,
)
from .models import (
    CapabilityFailure,
    Draft,
    GenerationInput,
    InternalModel,
    RetrievalRecord,
    RetrievalResult,
    ReviewDecision,
    ReviewInput,
    ReviewRecord,
    RoundRecord,
    RunContext,
    RunFailure,
    RunOutcome,
)

logger = logging.getLogger(__name__)
Model = TypeVar("Model", bound=InternalModel)
Value = TypeVar("Value")


async def _parallel(calls: Sequence[Awaitable[Value]]) -> list[Value]:
    tasks = [asyncio.create_task(call) for call in calls]
    try:
        return await asyncio.gather(*tasks)
    finally:
        # 取消或异常时等待兄弟任务退出，禁止返回后遗留模型调用。
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


class ContentOrchestrator:
    def __init__(
        self,
        *,
        generator: Generator,
        keywords: Reviewer,
        content_review: Reviewer,
        call_timeout_seconds: float,
        retrievers: Mapping[str, Retriever] | None = None,
        additional_reviews: Mapping[str, Reviewer] | None = None,
        journal: CallJournal | None = None,
    ) -> None:
        if (
            isinstance(call_timeout_seconds, bool)
            or not isinstance(call_timeout_seconds, (int, float))
            or not math.isfinite(call_timeout_seconds)
            or call_timeout_seconds <= 0
        ):
            raise ValueError("能力调用超时必须为正数且有限")
        self.generator = generator
        self.journal = journal
        self.timeout = call_timeout_seconds
        self.retrievers = dict(retrievers or {})
        extra = dict(additional_reviews or {})
        if {"keywords", "content"} & extra.keys():
            raise ValueError("附加审核不能覆盖必需审核")
        for name in (*self.retrievers, *extra):
            if (
                not isinstance(name, str)
                or not name.isascii()
                or not name.isidentifier()
            ):
                raise ValueError("能力名称必须为 ASCII 标识符")
        if generator is None or keywords is None or content_review is None:
            raise ValueError("生成、关键词及内容审核必须配置")
        self.reviewers = {"keywords": keywords, "content": content_review, **extra}

    async def _call(
        self,
        context: RunContext,
        stage: str,
        name: str,
        call: Callable[[], Awaitable[Model]],
        schema: type[Model],
        input_model: InternalModel,
    ) -> Model | CapabilityFailure:
        call_id = None
        if self.journal is not None:
            call_id = await self.journal.begin(
                context, stage, name, input_model.model_dump_json()
            )
        began = monotonic()
        code = None
        try:
            async with asyncio.timeout(self.timeout):
                result = schema.model_validate(await call())
        except TimeoutError:
            code = "CALL_TIMEOUT"
        except (ValidationError, InvalidCapabilityOutput):
            code = "INVALID_OUTPUT"
        except CapabilityError:
            code = "UPSTREAM_FAILED"
        except Exception:  # noqa: BLE001 -- 能力调用边界兜底，不输出敏感异常文本。
            code = "INTERNAL_ERROR"
        finally:
            logger.info(
                "内容调用 task_id=%s job_id=%s stage=%s capability=%s reason=%s elapsed_seconds=%.3f",
                context.task_id,
                context.job_id,
                stage,
                name,
                code or "completed_or_cancelled",
                monotonic() - began,
            )
        if code is not None:
            result = CapabilityFailure(capability=name, code=code)
        # 存储异常不作为供应商错误吞掉；未保存结果禁止进入下一步。
        if self.journal is not None:
            await self.journal.finish(call_id, result.model_dump_json())
        return result

    async def _retrieve(
        self, context: RunContext, name: str, provider: Retriever
    ) -> RetrievalRecord:
        result = await self._call(
            context,
            "retrieval",
            name,
            lambda: provider.retrieve(context),
            RetrievalResult,
            context,
        )
        if isinstance(result, CapabilityFailure):
            result = RetrievalResult(materials=(), failures=(result,))
        return RetrievalRecord(capability=name, output=result)

    async def _review(
        self, request: ReviewInput, name: str, reviewer: Reviewer
    ) -> ReviewRecord:
        result = await self._call(
            request.context,
            f"review_{request.round_number}",
            name,
            lambda: reviewer.review(request),
            ReviewDecision,
            request,
        )
        if isinstance(result, CapabilityFailure):
            return ReviewRecord(capability=name, decision=None, failure=result)
        return ReviewRecord(capability=name, decision=result, failure=None)

    async def run(
        self,
        context: RunContext,
        *,
        source_draft: Draft | None = None,
        instructions: str | None = None,
    ) -> RunOutcome:
        context = RunContext.model_validate(context)
        if (source_draft is None) != (instructions is None):
            raise ValueError("外部改稿必须同时提供来源稿件和修改意见")
        retrievals = tuple(
            await _parallel(
                [
                    self._retrieve(context, name, provider)
                    for name, provider in self.retrievers.items()
                ]
            )
        )
        materials = tuple(
            item for record in retrievals for item in record.output.materials
        )
        rounds: list[RoundRecord] = []
        previous = source_draft
        feedback: tuple[ReviewRecord, ...] = ()
        for number in (1, 2):
            request = GenerationInput(
                context=context,
                round_number=number,
                materials=materials,
                previous_draft=previous,
                feedback=feedback,
                instructions=instructions,
            )
            draft = await self._call(
                context,
                f"generation_{number}",
                "generator",
                lambda request=request: self.generator.generate(request),
                Draft,
                request,
            )
            if isinstance(draft, CapabilityFailure):
                return RunOutcome(
                    context=context,
                    result=None,
                    error=RunFailure(code="GENERATION_FAILED", causes=(draft,)),
                    retrievals=retrievals,
                    rounds=tuple(rounds),
                )
            review_input = ReviewInput(
                context=context, round_number=number, draft=draft
            )
            reviews = tuple(
                await _parallel(
                    [
                        self._review(review_input, name, reviewer)
                        for name, reviewer in self.reviewers.items()
                    ]
                )
            )
            rounds.append(
                RoundRecord(round_number=number, draft=draft, reviews=reviews)
            )
            failures = tuple(r.failure for r in reviews if r.failure is not None)
            if failures:
                return RunOutcome(
                    context=context,
                    result=None,
                    error=RunFailure(code="REVIEW_FAILED", causes=failures),
                    retrievals=retrievals,
                    rounds=tuple(rounds),
                )
            if all(r.decision is not None and r.decision.passed for r in reviews):
                return RunOutcome(
                    context=context,
                    result=ContentResult(
                        script=draft.script,
                        title=draft.title,
                        tags=list(draft.tags),
                        review_passed=True,
                    ),
                    error=None,
                    retrievals=retrievals,
                    rounds=tuple(rounds),
                )
            previous, feedback = draft, reviews
        return RunOutcome(
            context=context,
            result=None,
            error=RunFailure(code="CONTENT_REJECTED"),
            retrievals=retrievals,
            rounds=tuple(rounds),
        )
