from __future__ import annotations
from pathlib import Path

from api_payload import (APIPayloadBuilder, estimate_tokens, mask_env_value,
                         payload_has_images, payload_stats)
from models import Attachment, ChatMessage


def test_estimate_tokens_min_one():
    assert estimate_tokens("") == 1
    assert estimate_tokens("ab") == 1
    assert estimate_tokens("a" * 40) == 10


def test_estimate_tokens_cyrillic_denser_than_ascii():
    # та же длина строки: кириллицы должно быть заметно больше, чем len//4
    ascii_est = estimate_tokens("a" * 40)          # 10
    cyr_est = estimate_tokens("я" * 40)            # 20
    assert cyr_est > ascii_est
    assert cyr_est == 20


def test_estimate_tokens_mixed_text():
    out = estimate_tokens("Hello мир")  # 6 ASCII (включая пробел) + 3 не-ASCII
    # (6+3)//4 + (3+1)//2 = 2 + 2 = 4
    assert out == 4


def test_mask_env_value():
    assert mask_env_value("OPENAI_API_KEY", "sk-123") == "***"
    assert mask_env_value("GITHUB_TOKEN", "ghp_x") == "***"
    assert mask_env_value("DB_PASSWORD", "p") == "***"
    assert mask_env_value("MY_SECRET", "s") == "***"
    assert mask_env_value("PATH", "/usr/bin") == "/usr/bin"
    assert mask_env_value("PYTHON_VERSION", "3.11") == "3.11"


def test_format_env_block_masks_secrets():
    block = APIPayloadBuilder.format_env_block({
        "env_requirements": "requests==2.31",
        "env_vars": {"OPENAI_API_KEY": "sk-real", "HTTP_PROXY": "http://p:1"},
    })
    assert "sk-real" not in block
    assert "OPENAI_API_KEY=***" in block
    assert "HTTP_PROXY=http://p:1" in block
    assert "requests==2.31" in block


def test_format_env_block_empty():
    assert APIPayloadBuilder.format_env_block({}) == ""
    assert APIPayloadBuilder.format_env_block({"env_vars": {}}) == ""


def test_effective_system_joins_env_and_prompt():
    b = APIPayloadBuilder()
    out = b.effective_system({"env_requirements": "flask"}, "Ты помощник")
    assert out.startswith("[System Context: Environment]")
    assert out.endswith("Ты помощник")


def test_trim_by_max_messages():
    b = APIPayloadBuilder(max_ctx_messages=3)
    msgs = [ChatMessage(text=str(i), is_user=True) for i in range(10)]
    out = b.trim(msgs)
    assert [m.text for m in out] == ["7", "8", "9"]


def test_trim_by_token_budget_keeps_at_least_two():
    b = APIPayloadBuilder(max_ctx_messages=100, estimate=lambda s: 100)
    msgs = [ChatMessage(text="x" * 10, is_user=True) for _ in range(6)]
    out = b.trim(msgs, context_tokens=150)  # влезает ровно 1 сообщение -> остаются 2
    assert len(out) == 2


def test_trim_no_budget_keeps_all():
    b = APIPayloadBuilder(max_ctx_messages=10)
    msgs = [ChatMessage(text="hi", is_user=True)] * 5
    assert len(b.trim(msgs, context_tokens=0)) == 5


def test_build_system_and_user_mapping():
    b = APIPayloadBuilder()
    api = b.build([ChatMessage(text="q", is_user=True),
                   ChatMessage(text="a", is_user=False)],
                  system="sys")
    assert api[0] == {"role": "system", "content": "sys"}
    assert api[1]["role"] == "user"
    assert api[1]["content"][0] == {"type": "text", "text": "q"}
    assert api[2] == {"role": "assistant", "content": "a"}


def test_build_images_and_strip():
    img = Attachment(path="a.png", mime="image/png", b64="QUJD")
    m = ChatMessage(text="see", is_user=True, attachments=[img])
    b = APIPayloadBuilder()
    api = b.build([m], system="")
    assert payload_has_images(api)
    assert payload_stats(api) == {"messages": 1, "images": 1}

    stripped = b.build([m], system="", strip_images=True)
    assert not payload_has_images(stripped)
    assert "image omitted" in stripped[0]["content"][1]["text"]


def test_build_file_attachment_extracts_text(tmp_path):
    f = tmp_path / "note.txt"
    f.write_text("содержимое файла", encoding="utf-8")
    m = ChatMessage(text="вот", is_user=True,
                    attachments=[Attachment(path=str(f))])
    b = APIPayloadBuilder(extract_text=lambda p: Path(p).read_text("utf-8"))
    api = b.build([m], system="")
    parts = api[0]["content"]
    assert any("содержимое файла" in p.get("text", "") for p in parts)


def test_build_for_settings_uses_budget_and_system():
    b = APIPayloadBuilder(max_ctx_messages=5)
    msgs = [ChatMessage(text="m", is_user=True)] * 10
    api = b.build_for_settings(msgs, {"system_prompt": "S", "context_length": "0"})
    assert api[0]["content"] == "S"
    assert len(api) == 6  # system + 5


def test_server_api_key_never_reaches_the_prompt():
    """Ключ сервера (auth_mode/api_key) не должен утекать в контекст модели."""
    settings = {"auth_mode": "api_key", "api_key": "lm-studio-secret-key",
                "system_prompt": "S", "context_length": "0",
                "env_requirements": "flask==3.0",
                "env_vars": {"OPENAI_API_KEY": "sk-real", "APP_ENV": "prod"}}
    b = APIPayloadBuilder()
    block = b.format_env_block(settings)
    api = b.build_for_settings([ChatMessage(text="q", is_user=True)], settings)
    assert "lm-studio-secret-key" not in block
    assert "lm-studio-secret-key" not in str(api)
    assert "auth_mode" not in block and "api_key=" not in block
