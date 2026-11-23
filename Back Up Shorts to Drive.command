#!/bin/bash
# Jarvis YouTube business: copies finished Shorts to Google Drive now, checks every copy, and clears
# Mac copies only once they're on YouTube and safe on Drive. (Jarvis also does this by itself.)
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio drive backup "$@"
echo
read -n 1 -s -r -p "Press any key to close."
