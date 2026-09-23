"""默认来源工厂；HTTP 客户端由调用方创建和关闭。"""

from collections.abc import Mapping

import httpx

from .baidu import BaiduProvider
from .base import HttpProvider
from .douyin import DouyinProvider
from .weibo import WeiboProvider


def create_providers(
    client: httpx.AsyncClient, urls: Mapping[str, str] | None = None
) -> dict[str, HttpProvider]:
    overrides = urls or {}
    return {
        cls.platform: cls(client, overrides.get(cls.platform))
        for cls in (WeiboProvider, BaiduProvider, DouyinProvider)
    }
