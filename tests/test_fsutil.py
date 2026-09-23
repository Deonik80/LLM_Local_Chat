from __future__ import annotations
import fsutil
from fsutil import atomic_write_text, preserve_corrupt, read_json, write_json


def test_atomic_write_creates_and_overwrites(tmp_path):
    p = tmp_path / "sub" / "a.json"
    atomic_write_text(p, "hello")
    assert p.read_text("utf-8") == "hello"
    atomic_write_text(p, "world")
    assert p.read_text("utf-8") == "world"
    # после записи не остаётся мусорных tmp-файлов
    assert [f.name for f in p.parent.iterdir()] == ["a.json"]


def test_atomic_write_survives_locked_destination(tmp_path, monkeypatch):
    """WinError 32: целевой файл занят → ретраи, затем запись поверх."""
    p = tmp_path / "lock.json"
    atomic_write_text(p, "old")
    # симулируем вечную занятость для os.replace (как читатель без FILE_SHARE_DELETE)
    def busy(tmp, dst):
        raise PermissionError(32, "sharing violation", str(dst))
    monkeypatch.setattr(fsutil.os, "replace", busy)
    monkeypatch.setattr(fsutil, "_REPLACE_RETRIES", 2)
    monkeypatch.setattr(fsutil, "_REPLACE_DELAY", 0.001)
    atomic_write_text(p, "new")  # не должен упасть
    assert p.read_text("utf-8") == "new"
    assert not list(tmp_path.glob("*.tmp"))


def test_atomic_write_removes_tmp_when_both_paths_fail(tmp_path, monkeypatch):
    """И replace, и фоллбэк упали → исключение пробрасывается, tmp не остаётся."""
    p = tmp_path / "x.json"
    atomic_write_text(p, "base")

    def busy(tmp, dst):
        raise PermissionError(32, "sharing violation", str(dst))
    monkeypatch.setattr(fsutil.os, "replace", busy)
    monkeypatch.setattr(fsutil, "_REPLACE_RETRIES", 2)
    monkeypatch.setattr(fsutil, "_REPLACE_DELAY", 0.001)
    monkeypatch.setattr(fsutil, "_write_in_place",
                        lambda *a, **k: (_ for _ in ()).throw(OSError("blocked")))
    try:
        atomic_write_text(p, "fail")
    except OSError as ex:
        assert "blocked" in str(ex)
    else:
        raise AssertionError("ожидали OSError")
    assert not list(tmp_path.glob("*.tmp"))
    assert p.read_text("utf-8") == "base"  # старые данные целы


def test_write_json_roundtrip(tmp_path):
    p = tmp_path / "d.json"
    write_json(p, {"ключ": [1, 2, 3]})
    assert read_json(p, None) == {"ключ": [1, 2, 3]}


def test_read_json_missing_returns_default(tmp_path):
    assert read_json(tmp_path / "nope.json", {"x": 1}) == {"x": 1}


def test_read_json_corrupt_preserves_copy(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("{oops", encoding="utf-8")
    assert read_json(p, []) == []
    side = tmp_path / "bad.json.corrupt"
    assert side.is_file()
    assert side.read_text("utf-8") == "{oops"
    # повторное чтение не создаёт новых копий
    assert read_json(p, []) == []
    assert len(list(tmp_path.glob("*.corrupt"))) == 1


def test_preserve_corrupt_keeps_first_copy(tmp_path):
    p = tmp_path / "x.json"
    p.write_text("first", encoding="utf-8")
    side = preserve_corrupt(p)
    p.write_text("second", encoding="utf-8")
    preserve_corrupt(p)
    assert side.read_text("utf-8") == "first"


def test_read_json_invalid_utf8_returns_default(tmp_path):
    p = tmp_path / "bin.json"
    p.write_bytes(b"\xff\xfe\x00broken")
    assert read_json(p, "d") == "d"
