from dataclasses import dataclass, field
from enum import Enum

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
    CB_VALID,
    CB_SYM,
    CB_FP,
    CB_STEP,
    CB_PDF,
    CB_JLC,
    CB_PRICE,
    CB_SUBTOTAL,
    CB_LINKED,
) = range(15)

BOM_COL_NAMES = [
    "Designator(s)",
    "LCSC Part #",
    "Value",
    "Description",
    "Qty",
    "Stock",
    "V",
    "S",
    "F",
    "3D",
    "P",
    "J",
    "Price",
    "Sub Total",
    "Linked",
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

BOM_STEP_COLS = {
    "valid": CB_VALID,
    "symbol": CB_SYM,
    "footprint": CB_FP,
    "step": CB_STEP,
    "pdf": CB_PDF,
    "jlc": CB_JLC,
}

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

ATTR_COL = {
    "value": C_VALUE,
    "description": C_DESC,
    "mfr": C_MFR,
    "category": C_CAT,
    "fp_name_text": C_FP_TEXT,
    "package": C_PKG,
    "attributes": C_ATTRS,
}

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


def group_bom_parts(bom_data: list) -> list[dict]:
    groups: dict = {}
    order = []
    for p in bom_data:
        key = (p["lcsc"], p["val"]) if p["lcsc"] else (None, p["val"], p["fp"])
        if key not in groups:
            groups[key] = {
                "refs": [],
                "val": p["val"],
                "lcsc": p["lcsc"],
                "fp": p["fp"],
            }
            order.append(key)
        groups[key]["refs"].append(p["ref"])
    return [groups[k] for k in order]


def is_pcb_linked(pid: str, refs: list, fp_name_text: str, pcb_refs: dict):
    """True if all refs use our lib:footprint, False if not, None if unknown."""
    if not pid or not refs or not fp_name_text or ":" not in fp_name_text:
        return None
    our_lib, our_fp_name = fp_name_text.split(":", 1)
    if not our_fp_name:
        return None
    for ref in refs:
        pcb_info = pcb_refs.get(ref, {})
        if pcb_info.get("lib") != our_lib or pcb_info.get("fp") != our_fp_name:
            return False
    return True


def row_subtotal(qty: int, price_str: str) -> str | None:
    import re

    match = re.search(r"(\d+\.?\d*)", price_str or "")
    if not match:
        return None
    try:
        return f"${qty * float(match.group(1)):.2f}"
    except ValueError:
        return None


def apply_step_result(state: PartState, step: str, ok: bool, extra: dict) -> St:
    """Update PartState from a worker step_done payload. Returns new status."""
    extra = extra or {}
    if step == "pdf":
        url = extra.get("url", "")
        if extra.get("skipped"):
            ns = St.SKIPPED
        elif ok:
            ns = St.SUCCESS
            url = ""
        else:
            ns = St.FAILED
        state.put("pdf", ns)
        state.pdf_url = url
        return ns
    ns = St.SUCCESS if ok else St.FAILED
    state.put(step, ns)
    if step == "footprint" and ok and extra.get("fp_name"):
        state.fp_name_text = extra["fp_name"]
    if step in ("symbol", "jlc") and ok:
        for attr in (
            "value",
            "description",
            "package",
            "mfr",
            "category",
            "attributes",
            "price",
            "stock",
        ):
            val = extra.get(attr, "")
            if val:
                setattr(state, attr, val)
    if step == "valid" and ok:
        key_map = {
            "symbol": "sym_ok",
            "footprint": "fp_ok",
            "step": "step_ok",
            "pdf": "pdf_ok",
        }
        for sub, key in key_map.items():
            if extra.get(key):
                state.put(sub, St.SUCCESS)
    return ns
