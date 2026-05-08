"""
LCSC to KiCad Converter — KiCad Action Plugin

Supports two installation layouts:

  Repo-symlink (install_plugin.sh):
    scripting/plugins/lcsc2kicad/  →  kicad_plugin/
    gui2.py lives one level up at the repo root.
    Dependencies are expected in that repo's venv/.

  PCM self-contained (build_pcm.sh / KiCad "Install from File"):
    3rdparty/plugins/com_emmertex_lcsc2kicad/
    gui2.py is bundled alongside this file.
    On first run the plugin offers to install dependencies into a local venv.

Note on toolbar placement: pcbnew.ActionPlugin toolbar buttons only appear
in the PCB editor — this is a KiCad API limitation.  The plugin is
accessible from the schematic editor via Tools → External Plugins (menu).
"""

import shutil
import subprocess
import sys
from pathlib import Path

import pcbnew

_PLUGIN_DIR = Path(__file__).parent.resolve()
_ICON = _PLUGIN_DIR / "icon.png"

_GUI_CANDIDATES = [
    _PLUGIN_DIR / "gui2.py",           # PCM self-contained install
    _PLUGIN_DIR.parent / "gui2.py",    # Repo symlink install
]


# ── Helpers ───────────────────────────────────────────────────────────────────

def _find_gui():
    for p in _GUI_CANDIDATES:
        if p.exists():
            return p
    return None


def _check_pyside6(python: str) -> bool:
    """Return True if this interpreter can import PySide6."""
    try:
        r = subprocess.run(
            [python, "-c", "import PySide6"],
            capture_output=True, timeout=15,
        )
        return r.returncode == 0
    except Exception:
        return False


def _find_python(gui: Path) -> str | None:
    """
    Return a Python interpreter path that has PySide6, or None if not found.
    Preference order:
      1. venv next to gui2.py  (PCM mode, after first-run setup)
      2. venv one level up     (repo symlink mode)
      3. system python3 from PATH
      4. KiCad's own interpreter (last resort)
    """
    candidates = [
        gui.parent / "venv" / "bin" / "python",
        gui.parent.parent / "venv" / "bin" / "python",
    ]
    for p in candidates:
        if p.exists() and _check_pyside6(str(p)):
            return str(p)

    for name in ("python3", "python"):
        found = shutil.which(name)
        if found and _check_pyside6(found):
            return found

    if _check_pyside6(sys.executable):
        return sys.executable

    return None


def _create_venv(venv_dir: Path, requirements: Path) -> tuple[bool, str]:
    """
    Create a venv and install requirements.txt into it.
    Returns (success, error_message).
    """
    try:
        r = subprocess.run(
            [sys.executable, "-m", "venv", str(venv_dir)],
            capture_output=True, timeout=120,
        )
        if r.returncode != 0:
            return False, r.stderr.decode(errors="replace")

        pip = venv_dir / "bin" / "pip"
        r = subprocess.run(
            [str(pip), "install", "--upgrade", "pip", "--quiet"],
            capture_output=True, timeout=120,
        )
        r = subprocess.run(
            [str(pip), "install", "-r", str(requirements), "--quiet"],
            capture_output=True, timeout=600,
        )
        if r.returncode != 0:
            return False, r.stderr.decode(errors="replace")

        return True, ""
    except subprocess.TimeoutExpired:
        return False, "Timed out during installation."
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
        import wx  # always available inside KiCad

        # ── Locate gui2.py ────────────────────────────────────────────────────
        gui = _find_gui()
        if gui is None:
            wx.MessageBox(
                "gui2.py not found.\n\nExpected locations:\n" +
                "\n".join(f"  {p}" for p in _GUI_CANDIDATES) +
                "\n\nSee the README for installation instructions.",
                "LCSC to KiCad — Launch Error",
                wx.OK | wx.ICON_ERROR,
            )
            return

        # ── Find a Python with PySide6 ────────────────────────────────────────
        python = _find_python(gui)

        if python is None:
            # Offer to install dependencies into a local venv
            requirements = gui.parent / "requirements.txt"
            if not requirements.exists():
                wx.MessageBox(
                    "PySide6 is not available and requirements.txt was not found.\n\n"
                    "Please install PySide6 manually:\n  pip install PySide6\n\n"
                    "Then restart KiCad.",
                    "LCSC to KiCad — Missing Dependency",
                    wx.OK | wx.ICON_ERROR,
                )
                return

            answer = wx.MessageBox(
                "LCSC to KiCad Converter needs Python dependencies that are not\n"
                "currently installed (PySide6, requests, lxml, etc.).\n\n"
                "A virtual environment will be created at:\n"
                f"  {gui.parent / 'venv'}\n\n"
                "This is a one-time setup that may take a minute or two.\n\n"
                "Install now?",
                "LCSC to KiCad — First-time Setup",
                wx.YES_NO | wx.ICON_QUESTION,
            )
            if answer != wx.YES:
                return

            venv_dir = gui.parent / "venv"
            busy = wx.BusyInfo(
                "Installing dependencies — please wait…\n"
                "(This window will close when done.)"
            )
            ok, err = _create_venv(venv_dir, requirements)
            del busy

            if not ok:
                wx.MessageBox(
                    f"Dependency installation failed:\n\n{err}\n\n"
                    "You can install manually:\n"
                    f"  python3 -m venv {venv_dir}\n"
                    f"  {venv_dir}/bin/pip install -r {requirements}",
                    "LCSC to KiCad — Setup Failed",
                    wx.OK | wx.ICON_ERROR,
                )
                return

            python = _find_python(gui)
            if python is None:
                wx.MessageBox(
                    "Setup appeared to succeed but PySide6 still cannot be\n"
                    "imported. Please check the installation manually.",
                    "LCSC to KiCad — Setup Error",
                    wx.OK | wx.ICON_ERROR,
                )
                return

        # ── Launch gui2.py ────────────────────────────────────────────────────
        try:
            subprocess.Popen(
                [python, str(gui)],
                cwd=str(gui.parent),
                stderr=subprocess.PIPE,
                start_new_session=True,
            )
        except Exception as e:
            wx.MessageBox(
                f"Failed to launch LCSC to KiCad Converter.\n\n"
                f"Python:  {python}\n"
                f"Script:  {gui}\n\n"
                f"Error: {e}",
                "LCSC to KiCad — Launch Error",
                wx.OK | wx.ICON_ERROR,
            )


LCSCtoKiCadAction().register()
