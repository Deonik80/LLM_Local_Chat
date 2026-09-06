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

"""Voice input/output with optional dependencies (graceful degradation).

- listen(): microphone -> text via SpeechRecognition (needs `SpeechRecognition`
  + `PyAudio`; recognition via Google, needs internet).
- speak(): text -> audio, edge-tts if installed, else pyttsx3 (offline SAPI).

All failures raise VoiceError with a human-readable hint (what to pip-install).
Markdown is stripped before speaking.
"""
from __future__ import annotations
import re


class VoiceError(Exception): pass


def clean_for_speech(text: str) -> str:
    t = text or ""
    t = re.sub(r"```.*?```", " ", t, flags=re.S)      # code blocks
    t = re.sub(r"`([^`]*)`", r"\1", t)                 # inline code
    t = re.sub(r"!\[([^\]]*)\]\([^)]*\)", r"\1", t)    # images
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)     # links -> text
    t = re.sub(r"[#>*_\-~|]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t[:2000]


def listen(lang: str = "ru-RU", timeout: int = 8) -> str:
    try:
        import speech_recognition as sr
    except ImportError:
        raise VoiceError("Нет пакета SpeechRecognition: pip install SpeechRecognition PyAudio")
    try:
        r = sr.Recognizer()
        with sr.Microphone() as src:
            r.adjust_for_ambient_noise(src, duration=0.5)
            audio = r.listen(src, timeout=timeout, phrase_time_limit=30)
    except Exception as e:
        raise VoiceError(f"Микрофон недоступен: {e}")
    try:
        return r.recognize_google(audio, language=lang)
    except Exception as e:
        raise VoiceError(f"Не распознано: {e}")


_PG_OK = False  # pygame mixer инициализирован

TTS_DIR = None  # лениво: data/tts рядом с проектом


def _tts_dir():
    global TTS_DIR
    if TTS_DIR is None:
        from pathlib import Path as _P
        TTS_DIR = _P(__file__).parent / "data" / "tts"
        TTS_DIR.mkdir(parents=True, exist_ok=True)
        # подчистить огрызки прошлых запусков
        for f in TTS_DIR.glob("tts_*.mp3"):
            try: f.unlink()
            except OSError: pass
    return TTS_DIR


def _pg_ensure() -> bool:
    """Инициализировать pygame mixer один раз. True — можно играть внутри приложения."""
    global _PG_OK
    if _PG_OK:
        return True
    try:
        import pygame as _pg
        _pg.mixer.init()
        _PG_OK = True
        return True
    except Exception:
        return False


def is_playing() -> bool:
    try:
        import pygame as _pg
        return bool(_PG_OK and _pg.mixer.music.get_busy())
    except Exception:
        return False


def stop_playback():
    try:
        import pygame as _pg
        if _PG_OK:
            _pg.mixer.music.stop()
    except Exception:
        pass


def _play_file(path: str):
    """Проиграть внутри приложения (pygame); системный плеер — последний fallback."""
    try:
        if _pg_ensure():
            import pygame as _pg
            import time as _t
            try:
                _pg.mixer.music.load(path)
                _pg.mixer.music.play()
                while _pg.mixer.music.get_busy():
                    _t.sleep(0.2)
            finally:
                try: _pg.mixer.music.unload()  # отпустить файловый хендл (Windows)
                except Exception: pass
            return
    except Exception:
        pass
    try:
        import playsound as _ps
        _ps.playsound(path)
        return
    except ImportError: pass
    import os as _os, subprocess as _sp, sys as _sys
    if _sys.platform.startswith("win"):
        _os.startfile(path)  # type: ignore
    elif _sys.platform == "darwin":
        _sp.run(["afplay", path], check=False)
    else:
        _sp.run(["xdg-open", path], check=False)


def speak(text: str, lang: str = "ru"):
    clean = clean_for_speech(text)
    if not clean:
        raise VoiceError("Нечего озвучивать")
    # 1) edge-tts (качественно, нужен интернет): mp3 в data/tts + проигрывание
    try:
        import asyncio as _aio
        import edge_tts as _edge
        import time as _t

        async def _run(p):
            voice = "ru-RU-SvetlanaNeural" if lang.startswith("ru") else "en-US-AriaNeural"
            await _edge.Communicate(clean, voice).save(p)

        out = str(_tts_dir() / f"tts_{int(_t.time() * 1000)}.mp3")  # уникальное имя
        try:
            _aio.run(_run(out))
            _play_file(out)
        finally:
            try:
                import os as _os
                _os.remove(out)  # не копим файлы: один синтез — один файл
            except OSError: pass
        return
    except ImportError:
        pass
    except Exception as e:
        raise VoiceError(f"edge-tts: {e}")
    # 2) pyttsx3 (офлайн, системный голос)
    try:
        import pyttsx3 as _px
    except ImportError:
        raise VoiceError("Нет TTS-движка: pip install edge-tts (или pyttsx3 для офлайна)")
    try:
        eng = _px.init()
        eng.setProperty("rate", 175)
        eng.say(clean)
        eng.runAndWait()
    except Exception as e:
        raise VoiceError(f"pyttsx3: {e}")
