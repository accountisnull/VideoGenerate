import asyncio
import json
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.content.generation.models import (
    Draft,
    GenerationRequest,
    Material,
    ReviewIssue,
    RevisionRequest,
    SourceVersion,
)
from app.content.generation.service import ContentGenerator


def test_revision_keeps_snapshot_and_feedback_and_returns_new_version(stub_model, payload):
    previous = SourceVersion(version_id=uuid4(), draft=Draft(**payload))
    original = previous.model_dump_json()
    request = RevisionRequest(
        topic="养绿萝", previous=previous,
        review_issues=[ReviewIssue(type="quality", detail="开头直接说明盆土观察方法")],
    )
    payload["script"] = payload["script"].replace("养绿萝总怕浇水不对？", "判断绿萝是否需要浇水，先看盆土。")
    payload["title"] = "浇水之前先看盆土"
    stub_model.output = json.dumps(payload)
    result = asyncio.run(ContentGenerator(stub_model).revise(request))
    assert result.version_id != previous.version_id
    assert result.source_version_id == previous.version_id
    assert result.previous == previous
    assert result.previous is not previous
    assert previous.model_dump_json() == original
    assert result.draft.title == payload["title"]
    assert result.model_call.step == "revision"
    assert len(stub_model.calls) == 1
    sent = json.loads(stub_model.calls[0]["user_prompt"])
    assert sent["mode"] == "revise"
    assert sent["input"]["previous"] == previous.model_dump(mode="json")
    assert sent["input"]["review_issues"][0]["detail"] == request.review_issues[0].detail


def test_explicit_revision_accepts_oversize_source_for_shortening(stub_model, payload):
    payload["script"] = "原稿" * 200
    request = RevisionRequest(topic="养绿萝", previous=SourceVersion(version_id=uuid4(), draft=Draft(**payload)),
                              revision_request="缩短为 100—200 字")
    result = asyncio.run(ContentGenerator(stub_model).revise(request))
    assert 100 <= result.char_count <= 200
    assert len(result.previous.draft.script) == 400


def test_revision_requires_full_source_and_feedback(payload):
    with pytest.raises(ValidationError):
        RevisionRequest(topic="主题", revision_request="改短")
    with pytest.raises(ValidationError, match="修改意见"):
        RevisionRequest(topic="主题", previous=SourceVersion(version_id=uuid4(), draft=Draft(**payload)))


def test_revision_requires_materials_for_old_references(payload):
    payload["material_ids"] = ["source-1"]
    source = SourceVersion(version_id=uuid4(), draft=Draft(**payload))
    with pytest.raises(ValidationError, match="原稿引用"):
        RevisionRequest(topic="主题", previous=source, revision_request="改短")
    request = RevisionRequest(topic="主题", previous=source, revision_request="改短", materials=[
        Material(id="source-1", title="养护", content="参考资料"),
    ])
    assert request.previous.draft.material_ids == ["source-1"]


def test_generation_and_revision_entry_points_cannot_be_mixed(stub_model, payload):
    service = ContentGenerator(stub_model)
    request = RevisionRequest(topic="主题", previous=SourceVersion(version_id=uuid4(), draft=Draft(**payload)),
                              revision_request="改短")
    with pytest.raises(TypeError):
        asyncio.run(service.generate(request))
    with pytest.raises(TypeError):
        asyncio.run(service.revise(GenerationRequest(topic="主题")))
    assert not stub_model.calls
