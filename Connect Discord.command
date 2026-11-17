#!/bin/bash
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
.venv/bin/python setup_discord.py
echo; read -n 1 -s -r -p "Press any key to close."
