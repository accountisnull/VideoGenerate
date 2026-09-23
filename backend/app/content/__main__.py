"""前台启动内容服务；按显式配置组装百炼适配。"""

import argparse
from pathlib import Path

import uvicorn

from .api import create_app
from .bootstrap import ContentConfigurationError, build_orchestrator
from .settings import load_settings


def main() -> None:
    parser = argparse.ArgumentParser(description="启动内容作业服务")
    parser.add_argument("--port", type=int, default=8761)
    parser.add_argument("--asset-root", type=Path)
    parser.add_argument(
        "--check", action="store_true", help="仅校验配置与导入，不启动或写数据库"
    )
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口必须在 1—65535 之间")
    try:
        settings = load_settings()
        if args.asset_root is not None:
            settings.asset_root = args.asset_root.resolve()
        orchestrator = build_orchestrator(settings)
        if settings.similarity_enabled:
            from .config import SimilarityConfig
            from .errors import SimilarityError
            try:
                SimilarityConfig.load()
            except SimilarityError:
                raise ValueError("同质化配置无效") from None
            if settings.similarity_python is not None and not settings.similarity_python.is_file():
                raise ValueError("同质化 Python 解释器不存在")
            if orchestrator is None:
                raise ValueError("启用同质化需要内容模型")
    except (ValueError, ContentConfigurationError):
        parser.error("内容服务配置无效，请核对 CONTENT_* 字段")
    if args.check:
        print(
            "内容能力已组装，配置检查通过，未调用模型"
            if orchestrator
            else "内容能力未启用，生产未就绪"
        )
        return
    uvicorn.run(create_app(settings, orchestrator), host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
