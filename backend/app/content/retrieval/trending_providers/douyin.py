"""抖音公开榜单：没有单条链接，不自行拼接搜索 URL。"""

import json

from .base import HttpProvider, InvalidPayload, rows_to_items


class DouyinProvider(HttpProvider):
    platform = "douyin"
    source_id = "iesdouyin-word-billboard"
    default_url = "https://www.iesdouyin.com/web/api/v2/hotsearch/billboard/word/"

    def parse(self, body: bytes) -> list[dict]:
        payload = json.loads(body)
        if not isinstance(payload, dict) or payload.get("status_code") != 0:
            raise InvalidPayload("Upstream status is not successful")
        return rows_to_items(payload["word_list"], "word", "hot_value", None)
