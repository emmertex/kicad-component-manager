import logging
import sys

import requests


def mil2mm(data):
    return float(data) / 3.937


# Default headers for EasyEDA API requests
EASYEDA_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Origin": "https://easyeda.com",
    "Referer": "https://easyeda.com/",
}

# Default headers for LCSC requests
# Note: "br" (Brotli) is intentionally excluded because `requests` doesn't
# auto-decompress Brotli. Without this, the server returns brotli-compressed
# content that appears as binary garbage instead of the actual PDF.
LCSC_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Connection": "keep-alive",
    "Upgrade-Insecure-Requests": "1",
}


def set_logging(logging_level, logging_file):
    LOGGING_FILE = "JLC2KiCad_lib.log"

    if logging_file:
        logging.basicConfig(
            filename=LOGGING_FILE, format="%(asctime)s - %(levelname)s - %(message)s"
        )

    root_logger = logging.getLogger()
    root_logger.setLevel(logging_level)
    handler = logging.StreamHandler(sys.stdout)
    handler.setLevel(logging.INFO)
    root_logger.addHandler(handler)
    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    handler.setFormatter(formatter)


# Global session for EasyEDA API requests
_easyeda_session = None


def get_easyeda_session():
    """Get a shared requests.Session with cookies from easyeda.com.

    The first call visits easyeda.com to establish the session cookie,
    which is required by all subsequent EasyEDA API endpoints.
    """
    global _easyeda_session
    if _easyeda_session is None:
        _easyeda_session = requests.Session()
        try:
            # Visit the main page to get cookies (required for API auth)
            _easyeda_session.get(
                "https://easyeda.com/",
                headers={
                    "User-Agent": EASYEDA_HEADERS["User-Agent"],
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=15,
            )
        except requests.RequestException as e:
            logging.warning(f"Could not establish EasyEDA session cookie: {e}")
    return _easyeda_session


# Global session for LCSC requests (datasheets, product pages)
_lcsc_session = None


def get_lcsc_session():
    """Get a shared requests.Session with cookies from lcsc.com.

    The first call visits lcsc.com to establish session cookies,
    which are required by datasheet endpoints to return actual PDFs
    instead of HTML wrapper pages.
    """
    global _lcsc_session
    if _lcsc_session is None:
        _lcsc_session = requests.Session()
        try:
            # Visit the main page to get cookies (required for datasheets)
            _lcsc_session.get(
                "https://www.lcsc.com/",
                headers={
                    "User-Agent": LCSC_HEADERS["User-Agent"],
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=15,
            )
        except requests.RequestException as e:
            logging.warning(f"Could not establish LCSC session cookie: {e}")
    return _lcsc_session
