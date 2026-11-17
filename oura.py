"""Oura ring data for Jarvis's "vitals analysis".

Two ways to connect (run "Connect Oura.command"):
  1. A personal access token (config "oura_token"), if Oura still offers them for your account.
  2. Your own Oura API app (OAuth). Tokens are kept in oura_tokens.json and refreshed automatically.
"""
import datetime
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
TOKENS = HERE / "oura_tokens.json"
API = "https://api.ouraring.com/v2/usercollection/"
AUTH_URL = "https://cloud.ouraring.com/oauth/authorize"
TOKEN_URL = "https://api.ouraring.com/oauth/token"
REDIRECT_URI = "http://localhost:8889/callback"
SCOPES = "email personal daily heartrate workout session spo2"
try:   # Python from uv may not see the Mac's certificates, so use certifi's when available
    import certifi
    CTX = ssl.create_default_context(cafile=certifi.where())
except Exception:
    CTX = ssl.create_default_context()


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def _post(url, data):
    req = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(),
                                 headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
        return json.loads(r.read())


def save_tokens(tok):
    tok["expires_at"] = time.time() + int(tok.get("expires_in", 86400)) - 60
    TOKENS.write_text(json.dumps(tok, indent=2))


def _token():
    cfg = _cfg()
    if cfg.get("oura_token"):
        return cfg["oura_token"]
    if not TOKENS.exists():
        return None
    tok = json.loads(TOKENS.read_text())
    if time.time() > tok.get("expires_at", 0) and tok.get("refresh_token"):
        new = _post(TOKEN_URL, {"grant_type": "refresh_token", "refresh_token": tok["refresh_token"],
                                "client_id": cfg.get("oura_client_id", ""),
                                "client_secret": cfg.get("oura_client_secret", "")})
        new.setdefault("refresh_token", tok["refresh_token"])
        save_tokens(new)
        tok = new
    return tok.get("access_token")


def connected():
    return bool(_cfg().get("oura_token")) or TOKENS.exists()


def _get(kind, **params):
    token = _token()
    if not token:
        raise RuntimeError("not connected")
    url = API + kind + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=20, context=CTX) as r:
            return json.loads(r.read()).get("data", [])
    except urllib.error.HTTPError as e:
        print(f"(oura {kind}: HTTP {e.code})")
        if e.code == 401:
            raise RuntimeError("sign-in expired")
        return []          # e.g. no permission for that data type


def _mins(seconds):
    return round((seconds or 0) / 60)


def snapshot(days=7):
    """The last week of Oura data, condensed to what matters for a spoken analysis."""
    end = datetime.date.today() + datetime.timedelta(days=1)
    start = datetime.date.today() - datetime.timedelta(days=days)
    rng = {"start_date": start.isoformat(), "end_date": end.isoformat()}
    out = {}
    for row in _get("daily_sleep", **rng):
        out.setdefault(row.get("day"), {})["sleep_score"] = row.get("score")
    for row in _get("daily_readiness", **rng):
        d = out.setdefault(row.get("day"), {})
        d["readiness_score"] = row.get("score")
        d["temperature_deviation_c"] = row.get("temperature_deviation")
        d["readiness_contributors"] = row.get("contributors")
    for row in _get("daily_activity", **rng):
        d = out.setdefault(row.get("day"), {})
        d["activity_score"] = row.get("score")
        d["steps"] = row.get("steps")
        d["active_calories"] = row.get("active_calories")
    for row in _get("sleep", **rng):
        if row.get("type") not in (None, "long_sleep"):
            continue                                  # skip naps
        d = out.setdefault(row.get("day"), {})
        d.update({
            "total_sleep_min": _mins(row.get("total_sleep_duration")),
            "deep_min": _mins(row.get("deep_sleep_duration")),
            "rem_min": _mins(row.get("rem_sleep_duration")),
            "efficiency_pct": row.get("efficiency"),
            "lowest_heart_rate": row.get("lowest_heart_rate"),
            "avg_heart_rate_asleep": row.get("average_heart_rate"),
            "avg_hrv_ms": row.get("average_hrv"),
            "avg_breath_rate": row.get("average_breath"),
            "bedtime": (row.get("bedtime_start") or "")[11:16],
            "wake_time": (row.get("bedtime_end") or "")[11:16],
        })
    for row in _get("daily_spo2", **rng):
        pct = (row.get("spo2_percentage") or {}).get("average")
        if pct:
            out.setdefault(row.get("day"), {})["spo2_pct"] = round(pct, 1)
    for row in _get("daily_stress", **rng):
        d = out.setdefault(row.get("day"), {})
        d["stress_high_min"] = _mins(row.get("stress_high"))
        d["recovery_high_min"] = _mins(row.get("recovery_high"))
        d["stress_summary"] = row.get("day_summary")
    return {k: out[k] for k in sorted(k for k in out if k)}


def _not_connected():
    return "Your Oura ring isn't connected yet. Double-click Connect Oura in my folder to hook it up."


def analysis(question="Give me a vitals analysis."):
    if not connected():
        return _not_connected()
    try:
        data = snapshot()
    except RuntimeError as e:
        if "expired" in str(e):
            return "My Oura sign-in expired. Run Connect Oura again and we're back."
        return _not_connected()
    except Exception as e:
        print(f"(oura failed: {e})")
        return "I couldn't reach Oura just now. Try again in a minute."
    if not data:
        return "Oura didn't send me any data for this week. Make sure your ring has synced in the Oura app."
    import brain
    context = (
        "Here is the user's Oura ring data for the last week, newest day last (JSON). "
        "Answer his question from it like a sharp coach: lead with how he's doing today, compare "
        "today to his own weekly average, call out anything notable (short or late sleep, low HRV, "
        "high resting heart rate, temperature deviation over 0.5 C, low readiness), and give one "
        "practical tip. Use real numbers, rounded. Keep it to about four spoken sentences. You are "
        "not a doctor; if something looks seriously off, say to check with one, without being dramatic.\n"
        + json.dumps(data))
    return brain.think(question, context=context)
