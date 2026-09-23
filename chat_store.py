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

"""Central state management (Roadmap step 5: State Management).

ChatStore owns the whole UI-agnostic state (current chat, messages,
pending files, flags, filters, connection) and the service handles
(chat_repo / settings_repo / payload builder). Views mutate through
store methods and subscribe to events; the raw ``state`` dict stays
available as ``store.state`` so legacy code keeps working during the
migration (step 6 moves flet into subscribers only).

Pure helpers at module level (fully unit-tested, no I/O):
  list_folders / apply_folder_filter — sidebar folder logic
  cut_tail / edit_user_text          — edit-and-resend message logic
"""
from __future__ import annotations
import time
import uuid
from typing import Callable, Iterable, Optional


# ---------- pure helpers (no I/O, unit-tested) ----------

def list_folders(index: list) -> list:
    """Sorted unique folder names present in the chat index."""
    names = {(c.get("folder") or "").strip() for c in index}
    names.discard("")
    return sorted(names, key=str.lower)


def apply_folder_filter(index: list, folder: str) -> list:
    """Entries of ``folder``; empty folder → whole index (copy)."""
    folder = (folder or "").strip()
    if not folder:
        return list(index)
    return [c for c in index if (c.get("folder") or "").strip() == folder]


def cut_tail(msgs: list, keep: int) -> tuple[list, list]:
    """Keep ``msgs[:keep]``; return it plus assistant texts of the dropped tail.

    Dropped assistant texts become Variants of the next generated reply
    (branch / regenerate / edit-and-resend all share this contract).
    """
    keep_msgs = list(msgs[:keep])
    variants = [o.text for o in msgs[keep:]
                if not getattr(o, "is_user", False) and (o.text or "").strip()][:5]
    return keep_msgs, variants


def edit_user_text(msgs: list, uid: str, new_text: str) -> Optional[tuple[int, list]]:
    """Edit the user message with ``uid`` and cut everything after it.

    Returns ``(index, variants_of_dropped_assistant_replies)`` or ``None``
    when no such user message exists. Mutates ``msgs`` in place.
    """
    idx = next((i for i, m in enumerate(msgs) if getattr(m, "uid", None) == uid), -1)
    if idx < 0 or not getattr(msgs[idx], "is_user", False):
        return None
    msgs[idx].text = new_text
    _, variants = cut_tail(msgs, idx + 1)
    del msgs[idx + 1:]
    return idx, variants


class ChatStore:
    def __init__(self, chat_repo=None, settings_repo=None, payload=None, log=None):
        self.chat_repo = chat_repo
        self.settings_repo = settings_repo
        self.payload = payload
        self._log = log
        self.state: dict = {"cid": None, "msgs": [], "files": [], "sending": False,
                      "stick": True, "conn_ok": False, "conn_custom": None,
                      "chat_filter": "", "rated_only": False, "models_n": 0,
                      "folder_filter": "", "selected": set()}
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
        idx.insert(0, {"id": cid, "title": title, "ts": time.time(),
                       "preview": "", "msg_count": 0})
        self.chat_repo.save_index(idx)
        self.update("chat:new", cid=cid, msgs=[], files=[])
        return cid

    def open_chat(self, cid: str) -> list:
        msgs = self.chat_repo.load_chat(cid)
        # чистка legacy-пустышек: пустые ответы ассистента без вариантов
        before = len(msgs)
        msgs = [m for m in msgs
                if m.is_user or (m.text or "").strip()
                or [v for v in (m.variants or []) if (v or "").strip()]]
        if len(msgs) != before:
            try:
                self.chat_repo.save_chat(cid, msgs)
                if self._log:
                    self._log.info("scrubbed %d empty assistant message(s) in %s",
                                   before - len(msgs), cid)
            except Exception as ex:
                if self._log: self._log.warning("scrub save failed: %s", ex)
        self.update("chat:open", cid=cid, msgs=msgs, files=[],
                    chat_filter="", rated_only=False, selected=set())
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
        msgs = self.state.get("msgs")
        if isinstance(msgs, list):
            msgs.append(msg)
        self._emit("msgs:appended")

    def set_msgs(self, msgs: list):
        self.state["msgs"] = msgs
        self._emit("msgs:set")

    # -- index metadata (moved out of app.py) --
    def get_entry(self, cid: str) -> dict | None:
        for c in self.chat_repo.load_index():
            if c.get("id") == cid:
                return c
        return None

    def rename_chat(self, cid: str, title: str) -> bool:
        idx = self.chat_repo.load_index()
        changed = False
        for c in idx:
            if c.get("id") == cid:
                c["title"] = title
                changed = True
        if changed:
            self.chat_repo.save_index(idx)
            self._emit("chat:renamed")
        return changed

    def toggle_pin(self, cid: str) -> bool:
        """Flip pinned flag; return new value (False when cid missing)."""
        idx = self.chat_repo.load_index()
        new_val: bool | None = None
        for c in idx:
            if c.get("id") == cid:
                c["pinned"] = not c.get("pinned", False)
                new_val = bool(c["pinned"])
        if new_val is not None:
            self.chat_repo.save_index(idx)
            self._emit("chat:pin")
        return bool(new_val)

    def auto_rename(self, cid: str, title: str, only_if_in: Iterable[str]) -> bool:
        """Rename only while the current title is in ``only_if_in``.

        Never overwrites a manual rename (the caller passes the default /
        auto-generated titles it is allowed to replace).
        """
        allowed = set(only_if_in)
        idx = self.chat_repo.load_index()
        for c in idx:
            if c.get("id") == cid and c.get("title") in allowed:
                c["title"] = title
                self.chat_repo.save_index(idx)
                self._emit("chat:renamed")
                return True
        return False

    def set_folder(self, cid: str, folder: str | None) -> bool:
        """Assign a folder to the chat; empty/None removes the folder."""
        idx = self.chat_repo.load_index()
        changed = False
        for c in idx:
            if c.get("id") == cid:
                f = (folder or "").strip()
                if f:
                    c["folder"] = f
                else:
                    c.pop("folder", None)
                changed = True
        if changed:
            self.chat_repo.save_index(idx)
            self._emit("chat:folder")
        return changed

    # -- search --
    def search_all(self, query: str, limit: int = 50) -> list:
        """Global full-text search over all chats (titles + message bodies)."""
        return self.chat_repo.search_all(query, limit)

    # -- selection (export) --
    def toggle_select(self, uid: str) -> bool:
        """Toggle message uid in the selection set; return its new state."""
        sel = self.state.get("selected")
        if not isinstance(sel, set):
            sel = self.state["selected"] = set()
        on = uid not in sel
        if on:
            sel.add(uid)
        else:
            sel.discard(uid)
        self._emit("selection")
        return on

    def clear_selection(self):
        if self.state.get("selected"):
            self.state["selected"] = set()
            self._emit("selection")

    # -- filters / connection --
    def set_filter(self, q: str): self.set("chat_filter", q or "", "filter")
    def set_rated_only(self, v: bool): self.set("rated_only", bool(v), "filter")
    def set_folder_filter(self, folder: str): self.set("folder_filter", folder or "", "filter")
    def set_conn(self, ok: bool, label=None):
        self.update("conn", conn_ok=ok, conn_custom=label)
