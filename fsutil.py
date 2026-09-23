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

"""Safe file helpers: atomic text writes + tolerant JSON loads.

Atomic write (tmp file in the same directory + ``os.replace``) guarantees
that a crash mid-save never leaves a half-written JSON behind: readers
always see either the old complete file or the new complete file.

Windows caveat: ``os.replace`` needs exclusive rename rights on the target,
so any open handle to it (a concurrent reader, antivirus scan) fails with
``WinError 32`` (sharing violation). We therefore retry the replace with a
short backoff and, as a last resort, overwrite the file in place — a
non-atomic but rarely-hit fallback that beats crashing the save. Leftover
``*.tmp`` files are always removed.

On a corrupt/unreadable JSON, ``read_json`` keeps a one-off ``*.corrupt``
copy for manual recovery and returns the supplied default instead of
silently destroying data on the next save.
"""
from __future__ import annotations
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

_log = logging.getLogger("fsutil")

# ретраи os.replace: хэндл читателя обычно живёт миллисекунды
_REPLACE_RETRIES = 8
_REPLACE_DELAY = 0.03  # сек, линейный backoff → ~1 с суммарно


def _is_sharing_violation(ex: OSError) -> bool:
    """WinError 32/33 (file busy) — как OSError/PermissionError на Windows."""
    if isinstance(ex, PermissionError):
        return True
    return getattr(ex, "winerror", None) in (32, 33)


def _replace_with_retry(tmp: Path | str, path: Path | str) -> None:
    """``os.replace`` с ретраями при sharing violation; поднимает последнюю ошибку."""
    last: OSError | None = None
    for i in range(_REPLACE_RETRIES):
        try:
            os.replace(tmp, path)
            return
        except OSError as ex:
            if not _is_sharing_violation(ex):
                raise
            last = ex
            time.sleep(_REPLACE_DELAY * (i + 1))
    assert last is not None
    raise last


def _write_in_place(path: Path, data: bytes) -> None:
    """Фоллбэк: запись поверх файла без переименования (не атомарно)."""
    with open(path, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())


def atomic_write_text(path: str | Path, text: str, encoding: str = "utf-8") -> None:
    """Write ``text`` to ``path`` atomically (never a partial file).

    На Windows переживает занятый целевой файл: ретраи ``os.replace``,
    затем прямая запись поверх. ``*.tmp`` удаляется в любом случае.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.encode(encoding)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            _replace_with_retry(tmp, path)
        except OSError as ex:
            _log.warning("atomic replace of %s failed (%s); writing in place", path, ex)
            _write_in_place(path, data)
    finally:
        try:
            os.unlink(tmp)  # FileNotFoundError, если replace уже забрал tmp
        except OSError:
            pass


def write_json(path: str | Path, data: Any, indent: int = 2) -> None:
    """Serialize ``data`` as UTF-8 JSON and write it atomically."""
    atomic_write_text(path, json.dumps(data, ensure_ascii=False, indent=indent))


def preserve_corrupt(path: str | Path) -> Path | None:
    """Copy a broken file aside as ``<name>.corrupt`` (first time only)."""
    path = Path(path)
    side = path.with_name(path.name + ".corrupt")
    if side.exists():
        return side
    try:
        side.write_bytes(path.read_bytes())
        _log.error("corrupt file preserved as %s", side)
        return side
    except OSError as ex:
        _log.error("could not preserve corrupt file %s: %s", path, ex)
        return None


def read_json(path: str | Path, default: Any) -> Any:
    """Load JSON from ``path``; on any problem preserve it and return ``default``."""
    path = Path(path)
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as ex:
        _log.error("corrupt JSON in %s: %s", path, ex)
        preserve_corrupt(path)
        return default
    except OSError as ex:
        _log.error("cannot read %s: %s", path, ex)
        return default
