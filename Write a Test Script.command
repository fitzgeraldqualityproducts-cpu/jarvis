#!/bin/bash
# Jarvis YouTube business: writes one real script for the best idea (5 hooks, fact-check with sources,
# every calculation re-run), then records the narration. Shows you the result. Never uploads anything.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio script "$@"
echo
read -n 1 -s -r -p "Press any key to close."
