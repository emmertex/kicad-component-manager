from dataclasses import dataclass, field
from enum import Enum

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QTableWidgetItem

# ── Column Indices ───────────────────────────────────────────────────────────
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

# ── BOM Column Indices ───────────────────────────────────────────────────────
(
    CB_REFS,
    CB_PART,
    CB_VALUE,
    CB_DESC,
    CB_QTY,
    CB_STOCK,
    CB_STEP,
    CB_PRICE,
    CB_SUBTOTAL,
) = range(9)

BOM_COL_NAMES = [
    "Designator(s)",
    "LCSC Part #",
    "Value",
    "Description",
    "Qty",
    "Stock",
    "STEP",
    "Price",
    "Sub Total",
]

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


class _SortItem(QTableWidgetItem):
    """QTableWidgetItem that sorts by Qt.UserRole when set, else display text."""

    def __lt__(self, other):
        lv = self.data(Qt.UserRole)
        rv = other.data(Qt.UserRole)
        if lv is not None and rv is not None:
            return lv < rv
        return super().__lt__(other)


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
