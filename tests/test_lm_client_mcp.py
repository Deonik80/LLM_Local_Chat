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

"""LmClient: работа с MCP-серверами из mcp.json (stateless POST /v1/responses).

Живого сервера нет, поэтому httpx.MockTransport отдаёт заранее заданный SSE
поток. Проверяем ровно то, что задано документацией LM Studio: блок
``tools`` с ``{"type": "mcp", "server_label": ...}``, Bearer-заголовок,
OpenAI-совместимые события (``response.output_text.delta`` /
``response.output_item.done`` / ``response.completed``), агрегированный
``usage`` и stateless ``input`` со ВСЕЙ локальной историей (включая
ассистентские реплики) — серверной цепочки (``previous_response_id``) нет.
Плюс контроль, что при выключенном MCP приложение по-прежнему ходит
в ``/v1/chat/completions`` (там поддержки MCP нет).
"""
from __future__ import annotations
import asyncio
import json

import httpx
import pytest

from lm_client import (AuthError, LmClient, StreamCancelled, mcp_active,
                       mcp_enabled_from_settings, mcp_servers_from_settings)

BASE = "http://server.local:1234/v1"
ROOT = "http://server.local:1234"
RESPONSES = "/v1/responses"
OPENAI = "/v1/chat/completions"
KEY = "lm-studio-secret-key"


def _tr(key, **kw) -> str:
    """Переводчик-заглушка: код ключа + параметры (как реальный tr)."""
    return f"{key}:{kw.get('t', kw.get('e', ''))}"


class Stream:
    """MockTransport-обработчик: пишет запросы, отдаёт SSE поток (или HTTP-ошибку)."""

    def __init__(self, sse: str = "", status: int = 200, body: str = "{}"):
        self.sse, self.status, self.body, self.seen = sse, status, body, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        if self.status != 200:
            return httpx.Response(self.status, text=self.body,
                                  headers={"content-type": "application/json"})
        return httpx.Response(200, text=self.sse, headers={"content-type": "text/event-stream"})

    @property
    def payload(self) -> dict:
        return json.loads(self.seen[-1].content.decode())


def _client(rec: Stream, **kw) -> LmClient:
    kw.setdefault("tr", _tr)
    return LmClient(f"{BASE}/models", f"{BASE}/chat/completions", base_url=BASE,
                    transport=httpx.MockTransport(rec), **kw)


def _run(coro):
    return asyncio.run(coro)


def _settings(**kw) -> dict:
    s = {"temperature": 0.7, "max_tokens": 16, "repeat_penalty": 1.0, "context_length": 8192}
    s.update(kw)
    return s


def _mcp_settings(**kw) -> dict:
    """Настройки в том виде, в каком их пишет UI: mcp_enabled + список серверов."""
    if "mcp_servers" not in kw:
        kw["mcp_servers"] = ["playwright"]
    return _settings(mcp_enabled=True, **kw)


def _event(name: str, **data) -> str:
    """Кадр SSE: 'event: <type>' + 'data: <json>'."""
    payload = {"type": name}
    payload.update(data)
    return f"event: {name}\ndata: {json.dumps(payload)}\n\n"


def _completed(text: str = "hello", reasoning: str = "", usage: dict | None = None,
               output: list | None = None) -> str:
    """Финальный агрегированный ответ /v1/responses (response.completed)."""
    if output is None:
        output = []
        if reasoning:
            output.append({"type": "reasoning",
                           "content": [{"type": "summary_text", "text": reasoning}]})
        output.append({"type": "message",
                       "content": [{"type": "output_text", "text": text}]})
    item = {"type": "response.completed", "response": {
        "id": "resp_1", "object": "response", "output": output}}
    if usage is not None:
        item["response"]["usage"] = usage
    return _event("response.completed", **item)


def _delta(text: str = "hello", reasoning: str = "") -> str:
    sse = ""
    if reasoning:
        sse += _event("response.reasoning_summary_text.delta", delta=reasoning)
    sse += _event("response.output_text.delta", delta=text)
    return sse


MSGS = [{"role": "system", "content": "be brief"},
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "user", "content": [{"type": "text", "text": "open lmstudio.ai"}]}]


def _expected_input() -> list:
    """Вся локальная история -> OpenAI-формат /v1/responses (stateless)."""
    return [
        {"role": "system", "content": [{"type": "input_text", "text": "be brief"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "hi"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "hello"}]},
        {"role": "user", "content": [{"type": "input_text", "text": "open lmstudio.ai"}]},
    ]


# --- helpers: settings -> режим/список серверов ---
def test_mcp_enabled_from_settings():
    # тумблер хранится bool, но из env/ручной правки приходит строкой
    assert mcp_enabled_from_settings({"mcp_enabled": True}) is True
    assert mcp_enabled_from_settings({"mcp_enabled": False}) is False
    for yes in ("on", "true", "1", "YES", " y "):
        assert mcp_enabled_from_settings({"mcp_enabled": yes}) is True
    for no in ("off", "false", "0", "", "maybe", None, 0, []):
        assert mcp_enabled_from_settings({"mcp_enabled": no}) is False
    # по умолчанию всё выключено — старый путь /v1/chat/completions
    assert mcp_enabled_from_settings({}) is False
    assert mcp_enabled_from_settings(None) is False
    # ранняя сборка сохраняла mcp_mode: off/on — не теряем настройку
    assert mcp_enabled_from_settings({"mcp_mode": "on"}) is True
    assert mcp_enabled_from_settings({"mcp_mode": "off"}) is False
    # явный mcp_enabled побеждает устаревший mcp_mode
    assert mcp_enabled_from_settings({"mcp_enabled": True, "mcp_mode": "off"}) is True


def test_mcp_servers_normalizes_labels():
    # короткая метка -> полный id из mcp.json
    assert mcp_servers_from_settings({"mcp_servers": "playwright"}) == ["mcp/playwright"]
    assert mcp_servers_from_settings({"mcp_servers": "mcp/playwright"}) == ["mcp/playwright"]
    # разделители: запятая, точка с запятой, пробел, перевод строки
    assert mcp_servers_from_settings({"mcp_servers": "a, b\nc;d  e"}) == \
        ["mcp/a", "mcp/b", "mcp/c", "mcp/d", "mcp/e"]
    # список из settings.json принимается так же, дубликаты и мусор отбрасываются
    assert mcp_servers_from_settings({"mcp_servers": ["a", "mcp/a", " A ", "", None]}) == ["mcp/a"]
    assert mcp_servers_from_settings({"mcp_servers": "  ,  "}) == []
    assert mcp_servers_from_settings({"mcp_servers": 42}) == []
    assert mcp_servers_from_settings({}) == []


def test_mcp_active_requires_enabled_and_servers():
    assert mcp_active({"mcp_enabled": True, "mcp_servers": ["playwright"]}) is True
    # включён, но серверов нет -> /v1/responses не включаем
    assert mcp_active({"mcp_enabled": True, "mcp_servers": []}) is False
    # серверы выбраны, но тумблер выключен -> обычный путь
    assert mcp_active({"mcp_enabled": False, "mcp_servers": ["playwright"]}) is False
    assert mcp_active({}) is False


# --- маршрутизация транспорта ---
def test_mcp_disabled_keeps_openai_compatible_path_untouched():
    """Требование «минимум вторжений»: при mcp_enabled=false запрос и разбор
    ответа идут ровно как раньше — /v1/chat/completions, без tools/input."""
    sse = ('data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
           'data: {"choices":[{"delta":{}}],'
           '"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\n'
           "data: [DONE]\n\n")
    rec = Stream(sse)
    got: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _settings(), lambda k, t: got.append((k, t))))
    assert str(rec.seen[-1].url) == f"{ROOT}{OPENAI}"
    p = rec.payload
    assert p["messages"] == MSGS  # вся локальная история уходит как раньше
    assert p["model"] == "m1" and p["stream"] is True
    assert "tools" not in p and "input" not in p
    assert res[0] == "hi"
    assert res[2]["total_tokens"] == 3  # usage разобран как прежде
    assert got == [("content", "hi")]


def test_usage_only_chunk_with_empty_choices_does_not_crash():
    """Легаси-баг: usage-only чанк с `choices: []` (стандартен при include_usage)
    падал с IndexError; guard пропускает его и разбирает usage."""
    sse = ('data: {"choices":[{"delta":{"content":"hi"}}]}\n\n'
           'data: {"choices":[],'
           '"usage":{"prompt_tokens":1,"completion_tokens":2,"total_tokens":3}}\n\n'
           "data: [DONE]\n\n")
    rec = Stream(sse)
    got: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _settings(), lambda k, t: got.append((k, t))))
    assert res[0] == "hi"
    assert res[2]["total_tokens"] == 3  # usage из пустого чанка разобран
    assert got == [("content", "hi")]


def test_mcp_servers_selected_but_disabled_still_use_openai_path():
    """Выбранные серверы при выключенном тумблере не включают MCP."""
    rec = Stream('data: {"choices":[{"delta":{"content":"x"}}]}\n\ndata: [DONE]\n\n')
    cl = _client(rec)
    _run(cl.chat_stream(MSGS, "m1", _settings(mcp_enabled=False,
                                              mcp_servers=["playwright"]),
                        lambda k, t: None))
    assert str(rec.seen[-1].url) == f"{ROOT}{OPENAI}"
    assert "tools" not in rec.payload


def test_mcp_on_switches_to_responses_endpoint():
    rec = Stream(_delta() + _completed())
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert str(rec.seen[-1].url) == f"{ROOT}{RESPONSES}"
    assert res[0] == "hello"
    assert len(res) == 3  # (content, reasoning, usage) — без response_id


# --- payload /v1/responses ---
def test_responses_payload_shape():
    rec = Stream(_delta() + _completed())
    cl = _client(rec, api_key=KEY)
    _run(cl.chat_stream(MSGS, "granite", _mcp_settings(temperature=0.2, max_tokens=64,
                                                       repeat_penalty=1.1),
                        lambda k, t: None))
    p = rec.payload
    assert p["model"] == "granite"
    assert p["stream"] is True
    assert p["tools"] == [{"type": "mcp", "server_label": "playwright"}]
    assert p["temperature"] == 0.2
    assert p["max_output_tokens"] == 64
    assert p["repeat_penalty"] == 1.1
    # stateless: вся локальная история, в т.ч. ассистентская реплика
    assert p["input"] == _expected_input()
    # нативных/серверных механизмов больше нет
    assert "previous_response_id" not in p
    assert "integrations" not in p
    assert "system_prompt" not in p
    assert "context_length" not in p
    assert "messages" not in p
    # авторизация из прошлой фичи работает и здесь
    assert rec.seen[-1].headers.get("authorization") == f"Bearer {KEY}"


def test_responses_payload_tool_labels_strip_mcp_prefix():
    rec = Stream(_delta() + _completed())
    cl = _client(rec)
    _run(cl.chat_stream([{"role": "user", "content": "q"}], "m1",
                        _mcp_settings(mcp_servers=["mcp/playwright", "brave"]),
                        lambda k, t: None))
    assert rec.payload["tools"] == [
        {"type": "mcp", "server_label": "playwright"},
        {"type": "mcp", "server_label": "brave"},
    ]


def test_responses_payload_omits_defaults():
    rec = Stream(_delta() + _completed())
    cl = _client(rec)
    _run(cl.chat_stream([{"role": "user", "content": "q"}], "m1", _mcp_settings(),
                        lambda k, t: None))
    p = rec.payload
    assert "repeat_penalty" not in p   # 1.0 -> дефолт, поле не шлём
    assert p["temperature"] == 0.7     # не-дефолт уходит как обычно
    assert rec.seen[-1].headers.get("authorization") is None


def test_responses_payload_converts_images():
    rec = Stream(_delta() + _completed())
    cl = _client(rec)
    url = "data:image/png;base64,AAA"
    msgs = [{"role": "user", "content": [
        {"type": "text", "text": "what is it"},
        {"type": "image_url", "image_url": {"url": url}}]}]
    _run(cl.chat_stream(msgs, "m1", _mcp_settings(), lambda k, t: None))
    assert rec.payload["input"] == [{"role": "user", "content": [
        {"type": "input_text", "text": "what is it"},
        {"type": "input_image", "image_url": url}]}]


def test_mcp_input_role_based_part_types():
    """Текстовые части зависят от роли (живой факт 0.4.x): у assistant —
    только output_text, у system/user — input_text; картинки — только user."""
    rec = Stream(_delta() + _completed())
    cl = _client(rec)
    msgs = [
        {"role": "system", "content": "s"},
        {"role": "assistant", "content": "a"},
        {"role": "user", "content": [{"type": "text", "text": "q"},
                                     {"type": "image_url",
                                      "image_url": {"url": "data:image/png;base64,AAA"}}]},
        {"role": "assistant", "content": [{"type": "text", "text": "a2"},
                                          {"type": "image_url",
                                           "image_url": {"url": "data:image/png;base64,BB"}}]},
    ]
    _run(cl.chat_stream(msgs, "m1", _mcp_settings(), lambda k, t: None))
    assert rec.payload["input"] == [
        {"role": "system", "content": [{"type": "input_text", "text": "s"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "a"}]},
        {"role": "user", "content": [
            {"type": "input_text", "text": "q"},
            {"type": "input_image", "image_url": "data:image/png;base64,AAA"}]},
        {"role": "assistant", "content": [{"type": "output_text", "text": "a2"}]},
    ]


def test_responses_payload_blank_content_dropped():
    rec = Stream(_delta() + _completed())
    cl = _client(rec)
    msgs = [{"role": "user", "content": "   "},
            {"role": "user", "content": "real"}]
    _run(cl.chat_stream(msgs, "m1", _mcp_settings(), lambda k, t: None))
    assert rec.payload["input"] == [{"role": "user",
                                     "content": [{"type": "input_text", "text": "real"}]}]


def test_responses_payload_without_user_message():
    rec = Stream(_delta() + _completed())
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_no_input"):
        _run(cl.chat_stream([{"role": "system", "content": "only system"}],
                            "m1", _mcp_settings(), lambda k, t: None))
    assert rec.seen == []  # до сервера не дошли


# --- разбор событий стрима ---
def test_stream_deltas_and_final_result():
    rec = Stream(_delta("hi there", reasoning="thinking") + _completed("hi there"))
    got: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: got.append((k, t))))
    assert got == [("reasoning", "thinking"), ("content", "hi there")]
    assert res[0] == "hi there"
    assert res[1] == "thinking"


def test_stream_usage_mapped_to_app_keys():
    usage = {"input_tokens": 11, "output_tokens": 3, "total_tokens": 15,
             "output_tokens_details": {"reasoning_tokens": 1}}
    rec = Stream(_delta("hi there") + _completed("hi there", usage=usage))
    cl = _client(rec)
    content, reasoning, u = _run(
        cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert (content, reasoning) == ("hi there", "")
    assert u["prompt_tokens"] == 11
    assert u["completion_tokens"] == 3
    assert u["reasoning_tokens"] == 1
    assert u["total_tokens"] == 15


def test_stream_usage_total_from_sum_when_missing():
    usage = {"input_tokens": 11, "output_tokens": 3}
    rec = Stream(_delta() + _completed(usage=usage))
    cl = _client(rec)
    _, _, u = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert u["total_tokens"] == 14


def test_completed_output_used_when_no_deltas():
    """Сервер может не слать дельты — берём текст из агрегированного output[]. """
    rec = Stream(_completed(text="final answer", reasoning="because"))
    got: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(),
                              lambda k, t: got.append((k, t))))
    assert res[0] == "final answer"
    assert res[1] == "because"
    assert got == [("content", "final answer"), ("reasoning", "because")]


def test_completed_multiline_string_content():
    """У output[] текст может быть строкой, а не списком частей — читаем оба."""
    rec = Stream(_event("response.completed", response={"output": [
        {"type": "message", "content": "plain string"}]}))
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert res[0] == "plain string"


def test_event_name_used_when_payload_has_no_type():
    """Тип события может прийти только в строке 'event:'."""
    sse = "event: response.output_text.delta\ndata: {\"delta\": \"typed\"}\n\n"
    rec = Stream(sse)
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert res[0] == "typed"


# --- вызовы инструментов ---
def _tool_sse() -> str:
    """OpenAI-стиль: started -> аргументы (частями) -> done -> результат."""
    return (_event("response.output_item.added", item={"type": "function_call",
                                                       "call_id": "call_1",
                                                       "name": "browser_navigate",
                                                       "arguments": ""})
            + _event("response.function_call_arguments.delta", item_id="fc_1",
                     delta='{"url":"https://lmstudio.ai"}')
            + _event("response.output_item.done", item={"type": "function_call",
                                                        "call_id": "call_1",
                                                        "name": "browser_navigate",
                                                        "arguments": '{"url":"https://lmstudio.ai"}'})
            + _event("response.output_item.added", item={"type": "function_call_output",
                                                         "call_id": "call_1",
                                                         "output": "page loaded"})
            + _event("response.output_item.done", item={"type": "function_call_output",
                                                        "call_id": "call_1",
                                                        "output": "page loaded"}))


def test_tool_events_from_output_item_done():
    """Вызов инструмента рисуется строкой в чате; вывод не попадает в ответ."""
    sse = (_tool_sse()
           + _event("response.output_text.delta", delta="opened")
           + _completed("opened", output=[]))
    rec = Stream(sse)
    events: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(),
                              lambda k, t: None,
                              on_event=lambda name, data: events.append((name, data))))
    names = [n for n, _ in events]
    assert names == ["tool_start", "tool_done"]
    assert events[0][1]["tool"] == "browser_navigate"
    # аргументы приходят из done (полные), а не из пустого added
    assert events[0][1]["args"] == '{"url":"https://lmstudio.ai"}'
    assert events[1][1]["output"] == "page loaded"
    assert res[0] == "opened"


def test_tool_events_fall_back_to_completed():
    """Сервер может прислать только response.completed — события берём оттуда
    (и не дублируем, если стрим их уже отдал)."""
    output = [{"type": "function_call", "call_id": "call_1",
               "name": "brave_web_search",
               "arguments": '{"query":"lm studio mcp","count":3}'},
              {"type": "function_call_output", "call_id": "call_1",
               "output": '{"results": ["a", "b"]}'},
              {"type": "message", "content": [{"type": "output_text", "text": "done"}]}]
    rec = Stream(_completed(output=output))
    events: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                              on_event=lambda name, data: events.append((name, data))))
    assert [n for n, _ in events] == ["tool_start", "tool_done"]
    by_name = dict(events)
    assert by_name["tool_start"]["tool"] == "brave_web_search"
    assert by_name["tool_start"]["args"] == '{"query":"lm studio mcp","count":3}'
    assert '"a"' in by_name["tool_done"]["output"]
    assert res[0] == "done"


def test_tool_events_not_duplicated_when_stream_and_completed_present():
    """Тот же вызов приходит и стрим-событием, и в completed — UI видит один."""
    output = [{"type": "function_call", "call_id": "call_1", "name": "web",
               "arguments": "{}"},
              {"type": "function_call_output", "call_id": "call_1", "output": "ok"}]
    rec = Stream(_tool_sse() + _completed(output=output))
    events: list = []
    cl = _client(rec)
    _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                        on_event=lambda name, data: events.append((name, data))))
    assert [n for n, _ in events] == ["tool_start", "tool_done"]


def test_tool_call_native_style_inline_output():
    """Нативный элемент tool_call несёт результат в себе — start+done из одного."""
    output = [{"type": "tool_call", "name": "open_browser",
               "arguments": {"url": "https://lmstudio.ai"},
               "output": "browser opened"},
              {"type": "message", "content": "ok"}]
    rec = Stream(_completed(output=output))
    events: list = []
    cl = _client(rec)
    _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                        on_event=lambda name, data: events.append((name, data))))
    assert [n for n, _ in events] == ["tool_start", "tool_done"]
    # объект-аргументы приводим к компактному JSON, иначе в строке будет «{…}»
    assert '"url"' in events[0][1]["args"]
    assert events[1][1]["output"] == "browser opened"


def test_tool_fields_tolerate_alternative_names():
    cl = _client(Stream())
    # разные версии LM Studio пишут аргументы/ошибку по-разному
    assert cl._tool_field({"name": "t"}, "tool", "name") == "t"
    assert cl._tool_field({"args": '{"a":1}'}, "arguments", "args") == '{"a":1}'
    assert cl._tool_field({"detail": "boom"}, "error", "reason", "detail") == "boom"
    assert cl._tool_field({"tool": "t", "output": "  "}, "output", "result") == ""  # пустое
    assert cl._tool_field({}, "tool", "name") == ""


# --- ошибки ---
def test_error_event_without_content_raises():
    sse = _event("error", error={"type": "mcp_connection_error",
                                 "message": "Cannot connect to mcp/playwright"})
    rec = Stream(sse)
    events: list = []
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_error"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                            on_event=lambda name, data: events.append((name, data))))
    assert events and events[0][0] == "error"


def test_error_event_after_text_keeps_answer():
    """Частичный ответ не выбрасываем из-за ошибки в конце потока."""
    sse = (_event("response.output_text.delta", delta="partial")
           + _event("error", error={"type": "internal_error", "message": "boom"})
           + _completed("partial", output=[]))
    rec = Stream(sse)
    events: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                              on_event=lambda name, data: events.append((name, data))))
    assert res[0] == "partial"
    assert [n for n, _ in events] == ["error"]


def test_auth_error_on_responses_endpoint():
    rec = Stream(status=401, body='{"error":"unauthorized"}')
    cl = _client(rec, api_key="wrong-key")
    with pytest.raises(AuthError) as ei:
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert ei.value.code == 401


def test_missing_responses_endpoint_reports_version_requirement():
    """Старой сборки эндпоинта /v1/responses просто нет -> отдельный текст."""
    for status in (404, 405, 501):
        rec = Stream(status=status, body='{"error":"Not Found"}')
        cl = _client(rec)
        with pytest.raises(RuntimeError, match="mcp_err_version"):
            _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))


def test_rejected_tools_block_reports_remote_mcp():
    rec = Stream(status=400, body='{"error":"Unknown field: tools"}')
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_err_remote"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))


def test_disabled_remote_mcp_setting_reports_switch():
    """Выключен «Remote MCP» — сервер проговорил это в тексте."""
    rec = Stream(status=403,
                 body='{"error":"Tool calls are blocked. Enable Remote MCP in Developer settings"}')
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_err_remote"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))


def test_disabled_mcp_json_setting_reports_switch():
    """Выключен «Allow calling servers from mcp.json» — отдельная подсказка."""
    rec = Stream(status=403,
                 body='{"error":"Tool calls are blocked. Enable \'Allow calling servers from mcp.json\'"}')
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_err_setting"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))


def test_stream_error_about_remote_mcp_classified():
    sse = _event("error", error={"type": "tool_error",
                                 "message": "MCP is disabled: enable Remote MCP"})
    rec = Stream(sse)
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_err_remote"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))


def test_stream_error_about_mcp_json_setting_classified():
    sse = _event("error", error={"type": "tool_error",
                                 "message": "MCP integration is disabled: check mcp.json settings"})
    rec = Stream(sse)
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_err_setting"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))


def test_mcp_error_message_never_leaks_api_key():
    """Ключ из auth_mode не должен попасть ни в текст ошибки MCP, ни в лог."""
    import logging

    records: list = []

    class Cap(logging.Handler):
        def emit(self, record):
            records.append(record.getMessage())

    log = logging.getLogger("mcp-leak-test")
    log.setLevel(logging.DEBUG)
    log.addHandler(Cap())
    rec = Stream(status=400, body='{"error":"bad request: lm-studio-secret-key"}')
    cl = _client(rec, api_key=KEY, log=log)
    with pytest.raises(RuntimeError) as ei:
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert KEY not in str(ei.value)
    assert all(KEY not in m for m in records)


def test_http_error_body_reported():
    rec = Stream(status=500, body="boom")
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_error"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))


def test_cancel_raises_stream_cancelled():
    rec = Stream(_event("response.output_text.delta", delta="a")
                 + _event("response.output_text.delta", delta="b"))
    cl = _client(rec)
    with pytest.raises(StreamCancelled):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(),
                            lambda k, t: cl.cancel()))  # стоп после первого дельта


def test_broken_event_handler_does_not_break_stream():
    def boom(name, data):
        raise ValueError("ui down")

    rec = Stream(_delta("still fine") + _completed("still fine"))
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None, on_event=boom))
    assert res[0] == "still fine"


# --- локальный MCP-вызов (живой LM Studio шлёт mcp_call, не tool_call) ---

def test_mcp_call_item_streams_compact_tool_line():
    """output_item.added/done c mcp_call -> компактная строка tool_start/tool_done.

    ``arguments`` и ``output`` у mcp_call приходят строкой JSON — их нужно
    распаковать (текст частей output, компактный JSON аргументов).
    """
    added = _event("response.output_item.added", item={
        "type": "mcp_call", "id": "mcp_1", "server_label": "fetch", "name": "fetch",
        "arguments": "{}", "status": "in_progress"})
    done = _event("response.output_item.done", item={
        "type": "mcp_call", "id": "mcp_1", "server_label": "fetch", "name": "fetch",
        "arguments": '{"url":"https://example.com"}',
        "output": '[{"type":"text","text":"Contents of https://example.com/\\n<p>Example Domain</p>"}]',
        "status": "completed"})
    sse = added + done + _delta("done") + _completed("done")
    rec = Stream(sse)
    events: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                              on_event=lambda name, data: events.append((name, data))))
    assert res[0] == "done"
    assert [n for n, _ in events] == ["tool_start", "tool_done"]
    assert events[0][1]["tool"] == "fetch"
    assert "example.com" in events[0][1]["args"]  # аргументы распакованы из JSON-строки
    assert "Example Domain" in events[1][1]["output"]  # текст частей output


def test_mcp_call_in_completed_aggregation():
    """Агрегированный output c mcp_call/mcp_list_tools -> одна строка вызова."""
    output = [
        {"type": "mcp_list_tools", "id": "t_0", "server_label": "fetch",
         "tools": [{"name": "fetch", "description": "Fetch a URL"}]},
        {"type": "reasoning", "content": [{"type": "summary_text", "text": "думаю"}]},
        {"type": "mcp_call", "id": "mcp_9", "server_label": "fetch", "name": "fetch",
         "arguments": '{"url":"https://example.com"}',
         "output": '[{"type":"text","text":"Example Domain"}]', "status": "completed"},
        {"type": "message", "content": [{"type": "output_text", "text": "ok"}]},
    ]
    sse = _completed("ok", usage={"input_tokens": 10, "output_tokens": 2,
                                  "total_tokens": 12},
                     output=output)
    rec = Stream(sse)
    events: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                              on_event=lambda name, data: events.append((name, data))))
    assert res[0] == "ok"
    assert res[2] == {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    # mcp_list_tools — не вызов, в чат не рисуем; mcp_call — одна пара событий
    assert [n for n, _ in events] == ["tool_start", "tool_done"]
    assert events[1][1]["output"] == "Example Domain"


def test_mcp_list_tools_item_ignored_in_stream():
    sse = (_event("response.output_item.added", item={
        "type": "mcp_list_tools", "id": "t_0", "server_label": "fetch",
        "tools": [{"name": "fetch"}]})
        + _event("response.output_item.done", item={
            "type": "mcp_list_tools", "id": "t_0", "server_label": "fetch",
            "tools": [{"name": "fetch"}]})
        + _delta("ok") + _completed("ok"))
    rec = Stream(sse)
    events: list = []
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                              on_event=lambda name, data: events.append((name, data))))
    assert res[0] == "ok"
    assert events == []  # листинг инструментов не рисуем


def test_mcp_call_arguments_delta_does_not_add_text():
    """Частичные аргументы не дублируют строку вызова и не лезут в ответ."""
    sse = (_event("response.mcp_call_arguments.delta", delta='{"url":', item_id="mcp_1")
           + _event("response.mcp_call_arguments.delta", delta='"example.com"}', item_id="mcp_1")
           + _delta("x") + _completed("x"))
    rec = Stream(sse)
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert res[0] == "x"


def test_tool_unwrap_extracts_text_and_json():
    cl = _client(Stream())
    assert cl._tool_unwrap('[{"type":"text","text":"A"},{"type":"text","text":"B"}]') == "AB"
    assert cl._tool_unwrap('{"url":"https://x"}') == '{"url":"https://x"}'
    assert cl._tool_unwrap("not json") == "not json"
    assert cl._tool_unwrap("") == ""


# --- response.failed (OpenAI-style сбой стрима) ---

def test_response_failed_event_raises_localized():
    sse = _event("response.failed", response={
        "id": "resp_1",
        "error": {"type": "server_error", "message": "model exploded"}})
    rec = Stream(sse)
    events: list = []
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_error"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None,
                            on_event=lambda name, data: events.append((name, data))))
    assert events and events[0][0] == "error"
    assert events[0][1]["message"] == "model exploded"


def test_response_failed_after_text_keeps_answer():
    sse = (_event("response.output_text.delta", delta="partial")
           + _event("response.failed", response={
               "id": "resp_1",
               "error": {"type": "server_error", "message": "boom"}}))
    rec = Stream(sse)
    cl = _client(rec)
    res = _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))
    assert res[0] == "partial"


# --- изображения: отвержение сервером -> деградация на text-only повтор ---

MSGS_IMG = MSGS + [{"role": "user",
                    "content": [{"type": "image_url",
                                 "image_url": {"url": "data:image/png;base64,QUJD"}}]}]


def test_mcp_has_images_detects_image_parts():
    cl = _client(Stream())
    assert cl._mcp_has_images(MSGS_IMG)
    assert not cl._mcp_has_images(MSGS)


def test_image_rejection_offers_text_only_retry():
    """400 с упоминанием vision + картинки в запросе -> «HTTP 400: …», без MCP-подсказки."""
    rec = Stream(status=400, body='{"error":"image inputs are not supported by this model"}')
    cl = _client(rec)
    with pytest.raises(RuntimeError) as ei:
        _run(cl.chat_stream(MSGS_IMG, "m1", _mcp_settings(), lambda k, t: None))
    msg = str(ei.value)
    assert "HTTP 400" in msg  # app.py ловит 400 и предлагает повтор без картинок
    assert "mcp_err_image" in msg


def test_image_body_without_images_reports_remote_mcp():
    """Тот же 400, но без картинок в запросе — обычная диагностика MCP."""
    rec = Stream(status=400, body='{"error":"image inputs are not supported"}')
    cl = _client(rec)
    with pytest.raises(RuntimeError, match="mcp_err_remote"):
        _run(cl.chat_stream(MSGS, "m1", _mcp_settings(), lambda k, t: None))