from __future__ import annotations
from voice import VoiceError, clean_for_speech, split_for_speech


def test_voice_error_carries_i18n_code():
    e = VoiceError("Microphone unavailable: x", "e_voice_mic", e="x")
    assert e.code == "e_voice_mic"
    assert e.params == {"e": "x"}
    assert str(e) == "Microphone unavailable: x"
    # без кода — обычное исключение
    plain = VoiceError("plain")
    assert plain.code == ""
    assert plain.params == {}


def test_clean_strips_code_blocks():
    out = clean_for_speech("Смотри:\n```python\nprint(1)\n```\nконец")
    assert "print" not in out
    assert "Смотри" in out and "конец" in out


def test_clean_strips_markdown_and_links():
    out = clean_for_speech("# Заголовок **жирный** [ссылка](http://x.y) `код`")
    assert "Заголовок" in out
    assert "жирный" in out
    assert "ссылка" in out
    assert "http" not in out
    assert "#" not in out and "*" not in out and "`" not in out


def test_clean_empty():
    assert clean_for_speech("") == ""
    assert clean_for_speech("```\n```") .strip() in ("", " ")


def test_clean_caps_length():
    out = clean_for_speech("а" * 30000)
    assert len(out) == 20000


def test_split_short_single_chunk():
    assert split_for_speech("Одно предложение.") == ["Одно предложение."]


def test_split_respects_limit():
    text = ". ".join("Предложение номер номер " * 5 for _ in range(40))
    chunks = split_for_speech(text, limit=200)
    assert len(chunks) > 1
    assert all(len(c) <= 200 for c in chunks)
    # содержимое не теряется (без учёта склейки пробелов)
    joined = "".join(chunks)
    assert "Предложение" in joined


def test_split_hard_cuts_giant_sentence():
    chunks = split_for_speech("б" * 500, limit=100)
    assert all(len(c) <= 100 for c in chunks)
    assert sum(len(c) for c in chunks) == 500


def test_split_empty():
    assert split_for_speech("") == []
    assert split_for_speech("   ") == []
