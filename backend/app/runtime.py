import json
import math
import os
import shutil
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from dotenv import dotenv_values
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parents[2]


def environment_values():
    return {**dotenv_values(ROOT / ".env", encoding="utf-8-sig"), **os.environ}


def configuration():
    values = environment_values()
    defaults = {"FFMPEG_PATH": "ffmpeg", "FFPROBE_PATH": "ffprobe", "VIDEO_DATA_DIR": "data"}
    config = {}
    for name, default in defaults.items():
        value = values.get(name, default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty executable name or path")
        config[name] = value
    return config


def text_model_configuration(step):
    """按需读取指定步骤的模型配置；未配置模型不影响媒体自检。"""
    if step not in ("writing", "review", "revision"):
        raise ValueError("Unknown text model step; expected writing, review or revision")
    values = environment_values()
    prefix = f"TEXT_{step.upper()}_"

    def required(name, default=""):
        value = values.get(name, default)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} is required for text model {step}")
        return value.strip()

    base_url = required(prefix + "BASE_URL")
    model = required(prefix + "MODEL")
    api_key_env = required(prefix + "API_KEY_ENV", "DASHSCOPE_API_KEY")
    api_key = SecretStr(required(api_key_env))
    try:
        timeout = float(values.get(prefix + "TIMEOUT_SECONDS", "60"))
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise ValueError(f"{prefix}TIMEOUT_SECONDS must be a positive finite number") from None
    try:
        options = json.loads(values.get(prefix + "GENERATION_OPTIONS", "{}"))
        if not isinstance(options, dict):
            raise TypeError
    except (TypeError, ValueError):
        raise ValueError(f"{prefix}GENERATION_OPTIONS must be a JSON object") from None
    return {
        "base_url": base_url, "model": model, "api_key_env": api_key_env,
        "api_key": api_key, "timeout_seconds": timeout, "generation_options": options,
    }


_data_path = Path(configuration()["VIDEO_DATA_DIR"])
DATA = (_data_path if _data_path.is_absolute() else ROOT / _data_path).resolve()


def media_tools():
    config = configuration()
    tools = {}
    for name in ("ffmpeg", "ffprobe"):
        value = config[f"{name.upper()}_PATH"]
        # 显式相对路径以项目根目录为基准，不受进程工作目录影响。
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
