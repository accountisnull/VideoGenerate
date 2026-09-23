import json

import httpx
import pytest

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"

from app.content.retrieval.trending_providers import create_providers
from app.content.retrieval.trending_providers.baidu import BaiduProvider
from app.content.retrieval.trending_providers.douyin import DouyinProvider
from app.content.retrieval.trending_providers.weibo import WeiboProvider


@pytest.mark.parametrize(
    "cls,payload,expected_url",
    [
        (
            WeiboProvider,
            {
                "type": "weibo",
                "list": [
                    {
                        "title": "合成微博标题",
                        "hot_value": "123",
                        "url": "https://example.com/weibo",
                    }
                ],
            },
            "https://example.com/weibo",
        ),
        (
            DouyinProvider,
            {"status_code": 0, "word_list": [{"word": "合成抖音标题", "hot_value": 123}]},
            None,
        ),
        (
            BaiduProvider,
            {
                "data": {
                    "cards": [
                        {
                            "component": "hotList",
                            "content": [
                                {
                                    "word": "合成百度标题",
                                    "hotScore": "123",
                                    "url": "https://example.com/baidu",
                                }
                            ],
                        }
                    ]
                }
            },
            "https://example.com/baidu",
        ),
    ],
)
async def test_real_format_using_synthetic_body(cls, payload, expected_url):
    body = json.dumps(payload, ensure_ascii=False)
    if cls == BaiduProvider:
        body = "<html><!--s-data:" + body + "--></html>"
    transport = httpx.MockTransport(lambda req: httpx.Response(200, text=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await cls(client).fetch(5, 1)
    assert result.status == "ok"
    assert result.items[0]["source_url"] == expected_url
    assert str(result.items[0]["hot_score"]) == "123"


@pytest.mark.parametrize(
    "status,reason",
    [
        (401, "auth_required"),
        (403, "access_denied"),
        (429, "rate_limited"),
        (500, "http_error"),
        (302, "http_error"),
    ],
)
async def test_http_errors(status, reason):
    transport = httpx.MockTransport(lambda req: httpx.Response(status))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await WeiboProvider(client).fetch(5, 1)
    assert result.reason_code == reason


@pytest.mark.parametrize(
    "body", [b"", b"captcha", b"[]", b'{"type":"wrong"}', b'{"type":"weibo","list":{}}']
)
async def test_invalid_response_not_empty_success(body):
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=body))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await WeiboProvider(client).fetch(5, 1)
    assert result.status == "failed"
    assert result.reason_code == "invalid_payload"


@pytest.mark.parametrize(
    "exception,reason",
    [
        (httpx.ReadTimeout, "timeout"),
        (httpx.ConnectError, "network_error"),
    ],
)
async def test_transport_failure(exception, reason):
    def handler(req):
        raise exception("synthetic failure", request=req)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await DouyinProvider(client).fetch(5, 1)).reason_code == reason


async def test_response_size_limit():
    transport = httpx.MockTransport(lambda req: httpx.Response(200, content=b"x" * 3_000_001))
    async with httpx.AsyncClient(transport=transport) as client:
        assert (await BaiduProvider(client).fetch(5, 1)).reason_code == "invalid_payload"


async def test_defaults_and_endpoint_override():
    async with httpx.AsyncClient() as client:
        providers = create_providers(client, {"weibo": "https://example.com/same-schema"})
        assert set(providers) == {"weibo", "baidu", "douyin"}
        assert providers["weibo"].url == "https://example.com/same-schema"
