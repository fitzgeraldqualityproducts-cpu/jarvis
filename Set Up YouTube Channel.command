#!/bin/bash
# Jarvis YouTube business: channel name ideas, logo, banner and description, then how to create the channel.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio brand
echo
read -n 1 -s -r -p "Press any key to close."
