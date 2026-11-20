"""Titles, descriptions, hashtags and keywords for each clip.

One Claude call per job writes them for every clip, in the channel's brand voice, from what was
actually said. Rules: accurate, curious, no clickbait or claims the clip doesn't back up, a few
relevant hashtags, no keyword stuffing. Videos that aren't the owner's get a credit line (and Creative
Commons videos always do, as the licence requires). If Claude isn't available, plain titles are
made from the clip's opening line."""
import re

from clipper import llm

PROMPT = """Write YouTube Shorts metadata for these clips from one video, for the channel "{channel}"
(niches: {niches}; brand voice: {voice}; audience: {audience}).
Source video: "{title}" by {creator}.

{clips}

Rules: titles must be accurate to what is said in the clip, curious, natural, under 70 characters, no ALL CAPS,
no misleading claims, no emojis spam (at most one). Description: 1-3 short sentences about the clip, then nothing else.
3-5 relevant hashtags (no #shorts, it's added automatically). 6-12 search keywords. A pinned comment that invites
discussion about the clip's actual topic.
Return ONLY a JSON array: [{{"clip": n, "titles": ["best", "alt 2", "alt 3"], "description": "...",
"hashtags": ["#..."], "keywords": ["..."], "audience": "who it's for, a few words", "pinned_comment": "..."}}]"""


def generate(clips, video, channel):
    """Fills titles/description/hashtags/tags/pinned_comment for each clip dict (in place)."""
    got = {}
    if llm.available() and clips:
        blocks = [f"CLIP {n} ({c['duration']:.0f}s, hook type {c.get('hook_type')}): {c['transcript'][:1500]}"
                  for n, c in enumerate(clips)]
        try:
            res = llm.ask_json(PROMPT.format(
                channel=channel["name"], niches=", ".join(channel.get("niches", [])),
                voice=channel.get("brand", {}).get("voice", ""), audience=channel.get("audience", "general"),
                title=str(video.get("title") or "")[:150], creator=video.get("creator") or "unknown",
                clips="\n\n".join(blocks)), timeout=240)
            got = {int(r.get("clip", -1)): r for r in res if isinstance(r, dict)} if isinstance(res, list) else {}
        except llm.LLMError as e:
            print(f"(clipper: titles fell back to plain ones: {e})")
    for n, c in enumerate(clips):
        r = got.get(n, {})
        titles = [clean_title(t) for t in (r.get("titles") or []) if clean_title(t)]
        if c.get("title") and clean_title(c["title"]) and clean_title(c["title"]) not in titles:
            titles.append(clean_title(c["title"]))
        if not titles:
            titles = [clean_title(c.get("hook") or c["transcript"])]
        tags = [_tag(h) for h in (r.get("hashtags") or []) if _tag(h)][:5]
        tags = _uniq(tags + [h for h in channel.get("hashtags_always", ["#shorts"]) if h])
        desc = str(r.get("description") or c.get("reason") or "").strip()
        desc = desc + "\n\n" + " ".join(tags) if tags else desc
        credit = credit_line(video, channel)
        if credit:
            desc += "\n\n" + credit
        c.update(title=titles[0], titles=titles[:5], description=desc[:4800], hashtags=tags,
                 tags=_uniq([str(k)[:60] for k in (r.get("keywords") or [])] + [c.get("topic", "")])[:15],
                 audience=str(r.get("audience") or channel.get("audience", "general"))[:80],
                 pinned_comment=str(r.get("pinned_comment") or "")[:500], category_id=channel.get("category_id", "24"))
    return clips


def credit_line(video, channel):
    basis = str(video.get("auth_basis") or "")
    mine = basis.startswith("your own") or video.get("platform") == "file"
    if "Creative Commons" in basis:
        return f"Source: \"{video.get('title')}\" by {video.get('creator')} ({video.get('url')}), licensed CC BY."
    if not mine and channel.get("credit_sources", True):
        return f"Source: \"{video.get('title')}\" by {video.get('creator')} ({video.get('url')}), used with permission."
    return ""


def clean_title(t):
    t = re.sub(r"\s+", " ", str(t or "")).strip().strip('"')
    if not t:
        return ""
    if t.isupper() and len(t) > 12:
        t = t.title()
    if len(t) > 95:
        t = t[:92].rsplit(" ", 1)[0] + "..."
    return t.replace("<", "").replace(">", "")       # YouTube rejects angle brackets


def _tag(h):
    h = re.sub(r"[^\w]", "", str(h))
    return f"#{h}" if 1 < len(h) <= 40 and h.lower() != "shorts" else ""


def _uniq(items):
    seen, out = set(), []
    for x in items:
        if x and x.lower() not in seen:
            seen.add(x.lower())
            out.append(x)
    return out
