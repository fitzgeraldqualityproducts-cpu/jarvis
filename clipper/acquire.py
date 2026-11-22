"""Getting the video file, for authorized sources only.

  file       used where it is (never copied or changed)
  url        a direct link to a video file (e.g. a creator's Dropbox link): downloaded
  youtube    yt-dlp, only when the video is authorized (yours, CC BY, or permission recorded)
  twitch     yt-dlp, for your own VODs (or ones you have permission for)

Long videos (over 40 minutes) are fetched smartly: the audio first (small), and after the best
moments are picked, only those few sections of video. A 6-hour stream costs a few hundred MB
instead of 15 GB. Protected (DRM), private or members-only videos are never downloaded, and no
access control is ever worked around: if yt-dlp says it needs a login, the job stops there."""
import os
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor
import urllib.request
from pathlib import Path

from clipper import media, settings

SMART_OVER = 40 * 60          # seconds; longer videos get audio first, then just the clip sections


class AcquireError(Exception):
    pass


class NotPermitted(AcquireError):
    """Never retried: the platform says this needs a login/subscription, or it's protected."""


def _ydl(opts=None):
    ffmpeg_on_path()                                 # before yt-dlp looks (it remembers the answer)
    try:
        import yt_dlp
    except ImportError:
        raise AcquireError("yt-dlp isn't installed yet (it comes with the next Jarvis update)")
    base = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True,
            "retries": 3, "fragment_retries": 3, "socket_timeout": 30, "allow_unplayable_formats": False}
    try:
        base["ffmpeg_location"] = media.ffmpeg()
    except Exception:
        pass
    base.update(opts or {})
    return yt_dlp.YoutubeDL(base)


def ffmpeg_on_path():
    """yt-dlp checks for "ffmpeg" on PATH before cutting a section of a video, ignoring
    ffmpeg_location. On a computer without a system ffmpeg, the copy Jarvis brings (imageio-ffmpeg)
    is linked into clipper_data/bin as "ffmpeg" and that folder is put on PATH."""
    if shutil.which("ffmpeg"):
        return True
    try:
        from clipper import data_dir
        exe = media.ffmpeg()
        folder = data_dir("bin")
        link = folder / ("ffmpeg.exe" if os.name == "nt" else "ffmpeg")
        if not link.exists():
            try:
                os.symlink(exe, link)
            except (OSError, NotImplementedError):
                shutil.copy2(exe, link)
            os.chmod(link, 0o755)
        os.environ["PATH"] = str(folder) + os.pathsep + os.environ.get("PATH", "")
        return bool(shutil.which("ffmpeg"))
    except Exception as e:
        print(f"(clipper: couldn't put ffmpeg on PATH: {e})")
        return False


def _blocked(e):
    text = str(e).lower()
    return any(w in text for w in ("drm", "login", "sign in", "subscriber", "members-only", "private video",
                                   "premium", "confirm your age", "not available", "copyright"))


def info(url):
    """Metadata only (title, creator, duration, licence). No download."""
    try:
        with _ydl({"skip_download": True}) as y:
            d = y.extract_info(url, download=False)
    except Exception as e:
        if _blocked(e):
            return {"availability": "needs_auth", "error": str(e)[:200]}
        raise AcquireError(f"couldn't read that link: {str(e)[:200]}")
    return _norm(d)


def _norm(d):
    return {"title": d.get("title"), "creator": d.get("uploader") or d.get("channel") or d.get("creator"),
            "creator_id": (d.get("channel_id") or d.get("uploader_id") or "").lstrip("@"),
            "duration": d.get("duration"), "views": d.get("view_count"), "likes": d.get("like_count"),
            "published_at": d.get("upload_date") or d.get("timestamp"), "license": d.get("license"),
            "availability": d.get("availability"), "is_live": d.get("is_live"), "live_status": d.get("live_status"),
            "description": (d.get("description") or "")[:2000], "drm": bool(d.get("has_drm")),
            "url": d.get("webpage_url") or d.get("original_url"), "full": True}


def list_playlist(url, limit=5):
    """Newest videos on a channel/VOD page, as [{url, title, duration...}]."""
    with _ydl({"extract_flat": "in_playlist", "playlistend": limit, "noplaylist": False}) as y:
        d = y.extract_info(url, download=False)
    out = []
    for e in (d.get("entries") or [])[:limit]:
        link = e.get("url") or e.get("webpage_url")
        if not link:
            continue
        if not link.startswith("http"):
            link = f"https://www.youtube.com/watch?v={link}" if len(link) == 11 else link
        out.append({"url": link, "title": e.get("title"), "duration": e.get("duration"),
                    "creator": e.get("uploader") or d.get("uploader"),
                    "creator_id": (e.get("channel_id") or e.get("uploader_id") or d.get("uploader_id") or "").lstrip("@")})
    return out


def _download(url, folder, opts, label):
    folder.mkdir(parents=True, exist_ok=True)
    tmpl = str(folder / f"{label}.%(ext)s")
    try:
        with _ydl(dict({"outtmpl": tmpl, "merge_output_format": "mp4", "overwrites": True}, **opts)) as y:
            d = y.extract_info(url, download=True)
    except Exception as e:
        if _blocked(e):
            raise NotPermitted(f"the platform won't give this video without a login or subscription: {str(e)[:160]}")
        raise AcquireError(f"download failed: {str(e)[:300]}")
    if d.get("has_drm"):
        raise NotPermitted("this video is DRM protected")
    files = sorted(folder.glob(f"{label}.*"), key=lambda p: p.stat().st_size, reverse=True)
    files = [f for f in files if not f.name.endswith((".part", ".ytdl", ".json"))]
    if not files:
        raise AcquireError("download finished but no file appeared")
    return files[0]


VIDEO_FMT = "bv*[height<=1080][vcodec!*=av01]+ba/b[height<=1080]/bv*+ba/b"


def acquire(video, folder, progress=lambda f, d="": None):
    """Makes sure there's media for the job. Returns a dict saved as the job's source.json:
       {"mode": "full", "video": path, "audio": path}  or
       {"mode": "sections", "audio": path, "sections": {}}  (video sections come later)."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    have = media.load(folder / "source.json")
    if have and all(os.path.exists(have[k]) for k in ("audio",) if have.get(k)) and \
            (have.get("mode") == "sections" or os.path.exists(have.get("video", ""))):
        return have                                         # resumed: already got it
    if video["auth_status"] != "authorized":
        raise NotPermitted("this video isn't authorized for clipping")
    platform = video["platform"]
    if platform == "file":
        path = video["path"] or video["url"]
        if not os.path.isfile(path):
            raise NotPermitted(f"the file isn't there any more: {path}")
        info = media.probe(path)
        if not info["has_audio"]:
            raise NotPermitted("that video has no sound, so there's nothing to transcribe")
        progress(0.5, "extracting audio")
        audio = media.extract_audio(path, folder / "audio.wav")
        out = {"mode": "full", "video": path, "audio": str(audio), "info": info}
    elif platform == "url":
        progress(0.1, "downloading")
        path = _http_download(video["url"], folder / "source.mp4")
        info = media.probe(str(path))
        audio = media.extract_audio(str(path), folder / "audio.wav")
        out = {"mode": "full", "video": str(path), "audio": str(audio), "info": info, "downloaded": True}
    elif platform in ("youtube", "twitch"):
        if not settings.general().get("allow_platform_download", True):
            raise NotPermitted("downloading from YouTube/Twitch is switched off in the clipper settings")
        duration = float(video.get("duration") or 0)
        if duration > SMART_OVER:
            progress(0.1, "downloading the audio")
            a = _download(video["url"], folder, {"format": "ba/b[height<=480]/b"}, "audio_src")
            audio = media.extract_audio(str(a), folder / "audio.wav")
            if os.path.exists(a):
                os.remove(a)
            out = {"mode": "sections", "audio": str(audio), "sections": {}, "url": video["url"]}
        else:
            progress(0.1, "downloading the video")
            path = _download(video["url"], folder, {"format": VIDEO_FMT}, "source")
            info = media.probe(str(path))
            audio = media.extract_audio(str(path), folder / "audio.wav")
            out = {"mode": "full", "video": str(path), "audio": str(audio), "info": info, "downloaded": True}
    else:
        raise AcquireError(f"unknown source type {platform}")
    media.dump(out, folder / "source.json")
    return out


_pending = {}                     # (folder, key) -> a background download of that section
_lock = threading.Lock()


def _span(start, end, pad):
    s, e = max(0.0, start - pad), end + pad
    return s, e, f"{int(s * 10)}_{int(e * 10)}"


def prefetch(source, folder, spans, pad=3.0, workers=2):
    """Smart mode: starts downloading the next clips' sections in the background, two at a time,
    so the next clip's video is usually already here when its turn comes (same files, just sooner)."""
    if source.get("mode") != "sections" or (source.get("video") and os.path.exists(source["video"])):
        return 0
    pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="clipper-prefetch")
    n = 0
    for start, end in spans:
        s, e, key = _span(start, end, pad)
        with _lock:
            have = (source.get("sections") or {}).get(key)
            if (have and os.path.exists(have)) or (str(folder), key) in _pending:
                continue
            _pending[(str(folder), key)] = pool.submit(_fetch, source, Path(folder), s, e, key)
        n += 1
    pool.shutdown(wait=False)
    return n


def section(source, folder, start, end, pad=3.0):
    """For smart mode: the video for one clip (start..end seconds of the original), downloaded once.
    Returns (path, offset) where offset is the original time at the file's 0."""
    folder = Path(folder)
    if source.get("mode") != "sections":
        return source["video"], 0.0
    if source.get("video") and os.path.exists(source["video"]):
        return source["video"], 0.0                 # the whole video was fetched already (fallback)
    s, e, key = _span(start, end, pad)
    with _lock:
        fut = _pending.get((str(folder), key))
    if fut is not None:
        try:
            fut.result()                            # being downloaded in the background: wait for it
        except Exception:
            pass                                    # it failed there: try again here (errors show)
        finally:
            with _lock:
                _pending.pop((str(folder), key), None)
    have = source.setdefault("sections", {}).get(key)
    if have and os.path.exists(have):
        return have, s
    for k, p in list(source["sections"].items()):  # an earlier, wider section covers it
        a, b = (int(x) / 10 for x in k.split("_"))
        if a <= start and b >= end and os.path.exists(p):
            return p, a
    got = _fetch(source, folder, s, e, key)
    return (got, s) if got != source.get("video") else (got, 0.0)


def _fetch(source, folder, s, e, key):
    try:
        from yt_dlp.utils import download_range_func
    except ImportError:
        raise AcquireError("yt-dlp isn't installed")
    try:
        path = _download(source["url"], folder, {"format": VIDEO_FMT, "download_ranges": download_range_func(None, [(s, e)]),
                                                 "force_keyframes_at_cuts": True}, f"section_{key}")
    except AcquireError as err:
        if "ffmpeg" not in str(err).lower():
            raise
        # yt-dlp can't cut sections on this computer: get the whole video once and cut it ourselves
        with _lock:
            if not (source.get("video") and os.path.exists(source["video"])):
                source["video"] = str(_download(source["url"], folder, {"format": VIDEO_FMT}, "source"))
                media.dump(source, folder / "source.json")
        return source["video"]
    with _lock:
        source.setdefault("sections", {})[key] = str(path)
        media.dump(source, folder / "source.json")
    return str(path)


def _http_download(url, out, max_gb=20):
    tmp = Path(str(out) + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-clipper"})
    try:
        import ssl
        import certifi
        ctx = ssl.create_default_context(cafile=certifi.where())
    except Exception:
        ctx = None
    try:
        with urllib.request.urlopen(req, timeout=60, context=ctx) as r, open(tmp, "wb") as f:
            kind = r.headers.get("Content-Type", "")
            if "text/html" in kind:
                raise NotPermitted("that link is a web page, not a video file")
            total = 0
            while True:
                chunk = r.read(1 << 20)
                if not chunk:
                    break
                total += len(chunk)
                if total > max_gb * (1 << 30):
                    raise AcquireError("that file is too big")
                f.write(chunk)
    except (AcquireError, NotPermitted):
        if tmp.exists():
            tmp.unlink()
        raise
    except Exception as e:
        raise AcquireError(f"download failed: {e}")
    os.replace(tmp, out)
    return out


def cleanup(folder, keep=("source.json", "transcript.json", "candidates.json", "plan.json")):
    """Deletes the big downloaded media of a finished job (keeps the small notes)."""
    folder = Path(folder)
    freed = 0
    src = media.load(folder / "source.json") or {}
    for f in folder.iterdir() if folder.exists() else []:
        if f.name in keep or f.is_dir() or f.suffix in (".json", ".log"):
            continue
        if src.get("mode") == "full" and not src.get("downloaded") and str(f) == src.get("video"):
            continue                                     # never delete the owner's own file
        try:
            freed += f.stat().st_size
            f.unlink()
        except OSError:
            pass
    return freed


def free_gb(path):
    try:
        return shutil.disk_usage(str(path)).free / (1 << 30)
    except Exception:
        return 999.0


def folder_gb(path):
    total = 0
    for root, _, files in os.walk(str(path)):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
    return total / (1 << 30)

