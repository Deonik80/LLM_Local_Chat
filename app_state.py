"""AppStateManager: templates metadata + environment context metadata."""
from __future__ import annotations
import json
from dataclasses import dataclass, field, asdict
from pathlib import Path


@dataclass
class PromptSection:
    name: str
    content: str
    weight: float = 1.0  # priority/weight; higher = more important
    enabled: bool = True

    def to_dict(self): return asdict(self)
    @staticmethod
    def from_dict(d): return PromptSection(d["name"], d.get("content", ""), float(d.get("weight", 1.0)), d.get("enabled", True))


@dataclass
class PromptTemplate:
    name: str
    sections: list = field(default_factory=list)  # list[PromptSection]
    description: str = ""

    def to_dict(self):
        return {"name": self.name, "description": self.description,
                "sections": [s.to_dict() if isinstance(s, PromptSection) else s for s in self.sections]}
    @staticmethod
    def from_dict(d):
        secs = [PromptSection.from_dict(s) for s in d.get("sections", [])]
        return PromptTemplate(d["name"], secs, d.get("description", ""))


@dataclass
class EnvContext:
    requirements: str = ""   # parsed requirements.txt content
    env_vars: dict = field(default_factory=dict)  # parsed .env (keys only or masked values)
    raw_name: str = ""

    def to_dict(self): return asdict(self)
    @staticmethod
    def from_dict(d): return EnvContext(d.get("requirements", ""), d.get("env_vars", {}), d.get("raw_name", ""))


class AppStateManager:
    """Stores prompt-template metadata + environment metadata (persisted as JSON)."""

    def __init__(self, store: str | Path = "data/app_state.json"):
        self.store = Path(store)
        self.templates: dict[str, PromptTemplate] = {}
        self.active_template: str | None = None
        self.env = EnvContext()
        self.load()

    # ---- templates ----
    def create_template(self, name: str, sections: list[PromptSection], description: str = "") -> PromptTemplate:
        t = PromptTemplate(name, sections, description)
        self.templates[name] = t
        self.save()
        return t

    def delete_template(self, name: str):
        self.templates.pop(name, None)
        if self.active_template == name:
            self.active_template = None
        self.save()

    def set_active(self, name: str | None):
        if name is not None and name not in self.templates:
            raise KeyError(f"unknown template: {name}")
        self.active_template = name
        self.save()

    def get_active(self) -> PromptTemplate | None:
        return self.templates.get(self.active_template) if self.active_template else None

    # ---- environment ----
    def load_requirements_file(self, path: str | Path) -> str:
        text = Path(path).read_text(encoding="utf-8-sig")
        self.env.requirements = text[:8000]
        self.env.raw_name = Path(path).name
        self.save()
        return self.env.requirements

    def load_dotenv_file(self, path: str | Path, mask_values: bool = True) -> dict:
        out = {}
        for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k.startswith("export "):
                k = k[7:].strip()
            out[k] = "***" if mask_values else v.strip()
        self.env.env_vars = out
        self.save()
        return out

    def clear_env(self):
        self.env = EnvContext()
        self.save()

    # ---- persistence ----
    def save(self):
        self.store.parent.mkdir(parents=True, exist_ok=True)
        self.store.write_text(json.dumps({
            "templates": {k: v.to_dict() for k, v in self.templates.items()},
            "active_template": self.active_template,
            "env": self.env.to_dict(),
        }, ensure_ascii=False, indent=2), "utf-8")

    def load(self):
        try:
            if self.store.is_file():
                raw = json.loads(self.store.read_text("utf-8"))
                self.templates = {k: PromptTemplate.from_dict(v) for k, v in raw.get("templates", {}).items()}
                self.active_template = raw.get("active_template")
                if "env" in raw:
                    self.env = EnvContext.from_dict(raw["env"])
        except Exception:
            pass
