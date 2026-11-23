#!/bin/bash
cd "$(dirname "$0")"
echo "Installing Jarvis's local brain (Ollama + a small model + Chroma). Free, one time, about 3 GB."
echo "Jarvis keeps using Claude for anything that needs real knowledge."
echo
.venv/bin/python local_brain.py install
echo; read -n 1 -s -r -p "Press any key to close."
