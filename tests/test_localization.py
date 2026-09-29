from __future__ import annotations
import json
from pathlib import Path

import pytest
from localization import LocalizationManager

TABLE = {
    "ru": {"hello": "Привет", "greet": "Привет, {name}!", "missing_en": "только рус"},
    "en": {"hello": "Hello", "greet": "Hi, {name}!"},
}
PRESET = {"Обычный": "p_ordinary"}
LOCALES = Path(__file__).resolve().parents[1] / "locales"
MCP_KEYS = ("sec_mcp", "mcp_use", "mcp_add_id", "mcp_add_hint", "mcp_add",
            "mcp_remove", "mcp_no_servers", "mcp_active", "mcp_active_hint",
            "mcp_tool", "mcp_tool_call", "mcp_tool_result", "mcp_tool_failed",
            "mcp_error", "mcp_err_version", "mcp_err_remote",
            "mcp_err_setting", "mcp_err_image", "mcp_no_input", "mcp_no_text")


def _mgr():
    return LocalizationManager(TABLE, default="ru", preset_i18n=PRESET)


def test_default_lang():
    m = _mgr()
    assert m.lang == "ru"
    assert m.tr("hello") == "Привет"


def test_set_lang_and_toggle():
    m = _mgr()
    m.set_lang("en")
    assert m.tr("hello") == "Hello"
    assert m.toggle("ru", "en") == "ru"


def test_bad_lang_raises():
    m = _mgr()
    with pytest.raises(ValueError):
        m.set_lang("de")
    with pytest.raises(ValueError):
        LocalizationManager({"en": {}}, default="ru")


def test_fallback_to_default_lang():
    m = _mgr()
    m.set_lang("en")
    assert m.tr("missing_en") == "только рус"


def test_unknown_key_returns_key():
    assert _mgr().tr("no_such_key") == "no_such_key"


def test_format_kwargs():
    assert _mgr().tr("greet", name="Мир") == "Привет, Мир!"


def test_format_error_returns_raw():
    assert _mgr().tr("greet") == "Привет, {name}!"


def test_display_preset():
    m = _mgr()
    # известный пресет -> i18n-ключ; если ключа нет в таблице — возвращается сам ключ
    assert m.display("Обычный") == "p_ordinary"
    # неизвестный пресет — имя как есть
    assert m.display("Свой") == "Свой"


# --- реальные таблицы locales/*.json ---
def _locale(name: str) -> dict:
    return json.loads((LOCALES / f"{name}.json").read_text(encoding="utf-8"))


def test_locales_have_identical_keys():
    """Перевод не должен «отваливаться» на одной из локалей: tr() отдаёт
    сырой ключ, если его нет в таблице текущего языка."""
    ru, en = _locale("ru"), _locale("en")
    assert set(ru) == set(en), {"ru-only": sorted(set(ru) - set(en)),
                                "en-only": sorted(set(en) - set(ru))}


def test_mcp_keys_present_in_both_locales():
    for name in ("ru", "en"):
        table = _locale(name)
        missing = [k for k in MCP_KEYS if not table.get(k)]
        assert not missing, f"{name}.json без ключей: {missing}"


def test_mcp_status_keys_keep_placeholders():
    """Подстановки в статусах MCP используются как есть — плейсхолдеры обязаны быть."""
    assert "{t}" in _locale("ru")["mcp_tool"]
    assert "{t}" in _locale("en")["mcp_tool"]
    assert "{r}" in _locale("ru")["mcp_tool_failed"]
    assert "{t}" in _locale("ru")["mcp_error"]
    assert "{n}" in _locale("ru")["mcp_active"] and "{n}" in _locale("en")["mcp_active"]
    assert "{n}" in _locale("ru")["mcp_no_text"] and "{n}" in _locale("en")["mcp_no_text"]
    # строки вызовов инструментов: имя + аргументы/результат, одинаково в двух языках
    for key in ("mcp_tool_call", "mcp_tool_result", "mcp_tool_failed"):
        for lang in ("en", "ru"):
            text = _locale(lang)[key]
            assert "{t}" in text, (key, lang)
            assert ("{a}" in text) or ("{r}" in text), (key, lang)
    # диагностика причин: код ответа и текст сервера подставляются
    assert "{c}" in _locale("ru")["mcp_err_version"]
    assert "{c}" in _locale("en")["mcp_err_remote"]
    assert "{t}" in _locale("en")["mcp_err_setting"]
    assert "{t}" in _locale("ru")["mcp_err_image"]
