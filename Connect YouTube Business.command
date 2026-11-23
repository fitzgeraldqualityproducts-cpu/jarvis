#!/bin/bash
# Jarvis YouTube business: signs in to the NEW business channel and locks Jarvis to it (your existing channel stays untouched).
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio connect
echo
read -n 1 -s -r -p "Press any key to close."
