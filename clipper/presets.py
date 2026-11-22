"""Clipping presets: one choice that changes HOW moments are picked and HOW they're edited.

A preset is not a label. Each one changes, for real:
  - what Claude is asked to look for (a focus paragraph added to the moment-finding prompt)
  - how the 0-100 score weighs its 12 parts (multipliers on top of the channel's own priorities)
  - which kinds of moments get a small bonus or penalty (capped, so a great clip of another kind still wins)
  - the clip length range, when you haven't chosen one for the video or the channel
  - the editing: how hard pauses are tightened, zooms/punch-ins and their strength, the caption style
    (only when the channel's caption style is "auto")

Your own settings always win: a preset only fills in editing values you've left at their defaults,
and a length you pick for a video beats the preset's length.

    balanced       natural clips with professional editing (the defaults)
    viral          strongest hooks, emotion, surprise and shareability; tight pacing
    educational    complete explanations and real insights; calm editing, nothing cut before the takeaway
    entertainment  humour, reactions and memorable moments; lively editing
    storytelling   whole stories with a beginning, middle and end; longer clips are fine
    podcast        conversation with its context, the person making the point in view, clean captions
"""
from clipper import settings

PRESETS = {
    "balanced": {
        "label": "Balanced",
        "about": "Natural clips with professional editing.",
        "weights": {},
        "categories": {},
        "length": None,
        "focus": "",
        "edit": {},
    },
    "viral": {
        "label": "Viral potential",
        "about": "Strong hooks, emotion and surprise first. Tight pacing.",
        "weights": {"hook": 1.6, "shareability": 1.4, "curiosity": 1.3, "emotion": 1.3, "replay": 1.2, "payoff": 1.2,
                    "story": 0.8, "relevance": 0.8},
        "categories": {"funny": 3, "reaction": 4, "controversial": 3, "revelation": 4, "high_energy": 3, "one_liner": 4,
                       "suspense": 2, "educational": -2},
        "length": (15, 45),
        "focus": "PRESET: VIRAL POTENTIAL. Rank scroll-stopping moments first: a hook in the first 1-2 seconds "
                 "(bold claim, surprising fact, strong reaction, conflict), high emotion, surprise, and moments people "
                 "would share or rewatch. Prefer tight clips that get to the point fast, but never at the cost of "
                 "the context the moment needs or by misrepresenting anyone.",
        "edit": {"edit_intensity": "strong", "max_gap": 0.4, "jump_cuts": True, "punch_ins": True, "zooms": True},
    },
    "educational": {
        "label": "Educational",
        "about": "Complete explanations and real insights. Calm editing.",
        "weights": {"clarity": 1.6, "relevance": 1.5, "payoff": 1.4, "curiosity": 1.2, "story": 1.1,
                    "entertainment": 0.7, "emotion": 0.7, "shareability": 0.9},
        "categories": {"educational": 5, "revelation": 2, "opinion": 1, "funny": -1, "fail": -2, "high_energy": -1},
        "length": (30, 75),
        "focus": "PRESET: EDUCATIONAL. Rank complete explanations first: the question or problem, the explanation, "
                 "and the takeaway, all inside the clip. A clip must teach one clear thing fully on its own. Never "
                 "end before the takeaway, and include the definition or example the point depends on.",
        "edit": {"edit_intensity": "subtle", "max_gap": 0.65, "punch_ins": False, "caption_style": "educational"},
    },
    "entertainment": {
        "label": "Entertainment",
        "about": "Humour, reactions and memorable moments. Lively editing.",
        "weights": {"entertainment": 1.6, "replay": 1.3, "emotion": 1.3, "shareability": 1.2, "hook": 1.1,
                    "relevance": 0.8, "clarity": 0.9},
        "categories": {"funny": 5, "reaction": 4, "fail": 4, "one_liner": 3, "high_energy": 3, "achievement": 2,
                       "relatable": 2, "educational": -3},
        "length": (15, 45),
        "focus": "PRESET: ENTERTAINMENT. Rank the funniest and most entertaining moments first: jokes with their "
                 "setup and punchline, big reactions, fails, chaotic or high-energy moments, one-liners people quote. "
                 "Always include the setup a joke needs and the reaction after the punchline.",
        "edit": {"edit_intensity": "strong", "max_gap": 0.45, "punch_ins": True, "zooms": True},
    },
    "storytelling": {
        "label": "Storytelling",
        "about": "Whole stories with a beginning, middle and satisfying end.",
        "weights": {"story": 1.8, "payoff": 1.6, "emotion": 1.2, "clarity": 1.2, "pacing": 0.8, "hook": 0.9},
        "categories": {"story": 6, "suspense": 3, "emotional": 2, "revelation": 1, "one_liner": -2},
        "length": (40, 90, 120),
        "focus": "PRESET: STORYTELLING. Rank complete stories first: a clear beginning (who/where/what), a "
                 "development, and a conclusion or twist that lands. Include the setup and never cut before the "
                 "resolution. Longer clips are fine when the story needs them.",
        "edit": {"edit_intensity": "subtle", "max_gap": 0.7, "punch_ins": False, "caption_style": "cinematic"},
    },
    "podcast": {
        "label": "Podcast",
        "about": "Conversation with its context, the speaker in view, clean captions.",
        "weights": {"clarity": 1.4, "curiosity": 1.2, "relevance": 1.2, "payoff": 1.2, "story": 1.1, "emotion": 0.9},
        "categories": {"opinion": 3, "argument": 3, "controversial": 2, "revelation": 2, "story": 2, "educational": 1,
                       "gameplay": -4},
        "length": (30, 75),
        "focus": "PRESET: PODCAST. Rank conversational moments first: strong opinions, disagreements, surprising "
                 "answers, personal stories. Keep the question together with its answer and include the other "
                 "person's reaction when it matters. The clip must make sense to someone who never heard the episode.",
        "edit": {"edit_intensity": "normal", "max_gap": 0.6, "punch_ins": False, "transitions": True,
                 "caption_style": "podcast"},
    },
}
NAMES = list(PRESETS)
INTENSITY = {"subtle": {"zoom": 0.55, "punch_gap": 6.0}, "normal": {"zoom": 1.0, "punch_gap": 3.5},
             "strong": {"zoom": 1.4, "punch_gap": 2.5}}


def get(name):
    return PRESETS.get(name or "balanced") or PRESETS["balanced"]


def public():
    """For the dashboard's pickers."""
    out = []
    for k, p in PRESETS.items():
        ln = p["length"]
        out.append({"id": k, "label": p["label"], "about": p["about"],
                    "length": f"{ln[0]}-{ln[1]}s" if ln else "auto"})
    return out


def length_for(name):
    """The preset's (shortest, longest[, hard longest]) or None."""
    ln = get(name)["length"]
    if not ln:
        return None
    return tuple(float(x) for x in ln)


def apply(ch, name=None):
    """The channel's settings with a preset applied (see the module notes). name=None uses the
    channel's own default preset."""
    name = name if name in PRESETS else ch.get("preset", "balanced")
    if name not in PRESETS:
        name = "balanced"
    p = PRESETS[name]
    out = dict(ch, preset=name, preset_focus=p["focus"], preset_weights=dict(p["weights"]),
               category_bonus=dict(p["categories"]))
    for k, v in p["edit"].items():
        if k == "caption_style":
            if ch.get("caption_style", "auto") == "auto":
                out["preset_style"] = v
            continue
        default = settings.CHANNEL_DEFAULTS.get(k)
        if k not in settings.CHANNEL_DEFAULTS or ch.get(k, default) == default:
            out[k] = v
    return out
