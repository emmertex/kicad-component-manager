"""Shared part-number parsing for bulk import (CLI and GUI)."""

import re
from pathlib import Path


def normalize_pid(text):
    """Normalise a single token to an LCSC part number (e.g. 'C1234'), or None.

    Accepts a bare number (1234 -> C1234) or a C-prefixed number.
    """
    p = (text or "").strip().upper()
    if not p:
        return None
    if p.isdigit():
        return f"C{p}"
    if p.startswith("C") and p[1:].isdigit():
        return p
    return None


def parse_parts(tokens):
    """Parse an iterable of raw strings into part numbers.

    Each token may itself contain several parts separated by whitespace or
    commas (handy for pasted lists). Returns (valid_pids, invalid_tokens) with
    order preserved and duplicates removed.
    """
    valid, invalid, seen = [], [], set()
    for tok in tokens:
        for piece in re.split(r"[\s,]+", (tok or "").strip()):
            if not piece:
                continue
            pid = normalize_pid(piece)
            if pid is None:
                invalid.append(piece)
            elif pid not in seen:
                seen.add(pid)
                valid.append(pid)
    return valid, invalid


def read_parts_file(path):
    """Read a parts file (one part per line) into a list of raw lines."""
    return Path(path).read_text(encoding="utf-8", errors="replace").splitlines()
