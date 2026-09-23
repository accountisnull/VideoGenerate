"""旧库无损升级、原子受理、记录查询和备份的行为验证。"""

import sqlite3
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import pytest

from app.content.models import RunContext, RunFailure, RunOutcome
from app.content.storage import ContentStore, StoreError, migrations
from app.contracts import ContentRequest


@pytest.fixture
def store(tmp_path):
    instance = ContentStore(tmp_path / "content.db")
    instance.initialize()
    return instance


def submit(store, key=None, topic="绿萝养护"):
    request = ContentRequest(task_id=uuid4(), topic=topic)
    job, _ = store.submit(request, key or uuid4(), "caller", ready=True)
    return job, request


def test_migration_preserves_v1_job_and_call(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        for sql in migrations.STATEMENTS:
            db.execute(sql)
        db.execute(f"PRAGMA application_id={migrations.APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
    old = ContentStore(path)
    job, request = submit(old)
    old.claim()
    call_id = old.begin_call(job.root.job_id, "generation_1", "generator", "{}")
    old.initialize()
    found = old.inspect(job.root.job_id, "caller")
    assert found.request == request
    assert found.job.root.state == "running"
    assert found.calls[0].call_id == call_id
    assert found.calls[0].output_json is None
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 2
    old.initialize()
    assert old.inspect(job.root.job_id, "caller") == found


def test_failed_migration_rolls_back_all_changes(tmp_path, monkeypatch):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as db:
        for sql in migrations.STATEMENTS:
            db.execute(sql)
        db.execute(f"PRAGMA application_id={migrations.APPLICATION_ID}")
        db.execute("PRAGMA user_version=1")
    store = ContentStore(path)
    job, _ = submit(store)
    monkeypatch.setitem(migrations.MIGRATIONS, 2, (
        "CREATE TABLE migration_should_rollback(value TEXT)", "INVALID SQL",
    ))
    with pytest.raises(sqlite3.DatabaseError):
        store.initialize()
    with sqlite3.connect(path) as db:
        assert db.execute("PRAGMA user_version").fetchone()[0] == 1
        assert db.execute("SELECT name FROM sqlite_master WHERE name='migration_should_rollback'").fetchone() is None
    assert store.get(job.root.job_id, "caller") == job


def test_concurrent_replay_conflict_and_caller_isolation(store):
    request, key = ContentRequest(task_id=uuid4(), topic="绿萝"), uuid4()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: store.submit(request, key, "caller", ready=True), range(16)))
    assert sum(created for _, created in results) == 1
    assert len({job.root.job_id for job, _ in results}) == 1
    with pytest.raises(StoreError, match="IDEMPOTENCY_CONFLICT"):
        store.submit(ContentRequest(task_id=request.task_id, topic="不同请求"), key, "caller", ready=True)
    with pytest.raises(StoreError, match="JOB_NOT_FOUND"):
        store.inspect(results[0][0].root.job_id, "other")


def test_terminal_job_and_finished_call_cannot_be_overwritten(store):
    job, request = submit(store)
    store.claim()
    call_id = store.begin_call(job.root.job_id, "generation_1", "generator", "{}")
    store.finish_call(call_id, '{"code":"UPSTREAM_FAILED"}')
    outcome = RunOutcome(
        context=RunContext(task_id=request.task_id, job_id=job.root.job_id, topic=request.topic),
        result=None, error=RunFailure(code="GENERATION_FAILED"), retrievals=(), rounds=(),
    )
    final = store.finish(outcome)
    with pytest.raises(StoreError, match="INVALID_TRANSITION"):
        store.finish(outcome)
    with pytest.raises(StoreError, match="INVALID_TRANSITION"):
        store.finish_call(call_id, "{}")
    with pytest.raises(sqlite3.IntegrityError), sqlite3.connect(store.path) as db:
        db.execute("UPDATE content_jobs SET state='queued' WHERE job_id=?", (str(job.root.job_id),))
    assert store.inspect(job.root.job_id, "caller").outcome == outcome
    assert store.get(job.root.job_id, "caller") == final


def test_backup_is_readable_and_never_overwrites(store, tmp_path):
    job, _ = submit(store)
    backup = tmp_path / "backup.db"
    store.backup(backup)
    restored = ContentStore(backup)
    restored.initialize()
    assert restored.get(job.root.job_id, "caller") == job
    with pytest.raises(FileExistsError):
        store.backup(backup)
    with pytest.raises(ValueError):
        store.backup(store.path)


@pytest.mark.parametrize("payload", ["{", "[]", '{"value":NaN}', '{"a":1,"a":2}'])
def test_call_journal_rejects_invalid_json_before_write(store, payload):
    job, _ = submit(store)
    store.claim()
    with pytest.raises((TypeError, ValueError)):
        store.begin_call(job.root.job_id, "generation_1", "generator", payload)
    assert store.inspect(job.root.job_id, "caller").calls == ()
