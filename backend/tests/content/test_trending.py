import asyncio
import json
from dataclasses import dataclass
from datetime import datetime

import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"

from app.content.retrieval.trending import (
    ProviderResult,
    fetch_trending,
    normalize_item,
)


@dataclass
class Stub:
    platform: str
    rows: list
    delay: float = 0
    raises: bool = False

    async def fetch(self, limit, timeout):
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise RuntimeError("secret-token-must-not-leak")
        return ProviderResult(self.platform, self.rows[:limit])


def rows(*titles):
    return [{"title": title} for title in titles]


async def test_round_robin_exact_dedup_limit_without_cross_platform_score_sort():
    providers = {
        "weibo": Stub("weibo", [{"title": " A ", "hot_score": 1}, {"title": "B"}]),
        "baidu": Stub("baidu", [{"title": "A", "hot_score": 999}, {"title": "C"}]),
        "douyin": Stub("douyin", rows("a", "A!")),
    }
    result = await fetch_trending(list(providers), 4, providers)
    assert [x["title"] for x in result["data"]] == ["A", "a", "B", "C"]
    assert result["data"][0]["platform"] == "weibo"
    assert result["data"][0]["hot_score"] == 1
    assert result["success"] and not result["degraded"]
    datetime.fromisoformat(result["data"][0]["fetched_at"])


async def test_dedup_backfills_limit_and_deduplicates_platform_requests():
    provider = Stub("weibo", rows("A", "A", "B", "C"))
    result = await fetch_trending(["weibo", "weibo"], 3, {"weibo": provider})
    assert [x["title"] for x in result["data"]] == ["A", "B", "C"]
    assert len(result["platforms"]) == 1


async def test_partial_failure_preserves_materials_and_reason_no_exception_leak():
    result = await fetch_trending(
        ["weibo", "douyin"],
        3,
        {
            "weibo": Stub("weibo", rows("A")),
            "douyin": Stub("douyin", [], raises=True),
        },
    )
    assert result["success"] and result["degraded"]
    assert result["platforms"][1]["reason_code"] == "provider_error"
    assert "secret-token" not in json.dumps(result)
    assert len(result["data"]) == 1


async def test_timeout_is_bounded_and_other_source_completes():
    result = await fetch_trending(
        ["weibo", "baidu"],
        2,
        {
            "weibo": Stub("weibo", rows("slow"), delay=1),
            "baidu": Stub("baidu", rows("fast")),
        },
        timeout=0.01,
    )
    assert result["platforms"][0]["reason_code"] == "timeout"
    assert result["data"][0]["title"] == "fast"


async def test_fetches_start_concurrently():
    both_started = asyncio.Event()
    started = set()

    class Coordinated(Stub):
        async def fetch(self, limit, timeout):
            started.add(self.platform)
            if len(started) == 2:
                both_started.set()
            await both_started.wait()
            return ProviderResult(self.platform, self.rows)

    result = await fetch_trending(
        ["weibo", "baidu"], 2, {p: Coordinated(p, rows(p)) for p in ["weibo", "baidu"]}, timeout=0.5
    )
    assert not result["degraded"]
    assert len(result["data"]) == 2


async def test_all_failed_differs_from_valid_empty_board():
    failed = await fetch_trending(["weibo"], 2, {"weibo": Stub("weibo", [], raises=True)})
    empty = await fetch_trending(["weibo"], 2, {"weibo": Stub("weibo", [])})
    assert failed["success"] is False
    assert failed["reason_code"] == "all_platforms_failed"
    assert empty["success"] is True
    assert empty["platforms"][0]["status"] == "empty"
    assert empty["degraded"] is False
    assert failed["data"] == empty["data"] == []


async def test_missing_fields_and_invalid_rows():
    result = await fetch_trending(
        ["weibo"],
        5,
        {
            "weibo": Stub("weibo", [{}, {"title": " "}, 123, {"title": "valid"}]),
        },
    )
    assert result["degraded"]
    assert result["platforms"][0]["invalid_count"] == 3
    assert result["platforms"][0]["reason_code"] == "invalid_items_dropped"
    assert result["data"][0]["source_url"] is None
    assert result["data"][0]["hot_score"] is None


async def test_all_invalid_is_failure_not_empty():
    result = await fetch_trending(["weibo"], 5, {"weibo": Stub("weibo", [{}])})
    assert not result["success"]
    assert result["platforms"][0]["reason_code"] == "invalid_payload"


async def test_missing_provider_reports_unconfigured():
    result = await fetch_trending(["weibo"], 1, {})
    assert result["platforms"][0]["reason_code"] == "provider_not_configured"


@pytest.mark.parametrize(
    "platforms,limit",
    [
        ([], 2),
        ("weibo", 2),
        (["unknown"], 1),
        ([{}], 1),
        (["weibo"], 0),
        (["weibo"], True),
        (["weibo"], 101),
        (["weibo"], "20"),
    ],
)
async def test_invalid_input_rejected(platforms, limit):
    with pytest.raises(ValueError):
        await fetch_trending(platforms, limit, {})


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf"), True, "8"])
async def test_invalid_timeout_rejected(value):
    with pytest.raises(ValueError):
        await fetch_trending(["weibo"], 1, {}, timeout=value)


@pytest.mark.parametrize("score", [None, True, -1, float("inf"), "unknown", {}, [], 10**1000])
def test_invalid_optional_score_is_null(score):
    item = normalize_item({"title": "A", "hot_score": score}, "weibo", "now")
    assert item.hot_score is None


@pytest.mark.parametrize(
    "url",
    [None, "javascript:alert(1)", "/relative", {}, "https://user:secret@example.com", "https://["],
)
def test_invalid_optional_url_is_null(url):
    assert normalize_item({"title": "A", "url": url}, "weibo", "now").source_url is None


def test_score_zero_and_numeric_string_url_retained():
    assert normalize_item({"title": "A", "hot_score": 0}, "weibo", "now").hot_score == 0
    item = normalize_item(
        {"title": " A ", "hot_score": "120.5", "source_url": "https://example.com/a"},
        "baidu",
        "now",
    )
    assert item.hot_score == 120.5
    assert item.source_url == "https://example.com/a"


async def test_malformed_provider_return_and_cancel_propagation():
    class Invalid:
        async def fetch(self, limit, timeout):
            return {"not": "ProviderResult"}

    result = await fetch_trending(["weibo"], 1, {"weibo": Invalid()})
    assert result["platforms"][0]["reason_code"] == "invalid_payload"

    class Cancelled:
        async def fetch(self, limit, timeout):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await fetch_trending(["weibo"], 1, {"weibo": Cancelled()})
