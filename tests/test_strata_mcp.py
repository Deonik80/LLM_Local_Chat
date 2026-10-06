"""Strata MCP: флаг strata_mcp в payload + события strata_mcp чанков."""
import asyncio
import json

import httpx

from lm_client import LmClient


def _sse(*chunks):
    lines = []
    for c in chunks:
        lines.append("data: " + json.dumps(c, ensure_ascii=False))
    lines.append("data: [DONE]")
    return "\n".join(lines) + "\n"


def test_strata_payload_flag_and_events():
    seen = {}
    events = []

    body = _sse(
        {"choices": [{"index": 0, "delta": {"content": "hi"}, "finish_reason": None}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": None}],
         "strata_mcp": {"event": "call", "id": "c1", "name": "playwright__browser_navigate",
                        "server": "playwright", "tool": "browser_navigate",
                        "arguments": {"url": "https://example.com"}, "round": 1}},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": None}],
         "strata_mcp": {"event": "result", "id": "c1", "ok": True, "text": "Page Title: Example Domain",
                        "chars": 28, "truncated": False, "ms": 463}},
    )

    def handler(request):
        seen["json"] = json.loads(request.content.decode())
        return httpx.Response(200, text=body,
                              headers={"Content-Type": "text/event-stream"})

    async def go():
        c = LmClient("http://h/v1/models", "http://h/v1/chat/completions",
                     base_url="http://h/v1",
                     transport=httpx.MockTransport(handler))
        return await c.chat_stream(
            [{"role": "user", "content": "open example.com"}],
            "m", {"temperature": 0.7, "max_tokens": 50, "backend": "strata"},
            lambda k, t: None,
            on_event=lambda n, d: events.append((n, d)))

    content, _, _ = asyncio.run(go())
    assert seen["json"]["strata_mcp"] is True
    assert content == "hi"
    kinds = [n for n, _ in events]
    assert kinds == ["tool_start", "tool_done"]
    assert events[0][1]["tool"] == "playwright__browser_navigate"
    assert "Example Domain" in events[1][1]["output"]
