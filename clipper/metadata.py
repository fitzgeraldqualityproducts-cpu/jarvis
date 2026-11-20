"""Titles, descriptions, hashtags and keywords for each clip.

Usually the context check (moments.verify) writes them in the same Claude call; otherwise one call
per job writes them for every clip that still needs them, in the channel's brand voice, from what was
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

Use only what is actually said in each clip. Never invent events or claims. Titles: accurate, curious, concise,
natural, under 70 characters, relevant keywords, no ALL CAPS, no misleading clickbait, at most one emoji.
Descriptions: 1-2 short sentences. 3-5 relevant hashtags (no #shorts, it's added automatically), no stuffing.
Return ONLY a JSON array: [{{"clip": n, "titles": [5 options, best first], "descriptions": [3 options],
"hashtags": ["#..."], "keywords": [6-10 search terms], "hook_texts": [2 short accurate on-screen openers, 2-6 words],
"pinned": [2 pinned comment ideas about the clip's real topic], "audience": "who it's for, a few words"}}]"""


def generate(clips, video, channel, force=False):
    """Fills titles (5 options), descriptions (3), hashtags, tags, hook text ideas and pinned comment
    ideas for each clip dict (in place). Clips whose context check already wrote them (c["meta"]) don't
    cost another Claude call."""
    need = [n for n, c in enumerate(clips) if force or not (c.get("meta") or {}).get("titles")]
    got = {}
    if llm.available() and need:
        blocks = [f"CLIP {n} ({clips[n]['duration']:.0f}s, {clips[n].get('category') or clips[n].get('hook_type')}): "
                  f"{clips[n]['transcript'][:1500]}" for n in need]
        try:
            res = llm.ask_json(PROMPT.format(
                channel=channel["name"], niches=", ".join(channel.get("niches", [])),
                voice=channel.get("brand", {}).get("voice", ""), audience=channel.get("audience", "general"),
                title=str(video.get("title") or "")[:150], creator=video.get("creator") or "unknown",
                clips="\n\n".join(blocks)), timeout=240)
            for r in res if isinstance(res, list) else []:
                if isinstance(r, dict):
                    try:
                        got[int(r.get("clip", -1))] = r
                    except (TypeError, ValueError):
                        pass
        except llm.LLMError as e:
            print(f"(clipper: titles fell back to plain ones: {e})")
    for n, c in enumerate(clips):
        r = dict(c.get("meta") or {})
        if n in got:
            r.update({k: v for k, v in got[n].items() if v})
        fill(c, r, video, channel)
    return clips


def fill(c, r, video, channel):
    titles = _uniq([clean_title(t) for t in (r.get("titles") or []) if clean_title(t)])
    if c.get("title") and clean_title(c["title"]) and clean_title(c["title"]).lower() not in [t.lower() for t in titles]:
        titles.append(clean_title(c["title"]))
    if not titles:
        titles = [clean_title(c.get("hook") or c["transcript"])]
    tags = [_tag(h) for h in (r.get("hashtags") or []) if _tag(h)][:5]
    tags = _uniq(tags + [h for h in channel.get("hashtags_always", ["#shorts"]) if h])
    descs = [str(d).strip() for d in (r.get("descriptions") or []) if str(d).strip()]
    if r.get("description") and str(r["description"]).strip() not in descs:
        descs.insert(0, str(r["description"]).strip())
    if not descs:
        descs = [str(c.get("reason") or "").strip()]
    credit = credit_line(video, channel)
    full = [d + ("\n\n" + " ".join(tags) if tags else "") + ("\n\n" + credit if credit else "") for d in descs[:3]]
    hooks = [clean_title(h)[:40] for h in (r.get("hook_texts") or []) if clean_title(h)][:3]
    pinned = [str(p).strip()[:500] for p in (r.get("pinned") or ([r["pinned_comment"]] if r.get("pinned_comment") else [])) if str(p).strip()][:3]
    c.update(title=titles[0], titles=titles[:5], description=full[0][:4800], descriptions=[d[:4800] for d in full],
             hashtags=tags, tags=_uniq([str(k)[:60] for k in (r.get("keywords") or [])] + [c.get("topic", "")])[:15],
             audience=str(r.get("audience") or channel.get("audience", "general"))[:80],
             pinned_comment=pinned[0] if pinned else "", pinned_ideas=pinned, hook_texts=hooks,
             category_id=channel.get("category_id", "24"))
    if not c.get("hook_text") and hooks and channel.get("hook_overlay", True):
        c["hook_text"] = hooks[0]
    return c


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
