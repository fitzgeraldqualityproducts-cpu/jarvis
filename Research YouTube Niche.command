#!/bin/bash
# Jarvis YouTube business: researches niches with YouTube's free API and your Claude plan,
# picks one, and opens the Top 10 report. Nothing is uploaded and nothing costs money.
cd "$(dirname "$0")"
export PATH="$HOME/.local/bin:$HOME/.npm-global/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"
if [ ! -s studio_data/secrets/youtube_api_key ]; then
  echo "First, a free read-only YouTube key (about 5 minutes, no credit card, never add billing):"
  echo "  1. Open https://console.cloud.google.com and sign in (any Google account)."
  echo "  2. Create a project called Jarvis Business (top bar > project picker > New project)."
  echo "  3. APIs & Services > Library > YouTube Data API v3 > Enable."
  echo "  4. APIs & Services > Credentials > Create credentials > API key. Copy it."
  echo "     (Optional: Edit the key > API restrictions > YouTube Data API v3.)"
  echo
  open "https://console.cloud.google.com/apis/library/youtube.googleapis.com"
  .venv/bin/python -m studio add-key || exit 1
fi
.venv/bin/python -m studio research "$@" && open "$(ls -td studio_data/research/*/ | head -1)report.html"
echo
read -n 1 -s -r -p "Done. Press any key to close."
