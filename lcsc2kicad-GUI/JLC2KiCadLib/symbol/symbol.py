import json
import logging
import os
import re
import tempfile

import component_info
import helper
import requests

from .symbol_handlers import handlers

template_lib_header = f"""\
(kicad_symbol_lib (version 20210201) (generator TousstNicolas/JLC2KiCad_lib)
"""

template_lib_footer = ")\n"

supported_value_types = [
    "Resistance",
    "Capacitance",
    "Inductance",
    "Frequency",
]  # define which attribute/value from JLCPCB/LCSC will be added in the "value" field


def create_symbol(
    symbol_component_uuid,
    footprint_name,
    datasheet_link,
    library_name,
    symbol_path,
    output_dir,
    component_id,
    skip_existing,
    component_info_data=None,
    price=None,
    stock=None,
):
    class kicad_symbol:
        drawing = ""
        pinNamesHide = "(pin_names hide)"
        pinNumbersHide = "(pin_numbers hide)"

    kicad_symbol = kicad_symbol()

    ComponentName = ""
    session = helper.get_easyeda_session()
    for component_uuid in symbol_component_uuid:
        response = session.get(
            f"https://easyeda.com/api/components/{component_uuid}",
            headers=helper.EASYEDA_HEADERS,
        )
        if response.status_code == requests.codes.ok:
            data = json.loads(response.content.decode())
        else:
            logging.error(
                f"create_symbol error. Requests returned with error code {response.status_code}"
            )
            return ()

        symbol_shape = data["result"]["dataStr"]["shape"]
        symmbolic_prefix = data["result"]["packageDetail"]["dataStr"]["head"]["c_para"][
            "pre"
        ].replace("?", "")
        component_title = (
            data["result"]["title"]
            .replace(" ", "_")
            .replace(".", "_")
            .replace("/", "{slash}")
            .replace("\\", "{backslash}")
            .replace("<", "{lt}")
            .replace(">", "{gt}")
            .replace(":", "{colon}")
            .replace('"', "{dblquote}")
        )

        component_types_values = []
        for value_type in supported_value_types:
            if value_type in data["result"]["dataStr"]["head"]["c_para"]:
                component_types_values.append(
                    (
                        value_type,
                        data["result"]["dataStr"]["head"]["c_para"][value_type],
                    )
                )

        if not ComponentName:
            ComponentName = component_title
            component_title += "_0"
        if (
            len(symbol_component_uuid) >= 2
            and component_uuid == symbol_component_uuid[0]
        ):
            continue

        # if library_name is not defined, use component_title as library name
        if not library_name:
            library_name = ComponentName

        filename = f"{output_dir}/{symbol_path}/{library_name}.kicad_sym"

        logging.info(f"creating symbol {component_title} in {library_name}")

        kicad_symbol.drawing += f'''\n    (symbol "{component_title}_1"'''

        for line in symbol_shape:
            args = [
                i for i in line.split("~") if i
            ]  # split and remove empty string in list
            model = args[0]
            logging.debug(args)
            if model not in handlers:
                logging.warning("symbol : parsing model not in handler : " + model)
            else:
                handlers.get(model)(
                    data=args[1:],
                    translation=(
                        data["result"]["dataStr"]["head"]["x"],
                        data["result"]["dataStr"]["head"]["y"],
                    ),
                    kicad_symbol=kicad_symbol,
                )
        kicad_symbol.drawing += """\n    )"""

    # Create component properties from LCSC data
    component_properties = ""
    if component_info_data:
        component_properties = component_info.create_component_properties(
            component_info_data
        )

    # Add description property if available
    description_property = ""
    if component_info_data and component_info_data.get("description"):
        description_property = f"""
    (property "Description" "{component_info_data["description"]}" (id 6) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )"""

    # Determine the "Value" property - use LCSC value if available, otherwise use ComponentName
    value_property = ComponentName
    if component_info_data:
        value_property = (
            component_info_data.get("value")
            or component_info_data.get("Value")
            or ComponentName
        )

    template_lib_component = f"""\
  (symbol "{ComponentName}" {kicad_symbol.pinNamesHide} {kicad_symbol.pinNumbersHide} (in_bom yes) (on_board yes)
    (property "Reference" "{symmbolic_prefix}" (id 0) (at 0 1.27 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Value" "{value_property}" (id 1) (at 0 -2.54 0)
      (effects (font (size 1.27 1.27)))
    )
    (property "Footprint" "{footprint_name}" (id 2) (at 0 -10.16 0)
      (effects (font (size 1.27 1.27) italic) hide)
    )
    (property "Datasheet" "{datasheet_link}" (id 3) (at -2.286 0.127 0)
      (effects (font (size 1.27 1.27)) (justify left) hide)
    )
    (property "ki_keywords" "{component_id}" (id 4) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )
    (property "LCSC" "{component_id}" (id 5) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    ){description_property}
    {get_type_values_properties(7 if description_property else 6, component_types_values)}{component_properties}{_stock_price_properties(stock, price)}{kicad_symbol.drawing}
  )
"""

    if not os.path.exists(f"{output_dir}/{symbol_path}"):
        os.makedirs(f"{output_dir}/{symbol_path}")

    if os.path.exists(filename):
        update_library(
            library_name,
            symbol_path,
            ComponentName,
            template_lib_component,
            output_dir,
            skip_existing,
        )
    else:
        with open(filename, "w") as f:
            logging.info(f"writing in {filename} file")
            f.write(template_lib_header)
            f.write(template_lib_footer)
        update_library(
            library_name,
            symbol_path,
            ComponentName,
            template_lib_component,
            output_dir,
            skip_existing,
        )


def _stock_price_properties(stock, price) -> str:
    parts = []
    if stock is not None:
        parts.append(
            f'(property "Stock" "{stock}" (id 97) (at 0 0 0)\n'
            f"      (effects (font (size 1.27 1.27)) hide)\n"
            f"    )"
        )
    if price is not None:
        parts.append(
            f'(property "Price" "{price}" (id 98) (at 0 0 0)\n'
            f"      (effects (font (size 1.27 1.27)) hide)\n"
            f"    )"
        )
    if not parts:
        return ""
    return "\n    " + "\n    ".join(parts)


def get_type_values_properties(start_index, component_types_values):
    return "\n".join(
        [
            f"""(property "{type_value[0]}" "{type_value[1]}" (id {start_index + index}) (at 0 0 0)
      (effects (font (size 1.27 1.27)) hide)
    )"""
            for index, type_value in enumerate(component_types_values)
        ]
    )


def _find_lib_close(content):
    """Return the index of the ) that closes the kicad_symbol_lib node."""
    in_str = False
    esc = False
    depth = 0
    for i, c in enumerate(content):
        if esc:
            esc = False
            continue
        if c == "\\" and in_str:
            esc = True
            continue
        if c == '"':
            in_str = not in_str
            continue
        if in_str:
            continue
        if c == "(":
            depth += 1
        elif c == ")":
            depth -= 1
            if depth == 0:
                return i
    return content.rfind(")")


def update_library(
    library_name,
    symbol_path,
    component_title,
    template_lib_component,
    output_dir,
    skip_existing,
):
    """
    if component is already in library,
    the library will be updated,
    if not already present in library,
    the component will be added at the end
    """
    filepath = f"{output_dir}/{symbol_path}/{library_name}.kicad_sym"

    with open(filepath, "r", encoding="utf-8", errors="replace") as f:
        file_content = f.read()

    pattern = rf'  \(symbol "{re.escape(component_title)}" (\n|.)*?\n  \)'

    if f'symbol "{component_title}"' in file_content:
        if skip_existing:
            logging.info(
                f"component {component_title} already in symbols library, skipping"
            )
            return
        # use regex to find the old component template in the file and replace it with the new one
        logging.info(
            f"found component already in {library_name}, updating {library_name}"
        )
        new_content = re.sub(
            pattern=pattern,
            repl=template_lib_component,
            string=file_content,
            flags=re.DOTALL,
            count=1,
        )
    else:
        # Insert before the library's closing ) using a depth-aware search
        # so we always find the actual library footer, not a ) inside a symbol.
        # see https://github.com/TousstNicolas/JLC2KiCad_lib/issues/46
        close_pos = _find_lib_close(file_content)
        new_content = (
            file_content[:close_pos] + template_lib_component + template_lib_footer
        )

    # Atomic-ish write using a temporary file
    fd, temp_path = tempfile.mkstemp(dir=os.path.dirname(filepath), text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as temp_file:
            temp_file.write(new_content)
        os.replace(temp_path, filepath)
    except Exception:
        if os.path.exists(temp_path):
            os.remove(temp_path)
        raise
