import logging
import re
import sys
from pathlib import Path

from lib.categories import FINAL_CATEGORIES, resolve_category
from lib.kicad_escape import KICAD_QUOTED_VALUE_RE, escape_kicad_string, unescape_kicad_string

from .jlc import helper, pdf_downloader

# ── Constants ─────────────────────────────────────────────────────────────────
# Minimum bytes for a valid PDF.
# Files smaller than this are usually placeholder/error HTML pages from LCSC.
PDF_MIN_BYTES = 10240


# Empty KiCad symbol library (header + footer, no symbols).
EMPTY_SYM_LIB = (
    "(kicad_symbol_lib (version 20210201) (generator TousstNicolas/JLC2KiCad_lib)\n)\n"
)

# Name of the single consolidated symbol library (pre-v2, backward-compatible
# layout). Always literally "components" regardless of any library prefix, so
# older projects that reference "components:" keep resolving.
CONSOLIDATED_LIB = "components"

# Supported library organisation modes.
#   organised          → one library per category
#   consolidated       → single "components" library
#   both_organised     → write both; UI lists from the category libraries
#   both_consolidated  → write both; UI lists from "components"
# ("both" is a legacy value kept readable as an alias of both_organised.)
LIB_MODES = ("organised", "consolidated", "both_organised", "both_consolidated")

# Modes that write to the per-category libraries / to the consolidated library.
_WRITES_ORGANISED = {"organised", "both_organised", "both_consolidated", "both"}
_WRITES_CONSOLIDATED = {"consolidated", "both_organised", "both_consolidated", "both"}
# Modes whose primary (UI listing source) is the consolidated library.
_PRIMARY_CONSOLIDATED = {"consolidated", "both_consolidated"}


def _lib_name(category: str, prefix: str) -> str:
    """Compute KiCad library filename from category and optional prefix.

    The raw LCSC category is first resolved onto one of the fixed final
    categories, so every part lands in one of a small, stable set of libraries.
    """
    safe = resolve_category(category)
    return f"{prefix}_{safe}" if prefix else safe


def primary_is_consolidated(mode: str) -> bool:
    """True if the UI should list components from the consolidated library."""
    return mode in _PRIMARY_CONSOLIDATED


def sym_lib_targets(category: str, prefix: str, mode: str) -> list:
    """Return the symbol-library base name(s) a part should be written to.

    - ``organised``                       → [<category lib>]
    - ``consolidated``                    → ["components"]
    - ``both_organised``/``both_consolidated`` → [<category lib>, "components"]

    Footprints, 3D models and datasheets are shared regardless of mode; only the
    symbol placement differs.
    """
    names = []
    if mode in _WRITES_ORGANISED:
        names.append(_lib_name(category, prefix))
    if mode in _WRITES_CONSOLIDATED:
        names.append(CONSOLIDATED_LIB)
    if not names:  # unknown mode → fall back to organised
        names.append(_lib_name(category, prefix))
    # De-duplicate while preserving order (guards against prefix-less edge cases).
    return list(dict.fromkeys(names))


def ensure_libraries(output_dir: str, prefix: str = "", mode: str = "organised") -> None:
    """Create the empty .kicad_sym files for the chosen mode and (re)write the
    sym-lib-table, so KiCad sees the full library set without a restart.

    Organised-writing modes pre-create one library per final category;
    consolidated-writing modes pre-create the single "components" library.
    """
    sym_dir = Path(output_dir) / "symbol"
    try:
        sym_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.warning(f"Could not create symbol dir {sym_dir}: {e}")
        return

    names = []
    if mode in _WRITES_ORGANISED or mode not in _WRITES_CONSOLIDATED:
        names.extend(_lib_name(cat, prefix) for cat in FINAL_CATEGORIES)
    if mode in _WRITES_CONSOLIDATED:
        names.append(CONSOLIDATED_LIB)

    for name in names:
        f = sym_dir / f"{name}.kicad_sym"
        if not f.exists():
            try:
                f.write_text(EMPTY_SYM_LIB, encoding="utf-8")
            except OSError as e:
                logging.warning(f"Could not create library {f}: {e}")
    _update_sym_lib_table(output_dir)


def list_sym_files(output_dir: str, mode: str) -> list:
    """Return the .kicad_sym files the UI should read when listing components,
    honouring the chosen primary so the 'both' modes don't show every part
    twice.  Consolidated primary → only "components"; organised primary → the
    category libraries.  Falls back to all files if the primary set is empty.
    """
    sym_dir = Path(output_dir) / "symbol"
    if not sym_dir.exists():
        return []
    all_files = sorted(sym_dir.glob("*.kicad_sym"))
    comp = sym_dir / f"{CONSOLIDATED_LIB}.kicad_sym"
    if primary_is_consolidated(mode):
        return [comp] if comp.exists() else all_files
    organised = [f for f in all_files if f.name != f"{CONSOLIDATED_LIB}.kicad_sym"]
    return organised if organised else all_files


# Top-level symbol blocks are located via paren matching (see _iter_top_level_symbols).


def _property_value_re(prop_name: str) -> re.Pattern:
    return re.compile(
        r'\(property\s+"'
        + re.escape(prop_name)
        + r'"\s+"('
        + KICAD_QUOTED_VALUE_RE
        + r')"',
        re.DOTALL,
    )


def _iter_top_level_symbols(content: str):
    """Yield (name, bracket_pos, end_pos) for each top-level symbol in a library."""
    lib_end = _sym_lib_close(content)
    inner = content[:lib_end]
    decl_re = re.compile(
        r'^([ \t]*)\(symbol "(' + KICAD_QUOTED_VALUE_RE + r')"',
        re.MULTILINE,
    )
    matches = list(decl_re.finditer(inner))
    if not matches:
        return
    min_indent = min(len(m.group(1)) for m in matches)
    top_matches = [m for m in matches if len(m.group(1)) == min_indent]
    for i, m in enumerate(top_matches):
        bracket_pos = m.start() + len(m.group(1))
        end_idx = _find_block_end(content, bracket_pos)
        if end_idx == -1:
            end_pos = (
                top_matches[i + 1].start() + len(top_matches[i + 1].group(1))
                if i + 1 < len(top_matches)
                else lib_end
            )
        else:
            end_pos = end_idx + 1
        yield m.group(2), bracket_pos, end_pos


def sync_libraries(output_dir: str, prefix: str = "") -> tuple:
    """Copy every symbol so it exists in BOTH its category library and the
    consolidated "components" library.

    Footprints, 3D models and datasheets are already shared on disk, so only the
    symbol s-expressions need duplicating. Idempotent: symbols already present in
    a target are left untouched. Returns (parts_seen, copies_written).
    """
    sym_dir = Path(output_dir) / "symbol"
    if not sym_dir.exists():
        return (0, 0)

    # name -> (block_text, category_lib_name). A definition already living in a
    # category library wins, so existing organised placement is preserved.
    registry: dict[str, tuple] = {}
    for sf in sorted(sym_dir.glob("*.kicad_sym")):
        try:
            content = sf.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        is_comp = sf.stem == CONSOLIDATED_LIB
        for name, bracket_pos, end_pos in _iter_top_level_symbols(content):
            block = content[bracket_pos:end_pos]
            if is_comp:
                cm = _property_value_re("Category").search(block)
                cat_lib = _lib_name(cm.group(1) if cm else "", prefix)
            else:
                cat_lib = sf.stem
            if name not in registry or not is_comp:
                registry[name] = (block, cat_lib)

    # target lib stem -> {name: block} that should be present in it
    wanted: dict[str, dict] = {}
    for name, (block, cat_lib) in registry.items():
        for target in dict.fromkeys([cat_lib, CONSOLIDATED_LIB]):
            wanted.setdefault(target, {})[name] = block

    copies = 0
    for stem, blocks in wanted.items():
        lib_path = sym_dir / f"{stem}.kicad_sym"
        try:
            content = (
                lib_path.read_text(encoding="utf-8", errors="replace")
                if lib_path.exists()
                else EMPTY_SYM_LIB
            )
        except OSError as e:
            logging.warning(f"Could not read {lib_path}: {e}")
            continue
        existing = {name for name, *_ in _iter_top_level_symbols(content)}
        missing = [b for n, b in blocks.items() if n not in existing]
        if not missing:
            continue
        close = _sym_lib_close(content)
        insert = "".join(b.rstrip("\n") + "\n" for b in missing)
        new_content = content[:close] + insert + content[close:]
        try:
            lib_path.write_text(new_content, encoding="utf-8")
            copies += len(missing)
        except OSError as e:
            logging.warning(f"Could not write {lib_path}: {e}")

    _update_sym_lib_table(output_dir)
    return (len(registry), copies)


def _find_sym_file(pid: str, sym_dir: Path) -> "Path | None":
    """Search all .kicad_sym files in sym_dir for the given LCSC pid."""
    if not sym_dir.exists():
        return None
    # Match (property "LCSC" "PID" or (property "LCSC Part" "PID"
    pattern = re.compile(
        r'\(property\s+"LCSC(?:\s+Part)?"\s+"' + re.escape(pid) + r'"', re.DOTALL
    )
    for sf in sorted(sym_dir.glob("*.kicad_sym")):
        try:
            content = sf.read_text(encoding="utf-8", errors="replace")
            if pattern.search(content):
                return sf
        except OSError:
            pass
    return None


def _check_existing(pid, output_dir, fp_ref=None):
    lib = Path(output_dir)
    out = {}
    sym_file = _find_sym_file(pid, lib / "symbol")
    if sym_file is not None:
        out["sym_ok"] = True
        if not fp_ref:
            try:
                content = sym_file.read_text(encoding="utf-8", errors="replace")
                marker = re.compile(
                    r'\(property\s+"LCSC(?:\s+Part)?"\s+"' + re.escape(pid) + r'"',
                    re.DOTALL,
                )
                m_pid = marker.search(content)
                if m_pid:
                    for name, bracket_pos, end_pos in _iter_top_level_symbols(content):
                        block = content[bracket_pos:end_pos]
                        if not marker.search(block):
                            continue
                        m_fp = _property_value_re("Footprint").search(block)
                        if m_fp:
                            fp_ref = m_fp.group(1)
                        break
            except Exception:
                pass

    if fp_ref and ":" in fp_ref:
        lib_name, fp_name = fp_ref.split(":", 1)
        # Try both the specific lib_name and 'footprint' as directory names
        for ln in [lib_name, "footprint"]:
            mod = lib / f"{ln}.pretty" / f"{fp_name}.kicad_mod"
            if mod.exists():
                out["fp_ok"] = True
                out["fp_name"] = f"{ln}:{fp_name}"

                # Check for STEP file
                try:
                    mod_txt = mod.read_text(encoding="utf-8", errors="replace")
                    model_names = []
                    # 1. Descriptive name from (model ...)
                    for m in re.finditer(r'\(model\s+"([^"]+)"', mod_txt, re.DOTALL):
                        model_names.append(Path(m.group(1)).stem)
                    # 2. Part number
                    model_names.append(pid)
                    # 3. Footprint name
                    model_names.append(fp_name)

                    # Search directories
                    search_dirs = [
                        lib / ln / "packages3d",
                        lib / "footprint" / "packages3d",
                        lib / "packages3d",
                    ]

                    step_found = False
                    for d in search_dirs:
                        if not d.exists():
                            continue
                        for name in model_names:
                            if not name:
                                continue
                            for ext in [".step", ".STEP", ".stp", ".STP"]:
                                if (d / (name + ext)).exists():
                                    step_found = True
                                    break
                            if step_found:
                                break
                        if step_found:
                            break

                    if step_found:
                        out["step_ok"] = True
                except Exception:
                    pass
                break  # Found the footprint, stop searching libraries

    pdf = lib / "pdf" / f"{pid}.pdf"
    if pdf.exists() and pdf.stat().st_size >= PDF_MIN_BYTES:
        out["pdf_ok"] = True
    return out


def _update_symbol_datasheet(pid: str, output_dir: str, new_ds: str) -> bool:
    """Patch the Datasheet property for pid in the symbol library file."""
    sym_file = _find_sym_file(pid, Path(output_dir) / "symbol")
    if sym_file is None:
        return False
    try:
        content = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = re.compile(
            r'\(property\s+"LCSC(?:\s+Part)?"\s+"' + re.escape(pid) + r'"', re.DOTALL
        )
        safe_ds = escape_kicad_string(new_ds)
        ds_pat = re.compile(
            r'(\(property\s+"Datasheet"\s+")' + KICAD_QUOTED_VALUE_RE + r'(")',
            re.DOTALL,
        )

        lib_end = _sym_lib_close(content)
        new_chunks = []
        last_pos = 0
        found_block = False

        for name, bracket_pos, end_pos in _iter_top_level_symbols(content):
            new_chunks.append(content[last_pos:bracket_pos])
            block = content[bracket_pos:end_pos]
            if marker.search(block) and not found_block:
                found_block = True
                patched, count = ds_pat.subn(
                    lambda dm: dm.group(1) + safe_ds + dm.group(2),
                    block,
                    count=1,
                )
                new_chunks.append(patched if count else block)
            else:
                new_chunks.append(block)
            last_pos = end_pos

        new_chunks.append(content[last_pos:lib_end])
        new_chunks.append(content[lib_end:])
        new_content = "".join(new_chunks)

        if not found_block:
            return False
        if new_content != content:
            sym_file.write_text(new_content, encoding="utf-8")
        return True
    except Exception as e:
        logging.warning(f"Failed to update symbol datasheet for {pid}: {e}")
        return False


def _find_block_end(content, start_pos):
    """Find the index of the closing ) for the block starting at start_pos."""
    depth = 0
    in_str = False
    esc = False
    for i in range(start_pos, len(content)):
        c = content[i]
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _update_symbol_property(
    pid: str, output_dir: str, prop_names, new_value: str
) -> bool:
    """Patch one or more KiCad symbol properties for pid (tries each name in order)."""
    sym_file = _find_sym_file(pid, Path(output_dir) / "symbol")
    if sym_file is None:
        return False
    if isinstance(prop_names, str):
        prop_names = [prop_names]
    try:
        content = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = re.compile(
            r'\(property\s+"LCSC(?:\s+Part)?"\s+"' + re.escape(pid) + r'"', re.DOTALL
        )

        lib_end = _sym_lib_close(content)
        new_chunks = []
        last_pos = 0
        found_block = False

        for name, bracket_pos, end_pos in _iter_top_level_symbols(content):
            new_chunks.append(content[last_pos:bracket_pos])
            block = content[bracket_pos:end_pos]

            if marker.search(block) and not found_block:
                found_block = True
                patched = block
                replaced = False
                safe_value = escape_kicad_string(new_value)
                for pname in prop_names:
                    patched_new, count = re.subn(
                        r'(\(property\s+"'
                        + re.escape(pname)
                        + r'"\s+")'
                        + KICAD_QUOTED_VALUE_RE
                        + r'(")',
                        lambda dm, sv=safe_value: dm.group(1) + sv + dm.group(2),
                        block,
                        count=1,
                        flags=re.DOTALL,
                    )
                    if count > 0:
                        patched = patched_new
                        replaced = True
                        break

                if not replaced:
                    line_start = content.rfind("\n", 0, bracket_pos) + 1
                    indent = content[line_start:bracket_pos]
                    inner_indent = "\t" if "\t" in indent else "  "
                    inner = indent + inner_indent

                    ids = [int(x) for x in re.findall(r"\(id (\d+)\)", block)]
                    next_id = max(ids) + 1 if ids else 99

                    new_prop = (
                        f'\n{inner}(property "{prop_names[0]}" "{safe_value}" (id {next_id}) (at 0 0 0)\n'
                        f"{inner}{inner_indent}(effects (font (size 1.27 1.27)) hide)\n"
                        f"{inner})"
                    )

                    idx = patched.rfind(")")
                    if idx >= 0:
                        patched = patched[:idx] + new_prop + patched[idx:]

                new_chunks.append(patched)
            else:
                new_chunks.append(block)

            last_pos = end_pos

        new_chunks.append(content[last_pos:lib_end])
        new_chunks.append(content[lib_end:])
        new_content = "".join(new_chunks)

        if not found_block:
            logging.warning(f"Symbol block for {pid} not found in {sym_file}")
            return False
        if new_content != content:
            sym_file.write_text(new_content, encoding="utf-8")
            logging.info(f"Updated {prop_names} for {pid} in {sym_file.name}")
        return True
    except Exception as e:
        logging.warning(f"Failed to update symbol property for {pid}: {e}")
        import traceback

        logging.debug(traceback.format_exc())
        return False


def _sym_lib_close(content: str) -> int:
    """Return position of the ) closing the kicad_symbol_lib node."""
    in_str = False
    esc = False
    depth = 0
    for i, c in enumerate(content):
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(content)


def _remove_top_symbol(content: str, name: str) -> str:
    """Remove a top-level (symbol "name" ...) block via paren matching.

    Only the exact-named top-level symbol is removed (sub-units are named
    "name_d_d" and won't match). Returns content unchanged if not present.
    """
    pat = re.compile(r'(?m)^[ \t]*\(symbol "' + re.escape(name) + r'"')
    m = pat.search(content)
    if not m:
        return content
    start = content.find("(", m.start())
    end = _find_block_end(content, start)
    if end == -1:
        return content
    s = m.start()
    e = end + 1
    if e < len(content) and content[e] == "\n":
        e += 1
    return content[:s] + content[e:]


def upsert_symbol(lib_path: str, content: str) -> None:
    """Insert or replace a top-level symbol in a .kicad_sym file using
    paren-matched boundaries.

    This replaces easyeda2kicad's write_component_in_symbol_lib_file, whose
    regex-based replace includes a leading newline in the match but not in the
    replacement — so every overwrite eats a newline, eventually jamming symbols
    together, breaking id-detection, and duplicating sub-units. We instead remove
    any existing copy cleanly, then splice the new block before the library close.

    ``content`` is a full '(symbol "name" ... )' s-expression.
    """
    m = re.match(r'\s*\(symbol "(' + KICAD_QUOTED_VALUE_RE + r')"', content)
    if not m:
        raise ValueError("upsert_symbol: content is not a symbol s-expression")
    name = m.group(1)
    p = Path(lib_path)
    if p.exists():
        current = p.read_text(encoding="utf-8", errors="replace")
    else:
        current = EMPTY_SYM_LIB
    current = _remove_top_symbol(current, name)
    close = _sym_lib_close(current)
    prefix = current[:close]
    if not prefix.endswith("\n"):
        prefix += "\n"
    new = prefix + content.strip("\n") + "\n" + current[close:]
    p.write_text(new, encoding="utf-8")


def _parse_sym_file(sym_file: Path):
    content = sym_file.read_text(encoding="utf-8", errors="replace")
    lib_end = _sym_lib_close(content)

    prop_re = re.compile(
        r'\(property\s+"(' + KICAD_QUOTED_VALUE_RE + r')"\s+"(' + KICAD_QUOTED_VALUE_RE + r')"',
        re.DOTALL,
    )
    results = []
    symbols = list(_iter_top_level_symbols(content))
    for i, (name, bracket_pos, end_pos) in enumerate(symbols):
        chunk_end = symbols[i + 1][1] if i + 1 < len(symbols) else lib_end
        chunk = content[bracket_pos:chunk_end]
        props = {
            pm.group(1): unescape_kicad_string(pm.group(2))
            for pm in prop_re.finditer(chunk)
        }
        results.append(
            {
                "name": name,
                "lcsc": props.get("LCSC", props.get("LCSC Part", "")),
                "footprint": props.get("Footprint", ""),
                "datasheet": props.get("Datasheet", ""),
                "value": props.get("Value", ""),
                "description": (
                    props.get("Description_1")
                    or props.get("Description")
                    or props.get("ki_description", "")
                ),
                "package": props.get("Package", ""),
                "mfr": props.get("Manufacturer", props.get("MFR", "")),
                "category": props.get("Category", ""),
                "attributes": props.get("Key_Attributes", props.get("ki_keywords", "")),
                "price": props.get("Price", ""),
                "stock": props.get("Stock", ""),
            }
        )
    return results


def _update_sym_lib_table(output_dir: str) -> None:
    """Regenerate sym-lib-table from every .kicad_sym file in the symbol/ directory."""
    sym_dir = Path(output_dir) / "symbol"
    table_path = Path(output_dir) / "sym-lib-table"
    sym_files = sorted(sym_dir.glob("*.kicad_sym")) if sym_dir.exists() else []
    lines = ["(sym_lib_table", "\t(version 7)"]
    for sf in sym_files:
        name = sf.stem
        uri = str(sf.resolve()).replace("\\", "/")
        lines.append(
            f'\t(lib (name "{name}") (type "KiCad") (uri "{uri}") (options "") (descr ""))'
        )
    lines.append(")")
    try:
        table_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logging.info(f"sym-lib-table: {len(sym_files)} lib(s) → {table_path}")
    except OSError as e:
        logging.warning(f"Could not write sym-lib-table: {e}")


def _delete_from_lib(pid, output_dir):
    lib = Path(output_dir)
    errors = []
    fp_ref = ""

    sym_file = _find_sym_file(pid, lib / "symbol")
    if sym_file is not None:
        content = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = re.compile(
            r'\(property\s+"LCSC(?:\s+Part)?"\s+"' + re.escape(pid) + r'"', re.DOTALL
        )
        lib_end = _sym_lib_close(content)
        symbol_name = None
        for name, bracket_pos, end_pos in _iter_top_level_symbols(content):
            block = content[bracket_pos:end_pos]
            if not marker.search(block):
                continue
            symbol_name = name
            m_fp = _property_value_re("Footprint").search(block)
            if m_fp:
                fp_ref = m_fp.group(1)
            break
        if symbol_name:
            new = _remove_top_symbol(content, symbol_name)
            if new != content:
                try:
                    if not re.search(
                        r'(?m)^[ \t]*\(symbol "', new[: _sym_lib_close(new)]
                    ):
                        sym_file.unlink(missing_ok=True)
                    else:
                        sym_file.write_text(new, encoding="utf-8")
                except OSError as e:
                    errors.append(f"Symbol file: {e}")

    if fp_ref and ":" in fp_ref:
        ln, fn = fp_ref.split(":", 1)
        for p in [
            lib / f"{ln}.pretty" / f"{fn}.kicad_mod",
            lib / ln / "packages3d" / f"{fn}.step",
        ]:
            if p.exists():
                try:
                    p.unlink()
                except OSError as e:
                    errors.append(str(e))

    pdf = lib / "pdf" / f"{pid}.pdf"
    if pdf.exists():
        try:
            pdf.unlink()
        except OSError as e:
            errors.append(str(e))

    return errors
