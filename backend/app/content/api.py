"""可注入真实内容能力的独立 HTTP 服务，默认不受理新作业。"""

import asyncio
import json
import logging
import secrets
import sys
from contextlib import AsyncExitStack, asynccontextmanager
from copy import copy
from pathlib import Path
from uuid import UUID, uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.exceptions import HTTPException

from app.contracts import (
    ContentCapabilities,
    ContentRequest,
    CreateHeaders,
    Error,
    ErrorResponse,
    HealthResponse,
)

from .executor import ContentExecutor, StoredJournal
from .orchestrator import ContentOrchestrator
from .settings import ContentSettings, load_settings
from .storage import ContentStore, StoreError
from .storage.ownership import ServiceOwnership

logger = logging.getLogger(__name__)
MESSAGES = {
    "INVALID_JSON": "请求必须为有效 UTF-8 JSON 对象",
    "INVALID_ARGUMENT": "请求参数或改稿来源无效",
    "UNAUTHORIZED": "服务鉴权失败",
    "JOB_NOT_FOUND": "内容作业不存在",
    "IDEMPOTENCY_CONFLICT": "同一幂等键的请求内容不一致",
    "SERVICE_BUSY": "已有内容作业执行中",
    "SERVICE_NOT_READY": "内容生成与审核能力尚未就绪",
    "INTERNAL_ERROR": "服务异常，请通过请求编号核实",
}
STATUS = {
    "INVALID_JSON": 400,
    "INVALID_ARGUMENT": 422,
    "UNAUTHORIZED": 401,
    "JOB_NOT_FOUND": 404,
    "IDEMPOTENCY_CONFLICT": 409,
    "SERVICE_BUSY": 409,
    "SERVICE_NOT_READY": 503,
}


def error_response(
    request: Request, code: str, status: int | None = None
) -> JSONResponse:
    body = ErrorResponse(
        request_id=request.state.request_id,
        error=Error(
            code=code,
            message=MESSAGES.get(code, MESSAGES["INTERNAL_ERROR"]),
            retryable=code in ("SERVICE_BUSY", "SERVICE_NOT_READY"),
            details={},
        ),
    )
    headers = {"Retry-After": "2"} if code == "SERVICE_BUSY" else {}
    return JSONResponse(
        body.model_dump(mode="json"),
        status_code=status or STATUS.get(code, 500),
        headers=headers,
    )


def create_app(
    settings: ContentSettings | None = None,
    orchestrator: ContentOrchestrator | None = None,
) -> FastAPI:
    settings = settings or load_settings()
    store = ContentStore(settings.database.resolve())
    owned_orchestrator = copy(orchestrator) if orchestrator is not None else None
    if owned_orchestrator is not None:
        owned_orchestrator.journal = StoredJournal(store)
    executor = ContentExecutor(store, owned_orchestrator)
    ownership = ServiceOwnership(store.path)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        ownership.acquire()
        stack = AsyncExitStack()
        task = None
        try:
            await asyncio.to_thread(store.initialize)
            await asyncio.to_thread(store.interrupt_running)
            if settings.similarity_enabled:
                if owned_orchestrator is None:
                    raise ValueError("启用同质化需要先配置内容模型")
                from app.runtime import ROOT

                from .similarity_agent import vector_mcp_session
                vector = await stack.enter_async_context(vector_mcp_session(
                    settings.similarity_python or Path(sys.executable),
                    ROOT / "backend", settings.similarity_timeout_seconds,
                ))
                owned_orchestrator.reviewers = {**owned_orchestrator.reviewers, "similarity": vector}
                owned_orchestrator.vector_agent = vector
                owned_orchestrator.timeout = max(owned_orchestrator.timeout, settings.similarity_timeout_seconds)
            task = asyncio.create_task(executor.run())
            await asyncio.sleep(0)
            if owned_orchestrator is not None:
                # 等待历史向量意图核对结束，不能只等待后台任务被调度一次。
                while not executor.available and not task.done():
                    await asyncio.sleep(0.01)
                if task.done():
                    await task
                    raise RuntimeError("内容执行器未就绪即退出")
            yield
        finally:
            try:
                if task is not None:
                    executor.stop.set()
                    executor.wake.set()
                    # 正常关闭等待当前能力有界完成，不取消仍在写数据库的线程。
                    await task
                    await asyncio.to_thread(store.interrupt_running)
            finally:
                try:
                    await stack.aclose()
                finally:
                    ownership.release()

    app = FastAPI(title="内容作业服务", lifespan=lifespan)
    app.state.store = store
    app.state.executor = executor

    @app.middleware("http")
    async def boundary(request: Request, call_next):
        request.state.request_id = uuid4()
        try:
            raw = request.headers.get("X-Request-ID")
            if raw is not None:
                request.state.request_id = UUID(raw)
        except ValueError:
            response = error_response(request, "INVALID_ARGUMENT")
        else:
            supplied = request.headers.get("Authorization", "")
            expected = (
                "Bearer " + settings.token.get_secret_value()
                if settings.token
                else None
            )
            if (
                expected is not None
                and not secrets.compare_digest(supplied.encode(), expected.encode())
                or request.headers.get("origin")
                and request.headers["origin"] != str(request.base_url).rstrip("/")
            ):
                response = error_response(request, "UNAUTHORIZED")
            else:
                try:
                    response = await call_next(request)
                except Exception:  # noqa: BLE001 -- HTTP 边界仅记录追踪号，不泄露请求或堆栈。
                    logger.error(
                        "内容 HTTP 异常 request_id=%s reason=INTERNAL_ERROR",
                        request.state.request_id,
                    )
                    response = error_response(request, "INTERNAL_ERROR")
        response.headers["X-Request-ID"] = str(request.state.request_id)
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid(request: Request, error: RequestValidationError):
        return error_response(request, "INVALID_ARGUMENT")

    @app.exception_handler(StoreError)
    async def store_error(request: Request, error: StoreError):
        return error_response(
            request, error.code if error.code in STATUS else "INTERNAL_ERROR"
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, error: HTTPException):
        return error_response(
            request,
            "JOB_NOT_FOUND" if error.status_code == 404 else "INVALID_ARGUMENT",
            error.status_code,
        )

    @app.get("/v1/content/health")
    async def health(request: Request):
        if not executor.available:
            return error_response(request, "SERVICE_NOT_READY")
        return HealthResponse(service="content", api_version="1.0", ready=True)

    @app.get("/v1/content/capabilities")
    async def capabilities():
        return ContentCapabilities(
            service="content", api_version="1.0", profiles=[], resources=[]
        )

    @app.post("/v1/content/jobs")
    async def submit(request: Request):
        try:
            headers = CreateHeaders.model_validate(
                {
                    "Idempotency-Key": request.headers.get("Idempotency-Key"),
                    "X-Request-ID": str(request.state.request_id),
                }
            )
        except ValidationError:
            return error_response(request, "INVALID_ARGUMENT")
        try:
            body = json.loads(
                (await request.body()).decode("utf-8"),
                parse_constant=_invalid_constant,
                object_pairs_hook=_unique_object,
            )
        except (ValueError, UnicodeError, RecursionError):
            return error_response(request, "INVALID_JSON")
        try:
            content_request = ContentRequest.model_validate(body)
        except ValidationError:
            return error_response(request, "INVALID_ARGUMENT")
        job, created = await asyncio.to_thread(
            store.submit,
            content_request,
            headers.idempotency_key,
            "local-worker",
            ready=executor.available,
        )
        executor.wake.set()
        return JSONResponse(
            job.model_dump(mode="json"),
            status_code=202 if created else 200,
            headers={"Location": f"/v1/content/jobs/{job.root.job_id}"},
        )

    @app.get("/v1/content/jobs/{job_id}")
    async def get_job(job_id: UUID):
        return await asyncio.to_thread(store.get, job_id, "local-worker")

    return app


def _invalid_constant(value: str) -> None:
    raise ValueError("JSON 不允许非有限数值")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 不允许重复字段")
        result[key] = value
    return result
