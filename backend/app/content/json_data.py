"""严格解析内容配置和模型 JSON，拒绝重复字段与非有限数值。"""

import json


def strict_json(text: str) -> object:
    def reject_constant(value: str) -> None:
        raise ValueError("JSON 非有限数值")

    def unique_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 重复字段")
            result[key] = value
        return result

    return json.loads(
        text, parse_constant=reject_constant, object_pairs_hook=unique_pairs
    )
