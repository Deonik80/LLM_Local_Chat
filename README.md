# 🤖 LM Studio Chat

---
🇺🇸 English | [🇷🇺 На русском языке](README.ru.md)
---

A chat client built with **Python + Flet** for interacting with local LLMs via the **LM Studio** API.
---

## ✨ Key Features

*   **📄 Local Document Reading (RAG-ready):** Automatic text extraction from `.pdf`, `.docx`, `.xlsx`, `.csv`, `.txt`, `.py`, and `.md` files with smart context limit management.
*   **🖼️ Multimodality Support (Vision):** Attach images (`png`, `jpg`, `webp`, `gif`) with auto-detection of whether the current model supports vision (includes automatic fallback to text mode on HTTP 400 errors).
*   **🎭 Preset & Profile System:** Quickly switch between system prompts (Translator, Code Review, etc.). Create complete profiles that link a specific model, prompt, temperature, `top_p`, `seed`, and `context_length` in a single click.
*   **📦 Project Environment Loading:** Ability to load `requirements.txt` and `.env` files directly into the model's system context for precise development.
*   **🔄 Generation Management:** Streaming output with throttling for smoothness, along with an immediate stop button. Visible generation status (`Thinking…` → `Typing…`) in the bubble and status bar. Support for regenerating responses and storing alternative text options (Variants).
*   **📈 Token Control & Compression:** Visual progress bar tracking context utilization. Automatic or manual dialogue history compression (Summarization) when context limits are reached.
*   **🗣️ Voice Interface:** Voice message input (STT) with a red recording indicator on the mic button, and assistant response read-aloud (TTS, `edge-tts` online or `pyttsx3` offline, in-app playback).
*   **📦 Auto-dependencies:** On startup the app checks `pypdf` / `python-docx` / `openpyxl` and installs anything missing with the same Python interpreter; `run_app.bat` additionally runs `pip install -r requirements.txt`.
*   **📊 Feedback Loop & Export:** Rate messages to create a feedback loop with log exporting in `JSONL` format for subsequent fine-tuning. Export dialogues to `Markdown` and `HTML`.
*   **🌐 Localization & Themes:** Full support for English and Russian (i18n), featuring adaptive Dark and Light UI themes.
*   **🧹 Clean Exit:** Automatically unloads the model from LM Studio's memory when the application closes to save GPU resources.

---

## 💻 Tech Stack

*   **UI Framework:** [Flet](https://flet.dev) (Flutter for Python)
*   **Network Client:** Async HTTP requests via [Httpx](https://python-httpx.org)
*   **Parsers:** `pypdf`, `python-docx`, `openpyxl`, `csv`
*   **Asynchrony:** `asyncio`

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
pip install "flet==0.86.5" httpx pydantic

# For reading PDF, Word, and Excel documents
pip install pypdf python-docx openpyxl

# For voice input and text-to-speech (optional)
pip install edge-tts pyttsx3 pygame SpeechRecognition PyAudio
```
*(Note: The Flet version in the code is strictly locked to the 0.86.5 architecture. Missing document parsers (`pypdf`, `python-docx`, `openpyxl`) are also auto-installed on app startup.)*

### Environment Variables (Optional)

You can override the default settings using environment variables or a `.env` file:
*   `LM_STUDIO_URL` — Base API URL (default: `http://localhost:1234/v1`).
*   `DEFAULT_MODEL` — The default model to load.
*   `REQUEST_TIMEOUT` — Request timeout in seconds (default: `180`).
*   `CONTEXT_LENGTH` — Default context size (default: `8192`).
*   `MAX_IMAGE_MB` / `MAX_TEXT_MB` — Size limits for attachments.

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
*   `data/app.log` — Application log.
*   `data/index.json` — Global chat list for the sidebar.
*   `data/settings.json` — UI configuration, themes, language, and recent slider parameters.
*   `data/presets.json` — User-defined system prompts.
*   `data/profiles.json` — Your saved profile configurations.

---

## ⌨️ Hotkeys

*   `Ctrl + Enter` — Fast message submission from the input field.
*   `Shift + Enter` — Newline in the input field.
*   `Ctrl + K` — Create a new empty chat.
*   `Ctrl + F` — Open full-text search within the current dialogue.
*   `Escape` — Interrupt the current response generation (Stop).

---

## 📄 License

This project is distributed under the **GNU GPLv3** license. For more details, see the [LICENSE](LICENSE) file.
