"""ModelService: renders prompt templates + environment system context for API calls."""
from __future__ import annotations
from app_state import PromptTemplate, EnvContext


PLACEHOLDERS = ("{user_query}", "{context_data}", "{role_definition}")

def render_template(template: PromptTemplate, *, user_query: str = "",
                    context_data: str = "", role_definition: str = "") -> str:
    """Substitute placeholders; sections sorted by weight desc; disabled skipped."""
    mapping = {"user_query": user_query, "context_data": context_data, "role_definition": role_definition}
    secs = sorted([s for s in template.sections if getattr(s, "enabled", True)],
                  key=lambda s: float(getattr(s, "weight", 1.0)), reverse=True)
    out = []
    for s in secs:
        try:
            text = s.content.format(**mapping)
        except KeyError:
            text = s.content  # leave unknown placeholders as-is
        out.append(f"[{s.name} | weight={s.weight}]\n{text}")
    return "\n\n".join(out)


def format_env_context(env: EnvContext) -> str:
    """Format environment as a special System Context block."""
    if not env.requirements and not env.env_vars:
        return ""
    parts = ["[System Context: Environment]"]
    if env.requirements:
        parts.append(f"requirements ({env.raw_name or 'requirements.txt'}):\n{env.requirements}")
    if env.env_vars:
        keys = "\n".join(f"- {k}={v}" for k, v in env.env_vars.items())
        parts.append(f"environment variables:\n{keys}\nModel must respect these library versions and env vars.")
    return "\n".join(parts)


def build_system_prompt(base_system: str, template_text: str = "", env_text: str = "") -> str:
    blocks = [b for b in [env_text.strip(), template_text.strip(), (base_system or "").strip()] if b]
    return "\n\n".join(blocks)
