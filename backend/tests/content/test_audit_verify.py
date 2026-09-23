"""实测入口使用替身验证留痕；自动测试不发送模型请求。"""

import asyncio
import json

import pytest

from app.content.models import ReviewDecision
from app.content.review.audit_verify import AuditSample, verify_samples


def sample(expected=True):
    return AuditSample(
        sample_id="normal", topic="绿萝养护", script="观察土壤再浇水", title="养护",
        tags=["绿萝"], expected_passed=expected, label_reason="正常养护建议",
        label_status="pending_human_review",
    )


def test_receipt_is_written_before_call_and_existing_output_never_repeats(tmp_path):
    output = tmp_path / "evidence.jsonl"
    calls = []

    class Reviewer:
        async def review(self, request):
            rows = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
            assert rows[-1]["event"] == "started"
            assert rows[-1]["sample_id"] == "normal"
            calls.append(request)
            return ReviewDecision(passed=True, issues=())

    result = asyncio.run(verify_samples(Reviewer(), [sample()], output))
    assert result["matched"] == 1 and result["human_labeled"] == 0
    assert len(calls) == 1
    with pytest.raises(FileExistsError):
        asyncio.run(verify_samples(Reviewer(), [sample()], output))
    assert len(calls) == 1


def test_failure_does_not_count_as_rejection_or_leak_details(tmp_path):
    output = tmp_path / "evidence.jsonl"

    class Reviewer:
        async def review(self, request):
            raise TimeoutError("private credential")

    result = asyncio.run(verify_samples(Reviewer(), [sample(False)], output))
    assert result["call_failures"] == 1
    assert result["matched"] == 0
    assert result["false_positive"] == result["false_negative"] == 0
    assert "private credential" not in output.read_text(encoding="utf-8")
