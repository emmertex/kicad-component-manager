import logging
import re
import sys
from pathlib import Path

from lib.categories import FINAL_CATEGORIES, resolve_category

from .jlc import helper, pdf_downloader

# ── Constants ─────────────────────────────────────────────────────────────────
# Minimum bytes for a valid PDF.
# Files smaller than this are usually placeholder/error HTML pages from LCSC.
PDF_MIN_BYTES = 10240


# Empty KiCad symbol library (header + footer, no symbols).
EMPTY_SYM_LIB = (
    "(kicad_symbol_lib (version 20210201) (generator TousstNicolas/JLC2KiCad_lib)\n)\n"
)


def _lib_name(category: str, prefix: str) -> str:
    """Compute KiCad library filename from category and optional prefix.

    The raw LCSC category is first resolved onto one of the fixed final
    categories, so every part lands in one of a small, stable set of libraries.
    """
    safe = resolve_category(category)
    return f"{prefix}_{safe}" if prefix else safe


def ensure_libraries(output_dir: str, prefix: str = "") -> None:
    """Create an empty .kicad_sym for every final category and (re)write the
    sym-lib-table, so KiCad sees the full library set without a restart."""
    sym_dir = Path(output_dir) / "symbol"
    try:
        sym_dir.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        logging.warning(f"Could not create symbol dir {sym_dir}: {e}")
        return
    for cat in FINAL_CATEGORIES:
        name = _lib_name(cat, prefix)
        f = sym_dir / f"{name}.kicad_sym"
        if not f.exists():
            try:
                f.write_text(EMPTY_SYM_LIB, encoding="utf-8")
            except OSError as e:
                logging.warning(f"Could not create library {f}: {e}")
    _update_sym_lib_table(output_dir)


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
                    # Find start of symbol block by searching backwards for (symbol
                    block_start = content.rfind('(symbol "', 0, m_pid.start())
                    if block_start != -1:
                        # Find the end of this symbol block
                        block_end = _find_block_end(content, block_start)
                        if block_end == -1:
                            block_end = len(content)
                        else:
                            block_end += 1
                        block = content[block_start:block_end]
                        m_fp = re.search(
                            r'\(property\s+"Footprint"\s+"([^"]*)"', block, re.DOTALL
                        )
                        if m_fp:
                            fp_ref = m_fp.group(1)
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

        def patch_block(m):
            block = m.group(0)
            if not marker.search(block):
                return block
            return re.sub(
                r'(\(property\s+"Datasheet"\s+")[^"]*(")',
                lambda dm: dm.group(1) + new_ds + dm.group(2),
                block,
                flags=re.DOTALL,
            )

        new_content = re.sub(
            r'\n([ \t]+)\(symbol "[^"]*".*?\n\1\)',
            patch_block,
            content,
            flags=re.DOTALL,
        )
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

        # Find all top-level symbol starts.
        # Top-level symbols are direct children of kicad_symbol_lib.
        # They usually start with (symbol "Name" ...
        matches = list(re.finditer(r'^([ \t]+)\(symbol "[^"]+"', content, re.MULTILINE))
        if not matches:
            logging.warning(f"No symbol blocks found in {sym_file}")
            return False

        new_chunks = []
        last_pos = 0
        found_block = False

        for i, m in enumerate(matches):
            start = m.start()
            # Find the actual start of the '('
            bracket_pos = content.find("(", start)
            if bracket_pos == -1:
                continue

            # Add text between symbols
            new_chunks.append(content[last_pos:bracket_pos])

            # Find the end of this symbol block
            end_pos = _find_block_end(content, bracket_pos)
            if end_pos == -1:
                # If we can't find the end, just take until the next match or end of file
                end_pos = (
                    matches[i + 1].start() if i + 1 < len(matches) else len(content)
                )
            else:
                end_pos += 1  # Include the closing ')'

            block = content[bracket_pos:end_pos]
            indent = m.group(1)

            if marker.search(block) and not found_block:
                found_block = True
                patched = block
                replaced = False
                for name in prop_names:
                    # Use count=1 to ensure we only update the first occurrence in this block
                    patched_new, count = re.subn(
                        r'(\(property\s+"' + re.escape(name) + r'"\s+")[^"]*(")',
                        lambda dm: dm.group(1) + new_value + dm.group(2),
                        block,
                        count=1,
                        flags=re.DOTALL,
                    )
                    if count > 0:
                        patched = patched_new
                        replaced = True
                        break

                if not replaced:
                    # Property not found in block — insert before the closing paren of the symbol
                    inner_indent = "  "
                    if "\t" in indent:
                        inner_indent = "\t"
                    inner = indent + inner_indent

                    # Find a high ID for the new property
                    ids = [int(x) for x in re.findall(r"\(id (\d+)\)", block)]
                    next_id = max(ids) + 1 if ids else 99

                    new_prop = (
                        f'\n{inner}(property "{prop_names[0]}" "{new_value}" (id {next_id}) (at 0 0 0)\n'
                        f"{inner}{inner_indent}(effects (font (size 1.27 1.27)) hide)\n"
                        f"{inner})"
                    )

                    # Find the main symbol's closing parenthesis.
                    # It's at the very end of our block.
                    idx = patched.rfind(")")
                    if idx >= 0:
                        patched = patched[:idx] + new_prop + patched[idx:]

                new_chunks.append(patched)
            else:
                new_chunks.append(block)

            last_pos = end_pos

        # Add remaining content
        new_chunks.append(content[last_pos:])
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


def _parse_sym_file(sym_file: Path):
    content = sym_file.read_text(encoding="utf-8", errors="replace")

    # Only scan within the kicad_symbol_lib s-expression so we never pick up
    # symbols that were accidentally placed outside the library close.
    lib_end = _sym_lib_close(content)
    inner = content[:lib_end]

    # Find the minimum indentation of any (symbol declaration inside the library.
    # That minimum level corresponds to top-level (component) symbols.
    all_sym_re = re.compile(r'^([ \t]+)\(symbol "([^"]+)"', re.MULTILINE)
    all_matches = list(all_sym_re.finditer(inner))
    if not all_matches:
        return []
    min_indent = min(len(m.group(1)) for m in all_matches)
    top_matches = [m for m in all_matches if len(m.group(1)) == min_indent]

    prop_re = re.compile(
        r'\(property\s+"([^"]+)"\s+"((?:[^"\\]|\\.)*?)"',
        re.DOTALL,
    )
    results = []
    for i, m in enumerate(top_matches):
        start = m.start()
        end = top_matches[i + 1].start() if i + 1 < len(top_matches) else lib_end
        chunk = inner[start:end]
        props = {pm.group(1): pm.group(2) for pm in prop_re.finditer(chunk)}
        results.append(
            {
                "name": m.group(2),
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
            r'\(property\s+"LCSC"\s+"' + re.escape(pid) + r'"', re.DOTALL
        )
        m_pid = marker.search(content)
        if m_pid:
            idx = m_pid.start()
            block = content[max(0, idx - 3000) : idx + 200]
            m_fp = re.search(r'\(property\s+"Footprint"\s+"([^"]*)"', block, re.DOTALL)
            if m_fp:
                fp_ref = m_fp.group(1)
        # Remove the symbol block
        new = re.sub(
            r'\n([ \t]+)\(symbol "[^"]*".*?\n\1\)',
            lambda m: "" if marker.search(m.group(0)) else m.group(0),
            content,
            flags=re.DOTALL,
        )
        if new != content:
            try:
                sym_file.write_text(new, encoding="utf-8")
                # Remove the file if it now contains no symbols
                if not re.search(r'\(symbol "', new):
                    sym_file.unlink(missing_ok=True)
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
