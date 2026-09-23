"""验证成员接入后执行真实编排门槛；替身只用于测试。"""

import asyncio
from uuid import uuid4

import pytest

from app.content.agents import ContentAgents, assemble_agents
from app.content.interfaces import CapabilityError
from app.content.models import Draft, RetrievalResult, ReviewDecision, RunContext


def test_full_assembly_uses_all_members_and_blocks_failed_similarity():
    calls = []

    class Generate:
        async def generate(self, request):
            calls.append("generation")
            return Draft(script="正文", title="标题", tags=())

    class Retrieve:
        def __init__(self, name):
            self.name = name

        async def retrieve(self, context):
            calls.append(self.name)
            return RetrievalResult(materials=())

    class Review:
        def __init__(self, name):
            self.name = name

        async def review(self, request):
            calls.append(self.name)
            if self.name == "similarity":
                raise CapabilityError()
            return ReviewDecision(passed=True, issues=())

    agents = ContentAgents(
        generation=Generate(), audit=Review("audit"), keywords=Review("keywords"),
    )
    with pytest.raises(ValueError, match="完整组装"):
        assemble_agents(agents, call_timeout_seconds=1, require_all=True)
    complete = ContentAgents(
        generation=agents.generation,
        audit=agents.audit,
        keywords=agents.keywords,
        trending=Retrieve("trending"),
        search=Retrieve("search"),
        similarity=Review("similarity"),
    )
    outcome = asyncio.run(
        assemble_agents(complete, call_timeout_seconds=1, require_all=True).run(
            RunContext(task_id=uuid4(), job_id=uuid4(), topic="主题")
        )
    )
    assert sorted(calls) == sorted(
        ["generation", "audit", "keywords", "trending", "search", "similarity"]
    )
    assert outcome.result is None
    assert outcome.error.code == "REVIEW_FAILED"


def test_missing_member_method_is_rejected_at_assembly():
    with pytest.raises(TypeError, match="generation"):
        ContentAgents(generation=object(), audit=object(), keywords=object())
