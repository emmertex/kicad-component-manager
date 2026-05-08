"""gui2.py — LCSC to KiCad Library Converter v2"""

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from queue import Queue

from PySide6.QtCore import Qt, QThread, Signal
from PySide6.QtGui import QColor, QFont
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox,
    QFileDialog, QHBoxLayout, QHeaderView, QLabel, QLineEdit,
    QMainWindow, QMessageBox, QPushButton, QSizePolicy,
    QSplitter, QTableWidget, QTableWidgetItem, QTextEdit,
    QVBoxLayout, QWidget,
)

# ── Backend path setup ────────────────────────────────────────────────────────
_LIB = Path(__file__).parent / "lcsc2kicad-GUI" / "JLC2KiCadLib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import helper
import component_info as _cinfo
import pdf_downloader
from footprint.footprint import create_footprint
from symbol.symbol import create_symbol

logging.getLogger().setLevel(logging.INFO)

# ── Constants ─────────────────────────────────────────────────────────────────
CACHE_FILE = Path.home() / ".lcsc2kicad_cache.json"
MAX_RECENT = 10
PDF_MIN_BYTES = 10240
SYMBOL_LIB = "components"

C_PART, C_VALUE, C_DESC, C_VALID, C_SYM, C_FP, C_STEP, C_PDF, C_DEL = range(9)
COL_NAMES = ["LCSC Part #", "Value", "Description", "Valid", "Symbol", "Footprint", "STEP", "PDF", ""]
STEP_COLS = {"valid": C_VALID, "symbol": C_SYM, "footprint": C_FP, "step": C_STEP, "pdf": C_PDF}
COL_STEPS = {v: k for k, v in STEP_COLS.items()}


class St(Enum):
    PENDING    = "○"
    PROCESSING = "⟳"
    SUCCESS    = "✓"
    FAILED     = "✗"
    SKIPPED    = "—"


ST_COLOR = {
    St.PENDING:    "#808080",
    St.PROCESSING: "#2196F3",
    St.SUCCESS:    "#4CAF50",
    St.FAILED:     "#F44336",
    St.SKIPPED:    "#9E9E9E",
}


# ── Part state ────────────────────────────────────────────────────────────────
@dataclass
class PartState:
    pid: str
    valid:       St  = St.PENDING
    symbol:      St  = St.PENDING
    footprint:   St  = St.PENDING
    step:        St  = St.PENDING
    pdf:         St  = St.PENDING
    pdf_url:     str = ""
    value:       str = ""
    description: str = ""
    logs:        list = field(default_factory=list)

    def get(self, name): return getattr(self, name)
    def put(self, name, v): setattr(self, name, v)


# ── Log capture ───────────────────────────────────────────────────────────────
class _Cap(logging.Handler):
    def __init__(self, cb):
        super().__init__()
        self.cb = cb
        self.setFormatter(logging.Formatter("%(levelname)s %(message)s"))

    def emit(self, record):
        try:
            self.cb(self.format(record))
        except Exception:
            pass


# ── Worker ────────────────────────────────────────────────────────────────────
class Worker(QThread):
    step_started = Signal(str, str)
    step_done    = Signal(str, str, bool, dict)
    log_line     = Signal(str, str)

    def __init__(self):
        super().__init__()
        self._q: Queue = Queue()
        self._cache: dict[str, dict] = {}

    def process(self, pid, cfg):
        self._q.put(("new", pid, cfg))

    def retry(self, pid, step, cfg):
        self._q.put(("retry", pid, step, cfg))

    def stop(self):
        self._q.put(None)

    def run(self):
        while True:
            task = self._q.get()
            if task is None:
                break
            if task[0] == "new":
                self._full(task[1], task[2])
            elif task[0] == "retry":
                self._do_retry(task[1], task[2], task[3])

    # ── Full sequence ─────────────────────────────────────────────────────────
    def _full(self, pid, cfg):
        c = self._cache.setdefault(pid, {})
        ok, extra = self._do_validate(pid, cfg)
        if not ok:
            return
        c.update(extra)
        if not c.get("fp_ok"):
            self._do_footprint(pid, c, cfg)
        if not c.get("sym_ok"):
            self._do_symbol(pid, c, cfg)
        if cfg["dl_pdf"] and not c.get("pdf_ok"):
            self._do_pdf(pid, c, cfg)
        elif not cfg["dl_pdf"]:
            url = f"https://www.lcsc.com/product-detail/{pid}.html"
            self.step_done.emit(pid, "pdf", True, {"skipped": True, "url": url})

    def _do_retry(self, pid, step, cfg):
        c = self._cache.setdefault(pid, {})
        # Auto-validate if UUID missing
        if step in ("footprint", "step", "symbol", "pdf") and not c.get("fp_uuid"):
            ok, extra = self._do_validate(pid, cfg)
            if not ok:
                return
            c.update(extra)
        if step == "valid":
            ok, extra = self._do_validate(pid, cfg)
            if ok:
                c.update(extra)
        elif step in ("footprint", "step"):
            self._do_footprint(pid, c, cfg)
        elif step == "symbol":
            self._do_symbol(pid, c, cfg)
        elif step == "pdf":
            self._do_pdf(pid, c, cfg)

    # ── Steps ─────────────────────────────────────────────────────────────────
    def _do_validate(self, pid, cfg):
        self.step_started.emit(pid, "valid")
        log = self._logfn(pid)
        try:
            url = f"https://easyeda.com/api/products/{pid}/svgs"
            log(f"GET {url}")
            s = helper.get_easyeda_session()
            r = s.get(url, headers=helper.EASYEDA_HEADERS, timeout=30)
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            data = r.json()
            if not data.get("success"):
                raise RuntimeError("Part not found on EasyEDA")
            results = data["result"]
            fp_uuid = results[-1]["component_uuid"]
            sym_uuids = [i["component_uuid"] for i in results[:-1]]
            log(f"OK — {len(sym_uuids)} sym unit(s), fp {fp_uuid[:8]}…")
            existing = _check_existing(pid, cfg["output_dir"])
            log(f"Library: sym={existing.get('sym_ok')}, fp={existing.get('fp_ok')}, step={existing.get('step_ok')}, pdf={existing.get('pdf_ok')}")
            extra = {
                "fp_uuid": fp_uuid, "sym_uuids": sym_uuids,
                "lcsc_url": f"https://www.lcsc.com/product-detail/{pid}.html",
                **existing,
            }
            self.step_done.emit(pid, "valid", True, extra)
            return True, extra
        except Exception as e:
            log(f"FAIL: {e}")
            self.step_done.emit(pid, "valid", False, {"error": str(e)})
            return False, {}

    def _do_footprint(self, pid, c, cfg):
        fp_uuid = c.get("fp_uuid")
        if not fp_uuid:
            self.step_done.emit(pid, "footprint", False, {"error": "No UUID — re-run Valid"})
            return
        inc = cfg["dl_step"]
        h = _Cap(self._logfn(pid))
        logging.getLogger().addHandler(h)
        self.step_started.emit(pid, "footprint")
        if inc:
            self.step_started.emit(pid, "step")
        try:
            fp_name, ds_link = create_footprint(
                footprint_component_uuid=fp_uuid,
                component_id=pid,
                footprint_lib="footprint",
                output_dir=cfg["output_dir"],
                model_base_variable="",
                model_dir="packages3d",
                skip_existing=False,
                models="STEP" if inc else None,
            )
            c["fp_name"] = fp_name
            c["ds_link"] = ds_link
            self.step_done.emit(pid, "footprint", True, {"fp_name": fp_name})
            if inc:
                bare = fp_name.split(":")[-1] if ":" in fp_name else fp_name
                step_ok = (
                    Path(cfg["output_dir"]) / "footprint" / "packages3d" / f"{bare}.step"
                ).exists()
                self.step_done.emit(pid, "step", step_ok, {})
        except Exception as e:
            self._logfn(pid)(f"Footprint error: {e}")
            self.step_done.emit(pid, "footprint", False, {"error": str(e)})
            if inc:
                self.step_done.emit(pid, "step", False, {})
        finally:
            logging.getLogger().removeHandler(h)

    def _do_symbol(self, pid, c, cfg):
        sym_uuids = c.get("sym_uuids")
        if not sym_uuids:
            self.step_done.emit(pid, "symbol", False, {"error": "No UUIDs — re-run Valid"})
            return
        h = _Cap(self._logfn(pid))
        logging.getLogger().addHandler(h)
        self.step_started.emit(pid, "symbol")
        try:
            info = c.get("comp_info") or _cinfo.extract_component_info(pid)
            c["comp_info"] = info
            ds = c.get("ds_link") or c.get("lcsc_url") or ""
            fp = (c.get("fp_name") or "").replace(".pretty", "")
            create_symbol(
                symbol_component_uuid=sym_uuids,
                footprint_name=fp,
                datasheet_link=ds,
                library_name=SYMBOL_LIB,
                symbol_path="symbol",
                output_dir=cfg["output_dir"],
                component_id=pid,
                skip_existing=False,
                component_info_data=info,
            )
            self.step_done.emit(pid, "symbol", True, {
                "description": info.get("description", "") if info else "",
            })
        except Exception as e:
            self._logfn(pid)(f"Symbol error: {e}")
            self.step_done.emit(pid, "symbol", False, {"error": str(e)})
        finally:
            logging.getLogger().removeHandler(h)

    def _do_pdf(self, pid, c, cfg):
        h = _Cap(self._logfn(pid))
        logging.getLogger().addHandler(h)
        self.step_started.emit(pid, "pdf")
        try:
            ok, path, err = pdf_downloader.download_pdf(pid, cfg["output_dir"])
            if ok and path:
                sz = Path(path).stat().st_size if Path(path).exists() else 0
                if sz < PDF_MIN_BYTES:
                    ok = False
                    err = f"PDF too small ({sz} B) — likely placeholder"
                    try: Path(path).unlink()
                    except OSError: pass
            url = f"https://www.lcsc.com/product-detail/{pid}.html"
            self.step_done.emit(pid, "pdf", ok, {"url": url, "error": err or ""})
        except Exception as e:
            self._logfn(pid)(f"PDF error: {e}")
            url = f"https://www.lcsc.com/product-detail/{pid}.html"
            self.step_done.emit(pid, "pdf", False, {"url": url, "error": str(e)})
        finally:
            logging.getLogger().removeHandler(h)

    def _logfn(self, pid):
        def _l(msg): self.log_line.emit(pid, msg)
        return _l


# ── Library helpers ───────────────────────────────────────────────────────────
def _check_existing(pid, output_dir):
    lib = Path(output_dir)
    out = {}
    sym_file = lib / "symbol" / f"{SYMBOL_LIB}.kicad_sym"
    fp_ref = ""
    if sym_file.exists():
        txt = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = f'(property "LCSC" "{pid}"'
        if marker in txt:
            out["sym_ok"] = True
            idx = txt.find(marker)
            block = txt[max(0, idx - 3000):idx + 200]
            m = re.search(r'\(property "Footprint" "([^"]*)"', block)
            if m:
                fp_ref = m.group(1)
    if fp_ref and ":" in fp_ref:
        lib_name, fp_name = fp_ref.split(":", 1)
        mod = lib / f"{lib_name}.pretty" / f"{fp_name}.kicad_mod"
        if mod.exists():
            out["fp_ok"] = True
            out["fp_name"] = fp_ref
            # STEP lives under footprint_lib/ (no .pretty), not footprint_lib.pretty/
            step = lib / lib_name / "packages3d" / f"{fp_name}.step"
            if step.exists():
                out["step_ok"] = True
    pdf = lib / "pdf" / f"{pid}.pdf"
    if pdf.exists() and pdf.stat().st_size >= PDF_MIN_BYTES:
        out["pdf_ok"] = True
    return out


def _parse_sym_file(sym_file: Path):
    content = sym_file.read_text(encoding="utf-8", errors="replace")
    top_re = re.compile(r'^\t\(symbol "([^"]+)"', re.MULTILINE)
    prop_re = re.compile(r'^\t\t\(property "([^"]+)"\s+"((?:[^"\\]|\\.)*)"', re.MULTILINE)
    matches = list(top_re.finditer(content))
    results = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        chunk = content[start:end]
        props = {pm.group(1): pm.group(2) for pm in prop_re.finditer(chunk)}
        results.append({
            "name": m.group(1),
            "lcsc": props.get("LCSC", ""),
            "footprint": props.get("Footprint", ""),
            "datasheet": props.get("Datasheet", ""),
            "value": props.get("Value", ""),
            "description": props.get("Description_1", props.get("Description", "")),
        })
    return results


def _delete_from_lib(pid, output_dir):
    lib = Path(output_dir)
    errors = []
    fp_ref = ""

    sym_file = lib / "symbol" / f"{SYMBOL_LIB}.kicad_sym"
    if sym_file.exists():
        content = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = f'(property "LCSC" "{pid}"'
        if marker in content:
            idx = content.find(marker)
            block = content[max(0, idx - 3000):idx + 200]
            m = re.search(r'\(property "Footprint" "([^"]*)"', block)
            if m:
                fp_ref = m.group(1)
        # Remove the symbol block
        new = re.sub(
            r'\n  \(symbol "[^"]*".*?\n  \)',
            lambda m: "" if f'(property "LCSC" "{pid}"' in m.group(0) else m.group(0),
            content, flags=re.DOTALL,
        )
        if new != content:
            try:
                sym_file.write_text(new, encoding="utf-8")
            except OSError as e:
                errors.append(f"Symbol file: {e}")

    if fp_ref and ":" in fp_ref:
        ln, fn = fp_ref.split(":", 1)
        for p in [
            lib / f"{ln}.pretty" / f"{fn}.kicad_mod",
            lib / ln / "packages3d" / f"{fn}.step",
        ]:
            if p.exists():
                try: p.unlink()
                except OSError as e: errors.append(str(e))

    pdf = lib / "pdf" / f"{pid}.pdf"
    if pdf.exists():
        try: pdf.unlink()
        except OSError as e: errors.append(str(e))

    return errors


# ── Main window ───────────────────────────────────────────────────────────────
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LCSC to KiCad Converter")
        self.resize(1050, 720)
        self._parts: dict[str, PartState] = {}
        self._worker = Worker()
        self._worker.step_started.connect(self._on_started)
        self._worker.step_done.connect(self._on_done)
        self._worker.log_line.connect(self._on_log)
        self._worker.start()
        self._build_ui()
        self._load_cache()

    # ── UI construction ───────────────────────────────────────────────────────
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

        self._tbl = QTableWidget(0, 9)
        self._tbl.setHorizontalHeaderLabels(COL_NAMES)
        self._tbl.horizontalHeader().setSectionResizeMode(C_PART, QHeaderView.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(C_VALUE, QHeaderView.ResizeToContents)
        self._tbl.horizontalHeader().setSectionResizeMode(C_DESC, QHeaderView.Stretch)
        for c in (C_VALID, C_SYM, C_FP, C_STEP, C_PDF):
            self._tbl.horizontalHeader().setSectionResizeMode(c, QHeaderView.ResizeToContents)
        self._tbl.setColumnWidth(C_DEL, 38)
        self._tbl.horizontalHeader().setSectionResizeMode(C_DEL, QHeaderView.Fixed)
        self._tbl.setSelectionBehavior(QAbstractItemView.SelectRows)
        self._tbl.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self._tbl.cellClicked.connect(self._on_cell)
        self._tbl.currentCellChanged.connect(
            lambda cur_r, _cc, _pr, _pc: self._on_row(cur_r)
        )
        spl.addWidget(self._tbl)

        self._log = QTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Monospace", 9))
        self._log.setMaximumHeight(160)
        spl.addWidget(self._log)
        spl.setSizes([520, 140])
        vbox.addWidget(spl, 1)

        # Library location row
        row2 = QHBoxLayout()
        row2.addWidget(QLabel("Library Location:"))
        self._lib = QComboBox()
        self._lib.setEditable(True)
        self._lib.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        row2.addWidget(self._lib)
        br = QPushButton("Browse…")
        br.clicked.connect(self._browse)
        row2.addWidget(br)
        vbox.addLayout(row2)

        # Options
        row3 = QHBoxLayout()
        self._step_cb = QCheckBox("Download STEP")
        self._step_cb.setChecked(True)
        self._pdf_cb = QCheckBox("Download PDF")
        self._pdf_cb.setChecked(False)
        row3.addWidget(self._step_cb)
        row3.addWidget(self._pdf_cb)
        row3.addStretch()
        vbox.addLayout(row3)

        # Bottom buttons
        row4 = QHBoxLayout()
        load_btn = QPushButton("Load Library")
        load_btn.clicked.connect(self._load_library)
        exit_btn = QPushButton("Exit")
        exit_btn.clicked.connect(self.close)
        row4.addWidget(load_btn)
        row4.addStretch()
        row4.addWidget(exit_btn)
        vbox.addLayout(row4)

    # ── Cache ─────────────────────────────────────────────────────────────────
    def _load_cache(self):
        if not CACHE_FILE.exists():
            return
        try:
            d = json.loads(CACHE_FILE.read_text())
            for loc in d.get("recent_dirs", []):
                if self._lib.findText(loc) == -1:
                    self._lib.addItem(loc)
            if self._lib.count():
                self._lib.setCurrentIndex(0)
            self._step_cb.setChecked(d.get("dl_step", True))
            self._pdf_cb.setChecked(d.get("dl_pdf", False))
        except Exception:
            pass

    def _save_cache(self):
        cur = self._lib.currentText().strip()
        dirs = [self._lib.itemText(i) for i in range(self._lib.count())]
        if cur:
            if cur in dirs:
                dirs.remove(cur)
            dirs.insert(0, cur)
        dirs = dirs[:MAX_RECENT]
        # Rebuild combo to reflect order
        self._lib.blockSignals(True)
        self._lib.clear()
        for d in dirs:
            self._lib.addItem(d)
        if dirs:
            self._lib.setCurrentIndex(0)
        self._lib.blockSignals(False)
        try:
            CACHE_FILE.write_text(json.dumps({
                "recent_dirs": dirs,
                "dl_step": self._step_cb.isChecked(),
                "dl_pdf": self._pdf_cb.isChecked(),
            }, indent=2))
        except Exception:
            pass

    def _cfg(self):
        return {
            "output_dir": self._lib.currentText().strip(),
            "dl_step": self._step_cb.isChecked(),
            "dl_pdf": self._pdf_cb.isChecked(),
        }

    def _check_cfg(self):
        c = self._cfg()
        if not c["output_dir"]:
            QMessageBox.warning(self, "No Library", "Please set Library Location first.")
            return None
        return c

    # ── Part management ───────────────────────────────────────────────────────
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
        state = PartState(pid, pdf_url=f"https://www.lcsc.com/product-detail/{pid}.html")
        self._insert_row(pid, state)
        self._worker.process(pid, cfg)

    def _insert_row(self, pid, state: PartState):
        self._parts[pid] = state
        r = self._tbl.rowCount()
        self._tbl.insertRow(r)
        item = QTableWidgetItem(pid)
        item.setFlags(Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        self._tbl.setItem(r, C_PART, item)
        self._set_text_cell(r, C_VALUE, state.value)
        self._set_text_cell(r, C_DESC, state.description, clip=True)
        for step in ("valid", "symbol", "footprint", "step", "pdf"):
            self._set_cell(pid, step, state.get(step))
        del_btn = QPushButton("🗑")
        del_btn.setStyleSheet("color:#F44336;border:none;font-size:14px;")
        del_btn.setFixedWidth(34)
        del_btn.clicked.connect(lambda _, p=pid: self._delete(p))
        self._tbl.setCellWidget(r, C_DEL, del_btn)

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
            it = QTableWidgetItem()
            it.setFlags(Qt.ItemIsEnabled)
            self._tbl.setItem(r, col, it)
        it.setText(st.value)
        it.setForeground(QColor(ST_COLOR[st]))
        it.setTextAlignment(Qt.AlignCenter)
        if tip:
            it.setToolTip(tip)

    # ── Worker signals ────────────────────────────────────────────────────────
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

        # Populate Value/Description after symbol is created
        if step == "symbol" and ok:
            desc = extra.get("description", "")
            # Read back Value from the newly-written kicad_sym
            value = self._read_value_from_sym(pid)
            self._update_info_cells(pid, value=value, description=desc)

        # After successful validate, mark already-present items
        if step == "valid" and ok:
            key_map = {"symbol": "sym_ok", "footprint": "fp_ok", "step": "step_ok", "pdf": "pdf_ok"}
            for sub, key in key_map.items():
                if extra.get(key):
                    s.put(sub, St.SUCCESS)
                    self._set_cell(pid, sub, St.SUCCESS, "Already in library")

    def _read_value_from_sym(self, pid):
        cfg = self._cfg()
        if not cfg["output_dir"]:
            return ""
        sym_file = Path(cfg["output_dir"]) / "symbol" / f"{SYMBOL_LIB}.kicad_sym"
        if not sym_file.exists():
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
        if row < 0:
            return
        it = self._tbl.item(row, C_PART)
        if not it:
            return
        s = self._parts.get(it.text())
        if s:
            self._log.setPlainText("\n".join(s.logs))
            sb = self._log.verticalScrollBar()
            sb.setValue(sb.maximum())

    # ── Cell click (retry) ────────────────────────────────────────────────────
    def _on_cell(self, row, col):
        it = self._tbl.item(row, C_PART)
        if not it:
            return
        pid = it.text()
        s = self._parts.get(pid)
        if not s or col not in COL_STEPS:
            return
        step = COL_STEPS[col]
        cur = s.get(step)
        if cur not in (St.FAILED, St.PENDING):
            return
        cfg = self._check_cfg()
        if not cfg:
            return
        s.put(step, St.PROCESSING)
        self._set_cell(pid, step, St.PROCESSING)
        self._worker.retry(pid, step, cfg)

    # ── Delete ────────────────────────────────────────────────────────────────
    def _delete(self, pid):
        reply = QMessageBox.question(
            self, "Delete Part",
            f"Remove {pid} from the KiCad library?\n"
            "This deletes the symbol, footprint, STEP, and PDF files.",
            QMessageBox.Yes | QMessageBox.No, QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        cfg = self._cfg()
        if cfg["output_dir"]:
            errs = _delete_from_lib(pid, cfg["output_dir"])
            if errs:
                QMessageBox.warning(self, "Delete Errors", "\n".join(errs))
        r = self._row(pid)
        if r >= 0:
            self._tbl.removeRow(r)
        self._parts.pop(pid, None)

    # ── Browse ────────────────────────────────────────────────────────────────
    def _browse(self):
        d = QFileDialog.getExistingDirectory(self, "Select Library Directory")
        if d:
            if self._lib.findText(d) == -1:
                self._lib.insertItem(0, d)
            self._lib.setCurrentText(d)
            self._save_cache()

    # ── Load Library ──────────────────────────────────────────────────────────
    def _load_library(self):
        cfg = self._check_cfg()
        if not cfg:
            return
        lib = Path(cfg["output_dir"])
        sym_dir = lib / "symbol"
        sym_files = sorted(sym_dir.glob("*.kicad_sym")) if sym_dir.exists() else []
        if not sym_files:
            QMessageBox.information(self, "No Library", f"No .kicad_sym files in {sym_dir}")
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
                s = PartState(pid, pdf_url=f"https://www.lcsc.com/product-detail/{pid}.html")
                s.value = e.get("value", "")
                s.description = e.get("description", "")
                s.valid = St.SUCCESS
                s.symbol = St.SUCCESS
                fp_ref = e.get("footprint", "")
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
                    s.pdf = St.SKIPPED
                else:
                    rel = re.sub(r'^\$\{[^}]+\}/', "", raw_ds)
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

    # ── Close ─────────────────────────────────────────────────────────────────
    def closeEvent(self, event):
        self._save_cache()
        self._worker.stop()
        self._worker.wait(3000)
        super().closeEvent(event)


if __name__ == "__main__":
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
