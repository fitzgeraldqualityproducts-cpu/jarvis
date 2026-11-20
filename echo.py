"""Stops Jarvis hearing himself.

His speakers are near the microphone, so the mic hears his own voice while he talks and for a
moment after (the end of a word still coming out of the speakers, or bouncing round the room).
Speech-to-text then turns that into words, sometimes slightly wrong ones ("weather" heard as
"whether"), and he'd answer part of his own reply. This works out which heard words are really his:

  - while he talks, a talk-over only counts if it's mostly words he isn't saying (close mishearings
    of his own words count as his), on 3 checks in a row, and louder than his own echo;
  - right after he stops, the mic waits a moment for his voice to die away before it listens;
  - anything heard in the next few seconds that's mostly a stretch of what he just said is ignored,
    and his words at the start of what you said are cut off.

Saying "stop" or "Jarvis", or clapping, still interrupts him exactly as before.
Config: "echo_guard": false turns the after-speech checks off; "echo_gap" (0.5 s) is how long he
waits after talking; "barge_in_checks" (3) and "barge_in_louder" (1.2) tune talk-over."""
import difflib
import json
import re
import time
from pathlib import Path

CONFIG = Path(__file__).with_name("config.json")
LAST = {"end": 0.0, "text": ""}     # what he last said out loud, and when he finished
FILLER = {"you", "thank", "thanks", "the", "a", "uh", "um", "hmm", "oh", "so", "and", "i", "it", "is", "bye",
          "watching", "for", "to", "of", "okay", "ok", "yeah", "music", "applause", "laughter"}


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def words(text):
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def spoke(text):
    """voice.py calls this when he stops talking."""
    LAST.update(end=time.time(), text=text or "")


def gap():
    """Seconds to keep waiting before listening, so his own voice has died away."""
    if not _cfg().get("echo_guard", True):
        return 0.0
    return max(0.0, LAST["end"] + float(_cfg().get("echo_gap", 0.5)) - time.time())


def _mine(word, vocab):
    if word in vocab:
        return True
    return len(word) > 3 and bool(difflib.get_close_matches(word, vocab, 1, 0.75))


def new_words(heard, own):
    """While he's talking: the words the mic heard that aren't his own (or a near-miss of them) or
    noise, if it looks like a person talking: at least two, and most of what was heard."""
    ws = words(heard)
    vocab = list(set(words(own)))
    new = [w for w in ws if w not in FILLER and not _mine(w, vocab)]
    return new if len(new) >= 2 and len(new) >= 0.6 * len(ws) else []


def _match(heard, own):
    """How the heard words line up with a stretch of his words: (matched words, where his echo
    at the start of what was heard ends)."""
    vocab = list(set(own))
    norm = [w if w in vocab else (difflib.get_close_matches(w, vocab, 1, 0.75) or [w])[0] for w in heard]
    sm = difflib.SequenceMatcher(None, norm, own, autojunk=False)
    matched = sum(b.size for b in sm.get_matching_blocks())
    lead = 0
    for b in sm.get_matching_blocks():         # echo at the start: the end of his reply, or a long stretch of it
        tail = b.b + b.size >= len(own) - 1
        if b.size and b.a <= lead + (1 if lead else 0) and (b.size >= 5 or (b.size >= 2 and tail)):
            lead = b.a + b.size
    return matched, lead


def clean(text, window=12.0):
    """After he's talked: '' if what was heard is just his own voice, otherwise the text with
    any of his words at the start taken off."""
    if not text or not _cfg().get("echo_guard", True) or time.time() - LAST["end"] > window:
        return text
    heard, own = words(text), words(LAST["text"])
    if not heard or not own:
        return text
    matched, lead = _match(heard, own)
    content = [w for w in heard if w not in FILLER] or heard
    if len(heard) >= 2 and matched >= 0.75 * len(heard) and lead >= len(heard) - 1:
        print(f"(ignored my own voice: {text!r})")
        return ""
    if len(heard) == 1 and len(content[0]) > 3 and time.time() - LAST["end"] < 3 and \
            content[0] in own[-4:]:
        print(f"(ignored my own voice: {text!r})")    # the tail of his last word
        return ""
    if lead >= 2 and lead < len(heard):
        rest = text.split()
        cut = " ".join(rest[lead:]) if len(rest) == len(heard) else text
        print(f"(took my own words off the start: {text!r} -> {cut!r})")
        return cut
    return text
