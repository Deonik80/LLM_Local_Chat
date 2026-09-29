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

Server access mode (Authentication API): an optional ``api_key`` (or a
ready-made ``headers`` mapping) is injected into EVERY request —
``Authorization: Bearer <key>`` is added by default, and 401/403 are
mapped to a localized error instead of a raw HTTP dump. An empty key
(no auth) leaves the wire format byte-for-byte identical to before.
``transport`` is passed through to httpx so tests can use
``httpx.MockTransport``.

MCP-серверы из ``mcp.json`` (LM Studio 0.4.0+) доступны только через
OpenAI-совместимый ``POST /v1/responses`` с блоком ``tools`` типа ``mcp`` —
у ``/v1/chat/completions`` поддержки MCP нет. При ``mcp_enabled`` клиент сам
переключается на ``/v1/responses`` и разбирает его SSE-события
(``response.output_text.delta`` / ``response.reasoning_summary_text.delta`` /
``response.output_item.done`` / ``response.completed``). Диалог stateless:
в ``input`` уходит вся локальная история (включая ассистентские реплики),
поэтому правка, ветвление и перегенерация работают как обычно и серверная
цепочка не нужна. При выключенном MCP путь ``/v1/chat/completions`` и разбор
его ответа не меняются вовсе.
"""
from __future__ import annotations
import asyncio
import json
import re
from typing import Callable

import httpx

# коды, означающие «сервер не пустил» — ретраить и показывать HTTP-дамп бессмысленно
AUTH_CODES = (401, 403)
# режимы доступа к серверу: 'none' — как раньше, без Authorization-заголовков
AUTH_MODES = ("none", "api_key")
# префикс id сервера из mcp.json в LM Studio: 'mcp/<server_label>'
MCP_ID_PREFIX = "mcp/"
# значения настройки mcp_enabled, которые считаем «включено» (env/строки в json)
MCP_TRUE = ("1", "on", "true", "yes", "y")


def api_key_from_settings(settings: dict) -> str:
    """Ключ сервера из настроек; режим 'none' / пустое значение -> ''.

    Пустая строка означает «не слать Authorization вовсе»: пока авторизация
    на сервере не включена, поведение приложения не меняется. Неизвестный
    режим (в т.ч. дописанный вручную в settings.json) считаем 'none'.
    """
    if not isinstance(settings, dict):
        return ""
    if str(settings.get("auth_mode") or "none") != "api_key":
        return ""  # 'none' и любой неизвестный режим -> запросы без Authorization
    key = settings.get("api_key")
    return key.strip() if isinstance(key, str) else ""


def mcp_enabled_from_settings(settings: dict) -> bool:
    """Включён ли режим MCP — настройка ``mcp_enabled`` (bool).

    По умолчанию выключено: приложение работает ровно как раньше, обычный
    ``/v1/chat/completions`` и его парсинг не трогаем. Принимаем строку
    ('on'/'true'/'1') из переменной окружения и устаревшую пару ``mcp_mode``
    ('off'/'on') — чтобы не терять настройку у тех, кто её уже сохранил.
    """
    if not isinstance(settings, dict):
        return False
    val = settings.get("mcp_enabled")
    if val is None:
        val = settings.get("mcp_mode")
    if isinstance(val, bool):
        return val
    if isinstance(val, (int, float)):
        return bool(val)
    if isinstance(val, str):
        return val.strip().lower() in MCP_TRUE
    return False


def mcp_servers_from_settings(settings: dict) -> list[str]:
    """Выбранные серверы из mcp.json в нормализованном виде: ``['mcp/playwright']``.

    Настройка — список строк, но строки тоже принимаем (переменная окружения
    ``LM_STUDIO_MCP_SERVERS``, ручная правка json): метки через запятую,
    точку с запятой, пробел или перевод строки. Короткую метку без префикса
    LM Studio не поймёт, поэтому добавляем ``mcp/``. Порядок сохраняем,
    дубликаты (без учёта регистра) убираем.
    """
    raw = settings.get("mcp_servers") if isinstance(settings, dict) else None
    if isinstance(raw, (list, tuple)):
        parts = [str(x) for x in raw]
    elif isinstance(raw, str):
        parts = re.split(r"[,;\s]+", raw)
    else:
        parts = []
    out: list[str] = []
    seen: set[str] = set()
    for p in parts:
        sid = p.strip().strip("\"'").lstrip("/")
        if not sid or sid.lower() in ("none", "off", "null"):
            continue
        if not sid.lower().startswith(MCP_ID_PREFIX):
            sid = MCP_ID_PREFIX + sid
        if sid.lower() in seen:  # регистр метки не важен — дубликат отбрасываем
            continue
        seen.add(sid.lower())
        out.append(sid)
    return out


def mcp_active(settings: dict) -> bool:
    """MCP реально задействован: включён тумблер И выбран хотя бы один сервер.

    Пустой список серверов — не повод молча переключаться на /v1/responses:
    выигрыша в этом нет, а локальная история перестанет уходить в запрос.
    Такой случай UI показывает как подсказку.
    """
    return (mcp_enabled_from_settings(settings)
            and bool(mcp_servers_from_settings(settings)))


class StreamCancelled(Exception): pass


class AuthError(RuntimeError):
    """Сервер отклонил авторизацию (401/403); code — HTTP-статус.

    Отдельный класс (а не RuntimeError с текстом) нужен UI: health-статус
    отличает протухший ключ от недоступного сервера, не парся локализованную
    строку. Text по-прежнему локализован (_auth_error), код — отдельно.
    """

    def __init__(self, code: int, message: str):
        super().__init__(message)
        self.code = code


class LmClient:
    def __init__(self, model_url: str, chat_url: str, base_url: str = "",
                 timeout: float = 180.0,
                 tr: Callable | None = None, log=None,
                 api_key: str = "", headers: dict | None = None,
                 transport=None):
        self._model_url = model_url
        self._chat_url = chat_url
        self._base_url = base_url or model_url
        self._c = httpx.AsyncClient(timeout=timeout, transport=transport)
        self._cancel = False
        self._tr = tr or (lambda key, **kw: key)
        self._log = log
        self._api_key = ""
        self._headers = {str(k): str(v) for k, v in (headers or {}).items()
                         if v not in (None, "")}
        self.set_api_key(api_key)

    def cancel(self): self._cancel = True
    async def close(self): self._cancel = True; await self._c.aclose()

    # --- аутентификация сервера (LM Studio API key) ---
    def set_api_key(self, api_key: str):
        """Сменить ключ на лету: следующие запросы уйдут с новым заголовком."""
        self._api_key = (api_key or "").strip() if isinstance(api_key, str) else ""

    @property
    def api_key(self) -> str:
        return self._api_key

    def _req_headers(self) -> dict:
        """Заголовки для каждого запроса; без ключа — только свои (или пусто)."""
        h = dict(self._headers)
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    def _scrub(self, text: str) -> str:
        """Вычеркнуть ключ из текста, который уходит в лог/в UI."""
        t = text or ""
        if self._api_key and self._api_key in t:
            t = t.replace(self._api_key, "***")
        return t

    def _auth_error(self, code: int) -> AuthError:
        key = "invalid_api_key" if code == 401 else "auth_failed"
        return AuthError(code, self._tr(key, code=code))

    def _status_error(self, code: int, body: str = "", exc=None) -> RuntimeError:
        """Локализованная 401/403, остальные коды — как раньше (HTTP + тело)."""
        if self._log:
            self._log.error("HTTP %s for request: %s", code, self._scrub(body) or exc)
        if code in AUTH_CODES:
            return self._auth_error(code)
        return RuntimeError(f"HTTP {code}: {self._scrub(body) or exc}")

    @staticmethod
    def _status_code(exc) -> int:
        return getattr(getattr(exc, "response", None), "status_code", 0) or 0

    def _root(self) -> str:
        b = (self._base_url or "").rstrip("/")
        return b[:-len("/v1")] if b.endswith("/v1") else b

    async def load_model(self, model_id: str, context_length: int | None = None) -> dict:
        """Загрузить модель на сервере (долгая операция — отдельный таймаут)."""
        payload: dict = {"model": model_id}
        if context_length:
            payload["context_length"] = context_length
        r = await self._c.post(self._root() + "/api/v1/models/load",
                               json=payload, timeout=600.0, headers=self._req_headers())
        try:
            r.raise_for_status()
        except httpx.HTTPStatusError as e:
            raise self._status_error(r.status_code, await self._safe_body(e), e) from e
        return r.json()

    @staticmethod
    async def _safe_body(e: httpx.HTTPStatusError) -> str:
        body = ""
        try: body = (await e.response.aread()).decode("utf-8", "replace")[:2000]
        except Exception:
            try: body = e.response.text[:2000]
            except Exception: pass
        return body

    async def unload_model(self, instance_id: str) -> dict:
        """Выгрузить ранее загруженную модель из памяти сервера.

        state хранит model_id (напр. 'omnicoder-9b'), а сервер в новых
        версиях ждёт instance_id — поэтому пробуем оба варианта payload.
        """
        last = None
        for payload in ({"instance_id": instance_id}, {"model": instance_id}):
            try:
                r = await self._c.post(self._root() + "/api/v1/models/unload",
                                       json=payload, headers=self._req_headers())
                r.raise_for_status()
                try:
                    return r.json()
                except Exception:
                    return {}
            except Exception as e:
                # нет доступа — второй payload не поможет, ключ надо чинить
                if self._status_code(e) in AUTH_CODES:
                    raise self._auth_error(self._status_code(e)) from e
                last = e
                continue
        # текст ошибки сервера может содержать присланный Authorization —
        # прогоняем через _scrub, ключ наружу не уходит
        raise RuntimeError(self._scrub(str(last)))

    async def fetch_models(self) -> list[str]:
        last = None
        for i in range(3):  # ретраи
            try:
                r = await self._c.get(self._model_url, headers=self._req_headers())
                r.raise_for_status()
                return [m["id"] for m in r.json().get("data", []) if "id" in m]
            except Exception as e:
                if self._status_code(e) in AUTH_CODES:  # ретрай ключ не починит
                    raise self._auth_error(self._status_code(e)) from e
                last = e; await asyncio.sleep(1 * (i + 1))
        raise RuntimeError(self._tr("no_link", u=self._base_url,
                                    e=self._scrub(str(last))))

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
        сохранённую модель как раньше. Отказ по авторизации — единственное
        исключение, пробрасываемое наружу: молчаливый «пусто» здесь
        выглядел бы как «модель не загружена».
        """
        last_auth = None
        for p in ("/api/v1/models", "/api/v0/models"):
            try:
                r = await self._c.get(self._root() + p, timeout=15.0,
                                      headers=self._req_headers())
                r.raise_for_status()
                out, understood = self._loaded_from_api_models(r.json())
                if understood:
                    return out
            except Exception as e:
                if self._status_code(e) in AUTH_CODES:
                    last_auth = self._auth_error(self._status_code(e))
                continue
        # legacy: прямые списки загруженного (200 + непусто = в памяти)
        for p in ("/api/v1/models/loads", "/api/v1/models/loaded",
                  "/api/v0/models/loads"):
            try:
                r = await self._c.get(self._root() + p, timeout=15.0,
                                      headers=self._req_headers())
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
            except Exception as e:
                if self._status_code(e) in AUTH_CODES:
                    last_auth = self._auth_error(self._status_code(e))
                continue
        if last_auth is not None:
            raise last_auth
        return []

    async def chat_stream(self, msgs, model, s: dict, on_delta: Callable,
                          on_event: Callable | None = None):
        self._cancel = False
        if mcp_active(s):  # MCP доступен только через /v1/responses
            return await self._mcp_chat_stream(msgs, model, s, on_delta, on_event)
        payload = {"model": model, "messages": msgs, "temperature": s["temperature"],
            "max_tokens": s["max_tokens"], "stream": True}
        if s.get("seed", -1) >= 0: payload["seed"] = s["seed"]
        if s.get("repeat_penalty", 1.0) != 1.0: payload["repeat_penalty"] = s["repeat_penalty"]
        content, reasoning = "", ""
        usage: dict = {}
        for attempt in range(2):  # автореконнект: 1 ретрай при обрыве SSE
            try:
                async with self._c.stream("POST", self._chat_url, json=payload,
                                          headers=self._req_headers()) as r:
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
                        _choices = j.get("choices")
                        ch = _choices[0] if isinstance(_choices, list) and _choices else {}
                        delta = ch.get("delta", {}) or {}
                        if delta.get("reasoning_content"):
                            reasoning += delta["reasoning_content"]; on_delta("reasoning", delta["reasoning_content"])
                        if delta.get("content"):
                            content += delta["content"]; on_delta("content", delta["content"])
                break
            except StreamCancelled: raise
            except httpx.HTTPStatusError as e:
                raise self._status_error(e.response.status_code,
                                         await self._safe_body(e), e) from e
            except httpx.HTTPError as e:
                if self._cancel: raise StreamCancelled() from e
                if content or reasoning or attempt == 1:
                    # ключ не должен утекать в текст ошибки/лог
                    raise RuntimeError(self._tr("net_err", e=self._scrub(str(e))))
                if self._log: self._log.warning("sse interrupted, retrying: %s", self._scrub(str(e)))
                await asyncio.sleep(1.5 * (attempt + 1))  # backoff перед ретраем
                continue
        return content, reasoning, usage

    # ---------- MCP: stateless POST /v1/responses ----------
    MCP_CHAT_PATH = "/v1/responses"
    _TOOL_PREVIEW = 240  # символов вывода инструмента в статус/лог (не в ответ модели)
    _TOOL_ARGS_PREVIEW = 120  # символов аргументов инструмента в строке чата

    @staticmethod
    def _mcp_part(part: dict) -> dict | None:
        """Одна часть контента OpenAI-сообщения -> input_item /v1/responses."""
        if not isinstance(part, dict):
            return None
        if part.get("type") == "text" and isinstance(part.get("text"), str):
            return {"type": "input_text", "text": part["text"]}
        if part.get("type") == "image_url":
            img = part.get("image_url")
            url = img.get("url") if isinstance(img, dict) else img
            if isinstance(url, str) and url.startswith("data:"):
                return {"type": "input_image", "image_url": url}
        return None

    @staticmethod
    def _mcp_text_part(role: str, text: str) -> dict:
        """Тип текстовой части зависит от роли (проверено на живом 0.4.x):

        ассистентские реплики сервер принимает только как ``output_text``
        (``input_text`` внутри assistant отвечает 400 invalid_union), в
        system/user — только ``input_text`` (``output_text`` там — 400).
        """
        return {"type": "output_text" if role == "assistant" else "input_text",
                "text": text}

    def _mcp_input(self, msgs) -> list[dict]:
        """``input`` для /v1/responses: вся локальная история диалога.

        В отличие от нативного /api/v1/chat, /v1/responses принимает полный
        массив сообщений OpenAI-формата (role/content), включая ассистентские
        реплики, — поэтому серверная цепочка не нужна: локальная история
        остаётся источником истины. Картинки переводим из ``image_url`` в
        ``input_image`` (только у user), обычный текст — в ``input_text``,
        а у assistant — в ``output_text`` (см. ``_mcp_text_part``).
        """
        out: list[dict] = []
        for m in msgs or []:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role not in ("system", "user", "assistant"):
                continue
            content = m.get("content")
            if isinstance(content, str):
                if not content.strip():
                    continue
                parts: list[dict] = [self._mcp_text_part(role, content)]
            elif isinstance(content, list):
                parts = []
                for p in content:
                    if not isinstance(p, dict):
                        continue
                    if role == "assistant":
                        # ассистентская часть — только текст как output_text;
                        # картинок/вызовов в локальной истории у неё нет
                        if p.get("type") in ("text", "input_text", "output_text") \
                                and isinstance(p.get("text"), str):
                            parts.append(self._mcp_text_part(role, p["text"]))
                        continue
                    converted = LmClient._mcp_part(p)
                    if converted is not None:
                        parts.append(converted)
                if not parts:
                    continue
            else:
                continue
            out.append({"role": role, "content": parts})
        return out

    @staticmethod
    def _mcp_tools(s: dict) -> list[dict]:
        """tools для /v1/responses: серверы mcp.json формой ``server_label``.

        Проверено на живом LM Studio (0.4.x): это единственная рабочая форма.
        ``integrations: ["mcp/<id>"]`` формально отвечает 200, но инструменты
        НЕ подключаются (молчаливый no-op — usage не растёт, mcp_list_tools
        не приходит), а ``tools: [{"type": "mcp", "id": "mcp/<id>"}]``
        отвергается 400 «Invalid input». Префикс ``mcp/`` из настроек — это
        id нативного /api/v1/chat; здесь сервер сам нормализует
        ``server_label`` в ``mcp/<label>`` и ищет плагин mcp.json.
        ``allowed_tools`` не шлём: модель может вызвать любой инструмент.
        """
        out: list[dict] = []
        for sid in mcp_servers_from_settings(s):
            label = sid[len(MCP_ID_PREFIX):] if sid.lower().startswith(MCP_ID_PREFIX) else sid
            out.append({"type": "mcp", "server_label": label})
        return out

    def _mcp_payload(self, msgs, model, s: dict) -> dict:
        inp = self._mcp_input(msgs)
        if not inp or not any(m.get("role") == "user" for m in inp):
            # без пользовательской реплики запрос бессмыслен (и раньше так же
            # останавливались до отправки): показываем подсказку, а не HTTP-демп
            raise RuntimeError(self._tr("mcp_no_input"))
        payload: dict = {"model": model, "input": inp, "stream": True}
        tools = self._mcp_tools(s)
        if tools:
            payload["tools"] = tools
        if s.get("temperature") is not None:
            payload["temperature"] = s["temperature"]
        try:
            max_out = int(s.get("max_tokens") or 0)
        except (TypeError, ValueError):
            max_out = 0
        if max_out > 0:
            payload["max_output_tokens"] = max_out
        try:
            penalty = float(s.get("repeat_penalty", 1.0))
        except (TypeError, ValueError):
            penalty = 1.0
        if penalty != 1.0:
            payload["repeat_penalty"] = penalty
        return payload

    @staticmethod
    def _preview(value: str, limit: int = _TOOL_PREVIEW) -> str:
        """Обрезать вывод инструмента: в UI/лог полезно, но не в чат-историю."""
        t = value or ""
        return t if len(t) <= limit else t[:limit] + "…"

    def _notify(self, on_event, name: str, **data):
        """Событие MCP для UI (статус/ошибка). Ошибка хендлера не рвёт стрим."""
        if on_event is None:
            return
        try:
            on_event(name, data)
        except Exception as ex:  # noqa: BLE001 — UI-хендлер не должен ломать стрим
            if self._log:
                self._log.warning("mcp event handler failed: %s", ex)

    @staticmethod
    def _mcp_usage(response: dict) -> dict:
        """usage ответа /v1/responses (OpenAI-ключи) -> ключи приложения.

        OpenAI-ответы пишут ``input_tokens`` / ``output_tokens`` /
        ``total_tokens``; разбивка reasoning приходит вложенной
        (``output_tokens_details.reasoning_tokens``). Приложение читает
        ``prompt_tokens`` / ``completion_tokens`` / ``reasoning_tokens`` /
        ``total_tokens``.
        """
        raw = response.get("usage") if isinstance(response, dict) else None
        raw = raw if isinstance(raw, dict) else {}
        usage: dict = {}
        for src, dst in (("input_tokens", "prompt_tokens"),
                         ("output_tokens", "completion_tokens"),
                         ("total_tokens", "total_tokens")):
            v = raw.get(src)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                usage[dst] = v
        details = raw.get("output_tokens_details")
        reasons = details.get("reasoning_tokens") if isinstance(details, dict) else None
        if isinstance(reasons, (int, float)) and not isinstance(reasons, bool):
            usage["reasoning_tokens"] = reasons
        if "total_tokens" not in usage and "prompt_tokens" in usage \
                and "completion_tokens" in usage:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return usage

    @staticmethod
    def _mcp_item_text(item: dict) -> str:
        """Текст элемента output[]: content бывает строкой или списком частей."""
        body = item.get("content")
        if isinstance(body, str):
            return body or ""
        if isinstance(body, list):
            out = []
            for part in body:
                if not isinstance(part, dict):
                    continue
                v = part.get("text")
                if isinstance(v, str):
                    out.append(v)
            return "".join(out)
        return ""

    @staticmethod
    def _mcp_aggr_text(response: dict) -> tuple[str, str]:
        """Текст и размышления из агрегированного output[] (запасной путь)."""
        text, reasoning = "", ""
        for item in (response.get("output") or []) if isinstance(response, dict) else []:
            if not isinstance(item, dict):
                continue
            body = LmClient._mcp_item_text(item)
            if item.get("type") == "reasoning" and body:
                reasoning += body
            elif item.get("type") == "message" and body:
                text += body
        return text, reasoning

    @staticmethod
    def _tool_field(j: dict, *names: str, limit: int = _TOOL_PREVIEW) -> str:
        """Первое непустое поле события tool_call.*.

        Имена полей в разных версиях LM Studio отличаются (``tool``/``name``,
        ``output``/``result``, ``reason``/``error``), поэтому берём известный
        набор. Объекты (arguments) приводим к компактному JSON.
        """
        for n in names:
            v = j.get(n)
            if isinstance(v, str):
                if v.strip():
                    return LmClient._preview(v, limit)
                continue  # пустая/пробельная строка — поля считаем нет
            if v not in (None, "", [], {}):
                try:
                    return LmClient._preview(json.dumps(v, ensure_ascii=False,
                                                       separators=(",", ":")), limit)
                except (TypeError, ValueError):
                    return LmClient._preview(str(v), limit)
        return ""

    @staticmethod
    def _tool_unwrap(value: str) -> str:
        """Распаковать JSON-строку аргументов/вывода инструмента.

        Локальные MCP-вызовы (``mcp_call``) шлют ``arguments`` и ``output``
        строкой JSON: вытаскиваем из output текст частей, аргументы приводим
        к компактному JSON. Мусор, который не парсится, возвращаем как есть.
        """
        t = value or ""
        if not t.strip().startswith(("[", "{")):
            return t
        try:
            parsed = json.loads(t)
        except ValueError:
            return t
        if isinstance(parsed, list):
            texts = [p.get("text") for p in parsed
                     if isinstance(p, dict) and isinstance(p.get("text"), str)]
            if texts:
                return "".join(str(x) for x in texts)
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        if isinstance(parsed, dict):
            return json.dumps(parsed, ensure_ascii=False, separators=(",", ":"))
        return t

    def _mcp_reason_key(self, text: str) -> str:
        """Какой локализованный ключ описывает ошибку MCP — по тексту ответа.

        Две частые причины названы прямо в тексте ошибок LM Studio:
        выключенный вызов серверов из ``mcp.json`` и выключенный Remote MCP
        (у /v1/responses имя настройки — «Remote MCP», Developer → Settings).
        """
        t = self._scrub(text or "").lower()
        if not t:
            return ""
        if "mcp.json" in t or "allow calling servers" in t:
            return "mcp_err_setting"
        if "remote mcp" in t or "per-request mcp" in t:
            return "mcp_err_remote"
        if "mcp" in t and any(w in t for w in ("disabled", "not enabled",
                                               "not supported", "unsupported",
                                               "unknown field", "invalid",
                                               "enable it")):
            return "mcp_err_remote"
        return ""

    def _mcp_http_error(self, code: int, body: str = "", has_images: bool = False) -> RuntimeError:
        """Локализованная диагностика /v1/responses.

        Порядок важный: если сервер прямо объяснил причину (выключенный
        ``mcp.json`` или Remote MCP), показываем её — она полезнее общего
        «нет доступа». Иначе 401/403 — общая авторизация (её тексты уже
        локализованы), 404/405/501 — эндпоинта /v1/responses нет, то есть
        старая сборка без OpenAI-совместимого MCP; изображения в запросе +
        400/422 с упоминанием vision/image — модель без поддержки картинок
        (предупреждаем и даём app.py повторить без них); прочие 400/422 —
        Remote MCP не включён или сервер отклонил tools-блок.
        """
        body = self._scrub(body or "")
        key = self._mcp_reason_key(body)
        if key:
            return RuntimeError(self._tr(key, t=self._preview(body, 160)))
        if code in AUTH_CODES:
            return self._auth_error(code)
        if code in (404, 405, 501):
            if self._log:
                self._log.error("responses endpoint missing (HTTP %s) — "
                                "LM Studio with /v1/responses + MCP required", code)
            return RuntimeError(self._tr("mcp_err_version", c=code))
        if has_images and code in (400, 422):
            low = body.lower()
            if any(w in low for w in ("vision", "image", "multimodal", "input_image")):
                if self._log:
                    self._log.warning("mcp server rejected images (HTTP %s)", code)
                # префикс «HTTP <code>:» — app.py ловит 400 и предлагает
                # повтор без картинок (confirm_image_retry)
                return RuntimeError(
                    f"HTTP {code}: {self._tr('mcp_err_image', t=self._preview(body, 160))}")
        if code in (400, 422) or not code:
            return RuntimeError(self._tr("mcp_err_remote", c=code))
        return RuntimeError(self._tr("mcp_error",
                                     t=self._preview(body, 160) or f"HTTP {code}"))

    @staticmethod
    def _mcp_has_images(msgs) -> bool:
        """Есть ли в запросе картинки (для диагностики их отвержения сервером)."""
        for m in msgs or []:
            if not isinstance(m, dict):
                continue
            content = m.get("content")
            if isinstance(content, list):
                for part in content:
                    if isinstance(part, dict) and part.get("type") == "image_url":
                        return True
        return False

    @staticmethod
    def _delta_field(j: dict) -> str:
        """Текст дельты /v1/responses: поле delta (OpenAI) или content."""
        for n in ("delta", "content", "text"):
            v = j.get(n)
            if isinstance(v, str) and v:
                return v
        return ""

    def _tool_start(self, item: dict, acc: dict, name: str, on_event) -> str:
        """Одноразовое событие tool_start для элемента function_call/tool_call.

        Ключ дедупликации — call_id (или имя): стрим-события и агрегированный
        ``response.completed`` не должны рисовать один вызов дважды.
        """
        call_id = str(item.get("call_id") or item.get("id") or "")
        key = call_id if call_id else (name or "?")
        if key in acc["tool_start"]:
            return key
        args = self._tool_field(item, "arguments", "args", "input", "parameters",
                                limit=1_000_000)
        args = self._preview(self._tool_unwrap(args), self._TOOL_ARGS_PREVIEW)
        acc["tool_start"].add(key)
        if call_id:
            acc["call_names"][call_id] = name
        if self._log:
            self._log.info("mcp tool call: %s %s", name or "?", args)
        self._notify(on_event, "tool_start", tool=name or "?", args=args)
        return key

    def _tool_done(self, item: dict, acc: dict, name: str, on_event) -> None:
        """Одноразовое событие tool_done; имя берём из пары function_call."""
        call_id = str(item.get("call_id") or item.get("id") or "")
        key = call_id if call_id else (name or "?")
        if key in acc["tool_done"]:
            return
        acc["tool_done"].add(key)
        out = self._tool_field(item, "output", "result", "content", "response",
                               limit=1_000_000)
        out = self._preview(self._tool_unwrap(out), self._TOOL_PREVIEW)
        name = name or acc["call_names"].get(call_id, key)
        if self._log:
            self._log.info("mcp tool %s -> %s", name, out)
        self._notify(on_event, "tool_done", tool=name, output=out)

    def _mcp_output_item(self, item: dict, acc: dict, on_event, final: bool) -> None:
        """Элемент output[] (или output_item события) -> события tool_* для UI.

        Формы элементов разные: OpenAI-стиль ``function_call`` +
        ``function_call_output``, нативный ``tool_call`` с результатом внутри
        и локальный MCP ``mcp_call`` (живой LM Studio шлёт именно его:
        ``name`` + JSON-строка ``arguments`` + JSON-строка ``output``).
        ``mcp_list_tools`` — автоматический листинг инструментов сервера,
        в чат не выводим (это не вызов).
        ``final=False`` (событие output_item.added у function_call) пропускаем:
        аргументы ещё стримятся и появятся в done/completed целиком.
        """
        if not isinstance(item, dict):
            return
        ttype = item.get("type")
        if ttype == "function_call":
            if not final:
                return
            name = self._tool_field(item, "tool", "name", "tool_name")
            self._tool_start(item, acc, name, on_event)
        elif ttype == "function_call_output":
            self._tool_done(item, acc, "", on_event)
        elif ttype in ("tool_call", "mcp_call"):
            if ttype == "mcp_call" and item.get("status") != "completed":
                # на output_item.added аргументы пустые/заглушка «{}»; всё
                # полное (arguments + output) приходит в output_item.done —
                # ждём его, чтобы не зафиксировать пустую строку вызова
                return
            name = self._tool_field(item, "tool", "name", "tool_name")
            self._tool_start(item, acc, name, on_event)
            if self._tool_field(item, "output", "result", "content", "response",
                                limit=1_000_000):
                self._tool_done(item, acc, name, on_event)

    def _mcp_completed(self, j: dict, acc: dict, on_delta: Callable, on_event) -> None:
        """Агрегированный response.completed: недостающее берём здесь.

        Если дельты текст уже отдали, их не дублируем; тулколлы и usage
        разбираем из ``output[]``/``usage`` целиком (с дедупликацией по
        call_id) — стрим мог их не прислать вовсе.
        """
        resp = j.get("response")
        resp = resp if isinstance(resp, dict) else j
        if not acc["content"] and not acc["reasoning"]:
            text, reasoning = self._mcp_aggr_text(resp)
            if text or reasoning:
                acc["content"], acc["reasoning"] = text, reasoning
                if text:
                    on_delta("content", text)
                if reasoning:
                    on_delta("reasoning", reasoning)
        for item in (resp.get("output") or []) if isinstance(resp, dict) else []:
            self._mcp_output_item(item, acc, on_event, final=True)
        u = self._mcp_usage(resp)
        if u:
            acc["usage"] = u
        if self._log:
            self._log.info("mcp response completed usage=%s", u)

    def _mcp_event(self, j: dict, name: str, acc: dict, on_delta: Callable,
                   on_event) -> None:
        """Разобрать одно событие стрима /v1/responses (OpenAI-совместимые SSE)."""
        if name == "response.output_text.delta":
            piece = self._delta_field(j)
            if piece:
                acc["content"] += piece
                on_delta("content", piece)
        elif name in ("response.reasoning_summary_text.delta",
                      "response.reasoning_text.delta"):
            piece = self._delta_field(j)
            if piece:
                acc["reasoning"] += piece
                on_delta("reasoning", piece)
        elif name in ("response.function_call_arguments.delta",
                      "response.mcp_call_arguments.delta"):
            # аргументы приходят частями; полные — в output_item.done/completed,
            # здесь ничего не показываем (иначе дублировали бы строку вызова)
            pass
        elif name == "response.failed":
            # OpenAI-style сбой: error лежит в response.error
            resp = j.get("response")
            raw_err = resp.get("error") if isinstance(resp, dict) else None
            raw_err = raw_err if isinstance(raw_err, dict) else {}
            msg = self._scrub(str(raw_err.get("message") or "response failed"))
            if not acc["stream_err"]:
                acc["stream_err"] = msg
            if self._log:
                self._log.warning("mcp response failed: %s", msg)
            self._notify(on_event, "error", message=msg,
                         kind=str(raw_err.get("type") or "unknown"))
        elif name in ("response.output_item.added", "response.output_item.done"):
            item = j.get("item")
            item = item if isinstance(item, dict) else j
            if item.get("type") == "function_call" and name == "response.output_item.added":
                return  # ждём done: аргументы ещё стримятся
            self._mcp_output_item(item, acc, on_event, final=True)
        elif name == "response.completed":
            self._mcp_completed(j, acc, on_delta, on_event)

    async def _mcp_chat_stream(self, msgs, model, s: dict, on_delta: Callable,
                               on_event):
        """Стрим через stateless POST /v1/responses (единственный путь с MCP).

        Возвращает ``(content, reasoning, usage)``: вся локальная история
        уходит в ``input``, серверной цепочки (``previous_response_id``) нет.
        """
        payload = self._mcp_payload(msgs, model, s)
        has_images = self._mcp_has_images(msgs)
        acc: dict = {"content": "", "reasoning": "", "usage": {}, "stream_err": "",
                     "tool_start": set(), "tool_done": set(), "call_names": {}}
        for attempt in range(2):  # тот же автореконнект, что и у /v1/chat/completions
            try:
                async with self._c.stream("POST", self._root() + self.MCP_CHAT_PATH,
                                          json=payload,
                                          headers=self._req_headers()) as r:
                    r.raise_for_status()
                    event = ""
                    async for line in r.aiter_lines():
                        if self._cancel:
                            raise StreamCancelled()
                        line = (line or "").strip()
                        if not line:
                            continue
                        if line.startswith("event:"):
                            event = line[6:].strip()
                            continue
                        if not line.startswith("data:"):
                            continue
                        raw = line[5:].strip()
                        if not raw or raw == "[DONE]":
                            continue
                        try:
                            j = json.loads(raw)
                        except ValueError:
                            continue
                        if not isinstance(j, dict):
                            continue
                        name = str(j.get("type") or event)
                        if name == "error":
                            raw_err = j.get("error")
                            err: dict = raw_err if isinstance(raw_err, dict) else {}
                            if not acc["stream_err"]:
                                acc["stream_err"] = self._scrub(
                                    str(err.get("message") or name))
                            if self._log:
                                self._log.warning("mcp stream error: %s", acc["stream_err"])
                            self._notify(on_event, "error",
                                         message=acc["stream_err"],
                                         kind=str(err.get("type") or "unknown"))
                            continue
                        self._mcp_event(j, name, acc, on_delta, on_event)
                break
            except StreamCancelled:
                raise
            except httpx.HTTPStatusError as e:
                # 401/403, «эндпоинта нет», отвержение картинок и Remote MCP ->
                # свои локализованные сообщения вместо HTTP-дампа
                raise self._mcp_http_error(e.response.status_code,
                                           await self._safe_body(e),
                                           has_images) from e
            except httpx.HTTPError as e:
                if self._cancel:
                    raise StreamCancelled() from e
                if acc["content"] or acc["reasoning"] or attempt == 1:
                    raise RuntimeError(self._tr("net_err", e=self._scrub(str(e))))
                if self._log:
                    self._log.warning("mcp sse interrupted, retrying: %s", self._scrub(str(e)))
                await asyncio.sleep(1.5 * (attempt + 1))
                continue
        if acc["stream_err"] and not (acc["content"] or acc["reasoning"]):
            # текста нет — пользователю нужно объяснение, а не тишина;
            # по тексту ошибки разбираем частые причины (mcp.json выключен,
            # Remote MCP не включён) в отдельные подсказки
            key = self._mcp_reason_key(acc["stream_err"])
            if key:
                raise RuntimeError(self._tr(key, t=self._preview(acc["stream_err"], 160)))
            raise RuntimeError(self._tr("mcp_error", t=acc["stream_err"]))
        return acc["content"], acc["reasoning"], acc["usage"]
