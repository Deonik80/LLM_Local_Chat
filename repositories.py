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

"""Persistence repositories (Roadmap step 3: Service Layer).

ChatRepository     — chat index + per-chat message files (ChatMessage models).
SettingsRepository — settings.json merged over defaults.

Extracted verbatim from app.py storage helpers; behavior unchanged
except persistence safety: all writes go through fsutil (atomic
tmp+replace), corrupt JSON is preserved as ``*.corrupt`` instead of
being silently overwritten on the next save.
"""
from __future__ import annotations
from pathlib import Path

from fsutil import read_json, write_json
from models import ChatMessage, rehydrate_attachment


class ChatRepository:
    def __init__(self, chats_dir: str | Path, index_file: str | Path):
        self.chats_dir = Path(chats_dir)
        self.index_file = Path(index_file)
        self.chats_dir.mkdir(parents=True, exist_ok=True)

    # -- index --
    def load_index(self) -> list:
        idx = read_json(self.index_file, [])
        return idx if isinstance(idx, list) else []

    def save_index(self, idx: list):
        write_json(self.index_file, idx)

    # -- chats --
    def chat_path(self, cid) -> Path: return self.chats_dir / f"{cid}.json"

    def load_chat(self, cid) -> list[ChatMessage]:
        raw = read_json(self.chat_path(cid), None)
        if not isinstance(raw, list):
            return []
        try:
            msgs = [ChatMessage.from_dict(m) for m in raw if isinstance(m, dict)]
        except Exception:
            return []
        # вернуть картинки из файлов (в JSON лежит только путь)
        for m in msgs:
            try:
                m.attachments = [rehydrate_attachment(a) for a in (m.attachments or [])]
            except Exception: pass
        return msgs

    def save_chat(self, cid, msgs: list):
        # b64 на диск не пишем — только путь/имя (файл истории остаётся маленьким)
        out = []
        for m in msgs:
            if isinstance(m, ChatMessage):
                try: out.append(m.to_dict(include_b64=False))
                except TypeError: out.append(m.to_dict())
            else: out.append(m)
        write_json(self.chat_path(cid), out)
        self._touch_index(cid, msgs)

    def _touch_index(self, cid, msgs: list):
        """Держать превью/счётчик чата в index.json (сайдбар без чтения файлов)."""
        try:
            idx = self.load_index()
            changed = False
            for c in idx:
                if c.get("id") != cid:
                    continue
                last = next((m for m in reversed(msgs)
                             if ((getattr(m, "text", "") or "").strip())), None)
                text = (getattr(last, "text", "") or "").strip() if last else ""
                preview, count = text[:42], len(msgs)
                if c.get("preview") != preview or c.get("msg_count") != count:
                    c["preview"], c["msg_count"] = preview, count
                    changed = True
            if changed:
                self.save_index(idx)
        except Exception:
            pass  # превью — best effort, не роняем сохранение чата

    def delete_chat(self, cid):
        try: self.chat_path(cid).unlink(missing_ok=True)
        except Exception: pass

    # -- global search --
    def search_all(self, query: str, limit: int = 50) -> list:
        """Full-text search across titles + message bodies of every chat.

        Returns ``[{cid, title, uid, snippet}]``, newest chats first,
        at most ``limit`` hits. ``uid == ""`` marks a title-only hit.
        """
        q = (query or "").strip().lower()
        if not q or limit <= 0:
            return []
        hits: list[dict] = []
        index = [c for c in self.load_index() if isinstance(c, dict) and c.get("id")]
        # 1) заголовки — дёшево, сразу из index.json
        for c in index:
            title = c.get("title") or ""
            if q in title.lower():
                hits.append({"cid": c["id"], "title": title, "uid": "", "snippet": title})
        # 2) тела сообщений — свежие чаты первыми
        paths = sorted(self.chats_dir.glob("*.json"),
                       key=lambda p: p.stat().st_mtime if p.is_file() else 0,
                       reverse=True)
        titles = {c["id"]: c.get("title") or c["id"] for c in index}
        for p in paths:
            if len(hits) >= limit:
                break
            cid = p.stem
            for m in self.load_chat(cid):
                text = m.text or ""
                low = text.lower()
                if q in low:
                    i = low.index(q)
                    s, e = max(0, i - 24), min(len(text), i + len(q) + 56)
                    snip = (("…" if s else "") + text[s:e].replace("\n", " ")
                            + ("…" if e < len(text) else ""))
                    hits.append({"cid": cid, "title": titles.get(cid, cid),
                                 "uid": m.uid, "snippet": snip})
                    if len(hits) >= limit:
                        break
        return hits[:limit]


class SettingsRepository:
    def __init__(self, path: str | Path, defaults: dict):
        self.path = Path(path)
        self.defaults = dict(defaults)

    def load(self) -> dict:
        s = dict(self.defaults)
        raw = read_json(self.path, None)
        if isinstance(raw, dict):
            s.update(raw)
        return s

    def save(self, s: dict):
        write_json(self.path, s)


class ProfilesRepository:
    """Профили связок: model + preset + system_prompt + generation params."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict:
        raw = read_json(self.path, {})
        return raw if isinstance(raw, dict) else {}

    def save(self, profiles: dict):
        write_json(self.path, profiles)
