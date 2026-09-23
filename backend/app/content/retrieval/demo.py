"""离线演示和单独真实验证；真实输出仅写入显式指定的位置。"""

import argparse
import asyncio
import json
from pathlib import Path

from .service import get_trending
from .trending import ProviderResult, fetch_trending


class DemoProvider:
    source_id = "synthetic-demo-only"
    source_version = "1"

    def __init__(self, platform: str, fail: bool = False):
        self.platform = platform
        self.fail = fail

    async def fetch(self, limit: int, timeout: float) -> ProviderResult:
        if self.fail:
            return ProviderResult(self.platform, status="failed", reason_code="timeout")
        return ProviderResult(
            self.platform,
            items=[
                {"title": "演示素材：跨平台同名标题", "hot_score": 100},
                {"title": f"演示素材：{self.platform} 第二条", "hot_score": None},
            ],
        )


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="真实访问三个已登记的来源")
    parser.add_argument("--platforms", nargs="+", default=["weibo", "baidu", "douyin"])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--fail", nargs="*", default=[], choices=["weibo", "baidu", "douyin"])
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.live and args.fail:
        parser.error("--fail 只用于离线演示")
    result = (
        await get_trending(args.platforms, args.limit)
        if args.live
        else await fetch_trending(
            args.platforms,
            args.limit,
            {p: DemoProvider(p, p in args.fail) for p in args.platforms},
        )
    )
    result = {"mode": "live" if args.live else "synthetic_demo", **result}
    rendered = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
        print(f"Saved {args.output}; mode={result['mode']}; count={len(result['data'])}")
        print(json.dumps(result["platforms"], ensure_ascii=True))
    else:
        print(rendered)
    if not result["success"]:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
