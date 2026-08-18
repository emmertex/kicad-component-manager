"""Headless command-line import.

Runs the same import pipeline as the GUI Worker, with console logging.
"""

import sys
import threading

from gui.cache import bump_recent, load_cache, save_cache

def _out(msg):
    print(msg, flush=True)


def _err(msg):
    print(msg, file=sys.stderr, flush=True)


def save_library_path(path):
    """Persist a library location to the shared cache (front of recent_dirs)."""
    d = load_cache()
    d["recent_dirs"] = bump_recent(d.get("recent_dirs", []), path)
    save_cache(d)


def cfg_from_cache():
    """Build a worker config from the cached settings, or None if no library."""
    d = load_cache()
    recent = d.get("recent_dirs", [])
    output_dir = recent[0] if recent else ""
    if not output_dir:
        return None
    return {
        "output_dir": output_dir,
        "dl_step": d.get("dl_step", True),
        "dl_pdf": d.get("dl_pdf", False),
        "lib_prefix": d.get("lib_prefix", ""),
        "lib_mode": d.get("lib_mode", "organised"),
        "jlcpcb_api_key": d.get("jlcpcb_api_key", ""),
    }


def run_import(pids, cfg):
    """Import every pid headlessly. Returns a process exit code (0 = all ok)."""
    from gui.worker import Worker
    from lib.helpers import ensure_libraries

    ensure_libraries(cfg["output_dir"], cfg["lib_prefix"], cfg.get("lib_mode", "organised"))

    done = threading.Event()
    remaining = set(pids)
    failures = {}

    def _finish(pid):
        remaining.discard(pid)
        if not remaining:
            done.set()

    def _on_log(pid, msg):
        _out(f"[{pid}] {msg}")

    def _on_done(pid, step, ok, extra):
        extra = extra or {}
        if ok:
            _out(f"[{pid}] {step}: OK")
        else:
            err = extra.get("error", "")
            _err(f"[{pid}] {step}: FAIL {err}".rstrip())
            if step == "valid":
                failures[pid] = err or "validation failed"
        if step == "pdf" or (step == "valid" and not ok):
            _finish(pid)

    worker = Worker()
    worker.on_log_line = _on_log
    worker.on_step_done = _on_done
    worker.start()
    for pid in pids:
        worker.process(pid, cfg)

    done.wait()
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
            "  python manager.py --set-library /path/to/library"
        )
        return 2

    _out(f"Importing {len(pids)} part(s) into {cfg['output_dir']}")
    return run_import(pids, cfg)
