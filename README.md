# LCSC to KiCad Library Converter

A GUI for converting LCSC/JLCPCB components into KiCad symbols, footprints, and 3D models.

## Features

- Add components by LCSC part number (with or without `C` prefix)
- Annotate components with comments
- Save/load component lists as JSON
- Generates:
  - Symbols (`.kicad_sym`)
  - Footprints (`.pretty`)
  - 3D models (STEP)
  - Local PDF datasheets (optional)
- Settings cached between sessions

## Installation & Usage

```bash
git clone https://github.com/emmertex/lcsc2kicad-GUI
cd lcsc2kicad-GUI
./run.sh
```

`run.sh` creates a virtualenv, installs dependencies, and launches the GUI. On Windows without WSL, run manually:

```bash
python -m venv venv
source venv/bin/activate   # Windows: venv\Scripts\activate
pip install -r requirements.txt
python gui.py
```

## Output Structure

```
output_directory/
├── footprint.pretty/
│   ├── packages3d/
│   │   └── component.step
│   └── component.kicad_mod
├── symbol/
│   └── components.kicad_sym
└── pdf/
    └── component.pdf
```

## Importing into KiCad

- **Symbols:** Preferences → Manage Symbol Libraries → add `symbol/components.kicad_sym`
- **Footprints:** Preferences → Manage Footprint Libraries → add the `.pretty` folder
- **3D models:** Linked automatically if the directory structure is maintained

## Troubleshooting

- **Component not found:** Verify the LCSC part number exists on JLCPCB's website
- **Conversion errors:** Check the log output; ensure write permissions on the output directory
- **Import issues:** Verify KiCad library paths point to the generated files

## Credits

Built upon these projects:

- [JLC2KiCad_lib](https://github.com/TousstNicolas/JLC2KiCad_lib) by TousstNicolas — core symbol/footprint/3D model conversion (MIT)
- [lcsc2kicad](https://github.com/DasBasti/lcsc2kicad) by DasBasti — LCSC component handling approach (MIT)
- [lcsc2kicad-GUI](https://github.com/milutintech/lcsc2kicad-GUI) by milutintech — original GUI foundation (MIT)

## License

MIT
