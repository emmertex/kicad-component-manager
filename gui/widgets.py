import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from gui.models import (
    ATTR_COL,
    ATTR_PROP,
    C_ATTRS,
    C_CAT,
    C_DEL,
    C_DESC,
    C_FP,
    C_FP_TEXT,
    C_JLC,
    C_MFR,
    C_PART,
    C_PDF,
    C_PKG,
    C_STEP,
    C_SYM,
    C_VALID,
    C_VALUE,
    COL_NAMES,
    COL_STEPS,
    COL_VIEW_NAMES,
    METADATA_FIELDS,
    ST_COLOR,
    ST_SORT,
    STEP_COLS,
    PartState,
    St,
    _SortItem,
)
from gui.worker import Worker
from lib.categories import FINAL_CATEGORIES
from lib.helpers import (
    PDF_MIN_BYTES,
    _delete_from_lib,
    _find_sym_file,
    _parse_sym_file,
    _update_sym_lib_table,
    _update_symbol_property,
    ensure_libraries,
)

# ── Constants ─────────────────────────────────────────────────────────────────
CACHE_FILE = Path.home() / ".lcsc2kicad_cache.json"
CACHE_VERSION = 7
MAX_RECENT = 10


class SettingsDialog(QDialog):
    def __init__(
        self,
        parent,
        dl_step: bool,
        dl_pdf: bool,
        col_visible: list,
        lib_prefix: str = "",
        jlcpcb_api_key: str = "",
        output_dir: str = "",
        recent_dirs: list = None,
        allow_edit_category: bool = False,
    ):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(500)

        vbox = QVBoxLayout(self)
        vbox.setSpacing(10)

        # Library Location
        loc_group = QGroupBox("Library Location")
        loc_hbox = QHBoxLayout(loc_group)
        self._loc_combo = QComboBox()
        self._loc_combo.setEditable(True)
        self._loc_combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for d in recent_dirs or []:
            self._loc_combo.addItem(d)
        if output_dir:
            self._loc_combo.setCurrentText(output_dir)
        elif recent_dirs:
            self._loc_combo.setCurrentIndex(0)
        loc_btn = QPushButton("Browse…")
        loc_btn.clicked.connect(self._browse_loc)
        loc_hbox.addWidget(self._loc_combo, 1)
        loc_hbox.addWidget(loc_btn)
        vbox.addWidget(loc_group)

        # Library Options
        lib_group = QGroupBox("Library Options")
        lib_form = QFormLayout(lib_group)
        self._prefix_edit = QLineEdit(lib_prefix)
        self._prefix_edit.setPlaceholderText("e.g. MyProject  (leave blank for none)")
        lib_form.addRow("Library Prefix:", self._prefix_edit)
        self._api_key_edit = QLineEdit(jlcpcb_api_key)
        self._api_key_edit.setPlaceholderText("Optional — for JLCPCB API stock/price")
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        lib_form.addRow("JLCPCB API Key:", self._api_key_edit)
        self._edit_cat_cb = QCheckBox(
            "Allow editing category (relabels metadata only — does not move the "
            "symbol to another library)"
        )
        self._edit_cat_cb.setChecked(allow_edit_category)
        lib_form.addRow("", self._edit_cat_cb)
        vbox.addWidget(lib_group)

        # Download Options
        dl_group = QGroupBox("Download Options")
        dl_layout = QVBoxLayout(dl_group)
        self._step_cb = QCheckBox("Download STEP")
        self._step_cb.setChecked(dl_step)
        self._pdf_cb = QCheckBox("Download PDF")
        self._pdf_cb.setChecked(dl_pdf)
        dl_layout.addWidget(self._step_cb)
        dl_layout.addWidget(self._pdf_cb)
        vbox.addWidget(dl_group)

        # View Options
        view_group = QGroupBox("View Options")
        grid = QGridLayout(view_group)
        grid.setColumnStretch(0, 1)
        grid.setColumnStretch(1, 1)
        self._col_cbs: list[QCheckBox] = []
        for i, name in enumerate(COL_VIEW_NAMES):
            cb = QCheckBox(name)
            cb.setChecked(bool(col_visible[i]) if i < len(col_visible) else True)
            self._col_cbs.append(cb)
            grid.addWidget(cb, i // 2, i % 2)
        vbox.addWidget(view_group)

        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        vbox.addWidget(btns)

    @property
    def dl_step(self):
        return self._step_cb.isChecked()

    @property
    def dl_pdf(self):
        return self._pdf_cb.isChecked()

    @property
    def col_visible(self):
        return [cb.isChecked() for cb in self._col_cbs]

    def _browse_loc(self):
        d = QFileDialog.getExistingDirectory(self, "Select Library Directory")
        if d:
            if self._loc_combo.findText(d) == -1:
                self._loc_combo.insertItem(0, d)
            self._loc_combo.setCurrentText(d)

    @property
    def output_dir(self):
        return self._loc_combo.currentText().strip()

    @property
    def recent_dirs(self):
        dirs = [self._loc_combo.itemText(i) for i in range(self._loc_combo.count())]
        cur = self.output_dir
        if cur and cur not in dirs:
            dirs.insert(0, cur)
        return list(dict.fromkeys(dirs))[:MAX_RECENT]

    @property
    def lib_prefix(self):
        return self._prefix_edit.text().strip()

    @property
    def jlcpcb_api_key(self):
        return self._api_key_edit.text().strip()

    @property
    def allow_edit_category(self):
        return self._edit_cat_cb.isChecked()


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LCSC to KiCad Converter")
        self.resize(1050, 720)
        self._parts: dict[str, PartState] = {}
        self._dl_step = True
        self._dl_pdf = False
        self._lib_prefix = ""
        self._jlcpcb_api_key = ""
        self._output_dir = ""
        self._recent_dirs: list = []
        self._allow_edit_category = False
        # Info columns default hidden; status/identity columns visible
        self._col_visible = [True] * len(COL_NAMES)
        for _c in (C_MFR, C_CAT, C_FP_TEXT, C_PKG, C_ATTRS):
            self._col_visible[_c] = False
        self._sort_col = -1
        self._sort_asc = True
        self._worker = Worker()
        self._worker.step_started.connect(self._on_started)
        self._worker.step_done.connect(self._on_done)
        self._worker.log_line.connect(self._on_log)
        self._worker.scrape_done.connect(self._on_scrape_done)
        self._worker.start()
        self._build_ui()
        self._load_cache()
        self._apply_category_editable()
        # Pre-create the fixed library set so KiCad sees every category up front.
        if self._output_dir:
            ensure_libraries(self._output_dir, self._lib_prefix)

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setSpacing(6)
        vbox.setContentsMargins(8, 8, 8, 8)

        # Input row
        row = QHBoxLayout()
        row.addWidget(QLabel("LCSC Part Number:"))
        self._inp = QLineEdit()
        self._inp.setPlaceholderText("e.g. C1337258")
        self._inp.returnPressed.connect(self._add_part)
        row.addWidget(self._inp)
        btn = QPushButton("Convert")
        btn.setFixedWidth(80)
        btn.clicked.connect(self._add_part)
        row.addWidget(btn)
        vbox.addLayout(row)

        # Table + log splitter
        spl = QSplitter(Qt.Vertical)

        self._tbl = QTableWidget(0, len(COL_NAMES))
        self._tbl.setHorizontalHeaderLabels(COL_NAMES)
        hdr = self._tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        self._tbl.setColumnWidth(C_PART, 80)
        self._tbl.setColumnWidth(C_VALUE, 160)
        self._tbl.setColumnWidth(C_DESC, 500)
        self._tbl.setColumnWidth(C_MFR, 130)
        self._tbl.setColumnWidth(C_CAT, 110)
        self._tbl.setColumnWidth(C_FP_TEXT, 180)
        self._tbl.setColumnWidth(C_PKG, 80)
        self._tbl.setColumnWidth(C_ATTRS, 240)
        for c in (C_VALID, C_SYM, C_FP, C_STEP, C_PDF, C_JLC, C_DEL):
            self._tbl.setColumnWidth(c, 28)
            hdr.setSectionResizeMode(c, QHeaderView.Fixed)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.cellClicked.connect(self._on_cell)
        self._tbl.currentCellChanged.connect(
            lambda cur_r, _cc, _pr, _pc: self._on_row(cur_r)
        )
        hdr.setSortIndicatorShown(True)
        hdr.sectionClicked.connect(self._on_header_clicked)
        spl.addWidget(self._tbl)

        # Bottom pane: log (left 40%) + detail panel (right 60%)
        bottom = QSplitter(Qt.Horizontal)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Monospace", 9))
        bottom.addWidget(self._log)

        detail = QWidget()
        dv = QVBoxLayout(detail)
        dv.setContentsMargins(4, 2, 4, 2)
        dv.setSpacing(4)

        btn_row = QHBoxLayout()
        self._lcsc_btn = QPushButton("Open LCSC")
        self._lcsc_btn.setEnabled(False)
        self._lcsc_btn.clicked.connect(self._open_lcsc)
        self._pdf_btn = QPushButton("Open PDF")
        self._pdf_btn.setEnabled(False)
        self._pdf_btn.clicked.connect(self._open_pdf)
        self._scrape_btn = QPushButton("Scrape Data")
        self._scrape_btn.setEnabled(False)
        self._scrape_btn.clicked.connect(self._scrape_current)
        btn_row.addWidget(self._lcsc_btn)
        btn_row.addWidget(self._pdf_btn)
        btn_row.addWidget(self._scrape_btn)
        btn_row.addStretch()
        dv.addLayout(btn_row)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        form_widget = QWidget()
        self._form = QFormLayout(form_widget)
        self._form.setContentsMargins(0, 0, 4, 0)
        self._form.setSpacing(3)
        self._form.setLabelAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self._meta_widgets: dict[str, QWidget] = {}
        for label, attr, editable in METADATA_FIELDS:
            if attr == "category":
                edit = QComboBox()
                edit.setEditable(True)
                edit.addItems(FINAL_CATEGORIES)
                edit.setCurrentText("")
                if editable:
                    # Both editing finished (manual typing) and current index changed
                    edit.lineEdit().editingFinished.connect(
                        lambda a=attr, e=edit: self._on_meta_edit(a, e)
                    )
                    edit.currentIndexChanged.connect(
                        lambda i, a=attr, e=edit: self._on_meta_edit(a, e)
                    )
            else:
                edit = QLineEdit()
                edit.setReadOnly(not editable)
                if not editable:
                    edit.setStyleSheet(
                        "QLineEdit { background: transparent; border: 1px solid transparent; }"
                    )
                if editable:
                    edit.editingFinished.connect(
                        lambda a=attr, e=edit: self._on_meta_edit(a, e)
                    )
            self._meta_widgets[attr] = edit
            self._form.addRow(label + ":", edit)
        scroll.setWidget(form_widget)
        dv.addWidget(scroll, 1)

        bottom.addWidget(detail)
        bottom.setSizes([400, 600])

        spl.addWidget(bottom)
        spl.setSizes([500, 210])
        vbox.addWidget(spl, 1)

        # Bottom buttons
        row4 = QHBoxLayout()
        load_btn = QPushButton("Load Library")
        load_btn.clicked.connect(self._load_library)
        settings_btn = QPushButton("Settings…")
        settings_btn.clicked.connect(self._open_settings)
        exit_btn = QPushButton("Exit")
        exit_btn.clicked.connect(self.close)
        row4.addWidget(load_btn)
        row4.addWidget(settings_btn)
        row4.addStretch()
        self._path_label = QLabel("")
        self._path_label.setStyleSheet("color: #888; font-size: 11px;")
        row4.addWidget(self._path_label)
        row4.addStretch()
        row4.addWidget(exit_btn)
        vbox.addLayout(row4)

    def _open_settings(self):
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
        )
        if dlg.exec() == QDialog.Accepted:
            self._dl_step = dlg.dl_step
            self._dl_pdf = dlg.dl_pdf
            self._col_visible = dlg.col_visible
            self._lib_prefix = dlg.lib_prefix
            self._jlcpcb_api_key = dlg.jlcpcb_api_key
            self._output_dir = dlg.output_dir
            self._recent_dirs = dlg.recent_dirs
            self._allow_edit_category = dlg.allow_edit_category
            for col, visible in enumerate(self._col_visible):
                self._tbl.setColumnHidden(col, not visible)
            self._apply_category_editable()
            self._update_path_label()
            self._save_cache()
            # (Re)create the fixed library set for the chosen location.
            if self._output_dir:
                ensure_libraries(self._output_dir, self._lib_prefix)

    def _on_header_clicked(self, col):
        if col == C_DEL:
            return
        if self._sort_col == col:
            self._sort_asc = not self._sort_asc
        else:
            self._sort_col = col
            self._sort_asc = True
        order = Qt.AscendingOrder if self._sort_asc else Qt.DescendingOrder
        self._tbl.horizontalHeader().setSortIndicator(col, order)
        self._tbl.sortItems(col, order)

    def _load_cache(self):
        if not CACHE_FILE.exists():
            return
        try:
            d = json.loads(CACHE_FILE.read_text())
            version = d.get("version", 0)

            self._recent_dirs = d.get("recent_dirs", [])
            if self._recent_dirs:
                self._output_dir = self._recent_dirs[0]
                self._update_path_label()
            self._dl_step = d.get("dl_step", True)
            self._dl_pdf = d.get("dl_pdf", False)
            self._lib_prefix = d.get("lib_prefix", "")
            self._jlcpcb_api_key = d.get("jlcpcb_api_key", "")
            self._allow_edit_category = d.get("allow_edit_category", False)

            saved_vis = list(d.get("col_visible", []))

            # Migration logic
            if version < CACHE_VERSION:
                n = len(saved_vis)
                if n == 9:
                    # Original 9-col: 0-2 text, 3-7 status, 8 DEL
                    saved_vis = (
                        saved_vis[:3] + [False] * 5 + saved_vis[3:8] + [saved_vis[8]]
                    )
                elif n == 11:
                    # 11-col: 0-2 text, 3-7 status, 8 FP_TEXT, 9 PKG, 10 DEL
                    saved_vis = (
                        saved_vis[:3]
                        + [False, False, saved_vis[8], saved_vis[9], False]
                        + saved_vis[3:8]
                        + [saved_vis[10]]
                    )
                elif n == 13:
                    # 13-col: 0-6 text, 7-11 status, 12 DEL — insert ATTRS(F) at 7
                    saved_vis = saved_vis[:7] + [False] + saved_vis[7:]
                elif n == 14:
                    # 14-col: 0-12 text/status, 13 DEL — insert JLC(T) before DEL
                    saved_vis = saved_vis[:13] + [True] + saved_vis[13:]
                elif n < len(COL_NAMES):
                    saved_vis += [False] * (len(COL_NAMES) - n)

            if len(saved_vis) >= len(COL_NAMES):
                self._col_visible = saved_vis[: len(COL_NAMES)]

            for col, visible in enumerate(self._col_visible):
                self._tbl.setColumnHidden(col, not visible)
        except Exception:
            pass

    def _save_cache(self):
        if self._output_dir:
            if self._output_dir in self._recent_dirs:
                self._recent_dirs.remove(self._output_dir)
            self._recent_dirs.insert(0, self._output_dir)
        self._recent_dirs = list(dict.fromkeys(self._recent_dirs))[:MAX_RECENT]
        try:
            CACHE_FILE.write_text(
                json.dumps(
                    {
                        "version": CACHE_VERSION,
                        "recent_dirs": self._recent_dirs,
                        "dl_step": self._dl_step,
                        "dl_pdf": self._dl_pdf,
                        "col_visible": self._col_visible,
                        "lib_prefix": self._lib_prefix,
                        "jlcpcb_api_key": self._jlcpcb_api_key,
                        "allow_edit_category": self._allow_edit_category,
                    },
                    indent=2,
                )
            )
        except Exception:
            pass

    def _cfg(self):
        return {
            "output_dir": self._output_dir,
            "dl_step": self._dl_step,
            "dl_pdf": self._dl_pdf,
            "lib_prefix": self._lib_prefix,
            "jlcpcb_api_key": self._jlcpcb_api_key,
        }

    def _check_cfg(self):
        c = self._cfg()
        if not c["output_dir"]:
            QMessageBox.warning(
                self, "No Library", "Please set Library Location first."
            )
            return None
        return c

    @staticmethod
    def _norm(s):
        p = s.strip().upper()
        if p.isdigit():
            return f"C{p}"
        if p.startswith("C") and p[1:].isdigit():
            return p
        return None

    def _add_part(self):
        pid = self._norm(self._inp.text())
        if not pid:
            QMessageBox.warning(self, "Invalid", f"Not a valid LCSC part number.")
            return
        self._inp.clear()
        cfg = self._check_cfg()
        if not cfg:
            return
        if pid in self._parts:
            r = self._row(pid)
            if r >= 0:
                self._tbl.setCurrentCell(r, 0)
            return
        self._save_cache()
        state = PartState(
            pid, pdf_url=f"https://www.lcsc.com/product-detail/{pid}.html"
        )
        self._insert_row(pid, state)
        self._worker.process(pid, cfg)

    def _insert_row(self, pid, state: PartState):
        self._parts[pid] = state
        r = self._tbl.rowCount()
        self._tbl.insertRow(r)

        # Part number with numeric sort key for correct ordering (C2 < C10)
        part_item = _SortItem(pid)
        part_item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        part_item.setData(Qt.UserRole, int(pid[1:]) if pid[1:].isdigit() else 0)
        self._tbl.setItem(r, C_PART, part_item)

        self._set_text_cell(r, C_VALUE, state.value)
        self._set_text_cell(r, C_DESC, state.description, clip=True)
        self._set_text_cell(r, C_MFR, state.mfr)
        self._set_text_cell(r, C_CAT, state.category)
        self._set_text_cell(r, C_FP_TEXT, state.fp_name_text)
        self._set_text_cell(r, C_PKG, state.package)
        self._set_text_cell(r, C_ATTRS, state.attributes)
        for step in ("valid", "symbol", "footprint", "step", "pdf", "jlc"):
            self._set_cell(pid, step, state.get(step))

        # Delete as a plain item (not a cell widget) so table sorting works correctly
        del_item = QTableWidgetItem("🗑")
        del_item.setFlags(Qt.ItemIsEnabled | Qt.ItemIsSelectable)
        del_item.setTextAlignment(Qt.AlignCenter)
        del_item.setForeground(QColor("#F44336"))
        del_item.setToolTip("Click to delete")
        self._tbl.setItem(r, C_DEL, del_item)

    def _row(self, pid):
        for r in range(self._tbl.rowCount()):
            it = self._tbl.item(r, C_PART)
            if it and it.text() == pid:
                return r
        return -1

    def _set_text_cell(self, row, col, text, clip=False):
        it = self._tbl.item(row, col)
        if it is None:
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
            self._tbl.setItem(row, col, it)
        it.setText(text)
        if clip and text:
            it.setToolTip(text)

    def _update_info_cells(self, pid, value="", description=""):
        r = self._row(pid)
        if r < 0:
            return
        s = self._parts.get(pid)
        if s:
            if value:
                s.value = value
            if description:
                s.description = description
        if value:
            self._set_text_cell(r, C_VALUE, value)
        if description:
            self._set_text_cell(r, C_DESC, description, clip=True)

    def _set_cell(self, pid, step, st: St, tip=""):
        r = self._row(pid)
        if r < 0:
            return
        col = STEP_COLS[step]
        it = self._tbl.item(r, col)
        if it is None:
            it = _SortItem()
            it.setFlags(Qt.ItemIsEnabled)
            self._tbl.setItem(r, col, it)
        it.setText(st.value)
        it.setData(Qt.UserRole, ST_SORT[st])
        it.setForeground(QColor(ST_COLOR[st]))
        it.setTextAlignment(Qt.AlignCenter)
        if tip:
            it.setToolTip(tip)

    def _on_started(self, pid, step):
        s = self._parts.get(pid)
        if s:
            s.put(step, St.PROCESSING)
            self._set_cell(pid, step, St.PROCESSING)

    def _on_done(self, pid, step, ok, extra):
        s = self._parts.get(pid)
        if not s:
            return
        if step == "pdf":
            url = extra.get("url", "")
            if extra.get("skipped"):
                ns = St.SKIPPED
            elif ok:
                ns = St.SUCCESS
                url = ""
            else:
                ns = St.FAILED
            s.put("pdf", ns)
            s.pdf_url = url
            self._set_cell(pid, "pdf", ns, url)
        else:
            ns = St.SUCCESS if ok else St.FAILED
            s.put(step, ns)
            tip = extra.get("error", "") if not ok else ""
            self._set_cell(pid, step, ns, tip)

        if step == "footprint" and ok:
            fp_name = extra.get("fp_name", "")
            if fp_name:
                r = self._row(pid)
                if r >= 0:
                    s.fp_name_text = fp_name
                    self._set_text_cell(r, C_FP_TEXT, fp_name)

        if step == "symbol" and ok:
            cfg2 = self._cfg()
            if cfg2["output_dir"]:
                _update_sym_lib_table(cfg2["output_dir"])
            desc = extra.get("description", "")
            value = self._read_value_from_sym(pid)
            self._update_info_cells(pid, value=value, description=desc)
            r = self._row(pid)
            if r >= 0:
                for attr, col, key in (
                    ("package", C_PKG, "package"),
                    ("mfr", C_MFR, "mfr"),
                    ("category", C_CAT, "category"),
                    ("attributes", C_ATTRS, "attributes"),
                ):
                    val = extra.get(key, "")
                    if val:
                        setattr(s, attr, val)
                        self._set_text_cell(r, col, val)
            for attr in ("price", "stock"):
                val = extra.get(attr, "")
                if val:
                    setattr(s, attr, val)

        if step == "jlc" and ok:
            r = self._row(pid)
            for attr, col, key in (
                ("category", C_CAT, "category"),
                ("mfr", C_MFR, "mfr"),
                ("package", C_PKG, "package"),
                ("attributes", C_ATTRS, "attributes"),
            ):
                val = extra.get(key, "")
                if val:
                    setattr(s, attr, val)
                    if r >= 0:
                        self._set_text_cell(r, col, val)
            for attr in ("price", "stock"):
                val = extra.get(attr, "")
                if val:
                    setattr(s, attr, val)
            cfg = self._cfg()
            if cfg["output_dir"]:
                for attr, prop in (
                    ("category", "Category"),
                    ("mfr", "Manufacturer"),
                    ("package", "Package"),
                    ("attributes", "Key_Attributes"),
                    ("price", "Price"),
                    ("stock", "Stock"),
                ):
                    val = getattr(s, attr, "")
                    if val:
                        _update_symbol_property(pid, cfg["output_dir"], prop, val)

        self._refresh_detail(pid)

        if step == "valid" and ok:
            key_map = {
                "symbol": "sym_ok",
                "footprint": "fp_ok",
                "step": "step_ok",
                "pdf": "pdf_ok",
            }
            for sub, key in key_map.items():
                if extra.get(key):
                    s.put(sub, St.SUCCESS)
                    self._set_cell(pid, sub, St.SUCCESS, "Already in library")

    def _read_value_from_sym(self, pid):
        cfg = self._cfg()
        if not cfg["output_dir"]:
            return ""
        sym_file = _find_sym_file(pid, Path(cfg["output_dir"]) / "symbol")
        if sym_file is None:
            return ""
        try:
            for e in _parse_sym_file(sym_file):
                if e.get("lcsc") == pid:
                    return e.get("value", "")
        except Exception:
            pass
        return ""

    def _on_log(self, pid, msg):
        s = self._parts.get(pid)
        if s:
            s.logs.append(msg)
        if self._row(pid) == self._tbl.currentRow():
            self._log.append(msg)

    def _on_row(self, row):
        self._log.clear()
        self._lcsc_btn.setEnabled(False)
        self._pdf_btn.setEnabled(False)
        self._scrape_btn.setEnabled(False)
        for w in self._meta_widgets.values():
            if isinstance(w, QComboBox):
                w.setCurrentText("")
            else:
                w.setText("")
        if row < 0:
            return
        it = self._tbl.item(row, C_PART)
        if not it:
            return
        s = self._parts.get(it.text())
        if not s:
            return
        self._log.setPlainText("\n".join(s.logs))
        sb = self._log.verticalScrollBar()
        sb.setValue(sb.maximum())
        self._lcsc_btn.setEnabled(True)
        self._scrape_btn.setEnabled(True)
        cfg = self._cfg()
        if cfg["output_dir"]:
            pdf_p = Path(cfg["output_dir"]) / "pdf" / f"{s.pid}.pdf"
            self._pdf_btn.setEnabled(
                pdf_p.exists() and pdf_p.stat().st_size >= PDF_MIN_BYTES
            )
        for _label, attr, _editable in METADATA_FIELDS:
            w = self._meta_widgets[attr]
            val = getattr(s, attr, "")
            if isinstance(w, QComboBox):
                w.setCurrentText(val)
            else:
                w.setText(val)

    def _selected_pid(self):
        it = self._tbl.item(self._tbl.currentRow(), C_PART)
        return it.text() if it else None

    def _open_lcsc(self):
        pid = self._selected_pid()
        if pid:
            QDesktopServices.openUrl(
                QUrl(f"https://www.lcsc.com/product-detail/{pid}.html")
            )

    def _open_pdf(self):
        pid = self._selected_pid()
        if not pid:
            return
        cfg = self._cfg()
        if not cfg["output_dir"]:
            return
        pdf_p = Path(cfg["output_dir"]) / "pdf" / f"{pid}.pdf"
        if pdf_p.exists():
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(pdf_p)))

    def _scrape_current(self):
        pid = self._selected_pid()
        if pid:
            self._scrape_btn.setEnabled(False)
            self._scrape_btn.setText("Scraping…")
            self._worker.scrape(pid)

    def _on_scrape_done(self, pid: str, data: dict):
        if self._selected_pid() == pid:
            self._scrape_btn.setEnabled(True)
            self._scrape_btn.setText("Scrape Data")
        s = self._parts.get(pid)
        if not s or not data:
            return
        cfg = self._cfg()
        r = self._row(pid)
        for attr, val in data.items():
            if not val or not hasattr(s, attr):
                continue
            setattr(s, attr, val)
            col = ATTR_COL.get(attr)
            if col is not None and r >= 0:
                self._set_text_cell(r, col, val)
            prop = ATTR_PROP.get(attr)
            if prop and cfg["output_dir"]:
                _update_symbol_property(pid, cfg["output_dir"], prop, val)
        self._refresh_detail(pid)

    def _apply_category_editable(self):
        """Gate the category field on the 'Allow editing category' setting.

        When enabled, the field relabels the symbol's Category property in place
        only — it never moves the symbol to a different library file."""
        w = self._meta_widgets.get("category")
        if isinstance(w, QComboBox):
            w.setEnabled(self._allow_edit_category)
            w.setToolTip(
                "Relabels the Category property only — does not move the symbol "
                "to another library."
                if self._allow_edit_category
                else "Enable 'Allow editing category' in Settings to change this."
            )

    def _on_meta_edit(self, attr, w):
        if attr == "category" and not self._allow_edit_category:
            return
        pid = self._selected_pid()
        if not pid:
            return
        s = self._parts.get(pid)
        if not s:
            return
        val = w.currentText() if isinstance(w, QComboBox) else w.text()
        if getattr(s, attr) == val:
            return
        setattr(s, attr, val)
        col = ATTR_COL.get(attr)
        if col is not None:
            r = self._row(pid)
            if r >= 0:
                self._set_text_cell(r, col, val)
        prop = ATTR_PROP.get(attr)
        cfg = self._cfg()
        if prop and cfg["output_dir"]:
            _update_symbol_property(pid, cfg["output_dir"], prop, val)

    def _refresh_detail(self, pid):
        if self._selected_pid() == pid:
            self._on_row(self._tbl.currentRow())

    def _on_cell(self, row, col):
        it = self._tbl.item(row, C_PART)
        if not it:
            return
        pid = it.text()

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
        self._set_cell(pid, step, St.PROCESSING)
        self._worker.retry(pid, step, cfg)

    def _delete(self, pid):
        reply = QMessageBox.question(
            self,
            "Delete Part",
            f"Remove {pid} from the KiCad library?\n"
            "This deletes the symbol, footprint, STEP, and PDF files.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        cfg = self._cfg()
        if cfg["output_dir"]:
            errs = _delete_from_lib(pid, cfg["output_dir"])
            if errs:
                QMessageBox.warning(self, "Delete Errors", "\n".join(errs))
            _update_sym_lib_table(cfg["output_dir"])
        r = self._row(pid)
        if r >= 0:
            self._tbl.removeRow(r)
        self._parts.pop(pid, None)

    def _update_path_label(self):
        d = self._output_dir
        self._path_label.setText(d if d else "")

    def _load_library(self):
        cfg = self._check_cfg()
        if not cfg:
            return
        lib = Path(cfg["output_dir"])
        sym_dir = lib / "symbol"
        sym_files = sorted(sym_dir.glob("*.kicad_sym")) if sym_dir.exists() else []
        if not sym_files:
            QMessageBox.information(
                self, "No Library", f"No .kicad_sym files in {sym_dir}"
            )
            return
        loaded = 0
        for sf in sym_files:
            try:
                entries = _parse_sym_file(sf)
            except Exception as e:
                QMessageBox.warning(self, "Parse Error", f"{sf.name}: {e}")
                continue
            for e in entries:
                pid = e.get("lcsc") or e.get("name", "")
                if not pid or not pid.startswith("C") or not pid[1:].isdigit():
                    continue
                if pid in self._parts:
                    continue
                s = PartState(
                    pid, pdf_url=f"https://www.lcsc.com/product-detail/{pid}.html"
                )
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
                s.fp_name_text = fp_ref
                if fp_ref and ":" in fp_ref:
                    ln, fn = fp_ref.split(":", 1)
                    mod = lib / f"{ln}.pretty" / f"{fn}.kicad_mod"
                    if mod.exists():
                        s.footprint = St.SUCCESS
                        step_p = lib / ln / "packages3d" / f"{fn}.step"
                        s.step = St.SUCCESS if step_p.exists() else St.PENDING
                    else:
                        s.footprint = St.PENDING
                        s.step = St.PENDING
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
        msg = f"Loaded {loaded} part(s)." if loaded else "No new parts found."
        self._log.append(msg)
        _update_sym_lib_table(cfg["output_dir"])

    def closeEvent(self, event):
        self._save_cache()
        self._worker.stop()
        self._worker.wait(3000)
        super().closeEvent(event)
