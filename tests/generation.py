"""Shared helpers for generating KiCad libraries from captured fixtures."""

from __future__ import annotations

import json
import re
from pathlib import Path

from easyeda2kicad.easyeda.easyeda_importer import (
    EasyedaFootprintImporter,
    EasyedaSymbolImporter,
)
from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad
from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad

from lib.api import LCSCAPIClient
from lib.categories import resolve_category
from lib.helpers import EMPTY_SYM_LIB, sym_lib_targets, upsert_symbol
from lib.kicad_escape import escape_kicad_string

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load_json(name: str) -> dict:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def lcsc_product(pid: str) -> dict:
    body = load_json("lcsc_api_responses.json")[pid]["body"]
    return body.get("result") or {}


def parse_lcsc_from_fixture(pid: str) -> dict:
    """Run LCSCAPIClient parsing logic against a fixture (no HTTP)."""
    client = LCSCAPIClient()
    product = lcsc_product(pid)

    class _Resp:
        status_code = 200

        def json(self):
            return {"result": product}

    class _Session:
        def get(self, *a, **k):
            return _Resp()

    client_orig = __import__("lib.jlc.helper", fromlist=["helper"])
    old = client_orig.get_lcsc_session
    client_orig.get_lcsc_session = lambda: _Session()
    try:
        return client.get_component_data(pid) or {}
    finally:
        client_orig.get_lcsc_session = old


def build_custom_fields(pid: str, info: dict, category: str) -> dict:
    """Mirror gui.worker._do_symbol custom field construction."""
    mfr = escape_kicad_string(
        info.get("mfr") or info.get("manufacturer") or ""
    )
    desc = escape_kicad_string(info.get("description") or "")
    custom_fields = {
        "LCSC": pid,
        "Category": category,
        "Description": desc,
        "Manufacturer": mfr,
    }
    if info.get("attributes"):
        custom_fields["Key_Attributes"] = escape_kicad_string(info["attributes"])
    if info.get("stock"):
        custom_fields["Stock"] = escape_kicad_string(info["stock"])
    if info.get("price"):
        custom_fields["Price"] = escape_kicad_string(info["price"])
    for spec_name, spec_value in info.get("specifications", {}).items():
        clean_name = re.sub(r"[^\w\s-]", "", spec_name).strip()
        if clean_name and len(clean_name) <= 50 and clean_name not in custom_fields:
            if clean_name not in (
                "Reference",
                "Value",
                "Footprint",
                "Datasheet",
                "Manufacturer",
                "MPN",
                "LCSC Part",
                "Description",
            ):
                custom_fields[clean_name] = escape_kicad_string(spec_value)
    return custom_fields


def generate_symbol(
    pid: str,
    output_dir: Path,
    lib_mode: str = "organised",
    lib_prefix: str = "",
) -> Path:
    """Generate symbol .kicad_sym for *pid* using EasyEDA + LCSC fixtures."""
    easyeda = load_json("easyeda_responses.json")[pid]
    cad = easyeda["cad_data"]
    info = parse_lcsc_from_fixture(pid)

    sym_importer = EasyedaSymbolImporter(cad)
    ee_symbol = sym_importer.get_symbol()

    raw_category = info.get("category") or ""
    category = resolve_category(raw_category)
    lib_names = sym_lib_targets(raw_category, lib_prefix, lib_mode)

    mfr = escape_kicad_string(
        info.get("mfr") or info.get("manufacturer") or ee_symbol.info.manufacturer
    )
    desc = escape_kicad_string(info.get("description") or ee_symbol.info.description)
    val = escape_kicad_string(info.get("value") or info.get("Value") or pid)

    ee_symbol.info.manufacturer = mfr
    ee_symbol.info.package = escape_kicad_string(
        info.get("package") or info.get("Package") or ee_symbol.info.package
    )
    ee_symbol.info.description = desc
    ee_symbol.info.lcsc_id = pid
    ee_symbol.info.name = val
    ee_symbol.info.datasheet = escape_kicad_string(
        f"https://www.lcsc.com/product-detail/{pid}.html"
    )

    custom_fields = build_custom_fields(pid, info, category)

    sym_dir = output_dir / "symbol"
    sym_dir.mkdir(parents=True, exist_ok=True)
    primary = sym_dir / f"{lib_names[0]}.kicad_sym"
    primary.write_text(EMPTY_SYM_LIB, encoding="utf-8")

    exporter = ExporterSymbolKicad(
        ee_symbol,
        lib_path=str(primary),
        custom_fields=custom_fields,
    )
    sym_content = exporter.export(footprint_lib_name="footprint")

    for lib_name in lib_names:
        lib_path = sym_dir / f"{lib_name}.kicad_sym"
        if not lib_path.exists():
            lib_path.write_text(EMPTY_SYM_LIB, encoding="utf-8")
        upsert_symbol(str(lib_path), sym_content)

    return primary


def generate_footprint(pid: str, output_dir: Path) -> Path:
    """Generate footprint .kicad_mod for *pid* using EasyEDA fixture."""
    easyeda = load_json("easyeda_responses.json")[pid]
    cad = easyeda["cad_data"]
    fp_importer = EasyedaFootprintImporter(cad)
    ee_footprint = fp_importer.get_footprint()
    pretty_dir = output_dir / "footprint.pretty"
    pretty_dir.mkdir(parents=True, exist_ok=True)
    mod_path = pretty_dir / f"{ee_footprint.info.name}.kicad_mod"
    ExporterFootprintKicad(ee_footprint).export(
        footprint_full_path=str(mod_path),
        model_3d_path="${KICAD_USER_LIBRARY_DIR}/footprint/packages3d",
        model_3d_extension="step",
    )
    return mod_path
