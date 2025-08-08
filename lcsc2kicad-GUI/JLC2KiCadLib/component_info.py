import requests
import re
import logging
import json
from bs4 import BeautifulSoup
from urllib.parse import urljoin


def extract_component_info(component_id):
    """
    Extract component information from LCSC product page.
    
    Args:
        component_id (str): The LCSC component ID
        
    Returns:
        dict: Dictionary containing component information
    """
    try:
        # Fetch the product page
        product_url = f"https://www.lcsc.com/product-detail/{component_id}.html"
        
        headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
        }
        
        logging.info(f"Fetching component information from {product_url}")
        response = requests.get(product_url, headers=headers, timeout=30)
        
        if response.status_code != 200:
            logging.warning(f"Failed to fetch component info. HTTP status: {response.status_code}")
            return {}
        
        # Parse HTML content
        soup = BeautifulSoup(response.content, 'html.parser')
        
        component_info = {
            'description': '',
            'category': '',
            'manufacturer': '',
            'package': '',
            'specifications': {}
        }
        
        # Extract description from HTML table - look specifically for the "Description" field
        desc_elem = soup.find('td', string='Description')
        if desc_elem and desc_elem.find_next_sibling('td'):
            # Get the description from the next cell
            desc_cell = desc_elem.find_next_sibling('td')
            description_elem = desc_cell.find('span', class_='major2--text')
            if description_elem:
                component_info['description'] = description_elem.get_text(strip=True)
                logging.info(f"Found description from table: {component_info['description']}")
        
        # If no description found, try to find the most comprehensive description
        if not component_info['description']:
            all_spans = soup.find_all('span', class_='major2--text')
            longest_description = ""
            for span in all_spans:
                text = span.get_text(strip=True)
                # Look for the longest description that contains technical specifications
                # Remove hardcoded microcontroller keywords and make it generic
                if len(text) > len(longest_description) and len(text) > 20:
                    longest_description = text
            
            if longest_description:
                component_info['description'] = longest_description
                logging.info(f"Found longest description: {component_info['description']}")
        
        # Final fallback: try the main product description
        if not component_info['description']:
            description_elem = soup.find('span', class_='major2--text')
            if description_elem:
                component_info['description'] = description_elem.get_text(strip=True)
                logging.info(f"Found fallback description: {component_info['description']}")
        
        # Extract package
        package_elem = soup.find('td', id='package_id')
        if package_elem and package_elem.find_next_sibling('td'):
            component_info['package'] = package_elem.find_next_sibling('td').get_text(strip=True)
            logging.info(f"Found package: {component_info['package']}")
        
        # Extract category - find the row with td id="category_id" and get the title from the next sibling's anchor tag
        category_elem = soup.find('td', id='category_id')
        if category_elem and category_elem.find_next_sibling('td'):
            category_cell = category_elem.find_next_sibling('td')
            category_link = category_cell.find('a')
            if category_link and category_link.get('title'):
                component_info['category'] = category_link.get('title').strip()
                logging.info(f"Found category: {component_info['category']}")
            elif category_link:
                component_info['category'] = category_link.get_text(strip=True)
                logging.info(f"Found category: {component_info['category']}")

        # Extract manufacturer
        manufacturer_elem = soup.find('td', id='manufacturer_id')
        if manufacturer_elem and manufacturer_elem.find_next_sibling('td'):
            component_info['manufacturer'] = manufacturer_elem.find_next_sibling('td').get_text(strip=True)
            logging.info(f"Found manufacturer: {component_info['manufacturer']}")

        # Extract Parameters - look for paramsItem0 through paramsItem15
        ## Does not work because the Javascript has not executed.
        for i in range(16):  # 0 to 15
            param_id = f"paramsItem{i}"
            param_elem = soup.find('td', id=param_id)
            if param_elem and param_elem.find_next_sibling('td'):
                param_name = param_elem.get_text(strip=True)
                param_value = param_elem.find_next_sibling('td').get_text(strip=True)
                if param_name and param_value:
                    component_info['specifications'][param_name] = param_value
                    logging.info(f"Found parameter: {param_name} = {param_value}")

        return component_info
        
    except Exception as e:
        logging.error(f"Error extracting component info for {component_id}: {str(e)}")
        return {}


def create_component_properties(component_info):
    """
    Create KiCad symbol properties from component information.
    
    Args:
        component_info (dict): Component information dictionary
        
    Returns:
        str: KiCad symbol properties string
    """
    properties = []
    
    # Note: We don't create a "Description" property here because KiCad already has one
    # The description will be handled by the symbol template
    
    # Add category property
    if component_info.get('category'):
        properties.append(f'    (property "Category" "{component_info["category"]}" (id 7) (at 0 0 0)')
        properties.append('      (effects (font (size 1.27 1.27)) hide)')
        properties.append('    )')
    
    # Add manufacturer property
    if component_info.get('manufacturer'):
        properties.append(f'    (property "Manufacturer" "{component_info["manufacturer"]}" (id 8) (at 0 0 0)')
        properties.append('      (effects (font (size 1.27 1.27)) hide)')
        properties.append('    )')
    
    # Add package property
    if component_info.get('package'):
        properties.append(f'    (property "Package" "{component_info["package"]}" (id 9) (at 0 0 0)')
        properties.append('      (effects (font (size 1.27 1.27)) hide)')
        properties.append('    )')
    
    # Add specifications as properties
    spec_id = 10
    for spec_name, spec_value in component_info.get('specifications', {}).items():
        # Clean up property name for KiCad
        clean_name = re.sub(r'[^\w\s-]', '', spec_name).strip()
        if clean_name and len(clean_name) <= 50:  # KiCad has limits on property name length
            properties.append(f'    (property "{clean_name}" "{spec_value}" (id {spec_id}) (at 0 0 0)')
            properties.append('      (effects (font (size 1.27 1.27)) hide)')
            properties.append('    )')
            spec_id += 1
    
    return '\n'.join(properties) 