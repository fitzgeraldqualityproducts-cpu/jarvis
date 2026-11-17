"""Pick which speaker or audio channel Jarvis talks through (Windows).

For streaming: install the free VB-CABLE (https://vb-audio.com/Cable), pick "CABLE Input" here,
then in OBS add an "Audio Input Capture" source set to "CABLE Output" and turn on
"Monitor and Output" for it, so you still hear him. Run: Choose Jarvis Speaker.bat"""
import json
from pathlib import Path

import win

CONFIG = Path(__file__).with_name("config.json")


def main():
    cfg = json.loads(CONFIG.read_text(encoding="utf-8"))
    names = win.output_devices()
    now = cfg.get("audio_output") or "the normal Windows speaker"
    print(f"Jarvis talks through: {now}\n")
    print("  0. The normal Windows speaker (default)")
    for i, n in enumerate(names, 1):
        print(f"  {i}. {n}")
    cable = [n for n in names if "cable input" in n.lower()]
    if not cable:
        print("\nTip: for his own channel in OBS, install the free VB-CABLE from https://vb-audio.com/Cable,")
        print("restart the PC, then run this again and pick CABLE Input.")
    choice = input("\nType a number and press Enter (just Enter = no change): ").strip()
    if not choice.isdigit() or int(choice) > len(names):
        print("Nothing changed.")
        return
    if choice == "0":
        cfg.pop("audio_output", None)
        print("Jarvis will use the normal Windows speaker.")
    else:
        name = names[int(choice) - 1]
        cfg["audio_output"] = name.split(" (")[0] if name.lower().startswith("cable input") else name
        print(f"Jarvis will talk through: {name}")
        if "cable" in name.lower():
            print("\nNow in OBS:")
            print("  1. Sources > + > Audio Input Capture, name it Jarvis, pick 'CABLE Output'.")
            print("  2. In the Audio Mixer, click the three dots (or gear) on Jarvis > Advanced Audio Properties.")
            print("  3. Set Jarvis's Audio Monitoring to 'Monitor and Output' so you can still hear him.")
    CONFIG.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    print("\nRestart Jarvis to use it.")


if __name__ == "__main__":
    main()
