#!/usr/bin/env bash
# Factory Map website: run this (macOS: double-click start-mac.command) to set up the first time and start.
# Keep the window open while you play; Ctrl+C stops it.
set -e
cd "$(dirname "$0")/web"
command -v python3 >/dev/null || { echo "Python 3 is not installed. macOS: get it from https://www.python.org/downloads/  Linux: install python3 with your package manager."; exit 1; }
if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then   # missing, or left half-made by an earlier failed try
  echo "First start: setting up, this takes a minute..."
  rm -rf .venv
  python3 -m venv .venv || { rm -rf .venv; echo "Couldn't create the Python environment. On Ubuntu/Debian run: sudo apt install python3-venv  then start this again."; exit 1; }
fi
.venv/bin/python -m pip install -q --disable-pip-version-check -r requirements.txt
[ -f site.json ] || cp site.example.json site.json
OPEN_BROWSER=1 exec .venv/bin/python server.py
