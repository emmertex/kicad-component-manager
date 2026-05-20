import logging
import traceback
from pathlib import Path
from queue import Queue

from PySide6.QtCore import QThread, Signal

from gui.models import St
from lib.api import fetch_component_data
from lib.helpers import (
    PDF_MIN_BYTES,
    _check_existing,
    _lib_name,
    _update_symbol_datasheet,
)

# Backend imports
from lib.jlc import component_info as _cinfo
from lib.jlc import helper, pdf_downloader
from lib.jlc.footprint.footprint import create_footprint
from lib.jlc.symbol.symbol import create_symbol


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
            try:
                if task[0] == "new":
                    self._full(task[1], task[2])
                elif task[0] == "retry":
                    self._do_retry(task[1], task[2], task[3])
                elif task[0] == "scrape":
                    self._do_scrape(task[1])
            except Exception:
                logging.error(f"Worker task failed: {traceback.format_exc()}")

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
            info = c.get("comp_info") or fetch_component_data(
                pid, cfg.get("jlcpcb_api_key")
            )

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
            err_msg = f"Symbol error: {e}\n{traceback.format_exc()}"
            self._logfn(pid)(err_msg)
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
            info = fetch_component_data(pid, cfg.get("jlcpcb_api_key"))
            if not info:
                raise RuntimeError("No data returned from API sources")
            c["comp_info"] = {**(c.get("comp_info") or {}), **info}
            log(
                f"API OK — stock={info.get('stock', '?')}  price={info.get('price', '?')}"
            )
            self.step_done.emit(
                pid,
                "jlc",
                True,
                {
                    "category": info.get("category", ""),
                    "mfr": info.get("mfr", ""),
                    "package": info.get("package", ""),
                    "attributes": info.get("attributes", ""),
                    "price": info.get("price", ""),
                    "stock": info.get("stock", ""),
                },
            )
        except Exception as e:
            log(f"JLC fetch error: {e}")
            self.step_done.emit(pid, "jlc", False, {"error": str(e)})

    def _do_scrape(self, pid):
        self.log_line.emit(pid, f"Scraping LCSC data for {pid}…")
        data = fetch_component_data(pid)
        if data:
            self.log_line.emit(pid, f"Scrape OK — got: {', '.join(data.keys())}")
        else:
            self.log_line.emit(pid, "Scrape returned no data")
        self.scrape_done.emit(pid, data)

    def _logfn(self, pid):
        def _l(msg):
            self.log_line.emit(pid, msg)

        return _l
