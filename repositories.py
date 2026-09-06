"""Persistence repositories (Roadmap step 3: Service Layer).

ChatRepository     — chat index + per-chat message files (ChatMessage models).
SettingsRepository — settings.json merged over defaults.

Extracted verbatim from app.py storage helpers; behavior unchanged.
"""
from __future__ import annotations
import json
from pathlib import Path

from models import ChatMessage


class ChatRepository:
    def __init__(self, chats_dir: str | Path, index_file: str | Path):
        self.chats_dir = Path(chats_dir)
        self.index_file = Path(index_file)
        self.chats_dir.mkdir(parents=True, exist_ok=True)

    # -- index --
    def load_index(self) -> list:
        if self.index_file.is_file():
            try: return json.loads(self.index_file.read_text("utf-8"))
            except Exception: pass
        return []

    def save_index(self, idx: list):
        self.index_file.write_text(json.dumps(idx, ensure_ascii=False, indent=2), "utf-8")

    # -- chats --
    def chat_path(self, cid) -> Path: return self.chats_dir / f"{cid}.json"

    def load_chat(self, cid) -> list[ChatMessage]:
        try:
            return [ChatMessage.from_dict(m)
                    for m in json.loads(self.chat_path(cid).read_text("utf-8"))]
        except Exception: return []

    def save_chat(self, cid, msgs: list):
        self.chat_path(cid).write_text(json.dumps(
            [m.to_dict() if isinstance(m, ChatMessage) else m for m in msgs],
            ensure_ascii=False, indent=2), "utf-8")

    def delete_chat(self, cid):
        try: self.chat_path(cid).unlink(missing_ok=True)
        except Exception: pass


class SettingsRepository:
    def __init__(self, path: str | Path, defaults: dict):
        self.path = Path(path)
        self.defaults = dict(defaults)

    def load(self) -> dict:
        s = dict(self.defaults)
        if self.path.is_file():
            try: s.update(json.loads(self.path.read_text("utf-8")))
            except Exception: pass
        return s

    def save(self, s: dict):
        self.path.write_text(json.dumps(s, ensure_ascii=False, indent=2), "utf-8")


class ProfilesRepository:
    """Профили связок: model + preset + system_prompt + generation params."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict:
        try:
            if self.path.is_file():
                raw = json.loads(self.path.read_text("utf-8"))
                if isinstance(raw, dict): return raw
        except Exception: pass
        return {}

    def save(self, profiles: dict):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(profiles, ensure_ascii=False, indent=2), "utf-8")
