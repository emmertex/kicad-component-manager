import sys
sys.path.append('.')

import requests
import json
import logging
import argparse

# Import PDF downloader
try:
    from JLC2KiCadLib import pdf_downloader
    from JLC2KiCadLib import component_info
except ImportError:
    import pdf_downloader
    import component_info

__version__ = "1.0.0"

# Update imports
try:
    from JLC2KiCadLib import helper
    from JLC2KiCadLib.footprint.footprint import create_footprint, get_footprint_info
    from JLC2KiCadLib.symbol.symbol import create_symbol
except ImportError:
    import helper
    from footprint.footprint import create_footprint, get_footprint_info
    from symbol.symbol import create_symbol


def add_component(component_id, args):
    logging.info(f"creating library for component {component_id}")
    data = json.loads(
        requests.get(
            f"https://easyeda.com/api/products/{component_id}/svgs"
        ).content.decode()
    )

    if not data["success"]:
        logging.error(
            f"failed to get component uuid for {component_id}\nThe component # is probably wrong. Check a possible typo and that the component exists on easyEDA"
        )
        return ()

    footprint_component_uuid = data["result"][-1]["component_uuid"]
    symbol_component_uuid = [i["component_uuid"] for i in data["result"][:-1]]

    # Extract component information from LCSC
    logging.info(f"Extracting component information for {component_id}")
    component_info_data = component_info.extract_component_info(component_id)

    if args.footprint_creation:
        footprint_name, datasheet_link = create_footprint(
            footprint_component_uuid=footprint_component_uuid,
            component_id=component_id,
            footprint_lib=args.footprint_lib,
            output_dir=args.output_dir,
            model_base_variable=args.model_base_variable,
            model_dir=args.model_dir,
            skip_existing=args.skip_existing,
            models=args.models,
        )
    else:
        _, datasheet_link, _, _ = get_footprint_info(footprint_component_uuid)
        footprint_name = ""

    # Download PDF if requested
    if args.download_pdf:
        pdf_success, pdf_path, pdf_error = pdf_downloader.download_pdf(
            component_id, args.output_dir, args.pdf_dir
        )
        if pdf_success:
            # Update datasheet link to point to local PDF
            datasheet_link = pdf_downloader.update_datasheet_link(
                datasheet_link, component_id, args.pdf_dir
            )
            logging.info(f"Updated datasheet link to local PDF: {datasheet_link}")
        else:
            logging.warning(f"PDF download failed for {component_id}: {pdf_error}")

    if args.symbol_creation:
        create_symbol(
            symbol_component_uuid=symbol_component_uuid,
            footprint_name=footprint_name.replace(
                ".pretty", ""
            ),  # see https://github.com/TousstNicolas/JLC2KiCad_lib/issues/47
            datasheet_link=datasheet_link,
            library_name=args.symbol_lib,
            symbol_path=args.symbol_lib_dir,
            output_dir=args.output_dir,
            component_id=component_id,
            skip_existing=args.skip_existing,
            component_info_data=component_info_data,
        )


def main():
    parser = argparse.ArgumentParser(
        description="take a JLCPCB part # and create the according component's kicad's library",
        epilog="example use : \n	JLC2KiCadLib C1337258 C24112 -dir My_lib -symbol_lib My_Symbol_lib --no_footprint",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "components",
        metavar="JLCPCB_part_#",
        type=str,
        nargs="+",
        help="List of JLCPCB part # from the components you want to create",
    )

    parser.add_argument(
        "-dir",
        dest="output_dir",
        type=str,
        default="JLC2KiCad_lib",
        help="Base directory for output library files",
    )

    parser.add_argument(
        "--no_footprint",
        dest="footprint_creation",
        action="store_false",
        help="Use --no_footprint if you do not want to create the footprint",
    )

    parser.add_argument(
        "--no_symbol",
        dest="symbol_creation",
        action="store_false",
        help="Use --no_symbol if you do not want to create the symbol",
    )

    parser.add_argument(
        "-symbol_lib",
        dest="symbol_lib",
        type=str,
        default=None,
        help='Set symbol library name, default is "default_lib"',
    )

    parser.add_argument(
        "-symbol_lib_dir",
        dest="symbol_lib_dir",
        type=str,
        default="symbol",
        help='Set symbol library path, default is "symbol" (relative to OUTPUT_DIR)',
    )

    parser.add_argument(
        "-footprint_lib",
        dest="footprint_lib",
        type=str,
        default="footprint",
        help='Set footprint library name,  default is "footprint"',
    )

    parser.add_argument(
        "-models",
        dest="models",
        nargs="*",
        choices=["STEP", "WRL"],
        type=str,
        default="STEP",
        help="Select the 3D model you want to use. Default is STEP. If both are selected, only the STEP model will be added to the footprint (the WRL model will still be generated alongside the STEP model). If you do not want any model to be generated, use the --models without arguments",
    )

    parser.add_argument(
        "-model_dir",
        dest="model_dir",
        type=str,
        default="packages3d",
        help='Set directory for storing 3d models, default is "packages3d" (relative to FOOTPRINT_LIB)',
    )

    parser.add_argument(  # argument to skip already existing files and symbols
        "--skip_existing",
        dest="skip_existing",
        action="store_true",
        help="Use --skip_existing if you want do not want to replace already existing footprints and symbols",
    )

    parser.add_argument(
        "-model_base_variable",
        dest="model_base_variable",
        type=str,
        default="",
        help="Use -model_base_variable if you want to specify the base path of the 3D model using a path variable. If the specified variable starts with '$' it is used 'as-is', otherwise it is encapsulated: $(MODEL_BASE_VARIABLE)",
    )

    parser.add_argument(
        "-logging_level",
        dest="logging_level",
        type=str,
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Set logging level. If DEBUG is used, the debug logs are only written in the log file if the option  --log_file is set ",
    )

    parser.add_argument(
        "--log_file",
        dest="log_file",
        action="store_true",
        help="Use --log_file if you want logs to be written in a file",
    )

    parser.add_argument(
        "--download_pdf",
        dest="download_pdf",
        action="store_true",
        help="Download PDF datasheets from LCSC and update symbol links",
    )

    parser.add_argument(
        "-pdf_dir",
        dest="pdf_dir",
        type=str,
        default="pdf",
        help='Set directory for storing PDF datasheets, default is "pdf" (relative to OUTPUT_DIR)',
    )

    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
        help="Print versin number and exit",
    )

    args = parser.parse_args()

    helper.set_logging(args.logging_level, args.log_file)

    for component in args.components:
        add_component(component, args)


if __name__ == "__main__":
    main()
