#!/bin/sh
# Creates the virtual environment and installs everything, on a Mac with Apple Silicon.
# Safe to run again to update.
set -e
cd "$(dirname "$0")"
PYTHON="${PYTHON:-python3.12}"
need() { echo "$1 is missing. Install it with: $2"; exit 1; }
command -v "$PYTHON" >/dev/null || need "Python 3.12" "brew install python@3.12"
"$PYTHON" -c "import tkinter" 2>/dev/null || need "Tk" "brew install python-tk@3.12"
command -v ffmpeg >/dev/null || need "ffmpeg" "brew install ffmpeg"
xcode-select -p >/dev/null 2>&1 || need "The compiler of Apple" "xcode-select --install"
[ -x .venv/bin/python ] || "$PYTHON" -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install torch==2.11.0 torchvision==0.26.0 torchaudio==2.11.0
.venv/bin/python -m pip install --upgrade -r requirements.txt
.venv/bin/python -m pip install --no-deps -e .
echo
echo "Ready. Open window.command, or use .venv/bin/zitto from a terminal."
