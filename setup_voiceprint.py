"""Teach Jarvis your voice (one time, about 2 minutes).
Run it with the "Teach Jarvis My Voice" file in this folder."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
CONFIG = HERE / "config.json"

SENTENCES = [
    "Hey Jarvis, what's the weather looking like today?",
    "Play some music on Spotify and turn the volume up.",
    "Remind me to call my mom tomorrow at five in the afternoon.",
    "What's on my calendar this week? Anything important?",
    "Set a timer for ten minutes, then open YouTube for me.",
    "The quick brown fox jumps over the lazy dog, twice in a row.",
]
FRIEND_SENTENCES = SENTENCES[:4]


def say(text=""):
    print(text, flush=True)


def ask(prompt):
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def install():
    try:
        import sherpa_onnx  # noqa: F401
        return True
    except ImportError:
        pass
    say("== Installing the voice recognition part (about a minute) ==")
    pkgs = ["sherpa-onnx"]
    uv = shutil.which("uv") or next((p for p in [Path.home() / ".local/bin/uv", Path.home() / ".local/bin/uv.exe"]
                                     if p.exists()), None)
    cmds = ([[str(uv), "pip", "install", "--python", sys.executable] + pkgs] if uv else []) + \
        [[sys.executable, "-m", "pip", "install", "--upgrade"] + pkgs]
    for cmd in cmds:
        try:
            if subprocess.run(cmd).returncode == 0:
                return True
        except OSError:
            continue
    say("Installing failed. Copy everything above and send it to Claude.")
    return False


def record_sentences(sentences):
    import ears
    clips = []
    for i, line in enumerate(sentences, 1):
        while True:
            ask(f"\n[{i}/{len(sentences)}] Press Enter, then read this out loud in your normal voice:\n\n"
                f"      \"{line}\"\n")
            say("      (listening...)")
            audio = ears.record_phrase(start_timeout=8, max_seconds=12, silence_seconds=1.2)
            if audio is not None and len(audio) > ears.RATE:
                say("      Got it.")
                clips.append(audio)
                break
            say("      I didn't hear that. Let's try that one again (a bit louder or closer).")
    return clips


def teach(name, owner):
    import speaker
    sentences = SENTENCES if owner else FRIEND_SENTENCES
    say(f"\nI'll show you {len(sentences)} short sentences. Read each one when it says listening.")
    clips = record_sentences(sentences)
    ok, msg = speaker.enroll(name, clips, owner=owner)
    say("\n" + msg)
    return ok


def test(name):
    import ears
    import speaker
    ask("\nQuick test: press Enter and say anything you like.")
    say("      (listening...)")
    audio = ears.record_phrase(start_timeout=8, max_seconds=10)
    if audio is None:
        say("      Didn't hear anything, skipping the test.")
        return
    who = speaker.identify(audio)
    if who["owner"]:
        say(f"      That's you, {name}! (match {who['score']:.0%})")
    else:
        say(f"      Hmm, that didn't sound enough like you (match {who['score']:.0%}). "
            "Run this again in a quiet room and it'll get it.")


def set_group_mode(on):
    cfg = json.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    cfg["group_mode"] = on
    CONFIG.write_text(json.dumps(cfg, indent=2))


def add_friends():
    while True:
        name = ask("\nWant Jarvis to know a friend by name? Type their name, or just press Enter to finish: ")
        if not name:
            return
        name = name.strip().title()
        say(f"OK, hand the mic to {name}.")
        teach(name, owner=False)


def main():
    say("Let's teach Jarvis your voice, so he can tell you apart from your friends.\n")
    if not install():
        return
    import speaker
    if not speaker.download_model(say):
        say("Copy everything above and send it to Claude.")
        return
    if speaker.model() is None:
        say("The voice model didn't load. Copy everything above and send it to Claude.")
        return
    if speaker.enrolled():
        owner = speaker.owner_name()
        on = speaker.group_mode()
        friends = speaker.friends()
        say(f"Jarvis already knows {owner}'s voice" + (f", and {', '.join(friends)}." if friends else "."))
        say(f"Group chat mode is {'ON' if on else 'OFF'}.\n")
        say(f"  1. Re-teach {owner}'s voice")
        say("  2. Add a friend's voice")
        say("  3. Remove a friend's voice")
        say(f"  4. Turn group chat mode {'off' if on else 'on'}")
        say("  5. Test it")
        choice = ask("\nType a number (or press Enter to close): ")
        if choice == "1":
            if teach(owner, owner=True):
                test(owner)
        elif choice == "2":
            add_friends()
        elif choice == "3":
            name = ask(f"Whose voice should Jarvis forget? ({', '.join(friends) or 'none saved'}): ").title()
            say("Done." if speaker.remove(name) else "I don't have that name saved.")
        elif choice == "4":
            set_group_mode(not on)
            say(f"Group chat mode is now {'OFF' if on else 'ON'}. Restart Jarvis to be safe.")
        elif choice == "5":
            test(owner)
        return
    name = ask("What's your first name? (press Enter for Connor): ").strip().title() or "Connor"
    if not teach(name, owner=True):
        say("Let's try again another time. Run this file again whenever you like.")
        return
    test(name)
    set_group_mode(True)
    add_friends()
    say(f"\nAll done! Group chat mode is on. Restart Jarvis, and he'll know when it's {name} talking.")
    say("Friends can still chat, play music, set timers and ask questions, but your notes, memory,")
    say("calendar, reminders and files are for you only. Say \"group chat mode off\" any time to turn it off.")


if __name__ == "__main__":
    main()
