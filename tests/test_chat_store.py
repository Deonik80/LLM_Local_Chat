from __future__ import annotations
from chat_store import (ChatStore, apply_folder_filter, cut_tail,
                        edit_user_text, list_folders)
from models import ChatMessage
from repositories import ChatRepository, SettingsRepository


def _store(tmp_path):
    repo = ChatRepository(tmp_path / "chats", tmp_path / "index.json")
    sets = SettingsRepository(tmp_path / "settings.json", {"theme": "dark"})
    return ChatStore(chat_repo=repo, settings_repo=sets)


def test_subscribe_emit_unsubscribe(tmp_path):
    st = _store(tmp_path)
    seen = []
    unsub = st.subscribe(lambda e, s: seen.append(e))
    st.set("conn_ok", True, "conn")
    st.update("update", cid="x")
    unsub()
    st.set("chat_filter", "q", "filter")
    assert seen == ["conn", "update"]


def test_new_and_open_chat(tmp_path):
    st = _store(tmp_path)
    events = []
    st.subscribe(lambda e, s: events.append(e))
    cid = st.new_chat("Новый чат")
    assert st.state["cid"] == cid
    assert st.state["msgs"] == []
    assert "chat:new" in events
    assert st.chat_repo.load_index()[0]["id"] == cid

    st.set_msgs([ChatMessage(text="hi", is_user=True)])
    st.save_current()
    st.update("chat:open", cid="other", msgs=[], files=[],
              chat_filter="", rated_only=False)
    msgs = st.open_chat(cid)
    assert [m.text for m in msgs] == ["hi"]
    assert st.state["cid"] == cid
    assert st.state["chat_filter"] == ""


def test_append_and_set_msgs(tmp_path):
    st = _store(tmp_path)
    events = []
    st.subscribe(lambda e, s: events.append(e))
    st.append_msg(ChatMessage(text="a"))
    st.set_msgs([])
    assert events == ["msgs:appended", "msgs:set"]


def test_filters(tmp_path):
    st = _store(tmp_path)
    events = []
    st.subscribe(lambda e, s: events.append(e))
    st.set_filter("abc")
    st.set_filter(None)
    st.set_rated_only(True)
    assert st.state["chat_filter"] == ""
    assert st.state["rated_only"] is True
    assert events == ["filter", "filter", "filter"]


def test_delete_chat_updates_index(tmp_path):
    st = _store(tmp_path)
    cid = st.new_chat("t")
    st.delete_chat(cid)
    assert st.chat_repo.load_index() == []
    assert not st.chat_repo.chat_path(cid).exists()


def test_subscriber_exception_does_not_break_emit(tmp_path):
    st = _store(tmp_path)
    good = []
    def bad(e, s):
        raise RuntimeError("boom")
    st.subscribe(bad)
    st.subscribe(lambda e, s: good.append(e))
    st.set("x", 1)  # не должно упасть
    assert good == ["set:x"]


# ---------- folders (pure) ----------

def test_list_folders_sorted_unique():
    idx = [{"id": "a", "folder": "Work"}, {"id": "b", "folder": "личное"},
           {"id": "c"}, {"id": "d", "folder": "  "}, {"id": "e", "folder": "work"}]
    out = list_folders(idx)
    # пустые/пробельные не учитываются; регистрозависимые дубли — разные папки
    assert set(out) == {"Work", "work", "личное"}
    assert out == sorted(out, key=str.lower)


def test_list_folders_empty():
    assert list_folders([]) == []
    assert list_folders([{"id": "a"}]) == []


def test_apply_folder_filter():
    idx = [{"id": "a", "folder": "Work"}, {"id": "b", "folder": "Home"}, {"id": "c"}]
    assert apply_folder_filter(idx, "") == idx
    assert apply_folder_filter(idx, None) == idx
    assert [c["id"] for c in apply_folder_filter(idx, "Work")] == ["a"]
    assert apply_folder_filter(idx, "Nope") == []


# ---------- edit-and-resend (pure) ----------

def test_cut_tail_collects_assistant_variants():
    msgs = [ChatMessage(text="q", is_user=True),
            ChatMessage(text="a1", is_user=False),
            ChatMessage(text="q2", is_user=True),
            ChatMessage(text="a2", is_user=False),
            ChatMessage(text="", is_user=False)]
    keep, variants = cut_tail(msgs, 1)
    assert [m.text for m in keep] == ["q"]
    # все непустые ответы хвоста (как в старом branch_from); пустые — нет
    assert variants == ["a1", "a2"]


def test_cut_tail_keeps_at_most_five_variants():
    msgs = [ChatMessage(text=f"a{i}", is_user=False) for i in range(9)]
    keep, variants = cut_tail(msgs, 0)
    assert keep == []
    assert len(variants) == 5


def test_edit_user_text_updates_and_cuts():
    msgs = [ChatMessage(text="old", is_user=True),
            ChatMessage(text="reply", is_user=False),
            ChatMessage(text="more", is_user=True)]
    res = edit_user_text(msgs, msgs[0].uid, "new text")
    assert res is not None
    idx, variants = res
    assert idx == 0
    assert msgs[0].text == "new text"
    assert len(msgs) == 1  # хвост отрезан
    assert variants == ["reply"]


def test_edit_user_text_rejects_assistant_and_missing():
    a = ChatMessage(text="bot", is_user=False)
    assert edit_user_text([a], a.uid, "x") is None
    assert edit_user_text([ChatMessage(text="u", is_user=True)], "nope", "x") is None


# ---------- index metadata (store) ----------

def test_rename_and_get_entry(tmp_path):
    st = _store(tmp_path)
    cid = st.new_chat("old")
    assert st.rename_chat(cid, "new") is True
    assert st.get_entry(cid)["title"] == "new"
    assert st.rename_chat("missing", "x") is False
    assert st.get_entry("missing") is None


def test_toggle_pin(tmp_path):
    st = _store(tmp_path)
    cid = st.new_chat("t")
    events = []
    st.subscribe(lambda e, s: events.append(e))
    assert st.toggle_pin(cid) is True
    assert st.get_entry(cid)["pinned"] is True
    assert st.toggle_pin(cid) is False
    assert st.get_entry(cid).get("pinned") is False
    assert st.toggle_pin("missing") is False
    assert events == ["chat:pin", "chat:pin"]


def test_auto_rename_only_when_allowed(tmp_path):
    st = _store(tmp_path)
    cid = st.new_chat("Новый чат")
    # разрешено заменять дефолт
    assert st.auto_rename(cid, "Q&A", ("Новый чат", "New chat")) is True
    assert st.get_entry(cid)["title"] == "Q&A"
    # ручное название не затирается
    assert st.auto_rename(cid, "Other", ("Новый чат", "New chat")) is False
    assert st.get_entry(cid)["title"] == "Q&A"
    assert st.auto_rename("missing", "x", ("x",)) is False


def test_set_folder_and_persist(tmp_path):
    st = _store(tmp_path)
    cid = st.new_chat("t")
    events = []
    st.subscribe(lambda e, s: events.append(e))
    assert st.set_folder(cid, "Work") is True
    assert st.get_entry(cid)["folder"] == "Work"
    assert list_folders(st.chat_repo.load_index()) == ["Work"]
    assert st.set_folder(cid, "  ") is True  # очистка
    assert "folder" not in st.get_entry(cid)
    assert st.set_folder("missing", "X") is False
    assert events == ["chat:folder", "chat:folder"]


# ---------- selection ----------

def test_toggle_and_clear_selection(tmp_path):
    st = _store(tmp_path)
    events = []
    st.subscribe(lambda e, s: events.append(e))
    assert st.toggle_select("u1") is True
    assert st.state["selected"] == {"u1"}
    assert st.toggle_select("u1") is False
    assert st.state["selected"] == set()
    st.toggle_select("u2")
    st.clear_selection()
    assert st.state["selected"] == set()
    st.clear_selection()  # пусто — без события
    assert events == ["selection", "selection", "selection", "selection"]


# ---------- scrub on open ----------

def test_open_chat_scrubs_empty_assistant(tmp_path):
    st = _store(tmp_path)
    cid = st.new_chat("t")
    st.chat_repo.save_chat(cid, [
        ChatMessage(text="q", is_user=True),
        ChatMessage(text="", is_user=False),
        ChatMessage(text="ok", is_user=False, variants=["v"]),
    ])
    msgs = st.open_chat(cid)
    assert [m.text for m in msgs] == ["q", "ok"]
    # сохранилось на диск
    assert [m.text for m in st.chat_repo.load_chat(cid)] == ["q", "ok"]


# ---------- search delegation ----------

def test_search_all_delegates_to_repo(tmp_path):
    st = _store(tmp_path)
    cid = st.new_chat("Recipe book")
    st.chat_repo.save_chat(cid, [ChatMessage(text="add sugar and honey", is_user=True)])
    hits = st.search_all("honey")
    assert len(hits) == 1
    assert hits[0]["cid"] == cid
    assert "honey" in hits[0]["snippet"]
