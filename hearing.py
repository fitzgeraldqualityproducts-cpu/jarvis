"""Better hearing: upgrades Jarvis's speech recognition to Whisper large-v3-turbo, a bigger free
model that runs on this computer, but only if it's still quick here and there's room for it.

  - On an Apple Silicon Mac it uses MLX (the Mac's graphics chip), which runs turbo fast.
    The MLX parts are installed the first time (about 0.7 GB) plus the model (about 1.6 GB).
  - Anywhere else (Windows, Intel Macs) it uses faster-whisper on the processor (about 1.6 GB).

It needs 6 GB free (MLX) or 5 GB free before it downloads anything. Once it's downloaded, it times
the new model on something you actually said. If the new model is slower than "hearing_max_seconds"
(1.6 s by default, or the old model's time plus 0.6 s), he keeps the old small.en model and deletes
the download to give the space back.

  "how's your hearing"        what he's using and why
  "upgrade your hearing"      try again now (e.g. after freeing up space)
  "use your old hearing"      go back to small.en for good
Config: "hearing_upgrade": false turns this off. Setting "whisper_model" to anything other than
small.en or auto (e.g. "medium.en") also leaves it alone."""
import json
import platform
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
MODELS = HERE / "models"
STATE = HERE / ".hearing.json"
MLX_REPO = "mlx-community/whisper-large-v3-turbo"
MLX_DIR = MODELS / "whisper-turbo-mlx"
CT2_NAME = "large-v3-turbo"
CT2_DIR = MODELS / "whisper"
NEED_GB = {"mlx": 6.0, "ct2": 5.0}
_lock = threading.Lock()
_notes = []


def _cfg():
    try:
        return json.loads((HERE / "config.json").read_text())
    except Exception:
        return {}


def state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {}


def _save(st):
    STATE.write_text(json.dumps(st, indent=2))


def automatic():
    """True if Jarvis may choose his own speech model."""
    cfg = _cfg()
    return cfg.get("hearing_upgrade", True) and cfg.get("whisper_model", "small.en") in ("small.en", "auto", "")


def apple_silicon():
    return sys.platform == "darwin" and platform.machine() == "arm64"


def _engine():
    return "mlx" if apple_silicon() else "ct2"


# ---------------- the turbo model, behind the same interface ears.py uses ----------------

class _Seg:
    def __init__(self, d):
        self.text = d.get("text", "")
        self.avg_logprob = float(d.get("avg_logprob", 0.0))
        self.no_speech_prob = float(d.get("no_speech_prob", 0.0))


class MlxWhisper:
    """mlx-whisper made to look like a faster-whisper model (transcribe -> segments, info)."""

    def __init__(self, path):
        import mlx_whisper
        self.path, self.mlx = str(path), mlx_whisper
        self.name = "large-v3-turbo (MLX)"

    def transcribe(self, audio, language="en", initial_prompt=None, condition_on_previous_text=False, **_):
        out = self.mlx.transcribe(audio, path_or_hf_repo=self.path, language=language, initial_prompt=initial_prompt,
                                  condition_on_previous_text=condition_on_previous_text, verbose=None)
        return [_Seg(s) for s in out.get("segments", [])], out


def load(st=None):
    """The upgraded model if he's switched to it, else None (ears.py then uses small.en)."""
    st = st or state()
    if not automatic() or st.get("status") != "upgraded":
        return None
    try:
        if st.get("engine") == "mlx":
            return MlxWhisper(MLX_DIR)
        from faster_whisper import WhisperModel
        model = WhisperModel(CT2_NAME, device="cpu", compute_type="int8", download_root=str(CT2_DIR))
        model.name = CT2_NAME
        return model
    except Exception as e:
        print(f"(better hearing didn't load, using small.en: {str(e)[:150]})")
        return None


# ---------------- upgrading ----------------

def _free_gb():
    MODELS.mkdir(exist_ok=True)
    return shutil.disk_usage(MODELS).free / 1e9


def _pip(*packages):
    home = Path.home()
    uv = shutil.which("uv") or next((str(p) for p in (home / ".local/bin/uv", home / ".cargo/bin/uv") if p.exists()), None)
    cmd = [uv, "pip", "install", "--python", sys.executable, *packages] if uv else \
        [sys.executable, "-m", "pip", "install", "-q", *packages]
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip()[-300:])


def _download(engine):
    if engine == "mlx":
        try:
            import mlx_whisper  # noqa: F401
        except ImportError:
            print("(better hearing: installing MLX speech parts)")
            _pip("mlx-whisper")
        from huggingface_hub import snapshot_download
        snapshot_download(MLX_REPO, local_dir=str(MLX_DIR))
        return MlxWhisper(MLX_DIR)
    from faster_whisper import WhisperModel
    model = WhisperModel(CT2_NAME, device="cpu", compute_type="int8", download_root=str(CT2_DIR))
    model.name = CT2_NAME
    return model


def _time(model, audio):
    import numpy as np
    import ears
    kw = dict(language="en", beam_size=5, initial_prompt=ears._vocabulary(), condition_on_previous_text=False)
    clip = audio.astype(np.float32) / 32768.0
    list(model.transcribe(clip, **kw)[0])          # warm up
    start = time.time()
    segs = list(model.transcribe(clip, **kw)[0])
    return time.time() - start, " ".join(s.text for s in segs).strip()


def _remove_download(engine):
    shutil.rmtree(MLX_DIR if engine == "mlx" else CT2_DIR, ignore_errors=True)


def upgrade(sample=None, force=False):
    """Tries the bigger model. Returns a sentence saying what happened. Slow (a few minutes)."""
    if not _lock.acquire(blocking=False):
        return "I'm already working on my hearing."
    try:
        return _upgrade(sample, force)
    except Exception as e:
        print(f"(better hearing failed: {e})")
        st = state()
        st.update(status="failed", t=time.time(), why=str(e)[:200])
        _save(st)
        return "I couldn't set up the better hearing this time. I'm still using my usual ears."
    finally:
        _lock.release()


def _upgrade(sample, force):
    import ears
    st = state()
    if st.get("status") == "upgraded" and not force:
        return "I'm already on my best hearing."
    engine = _engine()
    need = NEED_GB[engine]
    free = _free_gb()
    if free < need and not (CT2_DIR.exists() or MLX_DIR.exists()):
        st.update(status="no_space", t=time.time(), free_gb=round(free, 1), need_gb=need)
        _save(st)
        return (f"I'd hear you better with a bigger speech model, but there's only {free:.0f} gigabytes free and "
                f"I need about {need:.0f}. Free up some space and say upgrade your hearing.")
    if sample is None:
        sample = ears.LAST_AUDIO.get("audio")
    if sample is None:
        return "Say something to me first, so I can test the new hearing on your voice."
    print(f"(better hearing: downloading large-v3-turbo via {engine}, {free:.0f} GB free)")
    st.update(status="downloading", t=time.time(), engine=engine)
    _save(st)
    new = _download(engine)
    old = ears.whisper_model()
    old_time, old_text = _time(old, sample) if old else (1.0, "")
    new_time, new_text = _time(new, sample)
    limit = max(float(_cfg().get("hearing_max_seconds", 1.6)), old_time + 0.6)
    print(f"(better hearing: small.en {old_time:.2f}s {old_text!r}, turbo {new_time:.2f}s {new_text!r}, limit {limit:.2f}s)")
    st.update(t=time.time(), old_seconds=round(old_time, 2), new_seconds=round(new_time, 2))
    if new_time > limit:
        st.update(status="too_slow")
        _save(st)
        del new
        _remove_download(engine)
        return (f"I tried a bigger speech model, but it took {new_time:.1f} seconds to understand you on this computer, "
                f"so I'm sticking with my quicker ears and gave the space back.")
    st.update(status="upgraded")
    _save(st)
    ears.use_model(new, "large-v3-turbo")
    return f"My hearing's upgraded. I'm now using a bigger speech model, and it understood you in {new_time:.1f} seconds."


def downgrade():
    st = state()
    engine = st.get("engine", _engine())
    st.update(status="declined", t=time.time())
    _save(st)
    import ears
    ears.use_model(None, None)           # reloads small.en
    _remove_download(engine)
    return "Done, I'm back on my original hearing, and I've deleted the bigger model to save space."


def describe():
    st = state()
    s = st.get("status")
    if not automatic():
        return "I'm using the speech model set in my settings."
    if s == "upgraded":
        return f"I'm on my best hearing, Whisper large turbo, taking about {st.get('new_seconds', 1)} seconds to understand you."
    if s == "too_slow":
        return (f"I'm on my quick hearing. The bigger model took {st.get('new_seconds')} seconds here, "
                "which is too slow for a conversation.")
    if s == "no_space":
        return (f"I'm on my usual hearing. The better one needs about {st.get('need_gb', 5):.0f} gigabytes free and "
                f"there were only {st.get('free_gb', 0):.0f}. Free up some space and say upgrade your hearing.")
    if s == "declined":
        return "I'm on my original hearing, like you asked. Say upgrade your hearing to try the bigger one."
    if s == "downloading":
        return "I'm downloading a better speech model in the background right now."
    return "I'm on my usual hearing for now. I'll try a bigger speech model when I'm not busy."


def pop_note():
    """Something to tell Connor about his hearing, once."""
    return _notes.pop(0) if _notes else None


def start(is_busy):
    """In the background, when he's idle and has heard Connor at least once, try the upgrade."""
    def loop():
        time.sleep(300)
        while True:
            try:
                st = state()
                s = st.get("status")
                retry = s in (None, "downloading") or (s in ("no_space", "failed") and time.time() - st.get("t", 0) > 3 * 86400)
                import ears
                if automatic() and retry and not is_busy() and ears.LAST_AUDIO.get("audio") is not None:
                    said = upgrade()
                    now = state().get("status")
                    if now != s and now in ("upgraded", "too_slow", "no_space") and said:
                        _notes.append(said)             # tell him once, the next time he wakes me
            except Exception as e:
                print(f"(hearing check failed: {e})")
            time.sleep(1800)
    threading.Thread(target=loop, daemon=True, name="hearing").start()
