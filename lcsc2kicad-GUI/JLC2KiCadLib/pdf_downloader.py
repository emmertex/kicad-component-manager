import os
import requests
import logging
import re
from pathlib import Path
from urllib.parse import urlparse, urljoin


def download_pdf(component_id, output_dir, pdf_dir="pdf"):
    """
    Download PDF datasheet from LCSC for a given component ID.
    Handles both LCSC-hosted PDFs (iframe) and manufacturer-hosted PDFs (direct link).
    
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
        
        # First, try to get the product page to check for manufacturer datasheet links
        product_url = f"https://www.lcsc.com/product-detail/{component_id}.html"
        
        # Headers for HTML request
        html_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
        }
        
        logging.info(f"Fetching product page from {product_url}")
        product_response = requests.get(product_url, headers=html_headers, timeout=30)
        
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
        all_pdf_links = re.findall(r'<a[^>]*href=["\']([^"\']*\.pdf)["\'][^>]*>', product_content, re.IGNORECASE)
        logging.info(f"Found {len(all_pdf_links)} PDF links: {all_pdf_links}")
        
        if datasheet_match:
            # Found manufacturer datasheet link
            pdf_url = datasheet_match.group(1)
            logging.info(f"Found manufacturer datasheet URL: {pdf_url}")
            
            # Download the PDF from manufacturer
            pdf_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/pdf,application/octet-stream,*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Referer': product_url,
            }
            
            logging.info(f"Downloading PDF from manufacturer: {pdf_url}")
            pdf_response = requests.get(pdf_url, headers=pdf_headers, timeout=30)
            
        else:
            # Try the LCSC iframe approach
            logging.info(f"No manufacturer datasheet found for {component_id}, trying LCSC iframe")
            lcsc_url = f"https://www.lcsc.com/datasheet/{component_id}.pdf"
            logging.info(f"Fetching LCSC datasheet page: {lcsc_url}")
            
            lcsc_response = requests.get(lcsc_url, headers=html_headers, timeout=30)
            
            if lcsc_response.status_code != 200:
                error_msg = f"Failed to fetch LCSC datasheet page. HTTP status: {lcsc_response.status_code}"
                logging.warning(f"{error_msg} for component {component_id}")
                return False, "", error_msg
            
            # Extract iframe URL from HTML content
            lcsc_content = lcsc_response.text
            iframe_pattern = r'<iframe[^>]*src=["\']([^"\']*\.pdf)["\'][^>]*>'
            iframe_match = re.search(iframe_pattern, lcsc_content, re.IGNORECASE)
            
            # Debug: log all iframe tags found
            all_iframes = re.findall(r'<iframe[^>]*src=["\']([^"\']*)["\'][^>]*>', lcsc_content, re.IGNORECASE)
            logging.info(f"Found {len(all_iframes)} iframe tags: {all_iframes}")
            
            if not iframe_match:
                error_msg = f"Could not find iframe with PDF URL in LCSC page for component {component_id}"
                logging.warning(error_msg)
                return False, "", error_msg
            
            # Get the actual PDF URL from iframe
            pdf_url = iframe_match.group(1)
            
            # Convert relative URL to absolute if needed
            if pdf_url.startswith('/'):
                pdf_url = f"https://www.lcsc.com{pdf_url}"
            elif not pdf_url.startswith('http'):
                pdf_url = f"https://www.lcsc.com/{pdf_url}"
                
            logging.info(f"Found PDF URL in LCSC iframe: {pdf_url}")
            
            # Download the actual PDF
            pdf_headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
                'Accept': 'application/pdf,application/octet-stream,*/*',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Referer': lcsc_url,
            }
            
            logging.info(f"Downloading PDF from LCSC: {pdf_url}")
            pdf_response = requests.get(pdf_url, headers=pdf_headers, timeout=30)
        
        if pdf_response.status_code == 200:
            # Check if the response is actually a PDF
            content_type = pdf_response.headers.get('content-type', '')
            if 'pdf' not in content_type.lower() and not pdf_response.content.startswith(b'%PDF'):
                error_msg = f"Response is not a PDF. Content-Type: {content_type}"
                logging.warning(f"{error_msg} for component {component_id}")
                return False, "", error_msg
            
            # Save the PDF file
            pdf_file_path = pdf_path / f"{component_id}.pdf"
            with open(pdf_file_path, 'wb') as f:
                f.write(pdf_response.content)
            
            logging.info(f"PDF downloaded successfully: {pdf_file_path}")
            return True, str(pdf_file_path), ""
        else:
            error_msg = f"Failed to download PDF. HTTP status: {pdf_response.status_code}"
            logging.warning(f"{error_msg} for component {component_id}")
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