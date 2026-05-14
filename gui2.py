"""gui2.py — LCSC to KiCad Library Converter v2"""

import json
import logging
import re
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from queue import Queue

from PySide6.QtCore import Qt, QThread, QUrl, Signal
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

# ── Backend path setup ────────────────────────────────────────────────────────
_LIB = Path(__file__).parent / "lcsc2kicad-GUI" / "JLC2KiCadLib"
if str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

import component_info as _cinfo
import helper
import pdf_downloader
from footprint.footprint import create_footprint
from symbol.symbol import create_symbol

logging.getLogger().setLevel(logging.INFO)

# ── Constants ─────────────────────────────────────────────────────────────────
CACHE_FILE = Path.home() / ".lcsc2kicad_cache.json"
MAX_RECENT = 10
PDF_MIN_BYTES = 10240


def _lib_name(category: str, prefix: str) -> str:
    """Compute KiCad library filename from category and optional prefix."""
    safe = re.sub(r'_+', '_', re.sub(r'[^\w\-]', '_', (category or "").strip())).strip('_')
    if not safe:
        safe = "components"
    return f"{prefix}_{safe}" if prefix else safe


def _find_sym_file(pid: str, sym_dir: Path) -> "Path | None":
    """Search all .kicad_sym files in sym_dir for the given LCSC pid."""
    if not sym_dir.exists():
        return None
    marker = f'(property "LCSC" "{pid}"'
    for sf in sorted(sym_dir.glob("*.kicad_sym")):
        try:
            if marker in sf.read_text(encoding="utf-8", errors="replace"):
                return sf
        except OSError:
            pass
    return None

(
    C_PART,
    C_VALUE,
    C_DESC,
    C_MFR,
    C_CAT,
    C_FP_TEXT,
    C_PKG,
    C_ATTRS,
    C_VALID,
    C_SYM,
    C_FP,
    C_STEP,
    C_PDF,
    C_JLC,
    C_DEL,
) = range(15)
COL_NAMES = [
    "LCSC Part #",
    "Value",
    "Description",
    "Manufacturer",
    "Category",
    "Footprint Name",
    "Package",
    "Key Attributes",
    "Valid",
    "Sym",
    "FP",
    "STEP",
    "PDF",
    "JLC",
    "",
]
COL_VIEW_NAMES = [
    "LCSC Part #",
    "Value",
    "Description",
    "Manufacturer",
    "Category",
    "Footprint Name",
    "Package",
    "Key Attributes",
    "Valid",
    "Symbol",
    "Footprint",
    "STEP",
    "PDF",
    "JLC",
    "Delete",
]
STEP_COLS = {
    "valid": C_VALID,
    "symbol": C_SYM,
    "footprint": C_FP,
    "step": C_STEP,
    "pdf": C_PDF,
    "jlc": C_JLC,
}
COL_STEPS = {v: k for k, v in STEP_COLS.items()}

# Detail-panel metadata fields: (label, PartState attr, editable)
METADATA_FIELDS = [
    ("LCSC Part #", "pid", False),
    ("Value", "value", True),
    ("Description", "description", True),
    ("Manufacturer", "mfr", True),
    ("Category", "category", True),
    ("Footprint Name", "fp_name_text", False),
    ("Package", "package", True),
    ("Key Attributes", "attributes", True),
    ("Price", "price", False),
    ("Stock", "stock", False),
]

# PartState attr → table column index (for live cell updates on edit)
ATTR_COL = {
    "value": C_VALUE,
    "description": C_DESC,
    "mfr": C_MFR,
    "category": C_CAT,
    "fp_name_text": C_FP_TEXT,
    "package": C_PKG,
    "attributes": C_ATTRS,
}

# PartState attr → KiCad symbol property name(s) to patch
ATTR_PROP: dict[str, str | list] = {
    "value": "Value",
    "description": ["Description_1", "Description"],
    "mfr": "Manufacturer",
    "category": "Category",
    "package": "Package",
    "attributes": "Key_Attributes",
}


class St(Enum):
    PENDING = "○"
    PROCESSING = "⟳"
    SUCCESS = "✓"
    FAILED = "✗"
    SKIPPED = "—"


ST_COLOR = {
    St.PENDING: "#808080",
    St.PROCESSING: "#2196F3",
    St.SUCCESS: "#4CAF50",
    St.FAILED: "#F44336",
    St.SKIPPED: "#9E9E9E",
}

ST_SORT = {
    St.FAILED: 0,
    St.PROCESSING: 1,
    St.PENDING: 2,
    St.SKIPPED: 3,
    St.SUCCESS: 4,
}


# ── Sortable table item ───────────────────────────────────────────────────────
class _SortItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by Qt.UserRole when set, else display text."""

    def __lt__(self, other):
        lv = self.data(Qt.UserRole)
        rv = other.data(Qt.UserRole)
        if lv is not None and rv is not None:
            return lv < rv
        return super().__lt__(other)


# ── Part state ────────────────────────────────────────────────────────────────
@dataclass
class PartState:
    pid: str
    valid: St = St.PENDING
    symbol: St = St.PENDING
    footprint: St = St.PENDING
    step: St = St.PENDING
    pdf: St = St.PENDING
    jlc: St = St.PENDING
    pdf_url: str = ""
    value: str = ""
    description: str = ""
    fp_name_text: str = ""
    package: str = ""
    mfr: str = ""
    category: str = ""
    attributes: str = ""
    price: str = ""
    stock: str = ""
    logs: list = field(default_factory=list)

    def get(self, name):
        return getattr(self, name)

    def put(self, name, v):
        setattr(self, name, v)


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
    step_done = Signal(str, str, bool, dict)
    log_line = Signal(str, str)
    scrape_done = Signal(str, dict)

    def __init__(self):
        super().__init__()
        self._q: Queue = Queue()
        self._cache: dict[str, dict] = {}

    def process(self, pid, cfg):
        self._q.put(("new", pid, cfg))

    def retry(self, pid, step, cfg):
        self._q.put(("retry", pid, step, cfg))

    def scrape(self, pid):
        self._q.put(("scrape", pid))

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
            elif task[0] == "scrape":
                self._do_scrape(task[1])

    # ── Full sequence ─────────────────────────────────────────────────────────
    def _full(self, pid, cfg):
        c = self._cache.setdefault(pid, {})
        ok, extra = self._do_validate(pid, cfg)
        if not ok:
            return
        c.update(extra)
        self._do_jlc(pid, c, cfg)
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
        elif step == "jlc":
            self._do_jlc(pid, c, cfg)

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
            log(
                f"Library: sym={existing.get('sym_ok')}, fp={existing.get('fp_ok')}, step={existing.get('step_ok')}, pdf={existing.get('pdf_ok')}"
            )
            extra = {
                "fp_uuid": fp_uuid,
                "sym_uuids": sym_uuids,
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
            self.step_done.emit(
                pid, "footprint", False, {"error": "No UUID — re-run Valid"}
            )
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
                    Path(cfg["output_dir"])
                    / "footprint"
                    / "packages3d"
                    / f"{bare}.step"
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
            self.step_done.emit(
                pid, "symbol", False, {"error": "No UUIDs — re-run Valid"}
            )
            return
        h = _Cap(self._logfn(pid))
        logging.getLogger().addHandler(h)
        self.step_started.emit(pid, "symbol")
        try:
            # Prefer _fetch_lcsc_data as it uses the JSON API which includes Key Attributes
            info = c.get("comp_info") or _fetch_lcsc_data(pid)
            if not info:
                # Fallback to the BS4-based scraper if API fails
                info = _cinfo.extract_component_info(pid)

            c["comp_info"] = info
            category = info.get("category") or info.get("Category") or ""
            lib_name = _lib_name(category, cfg.get("lib_prefix", ""))
            ds = c.get("ds_link") or c.get("lcsc_url") or ""
            fp = (c.get("fp_name") or "").replace(".pretty", "")
            create_symbol(
                symbol_component_uuid=sym_uuids,
                footprint_name=fp,
                datasheet_link=ds,
                library_name=lib_name,
                symbol_path="symbol",
                output_dir=cfg["output_dir"],
                component_id=pid,
                skip_existing=False,
                component_info_data=info,
                price=info.get("price") or None,
                stock=info.get("stock") or None,
            )

            # Extract attributes for the GUI
            self.step_done.emit(
                pid,
                "symbol",
                True,
                {
                    "value": info.get("value", ""),
                    "description": info.get("description", ""),
                    "package": (info.get("package") or info.get("Package") or ""),
                    "mfr": (
                        info.get("mfr")
                        or info.get("manufacturer")
                        or info.get("Manufacturer")
                        or ""
                    ),
                    "category": category,
                    "attributes": (
                        info.get("attributes") or info.get("Key_Attributes") or ""
                    ),
                    "price": info.get("price", ""),
                    "stock": info.get("stock", ""),
                },
            )
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
                    try:
                        Path(path).unlink()
                    except OSError:
                        pass
            if ok:
                local_ds = pdf_downloader.get_pdf_relative_path(pid)
                _update_symbol_datasheet(pid, cfg["output_dir"], local_ds)
                c["ds_link"] = local_ds
            url = f"https://www.lcsc.com/product-detail/{pid}.html"
            self.step_done.emit(pid, "pdf", ok, {"url": url, "error": err or ""})
        except Exception as e:
            self._logfn(pid)(f"PDF error: {e}")
            url = f"https://www.lcsc.com/product-detail/{pid}.html"
            self.step_done.emit(pid, "pdf", False, {"url": url, "error": str(e)})
        finally:
            logging.getLogger().removeHandler(h)

    def _do_jlc(self, pid, c, cfg):
        self.step_started.emit(pid, "jlc")
        log = self._logfn(pid)
        try:
            log(f"Fetching LCSC data for {pid}…")
            info = _fetch_lcsc_data(pid)
            if not info:
                raise RuntimeError("No data returned from LCSC API")
            c["comp_info"] = {**(c.get("comp_info") or {}), **info}
            log(f"LCSC OK — stock={info.get('stock','?')}  price={info.get('price','?')}")
            self.step_done.emit(pid, "jlc", True, {
                "category":   info.get("category", ""),
                "mfr":        info.get("mfr", ""),
                "package":    info.get("package", ""),
                "attributes": info.get("attributes", ""),
                "price":      info.get("price", ""),
                "stock":      info.get("stock", ""),
            })
        except Exception as e:
            log(f"JLC fetch error: {e}")
            self.step_done.emit(pid, "jlc", False, {"error": str(e)})

    def _do_scrape(self, pid):
        self.log_line.emit(pid, f"Scraping LCSC data for {pid}…")
        data = _fetch_lcsc_data(pid)
        if data:
            self.log_line.emit(pid, f"Scrape OK — got: {', '.join(data.keys())}")
        else:
            self.log_line.emit(pid, "Scrape returned no data")
        self.scrape_done.emit(pid, data)

    def _logfn(self, pid):
        def _l(msg):
            self.log_line.emit(pid, msg)

        return _l


# ── Library helpers ───────────────────────────────────────────────────────────
def _check_existing(pid, output_dir):
    lib = Path(output_dir)
    out = {}
    sym_file = _find_sym_file(pid, lib / "symbol")
    fp_ref = ""
    if sym_file is not None:
        txt = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = f'(property "LCSC" "{pid}"'
        out["sym_ok"] = True
        idx = txt.find(marker)
        if idx >= 0:
            block = txt[max(0, idx - 3000) : idx + 200]
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


def _update_symbol_datasheet(pid: str, output_dir: str, new_ds: str) -> bool:
    """Patch the Datasheet property for pid in the symbol library file."""
    sym_file = _find_sym_file(pid, Path(output_dir) / "symbol")
    if sym_file is None:
        return False
    try:
        content = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = f'(property "LCSC" "{pid}"'

        def patch_block(m):
            block = m.group(0)
            if marker not in block:
                return block
            return re.sub(
                r'(\(property "Datasheet" ")[^"]*(")',
                lambda dm: dm.group(1) + new_ds + dm.group(2),
                block,
            )

        new_content = re.sub(
            r'\n([ \t]+)\(symbol "[^"]*".*?\n\1\)',
            patch_block,
            content,
            flags=re.DOTALL,
        )
        if new_content != content:
            sym_file.write_text(new_content, encoding="utf-8")
        return True
    except Exception as e:
        logging.warning(f"Failed to update symbol datasheet for {pid}: {e}")
        return False


def _fetch_lcsc_data(pid: str) -> dict:
    """Fetch product metadata and Key Attributes from the LCSC product API."""
    out = {}
    try:
        session = helper.get_lcsc_session()
        url = f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={pid}"
        headers = {
            **helper.LCSC_HEADERS,
            "Accept": "application/json, text/plain, */*",
            "Referer": f"https://www.lcsc.com/product-detail/{pid}.html",
        }
        r = session.get(url, headers=headers, timeout=20)
        if r.status_code != 200:
            raise RuntimeError(f"HTTP {r.status_code}")
        product = r.json().get("result") or {}
        if not product:
            raise RuntimeError("Empty result")

        for attr, candidates in (
            ("value", ["productModel"]),
            ("mfr", ["brandNameEn", "manufacturerName"]),
            ("category", ["wmCatalogNameEn", "catalogName", "parentCatalogName"]),
            ("package", ["encapStandard", "packageType"]),
            ("description", ["productIntroEn", "productDescEn"]),
        ):
            for key in candidates:
                val = (product.get(key) or "").strip()
                if val:
                    out[attr] = val
                    break

        # Stock level
        stock = product.get("stockNumber")
        if stock is not None:
            out["stock"] = str(stock)

        # Price: cheapest quantity tier in USD
        price_list = product.get("productPriceList") or []
        if price_list:
            usd = price_list[0].get("usdPrice")
            if usd is not None:
                out["price"] = f"${usd:.4f}"

        # Key Attributes: paramVOList entries marked isMain=True first, then the rest
        params = product.get("paramVOList") or []
        main = [p for p in params if p.get("isMain") is True]
        other = [p for p in params if p.get("isMain") is not True]
        parts = []
        for p in main + other:
            name = (p.get("paramNameEn") or "").strip()
            value = (p.get("paramValueEn") or "").strip()
            if name and value and value != "-":
                parts.append(f"{name}: {value}")
        if parts:
            out["attributes"] = "; ".join(parts)
    except Exception as e:
        logging.warning(f"LCSC fetch failed for {pid}: {e}")
    return out


def _update_symbol_property(
    pid: str, output_dir: str, prop_names, new_value: str
) -> bool:
    """Patch one or more KiCad symbol properties for pid (tries each name in order)."""
    sym_file = _find_sym_file(pid, Path(output_dir) / "symbol")
    if sym_file is None:
        return False
    if isinstance(prop_names, str):
        prop_names = [prop_names]
    try:
        content = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = f'(property "LCSC" "{pid}"'

        # Find all symbol starts.
        # We need to distinguish between top-level symbols and nested ones.
        # Top-level symbols usually have the minimum indentation level (e.g. 2 spaces or 1 tab).
        all_matches = list(re.finditer(r'\n([ \t]+)\(symbol "[^"]+"', content))
        if not all_matches:
            logging.warning(f"No symbol blocks found in {sym_file}")
            return False

        # Heuristic: top-level symbols are those with the minimum indentation found.
        min_indent_len = min(len(m.group(1)) for m in all_matches)
        matches = [m for m in all_matches if len(m.group(1)) == min_indent_len]

        new_chunks = []
        last_pos = 0
        found_block = False

        for i, m in enumerate(matches):
            start = m.start()
            # Add text between symbols (or before first symbol)
            new_chunks.append(content[last_pos:start])

            # Determine block end: start of next top-level symbol or last ')' of the library
            if i + 1 < len(matches):
                end = matches[i + 1].start()
            else:
                # The library footer starts with the final ')'. The last symbol ends before it.
                end = content.rfind(")")
                if end == -1:
                    end = len(content)

            block = content[start:end]
            indent = m.group(1)

            if marker in block and not found_block:
                found_block = True
                patched = block
                replaced = False
                for name in prop_names:
                    # Use count=1 to ensure we only update the first occurrence in this block
                    patched_new, count = re.subn(
                        r'(\(property "' + re.escape(name) + r'" ")[^"]*(")',
                        lambda dm: dm.group(1) + new_value + dm.group(2),
                        block,
                        count=1,
                    )
                    if count > 0:
                        patched = patched_new
                        replaced = True
                        break

                if not replaced:
                    # Property not found in block — insert before the closing paren of the symbol
                    # Use the same indentation style as existing properties if possible
                    inner_indent = "  "
                    if "\t" in indent:
                        inner_indent = "\t"
                    inner = indent + inner_indent

                    new_prop = (
                        f'\n{inner}(property "{prop_names[0]}" "{new_value}" (id 99) (at 0 0 0)\n'
                        f"{inner}{inner_indent}(effects (font (size 1.27 1.27)) hide)\n"
                        f"{inner})"
                    )

                    # Find the main symbol's closing parenthesis.
                    # It should be the last ')' in the block that is preceded by a newline and our symbol's indentation.
                    # We look from the end of the block backwards.
                    idx = patched.rfind(f"\n{indent})")

                    if idx == -1:
                        # Fallback: find the last occurrence of a closing paren at the end of a line
                        # that has roughly the right indentation.
                        m_ends = list(re.finditer(r"\n[ \t]*\)\s*$", patched))
                        if m_ends:
                            # Prefer the one with matching indentation if it exists, otherwise the last one.
                            matching = [
                                me for me in m_ends if me.group(0).strip() == ")"
                            ]
                            # Wait, me.group(0) is \n[ \t]*\)\s*$
                            # Let's just take the last match and hope for the best,
                            # or better: find the one with the smallest indentation.
                            idx = m_ends[-1].start()
                        else:
                            # Final fallback: just the last ')' in the block
                            idx = patched.rfind(")")

                    if idx >= 0:
                        patched = patched[:idx] + new_prop + patched[idx:]

                new_chunks.append(patched)
            else:
                new_chunks.append(block)

            last_pos = end

        # Add remaining content (likely the final ')' and trailing newlines)
        new_chunks.append(content[last_pos:])
        new_content = "".join(new_chunks)

        if not found_block:
            logging.warning(f"Symbol block for {pid} not found in {sym_file}")
            return False
        if new_content != content:
            sym_file.write_text(new_content, encoding="utf-8")
            logging.info(f"Updated {prop_names} for {pid} in {sym_file.name}")
        return True
    except Exception as e:
        logging.warning(f"Failed to update symbol property for {pid}: {e}")
        import traceback

        logging.debug(traceback.format_exc())
        return False


def _sym_lib_close(content: str) -> int:
    """Return position of the ) closing the kicad_symbol_lib node."""
    in_str = False
    esc = False
    depth = 0
    for i, c in enumerate(content):
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    return len(content)


def _parse_sym_file(sym_file: Path):
    content = sym_file.read_text(encoding="utf-8", errors="replace")

    # Only scan within the kicad_symbol_lib s-expression so we never pick up
    # symbols that were accidentally placed outside the library close.
    lib_end = _sym_lib_close(content)
    inner = content[:lib_end]

    # Find the minimum indentation of any (symbol declaration inside the library.
    # That minimum level corresponds to top-level (component) symbols.
    all_sym_re = re.compile(r'^([ \t]+)\(symbol "([^"]+)"', re.MULTILINE)
    all_matches = list(all_sym_re.finditer(inner))
    if not all_matches:
        return []
    min_indent = min(len(m.group(1)) for m in all_matches)
    top_matches = [m for m in all_matches if len(m.group(1)) == min_indent]

    prop_re = re.compile(
        r'^[ \t]{2,}\(property "([^"]+)"\s+"((?:[^"\\]|\\.)*?)"',
        re.MULTILINE,
    )
    results = []
    for i, m in enumerate(top_matches):
        start = m.start()
        end = top_matches[i + 1].start() if i + 1 < len(top_matches) else lib_end
        chunk = inner[start:end]
        props = {pm.group(1): pm.group(2) for pm in prop_re.finditer(chunk)}
        results.append(
            {
                "name": m.group(2),
                "lcsc": props.get("LCSC", ""),
                "footprint": props.get("Footprint", ""),
                "datasheet": props.get("Datasheet", ""),
                "value": props.get("Value", ""),
                "description": props.get("Description_1", props.get("Description", "")),
                "package": props.get("Package", ""),
                "mfr": props.get("Manufacturer", props.get("MFR", "")),
                "category": props.get("Category", ""),
                "attributes": props.get("Key_Attributes", ""),
                "price": props.get("Price", ""),
                "stock": props.get("Stock", ""),
            }
        )
    return results


def _update_sym_lib_table(output_dir: str) -> None:
    """Regenerate sym-lib-table from every .kicad_sym file in the symbol/ directory."""
    sym_dir = Path(output_dir) / "symbol"
    table_path = Path(output_dir) / "sym-lib-table"
    sym_files = sorted(sym_dir.glob("*.kicad_sym")) if sym_dir.exists() else []
    lines = ["(sym_lib_table", "\t(version 7)"]
    for sf in sym_files:
        name = sf.stem
        uri = str(sf.resolve()).replace("\\", "/")
        lines.append(f'\t(lib (name "{name}") (type "KiCad") (uri "{uri}") (options "") (descr ""))')
    lines.append(")")
    try:
        table_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        logging.info(f"sym-lib-table: {len(sym_files)} lib(s) → {table_path}")
    except OSError as e:
        logging.warning(f"Could not write sym-lib-table: {e}")


def _delete_from_lib(pid, output_dir):
    lib = Path(output_dir)
    errors = []
    fp_ref = ""

    sym_file = _find_sym_file(pid, lib / "symbol")
    if sym_file is not None:
        content = sym_file.read_text(encoding="utf-8", errors="replace")
        marker = f'(property "LCSC" "{pid}"'
        if marker in content:
            idx = content.find(marker)
            block = content[max(0, idx - 3000) : idx + 200]
            m = re.search(r'\(property "Footprint" "([^"]*)"', block)
            if m:
                fp_ref = m.group(1)
        # Remove the symbol block
        new = re.sub(
            r'\n([ \t]+)\(symbol "[^"]*".*?\n\1\)',
            lambda m: "" if f'(property "LCSC" "{pid}"' in m.group(0) else m.group(0),
            content,
            flags=re.DOTALL,
        )
        if new != content:
            try:
                sym_file.write_text(new, encoding="utf-8")
                # Remove the file if it now contains no symbols
                if not re.search(r'\(symbol "', new):
                    sym_file.unlink(missing_ok=True)
            except OSError as e:
                errors.append(f"Symbol file: {e}")

    if fp_ref and ":" in fp_ref:
        ln, fn = fp_ref.split(":", 1)
        for p in [
            lib / f"{ln}.pretty" / f"{fn}.kicad_mod",
            lib / ln / "packages3d" / f"{fn}.step",
        ]:
            if p.exists():
                try:
                    p.unlink()
                except OSError as e:
                    errors.append(str(e))

    pdf = lib / "pdf" / f"{pid}.pdf"
    if pdf.exists():
        try:
            pdf.unlink()
        except OSError as e:
            errors.append(str(e))

    return errors


# ── Settings dialog ───────────────────────────────────────────────────────────
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
        for d in (recent_dirs or []):
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


# ── Main window ───────────────────────────────────────────────────────────────
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
        self._meta_edits: dict[str, QLineEdit] = {}
        for label, attr, editable in METADATA_FIELDS:
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
            self._meta_edits[attr] = edit
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

    # ── Settings ──────────────────────────────────────────────────────────────
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
        )
        if dlg.exec() == QDialog.Accepted:
            self._dl_step = dlg.dl_step
            self._dl_pdf = dlg.dl_pdf
            self._col_visible = dlg.col_visible
            self._lib_prefix = dlg.lib_prefix
            self._jlcpcb_api_key = dlg.jlcpcb_api_key
            self._output_dir = dlg.output_dir
            self._recent_dirs = dlg.recent_dirs
            for col, visible in enumerate(self._col_visible):
                self._tbl.setColumnHidden(col, not visible)
            self._update_path_label()
            self._save_cache()

    # ── Sorting ───────────────────────────────────────────────────────────────
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

    # ── Cache ─────────────────────────────────────────────────────────────────
    def _load_cache(self):
        if not CACHE_FILE.exists():
            return
        try:
            d = json.loads(CACHE_FILE.read_text())
            self._recent_dirs = d.get("recent_dirs", [])
            if self._recent_dirs:
                self._output_dir = self._recent_dirs[0]
                self._update_path_label()
            self._dl_step = d.get("dl_step", True)
            self._dl_pdf = d.get("dl_pdf", False)
            self._lib_prefix = d.get("lib_prefix", "")
            self._jlcpcb_api_key = d.get("jlcpcb_api_key", "")
            saved_vis = list(d.get("col_visible", []))
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
                        "recent_dirs": self._recent_dirs,
                        "dl_step": self._dl_step,
                        "dl_pdf": self._dl_pdf,
                        "col_visible": self._col_visible,
                        "lib_prefix": self._lib_prefix,
                        "jlcpcb_api_key": self._jlcpcb_api_key,
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

        # Populate text info cells after steps complete
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
            # Write updated fields back to the symbol file
            cfg = self._cfg()
            if cfg["output_dir"]:
                for attr, prop in (
                    ("category",   "Category"),
                    ("mfr",        "Manufacturer"),
                    ("package",    "Package"),
                    ("attributes", "Key_Attributes"),
                    ("price",      "Price"),
                    ("stock",      "Stock"),
                ):
                    val = getattr(s, attr, "")
                    if val:
                        _update_symbol_property(pid, cfg["output_dir"], prop, val)

        self._refresh_detail(pid)

        # After successful validate, mark already-present items
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
        for edit in self._meta_edits.values():
            edit.setText("")
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
            self._meta_edits[attr].setText(getattr(s, attr, ""))

    # ── Detail panel actions ──────────────────────────────────────────────────
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

    def _on_meta_edit(self, attr, edit: QLineEdit):
        pid = self._selected_pid()
        if not pid:
            return
        s = self._parts.get(pid)
        if not s:
            return
        val = edit.text()
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

    # ── Cell click (retry / delete) ───────────────────────────────────────────
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
        # JLC can always be re-triggered (force refresh); others only when FAILED/PENDING
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

    # ── Delete ────────────────────────────────────────────────────────────────
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

    # ── Load Library ──────────────────────────────────────────────────────────
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
                    rel = re.sub(r"^\$\{[^}]+\}/", "", raw_ds)
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
