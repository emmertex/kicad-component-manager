"""Atomic file writes: temp file in the same directory + os.replace."""

from __future__ import annotations

import contextlib
import os
import stat
import tempfile
from pathlib import Path


def _new_tmp(path: Path, mode: int | None) -> tuple[int, str]:
    """Temp file next to ``path``; keeps the target's mode (0644 if new)."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    if mode is None:
        try:
            mode = stat.S_IMODE(path.stat().st_mode)
        except OSError:
            mode = 0o644
    os.fchmod(fd, mode)
    return fd, tmp


def atomic_write_text(
    path, content: str, encoding: str = "utf-8", mode: int | None = None
):
    """Write text to ``path`` atomically (temp file + rename in the same dir).

    Existing files keep their permissions; new files default to 0644. Pass
    ``mode`` to force specific permissions (e.g. 0o600 for secrets).
    """
    p = Path(path)
    fd, tmp = _new_tmp(p, mode)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def atomic_write_bytes(path, data: bytes, mode: int | None = None):
    """Write bytes to ``path`` atomically (temp file + rename in the same dir)."""
    p = Path(path)
    fd, tmp = _new_tmp(p, mode)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, p)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
