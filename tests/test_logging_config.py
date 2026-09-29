from __future__ import annotations
import logging

import logging_config


def test_debug_clamps_http_client_loggers(monkeypatch, tmp_path):
    """LOG_LEVEL=DEBUG не должен сливать заголовки httpcore/httpx:
    на DEBUG эти библиотеки пишут полный Authorization: Bearer <ключ>
    в data/logs/*.log — прижимаем их к INFO, свой лог остаётся DEBUG."""
    monkeypatch.setattr(logging_config, "_INITIALIZED", False)
    root = logging.getLogger()
    saved_level, saved_handlers = root.level, list(root.handlers)
    try:
        logging_config.setup_logging(level="DEBUG", log_file=tmp_path / "app.log")
        assert root.level == logging.DEBUG  # сам лог по-прежнему DEBUG
        for lib in ("httpcore", "httpx"):
            assert logging.getLogger(lib).getEffectiveLevel() >= logging.INFO, lib
    finally:
        root.setLevel(saved_level)
        for h in list(root.handlers):
            root.removeHandler(h)
            if h not in saved_handlers:
                h.close()
        for h in saved_handlers:
            root.addHandler(h)


def test_info_keeps_http_client_logging_level(monkeypatch, tmp_path):
    """На INFO (и выше) уровни библиотек не трогаем ниже фактического."""
    monkeypatch.setattr(logging_config, "_INITIALIZED", False)
    root = logging.getLogger()
    saved_level, saved_handlers = root.level, list(root.handlers)
    try:
        logging_config.setup_logging(level="WARNING", log_file=tmp_path / "app.log")
        for lib in ("httpcore", "httpx"):
            lg = logging.getLogger(lib)
            assert lg.getEffectiveLevel() >= logging.WARNING
    finally:
        root.setLevel(saved_level)
        for h in list(root.handlers):
            root.removeHandler(h)
            if h not in saved_handlers:
                h.close()
        for h in saved_handlers:
            root.addHandler(h)