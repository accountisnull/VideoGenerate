"""事务化受理、终态保护和逐次能力调用记录。"""

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from app.contracts import ContentJob, ContentRequest, Error

from ..models import RunOutcome
from .migrations import APPLICATION_ID, STATEMENTS, VERSION


class StoreError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


def canonical_request(request: ContentRequest) -> str:
    return json.dumps(
        request.model_dump(mode="json"), sort_keys=True, ensure_ascii=False
    )


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


class ContentStore:
    def __init__(self, path: Path) -> None:
        self.path = path

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path, timeout=3, isolation_level=None)
        db.row_factory = sqlite3.Row
        try:
            db.execute("PRAGMA foreign_keys=ON")
            db.execute("BEGIN IMMEDIATE")
            yield db
            db.commit()
        finally:
            if db.in_transaction:
                db.rollback()
            db.close()

    def initialize(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._transaction() as db:
            application_id = db.execute("PRAGMA application_id").fetchone()[0]
            version = db.execute("PRAGMA user_version").fetchone()[0]
            if application_id == 0 and version == 0:
                if db.execute(
                    "SELECT 1 FROM sqlite_master WHERE type='table'"
                ).fetchone():
                    raise StoreError("STORAGE_SCHEMA_INVALID")
                for statement in STATEMENTS:
                    db.execute(statement)
                db.execute(f"PRAGMA application_id={APPLICATION_ID}")
                db.execute(f"PRAGMA user_version={VERSION}")
            elif application_id != APPLICATION_ID or version != VERSION:
                raise StoreError("STORAGE_SCHEMA_INVALID")
            # 即使版本号正确，也不能在缺表数据库中启动服务。
            db.execute(
                "SELECT job_id, caller_id, request_json, response_json, outcome_json FROM content_jobs LIMIT 0"
            )
            db.execute(
                "SELECT call_id, input_json, output_json FROM content_calls LIMIT 0"
            )

    def get(self, job_id: UUID, caller_id: str) -> ContentJob:
        with self._transaction() as db:
            row = db.execute(
                "SELECT response_json FROM content_jobs WHERE job_id=? AND caller_id=?",
                (str(job_id), caller_id),
            ).fetchone()
            if row is None:
                raise StoreError("JOB_NOT_FOUND")
            return ContentJob.model_validate_json(row[0])

    def submit(
        self,
        request: ContentRequest,
        key: UUID,
        caller_id: str,
        *,
        ready: bool,
    ) -> tuple[ContentJob, bool]:
        request_json = canonical_request(request)
        with self._transaction() as db:
            existing = db.execute(
                "SELECT request_json, response_json FROM content_jobs WHERE caller_id=? AND idempotency_key=?",
                (caller_id, str(key)),
            ).fetchone()
            if existing is not None:
                if existing[0] != request_json:
                    raise StoreError("IDEMPOTENCY_CONFLICT")
                return ContentJob.model_validate_json(existing[1]), False
            if not ready:
                raise StoreError("SERVICE_NOT_READY")
            if request.revision is not None:
                self._source(db, request, caller_id)
            if db.execute(
                "SELECT 1 FROM content_jobs WHERE state IN ('queued','running')"
            ).fetchone():
                raise StoreError("SERVICE_BUSY")
            now = timestamp()
            job = ContentJob.model_validate(
                {
                    "job_id": str(uuid4()),
                    "task_id": str(request.task_id),
                    "state": "queued",
                    "created_at": now,
                    "updated_at": now,
                    "result": None,
                    "error": None,
                }
            )
            db.execute(
                "INSERT INTO content_jobs(job_id,caller_id,task_id,idempotency_key,request_json,state,response_json) VALUES(?,?,?,?,?,'queued',?)",
                (
                    str(job.root.job_id),
                    caller_id,
                    str(request.task_id),
                    str(key),
                    request_json,
                    job.model_dump_json(),
                ),
            )
            return job, True

    def _source(
        self, db: sqlite3.Connection, request: ContentRequest, caller_id: str
    ) -> ContentJob:
        row = db.execute(
            "SELECT response_json FROM content_jobs WHERE job_id=? AND task_id=? AND caller_id=? AND state='succeeded'",
            (str(request.revision.source_job_id), str(request.task_id), caller_id),
        ).fetchone()
        if row is None:
            raise StoreError("INVALID_ARGUMENT")
        return ContentJob.model_validate_json(row[0])

    def claim(self) -> tuple[ContentJob, ContentRequest, ContentJob | None] | None:
        with self._transaction() as db:
            row = db.execute(
                "SELECT * FROM content_jobs WHERE state='queued' LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            request = ContentRequest.model_validate_json(row["request_json"])
            source = (
                self._source(db, request, row["caller_id"])
                if request.revision
                else None
            )
            job = self._update(db, row["job_id"], "running")
            return job, request, source

    def _update(
        self,
        db: sqlite3.Connection,
        job_id: str,
        state: str,
        *,
        error: Error | None = None,
        outcome: RunOutcome | None = None,
    ) -> ContentJob:
        row = db.execute(
            "SELECT * FROM content_jobs WHERE job_id=?", (job_id,)
        ).fetchone()
        if row is None or row["state"] in ("succeeded", "failed"):
            raise StoreError("INVALID_TRANSITION")
        if state == "running" and row["state"] != "queued":
            raise StoreError("INVALID_TRANSITION")
        if outcome is not None and (
            row["state"] != "running"
            or str(outcome.context.job_id) != job_id
            or str(outcome.context.task_id) != row["task_id"]
        ):
            raise StoreError("INVALID_TRANSITION")
        body = json.loads(row["response_json"])
        body.update(
            state=state,
            updated_at=max(
                datetime.fromisoformat(body["updated_at"]), datetime.now(UTC)
            ),
            result=outcome.result if outcome else None,
            error=error,
        )
        job = ContentJob.model_validate(body)
        db.execute(
            "UPDATE content_jobs SET state=?,response_json=?,outcome_json=? WHERE job_id=?",
            (
                state,
                job.model_dump_json(),
                outcome.model_dump_json() if outcome else None,
                job_id,
            ),
        )
        return job

    def finish(self, outcome: RunOutcome) -> ContentJob:
        error = None
        if outcome.error is not None:
            code = (
                "CONTENT_REJECTED"
                if outcome.error.code == "CONTENT_REJECTED"
                else "UPSTREAM_FAILED"
            )
            error = Error(
                code=code,
                message="内容检查未通过"
                if code == "CONTENT_REJECTED"
                else "内容能力调用失败，请核实作业记录",
                retryable=False,
                details={},
            )
        with self._transaction() as db:
            return self._update(
                db,
                str(outcome.context.job_id),
                "succeeded" if error is None else "failed",
                error=error,
                outcome=outcome,
            )

    def interrupt_running(self) -> int:
        # 只能由取得进程独占锁的服务在启动/停止时调用。
        with self._transaction() as db:
            rows = db.execute(
                "SELECT job_id FROM content_jobs WHERE state='running'"
            ).fetchall()
            for row in rows:
                self._update(
                    db,
                    row[0],
                    "failed",
                    error=Error(
                        code="EXECUTION_INTERRUPTED",
                        message="执行中断，未自动重新调用模型",
                        retryable=False,
                        details={},
                    ),
                )
            return len(rows)

    def fail_running(self, job_id: UUID) -> None:
        with self._transaction() as db:
            self._update(
                db,
                str(job_id),
                "failed",
                error=Error(
                    code="INTERNAL_ERROR",
                    message="内容执行异常，请通过作业编号核实",
                    retryable=False,
                    details={},
                ),
            )

    def begin_call(
        self, job_id: UUID, stage: str, capability: str, input_json: str
    ) -> UUID:
        call_id = uuid4()
        with self._transaction() as db:
            row = db.execute(
                "SELECT state FROM content_jobs WHERE job_id=?", (str(job_id),)
            ).fetchone()
            if row is None or row[0] != "running":
                raise StoreError("INVALID_TRANSITION")
            db.execute(
                "INSERT INTO content_calls(call_id,job_id,stage,capability,input_json,started_at) VALUES(?,?,?,?,?,?)",
                (str(call_id), str(job_id), stage, capability, input_json, timestamp()),
            )
        return call_id

    def finish_call(self, call_id: UUID, output_json: str) -> None:
        with self._transaction() as db:
            changed = db.execute(
                "UPDATE content_calls SET output_json=?,finished_at=? WHERE call_id=? AND finished_at IS NULL",
                (output_json, timestamp(), str(call_id)),
            ).rowcount
            if changed != 1:
                raise StoreError("INVALID_TRANSITION")

    def pending_vector_calls(self) -> list[dict]:
        """恢复只核对已持久化的入库意图，绝不重新运行生成或审核。"""
        with self._transaction() as db:
            return [dict(row) for row in db.execute(
                "SELECT c.call_id,c.input_json FROM content_calls c JOIN content_jobs j "
                "ON c.job_id=j.job_id WHERE c.stage='vector_store' AND c.finished_at IS NULL"
            ).fetchall()]
