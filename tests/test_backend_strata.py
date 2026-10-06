"""Backend abstraction: lmstudio | strata (п.1 миграции на Strata)."""
from lm_client import (
    backend_from_settings,
    base_url_from_settings,
    reasoning_effort_from_settings,
)


def test_backend_explicit():
    assert backend_from_settings({"backend": "strata"}) == "strata"
    assert backend_from_settings({"backend": "lmstudio"}) == "lmstudio"
    assert backend_from_settings({}) == "lmstudio"


def test_backend_detect_by_url():
    assert backend_from_settings({"strata_url": "http://127.0.0.1:8080/v1"}) == "strata"
    assert backend_from_settings({"base_url": "http://localhost:8080/v1"}) == "strata"


def test_base_url_resolution():
    assert base_url_from_settings({"backend": "strata"}) == "http://127.0.0.1:8080/v1"
    assert base_url_from_settings({"backend": "lmstudio"}) == "http://localhost:1234/v1"
    assert base_url_from_settings({"backend": "strata", "base_url": "http://x:1/v1"}) == "http://x:1/v1"


def test_reasoning_effort():
    assert reasoning_effort_from_settings({"reasoning_effort": "low"}) == "low"
    assert reasoning_effort_from_settings({"reasoning_effort": "HIGH"}) == "high"
    assert reasoning_effort_from_settings({"reasoning_effort": "bogus"}) == ""
    assert reasoning_effort_from_settings({}) == ""


def test_chat_payload_has_effort():
    import asyncio
    import httpx
    from lm_client import LmClient

    seen = {}

    def handler(request):
        seen["json"] = __import__("json").loads(request.content.decode())
        body = {"id": "x", "object": "chat.completion", "created": 1,
                "model": "m", "choices": [{"index": 0,
                 "message": {"role": "assistant", "content": "ok"},
                 "finish_reason": "stop"}],
                "usage": {}}
        return httpx.Response(200, json=body)

    async def go():
        c = LmClient("http://h/v1/models", "http://h/v1/chat/completions",
                     base_url="http://h/v1",
                     transport=httpx.MockTransport(handler))
        await c.chat_stream([{"role": "user", "content": "hi"}], "m",
                            {"temperature": 0.7, "max_tokens": 50,
                             "reasoning_effort": "low"},
                            lambda k, t: None)

    asyncio.run(go())
    assert seen["json"]["reasoning_effort"] == "low"
    assert seen["json"]["model"] == "m"
