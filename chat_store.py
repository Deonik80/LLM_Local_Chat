"""Central state management (Roadmap step 5: State Management).

ChatStore owns the whole UI-agnostic state (current chat, messages,
pending files, flags, filters, connection) and the service handles
(chat_repo / settings_repo / payload builder). Views mutate through
store methods and subscribe to events; the raw ``state`` dict stays
available as ``store.state`` so legacy code keeps working during the
migration (step 6 moves flet into subscribers only).
"""
from __future__ import annotations
import time
import uuid
from typing import Callable


class ChatStore:
    def __init__(self, chat_repo=None, settings_repo=None, payload=None, log=None):
        self.chat_repo = chat_repo
        self.settings_repo = settings_repo
        self.payload = payload
        self._log = log
        self.state = {"cid": None, "msgs": [], "files": [], "sending": False,
                      "stick": True, "conn_ok": False, "conn_custom": None,
                      "chat_filter": "", "rated_only": False, "models_n": 0}
        self._subs: list[Callable] = []

    # -- pub/sub --
    def subscribe(self, fn: Callable) -> Callable:
        self._subs.append(fn)
        def _unsub():
            try: self._subs.remove(fn)
            except ValueError: pass
        return _unsub

    def _emit(self, event: str):
        for fn in list(self._subs):
            try: fn(event, self.state)
            except Exception:
                if self._log: self._log.exception("store subscriber failed: %s", event)

    # -- generic mutation (emits) --
    def set(self, key: str, value, event: str | None = None):
        self.state[key] = value
        self._emit(event or f"set:{key}")

    def update(self, event: str = "update", **kw):
        self.state.update(kw)
        self._emit(event)

    # -- chat lifecycle (data only, no UI) --
    def new_chat(self, title: str) -> str:
        cid = uuid.uuid4().hex[:8]
        idx = self.chat_repo.load_index()
        idx.insert(0, {"id": cid, "title": title, "ts": time.time()})
        self.chat_repo.save_index(idx)
        self.update("chat:new", cid=cid, msgs=[], files=[])
        return cid

    def open_chat(self, cid: str) -> list:
        msgs = self.chat_repo.load_chat(cid)
        self.update("chat:open", cid=cid, msgs=msgs, files=[],
                    chat_filter="", rated_only=False)
        return msgs

    def save_current(self):
        if self.state["cid"] is not None:
            self.chat_repo.save_chat(self.state["cid"], self.state["msgs"])
            self._emit("chat:saved")

    def delete_chat(self, cid: str):
        self.chat_repo.delete_chat(cid)
        self.chat_repo.save_index([c for c in self.chat_repo.load_index() if c["id"] != cid])
        self._emit("chat:deleted")

    def append_msg(self, msg):
        self.state["msgs"].append(msg)
        self._emit("msgs:appended")

    def set_msgs(self, msgs: list):
        self.state["msgs"] = msgs
        self._emit("msgs:set")

    # -- filters / connection --
    def set_filter(self, q: str): self.set("chat_filter", q or "", "filter")
    def set_rated_only(self, v: bool): self.set("rated_only", bool(v), "filter")
    def set_conn(self, ok: bool, label=None):
        self.update("conn", conn_ok=ok, conn_custom=label)
