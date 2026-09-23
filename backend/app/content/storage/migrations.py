"""内容数据库版本 1；升级在同一事务中完成，禁止接管其他数据库。"""

APPLICATION_ID = 1129270868
VERSION = 1
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
