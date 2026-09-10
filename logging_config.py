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

"""Centralized logging configuration (Roadmap step 1: Foundation).

Каждый запуск пишет свой файл: data/logs/app-YYYYMMDD-HHMMSS.log
(старые чистятся, держать LOG_KEEP штук). Ориентироваться по сессиям
проще, чем по одному общему app.log.

Usage:
    from logging_config import setup_logging, get_logger
    setup_logging()  # once, at startup
    log = get_logger(__name__)
"""
from __future__ import annotations
import logging
import os
import time
from pathlib import Path

_INITIALIZED = False


def _prune_old_logs(log_dir: Path, keep: int) -> None:
    """Удалить самые старые логи, оставив `keep` свежих. Best effort."""
    try:
        files = sorted(log_dir.glob("app-*.log"), key=lambda p: p.name)
        for old in files[:-keep] if len(files) > keep else []:
            try:
                old.unlink()
            except OSError:
                pass
    except OSError:
        pass


def setup_logging(level: str | int | None = None, log_file: str | Path | None = None,
                  keep: int | None = None) -> logging.Logger:
    """Configure root logger: console + per-run file. Idempotent."""
    global _INITIALIZED
    lvl = level or os.getenv("LOG_LEVEL", "INFO")
    if isinstance(lvl, str):
        lvl = getattr(logging, lvl.upper(), logging.INFO)
    if log_file is not None:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
    else:
        log_dir = Path(__file__).parent / "data" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        log_path = log_dir / f"app-{stamp}.log"
        # одинаковый штамп при быстрых перезапусках — добавить суффикс
        n = 1
        while log_path.exists():
            n += 1
            log_path = log_dir / f"app-{stamp}-{n}.log"
        try:
            keep_n = keep if keep is not None else int(os.getenv("LOG_KEEP", "20"))
        except ValueError:
            keep_n = 20
        _prune_old_logs(log_dir, max(1, keep_n))

    root = logging.getLogger()
    root.setLevel(lvl)
    if _INITIALIZED:
        return logging.getLogger("app")

    fmt = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s", "%H:%M:%S")
    console = logging.StreamHandler()
    console.setLevel(lvl)
    console.setFormatter(fmt)
    root.addHandler(console)

    try:
        fh = logging.FileHandler(log_path, encoding="utf-8")
        fh.setLevel(lvl)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"))
        root.addHandler(fh)
    except OSError:
        pass  # file logging is best-effort

    _INITIALIZED = True
    app_log = logging.getLogger("app")
    app_log.info("=== session start: %s ===", log_path.name)
    return app_log


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
