#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")" || exit 1

# --- Venv setup ----------------------------------------------------------- #
VENV_DIR="venv"

if [ ! -d "$VENV_DIR" ]; then
    echo "Creating Python virtual environment in $VENV_DIR ..."
    python3 -m venv "$VENV_DIR"
fi

source "$VENV_DIR/bin/activate"

# --- Upgrade pip first (cheap, fast) -------------------------------------- #
echo "Checking/upgrading pip ..."
pip install --upgrade pip --quiet

# --- Install / verify requirements ---------------------------------------- #
if [ ! -f "requirements.txt" ]; then
    echo "ERROR: requirements.txt not found in $(pwd)" >&2
    exit 1
fi

echo "Verifying all requirements are satisfied ..."
# `pip check` reports any dependency conflicts.
# We also do a quick install -y to ensure every package is present;
# pip is smart enough to skip already-installed packages, so this is fast.
pip install -r requirements.txt --quiet

if ! pip check 2>/dev/null; then
    echo "" >&2
    echo "ERROR: Dependency conflicts detected!" >&2
    echo "Run 'pip check' for details." >&2
    exit 1
fi

echo "All dependencies satisfied. Launching GUI ..."
python gui.py
