# LCSC to KiCad Library Converter GUI

A graphical user interface for converting LCSC/JLCPCB component libraries to KiCad format. This project builds upon and combines these excellent projects.
- [JLC2KiCad_lib](https://github.com/TousstNicolas/JLC2KiCad_lib) by TousstNicolas
- [lcsc2kicad](https://github.com/DasBasti/lcsc2kicad) by DasBasti
- [lcsc2kicad-GUI](https://github.com/milutintech/lcsc2kicad-GUI) by milutintech

## Features

- User-friendly graphical interface
- Easy component management:
  - Add components with or without 'C' prefix
  - Add comments to components
  - Delete individual components
  - Clear entire list
- Save/Load component lists (JSON format)
- Generate KiCad libraries including:
  - Symbols (.kicad_sym)
  - Footprints (.pretty)
  - 3D Models (STEP format)
  - Locally stored PDF datasheets
- Progress tracking and error logging

## Installation

1. Clone the repository:
```bash
git clone https://github.com/emmertex/lcsc2kicad-GUI
cd lcsc2kicad-GUI
```

## Usage

### Automatically Run  (Linux, Mac and WSL)

```bash
./run.sh
```
This script does the following.  

- Looks for python venv.
   - If not present, creates venv and installs dependencies.
   - If venv is present, updates dependencies.
- Starts the GUI.


### Manually Run  

If you wish to run manually, or use Windows without WSL, use the following commands.

```bash
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python gui.py
```

### Adding Components

1. Enter LCSC part number in the input field:
   - Can enter with or without 'C' prefix (e.g., "C2931873" or "2931873")
   - Press Enter or click "Add Part"

2. Add comments (optional):
   - Select a component from the list
   - Enter comment in the comment field
   - Press Enter or click "Update Comment"

### Managing Component Lists

- **Save List**: Save your current component list with comments to a JSON file
- **Load List**: Load a previously saved component list
- **Delete Selected**: Remove selected component from the list
- **Clear All**: Remove all components from the list

### Converting Components

1. Select output directory using "Browse"
2. Set symbol library name (default: "components")
3. Choose whether to include 3D models (STEP format)
4. Click "Convert" to start the conversion process

### Output Structure

The converter will create the following directory structure:
```
output_directory/
├── footprint.pretty/      # Footprint library
│   ├── packages3d/       # 3D models
│   │   ├── component1.step
│   │   └── component2.step
│   ├── component1.kicad_mod
│   └── component2.kicad_mod
└── symbol/
    └── components.kicad_sym
```

### Importing to KiCad

1. Symbol Library:
   - Preferences → Manage Symbol Libraries
   - Add existing library
   - Select `symbol/components.kicad_sym`

2. Footprint Library:
   - Preferences → Manage Footprint Libraries
   - Add existing library
   - Select the `.pretty` folder

3. 3D Models:
   - Automatically linked if directory structure is maintained

4. PDF Datasheets:
   - Automatically links to the downloaded PDF datasheets.

## Credits

This project combines and builds upon two excellent LCSC/JLCPCB to KiCad converters:

### JLC2KiCad_lib
- Original Repository: [JLC2KiCad_lib](https://github.com/TousstNicolas/JLC2KiCad_lib) by TousstNicolas
- Features used:
  - Core conversion functionality
  - Symbol generation
  - Footprint creation
  - 3D model handling
- License: MIT

### lcsc2kicad
- Original Repository: [lcsc2kicad](https://github.com/DasBasti/lcsc2kicad) by DasBasti
- Features used:
  - Initial inspiration
  - LCSC component handling approach
  - Project structure influence
- License: MIT

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request. For major changes, please open an issue first to discuss what you would like to change.

## License

This project is released under the MIT License, matching the licenses of both original projects it builds upon.

## Troubleshooting

### Common Issues

1. **Component Not Found**
   - Verify the LCSC part number is correct
   - Check if the component exists on JLCPCB's website
   - Make sure to use the correct format (with or without 'C' prefix)

2. **Conversion Errors**
   - Check the log output for specific error messages
   - Verify all dependencies are installed correctly
   - Ensure you have write permissions in the output directory

3. **Import Issues**
   - Make sure KiCad paths are set correctly
   - Verify the library files are in the expected locations
   - Check if all necessary files were generated

### Component List Management

1. **Saving Lists**
   - Lists are saved in JSON format
   - Include any relevant comments for future reference
   - Use meaningful filenames for easy identification

2. **Loading Lists**
   - Make sure the JSON file is properly formatted
   - All component numbers should be valid
   - Comments will be preserved when loading

### GUI Usage Tips

1. **Adding Components**
   - You can paste part numbers directly from JLCPCB website
   - Use comments to track component purposes
   - The GUI automatically handles 'C' prefix normalization

2. **Batch Processing**
   - Save commonly used component lists for quick access
   - Use clear comments to identify component purposes
   - Check the log output for conversion progress

3. **Library Organization**
   - Keep related components in separate list files
   - Use meaningful comments for better organization
   - Maintain a consistent naming scheme for saved lists