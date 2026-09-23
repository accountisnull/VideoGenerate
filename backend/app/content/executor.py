"""单作业后台执行；所有 SQLite 调用在线程中执行。"""

import asyncio
import json
import logging
from uuid import UUID

from .models import Draft, RunContext
from .orchestrator import ContentOrchestrator
from .storage import ContentStore

logger = logging.getLogger(__name__)


class StoredJournal:
    def __init__(self, store: ContentStore) -> None:
        self.store = store

    async def begin(
        self, context: RunContext, stage: str, capability: str, input_json: str
    ) -> UUID:
        return await asyncio.to_thread(
            self.store.begin_call, context.job_id, stage, capability, input_json
        )

    async def finish(self, call_id: UUID, output_json: str) -> None:
        await asyncio.to_thread(self.store.finish_call, call_id, output_json)


class ContentExecutor:
    def __init__(
        self, store: ContentStore, orchestrator: ContentOrchestrator | None
    ) -> None:
        self.store = store
        self.orchestrator = orchestrator
        self.available = False
        self.wake = asyncio.Event()
        self.stop = asyncio.Event()

    async def run(self) -> None:
        if self.orchestrator is None:
            return
        vector = getattr(self.orchestrator, "vector_agent", None)
        if vector is not None:
            from .similarity_models import IndexProfile, StoreRequest
            for pending in await asyncio.to_thread(self.store.pending_vector_calls):
                try:
                    intent = json.loads(pending["input_json"])
                    request = StoreRequest.model_validate(intent["request"])
                    profile = IndexProfile.model_validate(intent["profile"])
                    result = await vector.reconcile(request, profile)
                    await asyncio.to_thread(
                        self.store.finish_call, UUID(pending["call_id"]), result.model_dump_json()
                    )
                except Exception:  # noqa: BLE001 -- 恢复边界保留未核对意图，不重做模型。
                    logger.error("向量入库核对未完成 call_id=%s", pending["call_id"])
        self.available = True
        try:
            while not self.stop.is_set():
                self.wake.clear()
                claimed = await asyncio.to_thread(self.store.claim)
                if claimed is None:
                    try:
                        await asyncio.wait_for(self.wake.wait(), 0.2)
                    except TimeoutError:
                        pass
                    continue
                job, request, source = claimed
                ctx = RunContext(
                    task_id=request.task_id, job_id=job.root.job_id, topic=request.topic
                )
                try:
                    draft = None
                    if source is not None:
                        result = source.root.result
                        draft = Draft(
                            script=result.script,
                            title=result.title,
                            tags=tuple(result.tags),
                        )
                    outcome = await self.orchestrator.run(
                        ctx,
                        source_draft=draft,
                        instructions=request.revision.instructions
                        if request.revision
                        else None,
                    )
                    if outcome.result is not None and vector is not None:
                        request = vector.store_request(outcome)
                        call_id = await asyncio.to_thread(
                            self.store.begin_call, ctx.job_id, "vector_store", "similarity",
                            json.dumps({
                                "request": request.model_dump(mode="json"),
                                "profile": vector.profiles[ctx.job_id].model_dump(mode="json"),
                                "outcome": outcome.model_dump(mode="json"),
                            }, ensure_ascii=False),
                        )
                        # 先记录意图再写外部库；结果不明保留未完成记录，重启仅核对。
                        saved = await vector.save(request)
                        await asyncio.to_thread(self.store.finish_call, call_id, saved.model_dump_json())
                    await asyncio.to_thread(self.store.finish, outcome)
                except Exception:  # noqa: BLE001 -- 作业边界停止执行并记录安全错误。
                    logger.error(
                        "内容作业异常 task_id=%s job_id=%s reason=INTERNAL_ERROR",
                        ctx.task_id,
                        ctx.job_id,
                    )
                    await asyncio.to_thread(self.store.fail_running, ctx.job_id)
                finally:
                    if vector is not None:
                        vector.profiles.pop(ctx.job_id, None)
        except Exception:  # noqa: BLE001 -- 存储或执行器异常后禁用新受理。
            logger.error("内容执行器停止 reason=EXECUTOR_FAILED")
        finally:
            self.available = False
