import asyncio
import json

import httpx
import pytest
from pydantic import SecretStr

from app.content.errors import ContentError
from app.content.generation.models import GenerationRequest
from app.content.generation.service import ContentGenerator
from app.content.providers import text_model


@pytest.fixture
def configuration(monkeypatch):
    calls = []

    def config(step):
        calls.append(step)
        return {"model": step + "-model", "base_url": "https://model.invalid/v1",
                "api_key": SecretStr("private-test-key"), "timeout_seconds": 2.0,
                "generation_options": {"temperature": 0.3, "extra_body": {"enable_thinking": False}}}

    monkeypatch.setattr(text_model.runtime, "text_model_configuration", config)
    return calls


def wire_transport(monkeypatch, handler):
    """保留真实 ChatOpenAI、Agent 和 Skill 中间件，只替换 HTTP 传输。"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError:
        pytest.skip("ChatOpenAI/tiktoken 不可加载：安装 content 依赖并检查系统 DLL 策略；此项未验证")

    built = []

    def factory(**kwargs):
        model = ChatOpenAI(http_async_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
                           **kwargs)
        built.append(model)
        return model

    monkeypatch.setattr("langchain_openai.ChatOpenAI", factory)
    return built


def completion(content):
    return httpx.Response(200, json={
        "id": "mock-completion", "object": "chat.completion", "created": 1,
        "model": "test-model", "choices": [{"index": 0, "finish_reason": "stop",
                                               "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
    })


def test_real_sdk_loads_skill_and_generates_via_single_http_request(monkeypatch, configuration, payload):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return completion(json.dumps(payload))

    built = wire_transport(monkeypatch, handler)
    result = asyncio.run(ContentGenerator().generate(GenerationRequest(topic="养绿萝")))
    assert result.draft.script == payload["script"]
    assert configuration == ["writing"]
    assert result.model_call.call_count == len(requests) == 1
    assert built[0].max_retries == 0
    assert "tools" not in requests[0]
    assert requests[0]["model"] == "writing-model"
    assert requests[0]["enable_thinking"] is False
    assert "100—200" in requests[0]["messages"][0]["content"]
    assert "material_ids" in requests[0]["messages"][0]["content"]
    assert "review_passed" not in result.model_dump_json()


def test_skill_metadata_loaded_by_installed_deepagents():
    _, state, instructions = text_model.load_generation_skill()
    assert any(item["name"] == "content-generation" for item in state["skills_metadata"])
    assert not state["skills_load_errors"]
    assert "previous" in instructions


@pytest.mark.parametrize("step", ["revision", "review"])
def test_shared_adapter_routes_step_without_adding_writing_rules_to_review(
    monkeypatch, configuration, payload, step,
):
    requests = []

    def handler(request):
        requests.append(json.loads(request.content))
        return completion(json.dumps(payload))

    wire_transport(monkeypatch, handler)
    reply = asyncio.run(text_model.AgentTextModel().generate(step=step, system_prompt="测试系统规则",
                                                           user_prompt="测试输入"))
    assert configuration == [step]
    assert requests[0]["model"] == step + "-model"
    assert reply.call.call_count == len(requests) == 1
    if step == "review":
        assert "100—200" not in requests[0]["messages"][0]["content"]


@pytest.mark.parametrize(("status", "code", "retryable"), [
    (401, "MODEL_REQUEST_REJECTED", False), (400, "MODEL_REQUEST_REJECTED", False),
    (429, "MODEL_UPSTREAM_FAILED", True), (500, "MODEL_UPSTREAM_FAILED", True),
])
def test_http_errors_have_no_retries_and_do_not_leak_secrets(
    monkeypatch, configuration, status, code, retryable,
):
    attempts = []

    def handler(request):
        attempts.append(request)
        return httpx.Response(status, json={"error": {"message": "private-test-key upstream body"}})

    wire_transport(monkeypatch, handler)
    with pytest.raises(ContentError) as caught:
        asyncio.run(ContentGenerator().generate(GenerationRequest(topic="主题")))
    error = caught.value
    assert error.code == code and error.retryable is retryable
    assert error.call.call_count == len(attempts) == 1
    assert "private-test-key" not in json.dumps(error.as_dict())
    assert "upstream body" not in str(error)


def test_timeout_has_one_attempt_and_no_secret_leak(monkeypatch, configuration):
    attempts = []

    def handler(request):
        attempts.append(request)
        raise httpx.ReadTimeout("private-test-key", request=request)

    wire_transport(monkeypatch, handler)
    with pytest.raises(ContentError) as caught:
        asyncio.run(ContentGenerator().generate(GenerationRequest(topic="主题")))
    assert caught.value.code == "MODEL_TIMEOUT"
    assert caught.value.call.status == "timeout"
    assert caught.value.call.call_count == len(attempts) == 1


def test_total_deadline_interrupts_slow_request(monkeypatch, configuration):
    original = text_model.runtime.text_model_configuration

    def config(step):
        return {**original(step), "timeout_seconds": 0.15}

    monkeypatch.setattr(text_model.runtime, "text_model_configuration", config)

    async def handler(request):
        await asyncio.sleep(2)
        raise AssertionError("整体超时应在此之前取消")

    wire_transport(monkeypatch, handler)
    with pytest.raises(ContentError) as caught:
        asyncio.run(ContentGenerator().generate(GenerationRequest(topic="主题")))
    assert caught.value.code == "MODEL_TIMEOUT"
    assert caught.value.call.elapsed_ms < 1500


@pytest.mark.parametrize("options", [{"max_retries": 4}, {"base_url": "https://other.invalid"},
                                    {"extra_body": {"tools": []}}, {"extra_body": []}])
def test_options_cannot_override_connection_or_call_limits(monkeypatch, configuration, options):
    original = text_model.runtime.text_model_configuration
    monkeypatch.setattr(text_model.runtime, "text_model_configuration",
                        lambda step: {**original(step), "generation_options": options})
    with pytest.raises(ContentError) as caught:
        asyncio.run(ContentGenerator().generate(GenerationRequest(topic="主题")))
    assert caught.value.code == "MODEL_CONFIGURATION"
    assert caught.value.call is None


def test_missing_configuration_fails_before_call_without_raw_values(monkeypatch):
    def invalid(step):
        raise ValueError("private-test-key")

    monkeypatch.setattr(text_model.runtime, "text_model_configuration", invalid)
    with pytest.raises(ContentError) as caught:
        asyncio.run(ContentGenerator().generate(GenerationRequest(topic="主题")))
    assert caught.value.code == "MODEL_CONFIGURATION"
    assert caught.value.call is None
    assert "private-test-key" not in str(caught.value)


def test_agent_and_skill_execute_one_model_turn_without_chatopenai(monkeypatch, configuration, payload):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    model = FakeListChatModel(responses=[json.dumps(payload)])
    monkeypatch.setattr(text_model, "build_chat_model", lambda config: model)
    result = asyncio.run(ContentGenerator().generate(GenerationRequest(topic="养绿萝")))
    assert result.draft.script == payload["script"]
    assert result.model_call.call_count == 1
    assert result.model_call.status == "succeeded"


def test_agent_propagates_sdk_timeout_and_records_attempt(monkeypatch, configuration):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel
    from openai import APITimeoutError

    class TimeoutModel(FakeListChatModel):
        async def _agenerate(self, *args, **kwargs):
            raise APITimeoutError(request=httpx.Request("POST", "https://example.invalid"))

    monkeypatch.setattr(text_model, "build_chat_model", lambda config: TimeoutModel(responses=["unused"]))
    with pytest.raises(ContentError) as caught:
        asyncio.run(ContentGenerator().generate(GenerationRequest(topic="养绿萝")))
    assert caught.value.code == "MODEL_TIMEOUT"
    assert caught.value.call.call_count == 1


def test_agent_total_deadline_with_offline_model(monkeypatch, configuration):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    class SlowModel(FakeListChatModel):
        async def _agenerate(self, *args, **kwargs):
            await asyncio.sleep(5)
            raise AssertionError("超时未生效")

    original = text_model.runtime.text_model_configuration
    monkeypatch.setattr(text_model.runtime, "text_model_configuration",
                        lambda step: {**original(step), "timeout_seconds": 0.15})
    monkeypatch.setattr(text_model, "build_chat_model", lambda config: SlowModel(responses=["unused"]))
    with pytest.raises(ContentError) as caught:
        asyncio.run(ContentGenerator().generate(GenerationRequest(topic="养绿萝")))
    assert caught.value.code == "MODEL_TIMEOUT"
    assert caught.value.call.call_count == 1
    assert caught.value.call.elapsed_ms < 1500


def test_cancellation_propagates_to_orchestrator(monkeypatch, configuration):
    from langchain_core.language_models.fake_chat_models import FakeListChatModel

    async def scenario():
        started = asyncio.Event()

        class SlowModel(FakeListChatModel):
            async def _agenerate(self, *args, **kwargs):
                started.set()
                await asyncio.sleep(5)
                raise AssertionError("编排层取消应当中止等待")

        monkeypatch.setattr(text_model, "build_chat_model", lambda config: SlowModel(responses=["unused"]))
        task = asyncio.create_task(ContentGenerator().generate(GenerationRequest(topic="养绿萝")))
        await asyncio.wait_for(started.wait(), timeout=2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(scenario())
