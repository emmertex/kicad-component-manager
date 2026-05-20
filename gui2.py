"""gui2.py — LCSC to KiCad Library Converter v2 (Entry Point)"""

import argparse
import logging
import sys
from pathlib import Path

# ── Backend path setup ────────────────────────────────────────────────────────
# Ensure JLC2KiCadLib is in the path for all modules
_LIB = Path(__file__).parent / "lcsc2kicad-GUI" / "JLC2KiCadLib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

# Also add the project root to sys.path so we can import 'gui' and 'lib'
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="gui2.py",
        description="LCSC to KiCad library converter. Run with no arguments for "
        "the GUI, or use -i/-I to import from the terminal.",
    )
    parser.add_argument(
        "-i",
        "--import",
        dest="parts",
        nargs="+",
        metavar="PART",
        help="One or more LCSC part numbers to import (e.g. -i C1234 C5678)",
    )
    parser.add_argument(
        "-I",
        "--import-file",
        dest="file",
        metavar="FILE",
        help="Import part numbers from a file (one per line)",
    )
    parser.add_argument(
        "--set-library",
        dest="set_library",
        metavar="PATH",
        help="Set (and cache) the KiCad library location, then exit",
    )
    return parser.parse_args(argv)


def main():
    args = _parse_args(sys.argv[1:])

    # CLI mode if any of the import/library flags were given.
    if args.parts or args.file or args.set_library:
        from lib.cli import run_cli

        sys.exit(run_cli(args))

    # GUI mode.
    from PySide6.QtWidgets import QApplication

    from gui.widgets import MainWindow

    logging.basicConfig(level=logging.INFO)
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
