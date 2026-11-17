"""Morning briefing: weather, calendar, reminders and a few news headlines."""
import datetime
import json
import threading
import time
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import obsidian
import personality
import organizer
import weather

CONFIG = Path(__file__).with_name("config.json")


def headlines(n=3):
    try:
        url = json.loads(CONFIG.read_text()).get("news_feed", "https://feeds.npr.org/1001/rss.xml")
        req = urllib.request.Request(url, headers={"User-Agent": "jarvis-assistant"})
        with urllib.request.urlopen(req, timeout=6) as r:
            root = ET.fromstring(r.read())
        return [i.findtext("title").strip() for i in root.iter("item") if i.findtext("title")][:n]
    except Exception:
        return []


def part_of_day():
    h = datetime.datetime.now().hour
    return "morning" if 4 <= h < 12 else "afternoon" if h < 18 else "evening"


def full():
    now = datetime.datetime.now()
    opener = personality.line("greet_open", part=part_of_day())
    parts = [f"{opener} It's {now.strftime('%A, %B')} {now.day}.", weather.report(),
             organizer.calendar_summary(0)]
    rem = organizer.reminders_summary(0)
    if not rem.startswith("No reminders"):
        parts.append(rem)
    try:
        if obsidian.enabled():
            todos = obsidian.todos_summary()
            if not todos.endswith("clear."):
                parts.append(todos)
    except Exception as e:
        print(f"(obsidian to-dos failed: {e})")
    try:
        import predict
        ahead = predict.insight_today()
        if ahead:
            parts.append("Looking ahead: " + ahead)
    except Exception as e:
        print(f"(look-ahead failed: {e})")
    news = headlines()
    if news:
        parts.append("In the news: " + ". ".join(news) + ".")
    parts.append(personality.line("brief_close"))
    refresh_display()
    return " ".join(parts)


def refresh_display():
    try:
        import display
        display.update(today=organizer.today_items())
    except Exception as e:
        print(f"(display refresh failed: {e})")


def keep_display_fresh(every=600):
    def loop():
        while True:
            refresh_display()
            time.sleep(every)
    threading.Thread(target=loop, daemon=True).start()
