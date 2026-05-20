import logging

# Try to find JLC2KiCadLib
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests
from bs4 import BeautifulSoup

_LIB = Path(__file__).parent.parent / "lcsc2kicad-GUI" / "JLC2KiCadLib"
if _LIB.exists() and str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

try:
    import helper
    from jlcpcb_client import JLCPCBAPIClient, JLCPCBAPIError
except ImportError:
    # Fallback if jlcpcb_client not in path (though we added it above)
    class JLCPCBAPIError(Exception):
        pass

    class JLCPCBAPIClient:
        def __init__(self, api_key=None):
            self.api_key = api_key

        def get_component(self, pid):
            return None

        def get_inventory(self, pid):
            return None

        def get_pricing(self, pid):
            return []


logger = logging.getLogger(__name__)


class APIError(Exception):
    """Base class for API errors."""

    pass


class JLCPCBAPIWrapper:
    def __init__(self, api_key: Optional[str] = None):
        self.client = JLCPCBAPIClient(api_key=api_key)

    def get_component_data(self, pid: str) -> Optional[Dict[str, Any]]:
        """Get component data from JLCPCB API using the specialized client."""
        try:
            data = self.client.get_component(pid)
            if not data:
                return None

            # Map JLCPCB fields to our common format
            out = {
                "value": data.get("model", ""),
                "mfr": data.get("brand", ""),
                "category": data.get("categoryName") or data.get("category") or "",
                "package": data.get("package", ""),
                "description": data.get("description", ""),
                "attributes": "; ".join(
                    [f"{p['name']}: {p['value']}" for p in data.get("parameters", [])]
                ),
            }

            # Use dedicated stock/pricing methods for accuracy as requested
            stock = self.client.get_inventory(pid)
            if stock is not None:
                out["stock"] = str(stock)
            else:
                out["stock"] = str(data.get("stock", "0"))

            pricing = self.client.get_pricing(pid)
            if pricing:
                # Use the lowest-tier price (usually the first one)
                price_val = pricing[0].get("price")
                if price_val is not None:
                    out["price"] = f"${float(price_val):.4f}"

            return out
        except (JLCPCBAPIError, Exception) as e:
            logger.debug(f"JLCPCB API fetch failed for {pid}: {e}")
            return None


class LCSCAPIClient:
    def __init__(self):
        # We'll use the helper sessions which handle cookies
        pass

    def get_component_data(self, pid: str) -> Optional[Dict[str, Any]]:
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
                return None

            product = r.json().get("result") or {}
            if not product:
                return None

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

            return out
        except Exception as e:
            logger.warning(f"LCSC API fetch failed for {pid}: {e}")
            return None


class LCSCScraper:
    def get_component_data(self, pid: str) -> Optional[Dict[str, Any]]:
        """Extract component information from LCSC product page using BS4."""
        try:
            product_url = f"https://www.lcsc.com/product-detail/{pid}.html"
            session = helper.get_lcsc_session()
            response = session.get(product_url, headers=helper.LCSC_HEADERS, timeout=30)

            if response.status_code != 200:
                return None

            soup = BeautifulSoup(response.content, "html.parser")
            out = {
                "description": "",
                "category": "",
                "mfr": "",
                "package": "",
                "attributes": "",
            }

            # Description
            desc_elem = soup.find("td", string="Description")
            if desc_elem and desc_elem.find_next_sibling("td"):
                desc_cell = desc_elem.find_next_sibling("td")
                description_elem = desc_cell.find("span", class_="major2--text")
                if description_elem:
                    out["description"] = description_elem.get_text(strip=True)

            # Package
            package_elem = soup.find("td", id="package_id")
            if package_elem and package_elem.find_next_sibling("td"):
                out["package"] = package_elem.find_next_sibling("td").get_text(
                    strip=True
                )

            # Category
            category_elem = soup.find("td", id="category_id")
            if category_elem and category_elem.find_next_sibling("td"):
                category_cell = category_elem.find_next_sibling("td")
                category_link = category_cell.find("a")
                if category_link and category_link.get("title"):
                    out["category"] = category_link.get("title").strip()
                elif category_link:
                    out["category"] = category_link.get_text(strip=True)

            # Manufacturer
            manufacturer_elem = soup.find("td", id="manufacturer_id")
            if manufacturer_elem and manufacturer_elem.find_next_sibling("td"):
                out["mfr"] = manufacturer_elem.find_next_sibling("td").get_text(
                    strip=True
                )

            return out
        except Exception as e:
            logger.error(f"LCSC scraper failed for {pid}: {e}")
            return None


def fetch_component_data(pid: str, api_key: Optional[str] = None) -> Dict[str, Any]:
    """Unified function to fetch component data from all available sources."""
    # 1. Try JLCPCB API if key provided
    if api_key:
        client = JLCPCBAPIWrapper(api_key)
        data = client.get_component_data(pid)
        if data:
            logger.info(f"Fetched {pid} data from JLCPCB API")
            return data

    # 2. Try LCSC JSON API (best free source)
    lcsc_api = LCSCAPIClient()
    data = lcsc_api.get_component_data(pid)
    if data:
        logger.info(f"Fetched {pid} data from LCSC JSON API")
        return data

    # 3. Fallback to HTML Scraper
    scraper = LCSCScraper()
    data = scraper.get_component_data(pid)
    if data:
        logger.info(f"Fetched {pid} data from LCSC HTML Scraper")
        return data

    return {}
