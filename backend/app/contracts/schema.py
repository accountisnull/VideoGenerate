"""从公共模型生成可独立分发的 JSON Schema，不导入运行时配置。"""

import argparse
import json
from pathlib import Path

from . import (
    AssetRef,
    ContentCapabilities,
    ContentJob,
    ContentRequest,
    ContentResult,
    CreateHeaders,
    ErrorResponse,
    HealthResponse,
    SpeechCapabilities,
    SpeechJob,
    SpeechRequest,
    TaskRequest,
    TaskResponse,
    VideoCapabilities,
    VideoJob,
    VideoRequest,
)

MODELS = (
    AssetRef, CreateHeaders, ErrorResponse, HealthResponse,
    ContentRequest, ContentResult, ContentJob, ContentCapabilities,
    SpeechRequest, SpeechJob, SpeechCapabilities,
    VideoRequest, VideoJob, VideoCapabilities, TaskRequest, TaskResponse,
)
SCHEMA_DIR = Path(__file__).resolve().parents[3] / "docs" / "contracts" / "v1"


def schemas():
    for model in MODELS:
        schema = model.model_json_schema(mode="validation")
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        yield model.__name__ + ".schema.json", schema


def main():
    parser = argparse.ArgumentParser(description="生成或核对 HTTP v1 契约文件")
    parser.add_argument("--check", action="store_true", help="只检查已提交文件是否与模型一致")
    args = parser.parse_args()
    mismatches = []
    for filename, schema in schemas():
        path = SCHEMA_DIR / filename
        content = json.dumps(schema, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                mismatches.append(filename)
        else:
            SCHEMA_DIR.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8", newline="\n")
    if mismatches:
        parser.exit(1, "契约文件需要重新生成：" + ", ".join(mismatches) + "\n")
    print("契约文件一致" if args.check else "契约文件已生成")


if __name__ == "__main__":
    main()
