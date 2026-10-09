"""KiCad IPC plugin entry point (KiCad 10.99 and newer); see plugin.json.

KiCad runs this file in the plugin's own virtual environment (built from
requirements.txt beside plugin.json) with KICAD_API_SOCKET/KICAD_API_TOKEN set.
It asks the running PCB editor for the open board's path and launches
manager.py in BOM mode on that file, or in library mode if there is no board.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher import _log, run_manager  # noqa: E402


def _board_path() -> "Path | None":
    """Path of the board open in KiCad, or None if there is none (or no API)."""
    try:
        from kipy import KiCad  # type: ignore[import-not-found]

        board = KiCad().get_board()
        doc = board.document
        path = Path(doc.project.path) / doc.board_filename
    except Exception as e:
        _log(f"IPC: no board available: {e!r}")
        return None
    _log(f"IPC: board = {path}")
    return path if doc.board_filename and path.exists() else None


def main() -> int:
    board = _board_path()
    if board is None:
        run_manager()
    else:
        # The board is read from disk; unsaved edits in the editor are not seen.
        run_manager(lambda: ["--pcb", str(board)])
    return 0


if __name__ == "__main__":
    sys.exit(main())
