#!/bin/bash
# Jarvis YouTube business: opens the live dashboard that shows every Short moving from idea to live.
# Read-only: it only shows what's happening. If Jarvis isn't running, this window keeps the page
# going; close it when you're done watching.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
.venv/bin/python -m studio dashboard "$@"
