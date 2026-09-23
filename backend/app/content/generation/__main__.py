"""真实模型验证入口：python -m app.content.generation generate|revise input.json。"""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from pydantic import ValidationError

from app.content.errors import ContentError

from .models import GenerationRequest, GenerationResult, RevisionRequest
from .service import ContentGenerator


def main():
    parser = argparse.ArgumentParser(description="生成或修订待审核口播稿")
    parser.add_argument("mode", choices=["generate", "revise", "schema", "check-config"])
    parser.add_argument("input", nargs="?", type=Path)
    args = parser.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if args.mode == "schema":
        print(json.dumps({
            "GenerationRequest": GenerationRequest.model_json_schema(),
            "RevisionRequest": RevisionRequest.model_json_schema(),
            "GenerationResult": GenerationResult.model_json_schema(mode="serialization"),
        }, ensure_ascii=False, indent=2))
        return 0
    if args.mode == "check-config":
        from app import runtime
        status = {}
        for step in ("writing", "revision", "review"):
            try:
                runtime.text_model_configuration(step)
                status[step] = "configured"
            except ValueError:
                status[step] = "missing_or_invalid"
        print(json.dumps(status))
        return 0 if all(v == "configured" for v in status.values()) else 1
    if args.input is None:
        parser.error("generate/revise 需要 UTF-8 JSON 输入文件")
    try:
        request_type = RevisionRequest if args.mode == "revise" else GenerationRequest
        request = request_type.model_validate_json(args.input.read_text(encoding="utf-8-sig"))
        service = ContentGenerator()
        operation = service.revise if args.mode == "revise" else service.generate
        result = asyncio.run(operation(request))
        print(json.dumps({"success": True, "data": result.model_dump(mode="json"), "error": None},
                         ensure_ascii=False, indent=2))
        return 0
    except (ValidationError, OSError, UnicodeError):
        error = ContentError("INPUT_INVALID", "输入文件不存在、编码错误或不符合输入契约")
    except ContentError as failure:
        error = failure
    print(json.dumps({"success": False, "data": None, "error": error.as_dict()}, ensure_ascii=False))
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
