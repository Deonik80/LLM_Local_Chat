from __future__ import annotations
from models import Attachment, ChatMessage, rehydrate_attachment


def test_roundtrip():
    m = ChatMessage(text="привет", is_user=True, rating=4,
                    feedback_type="helpful", gen_stats="10 tok", stopped=True)
    d = m.to_dict()
    m2 = ChatMessage.from_dict(d)
    assert m2.text == "привет"
    assert m2.is_user is True
    assert m2.rating == 4
    assert m2.feedback_type == "helpful"
    assert m2.gen_stats == "10 tok"
    assert m2.stopped is True
    assert m2.uid == m.uid  # uid переживает save/load


def test_uid_unique_and_stable():
    a, b = ChatMessage(text="a"), ChatMessage(text="b")
    assert a.uid and b.uid and a.uid != b.uid
    # legacy-JSON без uid: from_dict выдаёт новый, повторный roundtrip сохраняет
    m2 = ChatMessage.from_dict({"text": "old", "is_user": True})
    m3 = ChatMessage.from_dict(m2.to_dict())
    assert m3.uid == m2.uid
    assert len(m2.uid) == 12


def test_legacy_mcp_rid_fields_ignored():
    # MCP стал stateless (/v1/responses): поля mcp_rid/mcp_model убраны, но
    # старые чаты с ними должны грузиться без ошибок (и не плодить поля)
    m = ChatMessage.from_dict({"text": "y", "mcp_rid": "resp_abc",
                               "mcp_model": "granite", "is_user": False})
    assert m.text == "y"
    assert m.is_user is False
    assert not hasattr(m, "mcp_rid")
    assert not hasattr(m, "mcp_model")
    d = m.to_dict()
    assert "mcp_rid" not in d and "mcp_model" not in d


def test_legacy_invalid_rating_coerced():
    m = ChatMessage.from_dict({"text": "x", "is_user": False,
                               "rating": 99, "feedback_type": "spam"})
    assert m.rating is None
    assert m.feedback_type is None


def test_legacy_missing_fields_get_defaults():
    m = ChatMessage.from_dict({"text": "only"})
    assert m.is_user is False
    assert m.variants == []
    assert m.stopped is False
    assert m.ts > 0


def test_to_dict_drops_b64_when_asked():
    m = ChatMessage(text="img", attachments=[Attachment(path="p.png", mime="image/png", b64="XX")])
    assert "b64" not in m.to_dict(include_b64=False)["attachments"][0]
    assert m.to_dict(include_b64=True)["attachments"][0]["b64"] == "XX"


def test_validate_assignment_checks_rating():
    m = ChatMessage(text="x")
    try:
        m.rating = 7
        raised = False
    except Exception:
        raised = True
    assert raised


def test_rehydrate_skips_non_images_and_missing_files(tmp_path):
    a = Attachment(path=str(tmp_path / "gone.png"), mime="image/png")
    assert rehydrate_attachment(a).b64 is None  # файла нет
    txt = Attachment(path="note.txt", mime="text/plain")
    assert rehydrate_attachment(txt) is txt or rehydrate_attachment(txt).b64 is None
    # уже с b64 — не трогаем
    img = Attachment(path="x.png", mime="image/png", b64="QQ==")
    assert rehydrate_attachment(img) is img


def test_rehydrate_reads_image_from_disk(tmp_path):
    p = tmp_path / "i.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nfake")
    a = Attachment(path=str(p), mime="image/png")
    out = rehydrate_attachment(a)
    assert out.b64  # закодирован обратно
