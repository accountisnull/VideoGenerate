"""百度热榜 HTML 中公开嵌入的 s-data 数据，不解析展示文案。"""

import json
import re

from .base import HttpProvider, InvalidPayload, rows_to_items


class BaiduProvider(HttpProvider):
    platform = "baidu"
    source_id = "baidu-realtime-page"
    default_url = "https://top.baidu.com/board?tab=realtime"

    def parse(self, body: bytes) -> list[dict]:
        match = re.search(r"<!--s-data:(.*?)-->", body.decode("utf-8"), re.DOTALL)
        if not match:
            raise InvalidPayload("Missing embedded board data")
        payload = json.loads(match.group(1))
        cards = payload["data"]["cards"]
        if not isinstance(cards, list):
            raise InvalidPayload("Invalid cards")
        for card in cards:
            if isinstance(card, dict) and card.get("component") == "hotList":
                return rows_to_items(card["content"], "word", "hotScore", "url")
        raise InvalidPayload("Missing hotList")
