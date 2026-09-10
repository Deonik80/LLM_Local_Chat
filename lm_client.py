# Copyright (C) 2026 Deonik80 (https://github.com/Deonik80)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://gnu.org>.

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
        """Выгрузить ранее загруженную модель из памяти сервера.

        state хранит model_id (напр. 'omnicoder-9b'), а сервер в новых
        версиях ждёт instance_id — поэтому пробуем оба варианта payload.
        """
        last = None
        for payload in ({"instance_id": instance_id}, {"model": instance_id}):
            try:
                r = await self._c.post(self._root() + "/api/v1/models/unload",
                                       json=payload)
                r.raise_for_status()
                try:
                    return r.json()
                except Exception:
                    return {}
            except Exception as e:
                last = e
                continue
        raise RuntimeError(str(last))

    async def fetch_models(self) -> list[str]:
        last = None
        for i in range(3):  # ретраи
            try:
                r = await self._c.get(self._model_url); r.raise_for_status()
                return [m["id"] for m in r.json().get("data", []) if "id" in m]
            except Exception as e: last = e; await asyncio.sleep(1 * (i + 1))
        raise RuntimeError(self._tr("no_link", u=self._base_url, e=last))

    @staticmethod
    def _loaded_from_api_models(j) -> tuple[list[str], bool]:
        """Разобрать GET /api/v1/models: какие модели реально в памяти.

        Возвращает (ids, understood): understood=False — у ответа нет
        признаков loaded-state (например, каталог без loaded_instances),
        по нему решать нельзя.
        """
        items: list = []
        if isinstance(j, dict):
            if isinstance(j.get("models"), list):
                items = j["models"]
            elif isinstance(j.get("data"), list):
                items = j["data"]
            elif "key" in j or ("id" in j and "loaded_instances" in j):
                items = [j]
            else:
                return [], False
        elif isinstance(j, list):
            items = j
        else:
            return [], False
        out: list[str] = []
        understood = False
        for m in items:
            if not isinstance(m, dict):
                continue
            inst = m.get("loaded_instances")
            flag: bool | None = None
            if isinstance(inst, list):
                flag = len(inst) > 0
                understood = True
            elif isinstance(inst, bool):
                flag = inst
                understood = True
            if flag is None and isinstance(m.get("loaded"), bool):
                flag = m["loaded"]
                understood = True
            if flag is None and isinstance(m.get("state"), str):
                flag = m["state"].lower() in ("loaded", "active", "running")
                understood = True
            if flag is None:
                continue  # про эту запись сказать нечего
            if not flag:
                continue
            for k in ("key", "id", "display_name", "name"):
                v = m.get(k)
                if isinstance(v, str) and v and v not in out:
                    out.append(v)
                    break
            if isinstance(inst, list):
                for ins in inst:
                    if isinstance(ins, dict):
                        v = ins.get("id")
                        if isinstance(v, str) and v and v not in out:
                            out.append(v)
        return out, understood

    async def loaded_models(self) -> list[str]:
        """Модели, уже загруженные в память сервера (best effort).

        Только эндпоинты с явным loaded-state. Каталог без признаков
        загрузки игнорируем — по нему решать нельзя. Пусто = грузим
        сохранённую модель как раньше.
        """
        for p in ("/api/v1/models", "/api/v0/models"):
            try:
                r = await self._c.get(self._root() + p, timeout=15.0)
                r.raise_for_status()
                out, understood = self._loaded_from_api_models(r.json())
                if understood:
                    return out
            except Exception:
                continue
        # legacy: прямые списки загруженного (200 + непусто = в памяти)
        for p in ("/api/v1/models/loads", "/api/v1/models/loaded",
                  "/api/v0/models/loads"):
            try:
                r = await self._c.get(self._root() + p, timeout=15.0)
                r.raise_for_status()
                j = r.json()
                items = j.get("data", j) if isinstance(j, dict) else j
                if isinstance(items, dict):
                    items = [items]
                if not isinstance(items, list):
                    continue
                out = [x for x in
                       (m.get("id") or m.get("model") or m.get("key")
                        if isinstance(m, dict) else m for m in items)
                       if isinstance(x, str) and x]
                if out:
                    return out
            except Exception:
                continue
        return []

    async def chat_stream(self, msgs, model, s: dict, on_delta: Callable):
        self._cancel = False
        payload = {"model": model, "messages": msgs, "temperature": s["temperature"],
            "max_tokens": s["max_tokens"], "stream": True}
        if s.get("seed", -1) >= 0: payload["seed"] = s["seed"]
        if s.get("repeat_penalty", 1.0) != 1.0: payload["repeat_penalty"] = s["repeat_penalty"]
        content, reasoning = "", ""
        usage: dict = {}
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
                        if isinstance(j.get("usage"), dict):  # финальный чанк со счётчиками
                            usage = j["usage"]
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
        return content, reasoning, usage
