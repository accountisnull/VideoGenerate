from concurrent.futures import ThreadPoolExecutor

import pytest
from fastapi.testclient import TestClient

from app import runtime
from app.main import app


@pytest.fixture
def isolated_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "DATA", tmp_path)
    runtime.initialize()
    return tmp_path


def test_duplicate_check_is_one_job_and_claimed_once(isolated_runtime):
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: runtime.create_check(), range(8)))
    assert len({item["id"] for item in results}) == 1
    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(lambda _: runtime.claim_check(), range(4)))
    assert len([item for item in claims if item]) == 1


def test_restart_marks_interrupted_check_failed_preserves_completed(isolated_runtime):
    first = runtime.create_check()
    runtime.claim_check()
    runtime.finish_check(first["id"], "succeeded", "ok", "assets/check.mp4")
    second = runtime.create_check()
    runtime.claim_check()
    runtime.recover_checks()
    assert runtime.get_check(first["id"])["state"] == "succeeded"
    assert runtime.get_check(second["id"])["state"] == "failed"


def test_api_reports_not_ready_and_rejects_offline_worker(isolated_runtime):
    with TestClient(app) as client:
        assert client.get('/api/health').json()['production_ready'] is False
        assert client.post('/api/checks').status_code == 503
        runtime.heartbeat()
        assert client.post('/api/checks').status_code == 202
        assert client.post('/api/checks', headers={'Origin': 'https://example.com'}).status_code == 403
        assert client.get('/api/checks/missing/video').status_code == 404


def test_media_paths_resolve_from_project_not_working_directory(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(
        "FFMPEG_PATH='tools/media files/ffmpeg.exe'\nFFPROBE_PATH=ffprobe\n",
        encoding="utf-8-sig",
    )
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.chdir(tmp_path.parent)
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    monkeypatch.delenv("FFPROBE_PATH", raising=False)
    monkeypatch.setattr(runtime.shutil, "which", lambda value: value)
    assert runtime.media_tools() == {
        "ffmpeg": str(tmp_path / "tools/media files/ffmpeg.exe"), "ffprobe": "ffprobe",
    }


@pytest.mark.parametrize("value", ["FFMPEG_PATH=", "FFMPEG_PATH", "FFMPEG_PATH='  '"])
def test_invalid_media_config_reports_clear_error(tmp_path, monkeypatch, value):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.delenv("FFMPEG_PATH", raising=False)
    (tmp_path / ".env").write_text(value, encoding="utf-8")
    with pytest.raises(ValueError, match="FFMPEG_PATH must be a non-empty"):
        runtime.media_tools()


def test_configuration_defaults_and_environment_overrides(tmp_path, monkeypatch):
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    for name in ("FFMPEG_PATH", "FFPROBE_PATH", "VIDEO_DATA_DIR"):
        monkeypatch.delenv(name, raising=False)
    assert runtime.configuration() == {
        "FFMPEG_PATH": "ffmpeg", "FFPROBE_PATH": "ffprobe", "VIDEO_DATA_DIR": "data",
    }
    (tmp_path / ".env").write_text(
        "FFMPEG_PATH=file-ffmpeg\nFFPROBE_PATH=file-ffprobe\nVIDEO_DATA_DIR=custom-data\n",
        encoding="utf-8",
    )
    assert runtime.configuration()["VIDEO_DATA_DIR"] == "custom-data"
    monkeypatch.setenv("FFMPEG_PATH", "environment-ffmpeg")
    monkeypatch.setenv("VIDEO_DATA_DIR", "environment-data")
    assert runtime.configuration() == {
        "FFMPEG_PATH": "environment-ffmpeg", "FFPROBE_PATH": "file-ffprobe",
        "VIDEO_DATA_DIR": "environment-data",
    }
