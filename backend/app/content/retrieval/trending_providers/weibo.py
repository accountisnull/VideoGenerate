"""微博：明确采用 UAPI 第三方热榜，非微博官方开放 API。"""

import json

from .base import HttpProvider, InvalidPayload, rows_to_items


class WeiboProvider(HttpProvider):
    platform = "weibo"
    source_id = "uapi-weibo"
    default_url = "https://uapis.cn/api/v1/misc/hotboard?type=weibo"

    def parse(self, body: bytes) -> list[dict]:
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("type") != "weibo":
            raise InvalidPayload("Unexpected platform")
        return rows_to_items(payload["list"], "title", "hot_value", "url")
