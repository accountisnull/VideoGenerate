"""在组装边界选择真实供应商，配置校验不发起模型请求。"""

from collections.abc import Mapping

import httpx

from app import runtime

from .interfaces import Retriever, Reviewer
from .orchestrator import ContentOrchestrator
from .providers.bailian import (
    BailianTextClient,
    TextGenerator,
    TextModelSettings,
)
from .retrieval.agent import create_trending_agent
from .review.audit import AuditReviewer
from .review.keywords import KeywordReviewer, load_rules
from .settings import ContentSettings


class ContentConfigurationError(Exception):
    def __init__(self) -> None:
        super().__init__(
            "内容能力配置无效，请检查三组 TEXT_* 配置和 CONTENT_RULES_PATH"
        )


def build_orchestrator(
    settings: ContentSettings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    retrievers: Mapping[str, Retriever] | None = None,
    additional_reviews: Mapping[str, Reviewer] | None = None,
) -> ContentOrchestrator | None:
    if settings.provider == "disabled":
        return None
    try:
        if settings.rules_path is None:
            raise ValueError("缺少规则文件")
        rules = load_rules(settings.rules_path)
        clients = {}
        for step in ("writing", "review", "revision"):
            values = runtime.text_model_configuration(step)
            values.pop("api_key_env")
            clients[step] = BailianTextClient(
                TextModelSettings.model_validate(values), transport=transport
            )
    except (ValueError, OSError, TypeError, RecursionError):
        raise ContentConfigurationError() from None
    completions = dict(clients)
    if settings.engine == "deepagents":
        try:
            from .providers.deepagents_client import (
                BailianChatModel,
                DeepAgentsTextClient,
            )

            skill_path = runtime.ROOT / "backend/skills/orchestration/SKILL.md"
            completions = {
                step: DeepAgentsTextClient(
                    lambda schema, client=client: BailianChatModel(
                        client=client, content_schema=schema
                    ),
                    skill_path,
                    additional_skill_paths=(
                        runtime.ROOT / "backend/skills"
                        / ("content-audit" if step == "review" else "content-generation")
                        / "SKILL.md",
                    ),
                )
                for step, client in clients.items()
            }
        except (ImportError, ValueError, OSError):
            raise ContentConfigurationError() from None
    active_retrievers = dict(retrievers or {})
    if settings.trending.enabled and "trending" not in active_retrievers:
        active_retrievers["trending"] = create_trending_agent(settings.trending)
    return ContentOrchestrator(
        generator=TextGenerator(
            completions["writing"], completions["revision"], rules.prompt()
        ),
        keywords=KeywordReviewer(rules),
        content_review=AuditReviewer(
            completions["review"], rules, model_version=clients["review"].settings.model
        ),
        retrievers=active_retrievers,
        additional_reviews=additional_reviews,
        call_timeout_seconds=max(
            client.settings.timeout_seconds for client in clients.values()
        ),
    )
