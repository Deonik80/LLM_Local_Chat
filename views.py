"""Presentation layer (Roadmap step 6: Presentation).

Rule: flet widgets are mutated only from here or from store subscribers
wired here. Everything else in this module is pure (testable without flet):
  - visible_messages() — chat list filtering (search query + rated-only)
  - format_feedback()  — "★5 · helpful" label text
  - token_stats()      — (label_text, bar_fraction, is_over) for the context bar

ViewBinder binds a ChatStore to flet refresh callbacks, so UI updates
flow as: store mutation -> event -> subscriber -> flet refresh.
"""
from __future__ import annotations
from typing import Callable


def visible_messages(msgs: list, query: str = "", rated_only: bool = False) -> list:
    q = (query or "").lower()
    out = []
    for m in msgs:
        text = getattr(m, "text", "") or ""
        if rated_only and not (getattr(m, "rating", None) or getattr(m, "feedback_type", None)):
            continue
        if q and q not in text.lower():
            continue
        out.append(m)
    return out


def format_feedback(m) -> str:
    bits = []
    if getattr(m, "rating", None): bits.append(f"★{m.rating}")
    if getattr(m, "feedback_type", None): bits.append(str(m.feedback_type))
    return " · ".join(bits)


def token_stats(msgs: list, context_length: int,
                estimate: Callable[[str], int] | None = None) -> tuple[str, float, bool]:
    est = estimate or (lambda s: max(1, len(s or "") // 4))
    try: cl = int(context_length or 8192)
    except (TypeError, ValueError): cl = 8192
    t = sum(est(getattr(m, "text", "") or "") for m in msgs)
    frac = min(1.0, t / cl) if cl else 0.0
    return (f"~{t} / {cl}", frac, frac >= 1.0)


class ViewBinder:
    """Subscribe flet refresh callbacks to store events (no flet import here)."""

    TOKEN_EVENTS = ("msgs:appended", "msgs:set", "chat:open", "chat:new",
                    "chat:saved", "update", "conn")
    FILTER_EVENTS = ("filter", "chat:open", "chat:new", "msgs:set", "msgs:appended")

    def __init__(self, store, on_tokens: Callable[[], None] | None = None,
                 on_filter: Callable[[], None] | None = None):
        self._store = store
        self._on_tokens = on_tokens
        self._on_filter = on_filter
        self._unsub = None

    def bind(self):
        def _handler(event: str, _state: dict):
            try:
                if self._on_tokens and (event in self.TOKEN_EVENTS or event.startswith("set:")):
                    self._on_tokens()
                if self._on_filter and event in self.FILTER_EVENTS:
                    self._on_filter()
            except Exception:
                pass  # view refresh must never break state mutation
        self._unsub = self._store.subscribe(_handler)
        return self

    def unbind(self):
        if self._unsub: self._unsub(); self._unsub = None
