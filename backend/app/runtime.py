import json
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
_data_path = Path(os.environ.get("VIDEO_DATA_DIR", "data"))
DATA = (_data_path if _data_path.is_absolute() else ROOT / _data_path).resolve()


def media_tools():
    config_path = ROOT / "config" / "runtime.local.json"
    config = json.loads(config_path.read_text(encoding="utf-8-sig")) if config_path.exists() else {}
    if not isinstance(config, dict):
        raise TypeError("runtime.local.json must contain a JSON object")
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        value = config.get(name, name)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty executable name or path")
        # Explicit relative paths are relative to the project, not the process cwd.
        if "/" in value or "\\" in value:
            path = Path(value)
            value = str(path if path.is_absolute() else ROOT / path)
        tools[name] = shutil.which(value)
    return tools


@contextmanager
def database():
    DATA.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(DATA / "app.db", timeout=10)
    connection.row_factory = sqlite3.Row
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def initialize():
    with database() as db:
        db.execute("PRAGMA journal_mode=WAL")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS environment_checks (
                id TEXT PRIMARY KEY,
                state TEXT NOT NULL,
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL,
                detail TEXT NOT NULL,
                artifact TEXT
            );
            CREATE TABLE IF NOT EXISTS runtime_status (
                name TEXT PRIMARY KEY,
                heartbeat REAL NOT NULL
            );
        """)


def heartbeat():
    with database() as db:
        db.execute(
            "INSERT OR REPLACE INTO runtime_status VALUES ('worker', ?)", (time.time(),)
        )


def worker_online():
    with database() as db:
        row = db.execute("SELECT heartbeat FROM runtime_status WHERE name='worker'").fetchone()
    return bool(row and time.time() - row[0] < 15)


def create_check():
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        current = db.execute(
            "SELECT * FROM environment_checks WHERE state IN ('queued', 'running') LIMIT 1"
        ).fetchone()
        if current:
            return dict(current)
        check_id = str(uuid.uuid4())
        now = time.time()
        db.execute(
            "INSERT INTO environment_checks VALUES (?, 'queued', ?, ?, ?, NULL)",
            (check_id, now, now, "等待独立 Worker 执行环境自检"),
        )
        return dict(db.execute("SELECT * FROM environment_checks WHERE id=?", (check_id,)).fetchone())


def recover_checks():
    with database() as db:
        db.execute(
            "UPDATE environment_checks SET state='failed', detail=?, updated_at=? WHERE state='running'",
            ("上次环境自检被中断，可重新运行；未调用模型或发布接口", time.time()),
        )


def claim_check():
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute(
            "SELECT * FROM environment_checks WHERE state='queued' ORDER BY created_at LIMIT 1"
        ).fetchone()
        if row:
            db.execute(
                "UPDATE environment_checks SET state='running', detail=?, updated_at=? WHERE id=?",
                ("正在生成测试画面与测试音，再合成 MP4", time.time(), row["id"]),
            )
        return dict(row) if row else None


def finish_check(check_id, state, detail, artifact=None):
    with database() as db:
        db.execute(
            "UPDATE environment_checks SET state=?, detail=?, artifact=?, updated_at=? WHERE id=?",
            (state, detail, artifact, time.time(), check_id),
        )


def checks():
    with database() as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM environment_checks ORDER BY created_at DESC LIMIT 20"
        )]


def get_check(check_id):
    with database() as db:
        row = db.execute("SELECT * FROM environment_checks WHERE id=?", (check_id,)).fetchone()
    return dict(row) if row else None
