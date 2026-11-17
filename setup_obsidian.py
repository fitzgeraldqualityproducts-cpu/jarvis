"""Connect Jarvis to your Obsidian vault. Run: .venv/bin/python setup_obsidian.py"""
import json
import os
from pathlib import Path

import obsidian

CONFIG = obsidian.CONFIG


def main():
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    vaults = obsidian.known_vaults()
    print("== Connect Jarvis to Obsidian ==\n")
    windows = os.name == "nt"
    installed = (Path(os.environ.get("LOCALAPPDATA", "")) / "Programs/Obsidian").exists() if windows \
        else Path("/Applications/Obsidian.app").exists()
    if not vaults and not installed:
        print("Obsidian isn't installed on this computer yet.")
        print("1. Download it free from https://obsidian.md (opening it now).")
        print("2. Install it, open it, and click 'Create new vault'." if windows else
              "2. Drag Obsidian into Applications, open it, and click 'Create new vault'.")
        print("3. Turn on Settings > Core plugins > Daily notes (optional, it's on by default).")
        print("4. Run Connect Obsidian again.")
        import webbrowser
        webbrowser.open("https://obsidian.md")
        return
    if vaults:
        print("Vaults I found:")
        for i, v in enumerate(vaults, 1):
            print(f"  {i}. {v}")
        choice = input("\nPick a number, or paste a different folder path (Enter = 1): ").strip()
    else:
        choice = input("Paste your vault folder path (drag the folder into this window): ").strip()
    if not choice and vaults:
        path = vaults[0]
    elif choice.isdigit() and vaults and 1 <= int(choice) <= len(vaults):
        path = vaults[int(choice) - 1]
    else:
        path = Path(os.path.expanduser(choice.strip("'\" ").replace("\\ ", " ") if os.name != "nt" else choice.strip("'\" ")))
    if not path.is_dir():
        print(f"\nI can't find a folder at {path}. Nothing changed.")
        return
    cfg["obsidian_vault"] = str(path)
    CONFIG.write_text(json.dumps(cfg, indent=2))
    print(f"\nConnected to {path}.")
    print(f"Today's daily note: {obsidian.daily_note(create=False)}")
    print(obsidian.todos_summary())
    print("\nRestart Jarvis, then try: 'Jarvis, note that...', 'what's on my to-do list',")
    print("or 'what did I write about ...'.")


if __name__ == "__main__":
    main()
