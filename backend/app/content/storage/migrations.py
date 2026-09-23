"""内容数据库逐版迁移；所有 DDL 和版本标记在同一事务中提交。"""

APPLICATION_ID = 1129270868
VERSION = 2
STATEMENTS = (
    """CREATE TABLE content_jobs (
        job_id TEXT PRIMARY KEY, caller_id TEXT NOT NULL, task_id TEXT NOT NULL,
        idempotency_key TEXT NOT NULL, request_json TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('queued','running','succeeded','failed')),
        response_json TEXT NOT NULL, outcome_json TEXT,
        UNIQUE(caller_id, idempotency_key))""",
    """CREATE UNIQUE INDEX content_single_active ON content_jobs((1))
        WHERE state IN ('queued','running')""",
    """CREATE TABLE content_calls (
        call_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES content_jobs(job_id),
        stage TEXT NOT NULL, capability TEXT NOT NULL,
        input_json TEXT NOT NULL, output_json TEXT,
        started_at TEXT NOT NULL, finished_at TEXT)""",
)

# 保留 v1 定义供旧库升级，禁止通过重建表丢弃业务记录。
MIGRATIONS = {
    1: STATEMENTS,
    2: (
        "CREATE INDEX content_calls_by_job ON content_calls(job_id, started_at)",
        """CREATE TRIGGER content_terminal_immutable
            BEFORE UPDATE ON content_jobs
            WHEN OLD.state IN ('succeeded','failed')
            BEGIN SELECT RAISE(ABORT, 'terminal job is immutable'); END""",
        """CREATE TRIGGER content_finished_call_immutable
            BEFORE UPDATE ON content_calls WHEN OLD.finished_at IS NOT NULL
            BEGIN SELECT RAISE(ABORT, 'finished call is immutable'); END""",
    ),
}
