"""Weather for wherever this Mac is, found from its internet connection.
Uses free services that need no API key: ipapi.co / ip-api.com and Open-Meteo."""
import json
import urllib.request

CODES = {
    0: "clear", 1: "mostly clear", 2: "partly cloudy", 3: "overcast",
    45: "foggy", 48: "foggy", 51: "drizzly", 53: "drizzly", 55: "drizzly",
    61: "light rain", 63: "rain", 65: "heavy rain", 71: "light snow",
    73: "snow", 75: "heavy snow", 80: "showery", 81: "showery", 82: "stormy showers",
    95: "thunderstorms", 96: "thunderstorms", 99: "thunderstorms",
}


def _get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-assistant"})
    with urllib.request.urlopen(req, timeout=6) as r:
        return json.load(r)


def locate():
    try:
        d = _get("https://ipapi.co/json/")
        return d["latitude"], d["longitude"], d.get("city", "")
    except Exception:
        d = _get("http://ip-api.com/json/")
        return d["lat"], d["lon"], d.get("city", "")


def current():
    """Returns a dict for the display, or None if offline."""
    try:
        lat, lon, city = locate()
        w = _get(
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current=temperature_2m,weather_code"
            "&daily=temperature_2m_max,temperature_2m_min&timezone=auto&temperature_unit=fahrenheit"
        )
        cur = w["current"]
        temp = round(cur["temperature_2m"])
        sky = CODES.get(cur["weather_code"], "unsettled")
        hi = round(w["daily"]["temperature_2m_max"][0])
        lo = round(w["daily"]["temperature_2m_min"][0])
        return {"temp": temp, "sky": sky, "hi": hi, "lo": lo, "city": city}
    except Exception:
        return None


def report():
    w = current()
    try:
        import display
        display.update(weather=w)
    except Exception:
        pass
    if not w:
        return "I'm afraid I can't reach the weather service just now."
    place = f" in {w['city']}" if w["city"] else ""
    return f"It's {w['temp']} degrees and {w['sky']}{place}, with a high of {w['hi']} and a low of {w['lo']}."
