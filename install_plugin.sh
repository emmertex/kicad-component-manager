#!/usr/bin/env bash
# Install the plugin by symlinking it into each KiCad version's plugin directory.
# Re-running this script is safe — it just updates the symlink.

set -euo pipefail

REPO_DIR="$(cd "$(dirname "$0")" && pwd)"
PLUGIN_SRC="$REPO_DIR/kicad_plugin"
PLUGIN_NAME="lcsc2kicad"

# Install into every KiCad version found. KiCad 10.99+ dropped the SWIG
# ActionPlugin API and loads IPC plugins (plugin.json) from plugins/ instead.
INSTALLED=0
for verdir in "$HOME"/.local/share/kicad/*/; do
    ver="$(basename "$verdir")"
    [[ "$ver" =~ ^[0-9]+\.[0-9]+$ ]] || continue
    major="${ver%%.*}"; minor="${ver#*.}"
    if (( major > 10 || (major == 10 && minor >= 99) )); then
        TARGET_DIR="${verdir}plugins"
        mkdir -p "$TARGET_DIR"
    else
        TARGET_DIR="${verdir}scripting/plugins"
        [ -d "$TARGET_DIR" ] || continue
    fi
    LINK="$TARGET_DIR/$PLUGIN_NAME"
    if [ -L "$LINK" ] || [ -d "$LINK" ]; then
        rm -rf "$LINK"
        echo "Removed existing plugin at $LINK"
    fi
    ln -s "$PLUGIN_SRC" "$LINK"
    echo "KiCad $ver: $LINK -> $PLUGIN_SRC"
    INSTALLED=1
done

if [ "$INSTALLED" -eq 0 ]; then
    echo ""
    echo "ERROR: No KiCad version directory found under ~/.local/share/kicad/"
    echo "Manually symlink or copy '$PLUGIN_SRC' into your KiCad plugins folder."
    exit 1
fi

echo ""
echo "Restart KiCad. KiCad 10.0 and older: Tools > External Plugins."
echo "KiCad 10.99+: enable Preferences > Plugins > Enable KiCad API, then use the"
echo "toolbar button (KiCad builds the plugin's Python environment on first load)."
