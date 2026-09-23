"""实际 deepagents 图与 Skill 加载；每次能力调用只允许一次模型响应。"""

import re
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

from deepagents import create_deep_agent
from deepagents.backends import StateBackend
from deepagents.backends.utils import create_file_data
from langchain.agents.middleware import AgentMiddleware
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult

from ..interfaces import InvalidCapabilityOutput
from ..json_data import strict_json
from ..models import InternalModel
from .bailian import BailianTextClient

Model = TypeVar("Model", bound=InternalModel)
class NoToolsMiddleware(AgentMiddleware):
    def __init__(self, required_skills: tuple[str, ...]) -> None:
        super().__init__()
        self.required_skills = required_skills

    async def awrap_model_call(self, request, handler):
        loaded = {item["name"] for item in request.state.get("skills_metadata", [])}
        if not set(self.required_skills).issubset(loaded):
            raise InvalidCapabilityOutput()
        # 模型不负责分发收费调用，避免图内重试绕过主管的两轮上限。
        response = await handler(request.override(tools=[]))
        if any(
            isinstance(message, AIMessage)
            and (message.tool_calls or message.invalid_tool_calls)
            for message in response.result
        ):
            raise InvalidCapabilityOutput()
        return response


class BailianChatModel(BaseChatModel):
    client: BailianTextClient
    content_schema: type[InternalModel]

    @property
    def _llm_type(self) -> str:
        return "content-bailian"

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        raise NotImplementedError("内容服务仅支持异步模型调用")

    async def _agenerate(self, messages, stop=None, run_manager=None, **kwargs):
        system = "\n".join(
            str(message.content) for message in messages if message.type == "system"
        )
        users = [message for message in messages if message.type == "human"]
        if len(users) != 1:
            raise InvalidCapabilityOutput()
        output = await self.client.complete(
            system, str(users[0].content), self.content_schema
        )
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=output.model_dump_json()))
            ]
        )


class DeepAgentsTextClient:
    def __init__(
        self,
        model_factory: Callable[[type[InternalModel]], BaseChatModel],
        skill_path: Path,
        *,
        additional_skill_paths: tuple[Path, ...] = (),
    ) -> None:
        self.model_factory = model_factory
        self.skills: dict[str, str] = {}
        for path in (skill_path, *additional_skill_paths):
            content = path.read_text(encoding="utf-8")
            header = re.match(r"\A---\n(.*?)\n---(?:\n|$)", content, re.DOTALL)
            name = re.search(r"^name: ([a-z][a-z0-9-]*)$", header[1], re.MULTILINE) if header else None
            if name is None or name[1] in self.skills:
                raise ValueError("Skill 名称缺失或重复")
            self.skills[name[1]] = content
        if "orchestration" not in self.skills:
            raise ValueError("必须加载主管 Skill")

    async def complete(self, system: str, data: str, schema: type[Model]) -> Model:
        graph = create_deep_agent(
            model=self.model_factory(schema),
            backend=StateBackend(),
            skills=["/skills/"],
            system_prompt="\n\n".join(self.skills.values()) + "\n\n当前能力要求：\n" + system,
            middleware=[NoToolsMiddleware(tuple(self.skills))],
        )
        result = await graph.ainvoke(
            {
                "messages": [{"role": "user", "content": data}],
                "files": {
                    f"/skills/{name}/SKILL.md": create_file_data(content)
                    for name, content in self.skills.items()
                },
            },
            config={"recursion_limit": 6},
        )
        responses = [
            message for message in result["messages"] if isinstance(message, AIMessage)
        ]
        if len(responses) != 1:
            raise InvalidCapabilityOutput()
        message = responses[0]
        if (
            message.tool_calls
            or message.invalid_tool_calls
            or not isinstance(message.content, str)
            or message.response_metadata.get("finish_reason", "stop") != "stop"
        ):
            raise InvalidCapabilityOutput()
        try:
            return schema.model_validate(strict_json(message.content))
        except (ValueError, TypeError, RecursionError):
            raise InvalidCapabilityOutput() from None
