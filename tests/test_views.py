from __future__ import annotations
from views import ViewBinder, format_feedback, token_stats, visible_messages
from models import ChatMessage


class _FakeStore:
    def __init__(self):
        self.state = {}
        self._subs = []

    def subscribe(self, fn):
        self._subs.append(fn)
        def _unsub():
            try: self._subs.remove(fn)
            except ValueError: pass
        return _unsub

    def emit(self, event):
        for fn in list(self._subs):
            try:
                fn(event, self.state)
            except Exception:
                pass  # как в ChatStore._emit: сбой одного подписчика не роняет emit


def _msgs():
    return [
        ChatMessage(text="первый вопрос", is_user=True),
        ChatMessage(text="ответ полезный", is_user=False, rating=5, feedback_type="helpful"),
        ChatMessage(text="второй вопрос", is_user=True),
        ChatMessage(text="обычный ответ", is_user=False),
    ]


def test_visible_messages_query():
    out = visible_messages(_msgs(), query="вопрос")
    assert [m.text for m in out] == ["первый вопрос", "второй вопрос"]


def test_visible_messages_case_insensitive():
    out = visible_messages(_msgs(), query="ОТВЕТ")
    assert len(out) == 2


def test_visible_messages_rated_only():
    out = visible_messages(_msgs(), rated_only=True)
    assert [m.text for m in out] == ["ответ полезный"]


def test_visible_messages_combined():
    out = visible_messages(_msgs(), query="второй", rated_only=True)
    assert out == []


def test_format_feedback():
    m = ChatMessage(text="x", rating=5, feedback_type="helpful")
    assert format_feedback(m) == "★5 · helpful"
    assert format_feedback(ChatMessage(text="x")) == ""


def test_token_stats_basic():
    msgs = [ChatMessage(text="a" * 400, is_user=True)]
    label, frac, over = token_stats(msgs, context_length=800)
    assert label == "~100 / 800"
    assert abs(frac - 0.125) < 1e-9
    assert over is False


def test_token_stats_over_limit():
    msgs = [ChatMessage(text="a" * 4000, is_user=True)]
    _, frac, over = token_stats(msgs, context_length=100)
    assert frac == 1.0
    assert over is True


def test_token_stats_bad_context_length():
    label, frac, _ = token_stats([], context_length="oops")
    assert "8192" in label
    assert frac == 0.0


def test_view_binder_token_and_filter_events():
    store = _FakeStore()
    tokens, filters = [], []
    b = ViewBinder(store, on_tokens=lambda: tokens.append(1),
                   on_filter=lambda: filters.append(1)).bind()
    store.emit("msgs:appended")   # и токены, и фильтр
    store.emit("set:theme")       # только токены (set:* )
    store.emit("conn")            # только токены
    store.emit("unrelated")       # ничего
    assert len(tokens) == 3
    assert len(filters) == 1
    b.unbind()
    store.emit("msgs:appended")
    assert len(tokens) == 3  # отписались


def test_view_binder_swallows_subscriber_errors():
    store = _FakeStore()
    def boom(_e, _s):
        raise RuntimeError("view broken")
    store.subscribe(boom)
    ViewBinder(store, on_tokens=lambda: None).bind()
    store.emit("msgs:appended")  # не должно выбросить
