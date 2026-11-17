"""Tells Jarvis your Twitch channel for stream mode. Run: python setup_twitch.py"""
import twitch

print("Jarvis stream mode reads your Twitch chat out loud. No Twitch login is needed,")
print("just your channel name (the part after twitch.tv/).\n")
current = twitch.channel()
if current:
    print(f"Right now it's set to: {current}")
name = input("Your Twitch channel name: ").strip()
if name:
    name = twitch.set_channel(name)
    print(f"\nSaved: {name}. Restart Jarvis, then say \"Jarvis, stream mode\" when you go live.")
    print("Say \"stop stream mode\" when you're done.")
    import sys
    if sys.platform == "win32":
        print("\nSo viewers can hear him: OBS's \"Desktop Audio\" source already sends your PC's sound")
        print("to the stream. Make sure it's turned on in OBS's Audio Mixer.")
    else:
        print("\nSo viewers can hear him: in OBS add a \"macOS Audio Capture\" source")
        print("(or Display Capture with audio on) so your Mac's sound goes into the stream.")
else:
    print("Nothing changed.")
input("\nPress Enter to close.")
