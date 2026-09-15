"""Tests for GUI-decoupled models, cache, and KiCad patch helpers."""

from gui.cache import bump_recent, migrate_col_visible
from gui.models import PartState, St, apply_step_result, group_bom_parts, is_pcb_linked, row_subtotal
from lib.kicad_patch import (
    patch_pcb_footprint_refs,
    patch_sch_footprint_refs,
    patch_sch_lib_id_refs,
    sch_symbol_blocks,
)


def test_group_bom_parts_by_lcsc_and_value():
    parts = [
        {"ref": "R2", "val": "10k", "lcsc": "C1", "fp": "R_0805"},
        {"ref": "R1", "val": "10k", "lcsc": "C1", "fp": "R_0805"},
        {"ref": "C1", "val": "100n", "lcsc": "", "fp": "C_0603"},
    ]
    groups = group_bom_parts(parts)
    assert len(groups) == 2
    assert groups[0]["refs"] == ["R2", "R1"]
    assert groups[1]["lcsc"] == ""


def test_is_pcb_linked():
    refs = ["R1"]
    pcb = {"R1": {"lib": "footprint", "fp": "R_0805"}}
    assert is_pcb_linked("C1", refs, "footprint:R_0805", pcb) is True
    assert is_pcb_linked("C1", refs, "other:R_0805", pcb) is False
    assert is_pcb_linked("", refs, "footprint:R_0805", pcb) is None


def test_row_subtotal():
    assert row_subtotal(3, "$0.10") == "$0.30"
    assert row_subtotal(2, "n/a") is None


def test_apply_step_result_pdf_skip_and_symbol():
    s = PartState("C1")
    ns = apply_step_result(s, "pdf", True, {"skipped": True, "url": "http://x"})
    assert ns is St.SKIPPED
    assert s.pdf is St.SKIPPED
    apply_step_result(s, "symbol", True, {"value": "10k", "mfr": "Yageo"})
    assert s.value == "10k"
    assert s.mfr == "Yageo"
    apply_step_result(s, "valid", True, {"sym_ok": True})
    assert s.symbol is St.SUCCESS


def test_migrate_col_visible_9_to_15():
    vis = migrate_col_visible([True] * 9, version=1, n_cols=15)
    assert len(vis) == 15


def test_bump_recent_dedupes():
    assert bump_recent(["a", "b"], "b") == ["b", "a"]


def test_patch_pcb_footprint_refs():
    content = '(footprint "Lib:Old" (layer F.Cu) (property "Reference" "R1" (at 0 0)))'
    out = patch_pcb_footprint_refs(content, ["R1"], "footprint:New")
    assert '(footprint "footprint:New"' in out


def test_sch_symbol_blocks_and_patches():
    sch = """
(kicad_sch
  (lib_symbols
    (symbol "Device:R" (property "Reference" "R"))
  )
  (symbol (lib_id "Device:R")
    (property "Reference" "R1")
    (property "Footprint" "Resistor_SMD:R_0805")
  )
)
"""
    blocks = sch_symbol_blocks(sch)
    assert len(blocks) == 1
    out = patch_sch_footprint_refs(sch, ["R1"], "footprint:R_0805")
    assert 'Footprint" "footprint:R_0805"' in out
    out2 = patch_sch_lib_id_refs(sch, ["R1"], "Resistors:10k")
    assert '(lib_id "Resistors:10k")' in out2
