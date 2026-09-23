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

"""LLM API payload mapping (Roadmap step 4: Core Logic).

APIPayloadBuilder isolates everything the app knows about the LLM API:
system-prompt assembly (env block + template/base), history trimming by
token budget, and ChatMessage -> OpenAI-style message mapping
(text / image_url / extracted file text).

File-text extraction is injected (lives in app.py) to avoid a cycle.
"""
from __future__ import annotations
import re
from pathlib import Path
from typing import Callable

from models import Attachment, ChatMessage

# не-ASCII (кириллица, иероглифы и др.) токенизируется плотнее, чем латиница
_NON_ASCII_RE = re.compile(r"[^\x00-\x7f]+")


def estimate_tokens(s: str) -> int:
    """Грубая, но смещённая под реальность оценка числа токенов.

    ASCII ~4 символа/токен; не-ASCII ~2 символа/токена (кириллица в
    мультиязычных токенизаторах занимает больше токенов на символ, чем
    считает len//4 — прогресс-бар контекста «врал» в безопасную сторону
    только для английского). Занижение дороже завышения, поэтому
    не-ASCII считаем консервативно.
    """
    s = s or ""
    if not s:
        return 1
    non_ascii = sum(len(m.group(0)) for m in _NON_ASCII_RE.finditer(s))
    ascii_n = len(s) - non_ascii
    return max(1, (ascii_n + 3) // 4 + (non_ascii + 1) // 2)


# Ключи окружения, значения которых нельзя отдавать модели (утечка секретов
# в system prompt -> экспорт/логи/история).
_SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "PASSWD", "PWD",
                   "CREDENTIAL", "AUTH", "PRIVATE", "SALT", "SIGNATURE")


def mask_env_value(key: str, value: str) -> str:
    """Mask values of secret-like env keys; pass everything else through."""
    up = (key or "").upper()
    if any(m in up for m in _SECRET_MARKERS):
        return "***"
    return value


class APIPayloadBuilder:
    def __init__(self, max_ctx_messages: int = 20,
                 estimate: Callable[[str], int] | None = None,
                 extract_text: Callable[[str], str] | None = None):
        self.max_ctx_messages = max_ctx_messages
        self.estimate = estimate or estimate_tokens
        self.extract_text = extract_text or (lambda p: "")

    # -- system prompt --
    @staticmethod
    def format_env_block(settings: dict) -> str:
        """System Context block from requirements.txt / .env stored in settings.

        Secret-like env values (API_KEY, *TOKEN*, PASSWORD, ...) are masked
        so they never reach the model prompt, logs or exports.
        """
        req = (settings.get("env_requirements") or "").strip()
        env_vars = settings.get("env_vars") or {}
        if not req and not env_vars: return ""
        parts = ["[System Context: Environment]"]
        if req: parts.append(f"requirements:\n{req[:8000]}")
        if env_vars:
            parts.append("environment variables:\n" + "\n".join(
                f"- {k}={mask_env_value(k, v)}" for k, v in env_vars.items()))
            parts.append("Model must respect these library versions and env vars.")
        return "\n".join(parts)

    def effective_system(self, settings: dict, system: str) -> str:
        env_block = self.format_env_block(settings)
        blocks = [b for b in [env_block, (system or "").strip()] if b]
        return "\n\n".join(blocks)

    # -- messages --
    def trim(self, messages: list[ChatMessage], context_tokens: int = 0) -> list[ChatMessage]:
        hist = messages[-self.max_ctx_messages:]
        if context_tokens and context_tokens > 0:
            # выкидываем самые старые, пока сумма не влезет (минимум 2 сообщения);
            # считаем накопительно, а не пересчитываем sum() на каждой итерации
            total = sum(self.estimate(m.text or "") for m in hist)
            while len(hist) > 2 and total > context_tokens:
                total -= self.estimate(hist[0].text or "")
                hist = hist[1:]
        return hist

    def build(self, messages: list[ChatMessage], system: str, context_tokens: int = 0,
              strip_images: bool = False) -> list[dict]:
        api: list[dict] = []  # content: str у system/assistant, list[dict] у user с картинками
        if (system or "").strip(): api.append({"role": "system", "content": system.strip()})
        for m in self.trim(messages, context_tokens):
            if m.is_user:
                parts: list[dict] = [{"type": "text", "text": m.text}]  # text|image_url элементы
                for a in m.attachments:
                    if isinstance(a, Attachment) and a.mime and a.mime.startswith("image/"):
                        if strip_images:
                            parts.append({"type": "text", "text":
                                f"[image omitted (model has no vision support): {Path(a.path).name}]"})
                        elif a.b64:
                            parts.append({"type": "image_url",
                                          "image_url": {"url": f"data:{a.mime};base64,{a.b64}"}})
                        else:
                            parts.append({"type": "text", "text":
                                f"[image unavailable (file not found): {Path(a.path).name}]"})
                    elif isinstance(a, Attachment):
                        parts.append({"type": "text",
                                      "text": f"--- {Path(a.path).name} ---\n{self.extract_text(a.path)}"})
                api.append({"role": "user", "content": parts})
            else: api.append({"role": "assistant", "content": m.text})
        return api

    def build_for_settings(self, messages: list[ChatMessage], settings: dict,
                           strip_images: bool = False) -> list[dict]:
        """One-call mapping: settings -> effective system + token budget -> payload."""
        try: budget = int(settings.get("context_length", 0) or 0)
        except (TypeError, ValueError): budget = 0
        return self.build(messages,
                          self.effective_system(settings, settings.get("system_prompt", "")),
                          budget, strip_images=strip_images)


def payload_has_images(api: list[dict]) -> bool:
    for m in api:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
            return True
    return False


def payload_stats(api: list[dict]) -> dict:
    n_img = sum(1 for m in api if isinstance(m.get("content"), list)
                for p in m["content"] if isinstance(p, dict) and p.get("type") == "image_url")
    return {"messages": len(api), "images": n_img}
