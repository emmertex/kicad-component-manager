"""
KiCad Component Manager — Library Management Tool

Supports two installation layouts:

  Repo-symlink (install_plugin.sh):
    scripting/plugins/lcsc2kicad/  →  kicad_plugin/
    manager.py lives one level up at the repo root.

  PCM self-contained (build_pcm.sh / KiCad "Install from File"):
    3rdparty/plugins/com_emmertex_lcsc2kicad/
    manager.py is bundled alongside this file.
    On first run the plugin offers to install dependencies into a local venv.

Note on toolbar placement: pcbnew.ActionPlugin toolbar buttons only appear
in the PCB editor — this is a KiCad API limitation.  The plugin is
accessible from the schematic editor via Tools → External Plugins (menu).

Debug log: ~/.lcsc2kicad_plugin.log
"""

import re
import shutil
import subprocess
import sys
import traceback
from datetime import datetime
from pathlib import Path

import pcbnew  # type: ignore[import-not-found]

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


def _find_python(gui: Path) -> str:
    """
    Return a Python interpreter path. Checks venvs by file existence only
    (no subprocess calls).

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

    _log(f"falling back to sys.executable = {sys.executable}")
    return sys.executable


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
        board = None
        try:
            board = pcbnew.GetBoard()
        except Exception:
            board = None
        if board is not None and board.GetFileName():
            _run_manager(mode="bom")
        else:
            _run_manager(mode="manager")


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
        _run_manager(mode="bom")


def _run_manager(mode="manager"):
    _log("=" * 60)
    _log(f"Run({mode=}) called")
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
        _log(f"Using python: {python}")

        _log("Testing required imports...")
        try:
            result = subprocess.run(
                [
                    python,
                    "-c",
                    "import wx, requests, lxml, bs4, KicadModTree; print('ok')",
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
        if mode == "bom":
            import json
            import tempfile

            board = pcbnew.GetBoard()
            parts = []
            for fp in board.GetFootprints():
                lcsc_pn = ""
                for field_name in [
                    "LCSC",
                    "LCSC Part #",
                    "LCSC Part Number",
                    "LCSC#",
                    "lcsc",
                    "LCSC_PN",
                    "LCSC Part",
                    "JLC",
                    "JLCPCB",
                ]:
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
            with tempfile.NamedTemporaryFile(
                mode="w", suffix=".json", delete=False
            ) as f:
                json.dump(bom_payload, f)
                args.extend(["--bom", f.name])
            _log(f"BOM data saved to {f.name}")

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


_log(f"Plugin module loading from {_PLUGIN_DIR}")
KiCadComponentManagerAction().register()
KiCadBOMManagerAction().register()
_log("Plugins registered OK")
