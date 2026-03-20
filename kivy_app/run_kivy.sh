#!/bin/bash
# Launcher for Kivy RTK app
# Usage: ./run_kivy.sh

set -e

# Ensure we run from the app directory
cd "$(dirname "$0")"

WORKSPACE_ROOT="$(cd .. && pwd)"

# Prefer user's XAUTHORITY if set, otherwise default
XAUTH=${XAUTHORITY:-/home/berries/.Xauthority}
export XAUTHORITY="$XAUTH"
export DISPLAY=${DISPLAY:-:0}

# Activate venv if present (workspace-level first)
if [ -f "$WORKSPACE_ROOT/.venv/bin/activate" ]; then
  # shellcheck disable=SC1091
  source "$WORKSPACE_ROOT/.venv/bin/activate"
elif [ -f .venv/bin/activate ]; then
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

echo "Starting Kivy RTK app (DISPLAY=$DISPLAY, XAUTHORITY=$XAUTH)"
python3 app.py
