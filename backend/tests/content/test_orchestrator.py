"""使用替身验证审核门槛、调用次数和异步生命周期。"""

import asyncio
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.content.interfaces import CapabilityError
from app.content.models import (
    Draft,
    GenerationInput,
    Issue,
    Material,
    RetrievalResult,
    ReviewDecision,
    ReviewInput,
    RunContext,
)
from app.content.orchestrator import ContentOrchestrator


class Generator:
    def __init__(self, error=None):
        self.calls = []
        self.error = error

    async def generate(self, request: GenerationInput) -> Draft:
        self.calls.append(request)
        if self.error:
            raise self.error
        return Draft(
            script=f"第{request.round_number}轮口播稿",
            title="绿萝养护",
            tags=("养花", "养花"),
        )


class Reviewer:
    def __init__(self, *passed, error=None, raw=None):
        self.passed = passed or (True,)
        self.error, self.raw = error, raw
        self.calls = []

    async def review(self, request: ReviewInput) -> ReviewDecision:
        self.calls.append(request)
        if self.error:
            raise self.error
        if self.raw is not None:
            return self.raw
        passed = self.passed[min(len(self.calls) - 1, len(self.passed) - 1)]
        issues = (
            ()
            if passed
            else (Issue(field="title", code="PROMISE", message="删除承诺"),)
        )
        return ReviewDecision(passed=passed, issues=issues)


class Retriever:
    def __init__(self, error=None):
        self.error = error

    async def retrieve(self, context: RunContext) -> RetrievalResult:
        if self.error:
            raise self.error
        return RetrievalResult(
            materials=(Material(title=context.topic, source="test", url=None),)
        )


def context():
    return RunContext(task_id=uuid4(), job_id=uuid4(), topic="介绍绿萝养护")


def build(generator=None, keywords=None, audit=None, **kwargs):
    return ContentOrchestrator(
        generator=generator or Generator(),
        keywords=keywords or Reviewer(),
        content_review=audit or Reviewer(),
        call_timeout_seconds=kwargs.pop("timeout", 1),
        **kwargs,
    )


def test_first_pass_preserves_all_fields_and_context():
    gen, keywords, audit = Generator(), Reviewer(), Reviewer()
    ctx = context()
    outcome = asyncio.run(
        build(gen, keywords, audit, retrievers={"search": Retriever()}).run(ctx)
    )
    assert outcome.error is None
    assert outcome.result.script == "第1轮口播稿"
    assert outcome.result.title == "绿萝养护"
    assert outcome.result.tags == ["养花"]
    assert outcome.result.review_passed is True
    assert gen.calls[0].context == ctx
    assert gen.calls[0].materials[0].title == ctx.topic
    assert gen.calls[0].previous_draft is None
    assert len(gen.calls) == len(keywords.calls) == len(audit.calls) == 1
    assert keywords.calls[0] == audit.calls[0]


def test_revision_receives_prior_draft_and_every_review():
    gen, keywords, audit = Generator(), Reviewer(False, True), Reviewer()
    outcome = asyncio.run(build(gen, keywords, audit).run(context()))
    assert outcome.result.script == "第2轮口播稿"
    assert len(gen.calls) == len(keywords.calls) == len(audit.calls) == 2
    retry = gen.calls[1]
    assert retry.context == gen.calls[0].context
    assert retry.previous_draft == outcome.rounds[0].draft
    assert retry.feedback == outcome.rounds[0].reviews
    assert retry.feedback[0].decision.issues[0].field == "title"


def test_two_rejections_end_without_third_generation():
    gen = Generator()
    outcome = asyncio.run(build(gen, audit=Reviewer(False)).run(context()))
    assert outcome.result is None
    assert outcome.error.code == "CONTENT_REJECTED"
    assert len(gen.calls) == len(outcome.rounds) == 2
    assert outcome.rounds[-1].reviews[1].decision.issues


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"passed": "true", "issues": []},
        {"passed": True},
        {"passed": False, "issues": []},
        {
            "passed": True,
            "issues": [{"field": "script", "code": "X", "message": "有问题"}],
        },
        {"passed": True, "issues": [], "unexpected": True},
    ],
)
def test_invalid_review_fails_closed_without_revision(raw):
    gen = Generator()
    outcome = asyncio.run(build(gen, audit=Reviewer(raw=raw)).run(context()))
    assert outcome.result is None
    assert outcome.error.code == "REVIEW_FAILED"
    assert outcome.error.causes[0].code == "INVALID_OUTPUT"
    assert len(gen.calls) == 1


@pytest.mark.parametrize(
    "error,code",
    [
        (CapabilityError(), "UPSTREAM_FAILED"),
        (RuntimeError("secret-provider-response"), "INTERNAL_ERROR"),
        (TimeoutError("secret-provider-response"), "CALL_TIMEOUT"),
    ],
)
def test_review_call_failure_is_not_business_rejection(error, code, caplog):
    gen = Generator()
    with caplog.at_level("INFO"):
        outcome = asyncio.run(build(gen, audit=Reviewer(error=error)).run(context()))
    assert outcome.result is None
    assert outcome.error.code == "REVIEW_FAILED"
    assert outcome.error.causes[0].code == code
    assert len(gen.calls) == 1
    assert "secret-provider-response" not in outcome.model_dump_json() + caplog.text
    assert "job_id=" in caplog.text


def test_generator_failure_does_not_run_reviews():
    review = Reviewer()
    outcome = asyncio.run(
        build(Generator(CapabilityError()), audit=review).run(context())
    )
    assert outcome.error.code == "GENERATION_FAILED"
    assert outcome.rounds == () and not review.calls


def test_invalid_draft_is_never_reviewed():
    class InvalidGenerator:
        async def generate(self, request):
            return {"script": "稿件", "title": "标题", "tags": ["#违规标签"]}

    review = Reviewer()
    outcome = asyncio.run(build(InvalidGenerator(), audit=review).run(context()))
    assert outcome.error.causes[0].code == "INVALID_OUTPUT"
    assert not review.calls


def test_all_retrievals_failed_preserve_original_topic():
    gen = Generator()
    ctx = context()
    outcome = asyncio.run(
        build(
            gen,
            retrievers={
                "trending": Retriever(CapabilityError()),
                "search": Retriever(TimeoutError()),
            },
        ).run(ctx)
    )
    assert outcome.result is not None
    assert gen.calls[0].context.topic == ctx.topic
    assert gen.calls[0].materials == ()
    assert [r.output.failures[0].code for r in outcome.retrievals] == [
        "UPSTREAM_FAILED",
        "CALL_TIMEOUT",
    ]


def test_optional_similarity_participates_in_every_round():
    similarity = Reviewer(False)
    outcome = asyncio.run(
        build(additional_reviews={"similarity": similarity}).run(context())
    )
    assert outcome.error.code == "CONTENT_REJECTED"
    assert len(similarity.calls) == 2
    assert outcome.rounds[-1].reviews[-1].capability == "similarity"


def test_reviews_run_concurrently_and_timeout_cleans_up():
    async def scenario():
        started = set()
        ready = asyncio.Event()
        stopped = set()

        class WaitingReviewer:
            def __init__(self, name):
                self.name = name

            async def review(self, request):
                started.add(self.name)
                if len(started) == 2:
                    ready.set()
                try:
                    await ready.wait()
                    await asyncio.Event().wait()
                finally:
                    stopped.add(self.name)

        outcome = await build(
            keywords=WaitingReviewer("keywords"),
            audit=WaitingReviewer("content"),
            timeout=0.05,
        ).run(context())
        assert ready.is_set()
        assert stopped == {"keywords", "content"}
        assert outcome.error.code == "REVIEW_FAILED"
        assert all(c.code == "CALL_TIMEOUT" for c in outcome.error.causes)

    asyncio.run(scenario())


@pytest.mark.parametrize("stage", ["retrieval", "generation", "review"])
def test_cancellation_propagates_and_joins_pending_calls(stage):
    async def scenario():
        entered = asyncio.Event()
        active = set()
        expected = 1 if stage == "generation" else 2

        class WaitingCapability:
            def __init__(self, name):
                self.name = name

            async def wait(self, request):
                active.add(self.name)
                if len(active) == expected:
                    entered.set()
                try:
                    await asyncio.Event().wait()
                finally:
                    active.remove(self.name)

            retrieve = wait
            generate = wait
            review = wait

        kwargs = {}
        if stage == "retrieval":
            kwargs["retrievers"] = {
                name: WaitingCapability(name) for name in ("search", "trending")
            }
        elif stage == "generation":
            kwargs["generator"] = WaitingCapability("generator")
        else:
            kwargs["keywords"] = WaitingCapability("keywords")
            kwargs["audit"] = WaitingCapability("content")
        task = asyncio.create_task(build(**kwargs).run(context()))
        await asyncio.wait_for(entered.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not active

    asyncio.run(scenario())


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf"), True, "60"])
def test_invalid_timeout_rejected(timeout):
    with pytest.raises(ValueError):
        build(timeout=timeout)


def test_required_reviews_cannot_be_overridden():
    with pytest.raises(ValueError):
        build(additional_reviews={"keywords": Reviewer()})
    with pytest.raises(ValueError):
        ContentOrchestrator(
            generator=Generator(),
            keywords=None,
            content_review=Reviewer(),
            call_timeout_seconds=1,
        )


def test_draft_cannot_be_mutated_by_a_reviewer():
    draft = Draft(script="稿件", title="标题", tags=("标签",))
    with pytest.raises(ValidationError):
        draft.script = "未经审核的修改"


def test_char_count_excludes_unicode_whitespace_and_counts_punctuation():
    draft = Draft(script="甲 乙\n丙\t，\u3000丁。\u00a0", title="标题", tags=())
    assert draft.char_count == 6
    with pytest.raises(ValidationError):
        Draft.model_validate({**draft.model_dump(), "char_count": 999})


def test_partial_retrieval_preserves_materials_and_failure():
    gen = Generator()
    outcome = asyncio.run(
        build(
            gen,
            retrievers={
                "search": Retriever(),
                "trending": Retriever(CapabilityError()),
            },
        ).run(context())
    )
    assert outcome.result is not None
    assert len(gen.calls[0].materials) == 1
    assert outcome.retrievals[0].output.failures == ()
    assert outcome.retrievals[1].output.failures[0].capability == "trending"


def test_retrievals_run_concurrently():
    async def scenario():
        started = set()
        ready = asyncio.Event()

        class CoordinatedRetriever:
            def __init__(self, name):
                self.name = name

            async def retrieve(self, request):
                started.add(self.name)
                if len(started) == 2:
                    ready.set()
                await ready.wait()
                return RetrievalResult(materials=())

        outcome = await build(
            retrievers={
                name: CoordinatedRetriever(name) for name in ("search", "trending")
            }
        ).run(context())
        assert outcome.result is not None
        assert all(not item.output.failures for item in outcome.retrievals)

    asyncio.run(scenario())


def test_runs_do_not_share_previous_draft_or_feedback():
    async def scenario():
        gen = Generator()
        orchestrator = build(gen)
        first, second = context(), context()
        await orchestrator.run(first)
        outcome = await orchestrator.run(second)
        assert gen.calls[1].context == second
        assert gen.calls[1].previous_draft is None
        assert gen.calls[1].feedback == ()
        assert len(outcome.rounds) == 1

    asyncio.run(scenario())


def test_failed_revision_preserves_first_round_without_result():
    class RevisionFailure(Generator):
        async def generate(self, request):
            if request.round_number == 2:
                raise CapabilityError()
            return await super().generate(request)

    outcome = asyncio.run(
        build(RevisionFailure(), audit=Reviewer(False)).run(context())
    )
    assert outcome.result is None
    assert outcome.error.code == "GENERATION_FAILED"
    assert len(outcome.rounds) == 1
    assert outcome.rounds[0].reviews[1].decision.passed is False
