#!/bin/bash
cd "$(dirname "$0")"
PY=.venv/bin/python; [ -x "$PY" ] || PY=python3
"$PY" setup_oura.py
echo; read -n 1 -s -r -p "Press any key to close."
