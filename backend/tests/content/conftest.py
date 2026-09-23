import json
from datetime import UTC, datetime

import pytest

from app.content.generation.models import ModelCallRecord
from app.content.providers.text_model import ModelReply

SCRIPT = (
    "养绿萝总怕浇水不对？先别急着每天加水，可以从观察盆土开始。"
    "把花盆放在有明亮散射光的位置，避开长时间的烈日直晒。"
    "浇水前摸一摸表层土壤，如果仍然湿润，就再等一等；"
    "需要浇水时慢慢浇透，并倒掉托盘积水。"
    "每周看看叶片状态，再根据家里的温度和通风情况调整。"
    "你家绿萝放在哪里，又有哪些养护经验呢？"
)


class StubModel:
    def __init__(self, output):
        self.output = output
        self.calls = []

    async def generate(self, **kwargs):
        self.calls.append(kwargs)
        return ModelReply(self.output, ModelCallRecord(
            step=kwargs["step"], model="offline-test", started_at=datetime.now(UTC),
            elapsed_ms=1, call_count=1, status="succeeded",
        ))


@pytest.fixture
def payload():
    return {"script": SCRIPT, "title": "新手养绿萝，从观察盆土开始", "tags": ["绿萝", "养花"],
            "material_ids": []}


@pytest.fixture
def stub_model(payload):
    return StubModel(json.dumps(payload, ensure_ascii=False))
