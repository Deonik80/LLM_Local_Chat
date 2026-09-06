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

"""Data models on Pydantic v2 (Roadmap step 2: Modeling).

Strict validation for new data, tolerant loading of legacy JSON:
unknown/invalid ``rating`` / ``feedback_type`` values are coerced
to ``None`` instead of raising, so old chat files always load.

Images: base64 lives only in memory. On disk (chat JSON) we store
path/mime only — ``to_dict(include_b64=False)``; on load,
``rehydrate_attachment()`` re-encodes from disk when the file is
still available. This keeps history files small.
"""
from __future__ import annotations
import base64
import mimetypes
import time
from pathlib import Path
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

FeedbackType = Literal["helpful", "inaccurate", "harmful", "other"]
_FEEDBACK_OK = ("helpful", "inaccurate", "harmful", "other")
MAX_IMG_BYTES = 10 * 1024 * 1024


class Attachment(BaseModel):
    path: str
    mime: Optional[str] = None
    b64: Optional[str] = None

    def to_dict(self, include_b64: bool = True) -> dict:
        d = self.model_dump()
        if not include_b64:
            d.pop("b64", None)
        return d

    @staticmethod
    def from_dict(d: dict) -> "Attachment":
        return Attachment(path=d.get("path", ""), mime=d.get("mime"), b64=d.get("b64"))


def rehydrate_attachment(a: "Attachment", max_bytes: int = MAX_IMG_BYTES) -> "Attachment":
    """Re-encode image bytes from disk when chat JSON has path but no b64."""
    if not isinstance(a, Attachment) or a.b64:
        return a
    if not (a.mime or "").startswith("image/"):
        return a
    try:
        p = Path(a.path or "")
        if not p.is_file() or p.stat().st_size > max_bytes:
            return a
        mime, _ = mimetypes.guess_type(p.name)
        if not mime or not mime.startswith("image/"):
            return a
        return Attachment(path=a.path, mime=mime,
                          b64=base64.b64encode(p.read_bytes()).decode())
    except (OSError, ValueError):
        return a


class ChatMessage(BaseModel):
    model_config = {"validate_assignment": True}

    text: str = ""
    is_user: bool = False
    ts: float = Field(default_factory=time.time)
    attachments: list[Any] = Field(default_factory=list)
    variants: list[str] = Field(default_factory=list)
    rating: Optional[int] = Field(default=None, ge=1, le=5)
    feedback_type: Optional[FeedbackType] = None

    @field_validator("ts", mode="before")
    @classmethod
    def _ts_default(cls, v):
        return v if isinstance(v, (int, float)) and v else time.time()

    def to_dict(self, include_b64: bool = True) -> dict:
        d = self.model_dump()
        atts = []
        for a in self.attachments:
            if isinstance(a, Attachment):
                atts.append(a.to_dict(include_b64=include_b64))
            elif isinstance(a, BaseModel):
                atts.append(a.model_dump())
            else:
                atts.append(a)
        d["attachments"] = atts
        return d

    @staticmethod
    def from_dict(d: dict) -> "ChatMessage":
        atts = [
            Attachment.from_dict(a) if isinstance(a, dict) and "path" in a else a
            for a in (d.get("attachments") or [])
        ]
        rating = d.get("rating")
        try:
            rating = int(rating) if rating is not None else None
        except (TypeError, ValueError):
            rating = None
        if rating is not None and not 1 <= rating <= 5:
            rating = None
        fb = d.get("feedback_type")
        if fb not in _FEEDBACK_OK:
            fb = None
        return ChatMessage(
            text=d.get("text", ""), is_user=bool(d.get("is_user", False)),
            ts=d.get("ts") or time.time(), attachments=atts,
            variants=list(d.get("variants") or []),
            rating=rating, feedback_type=fb)
