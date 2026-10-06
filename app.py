# Copyright (C) 2026 Deonik80 (https://github.com/Deonik80)
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <https://gnu.org>.

# LLM Local Chat
from __future__ import annotations
import asyncio, base64, csv, html as html_mod, json, mimetypes, os, time
# ---------- опциональные парсеры документов ----------
# pypdf / python-docx / openpyxl / pillow — опциональны: без них приложение
# стартует, а попытка прочитать такой файл падает с понятной подсказкой.
# Никаких авто-pip-установок в рантайме: окружение не мутируем (сеть,
# воспроизводимость, supply-chain).
_AUTO_DEPS = {
    "pypdf": "pypdf",
    "docx": "python-docx",
    "openpyxl": "openpyxl",
    "PIL": "pillow",
}

def _check_doc_deps() -> None:
    missing: list[str] = []
    for mod, pip_name in _AUTO_DEPS.items():
        try:
            __import__(mod)
        except ImportError:
            missing.append(pip_name)
    if missing:
        print(f"[deps] optional document parsers missing: {missing} — "
              f"PDF/Word/Excel attachments will be unavailable. "
              f"Install with: pip install {' '.join(missing)}")

_check_doc_deps()

from pathlib import Path
import flet as ft  # Version: 0.86.5

# ---------- i18n: RU/EN (строки — в файлах locales/<lang>.json) ----------
def _load_langs() -> dict:
    """Загрузить таблицы переводов locales/ru.json + locales/en.json."""
    base = Path(__file__).parent / "locales"
    out: dict = {}
    for code in ("ru", "en"):
        p = base / f"{code}.json"
        try:
            if p.is_file():
                out[code] = json.loads(p.read_text("utf-8"))
        except (OSError, json.JSONDecodeError) as ex:
            print(f"[i18n] cannot load {p}: {ex}")
    if "ru" not in out:
        out["ru"] = {"title": "LLM Local Chat"}  # минимум для стартового экрана
    out.setdefault("en", {})
    return out

LANGS = _load_langs()
CUR = {"lang": "ru"}
from localization import LocalizationManager
from logging_config import setup_logging, get_logger
_i18n = LocalizationManager(LANGS, default="ru", preset_i18n={
    "Обычный": "p_ordinary", "Переводчик": "p_translator",
    "Ревью кода": "p_reviewer", "Простыми словами": "p_simple"})
_log = get_logger("app")
# ---------- 6: presentation-слой (views.py) ----------
from views import (visible_messages as _visible_messages,  # type: ignore
                   format_feedback as _format_feedback,
                   token_stats as _token_stats,
                   ViewBinder as _ViewBinder)

def tr(key: str, **kw) -> str:
    return _i18n.tr(key, **kw)

def voice_err(ex: BaseException) -> str:
    """Перевести VoiceError через его i18n-код; прочие исключения — как есть."""
    code = getattr(ex, "code", "")
    if code:
        return tr(code, **getattr(ex, "params", {}))
    return str(ex)
PRESET_I18N = {"Обычный": "p_ordinary", "Переводчик": "p_translator",
               "Ревью кода": "p_reviewer", "Простыми словами": "p_simple"}
def preset_display(key: str) -> str:
    k = PRESET_I18N.get(key)
    return tr(k) if k else key
BASE_URL = os.getenv("LM_STUDIO_URL", "http://localhost:1234/v1")
CHAT_URL = f"{BASE_URL}/chat/completions"
MODEL_URL = f"{BASE_URL}/models"
DEFAULT_MODEL = os.getenv("DEFAULT_MODEL", "default-model")
TIMEOUT = float(os.getenv("REQUEST_TIMEOUT", "180"))
MAX_IMG = int(os.getenv("MAX_IMAGE_MB", "10")) * 1024 * 1024
MAX_TXT = int(os.getenv("MAX_TEXT_MB", "5")) * 1024 * 1024
MAX_CTX = int(os.getenv("MAX_CONTEXT_MESSAGES", "20"))
DATA = Path(__file__).parent / "data"
CHATS = DATA / "chats"; ATTACH = DATA / "attachments"
INDEX_F = DATA / "index.json"; SET_F = DATA / "settings.json"; PRESETS_F = DATA / "presets.json"
PROFILES_F = DATA / "profiles.json"
for p in (CHATS, ATTACH): p.mkdir(parents=True, exist_ok=True)

def _all_presets() -> dict:  # встроенные (на текущем языке) + пользовательские из presets.json
    d = {k: (v.get(CUR["lang"], v["ru"]) if isinstance(v, dict) else v)
         for k, v in BUILTIN_PRESETS.items()}
    d.update(_load_custom_presets())
    return d
def _save_custom_presets(custom: dict):
    from fsutil import write_json
    write_json(PRESETS_F, custom)
def _load_custom_presets() -> dict:
    from fsutil import read_json
    raw = read_json(PRESETS_F, {})
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}

DEFAULT_SETTINGS = {"system_prompt": "", "temperature": 0.7, "top_p": 1.0,
    "repeat_penalty": 1.0, "seed": -1, "max_tokens": 2048,
    "context_length": int(os.getenv("CONTEXT_LENGTH", "8192")), "send_images": True,
    "model": os.getenv("DEFAULT_MODEL", ""), "no_vision_models": [],
    "window_width": 1100, "window_height": 860, "window_left": None, "window_top": None,
    "window_maximized": False,
    "lang": "ru",
    # доступ к серверу: 'none' (по умолчанию) или 'api_key'; значения из
    # окружения — только фолбэк, settings.json перекрывает их при запуске
    "auth_mode": os.getenv("LM_STUDIO_AUTH_MODE", "none"),
    "api_key": os.getenv("LM_STUDIO_API_KEY", ""),
    # MCP-серверы из mcp.json (LM Studio 0.4.0+): по умолчанию выключено, и
    # тогда запросы идут ровно как раньше — в /v1/chat/completions. Включённый
    # режим переключает клиент на stateless /v1/responses с блоком tools
    # (lm_client.mcp_active). Список серверов — метки из mcp.json, хранится
    # списком в форме 'mcp/<label>' (mcp_servers_from_settings нормализует), а
    # в payload /v1/responses уходят как server_label без префикса (lm_client.
    # _mcp_tools). Значения из
    # окружения — только фолбэк, settings.json перекрывает их при запуске.
    "mcp_enabled": os.getenv("LM_STUDIO_MCP_ENABLED", "").strip().lower()
                    in ("1", "on", "true", "yes", "y"),
    "mcp_servers": [x for x in os.getenv("LM_STUDIO_MCP_SERVERS", "").replace(",", " ").split() if x],
    # бэкенд LLM: 'lmstudio' | 'strata' (оба OpenAI-совместимы).
    # strata_url по умолчанию совпадает с сервером Strata из коробки.
    "backend": os.getenv("LLM_BACKEND", "lmstudio"),
    "strata_url": os.getenv("STRATA_URL", "http://127.0.0.1:8080/v1"),
    "reasoning_effort": os.getenv("STRATA_EFFORT", ""),
    "strata_mcp": True,
    "theme": "dark", "font_scale": 1.0, "preset": "Обычный"}
BUILTIN_PRESETS = {
    "Обычный": {"ru": "", "en": ""},
    "Переводчик": {"ru": "Ты профессиональный переводчик. Переводи точно, сохраняй стиль.",
                   "en": "You are a professional translator. Translate accurately and keep the style."},
    "Ревью кода": {"ru": "Ты senior-разработчик. Находи баги, предлагай исправления с кодом.",
                   "en": "You are a senior developer. Find bugs and suggest fixes with code."},
    "Простыми словами": {"ru": "Объясняй просто, с примерами и аналогиями.",
                         "en": "Explain simply, with examples and analogies."}}
PRESETS = _all_presets()
# (1) Дизайн-система: палитра + метрики в одном месте
S = {"radius": 12, "bubble_radius": 14, "pad": 12, "gap": 8,
     "sidebar_w": 260, "input_max": 4000, "bubble_margin": 110}
THEMES = {"dark": {"bg": "#1E1E1E", "panel": "#252526", "border": "#333333",
    "ubg": "#1976D2", "abg": "#2D2D2D", "utc": "#FFFFFF", "atc": "#E0E0E0",
    "muted": "#9A9A9A", "accent": "#4FA3FF", "hover": "#3A3A3C",
    "input_bg": "#252526", "chip_bg": "#333333", "shadow": "#000000"},
    "light": {"bg": "#F2F3F7", "panel": "#FFFFFF", "border": "#E0E3EB",
    "ubg": "#1976D2", "abg": "#FFFFFF", "utc": "#FFFFFF", "atc": "#222222",
    "muted": "#6B7280", "accent": "#1976D2", "hover": "#EEF1F6",
    "input_bg": "#FFFFFF", "chip_bg": "#EEF1F6", "shadow": "#B0B5C0"}}

# ---------- модели (Pydantic v2) ----------
from models import Attachment, ChatMessage  # type: ignore

class FileError(Exception): pass

# ---------- 2: парсинг файлов ----------
def encode_image(p: str):
    path = Path(p)
    if not path.is_file(): raise FileError(tr("file_not_found", p=p))
    if path.stat().st_size > MAX_IMG: raise FileError(tr("img_too_big"))
    mime, _ = mimetypes.guess_type(path.name)
    if not mime or not mime.startswith("image/"): raise FileError(tr("not_image", n=path.name))
    return base64.b64encode(path.read_bytes()).decode(), mime

def extract_text(p: str) -> str:
    """#2: txt/py/md/csv/pdf/docx/xlsx -> текст с обрезкой."""
    path = Path(p)
    if not path.is_file(): raise FileError(tr("file_not_found", p=p))
    suf = path.suffix.lower()
    try:
        if suf == ".pdf":
            from pypdf import PdfReader
            r = PdfReader(str(path))
            t = "\n".join((pg.extract_text() or "") for pg in r.pages)
            return _clip(t or tr("pdf_empty"))
        if suf == ".docx":
            from docx import Document
            return _clip("\n".join(x.text for x in Document(str(path)).paragraphs))
        if suf in (".xlsx", ".xlsm"):
            from openpyxl import load_workbook
            wb = load_workbook(str(path), read_only=True, data_only=True)
            out = []
            for ws in wb.worksheets[:5]:
                out.append(f"## {ws.title}")
                for row in list(ws.iter_rows(values_only=True))[:100]:
                    out.append(" | ".join("" if v is None else str(v) for v in row))
            return _clip("\n".join(out))
        if suf == ".csv":
            with open(path, encoding="utf-8-sig") as f:
                rows = list(csv.reader(f))[:200]
            return _clip("\n".join(" | ".join(r) for r in rows))
        # текстовые
        data = path.read_bytes()
        if len(data) > MAX_TXT: raise FileError(tr("file_too_big", m=MAX_TXT // 1048576))
        return _clip(data.decode("utf-8"))
    except FileError: raise
    except Exception as e: raise FileError(tr("cant_read", n=path.name, e=e))

def _clip(t: str, lim: int = 20000) -> str:
    return t if len(t) <= lim else t[:lim] + tr("clipped", n=len(t))

# ---------- 7: сетевой клиент (lm_client.LmClient) ----------
from lm_client import (LmClient as _LmClient, StreamCancelled, AuthError,  # type: ignore
                       AUTH_MODES, api_key_from_settings, base_url_from_settings,
                       backend_from_settings,
                       mcp_enabled_from_settings, mcp_servers_from_settings)

def LmClient(settings: dict | None = None) -> _LmClient:  # type: ignore
    # factory: endpoint/tr/logger берутся из настроек (backend->URL), ключ — из settings
    base = base_url_from_settings(settings or {})
    return _LmClient(f"{base}/models", f"{base}/chat/completions", base_url=base, timeout=TIMEOUT, tr=tr,
                     log=_log, api_key=api_key_from_settings(settings or {}))

# ---------- 1: хранилище чатов (repositories) ----------
from repositories import ChatRepository as _ChatRepository, SettingsRepository as _SettingsRepository  # type: ignore
from chat_store import (edit_user_text as _edit_user_text,
                        list_folders as _list_folders,
                        apply_folder_filter as _folder_filter)
from event_bus import EventBus  # noqa: F401 (задел под подписки UI на события)

_chat_repo = _ChatRepository(CHATS, INDEX_F)
_set_repo = _SettingsRepository(SET_F, DEFAULT_SETTINGS)

def load_index() -> list: return _chat_repo.load_index()
def save_index(idx): _chat_repo.save_index(idx)
def chat_path(cid): return _chat_repo.chat_path(cid)
def load_chat(cid) -> list: return _chat_repo.load_chat(cid)
def save_chat(cid, msgs): _chat_repo.save_chat(cid, msgs)
def load_settings() -> dict: return _set_repo.load()
def save_settings(s): _set_repo.save(s)

# ---------- 4: маппинг LLM API (api_payload.APIPayloadBuilder) ----------
from api_payload import (APIPayloadBuilder as _APIPayloadBuilder,  # type: ignore
                         estimate_tokens,
                         payload_has_images as _has_img_f,
                         payload_stats as _stats_f)

_payload = _APIPayloadBuilder(max_ctx_messages=MAX_CTX, extract_text=extract_text)

def build_api(messages: list[ChatMessage], system: str, context_tokens: int = 0,
              strip_images: bool = False) -> list[dict]:
    return _payload.build(messages, system, context_tokens, strip_images=strip_images)

def format_env_block(settings: dict) -> str:
    return _payload.format_env_block(settings)

def _without_mcp(settings: dict) -> dict:
    """Настройки с выключенным MCP — для фоновых вызовов (заголовок чата,
    сжатие истории). Им инструменты не нужны: /v1/responses без tools-блока
    работает как обычный chat, а привычный /v1/chat/completions остаётся
    нетронутым."""
    out = dict(settings or {})
    out["mcp_enabled"] = False
    return out

def effective_system(settings: dict, system: str) -> str:
    return _payload.effective_system(settings, system)

# ---------- UI ----------
async def main(page: ft.Page):
    setup_logging()
    settings = load_settings()
    th = THEMES[settings.get("theme", "dark")]
    fs = float(settings.get("font_scale", 1.0))
    CUR["lang"] = settings.get("lang", "ru") if settings.get("lang") in LANGS else "ru"
    try: _i18n.set_lang(CUR["lang"])
    except ValueError: pass
    _log.info("app started (lang=%s)", CUR["lang"])
    try:  # глушим известный shutdown-шум Windows-проактора (рваные keep-alive
        # сокеты при выгрузке/остановке сервера): это не ошибки программы
        loop0 = asyncio.get_running_loop()
        _prev_handler = loop0.get_exception_handler()

        def _quiet_shutdown_noise(loop, ctx):
            exc = ctx.get("exception")
            if isinstance(exc, (ConnectionResetError, ConnectionAbortedError)) and \
                    "_call_connection_lost" in str(ctx.get("message", "")):
                _log.debug("suppressed shutdown pipe noise: %r", exc)
                return
            if _prev_handler is not None:
                try: _prev_handler(loop, ctx)
                except Exception: pass
            else:
                try: loop.default_exception_handler(ctx)
                except Exception: pass

        loop0.set_exception_handler(_quiet_shutdown_noise)
    except Exception:
        pass
    UI = {}  # ссылки на контролы для apply_lang()
    page.title = tr("title"); page.bgcolor = th["bg"]
    try:  # восстановить геометрию окна с прошлого запуска
        if settings.get("window_maximized"):
            page.window.maximized = True
        else:
            page.window.width = max(600, int(settings.get("window_width", 1100) or 1100))
            page.window.height = max(500, int(settings.get("window_height", 860) or 860))
            if settings.get("window_left") is not None:
                page.window.left = int(settings["window_left"])
            if settings.get("window_top") is not None:
                page.window.top = int(settings["window_top"])
    except Exception as ex:
        _log.warning("restore window geometry failed: %s", ex)
    from chat_store import ChatStore
    client = LmClient(settings)  # ключ берётся из settings; клиент один на сессию
    store = ChatStore(_ChatRepository(CHATS, INDEX_F), _SettingsRepository(SET_F, DEFAULT_SETTINGS), _APIPayloadBuilder(), log=_log)

    # --- Event Subscription (TODO: подключить рендер при переписывании UI на события) ---
    # ViewBinder (views.py) пока работает через ChatStore.subscribe, поэтому
    # подписки EventBus отключены, чтобы не падать на несуществующем ChatViewRenderer.

    state = store.state  # единственное состояние
    state.setdefault("loaded_model", None)
    state.setdefault("model_touched", False)

    # --- виджеты ---
    chat_list = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, expand=True)
    search = ft.TextField(hint_text=tr("search"), dense=True,
                          prefix_icon=ft.Icons.SEARCH_OUTLINED, border_radius=S["radius"])
    folder_chips = ft.Row(spacing=4, wrap=True)  # №: фильтр по папкам
    chat_box = ft.Column(spacing=8, scroll=ft.ScrollMode.AUTO, expand=True, auto_scroll=True)
    status = ft.Text("", size=12, color=th["muted"])
    tok_label = ft.Text("", size=11, color=th["muted"])
    ctx_bar = ft.ProgressBar(width=110, value=0, bar_height=6,
                             color=th["accent"], bgcolor=th["border"])
    counter = ft.Text(f"0 / {S['input_max']}", size=11, color=th["muted"])
    err = ft.Container(visible=False, bgcolor="#B71C1C", border_radius=6, padding=10,
                       content=ft.Text("", color="white"))
    inp = ft.TextField(hint_text=tr("input_hint"), multiline=True, min_lines=1, max_lines=5,
                       expand=True, shift_enter=True)
    model_dd = ft.Dropdown(label=tr("model"), value=DEFAULT_MODEL, width=200,
                           options=[ft.DropdownOption(key=DEFAULT_MODEL, text=DEFAULT_MODEL)])
    dot = ft.Container(width=10, height=10, border_radius=5, bgcolor="#777")
    conn_t = ft.Text("—", size=11, color=th["muted"])
    sys_f = ft.TextField(label=tr("sys_prompt"), value=settings.get("system_prompt", ""), multiline=True, min_lines=2, max_lines=3)
    preset_dd = ft.Dropdown(label=tr("preset"), value=settings.get("preset", "Обычный"),
                            options=[ft.DropdownOption(key=k, text=preset_display(k)) for k in PRESETS])
    tmp = ft.Slider(min=0, max=2, divisions=40, value=settings.get("temperature", 0.7), expand=True)
    CTX_MIN, CTX_MAX_DEFAULT = 1024, 131072
    _ctx_init = min(CTX_MAX_DEFAULT, max(CTX_MIN, int(settings.get("context_length", 8192) or 8192)))
    ctxlen = ft.Slider(min=CTX_MIN, max=CTX_MAX_DEFAULT, divisions=127,
                       value=_ctx_init, expand=True)
    maxt = ft.TextField(label="max_tokens", value=str(settings.get("max_tokens", 2048)), width=120)
    seed = ft.TextField(label="seed (-1=off)", value=str(settings.get("seed", -1)), width=120)
    send_images_cb = ft.Checkbox(label=tr("vision_send"),
                                 value=bool(settings.get("send_images", True)))
    attach_row = ft.Row(spacing=8, wrap=True)
    clip = ft.Clipboard(); page.services.extend([ft.FilePicker(), clip])
    picker: ft.FilePicker = page.services[0]

    def persist():
        try: cl = int(float(ctxlen.value or 8192))
        except (ValueError, TypeError): cl = 8192
        cl = min(1000000, max(512, cl))
        settings.update(system_prompt=sys_f.value or "", temperature=float(tmp.value or 0.7),
            max_tokens=int(str(maxt.value or 2048)),
            seed=int(str(seed.value or -1)), preset=preset_dd.value, context_length=cl,
            send_images=bool(send_images_cb.value))
        # модель пишем только после реального выбора пользователем, иначе стартовый
        # open_chat()->persist() затрёт сохранённую модель плейсхолдером до load_models()
        if state.get("model_touched") and (model_dd.value or ""):
            settings["model"] = model_dd.value
        save_settings(settings)
    def show_e(t): err.content = ft.Text(t, color="white"); err.visible = True; page.update()
    def hide_e(): err.visible = False; page.update()
    def set_conn(ok, label=None):
        store.set_conn(ok, label)  # пишет в state + emit
        dot.bgcolor = "#4CAF50" if ok else "#EF5350"
        conn_t.value = label if label is not None else (tr("connected") if ok else tr("no_conn"))
        page.update()

    # --- (4) сайдбар: активный акцент + превью + счётчик ---
    def _load_preview_raw(cid) -> tuple[str, int]:
        """Старый путь: прочитать весь чат (используется только для миграции index)."""
        try:
            ms = load_chat(cid)
            if not ms: return "", 0
            last = next((m.text for m in reversed(ms) if (m.text or "").strip()), "")
            return (last or "").strip()[:42], len(ms)
        except Exception:
            return "", 0

    def _fmt_preview(raw: str, n: int) -> str:
        if n == 0: return tr("empty_chat")
        if not raw: return tr("only_files")
        return raw + "…" if len(raw) >= 42 else raw  # ровно 42 = обрезано при сохранении

    def refresh_sidebar():
        idx = load_index(); q = (search.value or "").lower()
        idx = _folder_filter(idx, state.get("folder_filter") or "")  # папки
        chat_list.controls.clear()
        # №4: закреплённые — первыми
        idx = sorted(idx, key=lambda c: (not c.get("pinned", False), -(c.get("ts", 0) or 0)))
        backfill = False
        for c in idx:
            if q and q not in c["title"].lower(): continue
            cid = c["id"]
            sel = cid == state["cid"]
            if "preview" not in c and "msg_count" not in c:
                # миграция старых записей index.json: один раз читаем файл и запоминаем
                c["preview"], c["msg_count"] = _load_preview_raw(cid)
                backfill = True
            prev = _fmt_preview(c.get("preview") or "", int(c.get("msg_count") or 0))
            n = int(c.get("msg_count") or 0)
            badge = ft.Container(content=ft.Text(str(n), size=10, color="white"),
                bgcolor=th["accent"], border_radius=8, padding=ft.Padding.symmetric(vertical=2, horizontal=6)) if n else ft.Container()
            pinned = bool(c.get("pinned", False))
            # Компактные кнопки действий: без ужатия 4 IconButton + дефолтный
            # spacing=10 Row съедали всю ширину карточки (260px сайдбара) и
            # Column(expand) схлопывался — заголовок шёл вертикально по буквам.
            _ib_style = ft.ButtonStyle(padding=ft.Padding.all(2),
                                       visual_density=ft.VisualDensity.COMPACT)
            def _ib(icon, tip, color, handler):
                # width/height — гарантированный компактный размер даже без M3:
                # иначе Material навязывает min 40 и строка снова переполняется
                return ft.IconButton(icon, icon_size=14, tooltip=tip,
                                     icon_color=color, style=_ib_style,
                                     width=28, height=28,
                                     on_click=handler)
            chat_list.controls.append(ft.Container(
                bgcolor=th["hover"] if sel else None,
                border_radius=S["radius"], padding=8,
                border=ft.Border.all(1, th["accent"]) if sel else ft.Border.all(1, th["border"]),
                on_click=lambda e, x=cid: open_chat(x),
                content=ft.Row([
                    ft.Column([ft.Text((("📌 " if pinned else "") + c["title"])[:30], size=13,
                                       weight=ft.FontWeight.BOLD if sel else ft.FontWeight.NORMAL,
                                       color=th["atc"],
                                       max_lines=1, overflow=ft.TextOverflow.ELLIPSIS),
                               ft.Text(prev, size=11, color=th["muted"],
                                       max_lines=1, overflow=ft.TextOverflow.ELLIPSIS)],
                              spacing=1, expand=True),
                    badge,
                    ft.Row([
                        _ib(ft.Icons.PUSH_PIN if pinned else ft.Icons.PUSH_PIN_OUTLINED,
                            tr("unpin") if pinned else tr("pin"),
                            th["accent"] if pinned else None,
                            lambda e, x=cid: toggle_pin(x)),
                        _ib(ft.Icons.FOLDER_OUTLINED, tr("folder"),
                            th["accent"] if c.get("folder") else None,
                            lambda e, x=cid: set_chat_folder(x)),
                        _ib(ft.Icons.EDIT_OUTLINED, tr("rename"), None,
                            lambda e, x=cid: rename_chat(x)),
                        _ib(ft.Icons.DELETE_OUTLINE, tr("delete"), None,
                            lambda e, x=cid: del_chat(x))],
                        spacing=0)],
                    spacing=6,
                    vertical_alignment=ft.CrossAxisAlignment.CENTER)))
        if backfill:
            try: save_index(idx)  # записали миграционные preview/msg_count
            except Exception: pass
        page.update()

    def refresh_folders():
        """Чипы папок над списком чатов: Все + уникальные folder из index."""
        cur = state.get("folder_filter") or ""
        folder_chips.controls.clear()

        def _chip(label: str, val: str, active: bool):
            return ft.Container(
                content=ft.Text(label, size=11,
                                color="white" if active else th["atc"]),
                bgcolor=th["accent"] if active else th["chip_bg"],
                padding=ft.Padding.symmetric(horizontal=8, vertical=3),
                border_radius=10,
                on_click=lambda e, v=val: set_folder_filter(v))

        folder_chips.controls.append(_chip(tr("folder_all"), "", cur == ""))
        for f in _list_folders(load_index()):
            folder_chips.controls.append(_chip(f, f, cur == f))
        try: folder_chips.update()
        except Exception: pass

    def set_folder_filter(v: str):
        store.set_folder_filter(v)
        refresh_folders(); refresh_sidebar()

    def toggle_pin(cid):
        store.toggle_pin(cid)
        refresh_sidebar()

    def set_chat_folder(cid):
        """Назначить/сменить/убрать папку чата (новое имя — поле, иначе список)."""
        folders = _list_folders(load_index())
        cur = next((c.get("folder", "") for c in load_index() if c.get("id") == cid), "")
        dd = ft.Dropdown(label=tr("folder"), value=cur or "", width=200,
                         options=[ft.DropdownOption(key="", text=tr("folder_none"))] +
                                 [ft.DropdownOption(key=f, text=f) for f in folders])
        new_f = ft.TextField(label=tr("folder_new"), hint_text=tr("folder_new_hint"))
        def close(e=None):
            try: page.pop_dialog()
            except Exception: pass
            page.update()
        def do_save(e=None):
            name = (new_f.value or "").strip() or (dd.value or "").strip()
            store.set_folder(cid, name or None)
            close(); refresh_folders(); refresh_sidebar()
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(tr("folder")), 
            content=ft.Column([dd, new_f], spacing=8, tight=True),
            actions=[ft.TextButton(tr("cancel"), on_click=close),
                     ft.TextButton(tr("save"), on_click=do_save)],
            actions_alignment=ft.MainAxisAlignment.END))

    async def auto_title(cid, first_text):
        """№4: фоном попросить модель коротко назвать чат."""
        await asyncio.sleep(0.5)
        try:
            if state.get("sending"):
                return
            prompt = tr("autotitle_prompt", t=(first_text or "")[:500])
            c, *_ = await client.chat_stream([{"role": "user", "content": prompt}],
                model_dd.value or DEFAULT_MODEL, _without_mcp(settings), lambda k, t: None)
            name = (c or "").strip().strip("\"'«»").split("\n")[0][:40].strip()
            if not name:
                return
            # не затирать ручное переименование: только если там ещё текст первого вопроса
            if store.auto_rename(cid, name, ((first_text or "")[:40],)):
                refresh_sidebar()
        except Exception as ex:
            _log.warning("auto_title failed: %s", ex)

    def new_chat():
        cid = store.new_chat(tr("me"))
        open_chat(cid)

    def open_chat(cid):
        persist()
        try: hide_e()  # не тащим красную плашку ошибку в другой чат
        except Exception: pass
        store.open_chat(cid)  # загрузка + scrub legacy-пустышек + reset фильтров/выбора
        state["files"] = []
        chat_box.controls.clear(); attach_row.controls.clear()
        for m in state["msgs"]: add_bubble(m)
        upd_tokens(); refresh_folders(); refresh_sidebar(); page.update()

    def del_chat(cid):
        was_cur = state["cid"] == cid
        store.delete_chat(cid)  # файл + запись index
        if was_cur:
            idx = load_index(); open_chat(idx[0]["id"]) if idx else new_chat(); return
        refresh_folders(); refresh_sidebar()

    def rename_chat(cid):
        cur = next((c.get("title", "") for c in load_index() if c.get("id") == cid), "")
        name_f = ft.TextField(label=tr("chat_name"), value=cur, autofocus=True)
        def do_save(e=None):
            new = (name_f.value or "").strip()[:60]
            if not new: show_e(tr("e_enter_name")); return
            store.rename_chat(cid, new)
            close_dlg(); refresh_sidebar()
        def close_dlg(e=None):
            try: page.pop_dialog()
            except Exception: pass
            page.update()
        dlg = ft.AlertDialog(title=ft.Text(tr("dlg_rename")),
            content=name_f, actions=[ft.TextButton(tr("cancel"), on_click=close_dlg),
            ft.TextButton(tr("save"), on_click=do_save)], actions_alignment=ft.MainAxisAlignment.END)
        page.show_dialog(dlg)

    # --- автопрокрутка вниз (со "прилипанием": не дёргаем, если юзер ушёл вверх) ---
    def scroll_end():
        async def _go():
            await asyncio.sleep(0.05)  # дать UI дорисовать новый контрол
            try: await chat_box.scroll_to(offset=999999, duration=200)
            except Exception: pass
        try: page.run_task(_go)
        except Exception:
            try: asyncio.get_running_loop().create_task(_go())
            except Exception: pass
    def on_chat_scroll(e):
        # e.pixels / e.max_scroll_extent могут отсутствовать — аккуратно
        try:
            px = getattr(e, "pixels", None); mx = getattr(e, "max_scroll_extent", None)
            state["stick"] = True if mx is None else (mx - (px or 0) < 120)
        except Exception: state["stick"] = True
    try: chat_box.on_scroll = on_chat_scroll
    except Exception: pass

    # --- #9 пузырь: аватар, время, reasoning-панель, edit/variants ---
    def add_bubble(m: ChatMessage):
        fs2 = fs; T = th
        av = ft.CircleAvatar(content=ft.Text("👤" if m.is_user else "🤖"), radius=14)
        tstr = time.strftime("%H:%M", time.localtime(m.ts or time.time()))
        md = ft.Markdown(m.text or "…", selectable=True, extension_set=ft.MarkdownExtensionSet.GITHUB_WEB,
            code_theme=ft.MarkdownCodeTheme.ATOM_ONE_DARK,
            code_style_sheet=ft.MarkdownStyleSheet(  # стили именно код-блоков (md_style_sheet их не касается)
                code_text_style=ft.TextStyle(color=T["atc"], size=int(13 * fs2)),
                codeblock_padding=8,
                codeblock_decoration=ft.BoxDecoration(
                    bgcolor=T["panel"], border=ft.Border.all(1, T["border"]),
                    border_radius=S["bubble_radius"])),
            md_style_sheet=ft.MarkdownStyleSheet(
                p_text_style=ft.TextStyle(color=T["utc"] if m.is_user else T["atc"], size=int(14 * fs2)),
                a_text_style=ft.TextStyle(color=T["utc"] if m.is_user else T["accent"],
                                          bgcolor="transparent",
                                          size=int(14 * fs2)),
                blockquote_padding=8,
                blockquote_decoration=ft.BoxDecoration(  # дефолт blue.shade100 нечитаем
                    bgcolor=T["panel"], border=ft.Border.all(1, T["border"]),
                    border_radius=S["bubble_radius"])))
        col = ft.Column(spacing=2, controls=[md])
        for a in m.attachments:
            if isinstance(a, Attachment) and a.mime and a.mime.startswith("image/") and a.b64:
                col.controls.insert(0, ft.Image(src=f"data:{a.mime};base64,{a.b64}", width=200, height=150, fit=ft.BoxFit.CONTAIN))
            elif isinstance(a, Attachment):
                col.controls.insert(0, ft.Text(f"📎 {Path(a.path).name}", size=12, color=T["utc"] if m.is_user else T["atc"]))
        # (2) Пузыри: заголовок + ~70% ширины через отступ + граница/тень
        sender = tr("you") if m.is_user else ("Strata" if str(settings.get("backend") or "") == "strata" else "LM Studio")
        head = ft.Row([ft.Text(sender, size=11, weight=ft.FontWeight.BOLD,
                               color=T["utc"] if m.is_user else T["accent"]),
                       ft.Text(tstr, size=10, color=T["muted"])], spacing=6)
        col = ft.Column(spacing=4, controls=[head] + col.controls)
        bubble = ft.Container(content=col, padding=S["pad"] + 2,
            expand=True,  # занять ширину flex-строки: Markdown получает границу и переносит текст
            border_radius=S["bubble_radius"],
            border=ft.Border.all(1, T["border"]) if not m.is_user else None,
            shadow=ft.BoxShadow(blur_radius=8, spread_radius=0, color=T["shadow"],
                                offset=ft.Offset(0, 2)) if settings.get("theme") == "light" and not m.is_user else None,
            bgcolor=T["ubg"] if m.is_user else T["abg"],
            margin=ft.Margin(left=S["bubble_margin"], right=0, top=0, bottom=0) if m.is_user
                   else ft.Margin(left=0, right=S["bubble_margin"], top=0, bottom=0))
        row = ft.Row([bubble, av] if m.is_user else [av, bubble],
                     alignment=ft.MainAxisAlignment.END if m.is_user else ft.MainAxisAlignment.START,
                     vertical_alignment=ft.CrossAxisAlignment.START)
        wrap = ft.Column(spacing=2, controls=[row])
        # действия
        acts = ft.Row(spacing=0)
        sel_set = state.get("selected") or set()  # №8: выбор сообщений для экспорта
        _sel_on = m.uid in sel_set
        sel_btn = ft.IconButton(ft.Icons.CHECK_BOX if _sel_on else ft.Icons.CHECK_BOX_OUTLINE_BLANK,
            icon_size=15, tooltip=tr("select"),
            icon_color=th["accent"] if _sel_on else None,
            on_click=lambda e: _toggle_sel(e))

        def _toggle_sel(e):
            on = store.toggle_select(m.uid)
            sel_btn.icon = ft.Icons.CHECK_BOX if on else ft.Icons.CHECK_BOX_OUTLINE_BLANK
            sel_btn.icon_color = th["accent"] if on else None
            try: sel_btn.update()
            except Exception: pass
        acts.controls.append(sel_btn)
        async def copy(e): await clip.set(m.text); status.value = tr("copied"); page.update()
        acts.controls.append(ft.IconButton(ft.Icons.COPY, icon_size=15, tooltip=tr("copy"), on_click=copy))
        async def speak_msg(e):
            try:
                from voice import speak, is_playing, stop_playback  # type: ignore
            except ImportError:
                show_e(tr("e_no_tts")); return
            if is_playing() or state.get("speaking"):  # повторный клик — стоп
                try: stop_playback()
                except Exception: pass
                state["speaking"] = False
                status.value = ""; status.color = th["muted"]; page.update(); return
            state["speaking"] = True
            loop = asyncio.get_running_loop()

            def _show(txt: str, color: str | None = None):
                def _apply():
                    status.value = txt
                    status.color = color or th["muted"]
                    try: page.update()
                    except Exception: pass
                try: loop.call_soon_threadsafe(_apply)
                except Exception: pass

            def _progress(stage: str, i: int, total: int):
                if stage == "prepare":
                    _show(tr("tts_preparing", i=i, n=total), th["accent"])
                else:
                    _show(tr("tts_playing", i=i, n=total), "#EF5350")

            _show(tr("tts_preparing", i=1, n=1), th["accent"])
            try:
                import functools as _ft
                await loop.run_in_executor(
                    None, _ft.partial(speak, m.text, CUR["lang"], _progress))
            except Exception as ex: show_e(voice_err(ex))
            finally:
                state["speaking"] = False
                status.value = ""; status.color = th["muted"]; page.update()
        acts.controls.append(ft.IconButton(ft.Icons.VOLUME_UP_OUTLINED, icon_size=15,
                                           tooltip=tr("tts_speak"), on_click=speak_msg))
        if m.is_user:
            def edit(e):
                """Диалог правки: сохранить → переотправить (старый ответ уходит в Variants)."""
                field = ft.TextField(label=tr("edit"), value=m.text, multiline=True,
                                     min_lines=3, max_lines=8, autofocus=True,
                                     max_length=S["input_max"] * 10)
                def close(ev=None):
                    try: page.pop_dialog()
                    except Exception: pass
                    page.update()
                def apply(ev=None):
                    new_t = (field.value or "").strip()
                    close()
                    if not new_t:
                        show_e(tr("e_empty_msg")); return
                    if new_t == (m.text or "").strip():
                        return
                    asyncio.create_task(_edit_resend(m, new_t))
                page.show_dialog(ft.AlertDialog(
                    title=ft.Text(tr("dlg_edit")), content=field,
                    actions=[ft.TextButton(tr("cancel"), on_click=close),
                             ft.TextButton(tr("save"), on_click=apply)],
                    actions_alignment=ft.MainAxisAlignment.END))
            async def dele(e):
                state["msgs"].remove(m); chat_box.controls.remove(wrap); save_chat(state["cid"], state["msgs"]); upd_tokens(); page.update()
            async def branch_u(e): await branch_from(_msg_index(m))  # №3: заново с этого места
            acts.controls += [ft.IconButton(ft.Icons.EDIT, icon_size=15, tooltip=tr("edit"), on_click=edit),
                              ft.IconButton(ft.Icons.DELETE, icon_size=15, tooltip=tr("delete"), on_click=dele),
                              ft.IconButton(ft.Icons.CALL_SPLIT, icon_size=15, tooltip=tr("branch"), on_click=branch_u)]
        else:
            async def regen(e): await regenerate()
            acts.controls.append(ft.IconButton(ft.Icons.REFRESH, icon_size=15, tooltip=tr("retry"), on_click=regen))
            try: _is_last = bool(state["msgs"]) and state["msgs"][-1] is m
            except Exception: _is_last = False
            if getattr(m, "stopped", False) and _is_last:  # №1: Продолжить прерванное
                async def cont(e): await continue_gen(m)
                acts.controls.append(ft.IconButton(ft.Icons.PLAY_ARROW, icon_size=15,
                                                   tooltip=tr("continue"), on_click=cont))
            async def branch_a(e): await branch_from(_msg_index(m))  # №3: ветвление
            acts.controls.append(ft.IconButton(ft.Icons.CALL_SPLIT, icon_size=15,
                                               tooltip=tr("branch"), on_click=branch_a))
            if "```" in (m.text or ""):  # копировать код из markdown-блоков
                async def copy_code(e):
                    parts = (m.text or "").split("```")
                    code = "\n\n".join(p.split("\n", 1)[1] if "\n" in p else p for p in parts[1::2])
                    await clip.set(code or m.text); status.value = tr("copied"); page.update()
                acts.controls.append(ft.IconButton(ft.Icons.CODE_OUTLINED, icon_size=15,
                                                   tooltip=tr("copy_code"), on_click=copy_code))
            fb_label = ft.Text("", size=11, color=T["muted"])
            def refresh_fb():
                fb_label.value = _format_feedback(m)
            refresh_fb()
            def set_fb(rating=None, ftype=None):
                async def _h(e):
                    # toggle off when clicking the same value again
                    if rating is not None:
                        m.rating = None if m.rating == rating else rating
                    if ftype is not None:
                        m.feedback_type = None if m.feedback_type == ftype else ftype
                    refresh_fb()
                    try: save_chat(state["cid"], state["msgs"])
                    except Exception: pass
                    fb_label.update()
                    page.update()
                return _h
            acts.controls += [
                ft.IconButton(ft.Icons.THUMB_UP_OUTLINED, icon_size=15, tooltip=tr("fb_helpful"),
                              on_click=set_fb(rating=5, ftype="helpful")),
                ft.IconButton(ft.Icons.THUMB_DOWN_OUTLINED, icon_size=15, tooltip=tr("fb_bad"),
                              on_click=set_fb(rating=2, ftype="inaccurate")),
                ft.IconButton(ft.Icons.FLAG_OUTLINED, icon_size=15, tooltip=tr("fb_harm"),
                              on_click=set_fb(rating=1, ftype="harmful")),
                fb_label,
            ]
            if m.variants:  # #4 переключатель вариантов
                vi = ft.Text(f'{tr("variants")}: {len(m.variants)+1}', size=11, color=T["muted"])
                async def prev_v(e):
                    if m.variants: m.text, m.variants[-1] = m.variants[-1], m.text
                    md.value = m.text; save_chat(state["cid"], state["msgs"]); page.update()
                acts.controls += [ft.IconButton(ft.Icons.ARROW_LEFT, icon_size=15, on_click=prev_v), vi]
        wrap.controls.append(acts)
        if not m.is_user and getattr(m, "gen_stats", None):
            wrap.controls.append(ft.Text(m.gen_stats, size=11, color=T["muted"]))
        chat_box.controls.append(wrap)
        if state.get("stick", True): scroll_end()
        return md

    def upd_tokens():  # view: токены + прогресс (математика — views.token_stats)
        try: cl = int(settings.get("context_length", 8192) or 8192)
        except (ValueError, TypeError): cl = 8192
        label, frac, _over = _token_stats(state["msgs"], cl, estimate_tokens)
        tok_label.value = f"{label} {tr('tokens')} · {len(state['msgs'])} msg"
        try:
            ctx_bar.value = frac
            ctx_bar.color = "#EF5350" if frac >= 0.9 else (th["accent"])
            ctx_bar.update()
        except Exception: pass
        try: page.update()
        except Exception: pass

    def _filtered_msgs():
        """Сообщения с учётом активных фильтров (поиск / только оценённые)."""
        return _visible_messages(state["msgs"], state.get("chat_filter", ""),
                                 state.get("rated_only", False))

    def render_all():
        """Перерисовать чат с учётом фильтров (поиск / только оценённые)."""
        chat_box.controls.clear()
        for m in _filtered_msgs():
            add_bubble(m)
        page.update()

    def heal_bubbles():
        """Инвариант: число пузырей == числу видимых сообщений.

        Если какой-то путь оставил осиротевший пузырь (или наоборот) —
        тихо перерисовываем из state, UI всегда производно от данных.
        """
        try:
            n_wraps = len(chat_box.controls)
            n_msgs = len(_filtered_msgs())
            if n_wraps != n_msgs:
                _log.warning("bubble/state mismatch: %d wraps vs %d msgs — re-render", n_wraps, n_msgs)
                render_all()
        except Exception as ex:
            _log.warning("heal_bubbles failed: %s", ex)

    async def _sync_ctx_max(sel: str, load_info: dict | None = None):
        """Подтянуть max ползунка Context Length с сервера (best effort)."""
        try:  # максимум ползунка = лимит контекста загруженной модели с сервера
            import httpx as _hx

            def _pick_max(obj) -> int | None:
                if isinstance(obj, dict):
                    # прямые ключи + вложенные meta/info/params
                    for k in ("max_context_length", "max_context", "context_length",
                              "n_ctx", "n_ctx_train", "contextLength"):
                        v = obj.get(k)
                        if isinstance(v, (int, float)) and v >= 1024:
                            return int(v)
                    for k in ("meta", "info", "params", "model", "data"):
                        v = _pick_max(obj.get(k))
                        if v:
                            return v
                elif isinstance(obj, list):
                    for it in obj:
                        v = _pick_max(it)
                        if v:
                            return v
                return None

            found: int | None = _pick_max(load_info or {})
            if not found:
                # сервер /v1/models обычно отдаёт только id — пробуем расширенные
                # эндпоинты LM Studio. Пути строим ТОЛЬКО от корня сервера:
                # base уже содержит /v1, склейка давала бы мусор вида
                # GET /v1/api/v1/models → ERROR в логе LM Studio.
                # Корень берём из настроек (бэкенд), а не из модульной константы,
                # иначе после переключения на Strata опрашивали бы LM Studio.
                _base = base_url_from_settings(settings).rstrip("/")
                root = _base[:-3] if _base.endswith("/v1") else _base
                # оба пути проверены по реальному логу сервера (200 без ERROR);
                # /api/v0/models — legacy-фолбэк, трогается только если не нашли
                # Strata: n_ctx сидит в meta /v1/models — его разберём ниже,
                # LM-пути для strata пропускаем.
                _is_strata = backend_from_settings(settings) == "strata"
                paths = ["/v1/models"] if _is_strata else ["/v1/models", "/api/v1/models", "/api/v0/models"]
                async with _hx.AsyncClient(timeout=15) as _c:
                    for p in paths:
                        if found:
                            break
                        try:
                            r = await _c.get(root + p)
                            r.raise_for_status()
                            j = r.json()
                            cands = []
                            if isinstance(j, dict):
                                # /api/v1/models -> {"models": [{key, max_context_length...}]}
                                entries = j.get("models") or j.get("data") or []
                                for m in entries:
                                    if not isinstance(m, dict):
                                        continue
                                    ids = {m.get("id"), m.get("key"), m.get("display_name"),
                                           m.get("name")}
                                    if sel in ids or None in ids and m.get("id") is None:
                                        cands.append(m)
                                if not cands and isinstance(j.get("data"), list):
                                    cands = [x for x in j["data"] if isinstance(x, dict)]
                                cands.append(j)
                            elif isinstance(j, list):
                                cands = [x for x in j if isinstance(x, dict)]
                            for c in cands:
                                # точное совпадение id/key — приоритет
                                cids = {c.get("id"), c.get("key"), c.get("display_name"),
                                        c.get("name")}
                                if sel not in cids and (c.get("id") is not None
                                                        or c.get("key") is not None):
                                    continue
                                found = _pick_max(c)
                                if found:
                                    break
                            if found:
                                break
                        except Exception:
                            continue
            if found:
                found = min(1000000, found)
                ctxlen.max = float(found)
                if float(ctxlen.value or 0) > ctxlen.max:
                    ctxlen.value = ctxlen.max
                ctxlen_val.value = f"{int(float(ctxlen.value or 0))}"
                ctxlen.update(); ctxlen_val.update(); persist()
                _log.info("ctx slider max set to %s for %s", found, sel)
            else:
                # лимит не отдал сервер — не занижаем: расширяем до 1M, чтобы
                # модели на 262k+ можно было выставить вручную
                if float(ctxlen.max or 0) < 1000000:
                    ctxlen.max = float(1000000)
                    try: ctxlen.update()
                    except Exception: pass
                _log.warning("ctx max not advertised for %s (load_info keys=%s)",
                             sel, list((load_info or {}).keys()) if isinstance(load_info, dict) else type(load_info))
        except Exception as ex:
            _log.warning("ctx max lookup failed: %s", ex)

    async def health_loop():
        """№10: тихий ping сервера каждые 30 c — точка статуса краснеет при падении."""
        await asyncio.sleep(5)  # дать старту завершиться
        while True:
            await asyncio.sleep(30)
            try:
                try:
                    await client.fetch_models()
                    ok = True
                except AuthError as ex:
                    # протухший/отклонённый ключ — это не «сервер недоступен»:
                    # показываем локализованную причину с HTTP-кодом (401/403)
                    ok = False
                    reason = str(ex)
                except Exception:
                    ok = False
                    reason = tr("server_down")
                try:
                    if ok:
                        if not state.get("conn_ok"):
                            set_conn(True)
                        else:
                            dot.bgcolor = "#4CAF50"; dot.update()
                    else:
                        set_conn(False, reason)
                except Exception:
                    pass
            except Exception:
                pass

    async def restart_server(e=None):
        """№10: перезапустить сервер LM Studio через lms и обновить модели."""
        import shutil
        lms = shutil.which("lms")
        if not lms:
            show_e(tr("e_no_lms")); return
        status.value = tr("server_starting"); page.update()
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, lambda: __import__("subprocess").run(
                    [lms, "server", "start"], timeout=30,
                    capture_output=True, text=True))
        except Exception as ex:
            show_e(str(ex)); return
        await load_models()

    def _remember_loaded(be: str, model: str):
        """Запомнить загруженную модель отдельно для каждого бэкенда."""
        try:
            settings[f"loaded_model_{be}"] = model
            save_settings(settings)
        except Exception as ex:
            _log.warning("remember loaded failed: %s", ex)

    async def load_models(e=None):
        try: models = await client.fetch_models()
        except RuntimeError as ex: _log.warning("fetch_models failed: %s", ex); show_e(str(ex)); set_conn(False); return
        if not models: models = [DEFAULT_MODEL]
        model_dd.options = [ft.DropdownOption(k, k) for k in models]
        _be = backend_from_settings(settings)
        # предпочтение — модели этого бэкенда (запомнили при прошлом визите),
        # иначе общая последняя выбранная
        saved = settings.get(f"loaded_model_{_be}") or settings.get("model") or ""
        # NEW: сервер уже держит модель в памяти? — используем её, вторую не грузим.
        # loaded_models() смотрит только эндпоинты с явным loaded-state
        # (GET /api/v1/models -> loaded_instances), каталог не в счёт.
        # Пусто = ничего не загружено (или сервер не отдал) — грузим сохранённую.
        try:
            already = await client.loaded_models(backend_from_settings(settings))
        except Exception as ex:
            _log.warning("loaded_models lookup failed: %s", ex)
            already = []
        if already:
            pick = saved if saved in already else None
            if pick is None:
                for cand in already:  # key из API может отличаться написанием от id в /v1/models
                    if cand in [o.key for o in model_dd.options]:
                        pick = cand
                        break
            if pick is None:
                pick = already[0]
            if pick not in [o.key for o in model_dd.options]:
                model_dd.options = [ft.DropdownOption(pick, pick)] + model_dd.options
            model_dd.value = pick
            try: model_dd.update()
            except Exception: pass
            state["models_n"] = len(models)
            state["loaded_model"] = pick
            state["model_touched"] = True
            _remember_loaded(backend_from_settings(settings), pick)
            persist()  # запомнить подхваченную модель как текущую
            set_conn(True, tr("models_n", n=len(models))); page.update()
            _log.info("adopted already-loaded model on server: %s (server holds: %s)", pick, already)
            status.value = tr("model_loaded", m=pick); page.update()
            await _sync_ctx_max(pick, {})
            return
        model_dd.value = saved if saved in models else models[0]
        try: model_dd.update()
        except Exception: pass
        state["models_n"] = len(models)
        set_conn(True, tr("models_n", n=len(models))); page.update()
        await ensure_model_loaded()

    async def ensure_model_loaded():
        """Загрузить выбранную модель на сервере, выгрузить предыдущую."""
        sel = model_dd.value or DEFAULT_MODEL
        if sel == state.get("loaded_model"): return
        prev = state.get("loaded_model")
        status.value = tr("model_loading", m=sel); page.update()
        if prev and prev != sel:
            try:
                await client.unload_model(prev, backend_from_settings(settings))  # сначала выгрузить текущую…
                _log.info("unloaded previous model: %s", prev)
            except Exception as ex:
                _log.warning("unload_model(%s) failed: %s", prev, ex)
            state["loaded_model"] = None
        load_info: dict = {}
        try:
            load_info = await client.load_model(sel, settings.get("context_length"),
                                                  backend_from_settings(settings)) or {}  # …только потом грузить новую
        except Exception as ex:
            _log.error("load_model(%s) failed: %s", sel, ex)
            show_e(tr("e_load_model", m=sel, e=ex))
            return
        state["loaded_model"] = sel
        state["model_touched"] = True
        _remember_loaded(backend_from_settings(settings), sel)
        persist()  # сразу запомнить выбор (и для этого бэкенда отдельно)
        await _sync_ctx_max(sel, load_info)
        try:
            if store is not None: store._emit("model:loaded")
        except Exception: pass
        status.value = tr("model_loaded", m=sel); page.update()

    async def on_model_change(e=None):
        state["model_touched"] = True
        persist()  # сразу запомнить выбор
        await ensure_model_loaded()

    # --- автоопределение vision: поведенческая память (settings['no_vision_models']) ---
    def _vision_known_bad(model_id: str) -> bool:
        try: return model_id in (settings.get("no_vision_models") or [])
        except Exception: return False

    def _vision_mark_bad(model_id: str):
        lst = [m for m in (settings.get("no_vision_models") or []) if m != model_id]
        lst.append(model_id)
        settings["no_vision_models"] = lst[-50:]  # кап списка
        save_settings(settings)
        _log.info("model marked as no-vision: %s", model_id)

    def _vision_mark_good(model_id: str):
        lst = [m for m in (settings.get("no_vision_models") or []) if m != model_id]
        if len(lst) != len(settings.get("no_vision_models") or []):
            settings["no_vision_models"] = lst
            save_settings(settings)
            _log.info("model proved vision-capable, unmarked: %s", model_id)

    async def confirm_image_retry() -> bool:
        """Модель без vision отклонила image_url (HTTP 400): повторить без картинок?"""
        fut = asyncio.get_event_loop().create_future()
        def close(v):
            try: page.pop_dialog()
            except Exception: pass
            if not fut.done(): fut.set_result(v)
            page.update()
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(tr("dlg_novision_t")),
            content=ft.Text(tr("dlg_novision_c")),
            actions=[ft.TextButton(tr("retry_noimg"), on_click=lambda e: close(True)),
                     ft.TextButton(tr("cancel"), on_click=lambda e: close(False))],
            actions_alignment=ft.MainAxisAlignment.END))
        return await fut

    async def generate(_strip_images: bool = False, _cont=None):
        """Генерация ответа. _cont — прерванное сообщение для «Продолжить»."""
        _model_id = model_dd.value or DEFAULT_MODEL
        if not settings.get("send_images", True):
            _strip_images = True  # vision выключен в настройках — всегда text-only
        elif not _strip_images and _vision_known_bad(_model_id):
            _strip_images = True  # авто: модель ранее роняла 400 на картинках — без диалога
            _log.info("auto-stripping images for known no-vision model: %s", _model_id)
        try: api = build_api(state["msgs"], effective_system(settings, settings.get("system_prompt", "")),
                             int(settings.get("context_length", 8192) or 0),
                             strip_images=_strip_images)
        except FileError as ex: show_e(str(ex)); return
        st = _stats_f(api)
        _log.info("request model=%s msgs=%s images=%s stripped=%s",
                  model_dd.value or DEFAULT_MODEL, st["messages"], st["images"], _strip_images)
        if _strip_images and st["images"]:
            status.value = (tr("vision_off")
                            if not settings.get("send_images", True)
                            else tr("vision_auto"))
            status.update()
        am = ChatMessage(text="", is_user=False); state["msgs"].append(am)
        md = None
        if _cont is None:
            md = add_bubble(am)
            # --- визуализация ожидания: спиннер-статус + плейсхолдер в пузыре ---
            md.value = tr("generating")
            disp = ""
        else:  # «Продолжить»: дописываем в то же сообщение
            am = _cont
            am.stopped = False
            chat_box.controls.clear()  # пересобрать ради живого md-хендла
            for mm in state["msgs"]:
                d = add_bubble(mm)
                if mm is am:
                    md = d
            if md is None:
                return
            disp = am.text or ""
            md.value = disp or tr("typing")
        status.value = tr("generating"); status.color = th["accent"]; page.update()
        buf, last = [], time.time()
        t0 = time.time()
        _cid0 = state["cid"]  # чат могли переключить mid-stream — тогда тихо выходим
        _first_tok = True
        t_first: float | None = None
        def on_delta(kind, tok):
            nonlocal disp, last, _first_tok, t_first
            if state["cid"] != _cid0 or am not in state["msgs"]:
                return  # чат сменили — чужой стрим не трогаем
            if _first_tok:
                _first_tok = False
                t_first = time.time()
                try:
                    status.value = tr("typing"); page.update()
                except Exception: pass
            buf.append(tok if kind == "content" else f"`{tok}`")
            if time.time() - last > 0.15:  # #9 троттлинг по времени
                disp += "".join(buf); buf.clear()
                try:
                    md.value = disp; md.update()
                except Exception:
                    pass  # пузырь уже не на странице (смена чата) — копим в disp
                if state.get("stick", True): scroll_end()
                last = time.time()
        _tools: list[str] = []  # вызовы MCP за этот ответ — нужны для фолбэка
        _tool_rows: dict[str, ft.Text] = {}  # tool -> строка вызова в чате
        _tools_col: ft.Column | None = None
        def _tool_line(tool: str) -> ft.Text | None:
            """Компактная строка вызова инструмента прямо над ответом.

            Строки живут только на время генерации (в историю чата не пишутся),
            поэтому при смене чата/пересборке они просто исчезают.
            """
            nonlocal _tools_col
            if state["cid"] != _cid0:
                return None
            if _tools_col is None:
                _tools_col = ft.Column(spacing=2, tight=True)
                try:  # вставляем прямо над пузырём ответа
                    i = chat_box.controls.index(md) if md in chat_box.controls else len(chat_box.controls)
                    chat_box.controls.insert(i, _tools_col)
                except Exception:
                    chat_box.controls.append(_tools_col)
            row = _tool_rows.get(tool)
            if row is None:
                row = ft.Text("", size=11, color=th["muted"], selectable=True, no_wrap=True)
                _tool_rows[tool] = row
                _tools_col.controls.append(row)
            return row
        def _set_tool_line(tool: str, text: str, color: str = ""):
            row = _tool_line(tool)
            if row is None:
                return
            row.value = text
            if color:
                row.color = color
            try: page.update()
            except Exception: pass  # чат могли переключить/закрыть
        def on_mcp_event(name, data):
            """События MCP: вызовы инструментов — компактной строкой в чате
            (имя + аргументы/результат), ошибка — плашкой над чатом."""
            tool = str(data.get("tool") or "")
            if name == "tool_start":
                _tools.append(tool or "?")
                args = str(data.get("args") or "").replace("\n", " ")
                _set_tool_line(tool, tr("mcp_tool_call", t=tool, a=args))
            elif name == "tool_done":
                _set_tool_line(tool, tr("mcp_tool_result", t=tool,
                                        r=str(data.get("output") or "").replace("\n", " ")),
                               th["muted"])
            elif name == "tool_failed":
                _set_tool_line(tool, tr("mcp_tool_failed", t=tool,
                                        r=str(data.get("reason") or "").replace("\n", " ")),
                               "#EF5350")
            elif name == "error":
                if state["cid"] == _cid0:
                    show_e(tr("mcp_error", t=data.get("message", "")))
                return
            elif state["cid"] != _cid0:
                return
            status.value = (tr("mcp_tool", t=tool) if tool else tr("mcp_active_hint"))
            status.color = th["accent"]
            try: page.update()
            except Exception: pass  # чат могли переключить/закрыть
        try:
            res = await client.chat_stream(api, model_dd.value or DEFAULT_MODEL, settings,
                                           on_delta, on_event=on_mcp_event)
            if state["cid"] != _cid0 or am not in state["msgs"]:
                _log.info("stream finished for inactive chat — dropped")
                return
            content, reasoning = res[0], res[1]
            usage = res[2] if len(res) > 2 and isinstance(res[2], dict) else {}
            disp += "".join(buf)
            # в режиме «Продолжить» disp уже содержит старый хвост + дописку
            if _cont is not None:
                am.text = disp or am.text
            else:
                am.text = content or reasoning or disp
            if not (am.text or "").strip() and _tools:
                # были вызовы инструментов, а текста нет: пузырь не убираем —
                # иначе пользователь не видит, что модель вообще делала
                am.text = tr("mcp_no_text", n=len(_tools))
            if not (am.text or "").strip():
                # №3: модель вернула пустое — пузырь «…» не оставляем
                try:
                    state["msgs"].remove(am)
                except ValueError:
                    pass
                try:
                    chat_box.controls.pop()
                except Exception:
                    pass
                save_chat(state["cid"], state["msgs"])
                _log.warning("empty model response dropped (cont=%s)", _cont is not None)
                status.value = tr("empty_response"); status.color = th["muted"]; page.update()
                scroll_end()
                return
            am.stopped = False
            md.value = am.text
            if not _strip_images and _has_img_f(api):
                _vision_mark_good(_model_id)  # картинки прошли — модель с vision
            # --- статистика генерации: токены + скорость ---
            try:
                t_end = time.time()
                gen_tok = usage.get("completion_tokens")
                if not isinstance(gen_tok, (int, float)) or gen_tok <= 0:
                    try:
                        gen_tok = estimate_tokens(am.text or "")
                    except Exception:
                        gen_tok = 0
                span = max(0.01, t_end - (t_first or t0))
                am.gen_stats = f"⚡ {int(gen_tok)} tok · {gen_tok / span:.1f} tok/s · {t_end - t0:.1f}s"
                try:
                    wrap_now = chat_box.controls[-1] if chat_box.controls else None
                    if wrap_now is not None:
                        wrap_now.controls.append(ft.Text(am.gen_stats, size=11, color=th["muted"]))
                        wrap_now.update()
                except Exception: pass
            except Exception as ex:
                _log.warning("gen stats failed: %s", ex)
                am.gen_stats = None
            save_chat(state["cid"], state["msgs"]); upd_tokens()
            status.value = am.gen_stats or ""; status.color = th["muted"]; page.update()
            scroll_end()
        except StreamCancelled:
            if state["cid"] != _cid0 or am not in state["msgs"]:
                return  # устаревшая задача из прошлого чата
            partial = (md.value or "").strip()
            # плейсхолдеры ожидания — не текст, продолжать нечего
            if partial in (tr("generating"), tr("typing"), "…", "...", ""):
                partial = ""
            if not partial:
                # №1: пустое сообщение не оставляем — убираем пузырь совсем
                try:
                    state["msgs"].remove(am)
                except ValueError:
                    pass
                render_all()
                save_chat(state["cid"], state["msgs"])
                _log.info("stop with no text — empty message dropped")
                status.value = tr("stopped"); status.color = th["muted"]; page.update()
                scroll_end()
            else:
                am.text = partial
                am.stopped = True  # №1: можно «Продолжить»
                save_chat(state["cid"], state["msgs"])
                _log.info("stopped with %d chars — continue available", len(partial))
                status.value = tr("stopped"); status.color = th["muted"]
                render_all()  # перерисовать: у пузыря появится кнопка Продолжить
                page.update()
                scroll_end()
        except RuntimeError as ex:
            _log.error("generate failed: %s", ex)
            state["msgs"].pop(); chat_box.controls.pop()
            if "400" in str(ex) and not _strip_images and _has_img_f(api):
                _log.warning("HTTP 400 with images in payload — offering text-only retry")
                if await confirm_image_retry():
                    _vision_mark_bad(_model_id)  # запомнить: без vision, дальше — автострип
                    await generate(_strip_images=True)
                    return
            show_e(str(ex))

    async def send(e=None):
        if state["sending"]: return
        txt = (inp.value or "").strip()
        if not txt and not state["files"]: return
        persist(); hide_e()
        # контроль переполнения контекста: предложить сжатие
        try: cl = int(settings.get("context_length", 8192) or 8192)
        except (ValueError, TypeError): cl = 8192
        cur = sum(estimate_tokens(m.text or "") for m in state["msgs"]) + estimate_tokens(txt)
        if cur > cl:
            go = await confirm_ctx_overflow(cur, cl)
            if go == "cancel": return
            if go == "compress":
                await summarize()
                cur = sum(estimate_tokens(m.text or "") for m in state["msgs"]) + estimate_tokens(txt)
                if cur > cl: show_e(f"~{cur} / {cl} {tr('tokens')} — {tr('ctx_still_over')}"); return
        state["sending"] = True; set_sending_ui(True); page.update()
        try:
            um = ChatMessage(text=txt, is_user=True)
            for p in state["files"]:
                mime, _ = mimetypes.guess_type(p)
                if mime and mime.startswith("image/"):
                    b64, mt = encode_image(p); um.attachments.append(Attachment(path=p, mime=mt, b64=b64))
                else: um.attachments.append(Attachment(path=p))  # текст извлекается в build_api (#2)
            state["msgs"].append(um); add_bubble(um)
            inp.value = ""; counter.value = f"0 / {S['input_max']}"; state["files"] = []; attach_row.controls.clear()
            # автоназвание чата (#1) — только пока заголовок дефолтный
            if store.auto_rename(state["cid"], txt[:40],
                                 (LANGS["ru"]["me"], LANGS["en"]["me"])):
                refresh_sidebar()
            save_chat(state["cid"], state["msgs"]); upd_tokens(); page.update()
            await generate()
            # №4: автоназвание чата моделью (фоном, не затирает ручное)
            try:
                cur_title = next((c["title"] for c in load_index() if c["id"] == state["cid"]), "")
                if cur_title == txt[:40]:
                    asyncio.create_task(auto_title(state["cid"], txt))
            except Exception:
                pass
        finally: state["sending"] = False; set_sending_ui(False); heal_bubbles(); page.update()

    def _msg_index(m) -> int:
        for i, x in enumerate(state["msgs"]):
            if x is m:
                return i
        return -1

    async def _edit_resend(m, new_text: str):
        """Редактирование своего сообщения + переотправка.

        Текст заменяется, всё после сообщения отбрасывается (старые ответы
        ассистента уходят в Variants нового ответа — как в branch_from).
        """
        if state["sending"]: show_e(tr("e_busy")); return
        res = _edit_user_text(state["msgs"], m.uid, new_text)
        if res is None: return
        chat_box.controls.clear()
        for x in state["msgs"]: add_bubble(x)
        save_chat(state["cid"], state["msgs"]); upd_tokens(); page.update()
        state["sending"] = True; set_sending_ui(True)
        try:
            _idx, variants = res
            prev_n = len(state["msgs"])
            await generate()
            if len(state["msgs"]) > prev_n and variants:
                state["msgs"][-1].variants = variants + state["msgs"][-1].variants[:5]
            save_chat(state["cid"], state["msgs"])
            _log.info("edit-resend at #%d: %d old reply text(s) kept as variants",
                      _idx, len(variants))
        finally:
            state["sending"] = False; set_sending_ui(False); heal_bubbles(); page.update()

    async def continue_gen(m):
        """№1: дописать прерванный Stop'ом ответ."""
        if state["sending"]: return
        if _msg_index(m) < 0: return
        state["sending"] = True; set_sending_ui(True); page.update()
        try:
            await generate(_cont=m)
        finally: state["sending"] = False; set_sending_ui(False); heal_bubbles(); page.update()

    async def branch_from(idx: int):
        """№3: ветвление — отбросить всё после msgs[idx] и сгенерировать заново.

        Отброшенный хвост сохраняется как варианты (Variants) нового ответа.
        Если ветка от ответа ассистента — сам ответ тоже уходит в хвост:
        история должна заканчиваться вопросом пользователя, иначе модель
        возвращает пустое (так и было на скрине).
        """
        if state["sending"]: return
        if idx < 0 or idx >= len(state["msgs"]): return
        cut = idx if not state["msgs"][idx].is_user else idx + 1
        tail = state["msgs"][cut:]
        tail_texts = [o.text for o in tail if not o.is_user and (o.text or "").strip()][:5]
        del state["msgs"][cut:]
        chat_box.controls.clear()
        for m in state["msgs"]: add_bubble(m)
        page.update(); state["sending"] = True; set_sending_ui(True)
        try:
            prev_n = len(state["msgs"])
            await generate()
            if len(state["msgs"]) > prev_n and tail_texts:
                state["msgs"][-1].variants = tail_texts + state["msgs"][-1].variants[:5]
                save_chat(state["cid"], state["msgs"])
        finally: state["sending"] = False; set_sending_ui(False); heal_bubbles(); page.update()

    async def regenerate(e=None):
        if state["sending"]: return
        idx = max((i for i, m in enumerate(state["msgs"]) if m.is_user), default=-1)
        if idx < 0: show_e(tr("e_no_msgs")); return
        old = state["msgs"][idx+1:]
        for o in old:  # сохранить прошлый ответ как вариант (#4)
            if not o.is_user and o.text: pass
        if old and not old[-1].is_user and old[-1].text:
            pass
        del state["msgs"][idx+1:]
        chat_box.controls.clear()
        for m in state["msgs"]: add_bubble(m)
        page.update(); state["sending"] = True; set_sending_ui(True)
        try:
            prev_n = len(state["msgs"])
            await generate()
            if len(state["msgs"]) > prev_n and old and not old[-1].is_user:
                state["msgs"][-1].variants = [o.text for o in old if not o.is_user and o.text][:5] + state["msgs"][-1].variants[:5]
                save_chat(state["cid"], state["msgs"])
        finally: state["sending"] = False; set_sending_ui(False); heal_bubbles(); page.update()

    async def summarize(e=None):  # #3 сжатие
        if len(state["msgs"]) < 4: show_e(tr("e_few_msgs")); return
        keep = state["msgs"][-4:]; old = state["msgs"][:-4]
        txt = "\n".join(f"{'U' if m.is_user else 'A'}: {m.text[:500]}" for m in old)
        try:
            c, *_ = await client.chat_stream([{"role": "user", "content": tr("summary_of", t=txt[:8000])}],
                model_dd.value or DEFAULT_MODEL, _without_mcp(settings), lambda k, t: None)
            state["msgs"] = [ChatMessage(text=tr("summary_hist", c=c), is_user=False)] + keep
            chat_box.controls.clear()
            for m in state["msgs"]: add_bubble(m)
            save_chat(state["cid"], state["msgs"]); upd_tokens(); page.update()
        except Exception as ex: show_e(str(ex))

    def md_to_html(src: str) -> str:
        """Лёгкий markdown→HTML для экспорта (без внешних зависимостей).

        Понимает: заголовки, bold/italic, код-блоки и инлайн-код,
        списки (-/* и 1.), цитаты, hr, ссылки, абзацы.
        """
        import re as _re
        esc = html_mod.escape(src or "")
        # 1) фenced-код блоки -> плейсхолдеры
        codes: list[str] = []

        def _code_sub(m):
            codes.append(f"<pre><code>{m.group(1)}</code></pre>")
            return f"\x00CODE{len(codes) - 1}\x00"

        esc = _re.sub(r"```(?:\w+)?\n?(.*?)```", _code_sub, esc, flags=_re.S)

        def _inline(t: str) -> str:
            t = _re.sub(r"`([^`]+?)`", r"<code>\1</code>", t)
            t = _re.sub(r"!\[([^\]]*?)\]\([^)]*?\)", r"\1", t)  # картинки -> alt
            t = _re.sub(r"\[([^\]]+?)\]\(([^)]+?)\)", r'<a href="\2">\1</a>', t)
            t = _re.sub(r"\*\*([^*]+?)\*\*", r"<strong>\1</strong>", t)
            t = _re.sub(r"(?<!\w)__([^_]+?)__(?!\w)", r"<strong>\1</strong>", t)
            t = _re.sub(r"(?<!\w)\*([^*\n]+?)\*(?!\w)", r"<em>\1</em>", t)
            t = _re.sub(r"(?<!\w)_([^_\n]+?)_(?!\w)", r"<em>\1</em>", t)
            return t

        out: list[str] = []
        lines = esc.split("\n")
        i, n = 0, len(lines)
        while i < n:
            ln = lines[i].rstrip()
            s = ln.strip()
            if not s:
                i += 1
                continue
            if s.startswith("\x00CODE") and s.endswith("\x00"):
                out.append(s)
                i += 1
                continue
            mh = _re.match(r"^(#{1,6})\s+(.*)$", s)
            if mh:
                lvl = len(mh.group(1))
                out.append(f"<h{lvl}>{_inline(mh.group(2))}</h{lvl}>")
                i += 1
                continue
            if _re.match(r"^---+$", s) or _re.match(r"^\*\*\*+$", s):
                out.append("<hr>")
                i += 1
                continue
            if s.startswith("&gt;") or s.startswith(">"):
                qs: list[str] = []
                while i < n and (lines[i].strip().startswith("&gt;") or lines[i].strip().startswith(">")):
                    qs.append(_re.sub(r"^(&gt;|&gt; |>|>)\s?", "", lines[i].strip()))
                    i += 1
                out.append(f"<blockquote>{'<br>'.join(_inline(q) for q in qs)}</blockquote>")
                continue
            if _re.match(r"^([-*•])\s+", s):
                items: list[str] = []
                while i < n and _re.match(r"^([-*•])\s+", lines[i].strip()):
                    items.append(_inline(_re.sub(r"^([-*•])\s+", "", lines[i].strip())))
                    i += 1
                out.append("<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>")
                continue
            if _re.match(r"^\d+[.)]\s+", s):
                items = []
                while i < n and _re.match(r"^\d+[.)]\s+", lines[i].strip()):
                    items.append(_inline(_re.sub(r"^\d+[.)]\s+", "", lines[i].strip())))
                    i += 1
                out.append("<ol>" + "".join(f"<li>{x}</li>" for x in items) + "</ol>")
                continue
            # обычный абзац: клеим до пустой строки
            para: list[str] = [s]
            i += 1
            while i < n and lines[i].strip() and not _re.match(
                    r"^(#{1,6}\s|---+$|\*\*\*+$|&gt;|>|([-*•])\s+|\d+[.)]\s+|```)", lines[i].strip()):
                para.append(lines[i].strip())
                i += 1
            out.append(f"<p>{'<br>'.join(_inline(p) for p in para)}</p>")
        html = "\n".join(out)
        for idx, code in enumerate(codes):  # вернуть код-блоки
            html = html.replace(f"\x00CODE{idx}\x00", code)
        return html or "<p>…</p>"

    def export(fmt):  # #5
        export_msgs(state["msgs"], fmt, state["cid"])

    def export_msgs(ms, fmt, tag):
        """Экспорт произвольного набора сообщений (весь чат или выбранные №8)."""
        if not ms:
            show_e(tr("e_no_msgs")); return
        if fmt == "md":
            out = "\n\n".join(f"**{tr('you') if m.is_user else tr('assistant')}** ({time.strftime('%H:%M', time.localtime(m.ts))}):\n{m.text}" for m in ms)
            (DATA / f"chat_{tag}.md").write_text(out, "utf-8")
        elif fmt == "html":
            css = ("body{background:#121212;color:#e8e8e8;font-family:Segoe UI,Arial,sans-serif;"
                   "max-width:900px;margin:0 auto;padding:24px}h1,h2,h3{color:#fff}"
                   ".msg{border:1px solid #333;border-radius:12px;padding:12px 16px;margin:12px 0}"
                   ".user{background:#1b3a5c}.assistant{background:#1e1e1e}"
                   ".head{font-size:12px;color:#9e9e9e;margin-bottom:6px}"
                   ".head b{color:#64b5f6}pre{background:#0d0d0d;border:1px solid #333;"
                   "border-radius:8px;padding:10px;overflow-x:auto}"
                   "code{background:#0d0d0d;padding:1px 5px;border-radius:4px}"
                   "pre code{background:none;padding:0}a{color:#64b5f6}"
                   "blockquote{border-left:3px solid #64b5f6;margin:8px 0;padding:4px 12px;color:#bdbdbd}"
                   "hr{border:none;border-top:1px solid #444}li{margin:3px 0}"
                   ".stats{font-size:11px;color:#757575;margin-top:6px}")
            parts = []
            for m in ms:
                who = tr('you') if m.is_user else tr('assistant')
                tstr = time.strftime("%H:%M %d.%m.%Y", time.localtime(m.ts or time.time()))
                cls = "user" if m.is_user else "assistant"
                stats = getattr(m, "gen_stats", None)
                stats_h = f'<div class="stats">{html_mod.escape(stats)}</div>' if stats and not m.is_user else ""
                parts.append(f'<div class="msg {cls}"><div class="head"><b>{html_mod.escape(who)}</b> {tstr}</div>'
                             f"{md_to_html(m.text)}{stats_h}</div>")
            page_h = (f"<!DOCTYPE html><html><head><meta charset='utf-8'>"
                      f"<title>Chat {tag}</title><style>{css}</style></head>"
                      f"<body>{''.join(parts)}</body></html>")
            (DATA / f"chat_{tag}.html").write_text(page_h, "utf-8")
        elif fmt == "json":  # полный экспорт чата в data/ (раньше молча уходил в data/chats/)
            def _dump(m):
                try:
                    return m.to_dict(include_b64=False)
                except TypeError:
                    d = m.to_dict()
                    for a in d.get("attachments", []):
                        if isinstance(a, dict):
                            a.pop("b64", None)
                    return d
            payload = {"cid": tag,
                       "exported_at": time.strftime("%Y-%m-%d %H:%M:%S"),
                       "messages": [_dump(m) for m in ms]}
            (DATA / f"chat_{tag}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), "utf-8")
        elif fmt == "feedback":  # выгрузка оценок в JSONL для fine-tuning (без b64)
            def _nodump(m):
                try: return m.to_dict(include_b64=False)
                except TypeError: return m.to_dict()
            rows = [_nodump(m) for m in ms if not m.is_user and (m.rating or m.feedback_type)]
            if not rows: show_e(tr("e_no_msgs")); return
            p = DATA / f"feedback_{tag}.jsonl"
            p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in rows), "utf-8")
        else: save_chat(state["cid"], ms)
        status.value = tr("exported", f=fmt); page.update()

    def export_selected(fmt):
        """№8: экспорт только отмеченных галочкой сообщений."""
        sel = [m for m in state["msgs"] if m.uid in (state.get("selected") or set())]
        if not sel:
            show_e(tr("no_selection")); return
        export_msgs(sel, fmt, f"{state['cid']}-sel")

    def export_selected_dlg(e=None):
        sel = [m for m in state["msgs"] if m.uid in (state.get("selected") or set())]
        if not sel:
            show_e(tr("no_selection")); return

        def _go(fmt):
            def _h(e):
                try: page.pop_dialog()
                except Exception: pass
                export_selected(fmt)
            return _h

        def _close(e):
            try: page.pop_dialog()
            except Exception: pass
            page.update()
        page.show_dialog(ft.AlertDialog(title=ft.Text(tr("m_exp_sel", n=len(sel))),
            content=ft.Text(tr("exp_sel_hint")),
            actions=[ft.TextButton("Markdown", on_click=_go("md")),
                     ft.TextButton("HTML", on_click=_go("html")),
                     ft.TextButton("JSON", on_click=_go("json")),
                     ft.TextButton(tr("cancel"), on_click=_close)],
            actions_alignment=ft.MainAxisAlignment.END))

    async def confirm_ctx_overflow(cur: int, cl: int) -> str:
        """Диалог при переполнении контекста: compress / continue / cancel."""
        fut = asyncio.get_event_loop().create_future()
        def close(v):
            try: page.pop_dialog()
            except Exception: pass
            if not fut.done(): fut.set_result(v)
            page.update()
        dlg = ft.AlertDialog(
            title=ft.Text(tr("dlg_ctxfull_t")),
            content=ft.Text(tr("dlg_ctxfull_c", cur=cur, cl=cl)),
            actions=[ft.TextButton(tr("summarize"), on_click=lambda e: close("compress")),
                     ft.TextButton(tr("continue_btn"), on_click=lambda e: close("continue")),
                     ft.TextButton(tr("cancel"), on_click=lambda e: close("cancel"))],
            actions_alignment=ft.MainAxisAlignment.END)
        page.show_dialog(dlg)
        return await fut

    def find_in_chat(e=None):  # полнотекстовый поиск по текущему чату
        q_f = ft.TextField(label=tr("m_find"), autofocus=True,
                           value=state.get("chat_filter", ""))
        def close(e=None):
            try: page.pop_dialog()
            except Exception: pass
            page.update()
        def apply(e=None):
            state["chat_filter"] = (q_f.value or "").strip()
            close(); render_all()
            if state["chat_filter"]:
                n = sum(1 for m in state["msgs"] if state["chat_filter"].lower() in (m.text or "").lower())
                status.value = f"🔍 {n}"; page.update()
        def clear(e=None):
            state["chat_filter"] = ""; close(); render_all()
        page.show_dialog(ft.AlertDialog(title=ft.Text(tr("m_find")),
            content=q_f, actions=[ft.TextButton(tr("clear"), on_click=clear),
            ft.TextButton(tr("cancel"), on_click=close), ft.TextButton(tr("save"), on_click=apply)],
            actions_alignment=ft.MainAxisAlignment.END))

    def find_all_chats(e=None):
        """№: глобальный поиск по всем чатам (заголовки + тела сообщений)."""
        q_f = ft.TextField(label=tr("m_find_all"), autofocus=True, dense=True)
        results = ft.Column(spacing=4, scroll=ft.ScrollMode.AUTO, expand=True)
        def close(e=None):
            try: page.pop_dialog()
            except Exception: pass
            page.update()
        def run(e=None):
            q = (q_f.value or "").strip()
            results.controls.clear()
            if not q:
                page.update(); return
            try:
                hits = store.search_all(q, limit=50)
            except Exception as ex:
                results.controls.append(ft.Text(str(ex), color="#EF5350"))
                page.update(); return
            if not hits:
                results.controls.append(ft.Text(tr("e_no_results"), color=th["muted"]))
            for h in hits:
                def go(ev, cid=h["cid"], query=q):
                    close()
                    open_chat(cid)
                    state["chat_filter"] = query  # чат откроется уже с фильтром
                    render_all()
                    n = sum(1 for m in state["msgs"]
                            if query.lower() in (m.text or "").lower())
                    status.value = f"🔍 {n}"; page.update()
                results.controls.append(ft.Container(
                    on_click=go, padding=8, border_radius=8,
                    border=ft.Border.all(1, th["border"]),
                    bgcolor=th["hover"],
                    content=ft.Column(spacing=2, controls=[
                        ft.Row([ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, size=12,
                                        color=th["accent"]),
                                ft.Text(h["title"], size=12,
                                        weight=ft.FontWeight.BOLD, color=th["atc"])],
                               spacing=4),
                        ft.Text(h["snippet"], size=11, color=th["muted"],
                                max_lines=2, overflow=ft.TextOverflow.ELLIPSIS)])))
            page.update()
        q_f.on_submit = run
        page.show_dialog(ft.AlertDialog(
            title=ft.Text(tr("m_find_all")),
            content=ft.Container(
                width=540, height=380,
                content=ft.Column([q_f, results], spacing=8, expand=True)),
            actions=[ft.TextButton(tr("search_run"), on_click=run),
                     ft.TextButton(tr("cancel"), on_click=close)],
            actions_alignment=ft.MainAxisAlignment.END))

    def toggle_rated_only(e=None):  # фильтр: только оценённые сообщения
        state["rated_only"] = not state.get("rated_only", False)
        render_all()
        n = sum(1 for m in state["msgs"] if m.rating or m.feedback_type)
        status.value = f"★ {n}" if state["rated_only"] else ""; page.update()

    async def pick(e=None):
        fs_ = await picker.pick_files(allow_multiple=True,
            allowed_extensions=["png", "jpg", "jpeg", "webp", "gif", "txt", "py", "md", "pdf", "docx", "xlsx", "csv"])
        if not fs_: return
        for f in fs_:
            if f.path and f.path not in state["files"]: state["files"].append(f.path)
        refresh_attach_row()

    def paste_image():
        """№5: вставить картинку из буфера обмена (скриншот по Ctrl+V)."""
        try:
            from PIL import ImageGrab  # type: ignore
        except ImportError:
            show_e(tr("e_no_pil")); return
        try:
            img = ImageGrab.grabclipboard()
        except Exception as ex:
            show_e(tr("e_clipboard", e=ex)); return
        if img is None:  # в буфере текст/пусто — обычный Ctrl+V отработает сам
            return
        try:
            if isinstance(img, list):  # имена файлов из проводника
                added = 0
                for p in img:
                    if isinstance(p, str) and Path(p).is_file() and str(p) not in state["files"]:
                        state["files"].append(str(p)); added += 1
                if added:
                    refresh_attach_row()
                return
            img = img.convert("RGB")
            ATTACH.mkdir(parents=True, exist_ok=True)
            p = str(ATTACH / f"clip_{int(time.time() * 1000)}.png")
            img.save(p, "PNG")
            if p not in state["files"]:
                state["files"].append(p)
            refresh_attach_row()
            status.value = tr("pasted_img", n=Path(p).name); page.update()
        except Exception as ex:
            show_e(tr("e_clipboard", e=ex))

    def refresh_attach_row():
        """Превью вложений: миниатюры картинок, чипы файлов, у каждого — крестик удаления."""
        attach_row.controls.clear()
        for p in list(state["files"]):
            mime, _ = mimetypes.guess_type(p)
            try: size = f" {(Path(p).stat().st_size // 1024)} KB"
            except Exception: size = ""
            def _rm(e, path=p):
                try: state["files"].remove(path)
                except ValueError: pass
                refresh_attach_row()
            if mime and mime.startswith("image/"):
                attach_row.controls.append(ft.Row([
                    ft.Container(ft.Image(src=p, width=72, height=56, fit=ft.BoxFit.COVER,
                                          border_radius=8, error_content=ft.Text("🖼")),
                                 tooltip=Path(p).name + size),
                    ft.IconButton(ft.Icons.CLOSE, icon_size=13, tooltip=tr("delete"),
                                  on_click=_rm)],
                    spacing=0, vertical_alignment=ft.CrossAxisAlignment.START))
            else:
                attach_row.controls.append(ft.Chip(
                    label=ft.Text(f"{Path(p).name}{size}"),
                    on_delete=lambda e, path=p: _rm(e, path)))
        page.update()

    def on_inp(e):
        n = len(inp.value or "")
        counter.value = f"{n} / {S['input_max']}"
        counter.color = "#EF5350" if n > S["input_max"] else th["muted"]
        counter.update()
    inp.on_change = on_inp; inp.on_submit = send
    async def load_prompt_file(e=None):  # загрузка System prompt из .txt
        hide_e()
        try:
            res = await picker.pick_files(allow_multiple=False, allowed_extensions=["txt", "md"])
        except Exception as ex: show_e(tr("e_open_dlg", e=ex)); return
        if not res:
            status.value = tr("load_cancel"); page.update(); return
        p = res[0].path if hasattr(res[0], "path") else res[0].get("path")
        try:
            txt = Path(p).read_text(encoding="utf-8-sig")
        except Exception as ex: show_e(tr("e_read_file", e=ex)); return
        sys_f.value = txt; persist(); status.value = tr("prompt_loaded", n=Path(p).name, l=len(txt)); page.update()
    async def save_prompt_file(e=None):  # сохранение System prompt в .txt
        hide_e()
        txt = (sys_f.value or "").strip()
        if not txt: show_e(tr("e_empty_prompt")); return
        try:
            out = await picker.save_file(file_name="system_prompt.txt", allowed_extensions=["txt"])
        except Exception as ex: show_e(tr("e_open_dlg", e=ex)); return
        if not out:
            status.value = tr("save_cancel"); page.update(); return
        p = out.path if hasattr(out, "path") else (out if isinstance(out, str) else None)
        try:
            Path(p).write_text(txt, encoding="utf-8")
        except Exception as ex: show_e(tr("e_save_file", ex=ex)); return
        persist(); status.value = tr("prompt_saved", p=p); page.update()
    search.on_change = lambda e: refresh_sidebar()
    preset_name = ft.TextField(label=tr("preset_name"), hint_text=tr("preset_name_hint"), width=200, dense=True)
    def refresh_presets(sel=None):
        PRESETS.clear(); PRESETS.update(_all_presets())
        preset_dd.options = [ft.DropdownOption(key=k, text=preset_display(k)) for k in PRESETS]
        if sel and sel in PRESETS: preset_dd.value = sel
        elif preset_dd.value not in PRESETS: preset_dd.value = "Обычный"
        try: preset_dd.update()
        except Exception: pass
        page.update()
    def apply_preset(e):
        key = (getattr(getattr(e, "control", None), "value", None)
               or preset_dd.value)  # значение из события надёжнее
        new_text = PRESETS.get(key, "") if key in PRESETS else ""
        sys_f.value = new_text  # очистить + вставить одним присвоением
        try: sys_f.update()
        except Exception: pass
        persist(); page.update()
    def save_preset(e):  # сохранить текущий System prompt как пресет
        hide_e()
        name = (preset_name.value or "").strip()
        txt = (sys_f.value or "").strip()
        if not name: show_e(tr("e_enter_preset")); return
        if not txt: show_e(tr("e_empty_prompt")); return
        if name in BUILTIN_PRESETS: show_e(tr("e_builtin_name")); return
        custom = _load_custom_presets(); custom[name] = txt
        try: _save_custom_presets(custom)
        except Exception as ex: show_e(tr("e_save_preset", ex=ex)); return
        preset_name.value = ""; refresh_presets(sel=name)
        status.value = tr("preset_saved", n=name); page.update()
    def delete_preset(e):  # удалить пользовательский пресет
        name = preset_dd.value
        if name in BUILTIN_PRESETS or name == "Обычный": show_e(tr("e_builtin_del")); return
        custom = _load_custom_presets()
        if name not in custom: show_e(tr("e_no_preset")); return
        del custom[name]
        try: _save_custom_presets(custom)
        except Exception as ex: show_e(tr("e_del_preset", ex=ex)); return
        refresh_presets(sel="Обычный"); apply_preset(None)
        status.value = tr("preset_deleted", n=name); page.update()
    def clear_prompt(e):  # очистить поле System prompt
        sys_f.value = ""; sys_f.update(); persist()
        status.value = tr("prompt_cleared"); page.update()
    # --- Профили связок: модель + пресет + параметры одним кликом ---
    from repositories import ProfilesRepository as _ProfilesRepository  # type: ignore
    _profiles_repo = _ProfilesRepository(PROFILES_F)
    def _load_profiles() -> dict: return _profiles_repo.load()
    def _save_profiles(p: dict): _profiles_repo.save(p)
    profile_dd = ft.Dropdown(label=tr("profile"), width=220)
    profile_name = ft.TextField(label=tr("profile_name"), hint_text=tr("profile_name_hint"), width=200, dense=True)
    def refresh_profiles(sel=None):
        try:
            profs = _load_profiles()
            profile_dd.options = [ft.DropdownOption(key=k, text=k) for k in profs]
            if sel and sel in profs: profile_dd.value = sel
            else: profile_dd.value = None
            profile_dd.update()
        except Exception: pass
    async def apply_profile(e=None):
        name = profile_dd.value
        profs = _load_profiles()
        if not name or name not in profs: return
        p = profs[name]
        hide_e()
        try: tmp.value = float(p.get("temperature", 0.7)); tmp_val.value = f"{float(tmp.value):.2f}"; tmp.update(); tmp_val.update()
        except Exception: pass
        maxt.value = str(p.get("max_tokens", 2048)); seed.value = str(p.get("seed", -1))
        try:
            _cv = min(float(ctxlen.max or 131072), max(float(ctxlen.min or 1024), float(p.get("context_length", 8192))))
            ctxlen.value = _cv; ctxlen_val.value = f"{int(_cv)}"; ctxlen.update(); ctxlen_val.update()
        except Exception: pass
        send_images_cb.value = bool(p.get("send_images", True))
        try:
            for c in (maxt, seed, ctxlen, send_images_cb): c.update()
        except Exception: pass
        if p.get("preset") in PRESETS:
            preset_dd.value = p["preset"]
            try: preset_dd.update()
            except Exception: pass
        if p.get("system_prompt") is not None:
            sys_f.value = p["system_prompt"]
            try: sys_f.update()
            except Exception: pass
        persist(); page.update()
        if p.get("model") and p["model"] != model_dd.value:
            if p["model"] not in [o.key for o in model_dd.options]:
                show_e(tr("e_no_model_on_server", m=p['model'])); return
            model_dd.value = p["model"]
            try: model_dd.update()
            except Exception: pass
            state["model_touched"] = True; persist()
            await ensure_model_loaded()
        status.value = tr("profile_applied", n=name); page.update()
    def save_profile(e):
        hide_e()
        name = (profile_name.value or "").strip()
        if not name: show_e(tr("e_enter_preset")); return
        profs = _load_profiles()
        profs[name] = {"model": model_dd.value or "", "preset": preset_dd.value,
            "system_prompt": sys_f.value or "", "temperature": float(tmp.value or 0.7),
            "max_tokens": str(maxt.value or 2048),
            "seed": str(seed.value or -1), "context_length": str(int(float(ctxlen.value or 8192))),
            "send_images": bool(send_images_cb.value)}
        try: _save_profiles(profs)
        except Exception as ex: show_e(str(ex)); return
        profile_name.value = ""; refresh_profiles(sel=name)
        status.value = tr("profile_saved", n=name); page.update()
    def delete_profile(e):
        name = profile_dd.value
        profs = _load_profiles()
        if not name or name not in profs: show_e(tr("e_no_preset")); return
        del profs[name]
        try: _save_profiles(profs)
        except Exception as ex: show_e(str(ex)); return
        refresh_profiles(); status.value = tr("profile_deleted", n=name); page.update()
    # --- Environment Context: requirements.txt / .env ---
    env_label = ft.Text("", size=12, color=th["muted"])
    def refresh_env_label():
        req = (settings.get("env_requirements") or "")
        nvars = len(settings.get("env_vars") or {})
        bits = []
        if req: bits.append(tr("env_req", n=len(req)))
        if nvars: bits.append(tr("env_vars_n", n=nvars))
        env_label.value = " · ".join(bits) if bits else tr("env_none")
    refresh_env_label()
    def _parse_dotenv(text: str) -> dict:
        out = {}
        for line in (text or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line: continue
            k, v = line.split("=", 1)
            k = k.strip()
            if k.startswith("export "): k = k[7:].strip()
            out[k] = "***"  # mask values, keep keys
        return out
    async def load_requirements(e=None):
        hide_e()
        try:
            res = await picker.pick_files(allow_multiple=False, allowed_extensions=["txt"])
        except Exception as ex: show_e(tr("e_open_dlg", e=ex)); return
        if not res: status.value = tr("load_cancel"); page.update(); return
        p = res[0].path if hasattr(res[0], "path") else res[0].get("path")
        try: txt = Path(p).read_text(encoding="utf-8-sig")[:8000]
        except Exception as ex: show_e(tr("e_read_file", e=ex)); return
        settings["env_requirements"] = txt; settings["env_req_name"] = Path(p).name
        save_settings(settings); refresh_env_label(); env_label.update()
        status.value = tr("req_loaded", n=Path(p).name); page.update()
    async def load_dotenv(e=None):
        hide_e()
        try:
            res = await picker.pick_files(allow_multiple=False, allowed_extensions=["env", "txt"])
        except Exception as ex: show_e(tr("e_open_dlg", e=ex)); return
        if not res: status.value = tr("load_cancel"); page.update(); return
        p = res[0].path if hasattr(res[0], "path") else res[0].get("path")
        try: txt = Path(p).read_text(encoding="utf-8-sig")
        except Exception as ex: show_e(tr("e_read_file", e=ex)); return
        settings["env_vars"] = _parse_dotenv(txt)
        save_settings(settings); refresh_env_label(); env_label.update()
        status.value = tr("env_loaded", n=len(settings['env_vars'])); page.update()
    def clear_env(e=None):
        settings["env_requirements"] = ""; settings["env_vars"] = {}
        save_settings(settings); refresh_env_label(); env_label.update(); page.update()
    # --- Authentication API: режим доступа к серверу + ключ ---
    # Ключ живёт в settings.json, но никогда не попадает в логи/в контекст модели
    # и не дублируется в UI-логах: пишем только «задан/пуст». Значение поля
    # сохраняется как есть (переключение режима его не стирает), но отправляется
    # только в режиме 'api_key' — см. lm_client.api_key_from_settings.
    _am = str(settings.get("auth_mode") or "none")
    if _am not in AUTH_MODES: _am = "none"
    auth_dd = ft.Dropdown(label=tr("auth_mode"), value=_am, width=240,
                          options=[ft.DropdownOption("none", tr("auth_mode_none")),
                                   ft.DropdownOption("api_key", tr("auth_mode_api_key"))])
    api_key_f = ft.TextField(label=tr("api_key"), value=str(settings.get("api_key") or ""),
                             hint_text=tr("api_key_hint"), password=True,
                             can_reveal_password=True, width=260, dense=True)
    api_key_f.visible = _am == "api_key"
    def _auth_labels():
        auth_dd.label = tr("auth_mode")
        auth_dd.options = [ft.DropdownOption("none", tr("auth_mode_none")),
                           ft.DropdownOption("api_key", tr("auth_mode_api_key"))]
        api_key_f.label = tr("api_key"); api_key_f.hint_text = tr("api_key_hint")
    def _save_auth(verify: bool = False):
        """Сохранить режим/ключ и применить их к живому клиенту сессии."""
        mode = auth_dd.value if auth_dd.value in AUTH_MODES else "none"
        key = (api_key_f.value or "").strip()
        settings["auth_mode"], settings["api_key"] = mode, key
        save_settings(settings)
        api_key_f.visible = mode == "api_key"
        live = api_key_from_settings(settings)  # в режиме 'none' всегда пусто
        client.set_api_key(live)  # следующие запросы — с новым ключом
        _log.info("server access mode=%s, api key %s", mode, "set" if live else "empty")
        try: page.update()
        except Exception: pass
        if verify:
            try: asyncio.get_running_loop().create_task(verify_auth())
            except Exception as ex: _log.warning("auth recheck not started: %s", ex)
    def on_auth_mode(e=None): _save_auth(verify=True)
    def on_api_key(e=None):
        """Печатаем — только применяем ключ в живом клиенте.

        settings.json пишется на blur (и при смене режима): писать файл и
        логировать на каждую клавишу незачем, а страницу обновлять нельзя
        — ввод и так перерисовывается сам.
        """
        mode = auth_dd.value if auth_dd.value in AUTH_MODES else "none"
        settings["api_key"] = (api_key_f.value or "").strip()  # только в памяти
        client.set_api_key(api_key_from_settings({**settings, "auth_mode": mode}))
    def on_api_key_blur(e=None): _save_auth(verify=True)
    auth_dd.on_select = on_auth_mode
    api_key_f.on_change = on_api_key
    api_key_f.on_blur = on_api_key_blur
    # --- Backend: lmstudio | strata + thinking-effort для Strata ---
    from lm_client import BACKENDS, STRATA_EFFORTS  # type: ignore
    _be = str(settings.get("backend") or "lmstudio").lower()
    if _be not in BACKENDS:
        _be = "lmstudio"
    backend_dd = ft.Dropdown(label="Backend", value=_be, width=150,
                             options=[ft.DropdownOption("lmstudio", "LM Studio"),
                                      ft.DropdownOption("strata", "Strata")])
    backend_url_f = ft.TextField(label="Strata URL",
                                 value=str(settings.get("strata_url") or "http://127.0.0.1:8080/v1"),
                                 hint_text="http://127.0.0.1:8080/v1", width=260, dense=True)
    backend_url_f.visible = _be == "strata"
    _ef = str(settings.get("reasoning_effort") or "").lower()
    effort_dd = ft.Dropdown(label="Thinking", value=_ef if _ef in STRATA_EFFORTS else "",
                            width=130,
                            options=[ft.DropdownOption("", "default")] +
                            [ft.DropdownOption(e, e) for e in STRATA_EFFORTS])
    effort_dd.visible = _be == "strata"

    def _save_backend(e=None):
        prev_be = backend_from_settings(settings)
        be = backend_dd.value if backend_dd.value in BACKENDS else "lmstudio"
        prev_model = state.get("loaded_model")
        settings["backend"] = be
        settings["strata_url"] = (backend_url_f.value or "").strip() or "http://127.0.0.1:8080/v1"
        settings["reasoning_effort"] = effort_dd.value or ""
        save_settings(settings)
        backend_url_f.visible = be == "strata"
        effort_dd.visible = be == "strata"
        _log.info("backend=%s strata_url=%s effort=%s", be,
                  settings["strata_url"], settings["reasoning_effort"] or "-")
        try:
            # шапка и MCP-секция зависят от бэкенда: thinking/URL/тумблеры
            _refresh_mcp_visibility()
        except Exception:
            pass
        try:
            page.update()
        except Exception:
            pass
        if be != prev_be:
            # смена сервера: выгрузить модель на старом (освободить VRAM/RAM),
            # переткнуть клиент и поднять запомненную модель нового
            try:
                asyncio.get_running_loop().create_task(
                    _switch_backend(prev_be, be, prev_model))
            except Exception as ex:
                _log.warning("backend switch not started: %s", ex)
        else:
            try:
                client.set_base_url(base_url_from_settings(settings))
                asyncio.get_running_loop().create_task(load_models())
            except Exception as ex:
                _log.warning("backend recheck not started: %s", ex)

    async def _switch_backend(old_be: str, new_be: str, old_model: str | None):
        """Переход между серверами с выгрузкой/загрузкой моделей.

        Важно: выгрузка идёт через клиент, ещё смотрящий на СТАРЫЙ сервер,
        и только потом перетыкаем URL. Всё best effort: сервер мог уже
        лежать — тогда просто идём дальше к новому.
        """
        if old_model:
            try:
                status.value = tr("model_unloading", m=old_model)
                page.update()
            except Exception:
                pass
            try:
                await client.unload_model(old_model, old_be)
                _log.info("unloaded %s on %s before backend switch", old_model, old_be)
            except Exception as ex:
                _log.warning("unload on backend switch failed (%s/%s): %s", old_be, old_model, ex)
            state["loaded_model"] = None
        try:
            # живой клиент создан при старте под старый бэкенд — переткнуть URL,
            # иначе load_models() и чат продолжат бить в прежний сервер
            client.set_base_url(base_url_from_settings(settings))
        except Exception as ex:
            _log.warning("client re-point failed: %s", ex)
        try:
            _refresh_mcp_visibility()
        except Exception:
            pass
        # load_models подхватит уже загруженное на новом сервере, иначе
        # поднимет запомненную именно для него модель (loaded_model_<backend>)
        await load_models()

    backend_dd.on_select = _save_backend
    backend_url_f.on_blur = _save_backend
    effort_dd.on_select = _save_backend
    async def verify_auth():
        """Разовая проверка связи с новыми credentials (без загрузки модели)."""
        try:
            models = await client.fetch_models()
        except Exception as ex:
            _log.warning("server access check failed: %s", ex)
            set_conn(False, tr("no_conn")); show_e(str(ex)); return
        set_conn(True); hide_e()
        status.value = tr("models_n", n=len(models)) if models else tr("connected")
        page.update()
    # --- MCP (серверы из mcp.json) ---
    # Эндпоинта перечисления настроенных MCP-серверов у LM Studio нет, поэтому
    # метки добавляются руками — ключи из mcp.json, хранятся как 'mcp/<label>',
    # в payload уходят server_label без префикса (lm_client._mcp_tools).
    # Выбранные серверы хранятся списком в data/settings.json и
    # показываются чипами с крестиком. Пустой список при включённом тумблере —
    # подсказка, а не молчаливое переключение транспорта.
    mcp_sw = ft.Switch(label=tr("mcp_use"), value=mcp_enabled_from_settings(settings))
    mcp_chips = ft.Row(wrap=True, spacing=4)
    mcp_add_f = ft.TextField(label=tr("mcp_add_id"), hint_text=tr("mcp_add_hint"),
                             width=300, dense=True,
                             on_submit=lambda e: _add_mcp_server(),
                             suffix=ft.IconButton(icon=ft.Icons.ADD, tooltip=tr("mcp_add"),
                                                  on_click=lambda e: _add_mcp_server()))
    def _mcp_labels():
        mcp_sw.label = tr("mcp_use")
        mcp_add_f.label = tr("mcp_add_id"); mcp_add_f.hint_text = tr("mcp_add_hint")
    def _render_mcp_chips():
        """Чипы выбранных серверов: подпись — id, крестик — убрать."""
        mcp_chips.controls = [
            ft.Chip(label=ft.Row([ft.Icon(icon=ft.Icons.CIRCLE, size=10, color=ft.Colors.GREEN), ft.Text(sid, size=11, no_wrap=True),
                                   ft.IconButton(icon=ft.Icons.CLOSE, icon_size=14,
                                                 tooltip=tr("mcp_remove"),
                                                 on_click=lambda e, s=sid: _del_mcp_server(s))],
                                  spacing=2, tight=True),
                     tooltip=sid, on_delete=lambda e, s=sid: _del_mcp_server(s))
            for sid in mcp_servers_from_settings(settings)]
    def _del_mcp_server(sid: str):
        keep = [s for s in mcp_servers_from_settings(settings) if s != sid]
        settings["mcp_servers"] = keep
        save_settings(settings)
        _render_mcp_chips(); _save_mcp()
    def _add_mcp_server():
        """Добавить введённый id: пустая строка игнорируется, дубликат не добавим."""
        raw = (mcp_add_f.value or "").strip()
        mcp_add_f.value = ""
        if not raw:
            return
        cur = mcp_servers_from_settings(settings)
        settings["mcp_servers"] = cur + [raw]  # нормализацию делает _save_mcp
        save_settings(settings)
        _render_mcp_chips(); _save_mcp()
    def _save_mcp(e=None):
        """Сохранить тумблер и список серверов; статус — короткой подсказкой."""
        settings["mcp_enabled"] = bool(mcp_sw.value)
        settings["mcp_servers"] = mcp_servers_from_settings(settings)
        settings.pop("mcp_mode", None)  # ранняя сборка: off/on больше не нужны
        save_settings(settings)
        ids = mcp_servers_from_settings(settings)
        mcp_add_f.visible = mcp_chips.visible = bool(mcp_sw.value)
        if mcp_sw.value and not ids:
            _log.warning("mcp enabled without servers — falling back to OpenAI-compatible path")
        _log.info("mcp enabled=%s servers=%s", bool(mcp_sw.value), ",".join(ids) or "-")
        if mcp_sw.value:  # мягкая подсказка в статусе: без серверов режим молча не работает
            status.value = (tr("mcp_no_servers") if not ids
                            else tr("mcp_active", n=len(ids)))
            status.color = th["muted"]
        try: page.update()
        except Exception: pass
    mcp_sw.on_change = _save_mcp
    mcp_add_f.on_blur = _save_mcp
    mcp_add_f.visible = mcp_chips.visible = bool(mcp_sw.value)
    _render_mcp_chips()
    # --- Strata MCP: тумблер + каталог инструментов (GET /mcp) ---
    # У Strata серверы настраиваются в strata-<model>.json, а не в нашем UI,
    # поэтому здесь только вкл/выкл флага strata_mcp в запросах и просмотр
    # того, что реально отдаёт сервер: серверы, статус, тулы с описаниями.
    from lm_client import strata_mcp_enabled as _strata_mcp_on  # type: ignore
    strata_mcp_sw = ft.Switch(label=tr("mcp_strata_use"),
                              value=bool(_strata_mcp_on(settings)))

    def _save_strata_mcp(e=None):
        settings["strata_mcp"] = bool(strata_mcp_sw.value)
        save_settings(settings)
        _log.info("strata_mcp=%s", settings["strata_mcp"])
        try:
            page.update()
        except Exception:
            pass

    strata_mcp_sw.on_change = _save_strata_mcp

    def _refresh_mcp_visibility():
        """LM-чипы — только для lmstudio, strata-тумблер — только для strata."""
        from lm_client import backend_from_settings as _be  # type: ignore
        is_strata = _be(settings) == "strata"
        strata_mcp_sw.visible = is_strata
        tools_btn.visible = is_strata
        lm_only = not is_strata
        mcp_sw.visible = lm_only
        mcp_add_f.visible = mcp_chips.visible = lm_only and bool(mcp_sw.value)
        try:
            page.update()
        except Exception:
            pass

    async def _fetch_tools():
        try:
            return await client.list_strata_tools()
        except Exception as ex:
            _log.warning("GET /mcp failed: %s", ex)
            return {"error": str(ex)}

    def open_tool_catalog(e=None):
        """Диалог «Каталог инструментов»: серверы + тулы с описаниями + поиск."""
        search_f = ft.TextField(hint_text=tr("tools_search"), width=320, dense=True)
        body = ft.Column(spacing=6, scroll=ft.ScrollMode.AUTO, height=380, width=560)
        status_t = ft.Text("", size=12, color=th["muted"])

        def _render(data: dict):
            q = (search_f.value or "").strip().lower()
            body.controls.clear()
            if data.get("error"):
                body.controls.append(ft.Text(str(data["error"]), color="#EF5350"))
                return
            servers = data.get("servers") or []
            if not servers:
                body.controls.append(ft.Text(tr("tools_empty"), color=th["muted"]))
                return
            for srv in servers:
                if not isinstance(srv, dict):
                    continue
                sname = str(srv.get("name") or "?")
                st = str(srv.get("status") or "")
                tools = srv.get("tools") or []
                shown = [t for t in tools
                         if isinstance(t, dict) and (not q or q in str(t.get("name", "")).lower()
                             or q in str(t.get("description", "")).lower())]
                if q and not shown:
                    continue
                body.controls.append(
                    ft.Text(f"{sname}  •  {st}  •  {len(tools)}", weight=ft.FontWeight.BOLD,
                            color=th["atc"]))
                for t in shown:
                    tname = str(t.get("name") or t.get("tool") or "?")
                    tdesc = str(t.get("description") or "")
                    body.controls.append(
                        ft.Column([
                            ft.Text(tname, size=13, color=th["accent"],
                                    weight=ft.FontWeight.BOLD),
                            ft.Text(tdesc, size=12, color=th["muted"])],
                            spacing=1, tight=True))
                body.controls.append(ft.Divider(height=4, color=th["border"]))

        async def _load():
            status_t.value = tr("tools_loading")
            try:
                status_t.update()
            except Exception:
                pass
            data = await _fetch_tools()
            dlg._last = data if isinstance(data, dict) else {}  # type: ignore[attr-defined]
            status_t.value = "" if not dlg._last.get("error") else str(dlg._last.get("error"))  # type: ignore[attr-defined]
            _render(dlg._last)  # type: ignore[attr-defined]
            try:
                status_t.update()
                body.update()
            except Exception:
                pass
            try:
                page.update()
            except Exception:
                pass

        def _on_search(e=None):
            async def _re():
                _render(getattr(dlg, "_last", {}))
                try:
                    body.update()
                except Exception:
                    pass
            try:
                asyncio.get_running_loop().create_task(_re())
            except Exception:
                pass

        async def _initial():
            data = await _fetch_tools()
            dlg._last = data if isinstance(data, dict) else {}  # type: ignore[attr-defined]
            _render(dlg._last)  # type: ignore[attr-defined]
            status_t.value = "" if not dlg._last.get("error") else str(dlg._last.get("error"))  # type: ignore[attr-defined]
            try:
                status_t.update()
                body.update()
            except Exception:
                pass

        search_f.on_change = _on_search
        dlg = ft.AlertDialog(
            title=ft.Text(tr("tools_title")),
            content=ft.Column([search_f, status_t, body], spacing=6,
                              tight=True),
            actions=[ft.TextButton(tr("tools_refresh"),
                                   on_click=lambda e: asyncio.get_running_loop().create_task(_load())),
                     ft.TextButton(tr("close"), on_click=lambda e: close_tools_dlg())],
            modal=False)

        def close_tools_dlg():
            try:
                page.pop_dialog()
            except Exception:
                pass
            try:
                page.update()
            except Exception:
                pass

        page.show_dialog(dlg)
        try:
            asyncio.get_running_loop().create_task(_initial())
        except Exception as ex:
            status_t.value = str(ex)

    tools_btn = ft.OutlinedButton(tr("tools_catalog"), icon=ft.Icons.EXTENSION_OUTLINED,
                                  on_click=open_tool_catalog)
    _refresh_mcp_visibility()
    preset_dd.on_select = apply_preset
    preset_dd.on_blur = apply_preset
    model_dd.on_select = on_model_change
    profile_dd.on_select = apply_profile
    # (7) живые значения слайдеров
    tmp_val = ft.Text(f"{float(tmp.value or 0.7):.2f}", size=12, color=th["atc"], width=36)
    ctxlen_val = ft.Text(f"{int(float(ctxlen.value or 8192))}", size=12, color=th["atc"], width=52)
    def on_tmp(e): tmp_val.value = f"{float(tmp.value or 0):.2f}"; tmp_val.update(); persist()
    def on_ctxlen(e): ctxlen_val.value = f"{int(float(ctxlen.value or 0))}"; ctxlen_val.update(); persist(); upd_tokens()
    tmp.on_change = on_tmp; ctxlen.on_change = on_ctxlen
    send_images_cb.on_change = lambda e: persist()
    def switch_theme(e):
        settings["theme"] = "light" if settings.get("theme") == "dark" else "dark"
        save_settings(settings); page.bgcolor = THEMES[settings["theme"]]["bg"]; page.update()
    def font_pm(d):
        settings["font_scale"] = min(1.5, max(0.8, float(settings.get("font_scale", 1)) + d))
        save_settings(settings); status.value = tr("font_n", v=f"{settings['font_scale']:.1f}"); page.update()

    # --- i18n: кнопка языка + живое обновление ---
    def safe_update(*cs):
        for c in cs:
            try: c.update()
            except Exception: pass
    def set_lang(e=None):
        lang = getattr(getattr(e, "control", None), "value", None) or lang_btn.value or "ru"
        if lang not in LANGS: return
        old = CUR["lang"]
        CUR["lang"] = lang
        try: _i18n.set_lang(CUR["lang"])
        except ValueError: pass
        settings["lang"] = CUR["lang"]; save_settings(settings)
        # если в поле лежит текст встроенного пресета на старом языке — заменить на новый
        for k, v in BUILTIN_PRESETS.items():
            if isinstance(v, dict) and (sys_f.value or "") == v.get(old, "") and v.get(old):
                sys_f.value = v.get(CUR["lang"], "")
                break
        apply_lang()
    def make_overflow():
        return [
            ft.PopupMenuItem(tr("m_compress"), icon=ft.Icons.COMPRESS_OUTLINED, on_click=summarize),
            ft.PopupMenuItem(tr("m_find"), icon=ft.Icons.SEARCH_OUTLINED, on_click=find_in_chat),
            ft.PopupMenuItem(tr("m_find_all"), icon=ft.Icons.FIND_IN_PAGE_OUTLINED, on_click=find_all_chats),
            ft.PopupMenuItem(tr("m_rated"), icon=ft.Icons.STAR_OUTLINE, on_click=toggle_rated_only),
            ft.PopupMenuItem(tr("m_exp_md"), icon=ft.Icons.SHARE_OUTLINED, on_click=lambda e: export("md")),
            ft.PopupMenuItem(tr("m_exp_html"), icon=ft.Icons.SHARE_OUTLINED, on_click=lambda e: export("html")),
            ft.PopupMenuItem(tr("m_exp_json"), icon=ft.Icons.SHARE_OUTLINED, on_click=lambda e: export("json")),
            ft.PopupMenuItem(tr("m_exp_fb"), icon=ft.Icons.FEEDBACK_OUTLINED, on_click=lambda e: export("feedback")),
            ft.PopupMenuItem(tr("m_exp_sel", n=len(state.get("selected") or set())), icon=ft.Icons.CHECKLIST_OUTLINED, on_click=export_selected_dlg),
            ft.PopupMenuItem(tr("handsfree"), icon=ft.Icons.HEARING_OUTLINED, on_click=lambda e: toggle_handsfree(e)),
            ft.PopupMenuItem(tr("restart_server"), icon=ft.Icons.DNS_OUTLINED, on_click=restart_server),
            ft.PopupMenuItem(tr("m_theme"), icon=ft.Icons.BRIGHTNESS_6_OUTLINED, on_click=switch_theme),
            ft.PopupMenuItem(tr("m_font_up"), icon=ft.Icons.TEXT_INCREASE_OUTLINED, on_click=lambda e: font_pm(0.1)),
            ft.PopupMenuItem(tr("m_font_down"), icon=ft.Icons.TEXT_DECREASE_OUTLINED, on_click=lambda e: font_pm(-0.1))]
    def apply_lang():
        page.title = tr("title")
        UI["side_head"].value = tr("chats")
        search.hint_text = tr("search")
        UI["new_btn"].content = tr("new_chat")
        UI["top_title"].value = tr("title")
        model_dd.label = tr("model")
        UI["refresh"].tooltip = tr("refresh_models")
        if state.get("models_n"):
            dot.bgcolor = "#4CAF50"; conn_t.value = tr("models_n", n=state["models_n"])
        else:
            conn_t.value = state["conn_custom"] if state["conn_custom"] is not None else (
                tr("connected") if state["conn_ok"] else tr("no_conn"))
        lang_btn.label = tr("lang")
        if lang_btn.value != CUR["lang"]:
            lang_btn.value = CUR["lang"]
        UI["overflow"].items = make_overflow()  # пересоздать пункты меню
        UI["overflow"].tooltip = tr("more")
        UI["settings_title"].value = tr("settings")
        UI["sec_model"].value = tr("sec_model"); UI["sec_prompt"].value = tr("sec_prompt")
        preset_dd.label = tr("preset")
        refresh_presets()  # тексты пресетов + подписи на новом языке
        preset_name.label = tr("preset_name"); preset_name.hint_text = tr("preset_name_hint")
        sys_f.label = tr("sys_prompt")
        UI["to_preset"].content = tr("to_preset")
        UI["del_preset"].tooltip = tr("del_preset")
        UI["clear"].content = tr("clear")
        UI["load_txt"].content = tr("load_txt"); UI["save_txt"].content = tr("save_txt")
        UI["attach"].tooltip = tr("attach")
        UI["mic"].tooltip = tr("stt_mic")
        send_images_cb.label = tr("vision_send")
        UI["sec_profiles"].value = tr("sec_profiles")
        UI["sec_env"].value = tr("sec_env")
        UI["sec_auth"].value = tr("sec_auth")
        _auth_labels()
        UI["sec_mcp"].value = tr("sec_mcp")
        _mcp_labels()
        strata_mcp_sw.label = tr("mcp_strata_use")
        tools_btn.text = tr("tools_catalog")
        profile_dd.label = tr("profile")
        profile_name.label = tr("profile_name"); profile_name.hint_text = tr("profile_name_hint")
        UI["profile_save"].content = tr("to_profile")
        UI["profile_del"].tooltip = tr("del_profile")
        UI["menu"].tooltip = tr("chats")
        refresh_env_label()
        try: env_label.update()
        except Exception: pass
        inp.hint_text = tr("input_hint")
        btn_send.tooltip = tr("send")
        try: btn_stop.tooltip = tr("stop")
        except NameError: pass
        safe_update(UI["side_head"], search, UI["new_btn"], UI["top_title"], model_dd,
                    UI["refresh"], conn_t, lang_btn, UI["overflow"],
                    UI["settings_title"], UI["sec_model"], UI["sec_prompt"],
                    preset_dd, preset_name, sys_f,
                    UI["to_preset"], UI["del_preset"], UI["clear"],
                    UI["load_txt"], UI["save_txt"], UI["attach"], UI["mic"], inp, btn_send,
                    UI["sec_profiles"], UI["sec_env"], UI["sec_auth"],
                    auth_dd, api_key_f, backend_dd, backend_url_f, effort_dd,
                    UI["sec_mcp"], mcp_sw, mcp_add_f,
                    mcp_chips, strata_mcp_sw, tools_btn,
                    profile_dd, profile_name,
                    UI["profile_save"], UI["profile_del"], send_images_cb, env_label)
        try: safe_update(btn_stop)
        except NameError: pass
        try: sys_f.update()
        except Exception: pass
        upd_tokens(); refresh_folders(); refresh_sidebar()
        chat_box.controls.clear()  # перерисовать подписи пузырей на новом языке
        for m in state["msgs"]: add_bubble(m)
        page.update()
    lang_btn = ft.Dropdown(label=tr("lang"), value=CUR["lang"], width=110,
                           options=[ft.DropdownOption("ru", "RU"), ft.DropdownOption("en", "EN")],
                           on_select=set_lang)

    # --- (4) sidebar + slim-топбар: модель + статус, остальное в «⋮» ---
    UI["side_head"] = ft.Text(tr("chats"), weight=ft.FontWeight.BOLD, color=th["atc"])
    UI["new_btn"] = ft.Button(tr("new_chat"), icon=ft.Icons.ADD_OUTLINED, on_click=lambda e: new_chat())
    sidebar = ft.Container(width=S["sidebar_w"], bgcolor=th["panel"], border_radius=S["radius"], padding=8,
        border=ft.Border.all(1, th["border"]), visible=False,
        content=ft.Column([UI["side_head"], search, folder_chips, chat_list, UI["new_btn"]]))
    def toggle_sidebar(e=None):
        sidebar.visible = not sidebar.visible
        try: sidebar.update()
        except Exception: pass
        page.update()
    UI["menu"] = ft.IconButton(ft.Icons.MENU_OUTLINED, tooltip=tr("chats"), on_click=toggle_sidebar)
    UI["overflow_items"] = make_overflow()
    UI["refresh"] = ft.IconButton(ft.Icons.REFRESH_OUTLINED, tooltip=tr("refresh_models"), on_click=load_models)
    overflow = ft.PopupMenuButton(icon=ft.Icons.MORE_VERT_OUTLINED, tooltip=tr("more"),
        items=UI["overflow_items"])
    UI["overflow"] = overflow
    # --- (7) хелпер секций + кнопки (должны быть созданы ДО топбара: Промпт живёт в нём) ---
    def sec(key, icon, *controls):
        t = ft.Text(tr(key), size=13, weight=ft.FontWeight.BOLD, color=th["atc"])
        UI[key] = t
        return ft.Column([ft.Row([ft.Icon(icon, size=16, color=th["accent"]), t],
                                 spacing=6)] + list(controls), spacing=6)
    UI["settings_title"] = ft.Text(tr("settings"), color=th["atc"])
    UI["to_preset"] = ft.OutlinedButton(tr("to_preset"), icon=ft.Icons.BOOKMARK_ADD_OUTLINED, on_click=save_preset)
    UI["del_preset"] = ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip=tr("del_preset"), on_click=delete_preset)
    UI["clear"] = ft.OutlinedButton(tr("clear"), icon=ft.Icons.CLEAR_OUTLINED, on_click=clear_prompt)
    UI["load_txt"] = ft.OutlinedButton(tr("load_txt"), icon=ft.Icons.UPLOAD_FILE_OUTLINED, on_click=load_prompt_file)
    UI["save_txt"] = ft.OutlinedButton(tr("save_txt"), icon=ft.Icons.SAVE_OUTLINED, on_click=save_prompt_file)
    UI["attach"] = ft.IconButton(ft.Icons.ATTACH_FILE_OUTLINED, tooltip=tr("attach"), on_click=pick)
    UI["load_req"] = ft.OutlinedButton("requirements.txt", icon=ft.Icons.UPLOAD_FILE_OUTLINED, on_click=load_requirements)
    UI["load_env"] = ft.OutlinedButton(".env", icon=ft.Icons.UPLOAD_FILE_OUTLINED, on_click=load_dotenv)
    UI["clear_env"] = ft.OutlinedButton(tr("clear"), icon=ft.Icons.CLEAR_OUTLINED, on_click=clear_env)
    UI["profile_save"] = ft.OutlinedButton(tr("to_profile"), icon=ft.Icons.BOOKMARK_ADD_OUTLINED, on_click=save_profile)
    UI["profile_del"] = ft.IconButton(ft.Icons.DELETE_OUTLINE, tooltip=tr("del_profile"), on_click=delete_profile)
    UI["sec_profiles"] = ft.Text(tr("sec_profiles"), size=13, weight=ft.FontWeight.BOLD, color=th["atc"])
    UI["top_title"] = ft.Text(tr("title"), weight=ft.FontWeight.BOLD, color=th["atc"])
    # Промпт — в том же ряду, что и модель: топбар из двух колонок (слева модель/статус, справа Промпт)
    preset_dd.width = 170
    sys_f.expand = True
    topbar = ft.Container(bgcolor=th["panel"], border_radius=S["radius"], padding=8,
        border=ft.Border.all(1, th["border"]),
        content=ft.Row([
            ft.Column([
                ft.Row([UI["menu"], ft.Icon(ft.Icons.CHAT_BUBBLE_OUTLINE, color=th["accent"]),
                    UI["top_title"]],
                    spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([backend_dd, model_dd, UI["refresh"]], spacing=8,
                       vertical_alignment=ft.CrossAxisAlignment.CENTER),
                ft.Row([dot, conn_t], spacing=4),
                ft.Row([ft.Column([tok_label, ctx_bar], spacing=2), lang_btn, overflow],
                       spacing=8, vertical_alignment=ft.CrossAxisAlignment.CENTER)],
                spacing=6),
            ft.VerticalDivider(width=8, color=th["border"]),
            ft.Column([
                sec("sec_prompt",
                    ft.Icons.CHAT_OUTLINED,
                    ft.Row([preset_dd, sys_f], spacing=8,
                           vertical_alignment=ft.CrossAxisAlignment.START),
                    ft.Row([preset_name, UI["to_preset"], UI["del_preset"], UI["clear"]],
                           wrap=True, spacing=6, run_spacing=4),
                    ft.Row([UI["load_txt"], UI["save_txt"]], wrap=True, spacing=6))],
                spacing=6, expand=True)],
            spacing=8, vertical_alignment=ft.CrossAxisAlignment.START))
    # --- Настройки: Модель + Окружение (Промпт живёт в топбаре) ---
    UI["sec_env"] = ft.Text(tr("sec_env"), size=13, weight=ft.FontWeight.BOLD, color=th["atc"])
    UI["sec_auth"] = ft.Text(tr("sec_auth"), size=13, weight=ft.FontWeight.BOLD, color=th["atc"])
    UI["sec_mcp"] = ft.Text(tr("sec_mcp"), size=13, weight=ft.FontWeight.BOLD, color=th["atc"])
    settings_p = ft.ExpansionTile(title=UI["settings_title"],
        leading=ft.Icon(ft.Icons.TUNE_OUTLINED, color=th["accent"]),
        controls=[
        sec("sec_model",
            ft.Icons.SMART_TOY_OUTLINED,
            ft.Row([ft.Text("Temperature", size=13, color=th["atc"], width=90), tmp, tmp_val],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([ft.Text("Context Length", size=13, color=th["atc"], width=90), ctxlen, ctxlen_val],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER),
            ft.Row([maxt, seed], wrap=True),
            ft.Row([send_images_cb], wrap=True)),
        ft.Divider(height=4, color=th["border"]),
        ft.Column([ft.Row([ft.Icon(ft.Icons.SECURITY_OUTLINED, size=16, color=th["accent"]),
                           UI["sec_auth"]],
                          spacing=6),
                    ft.Row([auth_dd], wrap=True),
                    ft.Row([api_key_f], wrap=True),
                    ft.Row([backend_url_f, effort_dd], wrap=True)], spacing=6),
        ft.Divider(height=4, color=th["border"]),
        ft.Column([ft.Row([ft.Icon(ft.Icons.EXTENSION_OUTLINED, size=16, color=th["accent"]),
                           UI["sec_mcp"]],
                          spacing=6),
                   ft.Row([mcp_sw], wrap=True),
                   ft.Row([mcp_add_f], wrap=True),
                   mcp_chips,
                   ft.Row([strata_mcp_sw], wrap=True),
                   ft.Row([tools_btn], wrap=True)], spacing=6),
        ft.Divider(height=4, color=th["border"]),
        ft.Column([ft.Row([ft.Icon(ft.Icons.LAYERS_OUTLINED, size=16, color=th["accent"]),
                           UI["sec_profiles"]],
                          spacing=6),
                   profile_dd,
                   ft.Row([profile_name, UI["profile_save"], UI["profile_del"]], wrap=True)], spacing=6),
        ft.Divider(height=4, color=th["border"]),
        ft.Column([ft.Row([ft.Icon(ft.Icons.DNS_OUTLINED, size=16, color=th["accent"]),
                           UI["sec_env"]],
                          spacing=6),
                   env_label,
                   ft.Row([UI["load_req"], UI["load_env"], UI["clear_env"]], wrap=True)], spacing=6)])
    # --- (5) плавающий док ввода ---
    btn_send = ft.IconButton(ft.Icons.ARROW_UPWARD_ROUNDED, tooltip=tr("send"),
        on_click=send, style=ft.ButtonStyle(bgcolor=th["accent"], color="white", shape=ft.CircleBorder()))
    def set_sending_ui(v: bool):
        """Переключить док в режим генерации: показать Стоп, заблокировать отправку."""
        try:
            btn_stop.visible = v
            btn_send.disabled = v
            btn_stop.update(); btn_send.update()
        except Exception: pass
    def do_stop(e=None):
        client.cancel()
        status.value = tr("stopped"); page.update()
    async def do_listen(e=None):
        if state.get("handsfree"):  # клик по микрофону в hands-free = выключить режим
            toggle_handsfree(); return
        try: hide_e()  # гасим прошлую ошибку распознавания
        except Exception: pass
        try:
            from voice import listen  # type: ignore
        except ImportError:
            show_e(tr("e_no_stt")); return
        mic = UI["mic"]
        # --- визуально: красная активная кнопка ---
        _old = (mic.icon, mic.bgcolor, mic.icon_color, mic.tooltip)
        try:
            mic.icon = ft.Icons.MIC_ROUNDED
            mic.bgcolor = "#EF5350"
            mic.icon_color = "white"
            mic.tooltip = tr("mic_stop_title")
            mic.update()
        except Exception: pass
        status.value = tr("mic_listening"); status.color = "#EF5350"; page.update()
        try:
            text = await asyncio.get_running_loop().run_in_executor(
                None, listen, "ru-RU" if CUR["lang"] == "ru" else "en-US")
            inp.value = ((inp.value or "") + " " + text).strip()
            inp.update(); on_inp(None)
        except Exception as ex: show_e(voice_err(ex))
        finally:  # --- возврат к базовому виду ---
            try:
                mic.icon, mic.bgcolor, mic.icon_color, mic.tooltip = _old
                mic.update()
            except Exception: pass
            status.value = ""; status.color = th["muted"]; page.update()
    UI["mic"] = ft.IconButton(ft.Icons.MIC_OUTLINED, tooltip=tr("stt_mic"), on_click=do_listen)
    btn_stop = ft.IconButton(ft.Icons.STOP_CIRCLE_OUTLINED, tooltip=tr("stop"), on_click=do_stop,
        visible=False, style=ft.ButtonStyle(color="#EF5350"))

    async def handsfree_loop():
        """№9: голосовой диалог без рук: слушаю → отправляю → озвучиваю → по кругу."""
        try:
            from voice import listen, speak  # type: ignore  (availability probe)
        except ImportError:
            show_e(tr("e_no_stt")); state["handsfree"] = False; return
        mic = UI["mic"]
        try:
            mic.icon = ft.Icons.MIC_ROUNDED; mic.bgcolor = "#EF5350"; mic.icon_color = "white"
            mic.tooltip = tr("handsfree_stop"); mic.update()
        except Exception: pass
        loop = asyncio.get_running_loop()
        lang = "ru-RU" if CUR["lang"] == "ru" else "en-US"
        status.value = tr("handsfree_on"); status.color = "#EF5350"; page.update()
        try:
            while state.get("handsfree"):
                status.value = tr("mic_listening"); status.color = "#EF5350"; page.update()
                try:
                    text = await loop.run_in_executor(None, listen, lang)
                except Exception as ex:
                    if state.get("handsfree"):
                        show_e(voice_err(ex))
                    break
                if not state.get("handsfree"):
                    break
                text = (text or "").strip()
                if not text:
                    continue
                inp.value = text; inp.update(); on_inp(None)
                await send()  # генерация + автоназвание как обычно
                if not state.get("handsfree"):
                    break
                last = next((m for m in reversed(state["msgs"]) if not m.is_user and m.text), None)
                if last is None:
                    continue
                status.value = tr("tts_playing", i=1, n=1); page.update()
                try:
                    await loop.run_in_executor(None, speak, last.text, CUR["lang"])
                except Exception as ex:
                    show_e(voice_err(ex)); break
        finally:
            state["handsfree"] = False
            try:
                mic.icon = ft.Icons.MIC_OUTLINED; mic.bgcolor = None; mic.icon_color = None
                mic.tooltip = tr("stt_mic"); mic.update()
            except Exception: pass
            status.value = ""; status.color = th["muted"]; page.update()

    def toggle_handsfree(e=None):
        if state.get("handsfree"):  # выключить: флаг + стоп всего звучащего/генерируемого
            state["handsfree"] = False
            try:
                from voice import stop_playback  # type: ignore
                stop_playback()
            except Exception: pass
            try: client.cancel()
            except Exception: pass
            status.value = ""; page.update()
        else:
            if state.get("sending"):
                show_e(tr("e_busy")); return
            try: hide_e()
            except Exception: pass
            state["handsfree"] = True
            try: asyncio.get_running_loop().create_task(handsfree_loop())
            except Exception as ex: show_e(str(ex)); state["handsfree"] = False
    dock = ft.Container(bgcolor=th["input_bg"], border_radius=S["radius"] + 4, padding=8,
        border=ft.Border.all(1, th["border"]),
        shadow=ft.BoxShadow(blur_radius=12, color=th["shadow"], offset=ft.Offset(0, -2)),
        content=ft.Column(spacing=4, controls=[
            attach_row,
            ft.Row([UI["attach"],
                    inp, UI["mic"], btn_stop, btn_send],
                   vertical_alignment=ft.CrossAxisAlignment.END),
            ft.Row([status, ft.Container(expand=True), counter],
                   vertical_alignment=ft.CrossAxisAlignment.CENTER)]))
    chat_wrap = ft.Container(content=chat_box, expand=True)
    page.add(ft.Row([sidebar,
                     ft.Column([topbar, settings_p, err, chat_wrap, dock],
                               expand=True, spacing=S["gap"])],
                    expand=True, vertical_alignment=ft.CrossAxisAlignment.START))
    def on_keyboard(e):
        k = (e.key or "").lower()
        if e.ctrl and k == "enter":
            asyncio.create_task(send())
        elif e.ctrl and k == "k":
            new_chat()
        elif e.ctrl and getattr(e, "shift", False) and k == "f":
            find_all_chats()  # глобальный поиск по всем чатам
        elif e.ctrl and k == "f":
            find_in_chat()
        elif e.ctrl and k == "v":
            paste_image()  # №5: картинка из буфера; текст вставится сам
        elif k == "escape" and state.get("sending"):
            do_stop()
    page.on_keyboard_event = on_keyboard
    def stop_lm_server():
        """Остановить сервер LM Studio через CLI (best effort, только если lms доступен)."""
        import shutil, subprocess
        lms = shutil.which("lms")
        if not lms:
            _log.info("lms CLI not found — server left running")
            return
        try:
            r = subprocess.run([lms, "server", "stop"], timeout=15, capture_output=True, text=True)
            _log.info("lms server stop: %s", (r.stdout or r.stderr or "ok").strip()[:200])
        except Exception as ex:
            _log.warning("lms server stop failed: %s", ex)

    def unload_via_cli(model_id: str | None = None) -> bool:
        """Fallback: выгрузка через `lms unload`, если HTTP API не сработал."""
        import shutil, subprocess
        lms = shutil.which("lms")
        if not lms:
            return False
        # сначала точечно модель, потом всё остальное
        cmds = []
        if model_id:
            cmds.append([lms, "unload", model_id])
        cmds.append([lms, "unload", "--all"])
        for cmd in cmds:
            try:
                r = subprocess.run(cmd, timeout=30, capture_output=True, text=True)
                _log.info("lms %s: %s", " ".join(cmd[1:]), (r.stdout or r.stderr or "ok").strip()[:200])
                if r.returncode == 0:
                    return True
            except Exception as ex:
                _log.warning("lms unload failed: %s", ex)
        return False

    def unload_sync_best_effort(model_id: str | None) -> bool:
        """Синхронная выгрузка при закрытии: без event loop.

        Flet при закрытии окна отменяет задачи и гасит loop — async unload
        до сервера не доходит (см. CancelledError в логах). Синхронный
        httpx + subprocess работают и после смерти loop.
        """
        if not model_id:
            return False
        try:  # 1) HTTP API синхронно
            import httpx as _hx
            _be = backend_from_settings(settings)
            _base = base_url_from_settings(settings)
            root = _base[:-3] if _base.endswith("/v1") else _base
            with _hx.Client(timeout=10.0) as c:
                if _be == "strata":
                    try:
                        r = c.post(root + "/unload", json={})
                        if r.status_code < 400:
                            _log.info("sync-unloaded strata model on exit")
                            return True
                    except Exception:
                        pass
                else:
                    for payload in ({"instance_id": model_id}, {"model": model_id}):
                        try:
                            r = c.post(root + "/api/v1/models/unload", json=payload)
                            if r.status_code < 400:
                                _log.info("sync-unloaded model on exit: %s", model_id)
                                return True
                        except Exception:
                            continue
        except Exception as ex:
            _log.warning("sync API unload failed: %s", ex)
        try:  # 2) CLI — переживает смерть loop
            return bool(unload_via_cli(model_id))
        except Exception as ex:
            _log.warning("sync CLI unload failed: %s", ex)
        return False

    async def _exit_network():
        """Выгрузка модели + стоп сервера. Вызывается под shield — переживает отмену задачи.

        Важно: шаги независимые — отмена/падение одного не пропускает
        остальные (CLI через потоки работает даже при мёртвом loop).
        """
        lm = state.get("loaded_model") or (model_dd.value or None)
        if lm and not state.get("exit_unloaded"):
            try:  # 1) API unload (best effort)
                await asyncio.wait_for(client.unload_model(lm, backend_from_settings(settings)), timeout=15)
                _log.info("unloaded model on exit: %s", lm)
                state["loaded_model"] = None
                state["exit_unloaded"] = True
            except BaseException as ex:
                _log.warning("unload on exit via API failed (%r) — пробую lms unload", ex)
        if lm and not state.get("exit_unloaded"):
            try:  # 2) CLI unload — отдельно, переживает смерть loop
                if await asyncio.get_running_loop().run_in_executor(None, unload_via_cli, lm):
                    state["exit_unloaded"] = True
            except BaseException as ex2:
                _log.warning("unload via CLI failed (%r)", ex2)
        state["loaded_model"] = None
        # клиент — ДО стопа сервера: закрываем keep-alive соединения чисто,
        # иначе Windows-проактор орёт ConnectionResetError при смерти сервера
        try: await client.close()
        except BaseException: pass
        try:
            await asyncio.get_running_loop().run_in_executor(None, stop_lm_server)
        except BaseException as ex:
            _log.warning("server stop on exit failed (%r)", ex)

    async def on_app_close(e=None):
        _log.info("app closing")
        try:  # 0) выгрузка модели — ПЕРВЫМ делом, синхронно: loop может умереть
            lm0 = state.get("loaded_model") or (model_dd.value or None)
            if lm0 and not state.get("exit_unloaded"):
                try:
                    state["exit_unloaded"] = bool(unload_sync_best_effort(lm0))
                    if state["exit_unloaded"]:
                        state["loaded_model"] = None
                except Exception as ex:
                    _log.warning("sync unload failed: %s", ex)
        except Exception:
            pass
        try:  # 1) геометрия окна — быстро и синхронно, первым делом
            try: settings["window_maximized"] = bool(page.window.maximized)
            except Exception: pass
            if not settings.get("window_maximized"):
                if page.window.width: settings["window_width"] = int(page.window.width)
                if page.window.height: settings["window_height"] = int(page.window.height)
                if page.window.left is not None: settings["window_left"] = int(page.window.left)
                if page.window.top is not None: settings["window_top"] = int(page.window.top)
            _log.info("saved geometry: %sx%s max=%s", settings.get("window_width"),
                      settings.get("window_height"), settings.get("window_maximized"))
        except Exception as ex:
            _log.warning("save window geometry failed: %s", ex)
        try:  # 2) остальные настройки
            persist()
        except Exception as ex:
            _log.warning("persist on exit failed: %s", ex)
        try:  # 3) сеть под shield — flet отменяет задачу при закрытии окна
            await asyncio.shield(_exit_network())
        except BaseException as ex:
            _log.warning("exit network cancelled: %s", type(ex).__name__)
        _log.info("exit handler done")
    page.on_close = on_app_close

    # шаг 6: flet-обновления едут от стора через подписчика (views.ViewBinder)
    if store is not None:
        try: _ViewBinder(store, on_tokens=upd_tokens).bind()
        except Exception: _log.exception("view binder failed")

    idx = load_index()
    if not idx: new_chat()
    else: open_chat(idx[0]["id"])
    refresh_folders(); refresh_sidebar(); refresh_profiles(); await load_models()
    try: asyncio.get_running_loop().create_task(health_loop())  # №10: мониторинг сервера
    except Exception as ex: _log.warning("health monitor not started: %s", ex)

if __name__ == "__main__":
    ft.run(main)
