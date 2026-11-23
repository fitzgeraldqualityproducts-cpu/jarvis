#!/bin/bash
# Jarvis YouTube business: finds new Short ideas for the channel (web research on your Claude plan,
# YouTube's official API for what's popular), checks the sources, scores them and lists the best.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio ideas "$@"
echo
read -n 1 -s -r -p "Press any key to close."
