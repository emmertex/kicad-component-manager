import logging
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from urllib.parse import urlparse, urlsplit, urlunsplit

import requests
import helper


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


def _curl_get(url, referer, timeout=60):
    """Fetch URL via curl subprocess and return content bytes, or None on error."""
    if not shutil.which("curl"):
        logging.warning("curl not found; cannot download external PDF")
        return None

    tmp_fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
    os.close(tmp_fd)
    try:
        cmd = [
            "curl", "--silent", "--location", "--http2",
            "--max-time", str(timeout),
            "--output", tmp_path,
            "--header", "Accept: text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "--header", "Accept-Language: en-AU,en;q=0.9",
            "--header", "Accept-Encoding: gzip, deflate, br, zstd",
            "--header", f"Referer: {referer}",
            "--header", "Sec-Fetch-Dest: document",
            "--header", "Sec-Fetch-Mode: navigate",
            "--header", "Sec-Fetch-Site: cross-site",
            "--header", "Upgrade-Insecure-Requests: 1",
            "--user-agent", "Mozilla/5.0 (X11; Linux x86_64; rv:150.0) Gecko/20100101 Firefox/150.0",
            "--write-out", "%{http_code}",
            url,
        ]
        logging.info(f"Attempting external PDF download: {url}")
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 5)
        http_code = result.stdout.strip()
        if http_code == "200":
            with open(tmp_path, "rb") as f:
                return f.read()
        logging.warning(f"External PDF fetch returned HTTP {http_code} for {url}")
    except subprocess.TimeoutExpired:
        logging.warning(f"curl timed out for {url}")
    except Exception as e:
        logging.warning(f"curl error for {url}: {e}")
    finally:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
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

        logging.info(f"Fetching product page from {product_url}")
        product_response = session.get(
            product_url,
            headers=helper.LCSC_HEADERS,
            timeout=30,
        )

        if product_response.status_code != 200:
            error_msg = f"Failed to fetch product page. HTTP status: {product_response.status_code}"
            logging.warning(f"{error_msg} for component {component_id}")
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
        logging.info(f"Found {len(all_pdf_links)} PDF links: {all_pdf_links}")

        if datasheet_match and "lcsc.com" not in urlparse(datasheet_match.group(1)).netloc:
            pdf_url = datasheet_match.group(1)
            logging.info(f"Found datasheet URL: {pdf_url}")
            if "lcsc.com" not in urlparse(pdf_url).netloc:
                pdf_content = _fetch_external_pdf(pdf_url, referer=product_url)
            else:
                pdf_headers = dict(helper.LCSC_HEADERS)
                pdf_headers["Accept"] = "application/pdf,application/octet-stream,*/*"
                pdf_headers["Referer"] = product_url
                logging.info(f"Downloading PDF from LCSC: {pdf_url}")
                r = session.get(pdf_url, headers=pdf_headers, timeout=30, allow_redirects=True)
                pdf_content = r.content if r.status_code == 200 and r.content.startswith(b"%PDF") else None

        else:
            # Try the LCSC iframe approach - visit the datasheet page first to get cookies
            logging.info(
                f"No manufacturer datasheet found for {component_id}, trying LCSC iframe"
            )
            lcsc_url = f"https://www.lcsc.com/datasheet/{component_id}.pdf"
            logging.info(f"Fetching LCSC datasheet page: {lcsc_url}")

            # Visit the product detail page first to establish cookies, then fetch datasheet
            session.get(product_url, headers=helper.LCSC_HEADERS, timeout=30)

            lcsc_response = session.get(
                lcsc_url, headers=helper.LCSC_HEADERS, timeout=30
            )

            if lcsc_response.status_code != 200:
                error_msg = f"Failed to fetch LCSC datasheet page. HTTP status: {lcsc_response.status_code}"
                logging.warning(f"{error_msg} for component {component_id}")
                return False, "", error_msg

            # Check if the response is already a PDF (some endpoints serve PDF directly)
            content_type = lcsc_response.headers.get("content-type", "")
            if "pdf" in content_type.lower() or lcsc_response.content.startswith(
                b"%PDF"
            ):
                logging.info(f"LCSC datasheet page returned PDF directly")
                pdf_file_path = pdf_path / f"{component_id}.pdf"
                with open(pdf_file_path, "wb") as f:
                    f.write(lcsc_response.content)
                logging.info(f"PDF downloaded successfully: {pdf_file_path}")
                return True, str(pdf_file_path), ""

            # Extract iframe URL from HTML content
            lcsc_content = lcsc_response.text
            iframe_pattern = r'<iframe[^>]*src=["\']([^"\']*\.pdf(?:[?#][^"\']*)?)["\'][^>]*>'
            iframe_match = re.search(iframe_pattern, lcsc_content, re.IGNORECASE)

            # Debug: log all iframe tags found
            all_iframes = re.findall(
                r'<iframe[^>]*src=["\']([^"\']*)["\'][^>]*>',
                lcsc_content,
                re.IGNORECASE,
            )
            logging.info(f"Found {len(all_iframes)} iframe tags: {all_iframes}")

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
                    logging.warning(error_msg)
                    return False, "", error_msg
            else:
                pdf_url = iframe_match.group(1).rstrip("\\")

            # Convert relative URL to absolute if needed
            if pdf_url.startswith("/"):
                pdf_url = f"https://www.lcsc.com{pdf_url}"
            elif not pdf_url.startswith("http"):
                pdf_url = f"https://www.lcsc.com/{pdf_url}"

            logging.info(f"Found PDF URL in LCSC iframe: {pdf_url}")

            # Use external downloader for off-LCSC URLs, LCSC session for lcsc.com URLs
            if "lcsc.com" not in urlparse(pdf_url).netloc:
                pdf_content = _fetch_external_pdf(pdf_url, referer=lcsc_url)
            else:
                pdf_headers = dict(helper.LCSC_HEADERS)
                pdf_headers["Accept"] = "application/pdf,application/octet-stream,*/*"
                pdf_headers["Referer"] = lcsc_url
                logging.info(f"Downloading PDF from LCSC: {pdf_url}")
                r = session.get(pdf_url, headers=pdf_headers, timeout=30, allow_redirects=True)
                pdf_content = r.content if r.status_code == 200 and r.content.startswith(b"%PDF") else None

        if pdf_content is not None:
            # Save the PDF file
            pdf_file_path = pdf_path / f"{component_id}.pdf"
            with open(pdf_file_path, "wb") as f:
                f.write(pdf_content)

            logging.info(f"PDF downloaded successfully: {pdf_file_path}")
            return True, str(pdf_file_path), ""
        else:
            error_msg = f"Failed to download PDF for {component_id}"
            logging.warning(error_msg)
            return False, "", error_msg

    except requests.exceptions.RequestException as e:
        error_msg = f"Network error downloading PDF: {str(e)}"
        logging.warning(f"{error_msg} for component {component_id}")
        return False, "", error_msg
    except Exception as e:
        error_msg = f"Unexpected error downloading PDF: {str(e)}"
        logging.error(f"{error_msg} for component {component_id}")
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
