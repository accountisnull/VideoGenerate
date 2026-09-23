"""六人成员能力的显式组装入口，不从请求加载模块或伪造缺失能力。"""

from dataclasses import dataclass

from .interfaces import CallJournal, Generator, Retriever, Reviewer
from .orchestrator import ContentOrchestrator


@dataclass(frozen=True)
class ContentAgents:
    generation: Generator
    audit: Reviewer
    keywords: Reviewer
    trending: Retriever | None = None
    search: Retriever | None = None
    similarity: Reviewer | None = None

    def __post_init__(self) -> None:
        for name, method in (
            ("generation", "generate"),
            ("audit", "review"),
            ("keywords", "review"),
            ("trending", "retrieve"),
            ("search", "retrieve"),
            ("similarity", "review"),
        ):
            member = getattr(self, name)
            if member is None and name in ("trending", "search", "similarity"):
                continue
            if not callable(getattr(member, method, None)):
                raise TypeError(f"成员能力 {name} 缺少 {method} 接口")


def assemble_agents(
    agents: ContentAgents,
    *,
    call_timeout_seconds: float,
    require_all: bool = False,
    journal: CallJournal | None = None,
) -> ContentOrchestrator:
    """部分联调允许缺少增强能力；完整能力组装必须显式开启 require_all。"""
    if require_all and any(
        member is None for member in (agents.trending, agents.search, agents.similarity)
    ):
        raise ValueError("完整组装需要热点、搜索和同质化成员实例")
    return ContentOrchestrator(
        generator=agents.generation,
        keywords=agents.keywords,
        content_review=agents.audit,
        retrievers={
            name: member
            for name, member in (("trending", agents.trending), ("search", agents.search))
            if member is not None
        },
        additional_reviews={"similarity": agents.similarity}
        if agents.similarity is not None
        else {},
        call_timeout_seconds=call_timeout_seconds,
        journal=journal,
    )
