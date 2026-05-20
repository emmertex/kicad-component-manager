"""gui2.py — LCSC to KiCad Library Converter v2 (Entry Point)"""

import logging
import sys
from pathlib import Path

from PySide6.QtWidgets import QApplication

# ── Backend path setup ────────────────────────────────────────────────────────
# Ensure JLC2KiCadLib is in the path for all modules
_LIB = Path(__file__).parent / "lcsc2kicad-GUI" / "JLC2KiCadLib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Also add the project root to sys.path so we can import 'gui' and 'lib'
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from gui.widgets import MainWindow


def main():
    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
