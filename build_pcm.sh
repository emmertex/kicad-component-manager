#!/usr/bin/env bash

set -euo pipefail

VERSION="${1:-1.0.3}"
REPO="$(cd "$(dirname "$0")" && pwd)"
OUT="$REPO/lcsc2kicad-$VERSION.zip"
STAGING="$(mktemp -d)"
PLUGIN_STAGING="$STAGING/plugins"

echo "Building PCM package v$VERSION ..."

# ── Plugin launcher ────────────────────────────────────────────────────────── #
mkdir -p "$PLUGIN_STAGING"
cp "$REPO/kicad_plugin/__init__.py" "$PLUGIN_STAGING/"
[ -f "$REPO/kicad_plugin/icon.png" ] && cp "$REPO/kicad_plugin/icon.png" "$PLUGIN_STAGING/"

# ── GUI and backend ────────────────────────────────────────────────────────── #
cp "$REPO/gui2.py" "$PLUGIN_STAGING/"
cp "$REPO/requirements.txt" "$PLUGIN_STAGING/"

BACKEND_SRC="$REPO/lcsc2kicad-GUI/JLC2KiCadLib"
BACKEND_DST="$PLUGIN_STAGING/lcsc2kicad-GUI/JLC2KiCadLib"

if [ ! -d "$BACKEND_SRC" ]; then
    echo "ERROR: Backend not found at $BACKEND_SRC" >&2
    rm -rf "$STAGING"
    exit 1
fi

# Copy backend, excluding cache and junk files
mkdir -p "$BACKEND_DST"
rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
    "$BACKEND_SRC/" "$BACKEND_DST/" 2>/dev/null \
|| { find "$BACKEND_SRC" -type f ! -name '*.pyc' ! -name '.DS_Store' | while read -r f; do
       rel="${f#$BACKEND_SRC/}"
       dir="$BACKEND_DST/$(dirname "$rel")"
       mkdir -p "$dir"
       cp "$f" "$dir/"
     done; }

# ── PCM metadata ───────────────────────────────────────────────────────────── #
# Stamp the version into a copy of metadata.json
python3 - <<PYEOF
import json, pathlib

meta = json.loads(pathlib.Path("$REPO/kicad_plugin/metadata.json").read_text())
meta["versions"][0]["version"] = "$VERSION"
pathlib.Path("$STAGING/metadata.json").write_text(json.dumps(meta, indent=2))
PYEOF

# ── Zip ────────────────────────────────────────────────────────────────────── #
rm -f "$OUT"
if command -v zip &>/dev/null; then
    (cd "$STAGING" && zip -r "$OUT" metadata.json plugins/)
else
    python3 -c "
import zipfile, pathlib, os
staging = pathlib.Path('$STAGING')
with zipfile.ZipFile('$OUT', 'w', zipfile.ZIP_DEFLATED) as zf:
    for f in staging.rglob('*'):
        if f.is_file():
            zf.write(f, f.relative_to(staging))
"
fi
rm -rf "$STAGING"

SIZE=$(stat -c %s "$OUT")
HASH=$(sha256sum "$OUT" | awk '{print $1}')

echo ""
echo "Package written to: $OUT"
echo ""
echo "To install in KiCad:"
echo "  Plugin and Content Manager → Install from File → $OUT"
echo ""
echo "Size of package: $SIZE bytes"
echo "Hash of package: $HASH"
