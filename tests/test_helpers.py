import pytest

from lib.bulk import normalize_pid, parse_parts, read_parts_file
from lib.categories import resolve_category
from lib.helpers import (
    CONSOLIDATED_LIB,
    EMPTY_SYM_LIB,
    ensure_libraries,
    list_sym_files,
    primary_is_consolidated,
    sym_lib_targets,
    sync_libraries,
    upsert_symbol,
    _check_existing,
    _delete_from_lib,
    _parse_sym_file,
    _update_symbol_datasheet,
    _update_symbol_property,
)
from lib.kicad_validate import validate_symbol_library
from tests.generation import generate_symbol


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("c25804", "C25804"),
        ("25804", "C25804"),
        ("C25804", "C25804"),
        ("", None),
        ("bad", None),
    ],
)
def test_normalize_pid(raw, expected):
    assert normalize_pid(raw) == expected


def test_parse_parts_dedupes_and_splits():
    valid, invalid = parse_parts(["C1, C2", "c1", "nope", "  3 "])
    assert valid == ["C1", "C2", "C3"]
    assert invalid == ["nope"]


def test_read_parts_file(tmp_path):
    f = tmp_path / "parts.txt"
    f.write_text("C25804\n# comment not supported\nC49678\n")
    lines = read_parts_file(f)
    assert lines == ["C25804", "# comment not supported", "C49678"]


@pytest.mark.parametrize(
    "mode,expected",
    [
        ("organised", ["Resistor"]),
        ("consolidated", [CONSOLIDATED_LIB]),
        ("both_organised", ["Resistor", CONSOLIDATED_LIB]),
        ("both_consolidated", ["Resistor", CONSOLIDATED_LIB]),
    ],
)
def test_sym_lib_targets(mode, expected):
    assert sym_lib_targets("Resistors", "", mode) == expected


def test_primary_is_consolidated():
    assert primary_is_consolidated("consolidated")
    assert not primary_is_consolidated("organised")


def test_ensure_libraries_creates_files(tmp_path):
    ensure_libraries(str(tmp_path), mode="both_organised")
    sym_dir = tmp_path / "symbol"
    assert (sym_dir / "Resistor.kicad_sym").exists()
    assert (sym_dir / f"{CONSOLIDATED_LIB}.kicad_sym").exists()
    assert (tmp_path / "sym-lib-table").exists()


def test_list_sym_files_respects_mode(tmp_path):
    ensure_libraries(str(tmp_path), mode="consolidated")
    files = list_sym_files(str(tmp_path), "consolidated")
    assert len(files) == 1
    assert files[0].stem == CONSOLIDATED_LIB


def test_upsert_and_parse_roundtrip(tmp_path):
    pid = "C25804"
    generate_symbol(pid, tmp_path)
    sym_files = list((tmp_path / "symbol").glob("*.kicad_sym"))
    parsed = []
    for sf in sym_files:
        parsed.extend(_parse_sym_file(sf))
    pids = [p["lcsc"] for p in parsed if p["lcsc"]]
    assert pid in pids


def test_sync_libraries_duplicates_to_consolidated(tmp_path):
    generate_symbol("C25804", tmp_path, lib_mode="organised")
    seen, copies = sync_libraries(str(tmp_path))
    assert seen >= 1
    assert copies >= 1
    comp = tmp_path / "symbol" / f"{CONSOLIDATED_LIB}.kicad_sym"
    assert comp.exists()
    validate_symbol_library(comp)


def test_update_symbol_property_with_special_chars(tmp_path):
    pid = "C25804"
    generate_symbol(pid, tmp_path)
    new_val = '±1% "special"\nline'
    assert _update_symbol_property(pid, str(tmp_path), "Price", new_val)
    sym_file = next((tmp_path / "symbol").glob("Resistor.kicad_sym"))
    validate_symbol_library(sym_file)
    content = sym_file.read_text(encoding="utf-8")
    assert "Price" in content


def test_resolve_category_maps_resistors():
    assert resolve_category("Chip Resistor - Surface Mount") == "Resistor"


def test_delete_from_lib_removes_top_level_symbol_only(tmp_path):
    """Deleting must not corrupt multi-unit symbols via sub-unit regex matches."""
    generate_symbol("C8734", tmp_path)
    sym_file = next((tmp_path / "symbol").glob("*.kicad_sym"))
    validate_symbol_library(sym_file)
    assert _delete_from_lib("C8734", str(tmp_path)) == []
    remaining = sym_file.read_text(encoding="utf-8") if sym_file.exists() else ""
    if remaining:
        validate_symbol_library(sym_file)
        assert "C8734" not in remaining
    else:
        assert not sym_file.exists()


def test_update_symbol_datasheet_multi_unit_ic(tmp_path):
    """Datasheet patch must target the parent symbol, not a sub-unit block."""
    generate_symbol("C8734", tmp_path)
    new_ds = "file:///local/datasheet.pdf"
    assert _update_symbol_datasheet("C8734", str(tmp_path), new_ds)
    sym_file = next((tmp_path / "symbol").glob("*.kicad_sym"))
    validate_symbol_library(sym_file)
    parsed = _parse_sym_file(sym_file)
    match = next(p for p in parsed if p["lcsc"] == "C8734")
    assert match["datasheet"] == new_ds


def test_check_existing_reads_lcsc_part_property(tmp_path):
    """_check_existing must find parts stored under LCSC Part, not only LCSC."""
    lib = tmp_path / "symbol"
    lib.mkdir(parents=True)
    sym = lib / "Resistor.kicad_sym"
    sym.write_text(
        EMPTY_SYM_LIB.replace(
            ")\n",
            '  (symbol "R_LCSC_PART"\n'
            '    (property "LCSC Part" "C99999" (id 0) (at 0 0 0)\n'
            '      (effects (font (size 1.27 1.27)))\n'
            "    )\n"
            '    (property "Footprint" "footprint:R_0603" (id 1) (at 0 0 0)\n'
            '      (effects (font (size 1.27 1.27)))\n'
            "    )\n"
            "  )\n"
            ")\n",
        ),
        encoding="utf-8",
    )
    fp_dir = tmp_path / "footprint.pretty"
    fp_dir.mkdir(parents=True)
    (fp_dir / "R_0603.kicad_mod").write_text(
        '(footprint "R_0603" (layer "F.Cu") (attr smd))\n', encoding="utf-8"
    )
    out = _check_existing("C99999", str(tmp_path))
    assert out.get("sym_ok")
    assert out.get("fp_ok")
    assert out.get("fp_name") == "footprint:R_0603"


def test_sync_libraries_multi_unit_symbol_stays_valid(tmp_path):
    generate_symbol("C8734", tmp_path, lib_mode="organised")
    sym_file = next((tmp_path / "symbol").glob("*.kicad_sym"))
    validate_symbol_library(sym_file)
    sync_libraries(str(tmp_path))
    validate_symbol_library(sym_file)
    comp = tmp_path / "symbol" / f"{CONSOLIDATED_LIB}.kicad_sym"
    validate_symbol_library(comp)
