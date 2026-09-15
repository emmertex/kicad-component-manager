import contextlib
import ipaddress
import logging
import os
import re
import shutil
import socket
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import requests

from ..fsutil import atomic_write_bytes
from . import helper

logger = logging.getLogger(__name__)


def _fetch_external_pdf(url, referer, timeout=60):
    """Download a PDF from an external (non-LCSC) host.

    Uses curl for HTTP/2 support and a browser-like TLS fingerprint, which is
    required by manufacturer CDNs (Molex, etc.) that block plain requests.
    Falls back to the bare URL (no query string) on failure.

    Returns raw PDF bytes, or None on failure.
    """
    for attempt_url in _url_variants(url):
        content = _curl_get(attempt_url, referer=referer, timeout=timeout)
        if content and content.startswith(b"%PDF"):
            return content
    return None


def _is_public_ip(ip_str: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_str)
    except ValueError:
        return False
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified
    )


def _validate_public_url(url: str) -> tuple[str, int, str] | None:
    """Return (host, port, ip) if url is https and resolves to a public IP.

    Blocks loopback, RFC1918, link-local (incl. the 169.254.169.254 cloud
    metadata address), reserved, multicast and unspecified addresses. The
    pinned IP is passed to curl via --resolve so DNS cannot rebind between
    validation and connect.
    """
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        logger.warning(f"Refusing non-https PDF URL: {url}")
        return None
    host = parts.hostname
    port = parts.port or 443
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except (socket.gaierror, OSError):
        logger.warning(f"Cannot resolve host for PDF URL: {host}")
        return None
    ips = list(dict.fromkeys(info[4][0] for info in infos))
    public = [ip for ip in ips if _is_public_ip(ip)]
    if not public:
        logger.warning(f"Refusing non-public address for PDF URL: {url}")
        return None
    v4 = next((ip for ip in public if ":" not in ip), public[0])
    return host, port, v4


def _curl_get(url, referer, timeout=60):
    """Fetch URL via curl subprocess and return content bytes, or None on error.

    https-only; every hop (including manual redirects) must resolve to a public
    IP, which is pinned with --resolve to prevent DNS-rebinding SSRF.
    """
    if not shutil.which("curl"):
        logger.warning("curl not found; cannot download external PDF")
        return None

    current = url
    for _ in range(5):
        validated = _validate_public_url(current)
        if validated is None:
            return None
        host, port, ip = validated

        tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
        os.close(tmp_fd)
        try:
            cmd = [
                "curl",
                "--silent",
                "--http2",
                "--max-time",
                str(timeout),
                "--resolve",
                f"{host}:{port}:{ip}",
                "--output",
                tmp_path,
                "--header",
                "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "--header",
                "Accept-Language: en-AU,en;q=0.9",
                "--header",
                "Accept-Encoding: gzip, deflate, br, zstd",
                "--header",
                f"Referer: {referer}",
                "--header",
                "Sec-Fetch-Dest: document",
                "--header",
                "Sec-Fetch-Mode: navigate",
                "--header",
                "Sec-Fetch-Site: cross-site",
                "--header",
                "Upgrade-Insecure-Requests: 1",
                "--user-agent",
                "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
                "--write-out",
                "%{http_code}\\n%{redirect_url}",
                current,
            ]
            logger.info(f"Attempting external PDF download: {current}")
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout + 5
            )
            lines = [ln.strip() for ln in result.stdout.splitlines()]
            http_code = lines[0] if lines else ""
            redirect_url = lines[1] if len(lines) > 1 else ""
            if http_code == "200":
                with open(tmp_path, "rb") as f:
                    return f.read()
            if http_code.startswith("3") and redirect_url:
                current = urljoin(current, redirect_url)
                continue
            logger.warning(
                f"External PDF fetch returned HTTP {http_code} for {current}"
            )
            return None
        except subprocess.TimeoutExpired:
            logger.warning(f"curl timed out for {current}")
            return None
        except Exception as e:
            logger.warning(f"curl error for {current}: {e}")
            return None
        finally:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)
    logger.warning(f"Too many redirects fetching PDF from {url}")
    return None


def _url_variants(url):
    """Yield the original URL then the same URL without query/fragment as a fallback."""
    yield url
    parts = urlsplit(url)
    if parts.query or parts.fragment:
        yield urlunsplit(parts._replace(query="", fragment=""))


def download_pdf(component_id, output_dir, pdf_dir="pdf"):
    """
    Download PDF datasheet from LCSC for a given component ID.
    Handles both LCSC-hosted PDFs (iframe) and manufacturer-hosted PDFs (direct link).

    Uses a shared session with cookies from lcsc.com to ensure datasheet
    endpoints return actual PDFs instead of HTML wrapper pages.

    Args:
        component_id (str): The LCSC component ID
        output_dir (str): Base output directory
        pdf_dir (str): Subdirectory for PDFs (relative to output_dir)

    Returns:
        tuple: (success: bool, pdf_path: str, error_message: str)
    """
    try:
        # Create PDF directory if it doesn't exist
        pdf_path = Path(output_dir) / pdf_dir
        pdf_path.mkdir(parents=True, exist_ok=True)

        session = helper.get_lcsc_session()

        # First, try to get the product page to check for manufacturer datasheet links
        product_url = f"https://www.lcsc.com/product-detail/{component_id}.html"

        logger.info(f"Fetching product page from {product_url}")
        product_response = session.get(
            product_url,
            headers=helper.LCSC_HEADERS,
            timeout=30,
        )

        if product_response.status_code != 200:
            error_msg = f"Failed to fetch product page. HTTP status: {product_response.status_code}"
            logger.warning(f"{error_msg} for component {component_id}")
            return False, "", error_msg

        # Look for manufacturer datasheet link in the product page
        product_content = product_response.text
        # More specific pattern to match external manufacturer datasheet links
        datasheet_pattern = r'<a[^>]*href=["\'](https?://[^"\']*\.pdf)["\'][^>]*title=["\'][^"\']*[Dd]atasheet[^"\']*["\'][^>]*>'
        datasheet_match = re.search(datasheet_pattern, product_content, re.IGNORECASE)

        # Debug: log all PDF links found
        all_pdf_links = re.findall(
            r'<a[^>]*href=["\']([^"\']*\.pdf)["\'][^>]*>',
            product_content,
            re.IGNORECASE,
        )
        logger.info(f"Found {len(all_pdf_links)} PDF links: {all_pdf_links}")

        if (
            datasheet_match
            and "lcsc.com" not in urlparse(datasheet_match.group(1)).netloc
        ):
            pdf_url = datasheet_match.group(1)
            logger.info(f"Found datasheet URL: {pdf_url}")
            if "lcsc.com" not in urlparse(pdf_url).netloc:
                pdf_content = _fetch_external_pdf(pdf_url, referer=product_url)
            else:
                pdf_headers = dict(helper.LCSC_HEADERS)
                pdf_headers["Accept"] = "application/pdf,application/octet-stream,*/*"
                pdf_headers["Referer"] = product_url
                logger.info(f"Downloading PDF from LCSC: {pdf_url}")
                r = session.get(
                    pdf_url, headers=pdf_headers, timeout=30, allow_redirects=True
                )
                pdf_content = (
                    r.content
                    if r.status_code == 200 and r.content.startswith(b"%PDF")
                    else None
                )

        else:
            # Try the LCSC iframe approach - visit the datasheet page first to get cookies
            logger.info(
                f"No manufacturer datasheet found for {component_id}, trying LCSC iframe"
            )
            lcsc_url = f"https://www.lcsc.com/datasheet/{component_id}.pdf"
            logger.info(f"Fetching LCSC datasheet page: {lcsc_url}")

            # Visit the product detail page first to establish cookies, then fetch datasheet
            session.get(product_url, headers=helper.LCSC_HEADERS, timeout=30)

            lcsc_response = session.get(
                lcsc_url, headers=helper.LCSC_HEADERS, timeout=30
            )

            if lcsc_response.status_code != 200:
                error_msg = f"Failed to fetch LCSC datasheet page. HTTP status: {lcsc_response.status_code}"
                logger.warning(f"{error_msg} for component {component_id}")
                return False, "", error_msg

            # Check if the response is already a PDF (some endpoints serve PDF directly)
            content_type = lcsc_response.headers.get("content-type", "")
            if "pdf" in content_type.lower() or lcsc_response.content.startswith(
                b"%PDF"
            ):
                logger.info("LCSC datasheet page returned PDF directly")
                pdf_file_path = pdf_path / f"{component_id}.pdf"
                atomic_write_bytes(pdf_file_path, lcsc_response.content)
                logger.info(f"PDF downloaded successfully: {pdf_file_path}")
                return True, str(pdf_file_path), ""

            # Extract iframe URL from HTML content
            lcsc_content = lcsc_response.text
            iframe_pattern = (
                r'<iframe[^>]*src=["\']([^"\']*\.pdf(?:[?#][^"\']*)?)["\'][^>]*>'
            )
            iframe_match = re.search(iframe_pattern, lcsc_content, re.IGNORECASE)

            # Debug: log all iframe tags found
            all_iframes = re.findall(
                r'<iframe[^>]*src=["\']([^"\']*)["\'][^>]*>',
                lcsc_content,
                re.IGNORECASE,
            )
            logger.info(f"Found {len(all_iframes)} iframe tags: {all_iframes}")

            if not iframe_match:
                # Try to find PDF URL in other patterns (e.g., data attributes, JS)
                alt_patterns = [
                    r'<iframe[^>]*data-src=["\']([^"\']*\.pdf(?:[?#][^"\']*)?)["\'][^>]*>',
                    r'src=["\']([^"\']*datasheet/[^"\']*\.pdf)["\'][^>]*',
                    r'window\.pdfUrl\s*=\s*["\']([^"\']+)["\']',
                ]
                for pattern in alt_patterns:
                    match = re.search(pattern, lcsc_content, re.IGNORECASE)
                    if match:
                        pdf_url = match.group(1).rstrip("\\")
                        break
                else:
                    error_msg = f"Could not find iframe with PDF URL in LCSC page for component {component_id}"
                    logger.warning(error_msg)
                    return False, "", error_msg
            else:
                pdf_url = iframe_match.group(1).rstrip("\\")

            # Convert relative URL to absolute if needed
            if pdf_url.startswith("/"):
                pdf_url = f"https://www.lcsc.com{pdf_url}"
            elif not pdf_url.startswith("http"):
                pdf_url = f"https://www.lcsc.com/{pdf_url}"

            logger.info(f"Found PDF URL in LCSC iframe: {pdf_url}")

            # Use external downloader for off-LCSC URLs, LCSC session for lcsc.com URLs
            if "lcsc.com" not in urlparse(pdf_url).netloc:
                pdf_content = _fetch_external_pdf(pdf_url, referer=lcsc_url)
            else:
                pdf_headers = dict(helper.LCSC_HEADERS)
                pdf_headers["Accept"] = "application/pdf,application/octet-stream,*/*"
                pdf_headers["Referer"] = lcsc_url
                logger.info(f"Downloading PDF from LCSC: {pdf_url}")
                r = session.get(
                    pdf_url, headers=pdf_headers, timeout=30, allow_redirects=True
                )
                pdf_content = (
                    r.content
                    if r.status_code == 200 and r.content.startswith(b"%PDF")
                    else None
                )

        if pdf_content is not None:
            # Save the PDF file
            pdf_file_path = pdf_path / f"{component_id}.pdf"
            atomic_write_bytes(pdf_file_path, pdf_content)

            logger.info(f"PDF downloaded successfully: {pdf_file_path}")
            return True, str(pdf_file_path), ""
        else:
            error_msg = f"Failed to download PDF for {component_id}"
            logger.warning(error_msg)
            return False, "", error_msg

    except requests.exceptions.RequestException as e:
        error_msg = f"Network error downloading PDF: {str(e)}"
        logger.warning(f"{error_msg} for component {component_id}")
        return False, "", error_msg
    except Exception as e:
        error_msg = f"Unexpected error downloading PDF: {str(e)}"
        logger.error(f"{error_msg} for component {component_id}")
        return False, "", error_msg


def get_pdf_relative_path(component_id, pdf_dir="pdf"):
    """
    Get the relative path for the PDF file that would be used in KiCad.

    Args:
        component_id (str): The LCSC component ID
        pdf_dir (str): PDF directory name

    Returns:
        str: Relative path to the PDF file
    """
    return f"${{KICAD_USER_LIBRARY_DIR}}/{pdf_dir}/{component_id}.pdf"


def update_datasheet_link(original_link, component_id, pdf_dir="pdf"):
    """
    Update the datasheet link to point to the local PDF file if available.

    Args:
        original_link (str): Original datasheet link from EasyEDA
        component_id (str): The LCSC component ID
        pdf_dir (str): PDF directory name

    Returns:
        str: Updated datasheet link
    """
    # If we have a local PDF, use it instead of the original link
    pdf_relative_path = get_pdf_relative_path(component_id, pdf_dir)
    return pdf_relative_path
