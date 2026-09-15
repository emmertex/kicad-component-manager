"""Regression tests for known library corruption patterns."""

from pathlib import Path

import pytest

from lib.helpers import CONSOLIDATED_LIB, EMPTY_SYM_LIB, sync_libraries, upsert_symbol, _sym_lib_close
from lib.kicad_escape import escape_kicad_string
from lib.kicad_validate import KicadFormatError, validate_symbol_library, parse_sexpr
from tests.generation import generate_symbol


def test_unescaped_quote_is_detected_by_balanced_paren_check():
    """Unescaped quotes inside a property value break string boundaries."""
    bad = (
        '(kicad_symbol_lib (version 20210201) (generator test)\n'
        '  (symbol "BAD"\n'
        '    (property "Description" "has "quote" inside" (id 0) (at 0 0 0)\n'
        "      (effects (font (size 1.27 1.27)))\n"
        "    )\n"
        "  )\n"
        ")\n"
    )
    # S-expression tokenizer may still parse this as atoms, but paren balance
    # in strings is wrong — KiCad would mis-read the property value.
    tree = parse_sexpr(bad)
    sym = next(n for n in tree if isinstance(n, list) and n and n[0] == "symbol")
    prop = next(n for n in sym if isinstance(n, list) and n and n[0] == "property")
    assert prop[2] == "has "  # value truncated at first inner quote — corruption


def test_escape_prevents_quote_corruption():
    val = escape_kicad_string('has "quote" inside')
    good = f'(property "Description" "{val}")'
    tree = parse_sexpr(good)
    assert tree[2] == 'has "quote" inside'


def test_upsert_does_not_eat_library_close(tmp_path):
    """Symbols must stay inside the kicad_symbol_lib wrapper."""
    lib = tmp_path / "reg.kicad_sym"
    lib.write_text(EMPTY_SYM_LIB, encoding="utf-8")
    block = (
        '(symbol "REG"\n'
        '  (property "Reference" "U" (id 0) (at 0 0 0)\n'
        '    (effects (font (size 1.27 1.27)))\n'
        "  )\n"
        '  (symbol "REG_0_1"\n'
        "    (rectangle (start -5 5) (end 5 -5)\n"
        "      (stroke (width 0.254) (type default))\n"
        "      (fill (type background))\n"
        "    )\n"
        "  )\n"
        ")\n"
    )
    for _ in range(5):
        upsert_symbol(str(lib), block)
    content = lib.read_text(encoding="utf-8")
    close = _sym_lib_close(content)
    assert content[close:].strip() == ")"
    validate_symbol_library(lib)


def test_unicode_in_real_part_symbol(tmp_library):
    """Real LCSC data includes Ω and ± — output must remain valid."""
    sym = generate_symbol("C25804", tmp_library)
    text = sym.read_text(encoding="utf-8")
    assert "kΩ" in text or "Ω" in text or "10k" in text
    validate_symbol_library(sym)


def test_ic_multi_unit_upsert(tmp_library):
    sym = generate_symbol("C8734", tmp_library)
    validate_symbol_library(sym)


def test_sync_libraries_category_with_escaped_value(tmp_path):
    """Category values with quotes must round-trip through sync_libraries."""
    from lib.helpers import _parse_sym_file

    sym_dir = tmp_path / "symbol"
    sym_dir.mkdir(parents=True)
    cat = escape_kicad_string('Analog "special"')
    block = (
        f'  (symbol "CAT_TEST"\n'
        f'    (property "Category" "{cat}" (id 0) (at 0 0 0)\n'
        f"      (effects (font (size 1.27 1.27)))\n"
        f"    )\n"
        f'    (property "LCSC" "C11111" (id 1) (at 0 0 0)\n'
        f"      (effects (font (size 1.27 1.27)))\n"
        f"    )\n"
        f'    (symbol "CAT_TEST_0_1"\n'
        f"      (rectangle (start -1 1) (end 1 -1)\n"
        f"        (stroke (width 0.254) (type default))\n"
        f"        (fill (type background))\n"
        f"      )\n"
        f"    )\n"
        f"  )\n"
    )
    lib = sym_dir / "Analog.kicad_sym"
    lib.write_text(EMPTY_SYM_LIB, encoding="utf-8")
    upsert_symbol(str(lib), block)
    validate_symbol_library(lib)
    sync_libraries(str(tmp_path))
    comp = sym_dir / f"{CONSOLIDATED_LIB}.kicad_sym"
    assert comp.exists()
    validate_symbol_library(comp)
    parsed = _parse_sym_file(comp)
    match = next(p for p in parsed if p["lcsc"] == "C11111")
    assert match["category"] == 'Analog "special"'


def test_multi_unit_with_special_char_name():
    """Verify that multi-unit integration works when name contains special characters."""
    from easyeda2kicad.easyeda.parameters_easyeda import EeSymbol, EeSymbolInfo, EeSymbolBbox
    from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad

    info = EeSymbolInfo(name='TEST/NAME with space', prefix='U', package='PKG', manufacturer='', lcsc_id='C123', description='')
    bbox = EeSymbolBbox(x=0, y=0, width=10, height=10)
    ee_symbol = EeSymbol(info=info, bbox=bbox, pins=[], rectangles=[], circles=[], arcs=[], polylines=[], polygons=[], paths=[], ellipses=[], texts=[])
    sub_symbol = EeSymbol(info=info, bbox=bbox, pins=[], rectangles=[], circles=[], arcs=[], polylines=[], polygons=[], paths=[], ellipses=[], texts=[])
    ee_symbol.sub_symbols = [sub_symbol]

    exporter = ExporterSymbolKicad(ee_symbol)
    exported = exporter.export(footprint_lib_name='footprint')
    assert '_1_1' in exported


def test_asymmetric_symbol_origin():
    """Verify that single-unit symbol extraction uses head coordinates for its origin,
    instead of the BBox center which can be shifted due to text fields or asymmetric shapes."""
    from easyeda2kicad.easyeda.easyeda_importer import EasyedaSymbolImporter

    # Mock easyeda response data with asymmetric BBox
    mock_cad_data = {
        "dataStr": {
            "head": {
                "x": "25",
                "y": "45",
                "c_para": {
                    "name": "TEST_CAP",
                    "pre": "C",
                    "package": "0805",
                }
            },
            # BBox center: x = 10 + 40/2 = 30.
            # Head x: x = 25.
            # There is a 5px asymmetry difference.
            "BBox": {
                "x": "10",
                "y": "37",
                "width": "40",
                "height": "16",
            },
            "shape": []
        }
    }

    importer = EasyedaSymbolImporter(mock_cad_data)
    symbol = importer.get_symbol()
    
    # The symbol bbox.x and bbox.y must be set to head coordinates (25, 45), not BBox center (30, 45)
    assert symbol.bbox.x == 25.0
    assert symbol.bbox.y == 45.0


