#!/bin/bash
# Upgrades Jarvis to a newer Python and installs his new hearing and voice.
# Your settings, memory and Spotify login are kept.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$PATH"
fail() { echo; echo "Something went wrong. Copy everything above and send it to Claude."; read -p "Press Enter to close."; exit 1; }

echo "== Getting a newer Python (Jarvis's voice needs Python 3.10 or later) =="
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh || fail
  export PATH="$HOME/.local/bin:$PATH"
fi
rm -rf .venv-new
uv venv --seed --python 3.12 .venv-new || fail

echo
echo "== Installing Jarvis's parts (a few minutes) =="
uv pip install --python .venv-new/bin/python -r requirements.txt || fail

echo
.venv-new/bin/python setup_voice.py || fail

echo
echo "== Switching Jarvis over to the new setup =="
if [ -d .venv ]; then mv .venv ".venv-old-$(date +%Y%m%d%H%M%S)"; fi
mv .venv-new .venv || fail

echo
echo "== Re-checking permissions and the wake word =="
.venv/bin/python setup_extras.py

echo
echo "All done. Start Jarvis with the Start Jarvis file on your Desktop."
read -p "Press Enter to close."
