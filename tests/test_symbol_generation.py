"""End-to-end symbol/footprint generation from captured fixtures."""

import pytest

from lib.helpers import sym_lib_targets, upsert_symbol, EMPTY_SYM_LIB
from lib.kicad_validate import validate_kicad_file, validate_symbol_library
from tests.conftest import PARTS_WITH_CAD
from tests.generation import (
    build_custom_fields,
    generate_footprint,
    generate_symbol,
    parse_lcsc_from_fixture,
)


@pytest.mark.parametrize("pid", PARTS_WITH_CAD)
def test_symbol_generation_from_fixture(tmp_library, pid):
    sym = generate_symbol(pid, tmp_library)
    assert sym.exists()
    validate_symbol_library(sym)


@pytest.mark.parametrize("pid", PARTS_WITH_CAD)
def test_footprint_generation_from_fixture(tmp_library, pid):
    fp = generate_footprint(pid, tmp_library)
    assert fp.suffix == ".kicad_mod"
    validate_kicad_file(fp)


@pytest.mark.parametrize("lib_mode", ("organised", "consolidated", "both_organised"))
def test_symbol_lib_modes(tmp_library, lib_mode):
    sym = generate_symbol("C25804", tmp_library, lib_mode=lib_mode)
    names = sym_lib_targets(
        parse_lcsc_from_fixture("C25804").get("category", ""), "", lib_mode
    )
    for name in names:
        path = tmp_library / "symbol" / f"{name}.kicad_sym"
        assert path.exists(), f"missing {path}"
        validate_symbol_library(path)


def test_custom_fields_include_specifications():
    info = parse_lcsc_from_fixture("C25804")
    fields = build_custom_fields("C25804", info, "Resistor")
    assert "Temperature Coefficient" in fields or "Key_Attributes" in fields


def test_double_upsert_does_not_corrupt_library(tmp_library):
    """Regression: repeated upsert must not jam symbols together."""
    sym = generate_symbol("C8734", tmp_library)
    content = sym.read_text(encoding="utf-8")
    # Extract first symbol block via upsert round-trip
    from lib.kicad_validate import parse_sexpr

    tree = parse_sexpr(content)
    # Re-upsert same symbol three times
    for _ in range(3):
        block_start = content.find('(symbol "')
        block_end = content.rfind(")\n)")
        block = content[block_start : content.find("\n)", block_start) + 2]
        # upsert full exported block from generation
        from tests.generation import load_json
        from easyeda2kicad.easyeda.easyeda_importer import EasyedaSymbolImporter
        from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad

        cad = load_json("easyeda_responses.json")["C8734"]["cad_data"]
        ee = EasyedaSymbolImporter(cad).get_symbol()
        info = parse_lcsc_from_fixture("C8734")
        ee.info.name = info.get("value", "C8734")
        exp = ExporterSymbolKicad(
            ee,
            lib_path=str(sym),
            custom_fields=build_custom_fields("C8734", info, "Microcontroller"),
        )
        upsert_symbol(str(sym), exp.export(footprint_lib_name="footprint"))
    validate_symbol_library(sym)
    # Should still have exactly one top-level symbol for this part name
    text = sym.read_text(encoding="utf-8")
    name = info.get("value", "C8734").replace(" ", "").replace("/", "_").replace(":", "_")
    assert text.count(f'(symbol "{name}"') == 1
