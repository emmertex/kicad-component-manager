"""Persistent GUI settings cache (no GUI toolkit)."""

from __future__ import annotations

import json
from pathlib import Path

from lib.fsutil import atomic_write_text

CACHE_FILE = Path.home() / ".lcsc2kicad_cache.json"
SECRET_FILE = Path.home() / ".lcsc2kicad_secret.json"
CACHE_VERSION = 7
MAX_RECENT = 10


def load_cache(path: Path | None = None) -> dict:
    p = path or CACHE_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(data: dict, path: Path | None = None) -> None:
    p = path or CACHE_FILE
    atomic_write_text(p, json.dumps(data, indent=2))

def load_secret(path: Path | None = None) -> dict:
    p = path or SECRET_FILE
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_secret(data: dict, path: Path | None = None) -> None:
    p = path or SECRET_FILE
    atomic_write_text(p, json.dumps(data, indent=2), mode=0o600)


def migrate_api_key(cache_data: dict, secret_path: Path | None = None) -> str:
    """Return the JLCPCB API key from the 0600-permission secret file.

    Moves any legacy copy out of ``cache_data`` (the world-readable cache),
    persisting both sides on first migration.
    """
    p = secret_path or SECRET_FILE
    secret = load_secret(p)
    legacy = cache_data.pop("jlcpcb_api_key", "")
    if legacy and not secret.get("jlcpcb_api_key"):
        secret["jlcpcb_api_key"] = legacy
        save_secret(secret, p)
        try:
            save_cache(cache_data)
        except Exception:
            pass
    return secret.get("jlcpcb_api_key") or legacy


def migrate_col_visible(saved_vis: list, version: int, n_cols: int) -> list:
    saved_vis = list(saved_vis)
    if version < CACHE_VERSION:
        n = len(saved_vis)
        if n == 9:
            saved_vis = saved_vis[:3] + [False] * 5 + saved_vis[3:8] + [saved_vis[8]]
        elif n == 11:
            saved_vis = (
                saved_vis[:3]
                + [False, False, saved_vis[8], saved_vis[9], False]
                + saved_vis[3:8]
                + [saved_vis[10]]
            )
        elif n == 13:
            saved_vis = saved_vis[:7] + [False] + saved_vis[7:]
        elif n == 14:
            saved_vis = saved_vis[:13] + [True] + saved_vis[13:]
        elif n < n_cols:
            saved_vis += [False] * (n_cols - n)
    if len(saved_vis) >= n_cols:
        return saved_vis[:n_cols]
    return saved_vis + [True] * (n_cols - len(saved_vis))


def bump_recent(recent: list, path: str) -> list:
    dirs = [r for r in recent if r != path]
    if path:
        dirs.insert(0, path)
    return list(dict.fromkeys(dirs))[:MAX_RECENT]
