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
    import json

    (tmp_path / "config").mkdir()
    (tmp_path / "config/runtime.local.json").write_text(json.dumps({
        "ffmpeg": "tools/media/ffmpeg.exe", "ffprobe": "ffprobe",
    }), encoding="utf-8-sig")
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    monkeypatch.setattr(runtime.shutil, "which", lambda value: value)
    assert runtime.media_tools() == {
        "ffmpeg": str(tmp_path / "tools/media/ffmpeg.exe"), "ffprobe": "ffprobe",
    }


def test_invalid_media_config_reports_clear_error(tmp_path, monkeypatch):
    (tmp_path / "config").mkdir()
    config = tmp_path / "config/runtime.local.json"
    monkeypatch.setattr(runtime, "ROOT", tmp_path)
    config.write_text('[]')
    with pytest.raises(TypeError, match="JSON object"):
        runtime.media_tools()
    config.write_text('{"ffmpeg": null}')
    with pytest.raises(ValueError, match="non-empty"):
        runtime.media_tools()
