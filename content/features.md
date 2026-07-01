---
title: Features
description: KiCad Component Manager — library and BOM management for LCSC components
weight: 20
---

A KiCad plugin for downloading and managing LCSC components with integrated BOM management.

## Library Manager

**Component Import**
- Quick import: enter LCSC part number and press Enter
- Bulk import: paste lists or load from file
- CLI support: `python gui2.py -i C1234` or `-I parts.txt`
- Automatic duplicate detection

**Organization**
- ~32 curated KiCAD symbol libraries with intelligent category mapping
- Pre-created libraries eliminate need for KiCAD restart
- Raw LCSC category preserved on symbol for detailed searching
- Non-KiCAD categories automatically filtered

**Download Management**
- Live progress tracking: Symbol, Footprint, STEP, PDF, JLC data
- Individual step retry by clicking cell
- Automatic deduplication of footprints and STEP models
- Smart 3D model offset correction (>50% deviation detected)

**PDF Handling**
- Auto-rejects placeholder PDFs under 10 KB
- Falls back to LCSC product URL when unavailable
- Optional disable via settings

**Library Tools**
- Load and inspect existing libraries
- Delete parts (symbol, footprint, STEP, PDF) in one click
- Recent library locations dropdown
- Full metadata editing and viewing

## BOM Manager

*Requires KiCad plugin installation*

**Component Detection**
- Automatic PCB component reading
- Smart grouping by part with designators and quantities
- Flexible LCSC field detection (supports 8+ field name variants)
- Fallback pattern matching for `Cxxxx` format

**Status & Pricing**
- Real-time part state visualization (symbol, footprint, STEP, PDF, JLC)
- Running total BOM cost
- JLCPCB API integration for pricing and stock
- Selective or bulk price/stock updates

**Part Management**
- Assign LCSC numbers to ungrouped components
- Replace library parts with freshly downloaded versions
- Direct metadata editing in table
- CSV export

## Technical

**Installation**
- KiCad PCM (recommended): self-contained, auto-installs dependencies
- Symlink install: Linux repo users
- Standalone CLI: without KiCAD integration

**Backend**
- easyeda2kicad.py for robust conversion
- LCSC and JLCPCB API integration
- PySide6 GUI with configurable columns and sorting
- Full debug logging to `~/.lcsc2kicad_plugin.log`

**Platform Support**
- Windows, Linux, macOS
- KiCad 10.0+
- Automatic Python detection
