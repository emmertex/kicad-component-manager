import csv
import json
import re
from pathlib import Path

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QColor, QDesktopServices, QFont
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
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
    QPlainTextEdit,
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
    BOM_COL_NAMES,
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
    CB_DESC,
    CB_FP,
    CB_JLC,
    CB_LINKED,
    CB_PART,
    CB_PDF,
    CB_PRICE,
    CB_QTY,
    CB_REFS,
    CB_STEP,
    CB_STOCK,
    CB_SUBTOTAL,
    CB_SYM,
    CB_VALID,
    CB_VALUE,
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
from lib.bulk import normalize_pid, parse_parts
from lib.categories import FINAL_CATEGORIES
from lib.kicad_escape import KICAD_QUOTED_VALUE_RE
from lib.helpers import (
    PDF_MIN_BYTES,
    _check_existing,
    _delete_from_lib,
    _find_block_end,
    _find_sym_file,
    _parse_sym_file,
    _update_sym_lib_table,
    _update_symbol_property,
    ensure_libraries,
    list_sym_files,
    sync_libraries,
)

# ── Constants ─────────────────────────────────────────────────────────────────
CACHE_FILE = Path.home() / ".lcsc2kicad_cache.json"
CACHE_VERSION = 7
MAX_RECENT = 10


class SettingsDialog(QDialog):
    # (stored value, display label) for the library organisation mode.
    LIB_MODE_LABELS = (
        ("organised", "Organised — per-category libraries"),
        ("consolidated", "Consolidated — single 'components' library"),
        ("both_organised", "Both (Organised Primary) — list from category libs"),
        ("both_consolidated", "Both (Consolidated Primary) — list from 'components'"),
    )

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
        lib_mode: str = "organised",
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

        # Library organisation mode. Stored value is kept in the combo's
        # userData so labels can stay human-friendly.
        self._mode_combo = QComboBox()
        for value, label in self.LIB_MODE_LABELS:
            self._mode_combo.addItem(label, value)
        idx = self._mode_combo.findData(lib_mode)
        self._mode_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._mode_combo.setToolTip(
            "Organised: per-category symbol libraries.\n"
            "Consolidated: all symbols in one 'components' library (pre-v2 layout).\n"
            "Both: write symbols to BOTH the category libraries and 'components' "
            "for backward compatibility. The 'Primary' chooses which set this app "
            "lists from (BOM view / Load Library) so parts aren't shown twice — "
            "KiCad still sees both.\n\n"
            "Footprints, 3D models and datasheets are shared across all modes."
        )
        lib_form.addRow("Library Mode:", self._mode_combo)

        # One-shot migration: copy every existing symbol into both layouts.
        sync_btn = QPushButton("Sync Libraries")
        sync_btn.setToolTip(
            "Copy all existing symbols into BOTH the per-category libraries and "
            "the consolidated 'components' library, so older and newer projects "
            "resolve every part. Footprints/3D/datasheets are already shared."
        )
        sync_btn.clicked.connect(self._sync_libraries)
        lib_form.addRow("", sync_btn)

        self._prefix_edit = QLineEdit(lib_prefix)
        self._prefix_edit.setPlaceholderText("e.g. MyProject  (leave blank for none)")
        lib_form.addRow("Library Prefix:", self._prefix_edit)
        self._api_key_edit = QLineEdit(jlcpcb_api_key)
        self._api_key_edit.setPlaceholderText("Optional — for JLCPCB API stock/price")
        self._api_key_edit.setEchoMode(QLineEdit.Password)
        lib_form.addRow("JLCPCB API Key:", self._api_key_edit)
        self._edit_cat_cb = QCheckBox("Allow editing category (relabels metadata only)")
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

    def _sync_libraries(self):
        out = self.output_dir
        if not out or not Path(out).exists():
            QMessageBox.warning(
                self, "No Library", "Set a valid Library Location first."
            )
            return
        try:
            QApplication.setOverrideCursor(Qt.WaitCursor)
            parts, copies = sync_libraries(out, self.lib_prefix)
        except Exception as e:
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Sync Failed", str(e))
            return
        finally:
            QApplication.restoreOverrideCursor()
        # Refresh the manager list / open BOM window so they reflect the new
        # on-disk state without needing a manual reload.
        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_after_sync"):
            parent.refresh_after_sync(out)
        if copies:
            msg = (
                f"Copied {copies} missing symbol(s) into the other layout.\n"
                f"All {parts} symbol(s) are now in both the category libraries "
                f"and 'components'."
            )
        else:
            msg = (
                f"Already in sync — all {parts} symbol(s) are present in both "
                f"the category libraries and 'components'. Nothing to copy."
            )
        QMessageBox.information(self, "Sync Libraries", msg)

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
    def lib_mode(self):
        return self._mode_combo.currentData() or "organised"

    @property
    def lib_prefix(self):
        return self._prefix_edit.text().strip()

    @property
    def jlcpcb_api_key(self):
        return self._api_key_edit.text().strip()

    @property
    def allow_edit_category(self):
        return self._edit_cat_cb.isChecked()


class BulkImportDialog(QDialog):
    """Collect a list of part numbers by paste or from a file."""

    def __init__(self, parent):
        super().__init__(parent)
        self.setWindowTitle("Bulk Import")
        self.setMinimumSize(360, 320)
        v = QVBoxLayout(self)
        v.addWidget(QLabel("Paste part numbers (one per line), or load from a file:"))
        self._text = QPlainTextEdit()
        self._text.setPlaceholderText("C1234\nC2040\nC25804")
        v.addWidget(self._text, 1)
        file_btn = QPushButton("Select File…")
        file_btn.clicked.connect(self._load_file)
        v.addWidget(file_btn)
        btns = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        btns.accepted.connect(self.accept)
        btns.rejected.connect(self.reject)
        v.addWidget(btns)

    def _load_file(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select Parts File", "", "Text Files (*.txt *.csv);;All Files (*)"
        )
        if path:
            try:
                self._text.setPlainText(
                    Path(path).read_text(encoding="utf-8", errors="replace")
                )
            except OSError as e:
                QMessageBox.warning(self, "Error", f"Could not read file: {e}")

    def tokens(self):
        return self._text.toPlainText().splitlines()


def _patch_pcb_footprint_refs(content: str, refs: list, new_fp: str) -> str:
    """Replace the footprint lib:name in a .kicad_pcb file for the given refs.

    Handles both KiCad 6+ (property "Reference") and KiCad 5 (fp_text reference) formats.
    """
    for ref in refs:
        # KiCad 6+: (property "Reference" "R1" ...)
        # KiCad 5:  (fp_text reference "R1" ...)
        ref_pat = re.compile(
            r'\((?:property\s+"Reference"|fp_text\s+reference)\s+"'
            + re.escape(ref)
            + r'"'
        )
        matched = False
        for m in ref_pat.finditer(content):
            before = content[: m.start()]
            # Search backwards for the enclosing (footprint "lib:name" declaration.
            # Also handle (module "lib:name" for KiCad 5.
            fp_matches = list(
                re.finditer(
                    r'\((?:footprint|module)\s+"' + KICAD_QUOTED_VALUE_RE + r'"',
                    before,
                )
            )
            if not fp_matches:
                continue
            last_m = fp_matches[-1]
            old_str = last_m.group(0)
            # Preserve footprint vs module keyword so we don't break older files.
            kw = "footprint" if old_str.startswith("(footprint") else "module"
            new_str = f'({kw} "{new_fp}"'
            content = (
                content[: last_m.start()]
                + new_str
                + content[last_m.start() + len(old_str) :]
            )
            matched = True
            break
        if not matched:
            # ref not found in PCB — skip silently
            pass
    return content


def _sch_symbol_blocks(content: str) -> list:
    """Scan a .kicad_sch file forward and return (start, end) for every
    placed-instance symbol block — i.e. blocks that open with
    (symbol (lib_id "...").  Library-definition blocks inside (lib_symbols ...)
    open with (symbol "Name" ...) and are skipped.
    Results are in file order (ascending start position).
    """
    sym_pat = re.compile(r'\(symbol\s+\(lib_id\b')
    blocks = []
    pos = 0
    while pos < len(content):
        m = sym_pat.search(content, pos)
        if not m:
            break
        start = m.start()
        end = _find_block_end(content, start)
        if end == -1:
            pos = m.end()
            continue
        blocks.append((start, end))
        pos = end + 1
    return blocks


def _patch_sch_footprint_refs(content: str, refs: list, new_fp: str) -> str:
    """Replace the Footprint property for all placed symbol blocks whose
    Reference matches any entry in *refs*.  Handles multi-unit ICs.
    """
    ref_set = set(refs)
    ref_check = re.compile(
        r'\(property\s+"Reference"\s+"(' + KICAD_QUOTED_VALUE_RE + r')"'
    )
    fp_pat = re.compile(
        r'(\(property\s+"Footprint"\s+")' + KICAD_QUOTED_VALUE_RE + r'(")'
    )

    patches = []
    for start, end in _sch_symbol_blocks(content):
        block = content[start : end + 1]
        m = ref_check.search(block)
        if m and m.group(1) in ref_set:
            new_block, count = fp_pat.subn(
                lambda fm: fm.group(1) + new_fp + fm.group(2), block, count=1
            )
            if count:
                patches.append((start, end, new_block))

    for start, end, new_block in reversed(patches):
        content = content[:start] + new_block + content[end + 1 :]
    return content


def _patch_sch_lib_id_refs(content: str, refs: list, new_lib_id: str) -> str:
    """Replace (lib_id "...") for all placed symbol blocks whose Reference
    matches any entry in *refs*.  Handles multi-unit ICs (each unit block
    has its own lib_id entry that must be updated).
    """
    ref_set = set(refs)
    ref_check = re.compile(
        r'\(property\s+"Reference"\s+"(' + KICAD_QUOTED_VALUE_RE + r')"'
    )
    lib_id_pat = re.compile(r'(\(lib_id\s+")' + KICAD_QUOTED_VALUE_RE + r'(")')

    patches = []
    for start, end in _sch_symbol_blocks(content):
        block = content[start : end + 1]
        m = ref_check.search(block)
        if m and m.group(1) in ref_set:
            new_block, count = lib_id_pat.subn(
                lambda fm: fm.group(1) + new_lib_id + fm.group(2), block, count=1
            )
            if count:
                patches.append((start, end, new_block))

    for start, end, new_block in reversed(patches):
        content = content[:start] + new_block + content[end + 1 :]
    return content


def _extract_sym_block_for_schematic(
    sym_file: Path, sym_name: str, lib_nickname: str
) -> "str | None":
    """Extract a symbol's definition from a .kicad_sym file and return it
    formatted for embedding in a schematic lib_symbols section.
    The top-level symbol name gets the library prefix; sub-symbols keep their
    short names (KiCad 6+ convention)."""
    try:
        content = sym_file.read_text(encoding="utf-8")
    except Exception:
        return None
    # Find top-level symbol (minimum indentation — sub-symbols are nested deeper)
    pat = re.compile(
        r'^([ \t]*)\(symbol\s+"' + re.escape(sym_name) + r'"', re.MULTILINE
    )
    all_m = list(pat.finditer(content))
    if not all_m:
        return None
    min_indent = min(len(m.group(1)) for m in all_m)
    top_m = next((m for m in all_m if len(m.group(1)) == min_indent), None)
    if not top_m:
        return None
    start = top_m.start()
    end = _find_block_end(content, start)
    if end == -1:
        return None
    block = content[start : end + 1]
    # Rename only the top-level opening token — sub-symbols keep short names
    block = block.replace(f'(symbol "{sym_name}"', f'(symbol "{lib_nickname}:{sym_name}"', 1)
    return block.strip()


def _ensure_sym_in_lib_symbols(content: str, sym_block: str) -> str:
    """Insert (or replace) a symbol definition in the schematic lib_symbols section
    so KiCad can resolve the new lib_id without reporting 'symbol not found'."""
    name_m = re.match(r'\(symbol\s+"(' + KICAD_QUOTED_VALUE_RE + r')"', sym_block.strip())
    if not name_m:
        return content
    lib_id = name_m.group(1)

    ls_m = re.search(r'\(lib_symbols\b', content)
    if not ls_m:
        return content
    ls_start = ls_m.start()
    ls_end = _find_block_end(content, ls_start)
    if ls_end == -1:
        return content

    ls_content = content[ls_start : ls_end + 1]

    # Remove any existing definition for this lib_id (avoid duplicates)
    existing = re.search(r'\(symbol\s+"' + re.escape(lib_id) + r'"', ls_content)
    if existing:
        sym_s = existing.start()
        sym_e = _find_block_end(ls_content, sym_s)
        if sym_e != -1:
            ls_content = ls_content[:sym_s] + ls_content[sym_e + 1 :]

    # Insert before the closing ) of lib_symbols, indented 2 spaces
    indented = "\n  " + sym_block.strip().replace("\n", "\n  ") + "\n"
    ls_content = ls_content[:-1] + indented + ")"

    return content[:ls_start] + ls_content + content[ls_end + 1 :]


class BOMWindow(QMainWindow):
    def __init__(
        self,
        bom_data: list,
        output_dir: str = "",
        pcb_file: str = "",
        lib_mode: str = "organised",
    ):
        super().__init__()
        self.setWindowTitle("KiCad BOM Manager")
        self.resize(1200, 750)
        self._bom_data = bom_data
        self._output_dir = output_dir
        self._pcb_file = pcb_file
        self._lib_mode = lib_mode
        self._parts: dict[str, PartState] = {}
        self._row_refs: dict[int, list] = {}
        # ref -> {"lib": ..., "fp": ...} from PCB scan data
        self._pcb_refs: dict[str, dict] = {
            p["ref"]: {"lib": p.get("lib", ""), "fp": p.get("fp", "")}
            for p in bom_data
        }
        self._worker = Worker()
        self._worker.step_started.connect(self._on_started)
        self._worker.step_done.connect(self._on_done)
        self._worker.scrape_done.connect(self._on_scrape_done)
        self._worker.start()

        self._build_ui()
        self._populate_bom()

    def _build_ui(self):
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setSpacing(6)
        vbox.setContentsMargins(8, 8, 8, 8)

        # Table + detail panel splitter
        spl = QSplitter(Qt.Vertical)

        self._tbl = QTableWidget(0, len(BOM_COL_NAMES))
        self._tbl.setHorizontalHeaderLabels(BOM_COL_NAMES)
        hdr = self._tbl.horizontalHeader()
        hdr.setSectionResizeMode(QHeaderView.Interactive)
        self._tbl.setColumnWidth(CB_REFS, 150)
        self._tbl.setColumnWidth(CB_PART, 100)
        self._tbl.setColumnWidth(CB_VALUE, 150)
        self._tbl.setColumnWidth(CB_DESC, 250)
        self._tbl.setColumnWidth(CB_QTY, 50)
        self._tbl.setColumnWidth(CB_STOCK, 80)
        for c in (CB_VALID, CB_SYM, CB_FP, CB_STEP, CB_PDF, CB_JLC, CB_LINKED):
            self._tbl.setColumnWidth(c, 28)
            hdr.setSectionResizeMode(c, QHeaderView.Fixed)
        self._tbl.setColumnWidth(CB_PRICE, 80)
        self._tbl.setColumnWidth(CB_SUBTOTAL, 100)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.currentCellChanged.connect(self._on_row_changed)
        spl.addWidget(self._tbl)

        # Bottom pane: detail panel
        bottom = QWidget()
        bh = QHBoxLayout(bottom)
        bh.setContentsMargins(0, 0, 0, 0)

        # Left: Detail Form
        detail = QGroupBox("Component Details")
        dv = QVBoxLayout(detail)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        form_widget = QWidget()
        self._form = QFormLayout(form_widget)
        self._meta_widgets: dict[str, QWidget] = {}
        for label, attr, editable in METADATA_FIELDS:
            if attr == "category":
                edit = QComboBox()
                edit.setEditable(True)
                edit.addItems(FINAL_CATEGORIES)
                edit.setCurrentText("")
            else:
                edit = QLineEdit()
                edit.setReadOnly(not editable)
                if not editable:
                    edit.setStyleSheet("background: transparent; border: none;")
            self._meta_widgets[attr] = edit
            self._form.addRow(label + ":", edit)
        scroll.setWidget(form_widget)
        dv.addWidget(scroll)
        bh.addWidget(detail, 3)

        # Right: Actions
        actions = QGroupBox("Actions")
        av = QVBoxLayout(actions)

        self._lcsc_input = QLineEdit()
        self._lcsc_input.setPlaceholderText("Enter LCSC #")
        av.addWidget(QLabel("Update LCSC Part Number:"))
        av.addWidget(self._lcsc_input)

        self._save_lcsc_btn = QPushButton("Save LCSC #")
        self._save_lcsc_btn.clicked.connect(self._on_save_lcsc)
        av.addWidget(self._save_lcsc_btn)

        self._action_btn = QPushButton("Download Component")
        self._action_btn.setToolTip("Download component to custom library")
        self._action_btn.setEnabled(False)
        self._action_btn.clicked.connect(self._on_action_btn)
        av.addWidget(self._action_btn)

        self._replace_sym_btn = QPushButton("Replace Symbol")
        self._replace_sym_btn.setToolTip(
            "Update the schematic symbol link to use the custom library symbol"
        )
        self._replace_sym_btn.setEnabled(False)
        self._replace_sym_btn.clicked.connect(self._on_replace_sym)
        av.addWidget(self._replace_sym_btn)

        self._fetch_single_btn = QPushButton("Fetch Price/Stock")
        self._fetch_single_btn.setToolTip(
            "Fetch price and stock for this specific component"
        )
        self._fetch_single_btn.clicked.connect(self._on_fetch_single)
        av.addWidget(self._fetch_single_btn)

        av.addStretch()

        self._total_label = QLabel("Total BOM Price: $0.00")
        self._total_label.setStyleSheet("font-weight: bold; font-size: 14px;")
        av.addWidget(self._total_label)

        bh.addWidget(actions, 1)

        spl.addWidget(bottom)
        spl.setSizes([500, 250])
        vbox.addWidget(spl, 1)

        # Footer
        footer = QHBoxLayout()
        fetch_btn = QPushButton("Fetch Prices")
        fetch_btn.clicked.connect(self._fetch_prices)
        fetch_missing_btn = QPushButton("Fetch Missing Prices")
        fetch_missing_btn.clicked.connect(self._fetch_missing_prices)
        export_btn = QPushButton("Export CSV…")
        export_btn.clicked.connect(self._export_csv)
        exit_btn = QPushButton("Close")
        exit_btn.clicked.connect(self.close)

        footer.addWidget(fetch_btn)
        footer.addWidget(fetch_missing_btn)
        footer.addWidget(export_btn)
        footer.addStretch()
        footer.addWidget(exit_btn)
        vbox.addLayout(footer)

    def _populate_bom(self):
        # Group by (LCSC Part #, Value)
        # If LCSC is missing, use (Value, Footprint)
        groups = {}
        for p in self._bom_data:
            key = (p["lcsc"], p["val"]) if p["lcsc"] else (None, p["val"], p["fp"])
            if key not in groups:
                groups[key] = {
                    "refs": [],
                    "val": p["val"],
                    "lcsc": p["lcsc"],
                    "fp": p["fp"],
                }
            groups[key]["refs"].append(p["ref"])

        self._tbl.setRowCount(0)
        self._row_refs.clear()
        for key, data in groups.items():
            r = self._tbl.rowCount()
            self._tbl.insertRow(r)

            refs = sorted(data["refs"])
            qty = len(refs)
            pid = data["lcsc"] or ""
            self._row_refs[r] = refs

            self._tbl.setItem(r, CB_REFS, QTableWidgetItem(", ".join(refs)))
            self._tbl.setItem(r, CB_PART, QTableWidgetItem(pid))
            self._tbl.setItem(r, CB_VALUE, QTableWidgetItem(data["val"]))
            self._tbl.setItem(r, CB_QTY, QTableWidgetItem(str(qty)))

            # Initial placeholder for others
            for c in (
                CB_DESC,
                CB_STOCK,
                CB_VALID,
                CB_SYM,
                CB_FP,
                CB_STEP,
                CB_PDF,
                CB_JLC,
                CB_PRICE,
                CB_SUBTOTAL,
                CB_LINKED,
            ):
                self._tbl.setItem(r, c, QTableWidgetItem(""))

            # If we have a PID, try to load from library
            if pid:
                self._load_part_from_lib(pid, r)
                self._update_linked_cell(r, pid)

        self._update_total()

    def _load_part_from_lib(self, pid, row):
        if not self._output_dir:
            return

        # Check library for existence and STEP
        existing = _check_existing(pid, self._output_dir)

        # Initialize PartState
        state = PartState(pid)
        state.valid = (
            St.SUCCESS
            if existing.get("sym_ok") or existing.get("fp_ok")
            else St.PENDING
        )
        state.symbol = St.SUCCESS if existing.get("sym_ok") else St.PENDING
        state.footprint = St.SUCCESS if existing.get("fp_ok") else St.PENDING
        state.step = St.SUCCESS if existing.get("step_ok") else St.PENDING
        state.pdf = St.SUCCESS if existing.get("pdf_ok") else St.PENDING
        if existing.get("fp_name"):
            state.fp_name_text = existing["fp_name"]
        self._parts[pid] = state

        # Update cells
        for step in ("valid", "symbol", "footprint", "step", "pdf"):
            self._set_cell(pid, step, state.get(step))

        if existing.get("sym_ok"):
            sym_file = _find_sym_file(pid, Path(self._output_dir) / "symbol")
            if sym_file:
                try:
                    for e in _parse_sym_file(sym_file):
                        if e.get("lcsc") == pid:
                            self._tbl.item(row, CB_DESC).setText(
                                e.get("description", "")
                            )
                            self._tbl.item(row, CB_STOCK).setText(e.get("stock", ""))
                            self._tbl.item(row, CB_PRICE).setText(e.get("price", ""))

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
                            state.jlc = (
                                St.SUCCESS
                                if (state.price or state.stock)
                                else St.PENDING
                            )
                            self._set_cell(pid, "jlc", state.jlc)

                            self._update_row_subtotal(row)
                            break
                except Exception:
                    pass

    def _update_row_subtotal(self, row):
        try:
            qty = int(self._tbl.item(row, CB_QTY).text())
            price_str = self._tbl.item(row, CB_PRICE).text()
            price_match = re.search(r"(\d+\.?\d*)", price_str)
            if price_match:
                price = float(price_match.group(1))
                subtotal = qty * price
                self._tbl.item(row, CB_SUBTOTAL).setText(f"${subtotal:.2f}")
        except (ValueError, TypeError):
            pass

    def _update_total(self):
        total = 0.0
        for r in range(self._tbl.rowCount()):
            item = self._tbl.item(r, CB_SUBTOTAL)
            if item is None:
                continue
            match = re.search(r"(\d+\.?\d*)", item.text())
            if match:
                total += float(match.group(1))
        self._total_label.setText(f"Total BOM Price: ${total:.2f}")

    def _on_row_changed(self, row, _col, _pr, _pc):
        if row < 0:
            self._update_action_btn(-1)
            return
        pid = self._tbl.item(row, CB_PART).text()
        self._lcsc_input.setText(pid)

        for w in self._meta_widgets.values():
            if isinstance(w, QComboBox):
                w.setCurrentText("")
            else:
                w.setText("")

        if pid in self._parts:
            s = self._parts[pid]
            for _label, attr, _editable in METADATA_FIELDS:
                w = self._meta_widgets[attr]
                val = getattr(s, attr, "")
                if isinstance(w, QComboBox):
                    w.setCurrentText(val)
                else:
                    w.setText(val)

        self._update_action_btn(row)
        self._update_sym_btn(row)

    def _set_cell(self, pid, step, st: St, tip=""):
        # Find row(s) for this pid
        rows = []
        for r in range(self._tbl.rowCount()):
            it = self._tbl.item(r, CB_PART)
            if it and it.text() == pid:
                rows.append(r)

        if not rows:
            return

        # Map step to BOM column
        step_map = {
            "valid": CB_VALID,
            "symbol": CB_SYM,
            "footprint": CB_FP,
            "step": CB_STEP,
            "pdf": CB_PDF,
            "jlc": CB_JLC,
        }
        col = step_map.get(step)
        if col is None:
            return

        for r in rows:
            it = self._tbl.item(r, col)
            if it is None:
                it = QTableWidgetItem()
                it.setFlags(Qt.ItemIsEnabled)
                self._tbl.setItem(r, col, it)
            it.setText(st.value)
            it.setForeground(QColor(ST_COLOR[st]))
            it.setTextAlignment(Qt.AlignCenter)
            if tip:
                it.setToolTip(tip)

    def _on_started(self, pid, step):
        s = self._parts.get(pid)
        if s:
            s.put(step, St.PROCESSING)
            self._set_cell(pid, step, St.PROCESSING)

    def _fetch_prices(self):
        if not self._output_dir:
            QMessageBox.warning(
                self,
                "No Library",
                "Please set Library Location in main window settings.",
            )
            return

        pids = []
        for r in range(self._tbl.rowCount()):
            pid = self._tbl.item(r, CB_PART).text()
            if pid:
                pids.append(pid)

        if not pids:
            return
        self._run_jlc_fetch(pids)

    def _fetch_missing_prices(self):
        if not self._output_dir:
            QMessageBox.warning(
                self,
                "No Library",
                "Please set Library Location in main window settings.",
            )
            return

        pids = []
        for r in range(self._tbl.rowCount()):
            pid = self._tbl.item(r, CB_PART).text()
            price = self._tbl.item(r, CB_PRICE).text()
            if pid and not price:
                pids.append(pid)

        if not pids:
            QMessageBox.information(
                self, "Fetch Missing", "No parts missing price info."
            )
            return

        self._run_jlc_fetch(pids)

    def _on_fetch_single(self):
        row = self._tbl.currentRow()
        if row < 0:
            return
        pid = self._tbl.item(row, CB_PART).text()
        if not pid:
            QMessageBox.warning(self, "No Part", "Selected row has no LCSC Part #.")
            return
        if not self._output_dir:
            QMessageBox.warning(
                self, "No Library", "Set library location in main window."
            )
            return

        self._run_jlc_fetch([pid])

    def _run_jlc_fetch(self, pids):
        cfg = {"output_dir": self._output_dir, "jlcpcb_api_key": ""}
        if CACHE_FILE.exists():
            try:
                d = json.loads(CACHE_FILE.read_text())
                cfg["jlcpcb_api_key"] = d.get("jlcpcb_api_key", "")
            except Exception:
                pass

        for pid in pids:
            self._worker.retry(pid, "jlc", cfg)

        if len(pids) == 1:
            QMessageBox.information(
                self, "Fetching", f"Fetching price for {pids[0]}..."
            )
        else:
            QMessageBox.information(
                self, "Fetching", f"Started fetching data for {len(pids)} part(s)."
            )

    def _on_done(self, pid, step, ok, extra):
        s = self._parts.get(pid)
        if not s:
            return

        ns = St.SUCCESS if ok else St.FAILED
        if step == "pdf" and extra.get("skipped"):
            ns = St.SKIPPED

        s.put(step, ns)
        tip = extra.get("error", "") if not ok else ""
        self._set_cell(pid, step, ns, tip)

        # Update library if JLC data was fetched
        if step == "jlc" and ok:
            s.price = extra.get("price", "")
            s.stock = extra.get("stock", "")
            if self._output_dir:
                for attr, prop in (
                    ("price", "Price"),
                    ("stock", "Stock"),
                    ("description", "Description"),
                    ("mfr", "Manufacturer"),
                    ("category", "Category"),
                ):
                    val = getattr(s, attr, "")
                    if val:
                        _update_symbol_property(pid, self._output_dir, prop, val)

        # Update table columns
        for r in range(self._tbl.rowCount()):
            if self._tbl.item(r, CB_PART).text() == pid:
                if step == "jlc" and ok:
                    self._tbl.item(r, CB_STOCK).setText(extra.get("stock", ""))
                    self._tbl.item(r, CB_PRICE).setText(extra.get("price", ""))
                    self._update_row_subtotal(r)
                    self._update_total()

                if step == "symbol" and ok:
                    desc = extra.get("description", "")
                    if desc:
                        self._tbl.item(r, CB_DESC).setText(desc)

                if step == "footprint" and ok:
                    fp_name = extra.get("fp_name", "")
                    if fp_name and s:
                        s.fp_name_text = fp_name

                self._update_linked_cell(r, pid)
                break

        cur = self._tbl.currentRow()
        if cur >= 0 and self._tbl.item(cur, CB_PART).text() == pid:
            self._on_row_changed(cur, 0, 0, 0)

    def _on_scrape_done(self, pid, data):
        pass

    def _on_save_lcsc(self):
        row = self._tbl.currentRow()
        if row < 0:
            return
        new_pid = normalize_pid(self._lcsc_input.text().strip())
        if not new_pid:
            return

        old_pid = self._tbl.item(row, CB_PART).text()
        if new_pid == old_pid:
            return

        self._tbl.item(row, CB_PART).setText(new_pid)
        self._load_part_from_lib(new_pid, row)
        self._update_linked_cell(row, new_pid)
        self._update_action_btn(row)
        self._update_sym_btn(row)
        self._update_total()

    def _is_linked(self, pid: str, refs: list) -> "bool | None":
        """Return True if all PCB refs use our custom library footprint, False if not, None if unknown."""
        if not pid or not refs:
            return None
        state = self._parts.get(pid)
        if not state or not state.fp_name_text:
            return None
        if ":" not in state.fp_name_text:
            return None
        our_lib, our_fp_name = state.fp_name_text.split(":", 1)
        if not our_fp_name:
            return None
        for ref in refs:
            pcb_info = self._pcb_refs.get(ref, {})
            # Must match BOTH the library nickname AND the footprint item name.
            # Checking only the item name causes false positives when a standard
            # KiCad library footprint happens to share a name with ours.
            if pcb_info.get("lib") != our_lib or pcb_info.get("fp") != our_fp_name:
                return False
        return True

    def _update_linked_cell(self, row: int, pid: str):
        refs = self._row_refs.get(row, [])
        linked = self._is_linked(pid, refs)
        item = self._tbl.item(row, CB_LINKED)
        if item is None:
            item = QTableWidgetItem()
            item.setFlags(Qt.ItemIsEnabled)
            self._tbl.setItem(row, CB_LINKED, item)
        if linked is True:
            item.setText("✓")
            item.setForeground(QColor("#4CAF50"))
            item.setToolTip("PCB footprint is linked to custom library")
        elif linked is False:
            item.setText("✗")
            item.setForeground(QColor("#F44336"))
            item.setToolTip("PCB footprint is not linked to custom library")
        else:
            item.setText("?")
            item.setForeground(QColor("#9E9E9E"))
            item.setToolTip("Link status unknown")
        item.setTextAlignment(Qt.AlignCenter)

    def _update_action_btn(self, row: int):
        if row < 0:
            self._action_btn.setEnabled(False)
            self._action_btn.setText("Download Component")
            return
        pid_item = self._tbl.item(row, CB_PART)
        pid = pid_item.text() if pid_item else ""
        if not pid:
            self._action_btn.setEnabled(False)
            self._action_btn.setText("Download Component")
            return
        refs = self._row_refs.get(row, [])
        state = self._parts.get(pid)
        is_downloaded = state is not None and state.footprint == St.SUCCESS
        is_linked = self._is_linked(pid, refs)
        if is_linked is True:
            self._action_btn.setText("Already Linked")
            self._action_btn.setEnabled(False)
            self._action_btn.setToolTip(
                "PCB already references the custom library footprint.\n"
                "If footprint data (pads, properties) looks stale, use\n"
                "KiCad PCB Editor → Tools → Update Footprints from Library."
            )
        elif is_downloaded:
            self._action_btn.setText("Replace Component")
            self._action_btn.setEnabled(True)
            self._action_btn.setToolTip(
                "Update PCB and schematic to use the custom library footprint"
            )
        else:
            self._action_btn.setText("Download Component")
            self._action_btn.setEnabled(True)
            self._action_btn.setToolTip("Download component to custom library")

    def _on_action_btn(self):
        row = self._tbl.currentRow()
        if row < 0:
            return
        pid_item = self._tbl.item(row, CB_PART)
        pid = pid_item.text() if pid_item else ""
        refs = self._row_refs.get(row, [])
        state = self._parts.get(pid)
        is_downloaded = state is not None and state.footprint == St.SUCCESS
        is_linked = self._is_linked(pid, refs)
        if is_linked is True:
            return
        if is_downloaded:
            self._on_replace_pcb(pid, refs)
        else:
            self._on_download(pid)

    def _on_download(self, pid: str = ""):
        if not pid:
            pid = normalize_pid(self._lcsc_input.text().strip())
        if not pid:
            QMessageBox.warning(self, "Invalid", "Enter a valid LCSC part number.")
            return
        if not self._output_dir:
            QMessageBox.warning(
                self, "No Library", "Set library location in main window."
            )
            return
        cfg = {
            "output_dir": self._output_dir,
            "dl_step": True,
            "dl_pdf": False,
            "lib_prefix": "",
            "lib_mode": "organised",
            "jlcpcb_api_key": "",
        }
        if CACHE_FILE.exists():
            try:
                d = json.loads(CACHE_FILE.read_text())
                cfg.update(
                    {
                        "dl_step": d.get("dl_step", True),
                        "dl_pdf": d.get("dl_pdf", False),
                        "lib_prefix": d.get("lib_prefix", ""),
                        "lib_mode": d.get("lib_mode", "organised"),
                        "jlcpcb_api_key": d.get("jlcpcb_api_key", ""),
                    }
                )
            except Exception:
                pass
        ensure_libraries(cfg["output_dir"], cfg["lib_prefix"], cfg["lib_mode"])
        if pid not in self._parts:
            self._parts[pid] = PartState(pid)
        self._worker.process(pid, cfg, overwrite=True)

    def _on_replace_pcb(self, pid: str, refs: list):
        state = self._parts.get(pid)
        if not state or not state.fp_name_text or ":" not in state.fp_name_text:
            QMessageBox.warning(self, "Not Downloaded", "Component not in library yet.")
            return
        new_fp = state.fp_name_text
        if not self._pcb_file or not Path(self._pcb_file).exists():
            QMessageBox.warning(
                self,
                "No PCB File",
                "PCB file not found. Close and reopen the BOM Manager from KiCad.",
            )
            return
        errors = []
        try:
            pcb_path = Path(self._pcb_file)
            content = pcb_path.read_text(encoding="utf-8")
            content = _patch_pcb_footprint_refs(content, refs, new_fp)
            pcb_path.write_text(content, encoding="utf-8")
        except Exception as e:
            errors.append(f"PCB update failed: {e}")
        sch_files = self._find_sch_files()
        patched_sch = 0
        for sch_path in sch_files:
            try:
                content = sch_path.read_text(encoding="utf-8")
                new_content = _patch_sch_footprint_refs(content, refs, new_fp)
                if new_content != content:
                    sch_path.write_text(new_content, encoding="utf-8")
                    patched_sch += 1
            except Exception as e:
                errors.append(f"Schematic update failed ({sch_path.name}): {e}")
        # Update our cached PCB ref info so linked status reflects reality
        lib_part, fp_name = new_fp.split(":", 1)
        for ref in refs:
            self._pcb_refs[ref] = {"lib": lib_part, "fp": fp_name}
        row = self._tbl.currentRow()
        if row >= 0:
            self._update_linked_cell(row, pid)
            self._update_action_btn(row)
            self._update_sym_btn(row)
        if errors:
            QMessageBox.warning(self, "Partial Update", "\n".join(errors))
        else:
            msg = f"Footprint reference updated for: {', '.join(refs)}\n"
            msg += f"Now using: {new_fp}\n\n"
            if patched_sch:
                msg += f"PCB and {patched_sch} schematic file(s) updated.\n\n"
                msg += "Next steps in KiCad:\n"
                msg += "1. Reload the files (File → Revert).\n"
                msg += "2. In PCB Editor: Tools → Update Footprints from Library\n"
                msg += "   (this refreshes pads, 3D model, and properties from\n"
                msg += "   the newly downloaded component).\n"
                msg += "3. In Schematic Editor: Tools → Update PCB from Schematic\n"
                msg += "   (to propagate any remaining schematic changes to the PCB)."
            else:
                msg += "PCB file updated (component not found in any schematic sheet).\n\n"
                msg += "Next steps in KiCad:\n"
                msg += "1. Reload the PCB (File → Revert).\n"
                msg += "2. Tools → Update Footprints from Library\n"
                msg += "   (refreshes pads, 3D model, and properties)."
            QMessageBox.information(self, "Done", msg)

    def _find_our_sym(self, pid: str) -> "tuple | None":
        """Return (sym_file, sym_name, lib_id) for pid, or None if not found.
        Scans every .kicad_sym so the right library is found regardless of
        alphabetical ordering."""
        if not self._output_dir:
            return None
        sym_dir = Path(self._output_dir) / "symbol"
        if not sym_dir.exists():
            return None
        # Prefer the primary library so 'both' modes pick the lib_id the user
        # intends; fall back to all files if the part isn't in the primary set.
        primary = list_sym_files(self._output_dir, self._lib_mode)
        rest = [f for f in sorted(sym_dir.glob("*.kicad_sym")) if f not in primary]
        for sf in primary + rest:
            try:
                for e in _parse_sym_file(sf):
                    if e.get("lcsc") == pid:
                        sym_name = e.get("name", "")
                        if sym_name:
                            return sf, sym_name, f"{sf.stem}:{sym_name}"
            except Exception:
                pass
        return None

    def _get_our_sym_lib_id(self, pid: str) -> "str | None":
        r = self._find_our_sym(pid)
        return r[2] if r else None

    def _update_sym_btn(self, row: int):
        if row < 0:
            self._replace_sym_btn.setEnabled(False)
            return
        pid_item = self._tbl.item(row, CB_PART)
        pid = pid_item.text() if pid_item else ""
        if not pid:
            self._replace_sym_btn.setEnabled(False)
            return
        state = self._parts.get(pid)
        sym_in_lib = state is not None and state.symbol == St.SUCCESS
        sch_exists = bool(self._find_sch_files())
        self._replace_sym_btn.setEnabled(sym_in_lib and sch_exists)
        if sym_in_lib and not sch_exists:
            self._replace_sym_btn.setToolTip(
                "No schematic file found alongside the PCB file."
            )
        else:
            self._replace_sym_btn.setToolTip(
                "Update the schematic symbol link to use the custom library symbol"
            )

    def _on_replace_sym(self):
        row = self._tbl.currentRow()
        if row < 0:
            return
        pid_item = self._tbl.item(row, CB_PART)
        pid = pid_item.text() if pid_item else ""
        refs = self._row_refs.get(row, [])
        if not pid or not refs:
            return

        sch_files = self._find_sch_files()
        if not sch_files:
            QMessageBox.warning(
                self,
                "No Schematic",
                "No schematic files found in the project directory.",
            )
            return

        sym_info = self._find_our_sym(pid)
        if not sym_info:
            QMessageBox.warning(
                self,
                "Symbol Not Found",
                f"Could not find symbol for {pid} in the custom library.\n"
                "Download the component first.",
            )
            return
        sym_file, sym_name, new_lib_id = sym_info
        lib_nickname = new_lib_id.split(":")[0]

        # Pre-extract the symbol block so we can inject it into lib_symbols
        sym_block = _extract_sym_block_for_schematic(sym_file, sym_name, lib_nickname)

        warn = (
            f"This will update the schematic symbol link for:\n"
            f"  {', '.join(refs)}\n\n"
            f"New symbol:  {new_lib_id}\n\n"
            "Risks\n"
            "─────\n"
            "• If the custom symbol has different pins than the\n"
            "  original, net connections may silently break.\n"
            "• KiCad does not warn you about pin mapping changes.\n\n"
            "After applying\n"
            "──────────────\n"
            "1. Reload the schematic in KiCad (File → Revert).\n"
            "2. Run ERC to check for unconnected or mismatched pins.\n"
            "3. Run Tools → Update PCB from Schematic to propagate\n"
            "   the new symbol and footprint to the PCB layout.\n\n"
            "Continue?"
        )
        reply = QMessageBox.warning(
            self,
            "Replace Symbol — Confirm",
            warn,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel,
        )
        if reply != QMessageBox.Yes:
            return

        errors = []
        patched = 0
        for sch_path in sch_files:
            try:
                content = sch_path.read_text(encoding="utf-8")
                new_content = _patch_sch_lib_id_refs(content, refs, new_lib_id)
                if new_content != content:
                    # Also inject the symbol definition into lib_symbols so
                    # KiCad can resolve the new lib_id without "symbol not found"
                    if sym_block:
                        new_content = _ensure_sym_in_lib_symbols(new_content, sym_block)
                    sch_path.write_text(new_content, encoding="utf-8")
                    patched += 1
            except Exception as e:
                errors.append(f"{sch_path.name}: {e}")

        if errors:
            QMessageBox.critical(self, "Error", "Failed to update schematic(s):\n" + "\n".join(errors))
            return

        QMessageBox.information(
            self,
            "Done",
            f"Symbol link updated for: {', '.join(refs)}\n"
            f"Now using: {new_lib_id}\n"
            f"({patched} schematic sheet(s) patched)\n\n"
            "Next steps in KiCad:\n"
            "1. Reload the schematic (File → Revert).\n"
            "2. Run ERC to verify pin connections.\n"
            "3. Tools → Update PCB from Schematic."
        )

    def _find_sch_files(self) -> "list[Path]":
        """Return all .kicad_sch files in the project directory (including sub-sheets)."""
        if not self._pcb_file:
            return []
        project_dir = Path(self._pcb_file).parent
        return sorted(project_dir.rglob("*.kicad_sch"))

    def _export_csv(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export BOM", "", "CSV Files (*.csv)"
        )
        if not path:
            return

        try:
            with open(path, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(BOM_COL_NAMES)
                for r in range(self._tbl.rowCount()):
                    row_data = [
                        self._tbl.item(r, c).text()
                        for c in range(self._tbl.columnCount())
                    ]
                    writer.writerow(row_data)
            QMessageBox.information(self, "Exported", f"BOM exported to {path}")
        except Exception as e:
            QMessageBox.critical(self, "Export Error", f"Could not export CSV: {e}")

    def closeEvent(self, event):
        self._worker.stop()
        self._worker.wait(2000)
        super().closeEvent(event)


class MainWindow(QMainWindow):
    def __init__(self, bom_file=None):
        super().__init__()
        self.setWindowTitle("KiCad Library Manager")
        self.resize(1050, 720)
        self._bom_file = bom_file
        self._parts: dict[str, PartState] = {}
        self._dl_step = True
        self._dl_pdf = False
        self._lib_prefix = ""
        self._lib_mode = "organised"
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
            ensure_libraries(self._output_dir, self._lib_prefix, self._lib_mode)

        if self._bom_file:
            from PySide6.QtCore import QTimer

            QTimer.singleShot(100, self._open_bom_window)

    def _open_bom_window(self):
        if not self._bom_file:
            return
        try:
            p = Path(self._bom_file)
            if not p.exists():
                return
            raw = json.loads(p.read_text())
            if isinstance(raw, list):
                bom_data = raw
                pcb_file = ""
            else:
                bom_data = raw.get("parts", [])
                pcb_file = raw.get("pcb_file", "")
            self._bom_win = BOMWindow(
                bom_data, self._output_dir, pcb_file=pcb_file, lib_mode=self._lib_mode
            )
            self._bom_win.show()
        except Exception as e:
            QMessageBox.warning(self, "BOM Error", f"Failed to load BOM data: {e}")

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
        bulk_btn = QPushButton("Bulk Import")
        bulk_btn.setFixedWidth(100)
        bulk_btn.clicked.connect(self._bulk_import)
        row.addWidget(bulk_btn)
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
            self._lib_mode,
        )
        if dlg.exec() == QDialog.Accepted:
            self._dl_step = dlg.dl_step
            self._dl_pdf = dlg.dl_pdf
            self._col_visible = dlg.col_visible
            self._lib_prefix = dlg.lib_prefix
            self._lib_mode = dlg.lib_mode
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
                ensure_libraries(self._output_dir, self._lib_prefix, self._lib_mode)

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
            self._lib_mode = d.get("lib_mode", "organised")
            # Legacy "both" predates the primary split → keep organised primary.
            if self._lib_mode == "both":
                self._lib_mode = "both_organised"
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
                        "lib_mode": self._lib_mode,
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
            "lib_mode": self._lib_mode,
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
        return normalize_pid(s)

    def _import_pid(self, pid, cfg):
        """Queue a single (already-normalised) part for import. No-op if known."""
        if pid in self._parts:
            return False
        state = PartState(
            pid, pdf_url=f"https://www.lcsc.com/product-detail/{pid}.html"
        )
        self._insert_row(pid, state)
        self._worker.process(pid, cfg)
        return True

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
                self._worker.process(pid, cfg, overwrite=True)
            return
        self._save_cache()
        self._import_pid(pid, cfg)

    def _bulk_import(self):
        cfg = self._check_cfg()
        if not cfg:
            return
        dlg = BulkImportDialog(self)
        if dlg.exec() != QDialog.Accepted:
            return
        pids, invalid = parse_parts(dlg.tokens())
        if not pids:
            QMessageBox.warning(
                self, "Bulk Import", "No valid LCSC part numbers found."
            )
            return
        self._save_cache()
        added = sum(1 for pid in pids if self._import_pid(pid, cfg))
        msg = f"Queued {added} part(s) for import."
        skipped = len(pids) - added
        if skipped:
            msg += f"\n{skipped} already in the list."
        if invalid:
            msg += f"\nIgnored {len(invalid)} invalid entr(y/ies)."
        QMessageBox.information(self, "Bulk Import", msg)

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
            value = extra.get("value") or self._read_value_from_sym(pid)
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
                    ("description", "Description"),
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
        # Honour the primary so the 'both' modes don't list every part twice.
        sym_files = list_sym_files(cfg["output_dir"], self._lib_mode)
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

                # Use unified check for footprint and STEP
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
        msg = f"Loaded {loaded} part(s)." if loaded else "No new parts found."
        self._log.append(msg)
        _update_sym_lib_table(cfg["output_dir"])

    def refresh_after_sync(self, synced_dir: str = ""):
        """Re-read the libraries after a Sync so this window and any open BOM
        window reflect the new on-disk state. Only refreshes views that point at
        the directory that was synced."""
        if not self._output_dir:
            return
        if synced_dir and Path(synced_dir) != Path(self._output_dir):
            return
        try:
            self._load_library()
        except Exception:
            pass
        win = getattr(self, "_bom_win", None)
        if win is None:
            return
        try:
            same_dir = not synced_dir or Path(win._output_dir) == Path(synced_dir)
            if win.isVisible() and same_dir:
                win._populate_bom()
        except RuntimeError:
            # Underlying Qt object already deleted — nothing to refresh.
            pass

    def closeEvent(self, event):
        self._save_cache()
        self._worker.stop()
        self._worker.wait(3000)
        super().closeEvent(event)
