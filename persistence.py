"""PersistenceService: chat persistence + per-assistant-message feedback/rating."""
from __future__ import annotations
import json
from pathlib import Path

FEEDBACK_TYPES = ("helpful", "inaccurate", "harmful", "other", None)

class PersistenceService:
    def __init__(self, chats_dir: str | Path = "data/chats"):
        self.chats_dir = Path(chats_dir)
        self.chats_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, cid): return self.chats_dir / f"{cid}.json"

    def load(self, cid) -> list:
        p = self._path(cid)
        if p.is_file():
            try: return json.loads(p.read_text("utf-8"))
            except Exception: pass
        return []

    def save(self, cid, messages: list):
        self._path(cid).write_text(json.dumps(messages, ensure_ascii=False, indent=2), "utf-8")

    def set_feedback(self, cid, index: int, *, rating: int | None = None,
                     feedback_type: str | None = None, comment: str = "") -> dict:
        """Attach rating/feedback to an assistant message. Validates input."""
        if feedback_type not in FEEDBACK_TYPES:
            raise ValueError(f"bad feedback_type: {feedback_type}")
        if rating is not None and not (1 <= int(rating) <= 5):
            raise ValueError("rating must be 1..5")
        msgs = self.load(cid)
        if not (0 <= index < len(msgs)):
            raise IndexError("message index out of range")
        m = msgs[index]
        if m.get("is_user"):
            raise ValueError("feedback applies to assistant messages only")
        m["rating"] = rating
        m["feedback_type"] = feedback_type
        if comment:
            m["feedback_comment"] = comment
        self.save(cid, msgs)
        return m

    def collect_labeled(self, cid) -> list:
        """Messages usable for future fine-tuning data collection."""
        return [m for m in self.load(cid) if not m.get("is_user") and (m.get("rating") is not None or m.get("feedback_type"))]
