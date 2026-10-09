"""
KiCad Component Manager — Library Management Tool

Supports two installation layouts:

  Repo-symlink (install_plugin.sh):
    scripting/plugins/lcsc2kicad/  →  kicad_plugin/   (KiCad 7 – 10.0)
    plugins/lcsc2kicad/            →  kicad_plugin/   (KiCad 10.99+)
    manager.py lives one level up at the repo root.

  PCM self-contained (build_pcm.sh / KiCad "Install from File"):
    3rdparty/plugins/com_emmertex_lcsc2kicad/
    manager.py is bundled alongside this file.
    On first run the plugin offers to install dependencies into a local venv.

This file is the SWIG ``pcbnew.ActionPlugin`` front end. KiCad 10.99 removed
the SWIG bindings and runs ipc_plugin.py instead (see plugin.json); both
launch manager.py through launcher.py.

Note on toolbar placement: pcbnew.ActionPlugin toolbar buttons only appear
in the PCB editor — this is a KiCad API limitation.  The plugin is
accessible from the schematic editor via Tools → External Plugins (menu).

Debug log: ~/.lcsc2kicad_plugin.log
"""

import re

from .launcher import _ICON, _PLUGIN_DIR, _log, run_manager

try:
    import pcbnew  # type: ignore[import-not-found]
except ImportError:  # KiCad 10.99+: no SWIG bindings, ipc_plugin.py is used
    pcbnew = None

_LCSC_FIELD_NAMES = [
    "LCSC",
    "LCSC Part #",
    "LCSC Part Number",
    "LCSC#",
    "lcsc",
    "LCSC_PN",
    "LCSC Part",
    "JLC",
    "JLCPCB",
]


def _bom_args() -> list[str]:
    """Scan the live board and return ``["--bom", <json file>]``."""
    import json
    import tempfile

    assert pcbnew is not None
    board = pcbnew.GetBoard()
    parts = []
    for fp in board.GetFootprints():
        lcsc_pn = ""
        for field_name in _LCSC_FIELD_NAMES:
            fobj = fp.GetField(field_name)
            if fobj:
                val = fobj.GetText().strip()
                if val:
                    lcsc_pn = val
                    break

        if not lcsc_pn:
            try:
                for field in fp.GetFields():
                    name = field.GetName() if hasattr(field, "GetName") else ""
                    if "lcsc" in name.lower() or "jlc" in name.lower():
                        val = field.GetText().strip()
                        if val:
                            lcsc_pn = val
                            break
            except Exception:
                pass

        if not lcsc_pn:
            try:
                for field in fp.GetFields():
                    val = field.GetText().strip()
                    if re.match(r"^C\d{4,}$", val):
                        lcsc_pn = val
                        break
            except Exception:
                pass

        if not lcsc_pn:
            val = fp.GetValue()
            match = re.search(r"(C\d{4,})", val)
            if match:
                lcsc_pn = match.group(1)

        parts.append(
            {
                "ref": str(fp.GetReference()),
                "val": str(fp.GetValue()),
                "lcsc": str(lcsc_pn),
                "fp": str(fp.GetFPID().GetLibItemName()),
                "lib": str(fp.GetFPID().GetLibNickname()),
            }
        )

    bom_payload = {
        "pcb_file": str(board.GetFileName()),
        "parts": parts,
    }
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(bom_payload, f)
    _log(f"BOM data saved to {f.name}")
    return ["--bom", f.name]


if pcbnew is not None:

    class KiCadComponentManagerAction(pcbnew.ActionPlugin):
        def defaults(self):
            self.name = "KiCad Component Manager"
            self.category = "Library Management"
            self.description = (
                "Download LCSC parts and manage the project BOM in one window"
            )
            self.show_toolbar_button = True
            if _ICON.exists():
                self.icon_file_name = str(_ICON)

        def Run(self):
            assert pcbnew is not None
            board = None
            try:
                board = pcbnew.GetBoard()
            except Exception:
                board = None
            if board is not None and board.GetFileName():
                run_manager(_bom_args)
            else:
                run_manager()

    class KiCadBOMManagerAction(pcbnew.ActionPlugin):
        """Kept so existing toolbar shortcuts still work; same single window."""

        def defaults(self):
            self.name = "KiCad BOM Manager"
            self.category = "Library Management"
            self.description = "Open Component Manager on the BOM tab for the current PCB"
            self.show_toolbar_button = True
            if _ICON.exists():
                self.icon_file_name = str(_ICON)

        def Run(self):
            run_manager(_bom_args)

    _log(f"Plugin module loading from {_PLUGIN_DIR}")
    KiCadComponentManagerAction().register()
    KiCadBOMManagerAction().register()
    _log("Plugins registered OK")
