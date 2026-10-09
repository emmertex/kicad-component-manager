"""Read the footprint list from a saved .kicad_pcb file for BOM mode.

KiCad 10.99 dropped the SWIG ``pcbnew`` module, so the IPC plugin can no longer
walk the live board; it passes the board path and the BOM is read from disk.
"""

from __future__ import annotations

import re
from pathlib import Path

from lib.kicad_validate import parse_sexpr

# Field names checked (in order) for an LCSC part number.
LCSC_FIELD_NAMES = [
    "LCSC",
    "LCSC Part #",
    "LCSC Part Number",
    "LCSC#",
    "lcsc",
    "LCSC_PN",
    "LCSC Part",
    "JLC",
    "JLCPCB",
]
_LCSC_RE = re.compile(r"^C\d{4,}$")


def find_lcsc(fields: dict[str, str], value: str) -> str:
    """Pick the LCSC part number from a footprint's fields, as the SWIG plugin did."""
    for name in LCSC_FIELD_NAMES:
        val = fields.get(name, "").strip()
        if val:
            return val
    for name, val in fields.items():
        if ("lcsc" in name.lower() or "jlc" in name.lower()) and val.strip():
            return val.strip()
    for val in fields.values():
        if _LCSC_RE.match(val.strip()):
            return val.strip()
    m = re.search(r"(C\d{4,})", value)
    return m.group(1) if m else ""


def _footprint_part(node: list) -> dict:
    lib_id = node[1] if len(node) > 1 and isinstance(node[1], str) else ""
    lib, _, fp = lib_id.rpartition(":")
    fields: dict[str, str] = {}
    for child in node[2:]:
        if not isinstance(child, list) or len(child) < 3:
            continue
        if child[0] == "property" and isinstance(child[1], str) and isinstance(child[2], str):
            fields[child[1]] = child[2]
        elif child[0] == "fp_text" and child[1] in ("reference", "value") and isinstance(child[2], str):
            # KiCad <= 7 stores reference/value as fp_text, not properties.
            fields.setdefault(child[1].capitalize(), child[2])
    ref = fields.pop("Reference", "")
    val = fields.pop("Value", "")
    return {"ref": ref, "val": val, "lcsc": find_lcsc(fields, val), "fp": fp, "lib": lib}


def read_pcb_parts(pcb_file: str | Path) -> list[dict]:
    """Return ``[{ref, val, lcsc, fp, lib}, ...]`` for every footprint on the board."""
    tree = parse_sexpr(Path(pcb_file).read_text(encoding="utf-8"))
    return [
        _footprint_part(node)
        for node in tree[1:]
        if isinstance(node, list) and node and node[0] in ("footprint", "module")
    ]


def pcb_bom_payload(pcb_file: str | Path) -> dict:
    """The JSON payload MainFrame loads in BOM mode."""
    return {"pcb_file": str(pcb_file), "parts": read_pcb_parts(pcb_file)}
