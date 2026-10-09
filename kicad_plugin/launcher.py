"""Launch manager.py in its own Python from inside KiCad.

Shared by both plugin front ends:

  __init__.py      SWIG ``pcbnew.ActionPlugin`` (KiCad 7 – 10.0)
  ipc_plugin.py    IPC API plugin, see plugin.json (KiCad 10.99+)

Neither runs in an interpreter with the GUI's dependencies, so this module
must only use the standard library (plus wx when KiCad provides it).

Debug log: ~/.lcsc2kicad_plugin.log
"""

import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

_PLUGIN_DIR = Path(__file__).parent.resolve()
_ICON = _PLUGIN_DIR / "icon.png"
_LOG_FILE = Path.home() / ".lcsc2kicad_plugin.log"

_WIN = sys.platform == "win32"
_VBIN = "Scripts" if _WIN else "bin"
_VPY = "python.exe" if _WIN else "python"

_GUI_CANDIDATES = [
    _PLUGIN_DIR / "manager.py",  # PCM self-contained install
    _PLUGIN_DIR.parent / "manager.py",  # Repo symlink install
]


def _log(msg: str):
    try:
        with open(_LOG_FILE, "a") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


def _load_wx():
    """KiCad's plugin interpreter is not the venv used to launch manager.py."""
    try:
        import wx  # type: ignore[import-not-found]

        return wx
    except Exception as e:
        _log(f"wx not available in KiCad Python: {e}")
        return None


def _notify(title, msg, wx_mod=None, kind="error"):
    """Dialog when wx is present; otherwise log only (no pcbnew UI)."""
    _log(f"{title}: {msg}")
    if wx_mod is None:
        wx_mod = _load_wx()
    if wx_mod is None:
        return None
    flags = {
        "error": wx_mod.OK | wx_mod.ICON_ERROR,
        "info": wx_mod.OK | wx_mod.ICON_INFORMATION,
        "question": wx_mod.YES_NO | wx_mod.ICON_QUESTION,
    }.get(kind, wx_mod.OK | wx_mod.ICON_ERROR)
    try:
        return wx_mod.MessageBox(msg, title, flags)
    except Exception as e:
        _log(f"wx.MessageBox failed: {e}")
        return None


def _find_gui():
    for p in _GUI_CANDIDATES:
        _log(f"gui candidate: {p} — {'found' if p.exists() else 'not found'}")
        if p.exists():
            return p
    return None


def _find_python(gui: Path) -> "str | None":
    """
    Return a Python interpreter path, or None if no usable one is found.
    Checks venvs by file existence only (no subprocess calls).

    On Windows, sys.executable is kicad.exe — not a Python interpreter.
    KiCad ships python.exe alongside kicad.exe, so we check that explicitly
    before falling back to anything on PATH.  The Windows Store stub
    (WindowsApps\\python*.exe, exit 9009) is silently skipped.
    """
    candidates = [
        gui.parent / "venv" / _VBIN / _VPY,
        gui.parent.parent / "venv" / _VBIN / _VPY,
    ]
    if _WIN:
        candidates.append(Path(sys.executable).parent / _VPY)

    for p in candidates:
        _log(f"python candidate: {p} — {'found' if p.exists() else 'not found'}")
        if p.exists():
            return str(p)

    for name in ("python3", "python"):
        sys_py = shutil.which(name)
        _log(f"shutil.which({name!r}) = {sys_py}")
        if sys_py and "WindowsApps" not in (sys_py or ""):
            return sys_py

    if not _WIN:
        # Outside KiCad on POSIX, sys.executable is a real Python interpreter.
        _log(f"falling back to sys.executable = {sys.executable}")
        return sys.executable

    # On Windows sys.executable is kicad.exe — not a Python interpreter.
    _log("no suitable Python interpreter found")
    return None


def _create_venv(
    venv_dir: Path, requirements: Path, python: str = ""
) -> tuple[bool, str]:
    pip_name = "pip.exe" if _WIN else "pip"
    bootstrap = python or sys.executable
    try:
        r = subprocess.run(
            [bootstrap, "-m", "venv", str(venv_dir)],
            capture_output=True,
            timeout=120,
        )
        if r.returncode != 0:
            return False, r.stderr.decode(errors="replace")
        pip = venv_dir / _VBIN / pip_name
        subprocess.run(
            [str(pip), "install", "--upgrade", "pip", "--quiet"],
            capture_output=True,
            timeout=120,
        )
        r = subprocess.run(
            [str(pip), "install", "-r", str(requirements), "--quiet"],
            capture_output=True,
            timeout=600,
        )
        if r.returncode != 0:
            return False, r.stderr.decode(errors="replace")
        return True, ""
    except Exception as e:
        return False, str(e)


def run_manager(extra_args: "Callable[[], list[str]] | None" = None):
    """Launch manager.py; ``extra_args`` builds its CLI args (e.g. ``--bom``)."""
    _log("=" * 60)
    _log("run_manager() called")
    _log(f"sys.executable = {sys.executable}")
    _log(f"sys.version    = {sys.version}")
    _log(f"_PLUGIN_DIR    = {_PLUGIN_DIR}")

    wx = _load_wx()

    try:
        gui = _find_gui()
        if gui is None:
            msg = "manager.py not found.\n\nExpected locations:\n" + "\n".join(
                f"  {p}" for p in _GUI_CANDIDATES
            )
            _notify("KiCad Component Manager — Launch Error", msg, wx)
            return

        _log(f"Using gui: {gui}")
        python = _find_python(gui)
        if python is None:
            msg = (
                "No suitable Python interpreter found.\n\n"
                "Searched the venv locations, KiCad's bundled Python and PATH.\n\n"
                "Install Python 3.13+ from https://www.python.org/downloads/,\n"
                "or create a virtual environment at:\n"
                f"  {gui.parent / 'venv'}\n\n"
                f"Log: {_LOG_FILE}"
            )
            _notify("KiCad Component Manager — No Python", msg, wx)
            return
        _log(f"Using python: {python}")

        _log("Testing required imports...")
        try:
            result = subprocess.run(
                [
                    python,
                    "-c",
                    "import wx, requests, lxml, bs4, KicadModTree, easyeda2kicad; print('ok')",
                ],
                capture_output=True,
                timeout=15,
            )
            _log(
                f"deps test returncode={result.returncode} "
                f"stdout={result.stdout.decode().strip()} "
                f"stderr={result.stderr.decode().strip()}"
            )
            deps_ok = result.returncode == 0
        except Exception as e:
            _log(f"deps test exception: {e}")
            deps_ok = False

        if not deps_ok:
            # PCM packages put the GUI requirements beside plugin.json, where
            # requirements.txt is the IPC plugin's own (KiCad installs it).
            requirements = gui.parent / "requirements-gui.txt"
            if not requirements.exists():
                requirements = gui.parent / "requirements.txt"
            if not requirements.exists():
                msg = (
                    f"wxPython is not available with:\n  {python}\n\n"
                    "Install it with:  pip install wxPython"
                )
                _notify("KiCad Component Manager — Missing Dependency", msg, wx)
                return

            if wx is None:
                _notify(
                    "KiCad Component Manager — First-time Setup",
                    "Dependencies are missing and wxPython is not available in "
                    "KiCad's Python, so the install prompt cannot be shown.\n\n"
                    f"Install into a venv at {gui.parent / 'venv'} then retry.\n"
                    f"Log: {_LOG_FILE}",
                    wx,
                )
                return

            answer = wx.MessageBox(
                "KiCad Component Manager needs Python dependencies that are\n"
                "not currently installed (wxPython, requests, lxml, etc.).\n\n"
                f"A virtual environment will be created at:\n  {gui.parent / 'venv'}\n\n"
                "This is a one-time setup (~1–2 minutes).\n\nInstall now?",
                "KiCad Component Manager — First-time Setup",
                wx.YES_NO | wx.ICON_QUESTION,
            )
            if answer != wx.YES:
                return

            venv_dir = gui.parent / "venv"
            _log(f"Creating venv at {venv_dir} using {python}")
            busy = wx.BusyInfo("Installing dependencies — please wait…")
            ok, err = _create_venv(venv_dir, requirements, python=python)
            del busy
            _log(f"Venv creation: ok={ok} err={err}")

            if not ok:
                _notify(
                    "KiCad Component Manager — Setup Failed",
                    f"Dependency installation failed:\n\n{err}",
                    wx,
                )
                return

            python = str(venv_dir / _VBIN / _VPY)

        args = [python, str(gui)]
        if extra_args is not None:
            args.extend(extra_args())

        _log(f"Launching: {' '.join(args)}")
        gui_log = _LOG_FILE.parent / ".kicad_component_manager_gui.log"
        try:
            with open(gui_log, "a") as gui_stderr:
                if _WIN:
                    flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(
                        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
                    )
                    proc = subprocess.Popen(
                        args,
                        cwd=str(gui.parent),
                        stdout=gui_stderr,
                        stderr=gui_stderr,
                        creationflags=flags,
                    )
                else:
                    proc = subprocess.Popen(
                        args,
                        cwd=str(gui.parent),
                        stdout=gui_stderr,
                        stderr=gui_stderr,
                        start_new_session=True,
                    )
            _log(f"Popen succeeded, pid={proc.pid}  (gui stderr → {gui_log})")
        except Exception as e:
            _notify(
                "KiCad Component Manager — Launch Error",
                f"Failed to launch KiCad Component Manager.\n\n"
                f"Python:  {python}\n"
                f"Script:  {gui}\n\n"
                f"Error: {e}",
                wx,
            )

    except Exception:
        tb = traceback.format_exc()
        _notify(
            "KiCad Component Manager — Error",
            f"KiCad Component Manager error:\n\n{tb}",
            wx,
        )
