"""同质化检测命令入口；显式初始化，不将缺失历史库自动当作空库。"""

import argparse
import json
import sys
from pathlib import Path

from app.content.adapters import ToolAdapter
from app.content.config import SimilarityConfig
from app.content.errors import SimilarityError
from app.content.factory import open_service
from app.content.providers.embedding import model_fingerprint


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["fingerprint", "init", "detect", "store", "reconcile"])
    parser.add_argument("--model-path", type=Path)
    parser.add_argument("--input", type=Path, help="UTF-8 JSON 文件；省略则读取 stdin")
    args = parser.parse_args()
    try:
        if args.action == "fingerprint":
            if args.model_path is None:
                raise SimilarityError("INVALID_INPUT", "fingerprint 必须提供 --model-path")
            data = {"model_revision": model_fingerprint(args.model_path)}
            result = {"success": True, "data": data, "error": None}
        else:
            with open_service(SimilarityConfig.load()) as service:
                if args.action == "init":
                    service.index.initialize()
                    result = {"success": True, "data": service.profile.model_dump(mode="json"), "error": None}
                else:
                    request = json.loads(args.input.read_text(encoding="utf-8-sig")) if args.input else json.load(sys.stdin)
                    result = ToolAdapter(service).call(args.action, request)
    except SimilarityError as error:
        result = {"success": False, "data": None, "error": error.as_dict()}
    except (OSError, ValueError):
        result = {"success": False, "data": None,
                  "error": SimilarityError("INVALID_INPUT", "请求文件不可读或 JSON 无效").as_dict()}
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
