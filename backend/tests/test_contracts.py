"""从调用方的 JSON 边界验证公共契约和跨模块交接。"""

import json
import re
from copy import deepcopy
from pathlib import Path
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.contracts import (
    AssetRef,
    ContentJob,
    ContentRequest,
    ContentResult,
    CreateHeaders,
    ErrorResponse,
    SpeechCapabilities,
    SpeechJob,
    SpeechRequest,
    TaskRequest,
    TaskResponse,
    VideoCapabilities,
    VideoJob,
    VideoRequest,
)
from app.contracts.schema import SCHEMA_DIR, schemas

ROOT = Path(__file__).resolve().parents[2]
TASK_ID = "11111111-1111-4111-8111-111111111111"
JOB_ID = "22222222-2222-4222-8222-222222222222"
NOW = "2026-09-22T03:00:00Z"
ERROR = {"code": "UPSTREAM_FAILED", "message": "上游暂不可用", "retryable": True, "details": {}}
CONTENT = {"script": "给绿萝适量浇水。", "title": "绿萝养护", "tags": [], "review_passed": True}
AUDIO = {
    "path": "tasks/示例人物/audio.wav", "sha256": "a" * 64,
    "size_bytes": 480044, "media_type": "audio/wav",
}
VIDEO = {**AUDIO, "path": "tasks/示例人物/video.mp4", "media_type": "video/mp4"}


def parse(model, payload):
    return model.model_validate_json(json.dumps(payload, ensure_ascii=False))


def job(state="queued", result=None, error=None):
    return {
        "job_id": JOB_ID, "task_id": TASK_ID, "state": state,
        "created_at": NOW, "updated_at": NOW, "result": result, "error": error,
    }


def speech_result():
    return {
        "content_job_id": JOB_ID, "audio": AUDIO, "duration_ms": 10000,
        "codec": "pcm_s16le", "sample_rate_hz": 24000, "channels": 1,
    }


def video_result():
    return {
        "speech_job_id": JOB_ID, "source_audio_sha256": AUDIO["sha256"],
        "video": VIDEO, "duration_ms": 10000, "codec": "h264", "pixel_format": "yuv420p",
        "width": 720, "height": 1280, "fps": "25/1", "has_audio": False, "audio_offset_ms": 0,
    }


def task():
    return {
        "task_id": TASK_ID, "state": "running", "stage": "content",
        "created_at": NOW, "updated_at": NOW,
        "jobs": {"content": JOB_ID, "speech": None, "video": None},
        "final_video": None, "error": None,
        "publication": {
            "state": "not_started", "platform": "douyin", "account_id": "demo-account",
            "submission_id": None, "platform_video_id": None, "published_at": None,
        },
    }


def test_protocol_examples_validate_at_json_boundary():
    text = (ROOT / "docs/HTTP接口协议.md").read_text(encoding="utf-8")
    examples = re.findall(r"```json\s*\n(.*?)\n```", text, re.DOTALL)
    models = [ContentJob, ErrorResponse, ContentRequest, ContentResult,
              SpeechRequest, VideoRequest, TaskRequest, TaskResponse]
    assert len(examples) == len(models)
    for model, example in zip(models, examples, strict=True):
        model.model_validate_json(example)


def test_content_to_audio_to_video_handoff_preserves_text_and_asset():
    content = parse(ContentJob, job("succeeded", CONTENT)).root.result
    speech = parse(SpeechRequest, {
        "task_id": TASK_ID, "content_job_id": JOB_ID, "text": content.script,
        "voice_id": "default", "profile_id": "speech-pcm16-mono-24k-v1",
    })
    assert speech.text == content.script
    response = parse(SpeechJob, job("succeeded", speech_result())).root.result
    video = parse(VideoRequest, {
        "task_id": TASK_ID, "speech_job_id": JOB_ID, "audio": response.audio.model_dump(),
        "avatar_resource_id": "avatar-demo-v1", "profile_id": "portrait-720x1280-25fps-v1",
    })
    assert video.audio.model_dump() == response.audio.model_dump()
    result = parse(VideoJob, job("succeeded", video_result())).root.result
    assert result.source_audio_sha256 == video.audio.sha256


@pytest.mark.parametrize("model,result", [
    (ContentJob, CONTENT), (SpeechJob, speech_result()), (VideoJob, video_result()),
])
def test_job_state_payload_invariants(model, result):
    for state, output, error in [
        ("queued", None, None), ("running", None, None),
        ("succeeded", result, None), ("failed", None, ERROR),
    ]:
        response = parse(model, job(state, output, error))
        assert json.loads(response.model_dump_json())["state"] == state
    for state, output, error in [
        ("queued", result, None), ("running", None, ERROR),
        ("succeeded", None, None), ("succeeded", result, ERROR),
        ("failed", result, ERROR), ("failed", None, None),
    ]:
        with pytest.raises(ValidationError):
            parse(model, job(state, output, error))
    missing_error = job()
    del missing_error["error"]
    with pytest.raises(ValidationError):
        parse(model, missing_error)


def test_unknown_request_fields_rejected_but_response_extensions_ignored():
    with pytest.raises(ValidationError):
        parse(ContentRequest, {"task_id": TASK_ID, "topic": "养花", "topik": "误拼"})
    with pytest.raises(ValidationError):
        parse(VideoRequest, {
            "task_id": TASK_ID, "speech_job_id": JOB_ID, "avatar_resource_id": "demo",
            "profile_id": "demo", "audio": {**AUDIO, "unexpected": True},
        })
    response = parse(SpeechJob, {
        **job("succeeded", {**speech_result(), "new_field": True}), "new_field": True,
    })
    assert "new_field" not in response.model_dump_json()


@pytest.mark.parametrize("topic", ["", "  ", "字" * 2001, 10, None])
def test_invalid_topic(topic):
    with pytest.raises(ValidationError):
        parse(ContentRequest, {"task_id": TASK_ID, "topic": topic})


def test_revision_and_content_review_constraints():
    initial = parse(ContentRequest, {"task_id": TASK_ID, "topic": "养花"})
    assert "revision" not in json.loads(initial.model_dump_json())
    request = parse(ContentRequest, {
        "task_id": TASK_ID, "topic": "  养花  ",
        "revision": {"source_job_id": JOB_ID, "instructions": "缩短口播稿"},
    })
    assert request.topic == "养花"
    for revision in [None, {}, {"source_job_id": JOB_ID, "instructions": "字" * 4001}]:
        with pytest.raises(ValidationError):
            parse(ContentRequest, {"task_id": TASK_ID, "topic": "养花", "revision": revision})
    assert parse(ContentResult, {**CONTENT, "tags": [" 绿萝 ", "绿萝"]}).tags == ["绿萝"]
    for change in [{"review_passed": False}, {"review_passed": 1}, {"tags": ["#绿萝"]}]:
        with pytest.raises(ValidationError):
            parse(ContentResult, {**CONTENT, **change})


@pytest.mark.parametrize("payload", [{"topic": "养花"}, {"task_id": "invalid", "topic": "养花"}])
def test_missing_or_invalid_task_id_rejected(payload):
    with pytest.raises(ValidationError):
        parse(ContentRequest, payload)


@pytest.mark.parametrize("path", [
    "/audio.wav", "C:/audio.wav", "C:audio.wav", "//server/share/audio.wav",
    "a\\audio.wav", "../audio.wav", "a/../audio.wav", "a/./audio.wav",
    "a//audio.wav", "a/", ".", "..", "", "a\x00.wav", "https://example.com/a.wav",
])
def test_unsafe_asset_path_rejected(path):
    with pytest.raises(ValidationError):
        parse(AssetRef, {**AUDIO, "path": path})


@pytest.mark.parametrize("change", [
    {"size_bytes": 0}, {"size_bytes": True}, {"size_bytes": "100"},
    {"sha256": "A" * 64}, {"sha256": "short"},
])
def test_invalid_asset_metadata(change):
    with pytest.raises(ValidationError):
        parse(AssetRef, {**AUDIO, **change})


@pytest.mark.parametrize("change", [
    {"fps": "25"}, {"fps": "25/0"}, {"fps": "0/1"}, {"fps": "-25/1"},
    {"width": 0}, {"height": -1}, {"duration_ms": 0}, {"audio_offset_ms": -1},
    {"has_audio": "false"},
])
def test_invalid_video_metadata(change):
    with pytest.raises(ValidationError):
        parse(VideoJob, job("succeeded", {**video_result(), **change}))


@pytest.mark.parametrize("change", [
    {"sample_rate_hz": 0}, {"channels": 0}, {"duration_ms": -1}, {"sample_rate_hz": "24000"},
])
def test_invalid_audio_metadata(change):
    with pytest.raises(ValidationError):
        parse(SpeechJob, job("succeeded", {**speech_result(), **change}))


@pytest.mark.parametrize("value", [NOW[:-1], "2026-09-22T11:00:00+08:00", 1789999999])
def test_only_utc_timestamps_accepted(value):
    with pytest.raises(ValidationError):
        parse(ContentJob, {**job(), "created_at": value})


def test_timestamps_and_headers():
    with pytest.raises(ValidationError):
        parse(ContentJob, {**job(), "updated_at": "2026-09-21T03:00:00Z"})
    headers = CreateHeaders.model_validate({"Idempotency-Key": TASK_ID})
    assert isinstance(headers.request_id, UUID)
    assert headers.idempotency_key == UUID(TASK_ID)
    for payload in [{}, {"Idempotency-Key": "invalid"}]:
        with pytest.raises(ValidationError):
            CreateHeaders.model_validate(payload)


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -float("inf")])
def test_non_finite_json_rejected_even_in_nested_error_details(value):
    with pytest.raises(ValidationError):
        parse(ErrorResponse, {
            "request_id": TASK_ID, "error": {**ERROR, "details": {"nested": [value]}},
        })


def test_task_publication_and_failure_preserve_final_video():
    payload = task()
    payload["final_video"] = {
        "asset": VIDEO, "duration_ms": 10000, "width": 720, "height": 1280, "fps": "25/1",
    }
    payload.update(state="failed", stage="publication", error=ERROR)
    payload["publication"]["state"] = "failed"
    assert parse(TaskResponse, payload).final_video is not None
    payload.update(state="waiting_external")
    payload["publication"]["state"] = "unknown"
    parse(TaskResponse, payload)
    payload.update(state="succeeded", stage="completed", error=None)
    payload["publication"].update(state="published", published_at=NOW)
    parse(TaskResponse, payload)
    invalid = deepcopy(payload)
    invalid["publication"].update(state="submitted", published_at=None)
    with pytest.raises(ValidationError):
        parse(TaskResponse, invalid)
    payload["final_video"] = None
    with pytest.raises(ValidationError):
        parse(TaskResponse, payload)


@pytest.mark.parametrize("change", [
    {"state": "failed"}, {"state": "waiting_external"}, {"stage": "completed"},
    {"state": "queued", "stage": "speech"}, {"error": ERROR},
])
def test_invalid_task_snapshot(change):
    with pytest.raises(ValidationError):
        parse(TaskResponse, {**task(), **change})


def test_capability_resources_reference_declared_profiles():
    payload = {
        "service": "speech", "api_version": "1.0",
        "profiles": [{
            "profile_id": "speech-pcm16-mono-24k-v1",
            "input_constraints": {"max_text_chars": 2000},
            "output_constraints": {
                "media_type": "audio/wav", "codec": "pcm_s16le",
                "sample_rate_hz": 24000, "channels": 1,
            },
        }],
        "resources": [{
            "resource_id": "default", "display_name": "默认音色", "kind": "voice",
            "profile_ids": ["speech-pcm16-mono-24k-v1"],
        }],
    }
    parse(SpeechCapabilities, payload)
    invalid = deepcopy(payload)
    invalid["profiles"][0]["output_constraints"]["sample_rate_hz"] = 0
    with pytest.raises(ValidationError):
        parse(SpeechCapabilities, invalid)
    payload["resources"][0]["profile_ids"] = ["missing-profile"]
    with pytest.raises(ValidationError):
        parse(SpeechCapabilities, payload)
    with pytest.raises(ValidationError):
        parse(VideoCapabilities, payload)


def test_video_capability_baseline():
    payload = {
        "service": "video", "api_version": "1.0",
        "profiles": [{
            "profile_id": "portrait-720x1280-25fps-v1",
            "input_constraints": {
                "audio_media_types": ["audio/wav"], "audio_codecs": ["pcm_s16le"],
                "sample_rates_hz": [24000], "channels": [1], "max_audio_duration_ms": 30000,
            },
            "output_constraints": {
                "media_type": "video/mp4", "codec": "h264", "pixel_format": "yuv420p",
                "width": 720, "height": 1280, "fps": "25/1", "has_audio": False,
            },
        }],
        "resources": [{
            "resource_id": "avatar-demo-v1", "display_name": "演示人物", "kind": "avatar",
            "profile_ids": ["portrait-720x1280-25fps-v1"],
        }],
    }
    parse(VideoCapabilities, payload)
    payload["profiles"][0]["input_constraints"]["sample_rates_hz"] = []
    with pytest.raises(ValidationError):
        parse(VideoCapabilities, payload)


def test_generated_schemas_are_current_and_references_resolve():
    for name, schema in schemas():
        assert json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8")) == schema

        def check_references(node, schema=schema):
            if isinstance(node, dict):
                if "$ref" in node:
                    target = schema
                    assert node["$ref"].startswith("#/")
                    for part in node["$ref"][2:].split("/"):
                        target = target[part.replace("~1", "/").replace("~0", "~")]
                for value in node.values():
                    check_references(value)
            elif isinstance(node, list):
                for item in node:
                    check_references(item)

        check_references(schema)
