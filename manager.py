"""KiCad Component Manager — Library Management Tool"""

import argparse
import logging
import sys
from pathlib import Path

# Also add the project root to sys.path so we can import 'gui' and 'lib'
_ROOT = Path(__file__).parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="manager.py",
        description="KiCad Component Manager. Run with no arguments for "
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
    parser.add_argument(
        "--bom",
        dest="bom",
        metavar="FILE",
        help="Open in BOM mode using parts from a PCB scan (JSON file)",
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
    win = MainWindow(bom_file=args.bom)
    if not args.bom:
        win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
