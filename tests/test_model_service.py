from __future__ import annotations
from app_state import EnvContext, PromptSection, PromptTemplate
from model_service import build_system_prompt, format_env_context, render_template


def _template():
    return PromptTemplate("t", [
        PromptSection("context", "Контекст: {context_data}", weight=2.0),
        PromptSection("role", "Роль: {role_definition}", weight=3.0),
        PromptSection("query", "Запрос: {user_query}", weight=1.0),
        PromptSection("off", "выключен", weight=9.0, enabled=False),
    ])


def test_render_template_sorted_by_weight():
    out = render_template(_template(), user_query="Q", context_data="C", role_definition="R")
    assert out.index("Роль:") < out.index("Контекст:") < out.index("Запрос:")
    assert "выключен" not in out
    assert "Запрос: Q" in out


def test_render_template_unknown_placeholder_left_as_is():
    t = PromptTemplate("t", [PromptSection("s", "до {unknown} после")])
    out = render_template(t, user_query="Q")
    assert out == "[s | weight=1.0]\nдо {unknown} после"


def test_format_env_context_masks_secrets():
    env = EnvContext(requirements="flask==3",
                     env_vars={"API_KEY": "real", "APP_ENV": "prod"},
                     raw_name="requirements.txt")
    out = format_env_context(env)
    assert "real" not in out
    assert "API_KEY=***" in out
    assert "APP_ENV=prod" in out
    assert "flask==3" in out
    assert "requirements.txt" in out


def test_format_env_context_empty():
    assert format_env_context(EnvContext()) == ""


def test_build_system_prompt_order():
    out = build_system_prompt("base", "template", "env")
    assert out == "env\n\ntemplate\n\nbase"
    assert build_system_prompt("base") == "base"
    assert build_system_prompt("", "", "") == ""
