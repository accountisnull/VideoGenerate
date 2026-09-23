"""显式运行的真实审核验证；逐次落盘意图，不重试或覆盖既有证据。"""

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Literal
from uuid import uuid4

from pydantic import StrictBool, TypeAdapter, ValidationError

from app import runtime
from app.contracts.common import Text

from ..interfaces import CapabilityError, InvalidCapabilityOutput, Reviewer
from ..json_data import strict_json
from ..models import Draft, InternalModel, ReviewInput, RunContext
from ..providers.bailian import BailianTextClient, TextModelSettings
from .audit import AuditReviewer
from .keywords import load_rules


class AuditSample(InternalModel):
    sample_id: Text
    topic: Text
    script: Text
    title: Text
    tags: tuple[Text, ...]
    expected_passed: StrictBool
    label_reason: Text
    label_status: Literal["pending_human_review", "human_reviewed"]


async def verify_samples(reviewer: Reviewer, samples: list[AuditSample], output: Path) -> dict:
    if not samples or len({sample.sample_id for sample in samples}) != len(samples):
        raise ValueError("样例不能为空且编号必须唯一")
    summary = {"total": len(samples), "matched": 0, "false_positive": 0,
               "false_negative": 0, "call_failures": 0, "human_labeled": 0}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as handle:
        def write(record: dict) -> None:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

        for sample in samples:
            request = ReviewInput(
                context=RunContext(task_id=uuid4(), job_id=uuid4(), topic=sample.topic),
                round_number=1,
                draft=Draft(script=sample.script, title=sample.title, tags=sample.tags),
            )
            write({"event": "started", "sample_id": sample.sample_id,
                   "sample": sample.model_dump(mode="json"), "request": request.model_dump(mode="json")})
            summary["human_labeled"] += sample.label_status == "human_reviewed"
            failure = None
            try:
                decision = await reviewer.review(request)
            except TimeoutError:
                failure = "CALL_TIMEOUT"
            except (InvalidCapabilityOutput, ValidationError):
                failure = "INVALID_OUTPUT"
            except CapabilityError:
                failure = "UPSTREAM_FAILED"
            if failure is not None:
                summary["call_failures"] += 1
                write({"event": "finished", "sample_id": sample.sample_id, "failure": failure})
                continue
            matched = decision.passed == sample.expected_passed
            summary["matched"] += matched
            summary["false_positive"] += sample.expected_passed and not decision.passed
            summary["false_negative"] += not sample.expected_passed and decision.passed
            write({"event": "finished", "sample_id": sample.sample_id,
                   "decision": decision.model_dump(mode="json"), "matched": matched})
        write({"event": "summary", **summary})
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="审核样例实测（会调用 TEXT_REVIEW 模型）")
    parser.add_argument("--samples", type=Path, required=True)
    parser.add_argument("--rules", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        samples = TypeAdapter(list[AuditSample]).validate_python(
            strict_json(args.samples.read_text(encoding="utf-8-sig"))
        )
        rules = load_rules(args.rules)
        settings = runtime.text_model_configuration("review")
        settings.pop("api_key_env")
        client = BailianTextClient(TextModelSettings.model_validate(settings))
        summary = asyncio.run(verify_samples(
            AuditReviewer(client, rules, model_version=client.settings.model), samples, args.output,
        ))
    except (OSError, ValueError, TypeError, RecursionError):
        print("实测未完成：检查样例、规则、TEXT_REVIEW 配置和输出路径；已有记录不会覆盖。")
        return 2
    print(json.dumps(summary, ensure_ascii=False))
    if summary["human_labeled"] != summary["total"]:
        print("样例尚未全部人工标注，不能作为人工准确率验收。")
    return int(summary["call_failures"] > 0 or summary["matched"] != summary["total"])


if __name__ == "__main__":
    raise SystemExit(main())
