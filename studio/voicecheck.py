"""Voice check: every sentence the narrator reads is heard back and re-recorded if it came out garbled, and science
words and units are read the way a person would say them.

spoken(text)   the text as the narrator should read it: "km/s" -> "kilometers per second", "CO2" -> "C O two",
               "°C" -> "degrees Celsius", "%" -> "percent" ... (PRONOUNCE, plus config "pronounce": {"word": "say"}
               for Connor's own additions). Captions keep the script's own text.
check(...)     Whisper (on this computer, free) listens to the whole voiceover once; each sentence's words are
               compared with what it should say (numbers compared as words, so "1,480" = "one thousand four
               hundred eighty"). A sentence with too many missing or extra words is "unclear".
Chatterbox narration (studio/narration.py): unclear sentences are recorded again with other random seeds (up to
REDO_ROUNDS times, same voice and delivery) and the clearest take of each is spliced in place. If a sentence is
still badly garbled after that, the voiceover falls back to Kokoro, which never garbles. Config "voice_check":
false turns the hearing-back off; the pronunciation list always applies."""
import re
from difflib import SequenceMatcher

import numpy as np

UNCLEAR_AT = 0.34        # share of a sentence's words missing or wrong (or extra) that counts as unclear
GARBLED_AT = 0.6         # still this bad after the retakes: use Kokoro instead
REDO_ROUNDS = 2
SEEDS = (101, 202)

# word or symbol -> how it's said (whole words only; case matters for formulas)
PRONOUNCE = {
    "km/s": "kilometers per second", "km/h": "kilometers per hour", "m/s": "meters per second",
    "m/s²": "meters per second squared", "m/s2": "meters per second squared", "mph": "miles per hour",
    "km²": "square kilometers", "m²": "square meters", "m³": "cubic meters", "cm³": "cubic centimeters",
    "°C": "degrees Celsius", "°F": "degrees Fahrenheit", "°": "degrees", "%": "percent", "≈": "about",
    "~": "about", "×": "times", "÷": "divided by", "±": "plus or minus", "→": "to", "²": " squared",
    "³": " cubed",
    "km": "kilometers", "cm": "centimeters", "mm": "millimeters", "nm": "nanometers", "µm": "micrometers",
    "kg": "kilograms", "kcal": "kilocalories", "mg": "milligrams", "kW": "kilowatts", "MW": "megawatts", "GW": "gigawatts",
    "kWh": "kilowatt hours", "Hz": "hertz", "kHz": "kilohertz", "MHz": "megahertz", "GHz": "gigahertz",
    "dB": "decibels", "AU": "astronomical units", "ly": "light-years", "mi": "miles", "ft": "feet",
    "lbs": "pounds", "lb": "pounds", "oz": "ounces", "hrs": "hours", "hr": "hour", "min": "minutes",
    "sec": "seconds", "yr": "years", "yrs": "years", "approx.": "about", "vs.": "versus", "vs": "versus",
    "e.g.": "for example", "i.e.": "that is", "etc.": "and so on",
    "CO2": "C O two", "H2O": "H two O", "O2": "O two", "H2": "H two", "CH4": "methane", "N2": "N two",
    "DNA": "D N A", "RNA": "R N A", "UV": "U V", "LED": "L E D", "GPS": "G P S", "ISS": "I S S",
    "JWST": "J W S T", "NASA": "NASA", "ESA": "E S A", "CERN": "CERN", "LHC": "L H C", "SI": "S I",
    "Betelgeuse": "Beetle-juice", "Uranus": "YOOR-uh-nus", "Io": "EYE-oh", "Pluto": "Pluto",
    "Sagittarius A*": "Sagittarius A star", "TRAPPIST-1": "Trappist one",
}
UNIT_AFTER_NUMBER = {"m", "g", "s", "K", "N", "J", "W", "V", "A", "L", "t"}     # only after a number
UNIT_WORDS = {"m": "meters", "g": "grams", "s": "seconds", "K": "kelvin", "N": "newtons", "J": "joules",
              "W": "watts", "V": "volts", "A": "amps", "L": "liters", "t": "tons"}


def _table():
    try:
        from studio import config
        extra = config.general().get("pronounce") or {}
    except Exception:
        extra = {}
    t = dict(PRONOUNCE)
    t.update({str(k): str(v) for k, v in (extra.items() if isinstance(extra, dict) else []) if str(k).strip()})
    return t


def spoken(text):
    """The text as it should be read aloud (see PRONOUNCE)."""
    s = " ".join(str(text).split())
    table = _table()
    for k in sorted(table, key=len, reverse=True):
        v = table[k]
        if re.match(r"\w", k):
            # a word: whole words only, also straight after a number ("40km" -> "40 kilometers")
            end = r"(?!\w)" if k[-1] in ".*" else r"(?![\w/²³])"
            s = re.sub(rf"(?<![\w/]){re.escape(k)}{end}", v, s)
            s = re.sub(rf"(?<=\d){re.escape(k)}{end}", " " + v, s)
        elif k in ("²", "³"):
            s = s.replace(k, v)
        else:
            s = s.replace(k, " " + v.strip() + " ")
    s = re.sub(r"(\d)\s*(" + "|".join(UNIT_AFTER_NUMBER) + r")(?![\w/])",
               lambda m: f"{m[1]} {UNIT_WORDS[m[2]]}", s)
    return re.sub(r"\s+([,.;:!?])", r"\1", " ".join(s.split()))


# ---------- hearing it back ----------

ONES = "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen " \
       "seventeen eighteen nineteen".split()
TENS = "_ _ twenty thirty forty fifty sixty seventy eighty ninety".split()
BIG = ((10 ** 12, "trillion"), (10 ** 9, "billion"), (10 ** 6, "million"), (1000, "thousand"), (100, "hundred"))


def say_number(n):
    """An integer as words: 1480 -> "one thousand four hundred eighty"."""
    n = int(n)
    if n < 0:
        return "minus " + say_number(-n)
    if n < 20:
        return ONES[n]
    if n < 100:
        return TENS[n // 10] + ("" if n % 10 == 0 else " " + ONES[n % 10])
    for size, name in BIG:
        if n >= size:
            rest = n % size
            return say_number(n // size) + " " + name + ("" if not rest else " " + say_number(rest))
    return str(n)


def _num_words(tok):
    comma = "," in tok
    tok = tok.replace(",", "")
    if re.fullmatch(r"\d+", tok) and len(tok) <= 15:
        if not comma and len(tok) == 4 and 1100 <= int(tok) <= 2099 and int(tok) % 100 >= 10:   # a year: "nineteen forty-seven"
            return say_number(int(tok[:2])) + " " + say_number(int(tok[2:]))
        return say_number(int(tok))
    m = re.fullmatch(r"(\d+)\.(\d+)", tok)
    if m and len(m[1]) <= 15:
        return say_number(int(m[1])) + " point " + " ".join(ONES[int(d)] for d in m[2])
    return tok


def tokens(text):
    """Words to compare, with numbers as words and hyphens/punctuation gone."""
    out = []
    for t in str(text).lower().replace("-", " ").split():
        t = re.sub(r"[^\w.,]", "", t).strip(".,")
        if not t:
            continue
        for w in _num_words(t).split():
            w = re.sub(r"[^a-z0-9]", "", w)
            if w and w != "and":
                out.append(w)
    return out


def error(expected, heard):
    """Share of the expected words that weren't heard, plus extra words, over the expected count (0 = perfect)."""
    a, b = tokens(expected), tokens(heard)
    if not a:
        return 0.0
    hit = sum(blk.size for blk in SequenceMatcher(None, a, b, autojunk=False).get_matching_blocks())
    missing, extra = len(a) - hit, max(0, len(b) - hit)
    return round((missing + max(0, extra - 1)) / len(a), 3)      # one stray word (a breath read as "uh") is fine


HEAR = None          # tests swap this: wav path -> [{w, s, e}]


def hear(path):
    if HEAR:
        return HEAR(path)
    from clipper import transcribe
    r = transcribe.transcribe(str(path))
    return r.get("words") if isinstance(r, dict) else r


def by_part(words, parts, rate):
    """What was heard during each part: [text]. A word belongs to the part its middle falls in."""
    out = [[] for _ in parts]
    for w in words or []:
        mid = (float(w.get("s") or 0) + float(w.get("e") or 0)) / 2 * rate
        for i, p in enumerate(parts):
            if p["start"] - 0.15 * rate <= mid <= p["end"] + 0.15 * rate:
                out[i].append(str(w.get("w", "")))
                break
    return [" ".join(x) for x in out]


def scores(path, parts, rate):
    """Error per part of a recording (heard once with Whisper)."""
    heard = by_part(hear(path), parts, rate)
    return [error(p["text"], h) for p, h in zip(parts, heard)], heard


def on():
    try:
        from studio import config
        return config.general().get("voice_check", True) is not False
    except Exception:
        return True


def splice(audio, parts, takes):
    """The recording with some sentences swapped for retakes. takes: {part index: samples}."""
    pieces, pos = [], 0
    for i, p in enumerate(parts):
        pieces.append(audio[pos:p["start"]])
        pieces.append(takes[i] if i in takes else audio[p["start"]:p["end"]])
        pos = p["end"]
    pieces.append(audio[pos:])
    return np.concatenate([np.asarray(x, dtype=np.float32).reshape(-1) for x in pieces])
