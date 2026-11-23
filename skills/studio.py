"""YouTube business (studio/) tools for Jarvis. Read-only: every answer comes from the studio's own
database, which holds numbers read from YouTube itself. Nothing here uploads, publishes or spends."""
from skills import tool


@tool("The YouTube business channel's stats and what Jarvis has learned: subscribers, views, each Short's "
      "views and retention, and which kinds of topics work best. Use for 'YouTube stats', 'how is the channel doing'.")
def youtube_business_stats():
    from studio import analytics, config
    if not config.enabled():
        return "The YouTube business module is off."
    text = analytics.report(save=False)
    return text[:3000]


@tool("What the YouTube business module is doing: Shorts being made, waiting, uploaded, and any problems.")
def youtube_business_status():
    from studio import config, db
    if not config.enabled():
        return "The YouTube business module is off."
    rows = db.q("SELECT state, COUNT(*) n FROM videos GROUP BY state")
    errs = db.q("SELECT detail FROM errors ORDER BY id DESC LIMIT 3")
    from studio import autopilot
    a = autopilot.status()
    out = ("Autopilot " + ("halted: " + a["halted"]["reason"] if a["halted"] else ("on" if a["on"] else "off")) +
           f"; publish mode {a['mode']}; {a['ready']} finished Short(s) waiting. ")
    out += "Shorts by state: " + (", ".join(f"{r['state']} {r['n']}" for r in rows) or "none yet") + "."
    if errs:
        out += " Recent problems: " + " | ".join(r["detail"][:160] for r in errs)
    return out


@tool("Progress toward the YouTube Partner Program on the business channel: subscribers, Shorts views in "
      "90 days, uploads, with estimates labelled. Never money, revenue or AdSense.")
def youtube_partner_progress():
    from studio import config, eligibility
    if not config.enabled():
        return "The YouTube business module is off."
    return eligibility.text()


@tool("Open the live YouTube business dashboard: the production line showing every Short moving from idea to "
      "script, fact-check, voice, video, quality check, upload and live. Use for 'show me the channel', "
      "'where are the videos at', 'let me watch the business run'.")
def open_youtube_dashboard():
    import display
    display.open_page("/studio")
    return "Opened the YouTube business dashboard in the browser."
