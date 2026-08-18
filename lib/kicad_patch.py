"""KiCad PCB/schematic text patching (no GUI)."""

from __future__ import annotations

import re
from pathlib import Path

from lib.helpers import _find_block_end
from lib.kicad_escape import KICAD_QUOTED_VALUE_RE


def patch_pcb_footprint_refs(content: str, refs: list, new_fp: str) -> str:
    """Replace the footprint lib:name in a .kicad_pcb file for the given refs."""
    for ref in refs:
        ref_pat = re.compile(
            r'\((?:property\s+"Reference"|fp_text\s+reference)\s+"'
            + re.escape(ref)
            + r'"'
        )
        for m in ref_pat.finditer(content):
            before = content[: m.start()]
            fp_matches = list(
                re.finditer(
                    r'\((?:footprint|module)\s+"' + KICAD_QUOTED_VALUE_RE + r'"',
                    before,
                )
            )
            if not fp_matches:
                continue
            last_m = fp_matches[-1]
            old_str = last_m.group(0)
            kw = "footprint" if old_str.startswith("(footprint") else "module"
            new_str = f'({kw} "{new_fp}"'
            content = (
                content[: last_m.start()]
                + new_str
                + content[last_m.start() + len(old_str) :]
            )
            break
    return content


def sch_symbol_blocks(content: str) -> list:
    """Return (start, end) for every placed-instance symbol block."""
    sym_pat = re.compile(r"\(symbol\s+\(lib_id\b")
    blocks = []
    pos = 0
    while pos < len(content):
        m = sym_pat.search(content, pos)
        if not m:
            break
        start = m.start()
        end = _find_block_end(content, start)
        if end == -1:
            pos = m.end()
            continue
        blocks.append((start, end))
        pos = end + 1
    return blocks


def patch_sch_footprint_refs(content: str, refs: list, new_fp: str) -> str:
    """Replace Footprint property on placed symbols matching refs."""
    ref_set = set(refs)
    ref_check = re.compile(
        r'\(property\s+"Reference"\s+"(' + KICAD_QUOTED_VALUE_RE + r')"'
    )
    fp_pat = re.compile(
        r'(\(property\s+"Footprint"\s+")' + KICAD_QUOTED_VALUE_RE + r'(")'
    )

    patches = []
    for start, end in sch_symbol_blocks(content):
        block = content[start : end + 1]
        m = ref_check.search(block)
        if m and m.group(1) in ref_set:
            new_block, count = fp_pat.subn(
                lambda fm: fm.group(1) + new_fp + fm.group(2), block, count=1
            )
            if count:
                patches.append((start, end, new_block))

    for start, end, new_block in reversed(patches):
        content = content[:start] + new_block + content[end + 1 :]
    return content


def patch_sch_lib_id_refs(content: str, refs: list, new_lib_id: str) -> str:
    """Replace (lib_id "...") on placed symbols matching refs."""
    ref_set = set(refs)
    ref_check = re.compile(
        r'\(property\s+"Reference"\s+"(' + KICAD_QUOTED_VALUE_RE + r')"'
    )
    lib_id_pat = re.compile(r'(\(lib_id\s+")' + KICAD_QUOTED_VALUE_RE + r'(")')

    patches = []
    for start, end in sch_symbol_blocks(content):
        block = content[start : end + 1]
        m = ref_check.search(block)
        if m and m.group(1) in ref_set:
            new_block, count = lib_id_pat.subn(
                lambda fm: fm.group(1) + new_lib_id + fm.group(2), block, count=1
            )
            if count:
                patches.append((start, end, new_block))

    for start, end, new_block in reversed(patches):
        content = content[:start] + new_block + content[end + 1 :]
    return content


def extract_sym_block_for_schematic(
    sym_file: Path, sym_name: str, lib_nickname: str
) -> str | None:
    """Extract a symbol definition for embedding in schematic lib_symbols."""
    try:
        content = sym_file.read_text(encoding="utf-8")
    except OSError:
        return None
    pat = re.compile(
        r'^([ \t]*)\(symbol\s+"' + re.escape(sym_name) + r'"', re.MULTILINE
    )
    all_m = list(pat.finditer(content))
    if not all_m:
        return None
    min_indent = min(len(m.group(1)) for m in all_m)
    top_m = next((m for m in all_m if len(m.group(1)) == min_indent), None)
    if not top_m:
        return None
    start = top_m.start()
    end = _find_block_end(content, start)
    if end == -1:
        return None
    block = content[start : end + 1]
    block = block.replace(
        f'(symbol "{sym_name}"', f'(symbol "{lib_nickname}:{sym_name}"', 1
    )
    return block.strip()


def ensure_sym_in_lib_symbols(content: str, sym_block: str) -> str:
    """Insert or replace a symbol definition in schematic lib_symbols."""
    name_m = re.match(
        r'\(symbol\s+"(' + KICAD_QUOTED_VALUE_RE + r')"', sym_block.strip()
    )
    if not name_m:
        return content
    lib_id = name_m.group(1)

    ls_m = re.search(r"\(lib_symbols\b", content)
    if not ls_m:
        return content
    ls_start = ls_m.start()
    ls_end = _find_block_end(content, ls_start)
    if ls_end == -1:
        return content

    ls_content = content[ls_start : ls_end + 1]

    existing = re.search(r'\(symbol\s+"' + re.escape(lib_id) + r'"', ls_content)
    if existing:
        sym_s = existing.start()
        sym_e = _find_block_end(ls_content, sym_s)
        if sym_e != -1:
            ls_content = ls_content[:sym_s] + ls_content[sym_e + 1 :]

    indented = "\n  " + sym_block.strip().replace("\n", "\n  ") + "\n"
    ls_content = ls_content[:-1] + indented + ")"

    return content[:ls_start] + ls_content + content[ls_end + 1 :]


def find_sch_files(pcb_file: str) -> list[Path]:
    if not pcb_file:
        return []
    return sorted(Path(pcb_file).parent.rglob("*.kicad_sch"))
