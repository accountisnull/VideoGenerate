"""一次生成或修订；审核轮次和作业保存由人员 6 负责。"""

import json
import re
from uuid import uuid4

from pydantic import ValidationError

from app.content.errors import ContentError
from app.content.providers.text_model import AgentTextModel, TextModel

from .models import Draft, GenerationRequest, GenerationResult, RevisionRequest
from .prompts import SYSTEM_PROMPT, user_prompt


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("JSON 字段重复")
        result[key] = value
    return result


def parse_draft(text: str, available_ids: set[str]) -> Draft:
    if not text.strip() or len(text) > 32000:
        raise ContentError("MODEL_OUTPUT_INVALID", "模型输出为空或过大")
    try:
        payload = json.loads(text, object_pairs_hook=_unique_object)
        draft = Draft.model_validate(payload)
    except (ValueError, TypeError, ValidationError):
        raise ContentError("MODEL_OUTPUT_INVALID", "模型输出不是有效的稿件 JSON，或字段缺失/类型错误") from None
    char_count = len("".join(draft.script.split()))
    if not 100 <= char_count <= 200:
        raise ContentError("SCRIPT_LENGTH_INVALID", "正文去除空白后必须为 100—200 个字符（含标点）")
    if re.search(r"[#＃`]|(?m:^[ \t]*(?:标题|标签|话题标签)\s*[:：])", draft.script):
        raise ContentError("SCRIPT_FORMAT_INVALID", "正文包含标签、标题前缀或代码标记，应仅含朗读内容")
    if not set(draft.material_ids).issubset(available_ids):
        raise ContentError("MATERIAL_REFERENCE_INVALID", "稿件引用了未提供的素材")
    return draft


class ContentGenerator:
    def __init__(self, model: TextModel | None = None):
        self.model = model if model is not None else AgentTextModel()

    async def generate(self, request: GenerationRequest) -> GenerationResult:
        if type(request) is not GenerationRequest:
            raise TypeError("generate 需要 GenerationRequest，修订请调用 revise")
        return await self._run(request)

    async def revise(self, request: RevisionRequest) -> GenerationResult:
        if type(request) is not RevisionRequest:
            raise TypeError("revise 需要 RevisionRequest")
        return await self._run(request)

    async def _run(self, request):
        # 固定本次输入快照，避免等待模型时调用方修改来源或原稿。
        request = type(request).model_validate_json(request.model_dump_json())
        is_revision = isinstance(request, RevisionRequest)
        reply = await self.model.generate(
            step="revision" if is_revision else "writing",
            system_prompt=SYSTEM_PROMPT, user_prompt=user_prompt(request),
        )
        try:
            draft = parse_draft(reply.text, {item.id for item in request.materials})
        except ContentError as error:
            error.call = reply.call
            raise
        return GenerationResult(
            version_id=uuid4(),
            source_version_id=request.previous.version_id if is_revision else None,
            draft=draft, previous=request.previous if is_revision else None,
            model_call=reply.call,
        )
