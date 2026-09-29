from __future__ import annotations
import pytest
from persistence import PersistenceService


def _seed(svc: PersistenceService, cid="c1"):
    svc.save(cid, [
        {"text": "q", "is_user": True},
        {"text": "a", "is_user": False},
    ])


def test_set_feedback_happy_path(tmp_path):
    svc = PersistenceService(tmp_path / "chats")
    _seed(svc)
    m = svc.set_feedback("c1", 1, rating=5, feedback_type="helpful", comment="ok")
    assert m["rating"] == 5
    assert m["feedback_type"] == "helpful"
    assert m["feedback_comment"] == "ok"
    assert svc.load("c1")[1]["rating"] == 5


def test_set_feedback_rejects_bad_type(tmp_path):
    svc = PersistenceService(tmp_path / "chats")
    _seed(svc)
    with pytest.raises(ValueError):
        svc.set_feedback("c1", 1, feedback_type="spam")


def test_set_feedback_rejects_bad_rating(tmp_path):
    svc = PersistenceService(tmp_path / "chats")
    _seed(svc)
    with pytest.raises(ValueError):
        svc.set_feedback("c1", 1, rating=9)


def test_set_feedback_index_bounds(tmp_path):
    svc = PersistenceService(tmp_path / "chats")
    _seed(svc)
    with pytest.raises(IndexError):
        svc.set_feedback("c1", 5, rating=3)


def test_set_feedback_only_assistant(tmp_path):
    svc = PersistenceService(tmp_path / "chats")
    _seed(svc)
    with pytest.raises(ValueError):
        svc.set_feedback("c1", 0, rating=3)


def test_collect_labeled(tmp_path):
    svc = PersistenceService(tmp_path / "chats")
    svc.save("c2", [
        {"text": "q", "is_user": True, "rating": 5},
        {"text": "a1", "is_user": False, "rating": 5},
        {"text": "a2", "is_user": False},
        {"text": "a3", "is_user": False, "feedback_type": "harmful"},
    ])
    labeled = svc.collect_labeled("c2")
    assert [m["text"] for m in labeled] == ["a1", "a3"]


def test_corrupt_file_returns_empty(tmp_path):
    svc = PersistenceService(tmp_path / "chats")
    (tmp_path / "chats" / "bad.json").write_text("{oops", encoding="utf-8")
    assert svc.load("bad") == []
