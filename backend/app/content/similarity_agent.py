"""主管到 vector-mcp 的受控客户端；固定版本与审核快照，不由模型调用工具。"""

import asyncio
import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path
from uuid import uuid5

from .interfaces import CapabilityError, InvalidCapabilityOutput
from .models import Issue, ReviewDecision, ReviewInput, RunOutcome
from .similarity_models import (
    Approval,
    DetectionResult,
    IndexProfile,
    StoreRequest,
    StoreResult,
    script_digest,
)

logger = logging.getLogger(__name__)


class SimilarityAgent:
    def __init__(self, call: Callable[[str, dict], Awaitable[dict]]) -> None:
        self.call = call
        self.profiles = {}

    async def invoke(self, action: str, request: dict) -> dict:
        result = await self.call(action, request)
        if not isinstance(result, dict):
            raise InvalidCapabilityOutput()
        if result.get("success") is not True:
            error = result.get("error") or {}
            code = error.get("code", "VECTOR_ERROR")
            if not isinstance(code, str) or not code.isidentifier():
                code = "VECTOR_ERROR"
            logger.error("同质化调用失败 job_id=%s action=%s reason=%s",
                         request.get("job_id"), action, code)
            raise CapabilityError()
        if not isinstance(result.get("data"), dict):
            raise InvalidCapabilityOutput()
        return result["data"]

    async def review(self, request: ReviewInput) -> ReviewDecision:
        version = uuid5(request.context.job_id, f"content-round-{request.round_number}")
        result = DetectionResult.model_validate(await self.invoke("detect", {
            "job_id": str(request.context.job_id), "content_version_id": str(version),
            "script": request.draft.script,
        }))
        previous = self.profiles.get(request.context.job_id)
        if previous is not None and previous != result.profile:
            raise InvalidCapabilityOutput()
        self.profiles[request.context.job_id] = result.profile
        if result.passed:
            return ReviewDecision(passed=True, issues=())
        feedback = "; ".join(
            f"版本{hit.content_version_id}，分数{hit.score:.6f}，参考正文：{hit.script_excerpt}"
            for hit in result.matches
        )
        return ReviewDecision(passed=False, issues=(
            Issue(field="script", code="SIMILARITY_BLOCKED", message=feedback),
        ))

    def store_request(self, outcome: RunOutcome) -> StoreRequest:
        if outcome.result is None or not outcome.rounds:
            raise ValueError("仅已审核最终稿可入库")
        final = outcome.rounds[-1]
        if final.draft.script != outcome.result.script:
            raise ValueError("最终正文与审核快照不一致")
        checks = {}
        for review in final.reviews:
            if review.failure is not None or review.decision is None or not review.decision.passed:
                raise ValueError("所有已启用审核必须通过")
            checks["content_audit" if review.capability == "content" else review.capability] = True
        if not {"keywords", "content_audit", "similarity"} <= checks.keys():
            raise ValueError("缺少必需审核快照")
        version = uuid5(outcome.context.job_id, f"content-round-{final.round_number}")
        return StoreRequest(
            job_id=outcome.context.job_id, content_version_id=version,
            script=outcome.result.script,
            approval=Approval(
                approval_id=uuid5(version, "approval"), job_id=outcome.context.job_id,
                content_version_id=version, script_sha256=script_digest(outcome.result.script),
                checks=checks,
            ),
        )

    async def save(self, request: StoreRequest) -> StoreResult:
        profile = self.profiles[request.job_id]
        result = StoreResult.model_validate(await self.invoke("store", {
            **request.model_dump(mode="json"), "expected_profile": profile.model_dump(mode="json"),
        }))
        self._check_result(request, result)
        if result.index_version != profile.index_version:
            raise InvalidCapabilityOutput()
        if not result.saved:
            raise CapabilityError()
        return result

    async def reconcile(self, request: StoreRequest, profile: IndexProfile) -> StoreResult:
        payload = request.model_dump(mode="json", exclude={"approval"})
        payload["expected_profile"] = profile.model_dump(mode="json")
        result = StoreResult.model_validate(await self.invoke("reconcile", payload))
        self._check_result(request, result)
        if result.index_version != profile.index_version:
            raise InvalidCapabilityOutput()
        return result

    @staticmethod
    def _check_result(request: StoreRequest, result: StoreResult) -> None:
        if result.point_id != request.content_version_id or result.content_version_id != request.content_version_id:
            raise InvalidCapabilityOutput()


@asynccontextmanager
async def vector_mcp_session(python: Path, backend: Path, timeout: float) -> AsyncIterator[SimilarityAgent]:
    """兼容已交付的 stdio 服务；仅启动显式配置的本地 Python 入口。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    from app.runtime import environment_values

    values = environment_values()
    child_env = {name: value for name, value in values.items()
                 if name.startswith("SIMILARITY_") and isinstance(value, str)}

    async with AsyncExitStack() as stack:
        read, write = await stack.enter_async_context(stdio_client(
            StdioServerParameters(command=str(python),
                                  args=["-X", "utf8", "-m", "app.content.mcp_server"],
                                  cwd=str(backend), env=child_env)))
        session = await stack.enter_async_context(ClientSession(read, write))
        async with asyncio.timeout(timeout):
            await session.initialize()
            tools = await session.list_tools()
        if not {"detect", "vector-insert", "reconcile"} <= {tool.name for tool in tools.tools}:
            raise ValueError("vector-mcp 缺少必需工具")

        async def call(action: str, request: dict) -> dict:
            async with asyncio.timeout(timeout):
                response = await session.call_tool(
                    "vector-insert" if action == "store" else action,
                    arguments={"request": request},
                )
            if response.isError:
                raise CapabilityError()
            # PR #6 的稳定包装；success=false 仍必须视为调用错误。
            from .json_data import strict_json
            payload = response.structuredContent
            if payload is None:
                if len(response.content) != 1 or response.content[0].type != "text":
                    raise InvalidCapabilityOutput()
                payload = strict_json(response.content[0].text)
            if not isinstance(payload, dict):
                raise InvalidCapabilityOutput()
            return payload

        yield SimilarityAgent(call)
