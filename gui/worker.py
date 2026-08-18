import json
import logging
import re
import traceback
from pathlib import Path
from queue import Queue

# Backend imports
from easyeda2kicad.easyeda.easyeda_api import EasyedaApi
from easyeda2kicad.easyeda.easyeda_importer import (
    Easyeda3dModelImporter,
    EasyedaFootprintImporter,
    EasyedaSymbolImporter,
)
from easyeda2kicad.kicad.export_kicad_3d_model import Exporter3dModelKicad
from easyeda2kicad.kicad.export_kicad_footprint import ExporterFootprintKicad
from easyeda2kicad.kicad.export_kicad_symbol import ExporterSymbolKicad
from lib.api import fetch_component_data
from lib.categories import resolve_category
from lib.helpers import (
    PDF_MIN_BYTES,
    _check_existing,
    _update_symbol_datasheet,
    sym_lib_targets,
    upsert_symbol,
)
from lib.kicad_escape import escape_kicad_string
from lib.jlc import helper, pdf_downloader
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


class Worker:
    """Background import worker. Callbacks marshalled to the GUI thread."""

    def __init__(self, marshal=None):
        self.marshal = marshal
        self.on_step_started = None  # type: ignore[assignment]
        self.on_step_done = None  # type: ignore[assignment]
        self.on_log_line = None  # type: ignore[assignment]
        self.on_scrape_done = None  # type: ignore[assignment]
        self._q: Queue = Queue()
        self._cache: dict[str, dict] = {}
        self._thread = None

    def _emit(self, cb, *args):
        if cb is None:
            return
        if self.marshal:
            self.marshal(cb, *args)
        else:
            cb(*args)

    def start(self):
        import threading
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run, daemon=True)
        self._thread.start()

    def wait(self, timeout_ms=2000):
        if self._thread:
            self._thread.join((timeout_ms or 0) / 1000)


    def process(self, pid, cfg, overwrite=False):
        self._q.put(("new", pid, cfg, overwrite))

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
                    self._full(task[1], task[2], task[3])
                elif task[0] == "retry":
                    self._do_retry(task[1], task[2], task[3])
                elif task[0] == "scrape":
                    self._do_scrape(task[1])
            except Exception:
                logging.error(f"Worker task failed: {traceback.format_exc()}")

    # ── Full sequence ─────────────────────────────────────────────────────────
    def _full(self, pid, cfg, overwrite=False):
        c = self._cache.setdefault(pid, {})
        log = self._logfn(pid)
        ok, extra = self._do_validate(pid, cfg)
        if not ok:
            log(f"Aborting: Validation failed for {pid}")
            return
        c.update(extra)

        # Always fetch fresh JLC data
        self._do_jlc(pid, c, cfg)

        # Check if we should skip based on library status
        if not overwrite and c.get("fp_ok"):
            log(
                f"Footprint for {pid} already in library, skipping (use Replace to force)"
            )
        else:
            self._do_footprint(pid, c, cfg)

        if not overwrite and c.get("sym_ok"):
            log(f"Symbol for {pid} already in library, skipping (use Replace to force)")
        else:
            self._do_symbol(pid, c, cfg)

        if cfg["dl_pdf"] and (overwrite or not c.get("pdf_ok")):
            self._do_pdf(pid, c, cfg)
        elif not cfg["dl_pdf"]:
            url = f"https://www.lcsc.com/product-detail/{pid}.html"
            self._emit(self.on_step_done, pid, "pdf", True, {"skipped": True, "url": url})

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
        self._emit(self.on_step_started, pid, "valid")
        log = self._logfn(pid)
        try:
            api = EasyedaApi()
            log(f"Fetching EasyEDA CAD data for {pid}...")
            cad_data = api.get_cad_data_of_component(pid)
            if not cad_data:
                raise RuntimeError("Part not found on EasyEDA")

            # Store cad_data in the internal cache for later steps
            c = self._cache.get(pid, {})
            c["cad_data"] = cad_data

            # For compatibility with legacy logic and GUI:
            # We still fetch the /svgs endpoint to get unit counts/UUIDs if needed,
            # though easyeda2kicad doesn't strictly need them as it uses cad_data.
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
            self._emit(self.on_step_done, pid, "valid", True, extra)
            return True, extra
        except Exception as e:
            log(f"FAIL: {e}")
            self._emit(self.on_step_done, pid, "valid", False, {"error": str(e)})
            return False, {}

    def _do_footprint(self, pid, c, cfg):
        fp_uuid = c.get("fp_uuid")
        if not fp_uuid:
            self._emit(self.on_step_done, 
                pid, "footprint", False, {"error": "No UUID — re-run Valid"}
            )
            return
        inc = cfg["dl_step"]
        self._emit(self.on_step_started, pid, "footprint")
        if inc:
            self._emit(self.on_step_started, pid, "step")

        log = self._logfn(pid)
        try:
            api = EasyedaApi()
            cad_data = c.get("cad_data")
            if not cad_data:
                log(f"Fetching EasyEDA CAD data for {pid}...")
                cad_data = api.get_cad_data_of_component(pid)
                if not cad_data:
                    raise RuntimeError("Failed to fetch CAD data from EasyEDA")
                c["cad_data"] = cad_data

            log(f"Importing footprint for {pid}...")
            fp_importer = EasyedaFootprintImporter(cad_data)
            ee_footprint = fp_importer.get_footprint()

            # Create exporter
            exporter = ExporterFootprintKicad(ee_footprint)

            # Prepare paths
            pretty_dir = Path(cfg["output_dir"]) / "footprint.pretty"
            pretty_dir.mkdir(parents=True, exist_ok=True)

            fp_name = ee_footprint.info.name
            mod_path = pretty_dir / f"{fp_name}.kicad_mod"

            # 3D model path. Must be an absolute / env-var path so the model
            # resolves from ANY project that uses this library. A relative path
            # (e.g. "footprint/packages3d") is resolved by KiCad against the
            # *project* directory, not the library, so it never loads. We use
            # ${KICAD_USER_LIBRARY_DIR} (the KiCad env var pointing at this
            # library) and .step so board STEP/fab export picks the model up.
            model_dir_name = "packages3d"
            model_rel_path = f"${{KICAD_USER_LIBRARY_DIR}}/footprint/{model_dir_name}"

            exporter.export(
                footprint_full_path=str(mod_path),
                model_3d_path=model_rel_path,
                model_3d_extension="step",
            )

            c["fp_name"] = f"footprint:{fp_name}"

            self._emit(self.on_step_done, pid, "footprint", True, {"fp_name": c["fp_name"]})

            if inc:
                log(f"Exporting 3D model for {pid}...")
                model_3d = ee_footprint.model_3d
                if model_3d:
                    # We need to fetch the raw data for exporter
                    model_3d.raw_obj = api.get_raw_3d_model_obj(model_3d.uuid)
                    model_3d.step = api.get_step_3d_model(model_3d.uuid)

                    exporter_3d = Exporter3dModelKicad(model_3d)
                    model_abs_dir = (
                        Path(cfg["output_dir"]) / "footprint" / model_dir_name
                    )
                    exporter_3d.export(str(model_abs_dir), overwrite=True)

                    step_ok = (model_abs_dir / f"{model_3d.name}.step").exists()
                    self._emit(self.on_step_done, pid, "step", step_ok, {})
                else:
                    log("No 3D model found for this part.")
                    self._emit(self.on_step_done, pid, "step", False, {"error": "No 3D model"})

        except Exception as e:
            log(f"Footprint error: {e}")
            self._emit(self.on_step_done, pid, "footprint", False, {"error": str(e)})
            if inc:
                self._emit(self.on_step_done, pid, "step", False, {})

    def _do_symbol(self, pid, c, cfg):
        self._emit(self.on_step_started, pid, "symbol")
        log = self._logfn(pid)
        try:
            api = EasyedaApi()
            cad_data = c.get("cad_data")
            if not cad_data:
                log(f"Fetching EasyEDA CAD data for {pid}...")
                cad_data = api.get_cad_data_of_component(pid)
                if not cad_data:
                    raise RuntimeError("Failed to fetch CAD data from EasyEDA")
                c["cad_data"] = cad_data

            log(f"Importing symbol for {pid}...")
            sym_importer = EasyedaSymbolImporter(cad_data)
            ee_symbol = sym_importer.get_symbol()

            # Prepare metadata
            info = c.get("comp_info") or fetch_component_data(
                pid, cfg.get("jlcpcb_api_key")
            )
            c["comp_info"] = info

            # Category and Library
            raw_category = info.get("category") or info.get("Category") or ""
            category = resolve_category(raw_category)
            # Which symbol library file(s) this part is written to depends on the
            # chosen organisation mode (organised / consolidated / both).
            lib_mode = cfg.get("lib_mode", "organised")
            lib_names = sym_lib_targets(
                raw_category, cfg.get("lib_prefix", ""), lib_mode
            )

            # Custom fields for KiCad symbol
            # Note: ExporterSymbolKicad from easyeda2kicad already adds:
            # Reference, Value, Footprint, Datasheet, Manufacturer, MPN, LCSC Part, ki_keywords, Description.
            # We'll update ee_symbol.info directly for these built-in fields.

            # Use LCSC data if available, fallback to EasyEDA CAD data
            mfr = escape_kicad_string(
                info.get("mfr")
                or info.get("manufacturer")
                or ee_symbol.info.manufacturer
            )
            desc = escape_kicad_string(
                info.get("description") or ee_symbol.info.description
            )

            ee_symbol.info.manufacturer = mfr
            ee_symbol.info.package = escape_kicad_string(
                info.get("package") or info.get("Package") or ee_symbol.info.package
            )
            ee_symbol.info.description = desc
            ee_symbol.info.lcsc_id = pid

            # Value
            val = escape_kicad_string(info.get("value") or info.get("Value") or pid)
            ee_symbol.info.name = val

            # Datasheet - use local PDF if available
            ds = escape_kicad_string(
                c.get("ds_link") or c.get("lcsc_url") or ee_symbol.info.datasheet or ""
            )
            ee_symbol.info.datasheet = ds

            # Additional custom fields
            custom_fields = {
                "LCSC": pid,
                "Category": category,
                "Description": desc,
                "Manufacturer": mfr,
            }

            if info.get("attributes"):
                custom_fields["Key_Attributes"] = escape_kicad_string(
                    info.get("attributes")
                )
            if info.get("stock"):
                custom_fields["Stock"] = escape_kicad_string(info.get("stock"))
            if info.get("price"):
                custom_fields["Price"] = escape_kicad_string(info.get("price"))

            # Add individual specifications as properties
            for spec_name, spec_value in info.get("specifications", {}).items():
                # Clean up property name for KiCad
                clean_name = re.sub(r"[^\w\s-]", "", spec_name).strip()
                if (
                    clean_name
                    and len(clean_name) <= 50
                    and clean_name not in custom_fields
                ):
                    # Avoid overwriting built-in fields
                    if clean_name not in (
                        "Reference",
                        "Value",
                        "Footprint",
                        "Datasheet",
                        "Manufacturer",
                        "MPN",
                        "LCSC Part",
                        "Description",
                    ):
                        custom_fields[clean_name] = escape_kicad_string(spec_value)

            # Footprint reference (shared across all symbol libraries)
            fp = (c.get("fp_name") or "").replace(".pretty", "")
            if ":" in fp:
                fp_lib, fp_bare = fp.split(":", 1)
            else:
                fp_lib, fp_bare = "footprint", fp

            sym_dir = Path(cfg["output_dir"]) / "symbol"
            sym_dir.mkdir(parents=True, exist_ok=True)

            # Generate the symbol block once, then write it into each target
            # library with our own paren-matched upsert. We deliberately avoid
            # ExporterSymbolKicad.save_to_lib: its regex-based replace eats a
            # newline on every overwrite, eventually jamming symbols together and
            # duplicating sub-units (corrupting multi-download libraries).
            exporter = ExporterSymbolKicad(
                ee_symbol,
                lib_path=str(sym_dir / f"{lib_names[0]}.kicad_sym"),
                custom_fields=custom_fields,
            )
            exporter.output.info.package = fp_bare
            sym_content = exporter.export(footprint_lib_name=fp_lib)

            log(f"Saving symbol to {', '.join(n + '.kicad_sym' for n in lib_names)}...")
            for lib_name in lib_names:
                lib_path = sym_dir / f"{lib_name}.kicad_sym"
                try:
                    upsert_symbol(str(lib_path), sym_content)
                except Exception as we:
                    log(f"Warning: could not write symbol to {lib_path.name}: {we}")

            # Extract attributes for the GUI
            self._emit(self.on_step_done, 
                pid,
                "symbol",
                True,
                {
                    "value": val,
                    "description": (
                        info.get("description") or ee_symbol.info.description
                    ),
                    "package": ee_symbol.info.package,
                    "mfr": ee_symbol.info.manufacturer,
                    "category": category,
                    "attributes": info.get("attributes", ""),
                    "price": info.get("price", ""),
                    "stock": info.get("stock", ""),
                },
            )
        except Exception as e:
            import traceback

            err_msg = f"Symbol error: {e}\n{traceback.format_exc()}"
            log(err_msg)
            self._emit(self.on_step_done, pid, "symbol", False, {"error": str(e)})

    def _do_pdf(self, pid, c, cfg):
        h = _Cap(self._logfn(pid))
        self._emit(self.on_step_started, pid, "pdf")
        logging.getLogger().addHandler(h)
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
            self._emit(self.on_step_done, pid, "pdf", ok, {"url": url, "error": err or ""})
        except Exception as e:
            self._logfn(pid)(f"PDF error: {e}")
            url = f"https://www.lcsc.com/product-detail/{pid}.html"
            self._emit(self.on_step_done, pid, "pdf", False, {"url": url, "error": str(e)})
        finally:
            logging.getLogger().removeHandler(h)

    def _do_jlc(self, pid, c, cfg):
        self._emit(self.on_step_started, pid, "jlc")
        log = self._logfn(pid)
        try:
            log(f"Fetching LCSC data for {pid}…")
            info = fetch_component_data(pid, cfg.get("jlcpcb_api_key"))
            if not info:
                log("Warning: No metadata found on LCSC/JLCPCB (using minimal data)")
                info = {}
            c["comp_info"] = {**(c.get("comp_info") or {}), **info}
            if info:
                log(
                    f"API OK — stock={info.get('stock', '?')}  price={info.get('price', '?')}"
                )

            cat = resolve_category(info.get("category", ""))
            self._emit(self.on_step_done, 
                pid,
                "jlc",
                True,
                {
                    "category": cat,
                    "mfr": (info.get("mfr") or info.get("manufacturer") or ""),
                    "package": (info.get("package") or info.get("Package") or ""),
                    "description": (info.get("description") or ""),
                    "attributes": info.get("attributes", ""),
                    "price": info.get("price", ""),
                    "stock": info.get("stock", ""),
                },
            )
        except Exception as e:
            log(f"JLC fetch warning: {e}")
            # Still mark as "done" (but maybe not success?)
            # Actually, let's keep it as SUCCESS if we want to continue,
            # or FAILED if we want to show a red cross.
            # Given it's a fallback, let's show SUCCESS but log the warning.
            self._emit(self.on_step_done, pid, "jlc", True, {"error": str(e)})

    def _do_scrape(self, pid):
        self._emit(self.on_log_line, pid, f"Scraping LCSC data for {pid}…")
        data = fetch_component_data(pid)
        if data:
            self._emit(self.on_log_line, pid, f"Scrape OK — got: {', '.join(data.keys())}")
        else:
            self._emit(self.on_log_line, pid, "Scrape returned no data")
        self._emit(self.on_scrape_done, pid, data)

    def _logfn(self, pid):
        def _l(msg):
            self._emit(self.on_log_line, pid, msg)

        return _l
