"""wxPython smoke tests (skipped if wx is unavailable)."""

import pytest

wx = pytest.importorskip("wx")

from gui.widgets import MainFrame  # noqa: E402


@pytest.fixture
def wx_app():
    app = wx.App(False)
    yield app
    app.Destroy()


def test_main_frame_has_single_notebook(wx_app):
    frame = MainFrame()
    try:
        assert frame._nb.GetPageCount() == 2
        assert frame._nb.GetPageText(0) == "Library"
        assert frame._nb.GetPageText(1) == "BOM"
        assert frame.GetTitle() == "KiCad Component Manager"
    finally:
        frame.Destroy()


def test_populate_bom_groups_rows(wx_app, tmp_path):
    frame = MainFrame()
    try:
        frame._output_dir = str(tmp_path)
        frame._bom_data = [
            {"ref": "R1", "val": "10k", "lcsc": "C1", "fp": "R", "lib": "l"},
            {"ref": "R2", "val": "10k", "lcsc": "C1", "fp": "R", "lib": "l"},
        ]
        frame._pcb_refs = {
            "R1": {"lib": "l", "fp": "R"},
            "R2": {"lib": "l", "fp": "R"},
        }
        frame._populate_bom()
        assert frame._bom_tbl.GetItemCount() == 1
        assert "R1" in frame._bom_tbl.get_text(0, 0)
    finally:
        frame.Destroy()
