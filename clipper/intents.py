"""What you say -> clipper commands. Pure text matching (no side effects), so it's easy to test.

Jarvis's fast path (jarvis.py) uses this for the everyday commands; anything else about clips goes to
his brain, which can call the same functions as tools (skills/clipper.py)."""
import re

NUM = {"one": 1, "a": 1, "an": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
       "nine": 9, "ten": 10, "a couple": 2, "a few": 3, "couple": 2, "few": 3}
CLIP = r"(?:shorts?|clips?|youtube shorts?|tik ?toks?|reels?|highlights?|best (?:moments|parts|bits))"
SOURCE = r"(?:this|that|the|my) (?:podcast|video|vod|stream|episode|interview|link|file|clip)"
STYLE = {"premium": "premium", "minimal": "minimal", "minimalist": "minimal", "clean": "clean", "bold": "bold",
         "podcast": "podcast", "kinetic": "kinetic", "gaming": "gaming", "educational": "educational",
         "high contrast": "educational", "high-contrast": "educational", "cinematic": "cinematic", "pop": "pop",
         "animated": "pop", "highlighted": "highlight", "highlight": "highlight", "viral": "bold", "professional": "clean"}
URL = re.compile(r"(https?://\S+|(?:www\.)?(?:youtube\.com|youtu\.be|twitch\.tv)/\S+)", re.I)


def _count(t):
    m = re.search(r"\b(\d{1,2}|one|two|three|four|five|six|seven|eight|nine|ten|a couple|a few|couple|few)\s+(?:more\s+)?"
                  r"(?:new\s+)?(?:youtube\s+)?(?:shorts?|clips?|highlights?|reels?)", t)
    if not m:
        return None
    v = m.group(1)
    return int(v) if v.isdigit() else NUM.get(v)


def parse(t, original=""):
    """t: lowercased command. Returns (intent, args) or None."""
    t = t.strip(" .?!")
    url = URL.search(original or t)
    if re.search(r"\b(?:emergency stop|stop (?:all )?(?:the )?(?:clipping|clipper|uploads|uploading|publishing)|"
                 r"kill (?:the )?clipper|clipper (?:emergency )?stop)\b", t):
        return "stop", {}
    if re.search(r"\b(?:pause (?:all )?(?:the )?(?:clipping|clipper)(?: jobs)?|pause (?:all )?clipping jobs)\b", t):
        return "pause", {}
    if re.search(r"\b(?:resume|restart|unpause|continue|start) (?:the )?(?:clipping|clipper)(?: jobs)?\b|\bclipper back on\b", t):
        return "resume", {}
    if re.search(r"\b(?:show|open|pull up|bring up|let me see|display)(?: me)? (?:the |my )?(?:clips?|shorts?) "
                 r"(?:awaiting|waiting for|that need|needing|pending) (?:my )?(?:approval|ok|okay|review)|"
                 r"\b(?:what|which) clips (?:are )?(?:waiting|need my (?:ok|okay|approval))\b|\bapproval (?:center|queue)\b", t):
        return "show", {"tab": "approvals"}
    if re.search(r"\b(?:show|open|pull up|bring up)(?: me)? (?:the |my )?(?:clipper|clip(?:ping)? dashboard|shorts dashboard|"
                 r"clip library|shorts library|clipping (?:queue|jobs))\b", t):
        return "show", {"tab": "overview"}
    m = re.search(r"\b(allow|turn on|enable|switch on|turn off|disable|switch off|stop) (?:automatic |auto )?"
                  r"(public|scheduled) (?:publishing|uploads|posting)\b", t)
    if m:
        return "permission", {"which": "publish_public" if m.group(2) == "public" else "schedule_public",
                              "on": m.group(1) in ("allow", "turn on", "enable", "switch on")}
    m = re.search(r"\b(allow|turn on|enable|switch on|turn off|disable|switch off) (?:private )?(?:youtube )?uploads\b", t)
    if m:
        return "permission", {"which": "upload_private", "on": m.group(1) in ("allow", "turn on", "enable", "switch on")}
    if re.search(r"\bupload (?:the |all )?(?:approved|ready) (?:clips|shorts)\b|\bpublish (?:the |all )?approved (?:clips|shorts)\b", t):
        return "upload_approved", {"public": bool(re.search(r"\bpublic(?:ly)?\b", t))}
    m = re.search(r"\b(?:approve|ok|okay) clip (?:number )?(\d+)\b", t)
    if m:
        return "approve", {"clip": int(m.group(1))}
    m = re.search(r"\b(?:reject|delete|drop|bin) clip (?:number )?(\d+)\b", t)
    if m:
        return "reject", {"clip": int(m.group(1))}
    m = re.search(r"\b(?:redo|regenerate|remake|replace) clip (?:number )?(\d+)\b", t)
    if m:
        return "regenerate", {"clip": int(m.group(1))}
    if re.search(r"\b(?:forget|reset|undo) (?:everything |all |what )?(?:the )?clipper(?: has)? (?:learned|learnt)|"
                 r"\bforget what (?:the clipper|you) learned (?:about|from) my (?:clips|edits)\b", t):
        return "unlearn", {}
    if re.search(r"\bwhat (?:has|did) (?:the )?clipper (?:learned|learnt|learn)\b|\bwhat have you learned (?:about|from) my "
                 r"(?:clips|edits|shorts)\b|\bclipper lessons\b", t):
        return "lessons", {}
    if re.search(r"\b(?:today'?s|daily|the) (?:processing|clipping|clipper|shorts) report\b|\bclipping report\b|"
                 r"\bhow did (?:the )?clipping go\b", t):
        return "report", {}
    m = re.search(r"\banaly[sz]e (?:which of )?(?:my )?(?:last |latest |recent )?(\d+)? ?(?:youtube )?shorts\b|"
                  r"\bwhich (?:of my )?(?:last )?(\d+)? ?shorts (?:did|performed|do) (?:the )?best\b|"
                  r"\bhow (?:are|did) my shorts (?:doing|do|perform)\b", t)
    if m:
        n = next((int(g) for g in m.groups() if g), 20)
        return "analyze", {"last": n}
    if re.search(r"\buse (?:the )?(?:highest|best|top)[- ]performing (?:editing |caption )?style\b", t):
        return "best_style", {}
    m = re.search(r"\buse (?:my |the )?(premium|minimal(?:ist)?|clean|bold|kinetic|educational|high[- ]contrast|podcast|gaming|"
                  r"cinematic|pop|animated|highlight(?:ed)?|viral|professional)"
                  r"(?: podcast| keyword| pop| white| viral| professional)? (?:caption|subtitle)s?(?: style)?\b", t)
    if m:
        return "style", {"style": STYLE[m.group(1).replace("minimalist", "minimal")]}
    m = re.search(r"\bprioriti[sz]e (?:clips (?:with|that have) )?(?:strong |good |great )?(?:opening )?(hooks?|stories|"
                  r"education(?:al)?|entertainment|emotion(?:al)?|funny|humou?r)\b", t)
    if m:
        key = {"hook": "hook", "hooks": "hook", "stories": "story", "education": "education", "educational": "education",
               "entertainment": "entertainment", "emotion": "emotion", "emotional": "emotion", "funny": "entertainment",
               "humor": "entertainment", "humour": "entertainment"}[m.group(1)]
        return "prioritize", {"criterion": key}
    if re.search(r"\b(?:how'?s|how is|what'?s|what is)(?: the)? (?:clipper|clipping)(?: doing| going| up to| status)?\b|"
                 r"\bclipp(?:er|ing) status\b|\bstatus of (?:the )?clipp(?:er|ing)\b", t):
        return "status", {}
    if re.search(r"\b(?:process|clip|check) (?:the )?(?:latest|newest|new) videos? from (?:my |the )?(?:approved )?sources\b|"
                 r"\b(?:check|scan) (?:my )?(?:approved )?sources\b|\bfind (?:me )?new videos to clip\b", t):
        return "discover", {}
    if re.search(r"\bmy (?:last|latest|most recent|newest|previous) (?:twitch )?(?:stream|vod|broadcast)\b", t) and \
            re.search(rf"\b(?:clip|cut|{CLIP})\b", t):
        return "clip", {"twitch_latest": True, "count": _count(t)}
    if re.search(rf"\b(?:find|get|pull|make|generate|create|cut|clip|give me)\b.*\b{CLIP}\b.*\b(?:from|of|in|out of)\b", t) and \
            (url or re.search(rf"\b{SOURCE}\b", t)):
        return "clip", {"url": url.group(1) if url else None, "count": _count(t)}
    if re.search(rf"^(?:clip|clipper)\b", t) and url:
        return "clip", {"url": url.group(1), "count": _count(t)}
    m = re.search(r"\bi have permission (?:for|to clip) (?:video|job) (?:number )?(\d+)\b", t)
    if m:
        return "grant", {"job": int(m.group(1))}
    return None
