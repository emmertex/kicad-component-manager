#!/usr/bin/env bash

set -euo pipefail

REPO="$(cd "$(dirname "$0")" && pwd)"
_META_VERSION="$(python3 -c "import json; print(json.load(open('$REPO/kicad_plugin/metadata.json'))['versions'][0]['version'])")"
VERSION="${1:-$_META_VERSION}"
OUT="$REPO/kicad-component-manager-$VERSION.zip"
STAGING="$(mktemp -d)"
PLUGIN_STAGING="$STAGING/plugins"

echo "Building PCM package v$VERSION ..."

# ── Plugin launcher ────────────────────────────────────────────────────────── #
mkdir -p "$PLUGIN_STAGING"
cp "$REPO/kicad_plugin/__init__.py" "$PLUGIN_STAGING/"
[ -f "$REPO/kicad_plugin/icon.png" ] && cp "$REPO/kicad_plugin/icon.png" "$PLUGIN_STAGING/"

# ── GUI and backend ────────────────────────────────────────────────────────── #
cp "$REPO/manager.py" "$PLUGIN_STAGING/"
cp "$REPO/requirements.txt" "$PLUGIN_STAGING/"

# Copy gui and lib folders, excluding cache and junk files
for dir in gui lib; do
    SRC="$REPO/$dir"
    DST="$PLUGIN_STAGING/$dir"
    mkdir -p "$DST"
    rsync -a --exclude='__pycache__' --exclude='*.pyc' --exclude='.DS_Store' \
        "$SRC/" "$DST/" 2>/dev/null \
    || { find "$SRC" -type f ! -name '*.pyc' ! -name '.DS_Store' | while read -r f; do
           rel="${f#$SRC/}"
           target="$DST/$(dirname "$rel")"
           mkdir -p "$target"
           cp "$f" "$target/"
         done; }
done

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
