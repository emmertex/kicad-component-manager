"""Headless command-line import.

Runs the exact same import pipeline as the GUI (the ``Worker`` thread), but
driven from the terminal with a minimal event loop and console logging.
"""

import json
import sys

from gui.widgets import CACHE_FILE, MAX_RECENT


def _out(msg):
    print(msg, flush=True)


def _err(msg):
    print(msg, file=sys.stderr, flush=True)


def _load_cache():
    try:
        return json.loads(CACHE_FILE.read_text())
    except Exception:
        return {}


def save_library_path(path):
    """Persist a library location to the shared cache (front of recent_dirs)."""
    d = _load_cache()
    recent = [r for r in d.get("recent_dirs", []) if r != path]
    recent.insert(0, path)
    d["recent_dirs"] = recent[:MAX_RECENT]
    CACHE_FILE.write_text(json.dumps(d, indent=2))


def cfg_from_cache():
    """Build a worker config from the cached settings, or None if no library."""
    d = _load_cache()
    recent = d.get("recent_dirs", [])
    output_dir = recent[0] if recent else ""
    if not output_dir:
        return None
    return {
        "output_dir": output_dir,
        "dl_step": d.get("dl_step", True),
        "dl_pdf": d.get("dl_pdf", False),
        "lib_prefix": d.get("lib_prefix", ""),
        "jlcpcb_api_key": d.get("jlcpcb_api_key", ""),
    }


def run_import(pids, cfg):
    """Import every pid headlessly. Returns a process exit code (0 = all ok)."""
    from PySide6.QtCore import QCoreApplication, QTimer

    from gui.worker import Worker
    from lib.helpers import ensure_libraries

    app = QCoreApplication(sys.argv)
    ensure_libraries(cfg["output_dir"], cfg["lib_prefix"])

    worker = Worker()
    remaining = set(pids)
    failures = {}

    def _finish(pid):
        remaining.discard(pid)
        if not remaining:
            QTimer.singleShot(0, app.quit)

    # NOTE: these slots must not use the logging module — the worker installs a
    # root-logger handler that re-emits records through log_line, so logging here
    # would recurse infinitely. Print directly instead.
    def _on_log(pid, msg):
        _out(f"[{pid}] {msg}")

    def _on_done(pid, step, ok, extra):
        if ok:
            _out(f"[{pid}] {step}: OK")
        else:
            err = extra.get("error", "")
            _err(f"[{pid}] {step}: FAIL {err}".rstrip())
            if step == "valid":
                failures[pid] = err or "validation failed"
        # A part is done after its final step (pdf) or an early validate failure.
        if step == "pdf" or (step == "valid" and not ok):
            _finish(pid)

    worker.log_line.connect(_on_log)
    worker.step_done.connect(_on_done)
    worker.start()
    for pid in pids:
        worker.process(pid, cfg)

    app.exec()
    worker.stop()
    worker.wait(2000)

    if failures:
        _err(f"{len(failures)} part(s) failed: {', '.join(sorted(failures))}")
        return 1
    return 0


def run_cli(args):
    """Entry point for CLI mode. Returns a process exit code."""
    if args.set_library:
        from pathlib import Path

        path = str(Path(args.set_library).expanduser().resolve())
        save_library_path(path)
        _out(f"Library location set to: {path}")
        if not (args.parts or args.file):
            return 0

    from lib.bulk import parse_parts, read_parts_file

    tokens = list(args.parts or [])
    if args.file:
        try:
            tokens += read_parts_file(args.file)
        except OSError as e:
            _err(f"Cannot read file '{args.file}': {e}")
            return 2

    pids, invalid = parse_parts(tokens)
    for inv in invalid:
        _err(f"Ignoring invalid part: {inv}")
    if not pids:
        _err("No valid LCSC part numbers provided.")
        return 2

    cfg = cfg_from_cache()
    if cfg is None:
        _err(
            "No library location set. Set one first:\n"
            "  python gui2.py --set-library /path/to/library"
        )
        return 2

    _out(f"Importing {len(pids)} part(s) into {cfg['output_dir']}")
    return run_import(pids, cfg)
