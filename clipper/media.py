"""FFmpeg and media helpers.

Uses the computer's ffmpeg if it has one, otherwise the copy that comes with the imageio-ffmpeg
Python package (so Connor never has to install FFmpeg by hand). Probing uses PyAV (already one of
Jarvis's packages), with a fallback that reads ffmpeg's own output."""
import json
import os
import re
import shutil
import subprocess
import sys
import wave

import numpy as np

WINDOWS = sys.platform == "win32"
_ffmpeg = []


class MediaError(Exception):
    """A video/audio file couldn't be read or written (not worth retrying)."""


def ffmpeg():
    if not _ffmpeg:
        exe = shutil.which("ffmpeg")
        if not exe:
            try:
                import imageio_ffmpeg
                exe = imageio_ffmpeg.get_ffmpeg_exe()
            except Exception:
                exe = None
        if not exe:
            raise MediaError("FFmpeg isn't available. Updating Jarvis installs it (imageio-ffmpeg).")
        _ffmpeg.append(exe)
    return _ffmpeg[0]


def _popen_kw():
    kw = {}
    if WINDOWS:
        kw["creationflags"] = 0x08000000 | 0x00004000      # no window, below-normal priority
    else:
        kw["preexec_fn"] = _nice
    return kw


def _nice():
    try:
        os.nice(10)                     # video work never makes Jarvis's voice stutter
    except Exception:
        pass


def run(args, timeout=None, check=True):
    """Runs ffmpeg with args. Returns the CompletedProcess (stderr has ffmpeg's report)."""
    cmd = [ffmpeg(), "-hide_banner", "-nostdin"] + [str(a) for a in args]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                       timeout=timeout, **_popen_kw())
    if check and r.returncode != 0:
        raise MediaError("ffmpeg failed: " + r.stderr.strip()[-600:])
    return r


def popen(args, **kw):
    cmd = [ffmpeg(), "-hide_banner", "-nostdin", "-loglevel", "error"] + [str(a) for a in args]
    return subprocess.Popen(cmd, **kw, **_popen_kw())


def probe(path):
    """{duration, width, height, fps, vcodec, acodec, has_audio, has_video, rotation, audio_rate}"""
    if not os.path.isfile(str(path)):
        raise MediaError(f"file not found: {path}")
    try:
        import av
        with av.open(str(path)) as c:
            info = {"duration": (c.duration or 0) / 1e6, "has_video": False, "has_audio": False,
                    "width": 0, "height": 0, "fps": 0.0, "vcodec": "", "acodec": "", "rotation": 0,
                    "audio_rate": 0, "audio_duration": 0.0, "video_duration": 0.0}
            for s in c.streams:
                if s.type == "video" and not info["has_video"]:
                    info.update(has_video=True, width=s.codec_context.width, height=s.codec_context.height,
                                vcodec=s.codec_context.name,
                                fps=float(s.average_rate or s.guessed_rate or 0) or 30.0)
                    if s.duration and s.time_base:
                        info["video_duration"] = float(s.duration * s.time_base)
                    rot = (s.metadata or {}).get("rotate")
                    if rot:
                        info["rotation"] = int(rot)
                elif s.type == "audio" and not info["has_audio"]:
                    info.update(has_audio=True, acodec=s.codec_context.name, audio_rate=s.codec_context.sample_rate)
                    if s.duration and s.time_base:
                        info["audio_duration"] = float(s.duration * s.time_base)
            if info["has_video"] and info["rotation"] in (90, -90, 270, -270):
                info["width"], info["height"] = info["height"], info["width"]
            return info
    except ImportError:
        pass
    except Exception as e:
        raise MediaError(f"can't read {os.path.basename(str(path))}: {e}")
    return _probe_ffmpeg(path)


def _probe_ffmpeg(path):
    r = run(["-i", path], check=False)
    text = r.stderr
    info = {"duration": 0.0, "has_video": False, "has_audio": False, "width": 0, "height": 0, "fps": 30.0,
            "vcodec": "", "acodec": "", "rotation": 0, "audio_rate": 0, "audio_duration": 0.0, "video_duration": 0.0}
    m = re.search(r"Duration: (\d+):(\d+):([\d.]+)", text)
    if m:
        info["duration"] = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.search(r"Video: (\w+).*?, (\d{2,5})x(\d{2,5})", text)
    if m:
        info.update(has_video=True, vcodec=m.group(1), width=int(m.group(2)), height=int(m.group(3)))
        f = re.search(r"([\d.]+) fps", text)
        if f:
            info["fps"] = float(f.group(1))
    m = re.search(r"Audio: (\w+).*?(\d+) Hz", text)
    if m:
        info.update(has_audio=True, acodec=m.group(1), audio_rate=int(m.group(2)))
    if not info["has_video"] and not info["has_audio"]:
        raise MediaError(f"can't read {os.path.basename(str(path))}: not a video")
    return info


def extract_audio(src, out, start=None, duration=None, rate=16000, channels=1):
    """Audio as 16-bit WAV (16 kHz mono for speech recognition by default)."""
    args = ["-y"]
    if start is not None:
        args += ["-ss", f"{start:.3f}"]
    args += ["-i", src]
    if duration is not None:
        args += ["-t", f"{duration:.3f}"]
    args += ["-vn", "-ac", channels, "-ar", rate, "-c:a", "pcm_s16le", str(out) + ".part.wav"]
    run(args)
    os.replace(str(out) + ".part.wav", str(out))
    return out


def read_wav(path):
    """(samples as float32 -1..1 with shape (n, channels), rate)"""
    with wave.open(str(path), "rb") as w:
        rate, ch, n = w.getframerate(), w.getnchannels(), w.getnframes()
        data = np.frombuffer(w.readframes(n), dtype=np.int16).astype(np.float32) / 32768.0
    return data.reshape(-1, ch), rate


def write_wav(path, samples, rate):
    a = np.asarray(samples, dtype=np.float32)
    if a.ndim == 1:
        a = a[:, None]
    pcm = (np.clip(a, -1, 1) * 32767).astype(np.int16)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(pcm.shape[1])
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm.tobytes())


def energy(samples, rate, hop=0.1):
    """Loudness (RMS, dB) every `hop` seconds."""
    mono = samples.mean(axis=1) if samples.ndim > 1 else samples
    n = int(rate * hop)
    k = len(mono) // n
    if k == 0:
        return np.zeros(0)
    rms = np.sqrt(np.mean(mono[:k * n].reshape(k, n) ** 2, axis=1) + 1e-10)
    return 20 * np.log10(rms + 1e-10)


def loudness(path):
    """Integrated loudness (LUFS) and true peak (dBTP) via ffmpeg's ebur128 filter."""
    r = run(["-i", path, "-vn", "-af", "ebur128=peak=true", "-f", "null", "-"], check=False)
    tail = r.stderr[r.stderr.rfind("Summary:"):] if "Summary:" in r.stderr else r.stderr
    i = re.search(r"I:\s+(-?[\d.]+) LUFS", tail)
    p = re.search(r"Peak:\s+(-?[\d.]+|-inf) dBFS", tail)
    lufs = float(i.group(1)) if i else None
    peak = (-120.0 if p.group(1) == "-inf" else float(p.group(1))) if p else None
    return lufs, peak


def detect(path, kind):
    """Runs one of ffmpeg's detectors: 'black', 'silence' or 'freeze'. Returns [(start, end)]."""
    if kind == "black":
        args = ["-i", path, "-an", "-vf", "blackdetect=d=0.4:pix_th=0.08", "-f", "null", "-"]
        pat = r"black_start:([\d.]+) black_end:([\d.]+)"
    elif kind == "silence":
        args = ["-i", path, "-vn", "-af", "silencedetect=n=-42dB:d=1.2", "-f", "null", "-"]
        pat = None
    elif kind == "freeze":
        args = ["-i", path, "-an", "-vf", "freezedetect=n=0.001:d=3", "-f", "null", "-"]
        pat = None
    else:
        raise ValueError(kind)
    text = run(args, check=False).stderr
    if pat:
        return [(float(a), float(b)) for a, b in re.findall(pat, text)]
    key = "silence" if kind == "silence" else "freeze"
    starts = [float(x) for x in re.findall(rf"{key}_start: ?([\d.]+)", text)]
    ends = [float(x) for x in re.findall(rf"{key}_end: ?([\d.]+)", text)]
    while len(ends) < len(starts):
        ends.append(None)
    return list(zip(starts, ends))


class FrameReader:
    """Decodes frames (BGR, numpy) from start for duration at a fixed fps, scaled so the height
    is at most max_h. Frame k is at source time start + k / fps."""

    def __init__(self, src, start, duration, fps, max_h=1080, max_w=None):
        info = probe(src)
        w, h = info["width"], info["height"]
        scale = min(1.0, max_h / h if h else 1.0)
        if max_w:
            scale = min(scale, max_w / w)
        self.w, self.h = max(2, int(w * scale) // 2 * 2), max(2, int(h * scale) // 2 * 2)
        self.fps = fps
        self.size = self.w * self.h * 3
        self.proc = popen(["-ss", f"{max(0.0, start):.3f}", "-i", src, "-t", f"{duration:.3f}", "-an",
                           "-vf", f"fps={fps},scale={self.w}:{self.h}:flags=bilinear", "-f", "rawvideo",
                           "-pix_fmt", "bgr24", "-"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          bufsize=self.size * 2)
        self.index = -1
        self.frame = None

    def read(self):
        buf = self.proc.stdout.read(self.size)
        if len(buf) < self.size:
            return None
        self.index += 1
        self.frame = np.frombuffer(buf, dtype=np.uint8).reshape(self.h, self.w, 3)
        return self.frame

    def seek_index(self, k):
        """Frame number k (reads forward; k never goes backwards in a render). Keeps the last frame
        if the source ends early."""
        while self.index < k:
            if self.read() is None:
                break
        return self.frame

    def close(self):
        try:
            self.proc.stdout.close()
            self.proc.kill()
            self.proc.wait(5)
        except Exception:
            pass


class FrameWriter:
    """Encodes BGR frames + a WAV file into an H.264/AAC MP4 made for phones."""

    def __init__(self, out, w, h, fps, audio, preset="veryfast", crf=20, vf=None):
        self.out = str(out)
        self.proc = popen(["-y", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", fps, "-i", "-",
                           "-i", audio, "-map", "0:v", "-map", "1:a"] + (["-vf", vf] if vf else []) +
                          ["-c:v", "libx264", "-preset", preset,
                           "-crf", crf, "-profile:v", "high", "-pix_fmt", "yuv420p", "-r", fps,
                           "-c:a", "aac", "-b:a", "192k", "-ar", "48000", "-movflags", "+faststart",
                           "-shortest", self.out], stdin=subprocess.PIPE, stderr=subprocess.PIPE)
        self.frames = 0

    def write(self, frame):
        try:
            self.proc.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
        except (BrokenPipeError, OSError):
            raise MediaError("the video encoder stopped: " + self._err())
        self.frames += 1

    def _err(self):
        try:
            return self.proc.stderr.read().decode("utf-8", "replace")[-500:]
        except Exception:
            return ""

    def close(self):
        try:
            self.proc.stdin.close()
        except Exception:
            pass
        code = self.proc.wait()
        if code != 0:
            raise MediaError("the video encoder failed: " + self._err())


def save_jpeg(frame, path, width=360):
    import cv2
    h, w = frame.shape[:2]
    small = cv2.resize(frame, (width, int(h * width / w)), interpolation=cv2.INTER_AREA)
    cv2.imwrite(str(path), small, [cv2.IMWRITE_JPEG_QUALITY, 82])


def dump(obj, path):
    tmp = str(path) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=1)
    os.replace(tmp, str(path))


def load(path, default=None):
    try:
        with open(str(path), encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default
