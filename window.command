#!/bin/sh
# Opens the window, on a Mac.
cd "$(dirname "$0")"
if [ ! -x .venv/bin/python ]; then
    echo "Run setup.sh first."
    exit 1
fi
exec .venv/bin/python -m zitto
