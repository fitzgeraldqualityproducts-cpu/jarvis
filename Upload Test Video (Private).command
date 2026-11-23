#!/bin/bash
# Jarvis YouTube business: uploads the newest finished test Short to the business channel as PRIVATE
# (only you can see it), after asking you. Your other channel is never touched.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio upload "$@"
echo
read -n 1 -s -r -p "Press any key to close."
