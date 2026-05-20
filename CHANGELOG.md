## v1.3 - KiCAD Library Managenent

### v1.3.0
** Features **
- Code Cleanup
  - Remove dead code
  - Break main GUI application into workers and smaller parts
- Deduplication of Footprints and STEP Models
  - Check if footprints are identical, if so, then reuse the existing footprint 
  - Check if STEP models are identical, if so, then reuse the existing model
- 3D Model Offsets
  - Some models have unusual offsets, often minor, some times severy hundred mm wrong. 
  - Detect an incorrect offset by it being more that 50% of the model's length on that particular axis
  - Almost all EasyEDA models have 0,0,0 offsets, so when known to be erroneous, adjust to 0,0,0
  - Tested on several known bad model imports, and works well.
  - This cannot be 100%, but will be a huge improvement over the default behavior

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
- Add support for EasyEDA Footprints where SMD is on more than 1 layer
  - Example component C2844246

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
