from pathlib import Path

import pytest

from lib.helpers import EMPTY_SYM_LIB, upsert_symbol
from lib.kicad_escape import escape_kicad_string
from lib.kicad_validate import (
    KicadFormatError,
    _kicad_cli_exe,
    kicad_cli_fp_upgrade,
    kicad_cli_upgrade,
    parse_sexpr,
    validate_balanced_parens,
    validate_kicad_file,
    validate_symbol_library,
    validate_symbol_properties_escapable,
)
from tests.generation import generate_footprint, generate_symbol


def test_validate_balanced_parens_good():
    validate_balanced_parens('(kicad_symbol_lib (version 20210201) (generator x)\n)\n')


def test_validate_balanced_parens_bad():
    with pytest.raises(KicadFormatError):
        validate_balanced_parens('(kicad_symbol_lib (version 20210201)\n')


def test_parse_sexpr_property_with_escaped_quotes():
    tree = parse_sexpr('(property "Value" "10k\\" special")')
    assert tree == ["property", "Value", '10k" special']


def test_generated_symbol_is_valid(tmp_library, part_with_cad):
    sym_path = generate_symbol(part_with_cad, tmp_library)
    validate_kicad_file(sym_path)
    validate_symbol_library(sym_path)
    validate_symbol_properties_escapable(sym_path)


def test_generated_footprint_is_valid(tmp_library, part_with_cad):
    fp_path = generate_footprint(part_with_cad, tmp_library)
    validate_kicad_file(fp_path)
    tree = parse_sexpr(fp_path.read_text(encoding="utf-8"))
    assert tree[0] in ("footprint", "module")


def test_kicad_cli_can_upgrade_symbol(tmp_library, part_with_cad):
    if not _kicad_cli_exe():
        pytest.skip("kicad-cli not installed")
    sym_path = generate_symbol(part_with_cad, tmp_library)
    out = tmp_library / "kicad_cli_out"
    kicad_cli_upgrade(sym_path, out)
    upgraded = out / sym_path.name
    assert upgraded.exists()
    validate_symbol_library(upgraded)


def test_kicad_cli_can_upgrade_footprint(tmp_library, part_with_cad):
    if not _kicad_cli_exe():
        pytest.skip("kicad-cli not installed")
    fp_path = generate_footprint(part_with_cad, tmp_library)
    out = tmp_library / "kicad_cli_fp_out"
    kicad_cli_fp_upgrade(fp_path, out)
    upgraded = out / fp_path.name
    assert upgraded.exists()
    validate_kicad_file(upgraded)


def test_kicad_cli_upgrade_special_chars(tmp_path):
    """Symbols with escaped property values must survive kicad-cli sym upgrade."""
    if not _kicad_cli_exe():
        pytest.skip("kicad-cli not installed")
    lib = tmp_path / "special.kicad_sym"
    val = escape_kicad_string('±1% 10kΩ "precision"')
    lib.write_text(
        f'(kicad_symbol_lib (version 20210201) (generator test)\n'
        f'  (symbol "SPECIAL"\n'
        f'    (property "Value" "{val}" (id 0) (at 0 0 0)\n'
        f"      (effects (font (size 1.27 1.27)))\n"
        f"    )\n"
        f"  )\n"
        f")\n",
        encoding="utf-8",
    )
    out = tmp_path / "out"
    kicad_cli_upgrade(lib, out)
    validate_symbol_library(out / lib.name)


def test_upsert_symbol_preserves_valid_structure(tmp_path):
    lib = tmp_path / "test.kicad_sym"
    lib.write_text(EMPTY_SYM_LIB, encoding="utf-8")
    block = (
        '(symbol "R_test"\n'
        '  (property "Reference" "R" (id 0) (at 0 0 0)\n'
        '    (effects (font (size 1.27 1.27)))\n'
        "  )\n"
        '  (property "Value" "10k" (id 1) (at 0 0 0)\n'
        '    (effects (font (size 1.27 1.27)))\n'
        "  )\n"
        '  (symbol "R_test_0_1"\n'
        "    (rectangle (start -2.54 1.27) (end 2.54 -1.27)\n"
        "      (stroke (width 0.254) (type default))\n"
        "      (fill (type background))\n"
        "    )\n"
        "  )\n"
        ")\n"
    )
    upsert_symbol(str(lib), block)
    validate_symbol_library(lib)
    upsert_symbol(str(lib), block.replace("10k", "10kΩ ±1%"))
    validate_symbol_library(lib)


def test_property_with_special_chars_does_not_break_parser(tmp_path):
    """Regression: unescaped quotes in property values corrupt libraries."""
    lib = tmp_path / "special.kicad_sym"
    val = escape_kicad_string('±1% 10kΩ "precision" line\n2')
    lib.write_text(
        f'(kicad_symbol_lib (version 20210201) (generator test)\n'
        f'  (symbol "SPECIAL"\n'
        f'    (property "Value" "{val}" (id 0) (at 0 0 0)\n'
        f"      (effects (font (size 1.27 1.27)))\n"
        f"    )\n"
        f"  )\n"
        f")\n",
        encoding="utf-8",
    )
    validate_symbol_library(lib)
    tree = parse_sexpr(lib.read_text(encoding="utf-8"))
    props = [n for n in tree if isinstance(n, list) and n and n[0] == "symbol"]
    assert props
