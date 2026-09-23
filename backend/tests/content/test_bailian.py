"""真实适配协议的离线验证，不使用本机凭据或发送收费请求。"""

import asyncio
import json
import time
from pathlib import Path
from uuid import UUID, uuid4

import httpx
import pytest
from fastapi.testclient import TestClient

from app import runtime
from app.content.api import create_app
from app.content.bootstrap import ContentConfigurationError, build_orchestrator
from app.content.interfaces import CapabilityError
from app.content.models import (
    Draft,
    Material,
    RetrievalResult,
    ReviewDecision,
    ReviewInput,
    RunContext,
)
from app.content.providers.bailian import (
    BailianTextClient,
    GenerationOptions,
    InvalidModelOutput,
    TextModelSettings,
)
from app.content.review.keywords import KeywordReviewer, load_rules
from app.content.settings import ContentSettings
from app.content.storage import ContentStore


def model_settings(**kwargs):
    return TextModelSettings(
        base_url="https://model.example.test/compatible-mode/v1",
        model="test-model",
        api_key="test-only-key",
        timeout_seconds=1,
        **kwargs,
    )


def completion(content, finish_reason="stop"):
    return httpx.Response(
        200,
        json={
            "choices": [
                {"finish_reason": finish_reason, "message": {"content": content}}
            ]
        },
    )


@pytest.fixture
def configured(tmp_path, monkeypatch):
    values = {}
    for step in ("WRITING", "REVIEW", "REVISION"):
        values.update(
            {
                f"TEXT_{step}_BASE_URL": "https://model.example.test/v1",
                f"TEXT_{step}_MODEL": step.lower(),
                f"TEXT_{step}_API_KEY_ENV": f"{step}_KEY",
                f"{step}_KEY": f"test-{step}-secret",
            }
        )
    monkeypatch.setattr(runtime, "environment_values", lambda: values)
    example = Path(__file__).resolve().parents[3] / "config/rules/content.example.json"
    rules = tmp_path / "rules.json"
    rules.write_text(example.read_text(encoding="utf-8"), encoding="utf-8")
    settings = ContentSettings(
        database=tmp_path / "content.db",
        asset_root=tmp_path / "assets",
        provider="bailian",
        rules_path=rules,
    )
    return settings, values


def test_enabled_assembly_requires_models_and_explicit_rules(configured):
    settings, values = configured
    assert build_orchestrator(settings) is not None
    values.pop("REVIEW_KEY")
    with pytest.raises(ContentConfigurationError):
        build_orchestrator(settings)
    assert (
        build_orchestrator(settings.model_copy(update={"provider": "disabled"})) is None
    )
    with pytest.raises(ContentConfigurationError):
        build_orchestrator(settings.model_copy(update={"rules_path": None}))


@pytest.mark.parametrize("engine", ["http", "deepagents"])
def test_http_job_uses_real_adapters_and_revises_once(configured, engine):
    if engine == "deepagents":
        pytest.importorskip("deepagents")
    settings, _ = configured
    settings = settings.model_copy(update={"engine": engine})
    calls = []
    review_count = 0
    good_script = "绿萝养护先观察光照和土壤，避免频繁浇水。" * 6

    def provider(request):
        nonlocal review_count
        body = json.loads(request.content)
        if settings.engine == "deepagents":
            expected_skill = "content-audit" if body["model"] == "review" else "content-generation"
            assert f"name: {expected_skill}" in body["messages"][0]["content"]
            assert "name: orchestration" in body["messages"][0]["content"]
        calls.append(body)
        assert request.url.path == "/v1/chat/completions"
        assert (
            request.headers["Authorization"]
            == f"Bearer test-{body['model'].upper()}-secret"
        )
        assert body["stream"] is False
        assert body["response_format"] == {"type": "json_object"}
        if body["model"] == "review":
            review_count += 1
            return completion(json.dumps({"passed": True, "issues": []}))
        draft = {
            "script": "保证涨薪" + good_script
            if body["model"] == "writing"
            else good_script,
            "title": "绿萝养护",
            "tags": ["养花"],
        }
        if body["model"] == "revision":
            data = json.loads(body["messages"][1]["content"])
            assert data["previous_draft"]["script"].startswith("保证涨薪")
            assert (
                data["feedback"][0]["decision"]["issues"][0]["code"] == "KEYWORD_MATCH"
            )
            assert data["context"]["topic"] == "绿萝养护"
        return completion(json.dumps(draft, ensure_ascii=False))

    engine = build_orchestrator(settings, transport=httpx.MockTransport(provider))
    with TestClient(create_app(settings, engine)) as client:
        response = client.post(
            "/v1/content/jobs",
            json={"task_id": str(uuid4()), "topic": "绿萝养护"},
            headers={"Idempotency-Key": str(uuid4())},
        )
        assert response.status_code == 202
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            body = client.get(response.headers["Location"]).json()
            if body["state"] in ("succeeded", "failed"):
                break
            time.sleep(0.01)
        assert body["state"] == "succeeded"
        assert body["result"]["script"] == good_script
    assert [c["model"] for c in calls] == ["writing", "review", "revision", "review"]
    assert review_count == 2
    # 元数据须经过主管的基础模型校验并持久化，不能在适配边界丢失。
    record = ContentStore(settings.database).inspect(UUID(body["job_id"]), "local-worker")
    assert all(
        review.decision.audit.model_version == "review"
        for round_record in record.outcome.rounds
        for review in round_record.reviews
        if review.capability == "content"
    )


def test_member_extensions_receive_materials_and_fail_closed(configured):
    settings, _ = configured
    checked = []

    class Search:
        async def retrieve(self, context):
            return RetrievalResult(
                materials=(
                    Material(title=context.topic, source="测试来源", url=None),
                )
            )

    class Similarity:
        async def review(self, request):
            checked.append(request.draft)
            raise CapabilityError()

    def provider(request):
        body = json.loads(request.content)
        if body["model"] == "review":
            return completion(json.dumps({"passed": True, "issues": []}))
        assert body["model"] == "writing"
        payload = json.loads(body["messages"][1]["content"])
        assert payload["materials"][0]["source"] == "测试来源"
        return completion(
            json.dumps({"script": "绿萝养护先观察土壤。" * 20, "title": "养护", "tags": []})
        )

    engine = build_orchestrator(
        settings,
        transport=httpx.MockTransport(provider),
        retrievers={"search": Search()},
        additional_reviews={"similarity": Similarity()},
    )
    outcome = asyncio.run(
        engine.run(RunContext(task_id=uuid4(), job_id=uuid4(), topic="绿萝养护"))
    )
    assert len(checked) == 1
    assert outcome.result is None
    assert outcome.error.code == "REVIEW_FAILED"
    assert outcome.error.causes[0].capability == "similarity"
    assert len(outcome.rounds) == 1


@pytest.mark.parametrize(
    "content,finish",
    [
        ('```json\n{"passed":true,"issues":[]}\n```', "stop"),
        ('{"passed":true,"issues":[]}', "length"),
        ('{"passed":"true","issues":[]}', "stop"),
        ('{"passed":true,"passed":false,"issues":[]}', "stop"),
        ('{"passed":true}', "stop"),
        ('{"passed":true,"issues":[],"score":NaN}', "stop"),
    ],
)
def test_invalid_or_truncated_output_never_passes(content, finish):
    calls = []

    def handler(request):
        calls.append(request)
        return completion(content, finish)

    client = BailianTextClient(model_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(InvalidModelOutput):
        asyncio.run(client.complete("JSON", "{}", ReviewDecision))
    assert len(calls) == 1


@pytest.mark.parametrize("status", [401, 429, 500, 302])
def test_http_failures_do_not_retry_or_expose_response(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            status,
            text="private-provider-response",
            headers={"Location": "https://other.example"},
        )

    client = BailianTextClient(model_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(CapabilityError) as captured:
        asyncio.run(client.complete("JSON", "{}", Draft))
    assert "private" not in str(captured.value)
    assert len(calls) == 1


def test_timeout_is_sanitized():
    def handler(request):
        raise httpx.ReadTimeout("secret-timeout", request=request)

    client = BailianTextClient(model_settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(TimeoutError) as captured:
        asyncio.run(client.complete("JSON", "{}", Draft))
    assert "secret" not in str(captured.value)


@pytest.mark.parametrize(
    "options",
    [
        {"model": "override"},
        {"messages": []},
        {"stream": True},
        {"temperature": True},
        {"top_p": 0},
        {"max_tokens": -1},
        {"temperature": float("nan")},
        {"unknown": 1},
    ],
)
def test_options_cannot_override_call_contract(options):
    with pytest.raises(ValueError):
        GenerationOptions.model_validate(options)


def test_rules_apply_to_title_tags_and_length(configured):
    settings, _ = configured
    reviewer = KeywordReviewer(load_rules(settings.rules_path))
    draft = Draft(script="太短", title="包过", tags=("加我微信",))
    result = asyncio.run(
        reviewer.review(
            ReviewInput(
                context=RunContext(task_id=uuid4(), job_id=uuid4(), topic="主题"),
                round_number=1,
                draft=draft,
            )
        )
    )
    assert not result.passed
    assert {i.field for i in result.issues} == {"script", "title", "tags"}


def test_invalid_rules_block_assembly(configured):
    settings, _ = configured
    settings.rules_path.write_text('{"version":"empty"}', encoding="utf-8")
    with pytest.raises(ContentConfigurationError):
        build_orchestrator(settings)
