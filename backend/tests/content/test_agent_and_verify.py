"""SDK Skill 加载和联调记录恢复；没有外部网络调用。"""

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from app.content.interfaces import InvalidCapabilityOutput
from app.content.models import Draft
from app.content.verify import load_receipt, verify


def test_skill_loaded_and_no_model_tool_runs():
    pytest.importorskip("deepagents")
    from langchain_core.language_models.fake_chat_models import (
        FakeMessagesListChatModel,
    )
    from langchain_core.messages import AIMessage

    from app.content.providers.deepagents_client import DeepAgentsTextClient

    model = FakeMessagesListChatModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "write_file",
                        "args": {"file_path": "/unexpected", "content": "x"},
                        "id": "call1",
                    }
                ],
            )
        ]
    )
    client = DeepAgentsTextClient(
        lambda schema: model,
        Path(__file__).resolve().parents[2] / "skills/orchestration/SKILL.md",
    )
    with pytest.raises(InvalidCapabilityOutput):
        asyncio.run(client.complete("JSON", "{}", Draft))


def test_receipt_keeps_same_request_and_key_after_post_timeout(tmp_path):
    path = tmp_path / "receipt.json"
    captured = []

    def first(request):
        captured.append(
            (request.headers["Idempotency-Key"], json.loads(request.content))
        )
        raise httpx.ReadTimeout("模拟已受理但回包丢失", request=request)

    assert (
        asyncio.run(
            verify(
                path,
                "http://127.0.0.1:8761",
                "主题",
                token=None,
                wait_seconds=1,
                transport=httpx.MockTransport(first),
            )
        )
        == 2
    )
    job_id = str(uuid4())

    def second(request):
        key, payload = captured[0]
        assert request.headers["Idempotency-Key"] == key
        assert json.loads(request.content) == payload
        return httpx.Response(
            200,
            json={
                "job_id": job_id,
                "task_id": payload["task_id"],
                "state": "succeeded",
                "created_at": "2026-09-23T00:00:00Z",
                "updated_at": "2026-09-23T00:00:01Z",
                "result": {
                    "script": "稿件",
                    "title": "标题",
                    "tags": [],
                    "review_passed": True,
                },
                "error": None,
            },
        )

    assert (
        asyncio.run(
            verify(
                path,
                "http://127.0.0.1:8761",
                None,
                token=None,
                wait_seconds=1,
                transport=httpx.MockTransport(second),
            )
        )
        == 0
    )
    assert json.loads(path.read_text(encoding="utf-8"))["job"]["job_id"] == job_id
    with pytest.raises(ValueError):
        load_receipt(path, "http://127.0.0.1:8761", "不同主题")


@pytest.mark.parametrize("mismatched_task", [False, True])
def test_terminal_receipt_does_not_resubmit(tmp_path, mismatched_task):
    path = tmp_path / "record.json"
    record = load_receipt(path, "http://127.0.0.1:8761", "主题")
    record["job"] = {
        "job_id": str(uuid4()),
        "task_id": str(uuid4()) if mismatched_task else record["request"]["task_id"],
        "state": "failed",
        "created_at": "2026-09-23T00:00:00Z",
        "updated_at": "2026-09-23T00:00:01Z",
        "result": None,
        "error": {
            "code": "CONTENT_REJECTED",
            "message": "未通过",
            "retryable": False,
            "details": {},
        },
    }
    path.write_text(json.dumps(record), encoding="utf-8")

    def unexpected(request):
        pytest.fail("终态不应触发网络调用")

    if mismatched_task:
        with pytest.raises(ValueError, match="与原任务不匹配"):
            asyncio.run(
                verify(
                    path,
                    "http://127.0.0.1:8761",
                    None,
                    token=None,
                    wait_seconds=1,
                    transport=httpx.MockTransport(unexpected),
                )
            )
        return

    assert (
        asyncio.run(
            verify(
                path,
                "http://127.0.0.1:8761",
                None,
                token=None,
                wait_seconds=1,
                transport=httpx.MockTransport(unexpected),
            )
        )
        == 1
    )
