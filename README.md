<div align="center">
  <h1 align="center">
LM Studio Chat
</h1>
<div align="center">

<div align="center">

English | [Русский](./README.ru.md)

</div>


A chat client built with **Python + Flet** for interacting with local LLMs via the **LM Studio** API.
---
<picture>
  <div align="center">
<img width="800" height="771" alt="1" src="https://github.com/user-attachments/assets/5b6f49d7-0c70-454c-9643-48bd3113d542" />
    </div>
</picture>

## ✨ Key Features

*   **📄 Local Document Reading (RAG-ready):** Automatic text extraction from `.pdf`, `.docx`, `.xlsx`, `.csv`, `.txt`, `.py`, and `.md` files with smart context limit management.
*   **🖼️ Multimodality Support (Vision):** Attach images (`png`, `jpg`, `webp`, `gif`) with auto-detection of whether the current model supports vision (includes automatic fallback to text mode on HTTP 400 errors). Paste screenshots straight from the clipboard with `Ctrl + V`.
*   **🎭 Preset & Profile System:** Quickly switch between system prompts (Translator, Code Review, etc.). Create complete profiles that link a specific model, prompt, temperature, `seed`, and `context_length` in a single click.
*   **📦 Project Environment Loading:** Ability to load `requirements.txt` and `.env` files directly into the model's system context for precise development. Secret-like variables (names containing `KEY` / `TOKEN` / `SECRET` / `PASSWORD`, etc.) are masked as `***` and never reach the model.
*   **🔄 Generation Management:** Streaming output with throttling for smoothness, along with an immediate stop button. Visible generation status (`Thinking…` → `Typing…`) in the bubble and status bar. Interrupted answers can be resumed with **Continue**; any message can start a new **branch** (the discarded tail is kept as Variants). Your own sent message can be **edited and resent** — the old replies move into the new answer's Variants. Support for regenerating responses and storing alternative text options (Variants).
*   **📈 Token Control & Compression:** Visual progress bar tracking context utilization. Every answer shows its cost (`⚡ N tok · X tok/s · Ys`). Automatic or manual dialogue history compression (Summarization) when context limits are reached. `Context Length` is a slider whose maximum is pulled from the loaded model.
*   **📌 Sidebar: Folders, Pins & Previews:** Pin important chats to the top; sort chats into **folders** (chip filters "All + folders" under the search box, a folder button on every chat row, a `folder` field in `index.json`). Every chat shows a preview of its last message and a message count. The model suggests a short title for new chats in the background (manual renames are never overwritten).
*   **🔍 Search:** Full-text search inside the current chat (`Ctrl + F`) and **search across all chats** (`Ctrl + Shift + F` or the "⋯" menu) — over titles and message bodies; clicking a result opens the chat with the filter applied.
*   **🗣️ Voice Interface:** Voice message input (STT) with a red recording indicator on the mic button, assistant response read-aloud (TTS, `edge-tts` online or `pyttsx3` offline, in-app playback, long answers are synthesized in chunks with progress), and a hands-free dialogue mode (listen → answer → speak, in a loop).
*   **🖥️ Server Handling:** On startup the app adopts the model already loaded on the server instead of loading a second one; a background health check watches the server (red/green status dot) with one-click restart via `lms`.
*   **📦 Dependency checks:** On startup the app checks `pypdf` / `python-docx` / `openpyxl` / `pillow` and prints what is missing (no runtime auto-install — the environment is never mutated); `run_app.bat` runs `pip install -r requirements.txt`.
*   **📊 Feedback Loop & Export:** Rate messages to create a feedback loop with log exporting in `JSONL` format for subsequent fine-tuning. Export dialogues (whole chat or ticked messages only) to `Markdown`, `HTML`, and `JSON`.
*   **🌐 Localization & Themes:** Full support for English and Russian (all strings live in `locales/en.json` and `locales/ru.json` — a new language is just a file; voice-mode errors are translated too), featuring adaptive Dark and Light UI themes.
*   **🧹 Clean Exit:** Automatically unloads the model from LM Studio's memory when the application closes to save GPU resources.

---

## 💻 Tech Stack

*   **UI Framework:** [Flet](https://flet.dev) (Flutter for Python)
*   **Network Client:** Async HTTP requests via [Httpx](https://python-httpx.org)
*   **Parsers:** `pypdf`, `python-docx`, `openpyxl`, `csv`, `pillow`
*   **Asynchrony:** `asyncio`
*   **Code Quality:** `pytest` + `ruff` + `mypy` + `pytest-cov` (coverage gate in CI), `tests/`, `pyproject.toml`, GitHub Actions CI (Python 3.9–3.13)

---
<img width="800" height="771" alt="2" src="https://github.com/user-attachments/assets/04dc902d-fd87-4775-8ab1-872839137336" />
<img width="800" height="771" alt="3" src="https://github.com/user-attachments/assets/b2968e48-ddc5-4987-9221-6129984ee63d" />
<img width="800" height="771" alt="4" src="https://github.com/user-attachments/assets/71555ac3-05fd-464c-9532-fa66c9ed578a" />
---

## 🚀 Quick Start

Via the `run_app.bat` batch file (automatically launches the LM Studio server and starts the chat).

### Requirements

Before running the application, make sure you have:
1.  **Python 3.9 or higher**
2.  A running **LM Studio** server (defaults to `http://localhost:1234/v1`). Running the project via `run_app.bat` will start the LM Studio server automatically alongside the chat.

### Installing Dependencies

Clone the repository and install the base packages, along with modules for document processing and voice features:

```bash
git clone https://github.com/Deonik80/LLM_Local_Chat.git
cd LLM_Local_Chat

# All dependencies at once (recommended)
pip install -r requirements.txt

# Or manually:
# Base
pip install "flet==0.86.5" httpx pydantic pillow

# For reading PDF, Word, and Excel documents
pip install pypdf python-docx openpyxl

# For voice input and text-to-speech (optional)
pip install edge-tts pyttsx3 pygame SpeechRecognition PyAudio
```
*(Note: The Flet version in the code is strictly locked to the 0.86.5 architecture. If helpers (`pypdf`, `python-docx`, `openpyxl`, `pillow`) are missing, the app still starts and prints the install command — there is no runtime auto-install.)*

### Environment Variables (Optional)

You can override the default settings using environment variables or a `.env` file:
*   `LM_STUDIO_URL` — Base API URL (default: `http://localhost:1234/v1`).
*   `DEFAULT_MODEL` — The default model to load.
*   `REQUEST_TIMEOUT` — Request timeout in seconds (default: `180`).
*   `CONTEXT_LENGTH` — Default context size (default: `8192`).
*   `MAX_IMAGE_MB` / `MAX_TEXT_MB` — Size limits for attachments.
*   `LOG_LEVEL` / `LOG_KEEP` — Log verbosity and how many per-run log files to keep (default: `INFO` / `20`).

### Running the Application

```bash
python.exe app.py
```
Or simply run the `run_app.bat` file.

---

## 📂 Application Data Structure

After the first launch, the application will create a `data/` directory in the root folder with the following structure:
*   `data/chats/` — Message history in JSON format (one file per chat).
*   `data/attachments/` — Cached copies of attached files.
*   `data/tts/` — Temporary TTS audio files (cleaned automatically).
*   `data/logs/` — One log file per run (`app-YYYYMMDD-HHMMSS.log`, keeps the newest 20, `LOG_KEEP` overrides).
*   `data/index.json` — Global chat list for the sidebar: title, pinned flag, folder, preview of the last message, and message count.
*   `data/settings.json` — UI configuration, themes, language, and recent slider parameters.
*   `data/presets.json` — User-defined system prompts.
*   `data/profiles.json` — Your saved profile configurations.

All JSON writes are atomic (temp file + rename, with retries on Windows if the file is busy), so a mid-save crash never leaves a half-written file; an unreadable JSON is preserved next to it as `*.corrupt` for manual recovery. Stale `*.tmp` files do not accumulate.

---

## ⌨️ Hotkeys

*   `Ctrl + Enter` — Fast message submission from the input field.
*   `Shift + Enter` — Newline in the input field.
*   `Ctrl + K` — Create a new empty chat.
*   `Ctrl + F` — Open full-text search within the current dialogue.
*   `Ctrl + Shift + F` — Global search across all chats (titles and messages).
*   `Ctrl + V` — Paste an image from the clipboard as an attachment.
*   `Escape` — Interrupt the current response generation (Stop).

---

## 🛠 Development

The same lint, type, test, and coverage commands run in CI (GitHub Actions, Python 3.9–3.13):

```bash
pip install -r requirements-dev.txt   # pytest + ruff + mypy + pytest-cov
python -m pytest                      # unit tests for the pure modules
python -m ruff check .                # linter
python -m mypy                        # types (chat_store, repositories, fsutil, api_payload, voice, models)
python -m pytest --cov --cov-report=term --cov-fail-under=75   # coverage (ratchet: the gate only goes up)
```

Linter/type/test/coverage configuration lives in `pyproject.toml`; the CI workflow is in `.github/workflows/ci.yml`. The coverage threshold is a ratchet: the measured floor (75%+) must not drop, and new tests raise it further.

---

## 📄 License

This project is distributed under the **GNU GPLv3** license. For more details, see the [LICENSE](LICENSE) file.
