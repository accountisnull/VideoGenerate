"""从实际内部模型生成检索 Schema，不手写另一份数据契约。"""

import argparse
import json
from pathlib import Path

from app.content.models import RetrievalResult

SCHEMA_PATH = Path(__file__).resolve().parents[4] / "docs/contracts/internal/RetrievalResult.schema.json"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    schema = RetrievalResult.model_json_schema(mode="validation")
    schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    content = json.dumps(schema, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.check:
        if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text("utf-8") != content:
            parser.exit(1, "检索 Schema 需要重新生成\n")
    else:
        SCHEMA_PATH.parent.mkdir(parents=True, exist_ok=True)
        SCHEMA_PATH.write_text(content, encoding="utf-8", newline="\n")
    print("检索 Schema 一致" if args.check else "检索 Schema 已生成")


if __name__ == "__main__":
    main()
