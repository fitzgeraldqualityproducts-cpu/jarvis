"""One-time setup for Jarvis's new hearing (Whisper) and voice (Kokoro / ElevenLabs)."""
import json
import sys
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
CONFIG = HERE / "config.json"
MODELS = HERE / "models"
BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {"kokoro-v1.0.onnx": 325532387, "voices-v1.0.bin": 28214398}


def download(name, size):
    dest = MODELS / name
    if dest.exists() and dest.stat().st_size == size:
        print(f"  {name}: already downloaded")
        return
    print(f"  Downloading {name} ({size // 1_000_000} MB)...")
    tmp = dest.with_suffix(".part")
    with urllib.request.urlopen(BASE + name, timeout=60) as r, open(tmp, "wb") as f:
        done = 0
        while True:
            chunk = r.read(1 << 20)
            if not chunk:
                break
            f.write(chunk)
            done += len(chunk)
            print(f"\r    {done * 100 // size}%", end="", flush=True)
    print()
    tmp.replace(dest)


def main():
    cfg = json.loads(CONFIG.read_text())

    print("\n== Natural voice (Kokoro, runs on this computer) ==")
    MODELS.mkdir(exist_ok=True)
    for name, size in FILES.items():
        download(name, size)

    print("\n== Optional: ElevenLabs, the most lifelike voice ==")
    print("ElevenLabs sounds the most human, but needs an account (free tier is about 10")
    print("minutes of speech a month). Get a key at https://elevenlabs.io > Profile > API keys.")
    have = "yes" if cfg.get("elevenlabs_api_key") else "no"
    key = input(f"Paste an ElevenLabs API key, or press Enter to skip (key saved: {have}): ").strip()
    if key:
        cfg["elevenlabs_api_key"] = key
    cfg.setdefault("voice_engine", "auto")
    cfg.setdefault("kokoro_voice", "bm_george")
    cfg.setdefault("speech_engine", "whisper")
    cfg.setdefault("whisper_model", "small.en")
    CONFIG.write_text(json.dumps(cfg, indent=2))

    print("\n== Better hearing (Whisper, runs on this computer) ==")
    print("  Downloading the speech model (about 500 MB the first time)...")
    import ears
    model = ears.whisper_model()
    print("  Ready." if model else "  Whisper couldn't load; Jarvis will use Google speech instead.")

    print("\n== Testing the voice ==")
    import voice
    import types
    sys.modules.setdefault("display", types.SimpleNamespace(log=lambda *a: None, update=lambda **k: None))
    voice.say("Good evening. My new voice is ready, and I can hear you rather better now.", interruptible=False)
    print("\nIf you'd like a different voice, set \"kokoro_voice\" in config.json to bm_george,")
    print("bm_fable, bm_lewis or bm_daniel (or bf_emma for a British woman).")


if __name__ == "__main__":
    main()
