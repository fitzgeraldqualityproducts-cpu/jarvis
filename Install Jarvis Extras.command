#!/bin/bash
# Installs everything Jarvis's new abilities need, then walks through permissions.
cd "$(dirname "$0")"
echo "Installing Jarvis's extra abilities. This can take a few minutes..."
export PATH="$HOME/.local/bin:$PATH"
if command -v uv >/dev/null 2>&1; then INSTALL="uv pip install --python .venv/bin/python"; else INSTALL=".venv/bin/python -m pip install"; fi
$INSTALL -r requirements.txt || { echo; echo "Install failed. Copy everything above and send it to Claude."; read -p "Press Enter to close."; exit 1; }
.venv/bin/python setup_extras.py
read -p "Press Enter to close."
