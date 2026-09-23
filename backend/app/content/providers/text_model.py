"""用真实 deepagents Skill 加载器及无工具 Agent 发起一次模型请求。"""

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from typing import Protocol

from app import runtime
from app.content.errors import ContentError
from app.content.generation.models import ModelCallRecord, Step

SKILLS_ROOT = Path(__file__).resolve().parents[3] / "skills"


@dataclass(frozen=True)
class ModelReply:
    text: str
    call: ModelCallRecord


class TextModel(Protocol):
    async def generate(self, *, step: Step, system_prompt: str,
                       user_prompt: str) -> ModelReply: ...


def load_generation_skill():
    """预加载可信本地 Skill；避免模型读文件导致额外轮次或工具权限。"""
    from deepagents.backends import FilesystemBackend
    from deepagents.middleware.skills import SkillsMiddleware

    middleware = SkillsMiddleware(
        backend=FilesystemBackend(root_dir=SKILLS_ROOT, virtual_mode=True),
        sources=["/"], system_prompt=None,
    )
    state = middleware.before_agent({"messages": []}, None, {})
    metadata = [item for item in state["skills_metadata"] if item["name"] == "content-generation"]
    if state["skills_load_errors"] or len(metadata) != 1:
        raise ContentError("SKILL_UNAVAILABLE", "内容生成 Skill 加载失败")
    instructions = (SKILLS_ROOT / "content-generation" / "SKILL.md").read_text(encoding="utf-8")
    return middleware, state, instructions


def build_chat_model(config):
    # 生成参数不能覆盖地址、密钥、重试次数或注入工具。
    allowed = {
        "temperature", "top_p", "max_tokens", "max_completion_tokens",
        "frequency_penalty", "presence_penalty", "seed", "stop", "extra_body",
    }
    options = config["generation_options"]
    if options.keys() - allowed:
        raise ContentError("MODEL_CONFIGURATION", "文本生成参数包含不支持的字段")
    extra = options.get("extra_body", {})
    if not isinstance(extra, dict) or extra.keys() - {"enable_thinking", "thinking_budget"}:
        raise ContentError("MODEL_CONFIGURATION", "extra_body 仅支持思考模式配置")
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=config["model"], base_url=config["base_url"], api_key=config["api_key"],
        timeout=config["timeout_seconds"], max_retries=0, n=1, cache=False,
        **options,
    )


class AgentTextModel:
    async def generate(self, *, step: Step, system_prompt: str,
                       user_prompt: str) -> ModelReply:
        try:
            config = runtime.text_model_configuration(step)
            from langchain.agents import create_agent
            from langchain_core.callbacks import AsyncCallbackHandler
            from openai import APITimeoutError, AuthenticationError, BadRequestError

            model = build_chat_model(config)
            # 审核调用者使用自己的系统规则，不注入写稿 Skill。
            middleware, state, instructions = load_generation_skill() if step != "review" else (
                None, {}, "",
            )
            agent = create_agent(
                model, tools=[], middleware=[middleware] if middleware else [],
                system_prompt=system_prompt + "\n" + instructions,
                name="content_" + step,
            )
        except ContentError:
            raise
        except ImportError:
            raise ContentError("MODEL_DEPENDENCY", "content 依赖无法加载，请检查安装和系统 DLL 策略") from None
        except (ValueError, TypeError, OSError):
            raise ContentError("MODEL_CONFIGURATION", "模型配置或 Skill 文件无效，请检查本机配置") from None

        class Counter(AsyncCallbackHandler):
            raise_error = True
            count = 0

            async def on_chat_model_start(self, *args, **kwargs):
                if self.count:
                    raise ContentError("MODEL_CALL_LIMIT", "一次操作只允许一次模型调用")
                self.count += 1

        counter = Counter()
        started = datetime.now(UTC)
        timer = perf_counter()

        def record(status):
            return ModelCallRecord(
                step=step, model=config["model"], started_at=started,
                elapsed_ms=int((perf_counter() - timer) * 1000),
                call_count=counter.count, status=status,
            )

        try:
            async with asyncio.timeout(config["timeout_seconds"]):
                result = await agent.ainvoke(
                    {**state, "messages": [{"role": "user", "content": user_prompt}]},
                    config={"callbacks": [counter], "recursion_limit": 8},
                )
            message = result["messages"][-1]
            if message.type != "ai" or message.tool_calls or not isinstance(message.content, str):
                raise ContentError("MODEL_OUTPUT_INVALID", "模型必须返回单个 JSON 文本结果")
            return ModelReply(message.content, record("succeeded"))
        except (TimeoutError, APITimeoutError):
            raise ContentError("MODEL_TIMEOUT", "文本模型请求超时", retryable=True,
                               call=record("timeout")) from None
        except ContentError as error:
            error.call = record("failed")
            raise
        except (AuthenticationError, BadRequestError):
            raise ContentError("MODEL_REQUEST_REJECTED", "模型拒绝请求，请检查凭据、模型和参数",
                               call=record("failed")) from None
        except Exception:  # noqa: BLE001 -- SDK 边界统一脱敏；取消异常仍向外传播。
            # 不将 HTTP 响应、异常堆栈和凭据交给上层或命令行。
            raise ContentError("MODEL_UPSTREAM_FAILED", "文本模型调用失败", retryable=True,
                               call=record("failed")) from None
