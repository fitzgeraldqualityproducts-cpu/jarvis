#!/bin/bash
# Jarvis YouTube business: switches the studio OFF on this Mac and packs it into "Jarvis Studio Handover.zip"
# on the Desktop, for "Take Over YouTube Studio" on the PC. The YouTube sign-in is not in the file.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio handover export
echo
read -n 1 -s -r -p "Press any key to close."
