"""Persistent GUI settings cache (no GUI toolkit)."""

from __future__ import annotations

import json
from pathlib import Path

CACHE_FILE = Path.home() / ".lcsc2kicad_cache.json"
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
    p.write_text(json.dumps(data, indent=2), encoding="utf-8")


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
