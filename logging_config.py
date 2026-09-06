"""Centralized logging configuration (Roadmap step 1: Foundation).

Usage:
    from logging_config import setup_logging, get_logger
    setup_logging()  # once, at startup
    log = get_logger(__name__)
"""
from __future__ import annotations
import logging
import os
from logging.handlers import RotatingFileHandler
from pathlib import Path

_INITIALIZED = False


def setup_logging(level: str | int | None = None, log_file: str | Path | None = None) -> logging.Logger:
    """Configure root logger: console + rotating file. Idempotent."""
    global _INITIALIZED
    lvl = level or os.getenv("LOG_LEVEL", "INFO")
    if isinstance(lvl, str):
        lvl = getattr(logging, lvl.upper(), logging.INFO)
    log_path = Path(log_file) if log_file else Path(__file__).parent / "data" / "app.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)

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
        fh = RotatingFileHandler(log_path, maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
        fh.setLevel(lvl)
        fh.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(name)s | %(funcName)s:%(lineno)d | %(message)s"))
        root.addHandler(fh)
    except OSError:
        pass  # file logging is best-effort

    _INITIALIZED = True
    return logging.getLogger("app")


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
