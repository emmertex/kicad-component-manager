### v1.1 - KiCAD Library Managenent

### v1.2.0

- JLCPCB API
  - Categories from JLC
  - Prices and Stock Levels
  - API Key in Settings (https://api.jlcpcb.com)
- Categories
  - Use sym-lib-table for KiCAD
  - Categories (file names) from JLC with fallback to LCSC
  - Optional prefix in Settings
- Minor UI tweaks
- Library split bug resolved

#### v1.1.2
- Correctly identify top-level symbols by analyzing the minimum indentation level in the file.
- Use a more robust "reverse search" to find the exact closing parenthesis of the main symbol block.
- Automatically detect and match the file's indentation style (tabs vs. spaces) for any new properties added.

#### v1.1.1
- Fix Attribute Loading

#### v1.1.0
- Improved Library Table
  - Turn on an off columns in settings
  - Drag to resize
  - Click to sort
- Edit Metadata
  - Click on an part to see metadata
  - Open LCSC or PDF directly
  - Scrape LCSC for new or missing data
  - Edit metadata directly
- More Metadata
  - Scraping and storing key attributes
- Minor Chnages
  - Convert button now called "Import"
  - Settings Dialogue for cleaner UI
  - Log and Metadata window split resizeable
  - Delete is safer throught tighter regex

### v1.0 - New Interface and KiCAD Integration

#### v1.0.6
- Fixed a bug where PDF links were not stored correctly

#### v1.0.5
- Use KiCAD's built in python
  - So can work without python being installed
  - Fixes weird issues with Windows

#### v1.0.2 -> v1.0.4
- Add support for KiCAD PCM

#### v1.0.1
- Support External PDFs (ie, not hosted on LCSC)

#### v1.0.0
- New GUI
- Load Library into table
- Visual Progress Indicator
- Retry Failed Downloads
- Delete from Library

### Forked by Emmertex
- Minor bug fixes
- Scrape more data
- Download PDF
- Cache settings

### Origional
- Download Symbol
- Download Footprint
- Download STEP
