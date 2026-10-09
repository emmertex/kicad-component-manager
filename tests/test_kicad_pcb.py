"""Tests for reading BOM parts from a saved .kicad_pcb (KiCad 10.99 IPC plugin path)."""

from lib.kicad_pcb import find_lcsc, pcb_bom_payload, read_pcb_parts

PCB = """(kicad_pcb (version 20250000) (generator "pcbnew")
  (footprint "Resistor_SMD:R_0805_2012Metric" (layer "F.Cu")
    (property "Reference" "R1" (at 0 0 0) (layer "F.SilkS"))
    (property "Value" "10k" (at 0 0 0) (layer "F.Fab"))
    (property "LCSC" "C17414" (at 0 0 0) (layer "F.Fab") (hide yes))
  )
  (footprint "footprint:SOT-23" (layer "F.Cu")
    (property "Reference" "Q1")
    (property "Value" "AO3400 \\"N\\"")
    (property "JLC Part" "C20917")
  )
  (footprint "Capacitor_SMD:C_0603" (layer "F.Cu")
    (property "Reference" "C1")
    (property "Value" "100n C14663")
  )
  (module "Old:LED" (layer "F.Cu")
    (fp_text reference "D1" (at 0 0))
    (fp_text value "LED" (at 0 0))
  )
)
"""


def test_read_pcb_parts(tmp_path):
    pcb = tmp_path / "board.kicad_pcb"
    pcb.write_text(PCB)
    parts = read_pcb_parts(pcb)
    assert parts == [
        {"ref": "R1", "val": "10k", "lcsc": "C17414", "fp": "R_0805_2012Metric", "lib": "Resistor_SMD"},
        {"ref": "Q1", "val": 'AO3400 "N"', "lcsc": "C20917", "fp": "SOT-23", "lib": "footprint"},
        {"ref": "C1", "val": "100n C14663", "lcsc": "C14663", "fp": "C_0603", "lib": "Capacitor_SMD"},
        {"ref": "D1", "val": "LED", "lcsc": "", "fp": "LED", "lib": "Old"},
    ]
    assert pcb_bom_payload(pcb) == {"pcb_file": str(pcb), "parts": parts}


def test_find_lcsc_prefers_named_fields():
    assert find_lcsc({"LCSC Part #": "C1", "Other": "C99999"}, "C55555") == "C1"
    assert find_lcsc({"Other": "C12345"}, "") == "C12345"
    assert find_lcsc({}, "x") == ""
