from __future__ import annotations
import json

from models import ChatMessage
from repositories import ChatRepository, ProfilesRepository, SettingsRepository


def _repos(tmp_path):
    return ChatRepository(tmp_path / "chats", tmp_path / "index.json")


def test_index_roundtrip(tmp_path):
    r = _repos(tmp_path)
    assert r.load_index() == []
    idx = [{"id": "a1", "title": "Привет", "ts": 1.0}]
    r.save_index(idx)
    assert r.load_index() == idx


def test_chat_roundtrip(tmp_path):
    r = _repos(tmp_path)
    msgs = [ChatMessage(text="hi", is_user=True),
            ChatMessage(text="hello", is_user=False, rating=5, feedback_type="helpful")]
    r.save_chat("c1", msgs)
    loaded = r.load_chat("c1")
    assert [m.text for m in loaded] == ["hi", "hello"]
    assert loaded[1].rating == 5
    assert loaded[1].feedback_type == "helpful"


def test_save_chat_does_not_persist_b64(tmp_path):
    from models import Attachment
    r = _repos(tmp_path)
    m = ChatMessage(text="img", is_user=True,
                    attachments=[Attachment(path="x.png", mime="image/png", b64="QUJD")])
    r.save_chat("c2", [m])
    raw = json.loads((tmp_path / "chats" / "c2.json").read_text("utf-8"))
    assert "b64" not in raw[0]["attachments"][0]
    assert raw[0]["attachments"][0]["path"] == "x.png"


def test_corrupt_chat_preserved_and_returns_empty(tmp_path):
    r = _repos(tmp_path)
    r.chat_path("bad").write_text("{broken", encoding="utf-8")
    assert r.load_chat("bad") == []
    assert r.chat_path("bad").with_name("bad.json.corrupt").is_file()


def test_corrupt_index_preserved_and_returns_empty(tmp_path):
    r = _repos(tmp_path)
    r.index_file.write_text("[1, 2,", encoding="utf-8")
    assert r.load_index() == []
    assert r.index_file.with_name("index.json.corrupt").is_file()


def test_delete_chat(tmp_path):
    r = _repos(tmp_path)
    r.save_chat("c3", [ChatMessage(text="x")])
    r.delete_chat("c3")
    assert not r.chat_path("c3").exists()


def test_save_chat_updates_index_preview_and_count(tmp_path):
    r = _repos(tmp_path)
    r.save_index([{"id": "c4", "title": "t", "ts": 1.0}])
    long_text = "А" * 100
    r.save_chat("c4", [ChatMessage(text="первое", is_user=True),
                       ChatMessage(text=long_text, is_user=False)])
    entry = r.load_index()[0]
    assert entry["msg_count"] == 2
    assert entry["preview"] == long_text[:42]
    # пустой чат
    r.save_chat("c4", [])
    assert r.load_index()[0]["msg_count"] == 0


def test_settings_merge_defaults(tmp_path):
    p = tmp_path / "settings.json"
    repo = SettingsRepository(p, {"a": 1, "b": "x"})
    s = repo.load()
    assert s == {"a": 1, "b": "x"}
    repo.save({"a": 42, "b": "y", "c": True})
    assert repo.load() == {"a": 42, "b": "y", "c": True}


def test_settings_corrupt_returns_defaults(tmp_path):
    p = tmp_path / "settings.json"
    p.write_text("not json", encoding="utf-8")
    repo = SettingsRepository(p, {"a": 1})
    assert repo.load() == {"a": 1}
    assert p.with_name("settings.json.corrupt").is_file()


def test_profiles_roundtrip(tmp_path):
    repo = ProfilesRepository(tmp_path / "profiles.json")
    assert repo.load() == {}
    repo.save({"Qwen": {"model": "m", "temperature": 0.2}})
    assert repo.load() == {"Qwen": {"model": "m", "temperature": 0.2}}


def test_search_all_by_title_and_body(tmp_path):
    r = _repos(tmp_path)
    r.save_index([
        {"id": "s1", "title": "Pasta recipe", "ts": 2.0},
        {"id": "s2", "title": "Other", "ts": 1.0},
    ])
    r.save_chat("s1", [ChatMessage(text="boil the water", is_user=True),
                       ChatMessage(text="add salt and pasta", is_user=False)])
    r.save_chat("s2", [ChatMessage(text="unrelated talk", is_user=True)])
    # по заголовку
    hits = r.search_all("pasta")
    cids = {h["cid"] for h in hits}
    assert "s1" in cids
    # по телу сообщения
    hits = r.search_all("salt")
    assert len(hits) == 1 and hits[0]["cid"] == "s1" and hits[0]["uid"]
    assert "salt" in hits[0]["snippet"]
    # пустой запрос / отсутствие совпадений
    assert r.search_all("") == []
    assert r.search_all("   ") == []
    assert r.search_all("zzz-nothing") == []


def test_search_all_limit(tmp_path):
    r = _repos(tmp_path)
    r.save_index([{"id": "L", "title": "t", "ts": 1.0}])
    r.save_chat("L", [ChatMessage(text=f"needle {i}", is_user=True) for i in range(10)])
    hits = r.search_all("needle", limit=3)
    assert len(hits) == 3


def test_search_all_title_hit_has_empty_uid(tmp_path):
    r = _repos(tmp_path)
    r.save_index([{"id": "T", "title": "needle title", "ts": 1.0}])
    r.save_chat("T", [ChatMessage(text="body", is_user=True)])
    hits = r.search_all("needle title")
    assert hits and hits[0]["uid"] == "" and hits[0]["cid"] == "T"
