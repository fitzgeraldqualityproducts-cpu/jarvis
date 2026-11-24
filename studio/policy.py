"""Policy watch: once a week Jarvis reads YouTube's own pages about the rules this channel lives by (the
Partner Program, monetization and "inauthentic content" policies, Shorts, AI labelling, the API terms) and
tells Connor when one of them changed, quoting what's new. It NEVER changes a setting by itself: a change
in the rules is Connor's call. The first read of each page only records it.

Pages are read with the same plain-text reader as the research (guard "web.read"); what they say is
treated as information, not instructions."""
import difflib
import hashlib
import json
import re
import time

from studio import config, db

PAGES = {
    "Partner Program eligibility": "https://support.google.com/youtube/answer/72851?hl=en",
    "Expanded Partner Program": "https://support.google.com/youtube/answer/13429240?hl=en",
    "Partner Program changes (2027)": "https://support.google.com/youtube/answer/12843009?hl=en",
    "Channel monetization policies (inauthentic / reused content)": "https://support.google.com/youtube/answer/1311392?hl=en",
    "Altered or synthetic content disclosure": "https://support.google.com/youtube/answer/14328491?hl=en",
    "YouTube API Services Terms of Service": "https://developers.google.com/youtube/terms/api-services-terms-of-service",
    "YouTube API developer policies": "https://developers.google.com/youtube/terms/developer-policies",
}
EVERY = 7 * 86400
CHANGED_BELOW = 0.985            # text this much the same counts as unchanged (page furniture moves around)
KEY = "policy_pages"


def _norm(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 25]


def new_sentences(old, new, limit=4):
    """Sentences in the new text that weren't in the old one (the clearest sign of what changed)."""
    before = set(_sentences(old))
    return [s[:300] for s in _sentences(new) if s not in before][:limit]


def check(now=None, read=None):
    """Reads every page once. Returns [{"name", "url", "state": "first"|"same"|"changed"|"unreadable", "new": [...]}]."""
    from studio import topics
    now = now or time.time()
    read = read or topics.page_text
    seen = db.get(KEY, {}) or {}
    out = []
    for name, url in PAGES.items():
        try:
            text = _norm(read(url))[:200_000]
        except Exception:
            text = ""
        prev = seen.get(url) or {}
        row = {"name": name, "url": url, "new": []}
        if len(text) < 400:                       # blocked, moved or empty: say so, keep the old copy
            row["state"] = "unreadable"
        elif not prev.get("text"):
            row["state"] = "first"
        else:
            ratio = difflib.SequenceMatcher(None, prev["text"], text, autojunk=False).quick_ratio()
            if ratio >= CHANGED_BELOW and hashlib.sha256(text.encode()).hexdigest() == prev.get("sha"):
                row["state"] = "same"
            elif ratio >= CHANGED_BELOW:
                row["state"] = "same"             # tiny shifts (dates, menus) aren't a rule change
            else:
                row["state"] = "changed"
                row["new"] = new_sentences(prev["text"], text)
        if row["state"] in ("first", "changed"):
            seen[url] = {"text": text, "sha": hashlib.sha256(text.encode()).hexdigest(), "t": now}
        elif row["state"] == "same":
            seen[url] = dict(prev, checked=now)
        out.append(row)
    db.put(KEY, seen)
    db.put("policy_last", now)
    changed = [r for r in out if r["state"] == "changed"]
    if changed:
        from studio import health
        parts = []
        for r in changed:
            parts.append(r["name"] + (": new wording includes \"" + r["new"][0][:160] + "\"" if r["new"] else ""))
        health.notify("policy", "A YouTube rules page changed since I last read it: " + "; ".join(parts)
                      + ". I haven't changed anything. Have a look, or ask me to go through it.", "info")
    db.insert("INSERT INTO decisions(t, area, decision, evidence) VALUES(?,?,?,?)", now, "policy",
              f"read {len(out)} policy pages: {len(changed)} changed",
              json.dumps([{k: r[k] for k in ("name", "url", "state", "new")} for r in out])[:4000])
    return out


def maybe_check(now=None):
    """Background: once a week, studio on only."""
    now = now or time.time()
    if not config.enabled():
        return None
    if now - float(db.get("policy_last", 0) or 0) < EVERY:
        return None
    db.put("policy_last", now)                    # even if reading fails, not again until next week
    return check(now)
