from __future__ import annotations
import pytest
from localization import LocalizationManager

TABLE = {
    "ru": {"hello": "Привет", "greet": "Привет, {name}!", "missing_en": "только рус"},
    "en": {"hello": "Hello", "greet": "Hi, {name}!"},
}
PRESET = {"Обычный": "p_ordinary"}


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
