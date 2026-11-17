#!/bin/bash
# Started automatically at login: runs Jarvis with just the menu bar icon (no browser window).
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python jarvis.py --no-display
