#!/bin/bash
# Gives Jarvis his brain: finds or installs Claude Code, logs you in, tests it, starts Jarvis.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"

if ! .venv/bin/python -c 'import brain,sys; sys.exit(0 if brain.find_claude() else 1)'; then
  echo "Claude Code isn't installed yet. Installing it now (this can take a few minutes)..."
  mkdir -p "$HOME/.claude/downloads"
  if command -v npm >/dev/null 2>&1; then
    echo "Using npm..."
    npm install -g @anthropic-ai/claude-code || {
      echo "npm couldn't install globally; trying a user folder instead..."
      mkdir -p "$HOME/.npm-global"
      npm config set prefix "$HOME/.npm-global"
      npm install -g @anthropic-ai/claude-code
    }
  fi
  if ! .venv/bin/python -c 'import brain,sys; sys.exit(0 if brain.find_claude() else 1)'; then
    echo "Trying the official installer (2 attempts)..."
    for i in 1 2; do
      rm -rf "$HOME/.claude/downloads/"*
      curl -fSL --retry 3 https://claude.ai/install.sh | bash && break
    done
  fi
  if ! .venv/bin/python -c 'import brain,sys; sys.exit(0 if brain.find_claude() else 1)' && command -v brew >/dev/null 2>&1; then
    echo "Trying Homebrew..."
    brew install --cask claude-code
  fi
  export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:$PATH"
fi

CLAUDE=$(.venv/bin/python -c 'import brain; print(brain.find_claude() or "")')
if [ -z "$CLAUDE" ]; then
  echo "Install didn't finish. Copy everything above and send it to Claude."
  read -p "Press Enter to close."
  exit 1
fi

echo
echo "Checking you're logged in to Claude Code..."
if ! "$CLAUDE" -p "Reply with just OK" >/dev/null 2>&1; then
  echo
  echo "You need to log in once. Claude Code will open now:"
  echo "  1. Follow the login steps (use your paid Claude account)."
  echo "  2. When you see the prompt box, type /exit and press Enter."
  echo
  read -p "Press Enter to start..."
  "$CLAUDE"
fi

echo
.venv/bin/python brain.py
echo
echo "Starting Jarvis..."
.venv/bin/python jarvis.py
