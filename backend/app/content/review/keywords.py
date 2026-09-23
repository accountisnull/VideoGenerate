"""版本化字面词规则和字数检查；规则文件缺失时禁止启用。"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, StrictInt, model_validator

from app.contracts.common import Text

from ..json_data import strict_json
from ..models import InternalModel, Issue, ReviewDecision, ReviewInput


class KeywordRule(InternalModel):
    rule_id: Text
    word: Text
    fields: Annotated[
        tuple[Literal["script", "title", "tags"], ...], Field(min_length=1)
    ]


class ContentRules(InternalModel):
    version: Text
    min_script_chars: Annotated[StrictInt, Field(ge=1)]
    max_script_chars: Annotated[StrictInt, Field(ge=1)]
    audit_instructions: Text
    keywords: Annotated[tuple[KeywordRule, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def valid_rules(self) -> "ContentRules":
        if self.min_script_chars > self.max_script_chars:
            raise ValueError("字数上下限不一致")
        ids = [rule.rule_id for rule in self.keywords]
        if len(ids) != len(set(ids)):
            raise ValueError("规则编号重复")
        return self

    def prompt(self) -> str:
        return (
            f"规则版本：{self.version}。正文去除空白后（包含标点）须为"
            f"{self.min_script_chars}—{self.max_script_chars}个字符。\n"
            + self.audit_instructions
            + "\n字面禁用规则："
            + self.model_dump_json()
        )


def load_rules(path: Path) -> ContentRules:
    return ContentRules.model_validate(
        strict_json(path.read_text(encoding="utf-8-sig"))
    )


class KeywordReviewer:
    def __init__(self, rules: ContentRules) -> None:
        self.rules = rules

    async def review(self, request: ReviewInput) -> ReviewDecision:
        issues = []
        length = request.draft.char_count
        if not self.rules.min_script_chars <= length <= self.rules.max_script_chars:
            issues.append(
                Issue(
                    field="script",
                    code="SCRIPT_LENGTH",
                    message=(
                        f"正文共{length}个非空白字符，请调整为{self.rules.min_script_chars}—"
                        f"{self.rules.max_script_chars}个；规则版本{self.rules.version}"
                    ),
                )
            )
        for rule in self.rules.keywords:
            for field in rule.fields:
                texts = (
                    request.draft.tags
                    if field == "tags"
                    else (getattr(request.draft, field),)
                )
                if any(rule.word.casefold() in text.casefold() for text in texts):
                    issues.append(
                        Issue(
                            field=field,
                            code="KEYWORD_MATCH",
                            message=(
                                f"命中字面词“{rule.word}”，请删除或修改；规则{rule.rule_id}，版本{self.rules.version}"
                            ),
                        )
                    )
        return ReviewDecision(passed=not issues, issues=tuple(issues))
