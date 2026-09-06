"""Network client for the LM Studio OpenAI-compatible API (Roadmap step 3).

Extracted verbatim from app.py: same retries, same SSE auto-reconnect,
same error mapping. Only difference: endpoint/tr/logger are injected
instead of module globals, so the client is testable in isolation.
"""
from __future__ import annotations
import asyncio
import json
from typing import Callable

import httpx


class StreamCancelled(Exception): pass


class LmClient:
    def __init__(self, model_url: str, chat_url: str, base_url: str = "",
                 timeout: float = 180.0,
                 tr: Callable | None = None, log=None):
        self._model_url = model_url
        self._chat_url = chat_url
        self._base_url = base_url or model_url
        self._c = httpx.AsyncClient(timeout=timeout)
        self._cancel = False
        self._tr = tr or (lambda key, **kw: key)
        self._log = log

    def cancel(self): self._cancel = True
    async def close(self): self._cancel = True; await self._c.aclose()

    def _root(self) -> str:
        b = (self._base_url or "").rstrip("/")
        return b[:-len("/v1")] if b.endswith("/v1") else b

    async def load_model(self, model_id: str, context_length: int | None = None) -> dict:
        """Загрузить модель на сервере (долгая операция — отдельный таймаут)."""
        payload: dict = {"model": model_id}
        if context_length:
            payload["context_length"] = context_length
        r = await self._c.post(self._root() + "/api/v1/models/load",
                               json=payload, timeout=600.0)
        r.raise_for_status()
        return r.json()

    async def unload_model(self, instance_id: str) -> dict:
        """Выгрузить ранее загруженную модель из памяти сервера."""
        r = await self._c.post(self._root() + "/api/v1/models/unload",
                               json={"instance_id": instance_id})
        r.raise_for_status()
        return r.json()

    async def fetch_models(self) -> list[str]:
        last = None
        for i in range(3):  # ретраи
            try:
                r = await self._c.get(self._model_url); r.raise_for_status()
                return [m["id"] for m in r.json().get("data", []) if "id" in m]
            except Exception as e: last = e; await asyncio.sleep(1 * (i + 1))
        raise RuntimeError(self._tr("no_link", u=self._base_url, e=last))

    async def chat_stream(self, msgs, model, s: dict, on_delta: Callable):
        self._cancel = False
        payload = {"model": model, "messages": msgs, "temperature": s["temperature"],
            "top_p": s.get("top_p", 1.0), "max_tokens": s["max_tokens"], "stream": True}
        if s.get("seed", -1) >= 0: payload["seed"] = s["seed"]
        if s.get("repeat_penalty", 1.0) != 1.0: payload["repeat_penalty"] = s["repeat_penalty"]
        content, reasoning = "", ""
        for attempt in range(2):  # автореконнект: 1 ретрай при обрыве SSE
            try:
                async with self._c.stream("POST", self._chat_url, json=payload) as r:
                    r.raise_for_status()
                    async for line in r.aiter_lines():
                        if self._cancel: raise StreamCancelled()
                        if not line or not line.strip().startswith("data:"): continue
                        d = line.strip()[6:]
                        if d == "[DONE]": break
                        try: j = json.loads(d)
                        except ValueError: continue
                        ch = j.get("choices", [{}])[0] if isinstance(j.get("choices"), list) else {}
                        delta = ch.get("delta", {}) or {}
                        if delta.get("reasoning_content"):
                            reasoning += delta["reasoning_content"]; on_delta("reasoning", delta["reasoning_content"])
                        if delta.get("content"):
                            content += delta["content"]; on_delta("content", delta["content"])
                break
            except StreamCancelled: raise
            except httpx.HTTPStatusError as e:
                body = ""
                try: body = (await e.response.aread()).decode("utf-8", "replace")[:2000]
                except Exception:
                    try: body = e.response.text[:2000]
                    except Exception: pass
                if self._log: self._log.error("HTTP %s for model payload: %s",
                                             e.response.status_code, body or e)
                raise RuntimeError(f"HTTP {e.response.status_code}: {body or e}")
            except httpx.HTTPError as e:
                if self._cancel: raise StreamCancelled() from e
                if content or reasoning or attempt == 1:
                    raise RuntimeError(self._tr("net_err", e=e))
                if self._log: self._log.warning("sse interrupted, retrying: %s", e)
                await asyncio.sleep(1.5 * (attempt + 1))  # backoff перед ретраем
                continue
        return content, reasoning
