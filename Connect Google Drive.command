#!/bin/bash
# Jarvis YouTube business: connects your Google Drive so finished Shorts are backed up there
# (Jarvis can only see the files it puts there itself), then runs the first backup.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio drive connect
echo
read -n 1 -s -r -p "Press any key to close."
