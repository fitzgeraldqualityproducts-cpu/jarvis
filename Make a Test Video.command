#!/bin/bash
# Jarvis YouTube business: makes one finished test Short (script, fact-check, narration, visuals,
# captions, render and the quality checks) and plays it. Never uploads anything.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio video "$@"
echo
read -n 1 -s -r -p "Press any key to close."
