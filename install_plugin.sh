#!/usr/bin/env bash
# Install the LCSC to KiCad plugin by symlinking into KiCad's scripting/plugins directory.
# Re-running this script is safe — it just updates the symlink.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_SRC="$REPO_DIR/kicad_plugin"
PLUGIN_NAME="lcsc2kicad"

# Search newest-first so we pick up the user's active KiCad version
FOUND=""
for ver in 10.0 9.0 8.0 7.0; do
    TARGET_DIR="$HOME/.local/share/kicad/$ver/scripting/plugins"
    if [ -d "$TARGET_DIR" ]; then
        FOUND="$TARGET_DIR"
        echo "Found KiCad $ver scripting plugins: $TARGET_DIR"
        break
    fi
done

if [ -z "$FOUND" ]; then
    echo ""
    echo "ERROR: No KiCad scripting/plugins directory found under ~/.local/share/kicad/"
    echo "Manually symlink or copy '$PLUGIN_SRC' into your KiCad scripting/plugins folder."
    exit 1
fi

LINK="$FOUND/$PLUGIN_NAME"

# Remove old link/directory if present
if [ -L "$LINK" ] || [ -d "$LINK" ]; then
    rm -rf "$LINK"
    echo "Removed existing plugin at $LINK"
fi

ln -s "$PLUGIN_SRC" "$LINK"
echo "Installed: $LINK -> $PLUGIN_SRC"
echo ""
echo "In KiCad, open Tools > Scripting Console and run:"
echo "  import importlib, lcsc2kicad; importlib.reload(lcsc2kicad)"
echo "or simply restart KiCad, then use Tools > External Plugins > LCSC to KiCad Converter."
