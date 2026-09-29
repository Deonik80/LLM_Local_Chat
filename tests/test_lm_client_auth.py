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

"""LmClient: Authorization API key (инъекция заголовка + 401/403).

httpx.MockTransport перехватывает запросы без сети: проверяем, что
``Authorization: Bearer <key>`` уходит в КАЖДЫЙ вызов клиента, что без
ключа заголовка нет вообще и что отказ по авторизации превращается в
локализованную ошибку, а не в HTTP-дамп.
"""
from __future__ import annotations
import asyncio
import json

import httpx
import pytest

from lm_client import AuthError, LmClient, StreamCancelled, api_key_from_settings

BASE = "http://server.local:1234/v1"
KEY = "lm-studio-secret-key"


def _tr(key, **kw) -> str:
    """Переводчик-заглушка: код ключа + параметры (как реальный tr)."""
    return f"{key}:{kw.get('code', '')}"


class Recorder:
    """MockTransport-обработчик: пишет заголовки, отдаёт заданный ответ."""

    def __init__(self, status: int = 200, body: str = '{"data": [{"id": "m1"}]}'):
        self.status, self.body, self.seen = status, body, []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen.append(request)
        return httpx.Response(self.status, text=self.body,
                              headers={"content-type": "application/json"})

    @property
    def auth(self) -> list:
        return [r.headers.get("authorization") for r in self.seen]


def _client(rec: Recorder, **kw) -> LmClient:
    kw.setdefault("tr", _tr)
    return LmClient(f"{BASE}/models", f"{BASE}/chat/completions", base_url=BASE,
                    transport=httpx.MockTransport(rec), **kw)


def _run(coro):
    return asyncio.run(coro)


def _sse(text: str = "hi") -> str:
    chunk = {"choices": [{"delta": {"content": text}}]}
    return f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n"


def _settings() -> dict:
    return {"temperature": 0.7, "max_tokens": 16}


# --- режим доступа -> ключ (settings.json -> заголовок) ---
def test_api_key_from_settings_mode_none_sends_nothing():
    assert api_key_from_settings({"auth_mode": "none", "api_key": KEY}) == ""
    assert api_key_from_settings({"auth_mode": "none"}) == ""
    assert api_key_from_settings({}) == ""
    assert api_key_from_settings({"auth_mode": None, "api_key": KEY}) == ""


def test_api_key_from_settings_mode_api_key():
    assert api_key_from_settings({"auth_mode": "api_key", "api_key": KEY}) == KEY
    assert api_key_from_settings({"auth_mode": "api_key", "api_key": f"  {KEY} "}) == KEY
    # режим включён, но ключ не задан -> тоже без заголовка
    assert api_key_from_settings({"auth_mode": "api_key", "api_key": ""}) == ""
    assert api_key_from_settings({"auth_mode": "api_key"}) == ""


def test_api_key_from_settings_ignores_garbage():
    assert api_key_from_settings({"auth_mode": "basic", "api_key": KEY}) == ""
    assert api_key_from_settings({"auth_mode": "api_key", "api_key": 42}) == ""
    assert api_key_from_settings({"auth_mode": "api_key", "api_key": None}) == ""
    assert api_key_from_settings(None) == ""


def test_client_from_settings_adds_header_only_in_api_key_mode():
    rec = Recorder()
    off = _client(rec, api_key=api_key_from_settings({"auth_mode": "none", "api_key": KEY}))
    _run(off.fetch_models()); _run(off.close())
    assert rec.auth == [None]
    on = _client(rec, api_key=api_key_from_settings({"auth_mode": "api_key", "api_key": KEY}))
    _run(on.fetch_models()); _run(on.close())
    assert rec.auth[-1] == f"Bearer {KEY}"


# --- инъекция заголовка во все запросы ---
def test_bearer_header_in_every_request():
    rec = Recorder()
    c = _client(rec, api_key=KEY)

    async def go():
        assert await c.fetch_models() == ["m1"]
        assert await c.load_model("m1") == {"data": [{"id": "m1"}]}
        assert await c.unload_model("m1") == {"data": [{"id": "m1"}]}
        await c.loaded_models()
        await c.chat_stream([{"role": "user", "content": "q"}], "m1", _settings(),
                            lambda kind, tok: None)
        await c.close()

    _run(go())
    urls = [r.url.path for r in rec.seen]
    assert "/v1/models" in urls and "/api/v1/models/load" in urls
    assert "/api/v1/models/unload" in urls and "/v1/chat/completions" in urls
    assert rec.auth == [f"Bearer {KEY}"] * len(rec.seen)  # ключ во ВСЕХ запросах


def test_no_header_without_key():
    rec = Recorder()
    c = _client(rec)  # режим 'none' / пустой ключ

    async def go():
        assert await c.fetch_models() == ["m1"]
        await c.chat_stream([{"role": "user", "content": "q"}], "m1", _settings(),
                            lambda kind, tok: None)
        await c.close()

    _run(go())
    assert all(a is None for a in rec.auth)
    assert "authorization" not in rec.seen[0].headers


def test_blank_key_is_no_auth():
    for blank in ("", "   ", None):
        rec = Recorder()
        c = _client(rec, api_key=blank)
        _run(c.fetch_models())
        _run(c.close())
        assert rec.auth == [None], blank


def test_set_api_key_applies_to_next_requests():
    rec = Recorder()
    c = _client(rec, api_key=KEY)
    _run(c.fetch_models())
    assert rec.auth == [f"Bearer {KEY}"]
    c.set_api_key("other-key")  # ключ сменили в UI -> следующие запросы новые
    assert c.api_key == "other-key"
    _run(c.fetch_models())
    assert rec.auth[-1] == "Bearer other-key"
    c.set_api_key("")  # режим 'none' -> заголовок исчезает
    _run(c.fetch_models())
    assert rec.auth[-1] is None
    _run(c.close())


def test_explicit_headers_provider():
    rec = Recorder()
    c = _client(rec, headers={"X-Api-Key": KEY, "X-Empty": None})
    _run(c.fetch_models())
    _run(c.close())
    assert rec.seen[0].headers["x-api-key"] == KEY
    assert rec.auth == [None]  # без api_key Bearer-заголовка не добавляем


def test_bearer_wins_over_headers_provider():
    rec = Recorder()
    c = _client(rec, api_key=KEY, headers={"Authorization": "Bearer stale"})
    _run(c.fetch_models())
    _run(c.close())
    assert rec.auth == [f"Bearer {KEY}"]


# --- 401/403 -> локализованная ошибка ---
@pytest.mark.parametrize("call,args,n_req", [
    ("fetch_models", (), 1),
    ("load_model", ("m1",), 1),
    ("unload_model", ("m1",), 1),
    ("loaded_models", (), 5),  # best-effort перебор эндпоинтов, ошибка одна
])
def test_401_maps_to_invalid_api_key(call, args, n_req):
    rec = Recorder(status=401, body='{"error":"unauthorized"}')
    c = _client(rec, api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(getattr(c, call)(*args))
    assert str(ex.value) == "invalid_api_key:401"
    assert len(rec.seen) == n_req  # ретраи на 401 бессмысленны
    _run(c.close())


@pytest.mark.parametrize("call,args", [
    ("fetch_models", ()),
    ("load_model", ("m1",)),
    ("unload_model", ("m1",)),
])
def test_403_maps_to_auth_failed(call, args):
    rec = Recorder(status=403, body='{"error":"forbidden"}')
    c = _client(rec, api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(getattr(c, call)(*args))
    assert str(ex.value) == "auth_failed:403"
    _run(c.close())


@pytest.mark.parametrize("status", [401, 403])
def test_auth_error_is_typed_and_carries_status_code(status):
    """UI должен отличать «ключ отклонён» от «сервер недоступен» без парсинга
    локализованной строки: отдельный тип с HTTP-кодом поверх RuntimeError."""
    rec = Recorder(status=status, body="nope")
    c = _client(rec, api_key=KEY)
    with pytest.raises(AuthError) as ex:
        _run(c.fetch_models())
    assert ex.value.code == status
    assert isinstance(ex.value, RuntimeError)  # старые except RuntimeError живы
    _run(c.close())


def test_chat_stream_401_maps_to_invalid_api_key():
    rec = Recorder(status=401, body='{"error":"unauthorized"}')
    c = _client(rec, api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(c.chat_stream([{"role": "user", "content": "q"}], "m1", _settings(),
                           lambda kind, tok: None))
    assert str(ex.value) == "invalid_api_key:401"
    _run(c.close())


def test_non_auth_status_keeps_http_dump():
    rec = Recorder(status=400, body="bad request payload")
    c = _client(rec, api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(c.load_model("m1"))
    assert "HTTP 400" in str(ex.value) and "bad request payload" in str(ex.value)
    _run(c.close())


# --- ключ не утекает в текст ошибки/лога ---
def test_api_key_scrubbed_from_error_text():
    rec = Recorder(status=400, body=f"rejected key {KEY} nope")
    c = _client(rec, api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(c.load_model("m1"))
    msg = str(ex.value)
    assert KEY not in msg and "***" in msg
    _run(c.close())


def test_api_key_scrubbed_from_unload_model_error():
    # сервер «вернул» текст с ключом (например, прокси эхо Authorization)
    class EchoKey(Recorder):
        def __call__(self, request):
            self.seen.append(request)
            raise httpx.ReadError(f"rejected key {KEY} nope")

    c = _client(EchoKey(), api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(c.unload_model("m1"))
    msg = str(ex.value)
    assert KEY not in msg and "***" in msg
    _run(c.close())


def test_api_key_scrubbed_from_fetch_models_error(no_sleep):
    def tr(key, **kw):  # локализованная фраза должна показывать scrubbed-ошибку
        return f"{key}:{kw.get('e', kw.get('code', ''))}"

    class EchoKey(Recorder):
        def __call__(self, request):
            self.seen.append(request)
            raise httpx.ReadError(f"rejected key {KEY} nope")

    c = _client(EchoKey(), api_key=KEY, tr=tr)
    with pytest.raises(RuntimeError) as ex:
        _run(c.fetch_models())
    msg = str(ex.value)
    assert KEY not in msg and "***" in msg
    _run(c.close())


def test_logger_never_receives_the_key(caplog):
    import logging
    rec = Recorder(status=401, body="nope")
    c = LmClient(f"{BASE}/models", f"{BASE}/chat/completions", base_url=BASE,
                 tr=_tr, log=logging.getLogger("test_auth"),
                 api_key=KEY, transport=httpx.MockTransport(rec))
    with caplog.at_level(logging.DEBUG):
        with pytest.raises(RuntimeError):
            _run(c.fetch_models())
    _run(c.close())
    assert KEY not in caplog.text


# --- базовое поведение не сломано (smoke) ---
def test_chat_stream_reads_sse():
    rec = Recorder(body=_sse("answer"))
    c = _client(rec, api_key=KEY)
    out = _run(c.chat_stream([{"role": "user", "content": "q"}], "m1", _settings(),
                             lambda kind, tok: None))
    assert out[0] == "answer"
    _run(c.close())


def test_cancel_raises_stream_cancelled():
    c = _client(Recorder(body=_sse("a")))

    def on_delta(kind, tok): c.cancel()  # Стоп нажат во время стрима

    with pytest.raises(StreamCancelled):
        _run(c.chat_stream([{"role": "user", "content": "q"}], "m1", _settings(), on_delta))
    _run(c.close())


# --- смежные пути, которые патчили: ретраи и разбор ответов ---
@pytest.fixture()
def no_sleep(monkeypatch):
    """Ретраи не должны жечь реальное время тестов."""
    slept: list = []

    async def fake_sleep(delay, *a, **kw):
        slept.append(delay)

    monkeypatch.setattr("lm_client.asyncio.sleep", fake_sleep)
    return slept


def test_fetch_models_retries_then_reports_no_link(no_sleep):
    rec = Recorder(status=500, body="boom")
    c = _client(rec, api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(c.fetch_models())
    assert str(ex.value).startswith("no_link")
    assert len(rec.seen) == 3  # 3 попытки, как и раньше
    assert no_sleep == [1, 2, 3]
    _run(c.close())


def test_unload_model_falls_back_to_model_payload():
    class FirstPayloadFails(Recorder):
        def __call__(self, request):
            if request.read() == b'{"instance_id": "m1"}':
                return httpx.Response(400, text="use model")
            return httpx.Response(200, json={"ok": True})

    c = _client(FirstPayloadFails(), api_key=KEY)
    assert _run(c.unload_model("m1")) == {"ok": True}
    _run(c.close())


def test_unload_model_raises_last_error():
    rec = Recorder(status=500, body="nope")
    c = _client(rec, api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(c.unload_model("m1"))
    assert "Internal Server Error" in str(ex.value)
    assert len(rec.seen) == 2  # оба payload
    _run(c.close())


def test_loaded_models_reads_loaded_instances():
    rec = Recorder(body=json.dumps({"models": [
        {"key": "loaded-one", "loaded_instances": [{"id": "inst-1"}]},
        {"key": "idle", "loaded_instances": []},
    ]}))
    c = _client(rec, api_key=KEY)
    assert _run(c.loaded_models()) == ["loaded-one", "inst-1"]
    _run(c.close())


def test_loaded_models_empty_without_loaded_state():
    rec = Recorder(body='{"models": []}')  # каталог без loaded-признаков
    c = _client(rec, api_key=KEY)
    assert _run(c.loaded_models()) == []
    _run(c.close())


@pytest.mark.parametrize("raw,expected", [
    ({"models": [{"key": "a", "loaded": True}]}, ["a"]),
    ({"data": [{"id": "b", "state": "loaded"}]}, ["b"]),
    ({"key": "c", "loaded": True}, ["c"]),
    ([{"name": "d", "loaded_instances": [{"id": "d"}]}], ["d"]),
    ({"models": [{"id": "e"}]}, []),          # нечего сказать -> не понято
    ({"models": "not-a-list"}, []),          # мусор -> не понято
])
def test_loaded_from_api_models_shapes(raw, expected):
    out, understood = LmClient._loaded_from_api_models(raw)
    assert out == expected
    assert understood == bool(expected)


def test_chat_stream_reconnects_after_broken_sse(no_sleep):
    class BreakOnce(Recorder):
        broken = False

        def __call__(self, request):
            if not BreakOnce.broken:
                BreakOnce.broken = True
                self.seen.append(request)  # обрыв «до» чтения тела
                raise httpx.ReadError("stream cut")
            return Recorder.__call__(self, request)

    rec = BreakOnce(body=_sse("after-retry"))
    c = _client(rec, api_key=KEY)
    content, _, _ = _run(c.chat_stream([{"role": "user", "content": "q"}], "m1",
                                       _settings(), lambda kind, tok: None))
    assert content == "after-retry"
    assert len(rec.seen) == 2
    assert rec.auth == [f"Bearer {KEY}"] * 2  # ретрай тоже с ключом
    _run(c.close())


def test_chat_stream_gives_up_after_second_error(no_sleep):
    class AlwaysBroken(Recorder):
        def __call__(self, request):
            raise httpx.ReadError("stream cut")

    c = _client(AlwaysBroken(), api_key=KEY)
    with pytest.raises(RuntimeError) as ex:
        _run(c.chat_stream([{"role": "user", "content": "q"}], "m1", _settings(),
                           lambda kind, tok: None))
    assert str(ex.value).startswith("net_err")
    _run(c.close())


def test_chat_stream_no_auth_still_adds_no_header_on_retry(no_sleep):
    class BreakOnce(Recorder):
        broken = False

        def __call__(self, request):
            if not BreakOnce.broken:
                BreakOnce.broken = True
                self.seen.append(request)
                raise httpx.ReadError("stream cut")
            return Recorder.__call__(self, request)

    rec = BreakOnce(body=_sse("x"))
    c = _client(rec)  # режим 'none'
    _run(c.chat_stream([{"role": "user", "content": "q"}], "m1", _settings(),
                       lambda kind, tok: None))
    assert rec.auth == [None, None]
    _run(c.close())
