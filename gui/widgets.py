"""Single-window wxPython UI: Library + BOM notebook."""

from __future__ import annotations

import csv
import json
import re
import webbrowser
from pathlib import Path

import wx
import wx.lib.scrolledpanel as scrolled

from gui.cache import CACHE_FILE, CACHE_VERSION, MAX_RECENT, load_cache, migrate_col_visible, save_cache, bump_recent
from gui.models import (
    ATTR_COL,
    ATTR_PROP,
    BOM_COL_NAMES,
    BOM_STEP_COLS,
    C_DEL,
    COL_NAMES,
    COL_STEPS,
    COL_VIEW_NAMES,
    METADATA_FIELDS,
    ST_COLOR,
    STEP_COLS,
    PartState,
    St,
    apply_step_result,
    group_bom_parts,
    is_pcb_linked,
    row_subtotal,
)
from gui.worker import Worker
from lib.bulk import normalize_pid, parse_parts
from lib.categories import FINAL_CATEGORIES
from lib.helpers import (
    PDF_MIN_BYTES,
    _check_existing,
    _delete_from_lib,
    _find_sym_file,
    _parse_sym_file,
    _update_sym_lib_table,
    _update_symbol_property,
    ensure_libraries,
    list_sym_files,
    sync_libraries,
)
from lib.kicad_patch import (
    ensure_sym_in_lib_symbols,
    extract_sym_block_for_schematic,
    find_sch_files,
    patch_pcb_footprint_refs,
    patch_sch_footprint_refs,
    patch_sch_lib_id_refs,
)

# Re-export for lib.cli
__all__ = ["MainFrame", "CACHE_FILE", "MAX_RECENT"]


def _hex_color(hexstr):
    h = hexstr.lstrip("#")
    return wx.Colour(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _warn(parent, title, msg):
    wx.MessageBox(msg, title, wx.OK | wx.ICON_WARNING, parent)


def _info(parent, title, msg):
    wx.MessageBox(msg, title, wx.OK | wx.ICON_INFORMATION, parent)


def _ask(parent, title, msg):
    return wx.MessageBox(msg, title, wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, parent) == wx.YES


class StatusListCtrl(wx.ListCtrl):
    def __init__(self, parent, colnames, widths):
        super().__init__(parent, style=wx.LC_REPORT | wx.LC_SINGLE_SEL)
        for i, name in enumerate(colnames):
            self.InsertColumn(i, name, width=widths[i] if i < len(widths) else 80)

    def append_row(self, values):
        idx = self.GetItemCount()
        self.InsertItem(idx, values[0] if values else "")
        for c, v in enumerate(values[1:], 1):
            self.SetItem(idx, c, v)
        return idx

    def set_cell(self, row, col, text, color=None, tip=""):
        self.SetItem(row, col, text)
        if color:
            self.SetItemTextColour(row, _hex_color(color) if isinstance(color, str) else color)
        if tip:
            self.SetItem(row, col, text)  # tooltip via ItemData not native; skip

    def get_text(self, row, col):
        return self.GetItem(row, col).GetText()

    def set_text(self, row, col, text):
        self.SetItem(row, col, text)

    def find_pid_rows(self, pid, col):
        return [r for r in range(self.GetItemCount()) if self.get_text(r, col) == pid]


class SettingsDialog(wx.Dialog):
    LIB_MODE_LABELS = (
        ("organised", "Organised — per-category libraries"),
        ("consolidated", "Consolidated — single 'components' library"),
        ("both_organised", "Both (Organised Primary) — list from category libs"),
        ("both_consolidated", "Both (Consolidated Primary) — list from 'components'"),
    )

    def __init__(
        self,
        parent,
        dl_step,
        dl_pdf,
        col_visible,
        lib_prefix="",
        jlcpcb_api_key="",
        output_dir="",
        recent_dirs=None,
        allow_edit_category=False,
        lib_mode="organised",
    ):
        super().__init__(parent, title="Settings", size=(540, 720))
        self._parent_frame = parent
        vbox = wx.BoxSizer(wx.VERTICAL)

        loc_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Library Location")
        loc_row = wx.BoxSizer(wx.HORIZONTAL)
        self._loc_combo = wx.ComboBox(self, choices=list(recent_dirs or []), style=wx.CB_DROPDOWN)
        if output_dir:
            self._loc_combo.SetValue(output_dir)
        elif recent_dirs:
            self._loc_combo.SetSelection(0)
        browse = wx.Button(self, label="Browse…")
        browse.Bind(wx.EVT_BUTTON, self._browse_loc)
        loc_row.Add(self._loc_combo, 1, wx.EXPAND | wx.RIGHT, 6)
        loc_row.Add(browse, 0)
        loc_box.Add(loc_row, 0, wx.EXPAND | wx.ALL, 6)
        vbox.Add(loc_box, 0, wx.EXPAND | wx.ALL, 8)

        lib_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Library Options")
        self._mode_combo = wx.Choice(self, choices=[lab for _, lab in self.LIB_MODE_LABELS])
        idx = next((i for i, (v, _) in enumerate(self.LIB_MODE_LABELS) if v == lib_mode), 0)
        self._mode_combo.SetSelection(idx)
        lib_box.Add(wx.StaticText(self, label="Library Mode:"), 0, wx.LEFT | wx.TOP, 6)
        lib_box.Add(self._mode_combo, 0, wx.EXPAND | wx.ALL, 6)
        sync_btn = wx.Button(self, label="Sync Libraries")
        sync_btn.Bind(wx.EVT_BUTTON, self._sync_libraries)
        lib_box.Add(sync_btn, 0, wx.ALL, 6)
        self._prefix_edit = wx.TextCtrl(self, value=lib_prefix)
        lib_box.Add(wx.StaticText(self, label="Library Prefix:"), 0, wx.LEFT, 6)
        lib_box.Add(self._prefix_edit, 0, wx.EXPAND | wx.ALL, 6)
        self._api_key_edit = wx.TextCtrl(self, value=jlcpcb_api_key, style=wx.TE_PASSWORD)
        lib_box.Add(wx.StaticText(self, label="JLCPCB API Key:"), 0, wx.LEFT, 6)
        lib_box.Add(self._api_key_edit, 0, wx.EXPAND | wx.ALL, 6)
        self._edit_cat_cb = wx.CheckBox(self, label="Allow editing category (relabels metadata only)")
        self._edit_cat_cb.SetValue(allow_edit_category)
        lib_box.Add(self._edit_cat_cb, 0, wx.ALL, 6)
        vbox.Add(lib_box, 0, wx.EXPAND | wx.ALL, 8)

        dl_box = wx.StaticBoxSizer(wx.VERTICAL, self, "Download Options")
        self._step_cb = wx.CheckBox(self, label="Download STEP")
        self._step_cb.SetValue(dl_step)
        self._pdf_cb = wx.CheckBox(self, label="Download PDF")
        self._pdf_cb.SetValue(dl_pdf)
        dl_box.Add(self._step_cb, 0, wx.ALL, 4)
        dl_box.Add(self._pdf_cb, 0, wx.ALL, 4)
        vbox.Add(dl_box, 0, wx.EXPAND | wx.ALL, 8)

        view_box = wx.StaticBoxSizer(wx.VERTICAL, self, "View Options")
        grid = wx.FlexGridSizer(cols=2, hgap=8, vgap=4)
        self._col_cbs = []
        for i, name in enumerate(COL_VIEW_NAMES):
            cb = wx.CheckBox(self, label=name)
            cb.SetValue(bool(col_visible[i]) if i < len(col_visible) else True)
            self._col_cbs.append(cb)
            grid.Add(cb, 0)
        view_box.Add(grid, 0, wx.ALL, 6)
        vbox.Add(view_box, 1, wx.EXPAND | wx.ALL, 8)

        btns = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        vbox.Add(btns, 0, wx.EXPAND | wx.ALL, 8)
        self.SetSizer(vbox)
        self.CentreOnParent()

    def _browse_loc(self, _evt=None):
        dlg = wx.DirDialog(self, "Select Library Directory")
        if dlg.ShowModal() == wx.ID_OK:
            d = dlg.GetPath()
            if self._loc_combo.FindString(d) == wx.NOT_FOUND:
                self._loc_combo.Insert(d, 0)
            self._loc_combo.SetValue(d)
        dlg.Destroy()

    def _sync_libraries(self, _evt=None):
        out = self.output_dir
        if not out or not Path(out).exists():
            _warn(self, "No Library", "Set a valid Library Location first.")
            return
        try:
            wx.BeginBusyCursor()
            parts, copies = sync_libraries(out, self.lib_prefix)
        except Exception as e:
            _warn(self, "Sync Failed", str(e))
            return
        finally:
            wx.EndBusyCursor()
        parent = self._parent_frame
        if parent is not None and hasattr(parent, "refresh_after_sync"):
            parent.refresh_after_sync(out)
        if copies:
            msg = (
                f"Copied {copies} missing symbol(s) into the other layout.\n"
                f"All {parts} symbol(s) are now in both the category libraries and 'components'."
            )
        else:
            msg = (
                f"Already in sync — all {parts} symbol(s) are present in both layouts."
            )
        _info(self, "Sync Libraries", msg)

    @property
    def dl_step(self):
        return self._step_cb.GetValue()

    @property
    def dl_pdf(self):
        return self._pdf_cb.GetValue()

    @property
    def col_visible(self):
        return [cb.GetValue() for cb in self._col_cbs]

    @property
    def output_dir(self):
        return self._loc_combo.GetValue().strip()

    @property
    def recent_dirs(self):
        dirs = [self._loc_combo.GetString(i) for i in range(self._loc_combo.GetCount())]
        cur = self.output_dir
        if cur and cur not in dirs:
            dirs.insert(0, cur)
        return list(dict.fromkeys(dirs))[:MAX_RECENT]

    @property
    def lib_mode(self):
        i = self._mode_combo.GetSelection()
        if i < 0:
            return "organised"
        return self.LIB_MODE_LABELS[i][0]

    @property
    def lib_prefix(self):
        return self._prefix_edit.GetValue().strip()

    @property
    def jlcpcb_api_key(self):
        return self._api_key_edit.GetValue().strip()

    @property
    def allow_edit_category(self):
        return self._edit_cat_cb.GetValue()


class BulkImportDialog(wx.Dialog):
    def __init__(self, parent):
        super().__init__(parent, title="Bulk Import", size=(400, 360))
        v = wx.BoxSizer(wx.VERTICAL)
        v.Add(wx.StaticText(self, label="Paste part numbers (one per line), or load from a file:"), 0, wx.ALL, 6)
        self._text = wx.TextCtrl(self, style=wx.TE_MULTILINE)
        v.Add(self._text, 1, wx.EXPAND | wx.ALL, 6)
        file_btn = wx.Button(self, label="Select File…")
        file_btn.Bind(wx.EVT_BUTTON, self._load_file)
        v.Add(file_btn, 0, wx.ALL, 6)
        btns = self.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
        v.Add(btns, 0, wx.EXPAND | wx.ALL, 6)
        self.SetSizer(v)
        self.CentreOnParent()

    def _load_file(self, _evt=None):
        with wx.FileDialog(
            self, "Select Parts File", wildcard="Text files (*.txt;*.csv)|*.txt;*.csv|All files (*.*)|*.*"
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
            try:
                self._text.SetValue(Path(path).read_text(encoding="utf-8", errors="replace"))
            except OSError as e:
                _warn(self, "Error", f"Could not read file: {e}")

    def tokens(self):
        return self._text.GetValue().splitlines()


class MainFrame(wx.Frame):
    def __init__(self, parent=None, bom_file=None):
        super().__init__(parent, title="KiCad Component Manager", size=(1200, 780))
        self._bom_file = bom_file
        self._parts: dict[str, PartState] = {}
        self._bom_parts: dict[str, PartState] = {}
        self._bom_data = []
        self._pcb_file = ""
        self._pcb_refs: dict = {}
        self._row_refs: dict = {}
        self._dl_step = True
        self._dl_pdf = False
        self._lib_prefix = ""
        self._lib_mode = "organised"
        self._jlcpcb_api_key = ""
        self._output_dir = ""
        self._recent_dirs: list = []
        self._allow_edit_category = False
        self._col_visible = [True] * len(COL_NAMES)
        for _c in (3, 4, 5, 6, 7):  # mfr, cat, fp, pkg, attrs
            self._col_visible[_c] = False
        self._worker = Worker(marshal=lambda cb, *a: wx.CallAfter(cb, *a))
        self._worker.on_step_started = self._on_started
        self._worker.on_step_done = self._on_done
        self._worker.on_log_line = self._on_log
        self._worker.on_scrape_done = self._on_scrape_done
        self._worker.start()

        self._build_ui()
        self._load_cache()
        self._apply_category_editable()
        if self._output_dir:
            ensure_libraries(self._output_dir, self._lib_prefix, self._lib_mode)
        if self._bom_file:
            wx.CallAfter(self._load_bom_file)
        self.Bind(wx.EVT_CLOSE, self._on_close)

    def _build_ui(self):
        panel = wx.Panel(self)
        vbox = wx.BoxSizer(wx.VERTICAL)
        self._nb = wx.Notebook(panel)
        lib_page = wx.Panel(self._nb)
        bom_page = wx.Panel(self._nb)
        self._build_library_page(lib_page)
        self._build_bom_page(bom_page)
        self._nb.AddPage(lib_page, "Library")
        self._nb.AddPage(bom_page, "BOM")
        vbox.Add(self._nb, 1, wx.EXPAND | wx.ALL, 4)

        footer = wx.BoxSizer(wx.HORIZONTAL)
        load_btn = wx.Button(panel, label="Load Library")
        load_btn.Bind(wx.EVT_BUTTON, lambda e: self._load_library())
        settings_btn = wx.Button(panel, label="Settings…")
        settings_btn.Bind(wx.EVT_BUTTON, lambda e: self._open_settings())
        footer.Add(load_btn, 0, wx.RIGHT, 6)
        footer.Add(settings_btn, 0, wx.RIGHT, 6)
        footer.AddStretchSpacer()
        self._path_label = wx.StaticText(panel, label="")
        footer.Add(self._path_label, 0, wx.ALIGN_CENTER_VERTICAL)
        footer.AddStretchSpacer()
        exit_btn = wx.Button(panel, label="Exit")
        exit_btn.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        footer.Add(exit_btn, 0)
        vbox.Add(footer, 0, wx.EXPAND | wx.ALL, 8)
        panel.SetSizer(vbox)

    def _build_library_page(self, page):
        v = wx.BoxSizer(wx.VERTICAL)
        row = wx.BoxSizer(wx.HORIZONTAL)
        row.Add(wx.StaticText(page, label="LCSC Part Number:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 6)
        self._inp = wx.TextCtrl(page, style=wx.TE_PROCESS_ENTER)
        self._inp.Bind(wx.EVT_TEXT_ENTER, lambda e: self._add_part())
        row.Add(self._inp, 1, wx.RIGHT, 6)
        conv = wx.Button(page, label="Convert")
        conv.Bind(wx.EVT_BUTTON, lambda e: self._add_part())
        bulk = wx.Button(page, label="Bulk Import")
        bulk.Bind(wx.EVT_BUTTON, lambda e: self._bulk_import())
        row.Add(conv, 0, wx.RIGHT, 6)
        row.Add(bulk, 0)
        v.Add(row, 0, wx.EXPAND | wx.ALL, 6)

        split = wx.SplitterWindow(page)
        widths = [90, 140, 280, 110, 90, 140, 70, 140, 28, 28, 28, 28, 28, 28, 28]
        self._tbl = StatusListCtrl(split, COL_NAMES, widths)
        self._tbl.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: self._on_row(e.GetIndex()))
        self._tbl.Bind(wx.EVT_LIST_ITEM_ACTIVATED, self._on_lib_activate)

        bottom = wx.SplitterWindow(split)
        self._log = wx.TextCtrl(bottom, style=wx.TE_MULTILINE | wx.TE_READONLY)
        detail = scrolled.ScrolledPanel(bottom)
        dv = wx.BoxSizer(wx.VERTICAL)
        btn_row = wx.BoxSizer(wx.HORIZONTAL)
        self._lcsc_btn = wx.Button(detail, label="Open LCSC")
        self._lcsc_btn.Disable()
        self._lcsc_btn.Bind(wx.EVT_BUTTON, lambda e: self._open_lcsc())
        self._pdf_btn = wx.Button(detail, label="Open PDF")
        self._pdf_btn.Disable()
        self._pdf_btn.Bind(wx.EVT_BUTTON, lambda e: self._open_pdf())
        self._scrape_btn = wx.Button(detail, label="Scrape Data")
        self._scrape_btn.Disable()
        self._scrape_btn.Bind(wx.EVT_BUTTON, lambda e: self._scrape_current())
        btn_row.Add(self._lcsc_btn, 0, wx.RIGHT, 4)
        btn_row.Add(self._pdf_btn, 0, wx.RIGHT, 4)
        btn_row.Add(self._scrape_btn, 0)
        dv.Add(btn_row, 0, wx.ALL, 4)
        form = wx.FlexGridSizer(cols=2, hgap=6, vgap=4)
        form.AddGrowableCol(1, 1)
        self._meta_widgets = {}
        for label, attr, editable in METADATA_FIELDS:
            form.Add(wx.StaticText(detail, label=label + ":"), 0, wx.ALIGN_CENTER_VERTICAL)
            if attr == "category":
                w = wx.ComboBox(detail, choices=list(FINAL_CATEGORIES), style=wx.CB_DROPDOWN)
                w.Bind(wx.EVT_COMBOBOX, lambda e, a=attr, ctrl=w: self._on_meta_edit(a, ctrl))
                w.Bind(wx.EVT_TEXT_ENTER, lambda e, a=attr, ctrl=w: self._on_meta_edit(a, ctrl))
            else:
                w = wx.TextCtrl(detail, style=0 if editable else wx.TE_READONLY)
                if editable:
                    w.Bind(wx.EVT_KILL_FOCUS, lambda e, a=attr, ctrl=w: self._on_meta_edit(a, ctrl))
            self._meta_widgets[attr] = w
            form.Add(w, 1, wx.EXPAND)
        dv.Add(form, 1, wx.EXPAND | wx.ALL, 4)
        detail.SetSizer(dv)
        detail.SetupScrolling()
        bottom.SplitVertically(self._log, detail, 420)
        split.SplitHorizontally(self._tbl, bottom, 420)
        v.Add(split, 1, wx.EXPAND)
        page.SetSizer(v)

    def _build_bom_page(self, page):
        v = wx.BoxSizer(wx.VERTICAL)
        widths = [150, 100, 120, 220, 40, 70, 28, 28, 28, 28, 28, 28, 70, 80, 40]
        self._bom_tbl = StatusListCtrl(page, BOM_COL_NAMES, widths)
        self._bom_tbl.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: self._on_bom_row(e.GetIndex()))
        v.Add(self._bom_tbl, 1, wx.EXPAND | wx.ALL, 4)

        actions = wx.BoxSizer(wx.HORIZONTAL)
        self._lcsc_input = wx.TextCtrl(page)
        self._save_lcsc_btn = wx.Button(page, label="Save LCSC #")
        self._save_lcsc_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_save_lcsc())
        self._action_btn = wx.Button(page, label="Download Component")
        self._action_btn.Disable()
        self._action_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_action_btn())
        self._replace_sym_btn = wx.Button(page, label="Replace Symbol")
        self._replace_sym_btn.Disable()
        self._replace_sym_btn.Bind(wx.EVT_BUTTON, lambda e: self._on_replace_sym())
        fetch_one = wx.Button(page, label="Fetch Price/Stock")
        fetch_one.Bind(wx.EVT_BUTTON, lambda e: self._on_fetch_single())
        actions.Add(wx.StaticText(page, label="LCSC #:"), 0, wx.ALIGN_CENTER_VERTICAL | wx.RIGHT, 4)
        actions.Add(self._lcsc_input, 1, wx.RIGHT, 4)
        actions.Add(self._save_lcsc_btn, 0, wx.RIGHT, 4)
        actions.Add(self._action_btn, 0, wx.RIGHT, 4)
        actions.Add(self._replace_sym_btn, 0, wx.RIGHT, 4)
        actions.Add(fetch_one, 0)
        v.Add(actions, 0, wx.EXPAND | wx.ALL, 4)

        foot = wx.BoxSizer(wx.HORIZONTAL)
        fp = wx.Button(page, label="Fetch Prices")
        fp.Bind(wx.EVT_BUTTON, lambda e: self._fetch_prices())
        fm = wx.Button(page, label="Fetch Missing Prices")
        fm.Bind(wx.EVT_BUTTON, lambda e: self._fetch_missing_prices())
        ex = wx.Button(page, label="Export CSV…")
        ex.Bind(wx.EVT_BUTTON, lambda e: self._export_csv())
        self._total_label = wx.StaticText(page, label="Total BOM Price: $0.00")
        foot.Add(fp, 0, wx.RIGHT, 6)
        foot.Add(fm, 0, wx.RIGHT, 6)
        foot.Add(ex, 0, wx.RIGHT, 6)
        foot.AddStretchSpacer()
        foot.Add(self._total_label, 0, wx.ALIGN_CENTER_VERTICAL)
        v.Add(foot, 0, wx.EXPAND | wx.ALL, 6)
        page.SetSizer(v)

    # ── cache / cfg ──────────────────────────────────────────────────────────
    def _cfg(self):
        return {
            "output_dir": self._output_dir,
            "dl_step": self._dl_step,
            "dl_pdf": self._dl_pdf,
            "lib_prefix": self._lib_prefix,
            "lib_mode": self._lib_mode,
            "jlcpcb_api_key": self._jlcpcb_api_key,
        }

    def _check_cfg(self):
        c = self._cfg()
        if not c["output_dir"]:
            _warn(self, "No Library", "Please set Library Location first.")
            return None
        return c

    def _load_cache(self):
        d = load_cache()
        if not d:
            return
        version = d.get("version", 0)
        self._recent_dirs = d.get("recent_dirs", [])
        if self._recent_dirs:
            self._output_dir = self._recent_dirs[0]
            self._path_label.SetLabel(self._output_dir)
        self._dl_step = d.get("dl_step", True)
        self._dl_pdf = d.get("dl_pdf", False)
        self._lib_prefix = d.get("lib_prefix", "")
        self._lib_mode = d.get("lib_mode", "organised")
        if self._lib_mode == "both":
            self._lib_mode = "both_organised"
        self._jlcpcb_api_key = d.get("jlcpcb_api_key", "")
        self._allow_edit_category = d.get("allow_edit_category", False)
        self._col_visible = migrate_col_visible(d.get("col_visible", []), version, len(COL_NAMES))

    def _save_cache(self):
        self._recent_dirs = bump_recent(self._recent_dirs, self._output_dir)
        try:
            save_cache(
                {
                    "version": CACHE_VERSION,
                    "recent_dirs": self._recent_dirs,
                    "dl_step": self._dl_step,
                    "dl_pdf": self._dl_pdf,
                    "col_visible": self._col_visible,
                    "lib_prefix": self._lib_prefix,
                    "lib_mode": self._lib_mode,
                    "jlcpcb_api_key": self._jlcpcb_api_key,
                    "allow_edit_category": self._allow_edit_category,
                }
            )
        except Exception:
            pass

    def _open_settings(self):
        try:
            dlg = SettingsDialog(
                self,
                self._dl_step,
                self._dl_pdf,
                self._col_visible,
                self._lib_prefix,
                self._jlcpcb_api_key,
                self._output_dir,
                self._recent_dirs,
                self._allow_edit_category,
                self._lib_mode,
            )
        except Exception as e:
            _warn(self, "Settings", f"Could not open Settings:\n{e}")
            return
        if dlg.ShowModal() == wx.ID_OK:
            self._dl_step = dlg.dl_step
            self._dl_pdf = dlg.dl_pdf
            self._col_visible = dlg.col_visible
            self._lib_prefix = dlg.lib_prefix
            self._lib_mode = dlg.lib_mode
            self._jlcpcb_api_key = dlg.jlcpcb_api_key
            self._output_dir = dlg.output_dir
            self._recent_dirs = dlg.recent_dirs
            self._allow_edit_category = dlg.allow_edit_category
            self._apply_category_editable()
            self._path_label.SetLabel(self._output_dir or "")
            self._save_cache()
            if self._output_dir:
                ensure_libraries(self._output_dir, self._lib_prefix, self._lib_mode)
        dlg.Destroy()

    def _apply_category_editable(self):
        w = self._meta_widgets.get("category")
        if w:
            w.Enable(self._allow_edit_category)

    # ── library table ────────────────────────────────────────────────────────
    def _lib_row(self, pid):
        rows = self._tbl.find_pid_rows(pid, 0)
        return rows[0] if rows else -1

    def _insert_row(self, pid, state: PartState):
        self._parts[pid] = state
        vals = [
            pid,
            state.value,
            state.description,
            state.mfr,
            state.category,
            state.fp_name_text,
            state.package,
            state.attributes,
            state.valid.value,
            state.symbol.value,
            state.footprint.value,
            state.step.value,
            state.pdf.value,
            state.jlc.value,
            "DEL",
        ]
        r = self._tbl.append_row(vals)
        for step in STEP_COLS:
            self._set_lib_cell(pid, step, state.get(step))
        return r

    def _set_lib_cell(self, pid, step, st: St, tip=""):
        r = self._lib_row(pid)
        if r < 0:
            return
        col = STEP_COLS[step]
        self._tbl.set_cell(r, col, st.value, ST_COLOR[st], tip)

    def _set_text_cell(self, row, col, text):
        self._tbl.set_text(row, col, text)

    def _import_pid(self, pid, cfg):
        if pid in self._parts:
            return False
        state = PartState(pid, pdf_url=f"https://www.lcsc.com/product-detail/{pid}.html")
        self._insert_row(pid, state)
        self._worker.process(pid, cfg)
        return True

    def _add_part(self):
        pid = normalize_pid(self._inp.GetValue())
        if not pid:
            _warn(self, "Invalid", "Not a valid LCSC part number.")
            return
        self._inp.SetValue("")
        cfg = self._check_cfg()
        if not cfg:
            return
        if pid in self._parts:
            r = self._lib_row(pid)
            if r >= 0:
                self._tbl.Select(r)
                self._worker.process(pid, cfg, overwrite=True)
            return
        self._save_cache()
        self._import_pid(pid, cfg)

    def _bulk_import(self):
        cfg = self._check_cfg()
        if not cfg:
            return
        dlg = BulkImportDialog(self)
        if dlg.ShowModal() != wx.ID_OK:
            dlg.Destroy()
            return
        pids, invalid = parse_parts(dlg.tokens())
        dlg.Destroy()
        if not pids:
            _warn(self, "Bulk Import", "No valid LCSC part numbers found.")
            return
        self._save_cache()
        added = sum(1 for pid in pids if self._import_pid(pid, cfg))
        msg = f"Queued {added} part(s) for import."
        skipped = len(pids) - added
        if skipped:
            msg += f"\n{skipped} already in the list."
        if invalid:
            msg += f"\nIgnored {len(invalid)} invalid entr(y/ies)."
        _info(self, "Bulk Import", msg)

    def _on_lib_activate(self, evt):
        row, col = evt.GetIndex(), evt.GetColumn() if hasattr(evt, "GetColumn") else 0
        pid = self._tbl.get_text(row, 0)
        if not pid:
            return
        # wx ListCtrl activation doesn't give column; use hit test
        x, y = self._tbl.ScreenToClient(wx.GetMousePosition())
        item, flags = self._tbl.HitTest(wx.Point(x, y))
        col = 0
        # approximate column from x
        acc = 0
        for c in range(self._tbl.GetColumnCount()):
            acc += self._tbl.GetColumnWidth(c)
            if x < acc:
                col = c
                break
        if col == C_DEL:
            self._delete(pid)
            return
        s = self._parts.get(pid)
        if not s or col not in COL_STEPS:
            return
        step = COL_STEPS[col]
        cur = s.get(step)
        if step != "jlc" and cur not in (St.FAILED, St.PENDING):
            return
        if cur == St.PROCESSING:
            return
        cfg = self._check_cfg()
        if not cfg:
            return
        s.put(step, St.PROCESSING)
        self._set_lib_cell(pid, step, St.PROCESSING)
        self._worker.retry(pid, step, cfg)

    def _delete(self, pid):
        if not _ask(
            self,
            "Delete Part",
            f"Remove {pid} from the KiCad library?\nThis deletes the symbol, footprint, STEP, and PDF files.",
        ):
            return
        cfg = self._cfg()
        if cfg["output_dir"]:
            errs = _delete_from_lib(pid, cfg["output_dir"])
            if errs:
                _warn(self, "Delete Errors", "\n".join(errs))
            _update_sym_lib_table(cfg["output_dir"])
        r = self._lib_row(pid)
        if r >= 0:
            self._tbl.DeleteItem(r)
        self._parts.pop(pid, None)

    def _on_row(self, row):
        self._log.SetValue("")
        self._lcsc_btn.Disable()
        self._pdf_btn.Disable()
        self._scrape_btn.Disable()
        for w in self._meta_widgets.values():
            w.SetValue("")
        if row < 0:
            return
        pid = self._tbl.get_text(row, 0)
        s = self._parts.get(pid)
        if not s:
            return
        self._log.SetValue("\n".join(s.logs))
        self._lcsc_btn.Enable()
        self._scrape_btn.Enable()
        cfg = self._cfg()
        if cfg["output_dir"]:
            pdf_p = Path(cfg["output_dir"]) / "pdf" / f"{s.pid}.pdf"
            self._pdf_btn.Enable(pdf_p.exists() and pdf_p.stat().st_size >= PDF_MIN_BYTES)
        for _label, attr, _ed in METADATA_FIELDS:
            self._meta_widgets[attr].SetValue(getattr(s, attr, "") or "")

    def _selected_pid(self):
        r = self._tbl.GetFirstSelected()
        if r < 0:
            return None
        return self._tbl.get_text(r, 0)

    def _open_lcsc(self):
        pid = self._selected_pid()
        if pid:
            webbrowser.open(f"https://www.lcsc.com/product-detail/{pid}.html")

    def _open_pdf(self):
        pid = self._selected_pid()
        if not pid or not self._output_dir:
            return
        pdf_p = Path(self._output_dir) / "pdf" / f"{pid}.pdf"
        if pdf_p.exists():
            webbrowser.open(pdf_p.as_uri())

    def _scrape_current(self):
        pid = self._selected_pid()
        if pid:
            self._scrape_btn.Disable()
            self._scrape_btn.SetLabel("Scraping…")
            self._worker.scrape(pid)

    def _on_meta_edit(self, attr, w):
        if attr == "category" and not self._allow_edit_category:
            return
        pid = self._selected_pid()
        if not pid:
            return
        s = self._parts.get(pid)
        if not s:
            return
        val = w.GetValue()
        if getattr(s, attr) == val:
            return
        setattr(s, attr, val)
        col = ATTR_COL.get(attr)
        if col is not None:
            r = self._lib_row(pid)
            if r >= 0:
                self._set_text_cell(r, col, val)
        prop = ATTR_PROP.get(attr)
        if prop and self._output_dir:
            _update_symbol_property(pid, self._output_dir, prop, val)

    def _load_library(self):
        cfg = self._check_cfg()
        if not cfg:
            return
        lib = Path(cfg["output_dir"])
        sym_files = list_sym_files(cfg["output_dir"], self._lib_mode)
        if not sym_files:
            _info(self, "No Library", f"No .kicad_sym files in {lib / 'symbol'}")
            return
        loaded = 0
        for sf in sym_files:
            try:
                entries = _parse_sym_file(sf)
            except Exception as e:
                _warn(self, "Parse Error", f"{sf.name}: {e}")
                continue
            for e in entries:
                pid = e.get("lcsc") or e.get("name", "")
                if not pid or not pid.startswith("C") or not pid[1:].isdigit():
                    continue
                if pid in self._parts:
                    continue
                s = PartState(pid, pdf_url=f"https://www.lcsc.com/product-detail/{pid}.html")
                s.value = e.get("value", "")
                s.description = e.get("description", "")
                s.attributes = e.get("attributes", "")
                s.price = e.get("price", "")
                s.stock = e.get("stock", "")
                s.jlc = St.SUCCESS if (s.price or s.stock) else St.PENDING
                s.valid = St.SUCCESS
                s.symbol = St.SUCCESS
                s.mfr = e.get("mfr", "")
                s.category = e.get("category", "")
                s.package = e.get("package", "")
                fp_ref = e.get("footprint", "")
                existing = _check_existing(pid, cfg["output_dir"], fp_ref=fp_ref)
                s.fp_name_text = existing.get("fp_name", fp_ref)
                s.footprint = St.SUCCESS if existing.get("fp_ok") else St.PENDING
                s.step = St.SUCCESS if existing.get("step_ok") else St.PENDING
                raw_ds = e.get("datasheet", "")
                if raw_ds.startswith("http"):
                    s.pdf = St.PENDING
                else:
                    rel = re.sub(r"^\$\{[^}]+\}/", "", raw_ds) if raw_ds else ""
                    pdf_p = (lib / rel) if rel else (lib / "pdf" / f"{pid}.pdf")
                    if pdf_p.exists() and pdf_p.stat().st_size >= PDF_MIN_BYTES:
                        s.pdf = St.SUCCESS
                    elif raw_ds:
                        s.pdf = St.FAILED
                    else:
                        s.pdf = St.PENDING
                self._insert_row(pid, s)
                loaded += 1
        self._log.AppendText(f"\nLoaded {loaded} part(s)." if loaded else "\nNo new parts found.")
        _update_sym_lib_table(cfg["output_dir"])

    def refresh_after_sync(self, synced_dir: str = ""):
        if not self._output_dir:
            return
        if synced_dir and Path(synced_dir) != Path(self._output_dir):
            return
        try:
            self._load_library()
        except Exception:
            pass
        if self._bom_data:
            self._populate_bom()

    # ── worker callbacks (GUI thread via CallAfter) ──────────────────────────
    def _on_started(self, pid, step):
        for store, setter in (
            (self._parts, self._set_lib_cell),
            (self._bom_parts, self._set_bom_cell),
        ):
            s = store.get(pid)
            if s:
                s.put(step, St.PROCESSING)
                setter(pid, step, St.PROCESSING)

    def _on_done(self, pid, step, ok, extra):
        extra = extra or {}
        for store, setter in (
            (self._parts, self._set_lib_cell),
            (self._bom_parts, self._set_bom_cell),
        ):
            s = store.get(pid)
            if not s:
                continue
            ns = apply_step_result(s, step, ok, extra)
            setter(pid, step, ns, extra.get("error", "") if not ok else extra.get("url", ""))
            if step == "footprint" and ok and extra.get("fp_name"):
                r = self._lib_row(pid)
                if r >= 0:
                    self._set_text_cell(r, 5, extra["fp_name"])
            if step in ("symbol", "jlc") and ok:
                r = self._lib_row(pid)
                if r >= 0:
                    mapping = [("value", 1), ("description", 2), ("mfr", 3), ("category", 4), ("package", 6), ("attributes", 7)]
                    for attr, col in mapping:
                        val = extra.get(attr, "")
                        if val:
                            self._set_text_cell(r, col, val)
                if step == "jlc" and self._output_dir:
                    for attr, prop in (
                        ("category", "Category"),
                        ("mfr", "Manufacturer"),
                        ("package", "Package"),
                        ("attributes", "Key_Attributes"),
                        ("price", "Price"),
                        ("stock", "Stock"),
                        ("description", "Description"),
                    ):
                        val = getattr(s, attr, "")
                        if val:
                            _update_symbol_property(pid, self._output_dir, prop, val)
            if step == "symbol" and ok and self._output_dir:
                _update_sym_lib_table(self._output_dir)
            if step == "valid" and ok:
                for sub in ("symbol", "footprint", "step", "pdf"):
                    if s.get(sub) == St.SUCCESS:
                        setter(pid, sub, St.SUCCESS, "Already in library")
        self._refresh_bom_after_done(pid, step, ok, extra)
        if self._selected_pid() == pid:
            self._on_row(self._tbl.GetFirstSelected())

    def _on_log(self, pid, msg):
        s = self._parts.get(pid)
        if s:
            s.logs.append(msg)
        if self._selected_pid() == pid:
            self._log.AppendText(msg + "\n")

    def _on_scrape_done(self, pid, data):
        self._scrape_btn.Enable()
        self._scrape_btn.SetLabel("Scrape Data")
        s = self._parts.get(pid)
        if not s or not data:
            return
        r = self._lib_row(pid)
        for attr, val in data.items():
            if not val or not hasattr(s, attr):
                continue
            setattr(s, attr, val)
            col = ATTR_COL.get(attr)
            if col is not None and r >= 0:
                self._set_text_cell(r, col, val)
            prop = ATTR_PROP.get(attr)
            if prop and self._output_dir:
                _update_symbol_property(pid, self._output_dir, prop, val)
        if self._selected_pid() == pid:
            self._on_row(self._tbl.GetFirstSelected())

    # ── BOM ──────────────────────────────────────────────────────────────────
    def _load_bom_file(self):
        try:
            p = Path(self._bom_file)
            if not p.exists():
                return
            raw = json.loads(p.read_text())
            if isinstance(raw, list):
                self._bom_data = raw
                self._pcb_file = ""
            else:
                self._bom_data = raw.get("parts", [])
                self._pcb_file = raw.get("pcb_file", "")
            self._pcb_refs = {
                part["ref"]: {"lib": part.get("lib", ""), "fp": part.get("fp", "")}
                for part in self._bom_data
            }
            self._populate_bom()
            self._nb.SetSelection(1)
        except Exception as e:
            _warn(self, "BOM Error", f"Failed to load BOM data: {e}")

    def _populate_bom(self):
        self._bom_tbl.DeleteAllItems()
        self._row_refs.clear()
        groups = group_bom_parts(self._bom_data)
        for data in groups:
            refs = sorted(data["refs"])
            qty = len(refs)
            pid = data["lcsc"] or ""
            r = self._bom_tbl.append_row(
                [", ".join(refs), pid, data["val"], "", str(qty), ""] + [""] * 9
            )
            self._row_refs[r] = refs
            if pid:
                self._load_part_from_lib(pid, r)
                self._update_linked_cell(r, pid)
        self._update_total()

    def _load_part_from_lib(self, pid, row):
        if not self._output_dir:
            return
        existing = _check_existing(pid, self._output_dir)
        state = PartState(pid)
        state.valid = St.SUCCESS if existing.get("sym_ok") or existing.get("fp_ok") else St.PENDING
        state.symbol = St.SUCCESS if existing.get("sym_ok") else St.PENDING
        state.footprint = St.SUCCESS if existing.get("fp_ok") else St.PENDING
        state.step = St.SUCCESS if existing.get("step_ok") else St.PENDING
        state.pdf = St.SUCCESS if existing.get("pdf_ok") else St.PENDING
        if existing.get("fp_name"):
            state.fp_name_text = existing["fp_name"]
        self._bom_parts[pid] = state
        for step in ("valid", "symbol", "footprint", "step", "pdf"):
            self._set_bom_cell(pid, step, state.get(step))
        if existing.get("sym_ok"):
            sym_file = _find_sym_file(pid, Path(self._output_dir) / "symbol")
            if not sym_file:
                return
            try:
                for e in _parse_sym_file(sym_file):
                    if e.get("lcsc") == pid:
                        self._bom_tbl.set_text(row, 3, e.get("description", ""))
                        self._bom_tbl.set_text(row, 5, e.get("stock", ""))
                        self._bom_tbl.set_text(row, 12, e.get("price", ""))
                        state.value = e.get("value", "")
                        state.description = e.get("description", "")
                        state.mfr = e.get("mfr", "")
                        state.category = e.get("category", "")
                        state.package = e.get("package", "")
                        state.attributes = e.get("attributes", "")
                        state.price = e.get("price", "")
                        state.stock = e.get("stock", "")
                        if not state.fp_name_text and e.get("footprint"):
                            state.fp_name_text = e["footprint"]
                        state.jlc = St.SUCCESS if (state.price or state.stock) else St.PENDING
                        self._set_bom_cell(pid, "jlc", state.jlc)
                        sub = row_subtotal(int(self._bom_tbl.get_text(row, 4) or 0), state.price)
                        if sub:
                            self._bom_tbl.set_text(row, 13, sub)
                        break
            except Exception:
                pass

    def _set_bom_cell(self, pid, step, st: St, tip=""):
        col = BOM_STEP_COLS.get(step)
        if col is None:
            return
        for r in self._bom_tbl.find_pid_rows(pid, 1):
            self._bom_tbl.set_cell(r, col, st.value, ST_COLOR[st], tip)

    def _update_total(self):
        total = 0.0
        for r in range(self._bom_tbl.GetItemCount()):
            m = re.search(r"(\d+\.?\d*)", self._bom_tbl.get_text(r, 13) or "")
            if m:
                total += float(m.group(1))
        self._total_label.SetLabel(f"Total BOM Price: ${total:.2f}")

    def _on_bom_row(self, row):
        pid = self._bom_tbl.get_text(row, 1)
        self._lcsc_input.SetValue(pid)
        self._update_action_btn(row)
        self._update_sym_btn(row)

    def _update_linked_cell(self, row, pid):
        refs = self._row_refs.get(row, [])
        state = self._bom_parts.get(pid)
        linked = is_pcb_linked(pid, refs, state.fp_name_text if state else "", self._pcb_refs)
        if linked is True:
            self._bom_tbl.set_cell(row, 14, "✓", "#4CAF50")
        elif linked is False:
            self._bom_tbl.set_cell(row, 14, "✗", "#F44336")
        else:
            self._bom_tbl.set_cell(row, 14, "?", "#9E9E9E")

    def _update_action_btn(self, row):
        if row < 0:
            self._action_btn.Disable()
            return
        pid = self._bom_tbl.get_text(row, 1)
        if not pid:
            self._action_btn.Disable()
            self._action_btn.SetLabel("Download Component")
            return
        refs = self._row_refs.get(row, [])
        state = self._bom_parts.get(pid)
        is_downloaded = state is not None and state.footprint == St.SUCCESS
        linked = is_pcb_linked(pid, refs, state.fp_name_text if state else "", self._pcb_refs)
        if linked is True:
            self._action_btn.SetLabel("Already Linked")
            self._action_btn.Disable()
        elif is_downloaded:
            self._action_btn.SetLabel("Replace Component")
            self._action_btn.Enable()
        else:
            self._action_btn.SetLabel("Download Component")
            self._action_btn.Enable()

    def _update_sym_btn(self, row):
        if row < 0:
            self._replace_sym_btn.Disable()
            return
        pid = self._bom_tbl.get_text(row, 1)
        state = self._bom_parts.get(pid)
        ok = state is not None and state.symbol == St.SUCCESS and bool(find_sch_files(self._pcb_file))
        self._replace_sym_btn.Enable(ok)

    def _on_action_btn(self):
        row = self._bom_tbl.GetFirstSelected()
        if row < 0:
            return
        pid = self._bom_tbl.get_text(row, 1)
        refs = self._row_refs.get(row, [])
        state = self._bom_parts.get(pid)
        is_downloaded = state is not None and state.footprint == St.SUCCESS
        linked = is_pcb_linked(pid, refs, state.fp_name_text if state else "", self._pcb_refs)
        if linked is True:
            return
        if is_downloaded:
            self._on_replace_pcb(pid, refs)
        else:
            self._on_download(pid)

    def _on_download(self, pid=""):
        if not pid:
            pid = normalize_pid(self._lcsc_input.GetValue().strip())
        if not pid:
            _warn(self, "Invalid", "Enter a valid LCSC part number.")
            return
        cfg = self._check_cfg()
        if not cfg:
            return
        ensure_libraries(cfg["output_dir"], cfg["lib_prefix"], cfg["lib_mode"])
        if pid not in self._bom_parts:
            self._bom_parts[pid] = PartState(pid)
        if pid not in self._parts:
            self._insert_row(pid, PartState(pid))
        self._worker.process(pid, cfg, overwrite=True)

    def _on_save_lcsc(self):
        row = self._bom_tbl.GetFirstSelected()
        if row < 0:
            return
        new_pid = normalize_pid(self._lcsc_input.GetValue().strip())
        if not new_pid:
            return
        self._bom_tbl.set_text(row, 1, new_pid)
        self._load_part_from_lib(new_pid, row)
        self._update_linked_cell(row, new_pid)
        self._update_action_btn(row)
        self._update_sym_btn(row)

    def _jlc_cfg(self):
        cfg = {"output_dir": self._output_dir, "jlcpcb_api_key": self._jlcpcb_api_key}
        return cfg

    def _run_jlc_fetch(self, pids):
        cfg = self._jlc_cfg()
        for pid in pids:
            self._worker.retry(pid, "jlc", cfg)

    def _fetch_prices(self):
        if not self._output_dir:
            _warn(self, "No Library", "Please set Library Location in Settings.")
            return
        pids = [self._bom_tbl.get_text(r, 1) for r in range(self._bom_tbl.GetItemCount())]
        pids = [p for p in pids if p]
        if pids:
            self._run_jlc_fetch(pids)

    def _fetch_missing_prices(self):
        if not self._output_dir:
            _warn(self, "No Library", "Please set Library Location in Settings.")
            return
        pids = []
        for r in range(self._bom_tbl.GetItemCount()):
            pid = self._bom_tbl.get_text(r, 1)
            price = self._bom_tbl.get_text(r, 12)
            if pid and not price:
                pids.append(pid)
        if not pids:
            _info(self, "Fetch Missing", "No parts missing price info.")
            return
        self._run_jlc_fetch(pids)

    def _on_fetch_single(self):
        row = self._bom_tbl.GetFirstSelected()
        if row < 0:
            return
        pid = self._bom_tbl.get_text(row, 1)
        if not pid:
            _warn(self, "No Part", "Selected row has no LCSC Part #.")
            return
        if not self._output_dir:
            _warn(self, "No Library", "Set library location in Settings.")
            return
        self._run_jlc_fetch([pid])

    def _refresh_bom_after_done(self, pid, step, ok, extra):
        extra = extra or {}
        for r in self._bom_tbl.find_pid_rows(pid, 1):
            if step == "jlc" and ok:
                self._bom_tbl.set_text(r, 5, extra.get("stock", ""))
                self._bom_tbl.set_text(r, 12, extra.get("price", ""))
                qty = int(self._bom_tbl.get_text(r, 4) or 0)
                sub = row_subtotal(qty, extra.get("price", ""))
                if sub:
                    self._bom_tbl.set_text(r, 13, sub)
                self._update_total()
            if step == "symbol" and ok and extra.get("description"):
                self._bom_tbl.set_text(r, 3, extra["description"])
            self._update_linked_cell(r, pid)
        row = self._bom_tbl.GetFirstSelected()
        if row >= 0:
            self._update_action_btn(row)
            self._update_sym_btn(row)

    def _on_replace_pcb(self, pid, refs):
        state = self._bom_parts.get(pid)
        if not state or not state.fp_name_text or ":" not in state.fp_name_text:
            _warn(self, "Not Downloaded", "Component not in library yet.")
            return
        new_fp = state.fp_name_text
        if not self._pcb_file or not Path(self._pcb_file).exists():
            _warn(self, "No PCB File", "PCB file not found. Reopen BOM from KiCad.")
            return
        errors = []
        try:
            pcb_path = Path(self._pcb_file)
            content = patch_pcb_footprint_refs(pcb_path.read_text(encoding="utf-8"), refs, new_fp)
            pcb_path.write_text(content, encoding="utf-8")
        except Exception as e:
            errors.append(f"PCB update failed: {e}")
        patched_sch = 0
        for sch_path in find_sch_files(self._pcb_file):
            try:
                content = sch_path.read_text(encoding="utf-8")
                new_content = patch_sch_footprint_refs(content, refs, new_fp)
                if new_content != content:
                    sch_path.write_text(new_content, encoding="utf-8")
                    patched_sch += 1
            except Exception as e:
                errors.append(f"Schematic update failed ({sch_path.name}): {e}")
        lib_part, fp_name = new_fp.split(":", 1)
        for ref in refs:
            self._pcb_refs[ref] = {"lib": lib_part, "fp": fp_name}
        row = self._bom_tbl.GetFirstSelected()
        if row >= 0:
            self._update_linked_cell(row, pid)
            self._update_action_btn(row)
        if errors:
            _warn(self, "Partial Update", "\n".join(errors))
        else:
            _info(
                self,
                "Done",
                f"Footprint updated for {', '.join(refs)} → {new_fp}\n"
                f"Schematic sheets patched: {patched_sch}\nReload files in KiCad (File → Revert).",
            )

    def _find_our_sym(self, pid):
        if not self._output_dir:
            return None
        sym_dir = Path(self._output_dir) / "symbol"
        if not sym_dir.exists():
            return None
        primary = list_sym_files(self._output_dir, self._lib_mode)
        rest = [f for f in sorted(sym_dir.glob("*.kicad_sym")) if f not in primary]
        for sf in primary + rest:
            try:
                for e in _parse_sym_file(sf):
                    if e.get("lcsc") == pid:
                        name = e.get("name", "")
                        if name:
                            return sf, name, f"{sf.stem}:{name}"
            except Exception:
                pass
        return None

    def _on_replace_sym(self):
        row = self._bom_tbl.GetFirstSelected()
        if row < 0:
            return
        pid = self._bom_tbl.get_text(row, 1)
        refs = self._row_refs.get(row, [])
        sch_files = find_sch_files(self._pcb_file)
        if not sch_files:
            _warn(self, "No Schematic", "No schematic files found in the project directory.")
            return
        info = self._find_our_sym(pid)
        if not info:
            _warn(self, "Symbol Not Found", f"Could not find symbol for {pid}.")
            return
        sym_file, sym_name, new_lib_id = info
        lib_nickname = new_lib_id.split(":")[0]
        if not _ask(
            self,
            "Replace Symbol — Confirm",
            f"Update schematic symbol for {', '.join(refs)} to {new_lib_id}?\n"
            "Pin mapping may change. Run ERC after reload.",
        ):
            return
        sym_block = extract_sym_block_for_schematic(sym_file, sym_name, lib_nickname)
        errors = []
        patched = 0
        for sch_path in sch_files:
            try:
                content = sch_path.read_text(encoding="utf-8")
                new_content = patch_sch_lib_id_refs(content, refs, new_lib_id)
                if new_content != content:
                    if sym_block:
                        new_content = ensure_sym_in_lib_symbols(new_content, sym_block)
                    sch_path.write_text(new_content, encoding="utf-8")
                    patched += 1
            except Exception as e:
                errors.append(f"{sch_path.name}: {e}")
        if errors:
            _warn(self, "Error", "Failed to update schematic(s):\n" + "\n".join(errors))
            return
        _info(self, "Done", f"Symbol link updated → {new_lib_id}\n({patched} sheet(s))")

    def _export_csv(self):
        with wx.FileDialog(
            self, "Export BOM", wildcard="CSV files (*.csv)|*.csv", style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT
        ) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path = dlg.GetPath()
        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(BOM_COL_NAMES)
                for r in range(self._bom_tbl.GetItemCount()):
                    writer.writerow(
                        [self._bom_tbl.get_text(r, c) for c in range(self._bom_tbl.GetColumnCount())]
                    )
            _info(self, "Exported", f"BOM exported to {path}")
        except Exception as e:
            _warn(self, "Export Error", f"Could not export CSV: {e}")

    def _on_close(self, event):
        self._save_cache()
        self._worker.stop()
        self._worker.wait(3000)
        event.Skip()


# Back-compat alias
MainWindow = MainFrame
