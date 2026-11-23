#!/bin/bash
# Jarvis YouTube business: reads the business channel's latest numbers from YouTube (read-only)
# and shows the report. Nothing is uploaded or changed.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio stats "$@"
echo
read -n 1 -s -r -p "Press any key to close."
