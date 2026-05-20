"""
Importable module for fetching and updating component stock/price data in KiCad libraries.

Usage from an external application:
    from lib.jlc.component_data import refresh_stock_price, update_symbol_stock_price, fetch_stock_price
"""

import logging
import re
from pathlib import Path
from typing import Dict, Optional, Tuple

from .jlcpcb_client import JLCPCBAPIError, get_jlcpcb_client

logger = logging.getLogger(__name__)


def fetch_stock_price(pid: str, api_key: Optional[str] = None) -> Dict[str, str]:
    """
    Fetch current stock level and lowest-tier price from the JLCPCB API.

    Args:
        pid: LCSC/JLCPCB part number (e.g. "C2040")
        api_key: Optional JLCPCB API key

    Returns:
        Dict with available keys: 'stock' (str), 'price' (str)
    """
    client = get_jlcpcb_client(api_key)
    result: Dict[str, str] = {}

    try:
        stock = client.get_inventory(pid)
        if stock is not None:
            result["stock"] = str(stock)
    except JLCPCBAPIError as e:
        logger.debug(f"Stock fetch failed for {pid}: {e}")

    try:
        pricing = client.get_pricing(pid)
        if pricing:
            price_val = pricing[0].get("price", "")
            if price_val:
                result["price"] = str(price_val)
    except JLCPCBAPIError as e:
        logger.debug(f"Pricing fetch failed for {pid}: {e}")

    return result


def fetch_component_info(pid: str, api_key: Optional[str] = None) -> Dict[str, str]:
    """
    Fetch component details including category, stock and price from the JLCPCB API.

    Args:
        pid: LCSC/JLCPCB part number
        api_key: Optional JLCPCB API key

    Returns:
        Dict with available fields: 'category', 'stock', 'price'
    """
    client = get_jlcpcb_client(api_key)
    result: Dict[str, str] = {}

    try:
        data = client.get_component(pid)
        if data:
            for key in ("category", "categoryName", "firstCatalog", "secondCatalog"):
                cat = (data.get(key) or "").strip()
                if cat:
                    result["category"] = cat
                    break
    except JLCPCBAPIError as e:
        logger.debug(f"Component info fetch failed for {pid}: {e}")

    result.update(fetch_stock_price(pid, api_key))
    return result


def update_symbol_stock_price(
    pid: str,
    output_dir: str,
    stock: Optional[str] = None,
    price: Optional[str] = None,
) -> bool:
    """
    Update Stock and Price properties in the KiCad symbol library for a component.

    Searches all .kicad_sym files under output_dir/symbol/ for the component.

    Args:
        pid: LCSC/JLCPCB part number
        output_dir: Root library directory (contains 'symbol/' subdirectory)
        stock: Stock quantity as string, or None to skip
        price: Price as string, or None to skip

    Returns:
        True if the symbol was found and updated
    """
    sym_dir = Path(output_dir) / "symbol"
    sym_file = _find_sym_file(pid, sym_dir)
    if sym_file is None:
        logger.warning(f"Symbol for {pid} not found in {sym_dir}")
        return False

    content = sym_file.read_text(encoding="utf-8", errors="replace")
    marker = f'(property "LCSC" "{pid}"'
    new_content = content

    if stock is not None:
        new_content = _patch_property(new_content, marker, "Stock", stock)
    if price is not None:
        new_content = _patch_property(new_content, marker, "Price", price)

    if new_content != content:
        sym_file.write_text(new_content, encoding="utf-8")
        logger.info(f"Updated stock/price for {pid} in {sym_file.name}")
        return True
    return False


def refresh_stock_price(
    pid: str, output_dir: str, api_key: Optional[str] = None
) -> Tuple[bool, Dict[str, str]]:
    """
    Fetch fresh stock/price from JLCPCB API and update the KiCad symbol file.

    Args:
        pid: LCSC/JLCPCB part number
        output_dir: Root library directory
        api_key: Optional JLCPCB API key

    Returns:
        (success, data) where data contains the fetched 'stock' and/or 'price'
    """
    data = fetch_stock_price(pid, api_key)
    if not data:
        return False, {}
    ok = update_symbol_stock_price(
        pid, output_dir, stock=data.get("stock"), price=data.get("price")
    )
    return ok, data


def _find_sym_file(pid: str, sym_dir: Path) -> Optional[Path]:
    """Find the .kicad_sym file containing the given LCSC part number."""
    if not sym_dir.exists():
        return None
    marker = f'(property "LCSC" "{pid}"'
    for sf in sorted(sym_dir.glob("*.kicad_sym")):
        try:
            if marker in sf.read_text(encoding="utf-8", errors="replace"):
                return sf
        except OSError:
            pass
    return None


def _patch_property(content: str, marker: str, prop_name: str, value: str) -> str:
    """Insert or update a KiCad symbol property in the symbol block containing marker."""
    all_matches = list(re.finditer(r'\n([ \t]+)\(symbol "[^"]+"', content))
    if not all_matches:
        return content

    min_indent_len = min(len(m.group(1)) for m in all_matches)
    matches = [m for m in all_matches if len(m.group(1)) == min_indent_len]

    chunks = []
    last_pos = 0
    found = False

    for i, m in enumerate(matches):
        start = m.start()
        chunks.append(content[last_pos:start])

        end = matches[i + 1].start() if i + 1 < len(matches) else content.rfind(")")
        if end == -1:
            end = len(content)

        block = content[start:end]
        indent = m.group(1)

        if marker in block and not found:
            found = True
            patched, n = re.subn(
                r'(\(property "' + re.escape(prop_name) + r'" ")[^"]*(")',
                lambda dm: dm.group(1) + value + dm.group(2),
                block,
                count=1,
            )
            if n == 0:
                inner = indent + ("  " if "\t" not in indent else "\t")
                new_prop = (
                    f'\n{inner}(property "{prop_name}" "{value}" (id 97) (at 0 0 0)\n'
                    f"{inner}  (effects (font (size 1.27 1.27)) hide)\n"
                    f"{inner})"
                )
                idx = patched.rfind(f"\n{indent})")
                if idx >= 0:
                    patched = patched[:idx] + new_prop + patched[idx:]
            chunks.append(patched)
        else:
            chunks.append(block)

        last_pos = end

    chunks.append(content[last_pos:])
    return "".join(chunks)
