"""
LCSC to KiCad Converter — KiCad Action Plugin

Supports two installation layouts:

  Repo-symlink (install_plugin.sh):
    scripting/plugins/lcsc2kicad/  →  kicad_plugin/
    gui2.py lives one level up at the repo root.

  PCM self-contained (build_pcm.sh / KiCad "Install from File"):
    3rdparty/plugins/com_emmertex_lcsc2kicad/
    gui2.py is bundled alongside this file.
    On first run the plugin offers to install dependencies into a local venv.

Note on toolbar placement: pcbnew.ActionPlugin toolbar buttons only appear
in the PCB editor — this is a KiCad API limitation.  The plugin is
accessible from the schematic editor via Tools → External Plugins (menu).

Debug log: ~/.lcsc2kicad_plugin.log
"""

import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pcbnew

_PLUGIN_DIR = Path(__file__).parent.resolve()
_ICON       = _PLUGIN_DIR / "icon.png"
_LOG_FILE   = Path.home() / ".lcsc2kicad_plugin.log"

_GUI_CANDIDATES = [
    _PLUGIN_DIR / "gui2.py",           # PCM self-contained install
    _PLUGIN_DIR.parent / "gui2.py",    # Repo symlink install
]


# ── Logging ───────────────────────────────────────────────────────────────────

def _log(msg: str):
    try:
        with open(_LOG_FILE, "a") as f:
            f.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}\n")
    except Exception:
        pass


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_gui():
    for p in _GUI_CANDIDATES:
        _log(f"gui candidate: {p} — {'found' if p.exists() else 'not found'}")
        if p.exists():
            return p
    return None


def _find_python(gui: Path) -> str:
    """
    Return a Python interpreter path. Checks venvs by file existence only
    (no subprocess calls), then falls back to system python3 / sys.executable.
    """
    candidates = [
        gui.parent / "venv" / "bin" / "python",
        gui.parent.parent / "venv" / "bin" / "python",
    ]
    for p in candidates:
        _log(f"python candidate: {p} — {'found' if p.exists() else 'not found'}")
        if p.exists():
            return str(p)

    sys_py = shutil.which("python3")
    _log(f"shutil.which('python3') = {sys_py}")
    if sys_py:
        return sys_py

    _log(f"falling back to sys.executable = {sys.executable}")
    return sys.executable


def _create_venv(venv_dir: Path, requirements: Path) -> tuple[bool, str]:
    try:
        r = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0:
            return False, r.stderr.decode(errors="replace")
        pip = venv_dir / "bin" / "pip"
        subprocess.run([str(pip), "install", "--upgrade", "pip", "--quiet"],
                       capture_output=True, timeout=120)
        r = subprocess.run(
            [str(pip), "install", "-r", str(requirements), "--quiet"],
            capture_output=True, timeout=600,
        )
        if r.returncode != 0:
            return False, r.stderr.decode(errors="replace")
        return True, ""
    except Exception as e:
        return False, str(e)


# ── Action plugin ─────────────────────────────────────────────────────────────

class LCSCtoKiCadAction(pcbnew.ActionPlugin):
    def defaults(self):
        self.name = "LCSC to KiCad Converter"
        self.category = "Library Management"
        self.description = (
            "Download LCSC/JLCPCB parts and add them to your KiCad libraries"
        )
        self.show_toolbar_button = True
        if _ICON.exists():
            self.icon_file_name = str(_ICON)

    def Run(self):
        _log("=" * 60)
        _log("Run() called")
        _log(f"sys.executable = {sys.executable}")
        _log(f"sys.version    = {sys.version}")
        _log(f"_PLUGIN_DIR    = {_PLUGIN_DIR}")

        try:
            self._run()
        except Exception:
            tb = traceback.format_exc()
            _log(f"UNHANDLED EXCEPTION in Run():\n{tb}")
            try:
                import wx
                wx.MessageBox(
                    f"LCSC to KiCad plugin error:\n\n{tb}",
                    "LCSC to KiCad — Error",
                    wx.OK | wx.ICON_ERROR,
                )
            except Exception as wx_err:
                _log(f"wx.MessageBox also failed: {wx_err}")

    def _run(self):
        import wx

        # ── Locate gui2.py ────────────────────────────────────────────────────
        gui = _find_gui()
        if gui is None:
            msg = (
                "gui2.py not found.\n\nExpected locations:\n" +
                "\n".join(f"  {p}" for p in _GUI_CANDIDATES)
            )
            _log(msg)
            wx.MessageBox(msg, "LCSC to KiCad — Launch Error", wx.OK | wx.ICON_ERROR)
            return

        _log(f"Using gui: {gui}")
        python = _find_python(gui)
        _log(f"Using python: {python}")

        # ── Check all required packages are importable ────────────────────────
        _log("Testing required imports...")
        try:
            result = subprocess.run(
                [python, "-c",
                 "import PySide6, requests, lxml, bs4, KicadModTree; print('ok')"],
                capture_output=True, timeout=15,
            )
            _log(f"deps test returncode={result.returncode} "
                 f"stdout={result.stdout.decode().strip()} "
                 f"stderr={result.stderr.decode().strip()}")
            deps_ok = result.returncode == 0
        except Exception as e:
            _log(f"deps test exception: {e}")
            deps_ok = False

        if not deps_ok:
            requirements = gui.parent / "requirements.txt"
            if not requirements.exists():
                msg = (
                    f"PySide6 is not available with:\n  {python}\n\n"
                    "Install it with:  pip install PySide6"
                )
                _log(msg)
                wx.MessageBox(msg, "LCSC to KiCad — Missing Dependency",
                              wx.OK | wx.ICON_ERROR)
                return

            answer = wx.MessageBox(
                "LCSC to KiCad Converter needs Python dependencies that are\n"
                "not currently installed (PySide6, requests, lxml, etc.).\n\n"
                f"A virtual environment will be created at:\n  {gui.parent / 'venv'}\n\n"
                "This is a one-time setup (~1–2 minutes).\n\nInstall now?",
                "LCSC to KiCad — First-time Setup",
                wx.YES_NO | wx.ICON_QUESTION,
            )
            if answer != wx.YES:
                return

            venv_dir = gui.parent / "venv"
            _log(f"Creating venv at {venv_dir}")
            busy = wx.BusyInfo("Installing dependencies — please wait…")
            ok, err = _create_venv(venv_dir, requirements)
            del busy
            _log(f"Venv creation: ok={ok} err={err}")

            if not ok:
                wx.MessageBox(
                    f"Dependency installation failed:\n\n{err}",
                    "LCSC to KiCad — Setup Failed",
                    wx.OK | wx.ICON_ERROR,
                )
                return

            python = str(venv_dir / "bin" / "python")

        # ── Launch gui2.py ────────────────────────────────────────────────────
        _log(f"Launching: {python} {gui}")
        gui_log = _LOG_FILE.parent / ".lcsc2kicad_gui.log"
        try:
            with open(gui_log, "a") as gui_stderr:
                proc = subprocess.Popen(
                    [python, str(gui)],
                    cwd=str(gui.parent),
                    stdout=gui_stderr,
                    stderr=gui_stderr,
                    start_new_session=True,
                )
            _log(f"Popen succeeded, pid={proc.pid}  (gui stderr → {gui_log})")
        except Exception as e:
            _log(f"Popen failed: {e}")
            wx.MessageBox(
                f"Failed to launch LCSC to KiCad Converter.\n\n"
                f"Python:  {python}\n"
                f"Script:  {gui}\n\n"
                f"Error: {e}",
                "LCSC to KiCad — Launch Error",
                wx.OK | wx.ICON_ERROR,
            )


_log(f"Plugin module loading from {_PLUGIN_DIR}")
LCSCtoKiCadAction().register()
_log("Plugin registered OK")
