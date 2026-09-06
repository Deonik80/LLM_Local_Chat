"""LocalizationManager (Roadmap step 1: Foundation).

Central owner of the i18n table + current language. Replaces the
scattered ``LANGS`` / ``CUR`` / ``tr()`` globals in app.py.

Usage:
    from localization import LocalizationManager
    i18n = LocalizationManager(TABLE, default="ru")
    i18n.tr("send")              # uses current lang
    i18n.set_lang("en")
    i18n.display("Обычный")      # preset display-name mapping
"""
from __future__ import annotations
from typing import Callable


class LocalizationManager:
    def __init__(self, table: dict, default: str = "ru",
                 preset_i18n: dict | None = None,
                 on_change: Callable[[str], None] | None = None):
        if default not in table:
            raise ValueError(f"default lang {default!r} missing from table")
        self._table = table
        self._lang = default
        self._default = default
        self._preset_i18n = preset_i18n or {}
        self._on_change = on_change

    @property
    def lang(self) -> str:
        return self._lang

    @property
    def langs(self) -> list:
        return list(self._table.keys())

    def set_lang(self, lang: str):
        if lang not in self._table:
            raise ValueError(f"unsupported lang: {lang}")
        self._lang = lang
        if self._on_change:
            self._on_change(lang)

    def toggle(self, a: str = "ru", b: str = "en") -> str:
        self.set_lang(b if self._lang == a else a)
        return self._lang

    def tr(self, key: str, **kw) -> str:
        s = self._table.get(self._lang, {}).get(key,
            self._table.get(self._default, {}).get(key, key))
        try:
            return s.format(**kw) if kw else s
        except (KeyError, IndexError, ValueError):
            return s

    def display(self, preset_key: str) -> str:
        """Map a (ru) preset name to its i18n key and translate."""
        k = self._preset_i18n.get(preset_key)
        return self.tr(k) if k else preset_key
