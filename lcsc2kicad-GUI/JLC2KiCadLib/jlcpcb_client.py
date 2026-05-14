"""JLCPCB Components API client."""
import logging
import time
from typing import Any, Dict, List, Optional

import requests

logger = logging.getLogger(__name__)


class JLCPCBAPIError(Exception):
    pass


class JLCPCBAPIClient:
    BASE_URL = "https://api.jlcpcb.com"
    COMPONENTS_URL = f"{BASE_URL}/components/v1"
    REQUEST_DELAY = 1.0

    def __init__(self, api_key: Optional[str] = None):
        self.api_key = api_key
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "KiCad-LCSC-Manager/1.0",
            "Accept": "application/json",
        })
        if self.api_key:
            self.session.headers["Authorization"] = f"Bearer {self.api_key}"
        self.last_request_time = 0.0

    def _rate_limit(self):
        elapsed = time.time() - self.last_request_time
        if elapsed < self.REQUEST_DELAY:
            time.sleep(self.REQUEST_DELAY - elapsed)
        self.last_request_time = time.time()

    def _make_request(
        self,
        method: str,
        endpoint: str,
        params: Optional[Dict] = None,
        json_data: Optional[Dict] = None,
        timeout: int = 30,
    ) -> Dict:
        self._rate_limit()
        url = f"{self.COMPONENTS_URL}/{endpoint.lstrip('/')}"
        try:
            logger.debug(f"{method} {url} params={params}")
            response = self.session.request(
                method=method, url=url, params=params, json=json_data, timeout=timeout
            )
            response.raise_for_status()
            return response.json()
        except requests.exceptions.HTTPError as e:
            if e.response is not None and e.response.status_code == 401:
                raise JLCPCBAPIError("Authentication failed — check API key.")
            if e.response is not None and e.response.status_code == 429:
                raise JLCPCBAPIError("Rate limit exceeded.")
            raise JLCPCBAPIError(f"API request failed: {e}")
        except requests.exceptions.RequestException as e:
            raise JLCPCBAPIError(f"Network error: {e}")
        except ValueError as e:
            raise JLCPCBAPIError(f"Invalid API response: {e}")

    def get_component(self, component_code: str) -> Optional[Dict[str, Any]]:
        """Get detailed component information including category."""
        try:
            response = self._make_request("GET", f"component/{component_code}")
            if response.get("success"):
                return response.get("data")
            return None
        except JLCPCBAPIError:
            raise
        except Exception as e:
            raise JLCPCBAPIError(f"Failed to fetch component: {e}")

    def get_pricing(self, component_code: str) -> List[Dict[str, Any]]:
        """Get component pricing tiers (lowest quantity first)."""
        try:
            response = self._make_request("GET", f"component/{component_code}/pricing")
            if response.get("success"):
                return response.get("data", [])
            return []
        except JLCPCBAPIError:
            raise
        except Exception as e:
            raise JLCPCBAPIError(f"Failed to fetch pricing: {e}")

    def get_inventory(self, component_code: str) -> Optional[int]:
        """Get current stock level."""
        try:
            response = self._make_request("GET", f"component/{component_code}/inventory")
            if response.get("success"):
                return (response.get("data") or {}).get("stock")
            return None
        except JLCPCBAPIError:
            raise
        except Exception as e:
            raise JLCPCBAPIError(f"Failed to fetch inventory: {e}")

    def get_categories(self) -> List[Dict[str, Any]]:
        """Get full category tree."""
        try:
            response = self._make_request("GET", "categories")
            if response.get("success"):
                return response.get("data", [])
            return []
        except JLCPCBAPIError:
            raise
        except Exception as e:
            raise JLCPCBAPIError(f"Failed to fetch categories: {e}")

    def search_components(
        self,
        keyword: str,
        category: Optional[str] = None,
        in_stock: bool = True,
        page: int = 1,
        page_size: int = 20,
    ) -> Dict[str, Any]:
        """Search components by keyword."""
        params: Dict[str, Any] = {
            "keyword": keyword,
            "page": page,
            "pageSize": page_size,
        }
        if category:
            params["category"] = category
        if in_stock:
            params["inStock"] = "true"
        return self._make_request("GET", "search", params=params)


_jlcpcb_client: Optional[JLCPCBAPIClient] = None


def get_jlcpcb_client(api_key: Optional[str] = None) -> JLCPCBAPIClient:
    """Get or create the global JLCPCB API client."""
    global _jlcpcb_client
    if _jlcpcb_client is None or (api_key and _jlcpcb_client.api_key != api_key):
        _jlcpcb_client = JLCPCBAPIClient(api_key=api_key)
    return _jlcpcb_client
