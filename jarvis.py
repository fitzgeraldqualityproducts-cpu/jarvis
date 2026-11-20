"""Jarvis: say "Hey Jarvis" or clap twice to wake him, then talk.

  python3 jarvis.py               start Jarvis (menu bar icon + display)
  python3 jarvis.py --no-display  don't open the browser display on start
  python3 jarvis.py --text        type instead of speaking (for testing)
  python3 settings.py             choose which apps Jarvis may open
"""
import datetime
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path


class _Tee:
    """Mirror everything printed into jarvis.log so problems can be diagnosed."""
    def __init__(self, stream, path):
        self.stream, self.file = stream, open(path, "a", buffering=1, encoding="utf-8", errors="replace")
    def write(self, text):
        self.stream.write(text)
        if text.strip():
            stamp = datetime.datetime.now().strftime("%H:%M:%S")
            self.file.write("".join(f"[{stamp}] {line}\n" for line in text.splitlines() if line.strip()))
    def flush(self):
        self.stream.flush()
    def __getattr__(self, name):
        return getattr(self.stream, name)


_LOG = Path(__file__).with_name("jarvis.log")
if __name__ == "__main__" and not os.environ.get("JARVIS_CHILD") and not os.environ.get("JARVIS_CHECK") \
        and "--text" not in sys.argv:
    # Start the watchdog: it runs Jarvis, restarts him if he crashes and stops a second copy.
    import keeper
    sys.exit(keeper.supervise(Path(__file__).resolve()))
if not os.environ.get("JARVIS_CHECK"):          # set when selfedit just checks the code imports
    sys.stdout = _Tee(sys.stdout, _LOG)
    sys.stderr = _Tee(sys.stderr, _LOG)
    print("---- Jarvis starting ----")

import brain  # noqa: E402
import briefing  # noqa: E402
import display  # noqa: E402
import hands  # noqa: E402
import inbox  # noqa: E402
import clarify  # noqa: E402
import hearing  # noqa: E402
import keeper  # noqa: E402
import learn  # noqa: E402
import nightschool  # noqa: E402
import repair  # noqa: E402
import mac  # noqa: E402
import obsidian  # noqa: E402
import personality  # noqa: E402
import memory  # noqa: E402
import organizer  # noqa: E402
import predict  # noqa: E402
import selfedit  # noqa: E402
import speaker  # noqa: E402
import spotify_control as spotify  # noqa: E402
import timeparse  # noqa: E402
import twitch  # noqa: E402
import updater  # noqa: E402
import voice  # noqa: E402
import weather  # noqa: E402
import workforce  # noqa: E402

CONFIG = json.loads(Path(__file__).with_name("config.json").read_text())
SLEEP_RE = re.compile(r"(ok |okay |thanks |thank you )?(goodbye|bye|go to sleep|sleep|that's all|thats all|that is all|"
                      r"dismissed|nothing|never mind|nevermind|no thanks|no thank you|that's it|thats it)( for now)?( jarvis)?")
PAUSED = threading.Event()
TALKING = threading.Event()     # set while he's in a conversation (updates wait for this to clear)
TYPING = threading.Event()      # set while he's answering something typed into the display
HANDLE_LOCK = threading.RLock()  # one command at a time, whether spoken or typed
PENDING = {}          # a question Jarvis asked and is waiting on, e.g. {"kind": "event"}
TIME_WORDS = re.compile(r"\d|noon|midnight|morning|afternoon|evening|tonight|night")
EVENT_FILLER = re.compile(
    r"\b(add|put|schedule|create|make|book|set up|can you|could you|please|i need|i want|"
    r"a new|new|an event|a calendar event|event|a meeting(?! with)|an appointment(?! with)|meeting(?! with)|appointment(?! with)|"
    r"to my calendar|on my calendar|in my calendar|to the calendar|on the calendar|in the calendar|"
    r"to calendar|my calendar|calendar|called|named|titled|something|make it|call it|it's|it is|it)\b")

CAPABILITIES = (
    "I can tell you the weather, your calendar and reminders, and give you a morning briefing. "
    "I can set reminders, timers and alarms, take notes (in Obsidian if you use it), answer questions about your notes, and remember things about you. "
    "I can open apps and websites, search Google or YouTube, close or switch apps, run your Shortcuts, "
    "and control Spotify. Say stream mode when you go live and I'll read your Twitch chat out loud. "
    "After I answer, just keep talking, no need to say my name, and you can talk over me any time. "
    "I learn your routines and offer them before you ask, give you a heads-up before events, and say what do you "
    "think I need to hear what's coming. For bigger jobs, say get the team on, then what you want, and my AI team "
    "of researchers, planners, coders and strategists will work on it in the background. "
    "If I can't do something, I'll work out an upgrade and offer it to you, and say review yourself for my report "
    "card. And you can ask me anything else."
)


def greeting():
    p = briefing.part_of_day()
    return f"{personality.line('greet_open', part=p)} {weather.report()} {personality.line('greet_close')}"


def music(t):
    """Spotify commands. Returns what to say, or None if it isn't a music command."""
    if re.fullmatch(r"(pause|stop)( the)?( music| song| spotify)?|pause it|stop playing", t):
        return spotify.pause()
    if re.fullmatch(r"(resume|unpause|play)( the)?( music| song| spotify)?|keep playing|continue( playing)?", t):
        return spotify.play()
    if re.fullmatch(r"(next|skip)( the)?( song| track| one)?|skip (this|it)( song| track)?|next", t):
        return spotify.next_track()
    if re.fullmatch(r"(previous|last|go back( a| one)?)( the)?( song| track| one)?|play the (previous|last) (song|track)", t):
        return spotify.previous_track()
    if re.search(r"what('s| is) (this song|playing|the song)|what song is (this|playing)|who (sings|is singing) this", t):
        return spotify.now_playing()
    m = re.search(r"(?:set )?(?:the )?volume (?:to )?(\d+)", t)
    if m:
        return spotify.set_volume(m.group(1))
    if re.fullmatch(r"(turn (it|the music|the volume) up|volume up|louder)", t):
        return spotify.change_volume(+15)
    if re.fullmatch(r"(turn (it|the music|the volume) down|volume down|quieter|softer)", t):
        return spotify.change_volume(-15)
    m = re.fullmatch(r"(?:add|queue( up)?|put)\s+(.+?)(?:\s+(?:to|in|on|next in) (?:the |my )?queue)?(?: next)?", t)
    if m and ("queue" in t or t.startswith("queue")):
        return spotify.queue_query(m.group(2))
    m = re.fullmatch(r"play (?:me )?(?:some )?(?:the )?(playlist|album|artist)\s+(.+)", t) \
        or re.fullmatch(r"play (?:me )?(?:some )?(?:the )?(.+?)\s+(playlist|album)", t)
    if m:
        a, b = m.groups()
        kind, query = (a, b) if a in ("playlist", "album", "artist") else (b, a)
        return spotify.play_query(query.removesuffix(" on spotify"), kind)
    m = re.fullmatch(r"play (?:me )?(?:some )?(.+?)(?: on spotify)?", t)
    if m:
        query = m.group(1)
        if query.startswith("music by ") or query.startswith("songs by "):
            return spotify.play_query(query.split(" by ", 1)[1], "artist")
        return spotify.play_query(query, "track")
    return None


LAST_TEXT = {"text": ""}
LAST_SELF_EDIT = {}   # the last change asked for, so "go ahead" still works if a confirmation got lost


def original_case(phrase):
    """Give back the phrase with the capitals the speech recogniser heard (names etc.)."""
    if not phrase:
        return phrase
    m = re.search(re.escape(phrase).replace("\\ ", r"\s+"), LAST_TEXT["text"], re.IGNORECASE)
    return m.group(0) if m else phrase


def add_event_from(text):
    """Pull a title and time out of what was said and add it to the calendar."""
    when, rest = timeparse.parse_when(text)
    title = EVENT_FILLER.sub(" ", rest)
    title = re.sub(r"^(for|about|on|at|to)\s+|\s+(for|on|at|to)$", "", re.sub(r"\s+", " ", title).strip(" ,.")).strip()
    title = re.sub(r"^(a|an|the)\s+", "", title)
    title = original_case(title)
    if not title and not when:
        PENDING["kind"] = "event"
        return "Of course. What's the event, and when is it?"
    if not when:
        PENDING.update(kind="event", title=title)
        return f"When is {title}?"
    if not title:
        PENDING.update(kind="event", when=when, timed=bool(TIME_WORDS.search(text)))
        return "And what should I call it?"
    all_day = not TIME_WORDS.search(text)
    reply = organizer.add_event(title, when, all_day=all_day)
    briefing.refresh_display()
    return reply


SELF_EDIT = [
    re.compile(r"(?:upgrade|update|edit|modify|improve|change|reprogram|rewrite|tweak|fix) "
               r"(?:yourself|your (?:own )?code|your settings|your programming|your behaviou?r)"
               r"(?:,| to| so that| so| and| by)?\s*(.+)"),
    re.compile(r"(?:teach yourself|learn) (?:how )?to (.+)"),
    re.compile(r"(?:add|create|build|make|give yourself) (?:a |an )?(?:new )?(?:feature|command|ability|skill|voice command)\s*"
               r"(?:that|to|which|so|where|for|called)?\s*(.+)"),
    re.compile(r"(?:from now on|in future|in the future),? (.+)"),
    re.compile(r"(?:change|switch|set) your (voice .+|wake word .+|clap .+|name .+|personality .+|accent .+)"),
]


SELF_TARGET = re.compile(
    r"\b(?:your|ur) (?:own )?(?:code|layout|display|look|looks|appearance|interface|design|ui|screen|hud|"
    r"theme|colou?rs?|orb|voice|accent|personality|settings|programming|behaviou?r|skills?|features?|commands?|"
    r"wake word|greeting|responses?|answers?)\b|\byourself\b")
SELF_VERB = re.compile(r"\b(?:update|change|make|redesign|redo|edit|improve|modify|upgrade|tweak|give|add|"
                       r"rewrite|restyle|customi[sz]e|reprogram|fix|teach|turn)\b")


def self_edit_request(t):
    # e.g. "now that you can update your own code, update your layout so you look more high-tech"
    if SELF_TARGET.search(t) and SELF_VERB.search(t) and len(t.split()) >= 4 and \
            not re.search(r"\b(remind|calendar|reminder|timer|alarm|note|spotify|play)\b", t):
        return original_case(t)
    for pattern in SELF_EDIT:
        m = pattern.fullmatch(t)
        if m and len(m.group(1).split()) >= 2:
            return original_case(t)
    return None


def do_self_edit(request):
    voice.say(personality.line("working"), interruptible=False)
    display.update(status="thinking")
    reply, restart = selfedit.apply(request)
    learn.record_result("upgrade", request[:150], restart, detail=reply)
    if restart:
        voice.say(reply + restart_words(), interruptible=False)
        restart_jarvis()
    return reply


def continue_pending(t):
    """Handle the answer to a question Jarvis just asked."""
    kind = PENDING.pop("kind", None)
    if kind == "selfedit":
        request = PENDING.pop("request")
        asks = PENDING.pop("asks", 0)
        idea = PENDING.pop("idea", None)        # an upgrade he suggested himself (learn.py)
        yes = re.search(r"\b(yes|yeah|yep|yup|sure|go ahead|do it|go for it|go through|proceed|ok|okay|"
                        r"please do|absolutely|correct|that's right|make it so|let's do it|send it)\b", t)
        no = re.fullmatch(r"(no|nope|nah|cancel|don't|do not|stop|never mind|forget it|leave it)( (thanks|thank you|it|that))?", t)
        if idea and not yes and not no:
            return None                         # he moved on: drop the suggestion and just do what he asked
        if idea:
            learn.answered(idea, bool(yes))
        LAST_SELF_EDIT.update(request=request, t=time.time())
        if yes:
            return do_self_edit(request)
        if no:
            return "Alright, I'll leave myself as I am."
        if asks >= 2:
            return "I'll hold off for now. Say go ahead if you want me to make that change."
        if len(t.split()) < 3:
            PENDING.update(kind="selfedit", request=request, asks=asks + 1)
            return "Sorry, shall I go ahead with that change? Yes or no."
        # Anything longer is probably an extra detail for the change.
        PENDING.update(kind="selfedit", request=f"{request}. Also: {original_case(t)}", asks=asks + 1)
        return "Got it, I'll include that. Shall I go ahead?"
    if kind != "event":
        return None
    title, when = PENDING.pop("title", None), PENDING.pop("when", None)
    timed = PENDING.pop("timed", False)
    if title and not when:
        return add_event_from(f"{title} {t}")
    if when and not title:
        title = EVENT_FILLER.sub(" ", t).strip(" ,.")
        reply = organizer.add_event(title, when, all_day=not timed)
        briefing.refresh_display()
        return reply
    return add_event_from(t)


def organize(t):
    """Memory, timers, alarms, reminders, calendar, notes, briefing."""
    # memory
    if re.search(r"what do you (know|remember)( about me)?|what have i (told|asked) you to remember", t):
        return memory.recall_summary()
    m = re.fullmatch(r"(?:please )?remember (?:that )?(.+)", t)
    if m and not t.startswith("remember when"):
        return memory.remember(original_case(m.group(1)))
    m = re.fullmatch(r"forget (?:that |about )?(.+)", t)
    if m:
        return memory.forget(m.group(1))

    # email (Mac Mail app)
    if sys.platform != "win32" and \
            re.search(r"\b(read|summari[sz]e|check|go through|any|new|what)\b.*\be-?mails?\b|\bmy (e-?mails?|inbox)\b", t):
        voice.say("One moment, reading your email.")
        return inbox.summary()

    # timers and alarms
    if re.search(r"(how (much time|long)( is)? left|time left|how's my timer|check (the|my) timer)", t):
        return organizer.timers_status()
    if re.search(r"(cancel|stop|delete|clear) (the |my |all )?(timers?|alarms?)", t):
        return organizer.cancel_timers()
    if re.search(r"\btimer\b", t):
        secs = timeparse.parse_duration(t)
        if not secs:
            return "How long should the timer be?"
        label = None
        m = re.search(r"\bfor (?:the |my )?([a-z ]+?)$", t)
        if m and not timeparse.parse_duration(m.group(1)):
            label = f"{m.group(1)} timer"
        return organizer.set_timer(secs, label)
    m = re.search(r"(?:set (?:an |a |my )?alarm|wake me(?: up)?)(?: for| at)?\s*(.+)", t)
    if m:
        when, _ = timeparse.parse_when(m.group(1) if re.search(r"\d|noon|midnight|in ", m.group(1)) else "at " + m.group(1))
        if not when:
            when, _ = timeparse.parse_when("at " + m.group(1))
        if not when:
            return "What time should I set the alarm for?"
        # Alarms without am/pm are usually for the morning.
        if when.hour >= 12 and not re.search(r"\bp\.?\s?m\b|evening|tonight|afternoon|noon", t):
            earlier = when - datetime.timedelta(hours=12)
            if earlier > datetime.datetime.now():
                when = earlier
            elif when.hour - 12 < 12:
                when = earlier + datetime.timedelta(days=1)
        return organizer.set_alarm(when)

    # reminders
    # Obsidian: to-dos, notes and questions about notes (when a vault is set up)
    if obsidian.enabled():
        m = re.fullmatch(r"add (.+?) to my (?:obsidian )?to ?-?do(?:s| list)?", t) \
            or re.fullmatch(r"(?:add (?:a )?to ?-?do|new to ?-?do|to ?-?do)[: ]+(.+)", t)
        if m:
            return obsidian.add_todo(original_case(m.group(1)))
        if re.search(r"(read|list|check|what'?s on|what is on|what are) my (obsidian )?to ?-?dos?( list)?|my to ?-?do list|what do i (need|have) to do", t):
            return obsidian.todos_summary()
        m = re.fullmatch(r"(?:what did i (?:write|note|say)|what have i (?:written|noted)|what do my notes say|"
                         r"search my (?:obsidian |notes? )*(?:for)?|check my notes (?:for|about)|"
                         r"look (?:in|through) my notes (?:for|about)|in my notes,?)\s*(?:about|on|for|regarding)?\s*(.+)", t)
        if m or re.search(r"\b(my|in) (obsidian|notes|vault)\b.*\?|^according to my notes", t):
            return obsidian.ask(original_case(t))
    if re.search(r"what are my reminders|(read|list|check) my reminders|what do i (need|have) to do|my to ?do list|any reminders", t):
        return organizer.reminders_summary(1 if "tomorrow" in t else 0)
    m = re.fullmatch(r"(?:please )?(?:remind me|set a reminder|add a reminder|make a reminder|create a reminder)\s*(?:to |about |that |for )?(.+)", t) \
        or re.fullmatch(r"add (.+?) to my (?:reminders|to ?do list)", t)
    if m:
        when, task = timeparse.parse_when(m.group(1))
        task = re.sub(r"^(to|about|that|i need to|i have to)\s+", "", task)
        if not task:
            return "What should I remind you about?"
        reply = organizer.add_reminder(original_case(task), when)
        briefing.refresh_display()
        return reply

    # calendar: adding events
    if re.search(r"\b(add|put|schedule|create|make|book)\b.*\b(calendar|event|meeting|appointment)\b", t) or \
            re.match(r"schedule\b", t):
        return add_event_from(t)

    # calendar: reading
    if re.search(r"what('s| is)( on)? (my |the )?(calendar|schedule|agenda)|what do i have (on |going on )?(today|tomorrow)|"
                 r"what('s| is) on (today|tomorrow)|(any|do i have any) (meetings|events|appointments)|my (calendar|schedule)", t):
        offset = 1 if "tomorrow" in t else 0
        reply = organizer.calendar_summary(offset)
        rems = organizer.reminders_summary(offset)
        if not rems.startswith("No reminders") and not rems.startswith("I don't"):
            reply += " " + rems
        return reply

    # notes
    m = re.fullmatch(r"(?:take|make|add|write|create) (?:a |me a )?note(?: that| saying| to say)?:?\s*(.+)", t) \
        or re.fullmatch(r"(?:note (?:that|down)|write down|jot down)\s+(.+)", t)
    if m:
        text = original_case(m.group(1))
        return (obsidian.add_note(text) if obsidian.enabled() else None) or organizer.add_note(text)
    if re.fullmatch(r"(take|make) a note", t):
        return "What should the note say? Start with 'take a note', then say it."

    # briefing
    if re.search(r"brief me|briefing|what does my day look like|what's my day look like|how does my day look|catch me up", t):
        return briefing.full()
    return None


BROWSER_RE = r"(chrome|google chrome|safari|firefox|brave|arc|edge|opera)"


def computer(t):
    """Websites, search, apps, Shortcuts."""
    m = re.fullmatch(r"(?:open )?(?:a )?new tab in " + BROWSER_RE + r"(?:,? and)? (?:go to|open|load|pull up) (.+)", t)
    if m:
        return mac.open_in_browser(m.group(2), m.group(1))
    m = re.fullmatch(r"(?:open|go to|load|pull up|bring up) (.+?) (?:in|on|using|with) " + BROWSER_RE, t)
    if m:
        return mac.open_in_browser(m.group(1), m.group(2))
    m = re.fullmatch(r"(?:open )?(?:a )?new tab(?: in " + BROWSER_RE + r")?", t)
    if m:
        return mac.new_tab(m.group(1) or "safari")
    m = re.fullmatch(r"(?:play|search for|find|show me) (.+?) on youtube", t) or \
        re.fullmatch(r"(?:search )?youtube (?:for )?(.+)", t)
    if m:
        return mac.youtube(m.group(1))
    m = re.fullmatch(r"(?:google|search google for|open google (?:for|and search(?: for)?))\s+(.+)", t)
    if m:
        return mac.google(m.group(1))
    m = re.fullmatch(r"(?:search (?:the web |the internet |online )?(?:for )?|look up|look online (?:for )?|"
                     r"find out|check online (?:for )?)\s*(.+)", t)
    if m:
        return brain.think_stream(f"Search the web and tell me: {original_case(m.group(1))}")
    if re.search(r"(what|which) shortcuts|list (my )?shortcuts", t):
        return mac.list_shortcuts()
    m = re.fullmatch(r"run (?:the |my )?shortcut (.+)", t) or re.fullmatch(r"run (?:the |my )?(.+?)(?: shortcut)?", t)
    if m:
        return mac.run_shortcut(m.group(1))
    m = re.fullmatch(r"(?:close|closed|quit|exit|shut down|kill)\s+(?:the )?(.+?)(?: app)?", t)
    if m:
        return mac.quit_app(m.group(1))
    m = re.fullmatch(r"(?:switch to|go to|bring up|take me to)\s+(.+)", t)
    if m:
        return mac.switch_to(m.group(1))
    return None


COMMAND_START = (r"(?:open|close|quit|play|pause|go to|search|google|look up|set|remind|add|turn|switch|run|take|"
                 r"start|launch|text|what|tell|skip|next|cancel|put|schedule|make)\b")


# ---------- group chat mode: friends in the room are guests ----------

WHO = {"owner": True, "name": None}      # who said the command being handled right now
PRIVATE = re.compile(
    r"\b(remember|forget|what do you (know|remember))\b"
    r"|\b(my|connor'?s|his|her|their|your owner'?s) (calendar|schedule|agenda|appointments?|meetings?|events?|"
    r"reminders?|to ?-?dos?|notes?|obsidian|vault|journal|day|week|files?|folders?|documents?|desktop|e-?mails?|"
    r"inbox|messages?|texts?|passwords?|accounts?|settings|memory|vitals|sleep|discord|stream|computer|address|phone)\b"
    r"|\b(remind me|reminders?|to ?-?do|take a note|make a note|note (that|down)|write down|jot down|brief me|"
    r"briefing|catch me up|what do i have|what('?s| is) on (today|tomorrow)|add .* to (my |the )?calendar)\b"
    r"|^(schedule|close|quit|exit|kill|shut down|switch to|open|launch|run|start|delete|send)\b"
    r"|\b(upgrade|update|edit|modify|reprogram|rewrite|change|fix|tweak|teach|improve) (yourself|your)\b"
    r"|\b(undo|revert|roll back|from now on|learn (how )?to)\b"
    r"|\b(discord|stream mode|going live|twitch|hype ?man|hype mode|butler|personality|vitals?|oura|readiness|hrv|"
    r"how did i sleep|shortcuts?|e-?mails?|inbox|group chat mode|party mode|guest mode|voice ?prints?|check for updates|self[- ]?(check|test)|diagnostics?|"
    r"upgrade ideas?|improvement ideas?|struggling with|(?:your|the|ai) (?:ai )?(?:team|agents|crew|workforce)|"
    r"report|my routines?|my habits?|noticed about me|what do (you think )?i need|review yourself|"
    r"how have you been doing|your rules|your hearing|your ears|speech model|skills|add-?ons|plug-?ins|for everything)\b")
GUEST_OK = re.compile(r"\b(timer|alarm|time left|how long left|weather|youtube|google|blinker|cough|breath)\b")


def guest_reply(t, text):
    """What Jarvis does for a friend. Their music, timers, weather and questions are fine;
    Connor's notes, memory, calendar, files, apps and settings are not."""
    owner = speaker.owner_name()
    name = WHO.get("name")
    if re.search(r"\bwho (am i|is (this|talking|speaking))\b|do you know (me|who i am)|whose voice", t):
        return f"You're {name}, right? Good to have you here." if name else \
            f"I don't know your voice yet. {owner} can teach me it with the Teach Jarvis My Voice file."
    timerish = GUEST_OK.search(t) and not re.search(r"\b(calendar|reminders?|notes?)\b", t)
    if PRIVATE.search(t) and not timerish:
        print(f"(group chat mode: blocked a private request from {name or 'a guest'})")
        return (f"Sorry{', ' + name if name else ''}, that's {owner}'s private stuff, so only {owner} can ask for it. "
                f"{owner}, if that was you, say it again a little closer to me.")
    for skill in (music, organize, computer):
        if skill is organize and not re.search(r"\b(timer|timers|alarm|time left|how long left|how much time)\b", t):
            continue
        if skill is computer and not re.search(r"\b(youtube|google)\b", t):
            continue
        try:
            reply = skill(t)
        except Exception as e:
            print(f"(guest skill error in {skill.__name__}: {e})")
            reply = None
        if reply:
            return personality.flair(reply) if isinstance(reply, str) else reply
    if re.search(r"\b(take|hit|rip) (a|the) blinker\b", t):
        import sfx
        voice.say(personality.line("blinker_start"))
        sfx.blinker()
        return personality.line("blinker_end")
    if "weather" in t and len(t) < 40 and not re.search(r"\b(in|at|for) (?!here\b|today\b|tomorrow\b)[a-z]", t):
        return weather.report()
    display.update(status="thinking")
    return brain.guest_think_stream(text, name, owner)


def group_mode_command(t):
    """Owner-only: 'group chat mode on/off', 'who am I'. Returns what to say, or None."""
    if re.search(r"\b(group (chat )?mode|party mode|guest mode|voice lock)\b", t):
        if not speaker.enrolled():
            return ("I don't know your voice yet. Double-click Teach Jarvis My Voice in my folder, "
                    "read a few sentences, and then I'll know you from your friends.")
        on = not re.search(r"\b(off|stop|disable|end|deactivate|turn off)\b", t)
        cfg = json.loads(Path(__file__).with_name("config.json").read_text())
        cfg["group_mode"] = on
        Path(__file__).with_name("config.json").write_text(json.dumps(cfg, indent=2))
        friends = speaker.friends()
        return (f"Group chat mode on. I'll keep your private stuff for you only"
                + (f", and I know {', '.join(friends)} by voice." if friends else ".")) if on else \
            "Group chat mode off. I'll do anything for anyone again."
    if re.search(r"\bwho (am i|is (this|talking|speaking))\b|do you know (me|who i am)|whose voice", t):
        if speaker.group_mode():
            return f"That's you, {speaker.owner_name()}. I'd know that voice anywhere."
        return "Group chat mode is off, so I'm not checking voices right now."
    return None


SHUTDOWN_RE = re.compile(r"(?:(?:shut|power|turn) (?:yourself |it |him )?(?:down|off)|shutdown|quit|exit|"
                         r"close (?:yourself|down)|stop running|log off|go offline|kill (?:yourself|the app))"
                         r"(?: now| for (?:now|tonight|today))?(?: please)?")


def shut_down():
    """Typed or said "shut down" / "quit": Jarvis says bye and closes completely (exit code 0 tells
    the keeper not to restart him). Start him again from the Desktop / Start Jarvis file."""
    print("(shutting down: asked to)")
    try:
        spotify.unduck()
    except Exception:
        pass
    try:
        voice.say("Shutting down. Holler when you need me.", interruptible=False)
    except Exception:
        pass
    sys.stdout.flush()
    os._exit(0)


def handle(text, _part=False):
    """Returns False when Jarvis should go back to sleep."""
    if not _part:
        LAST_TEXT["text"] = text
    t = text.lower().strip()
    t = re.sub(r"\b(?:hey |ok |okay )?jarvis\b[,.]?", " ", t)
    t = re.sub(r"^\s*(?:please\s+|can you\s+|could you\s+|would you\s+)*", "", t)
    t = re.sub(r"\b(play|open|add|remind me)\s+(?:\1\s+)+", r"\1 ", re.sub(r"\s+", " ", t)).strip(" .?!,")
    # "play my gym playlist, remind me in 20 and tell the squad I'm off at 9": his brain does them all
    if not _part and not PENDING and WHO.get("owner", True) and brain.tools_on() and chained(t):
        display.update(status="thinking")
        voice.say(brain.think_stream(text, unsure=HEARD_UNSURE.is_set(), tools=True))
        return True
    # "open Safari and open YouTube" -> two commands, done in order
    if not _part and not PENDING and not re.search(r"new tab in|\b(team|agents|crew|workforce|project)\b", t):
        parts = re.split(r",?\s+(?:and then|and|then)\s+(?=" + COMMAND_START + ")", t)
        if len(parts) > 1 and all(re.match(COMMAND_START, p) for p in parts):
            awake = True
            for p in parts:
                awake = handle(p, _part=True) and awake
            return awake
    if SHUTDOWN_RE.fullmatch(t) and WHO.get("owner", True):     # only Connor can switch him off
        shut_down()
    if re.fullmatch(r"(stop talking|shut up|be quiet|quiet|hush|enough|that's enough|ok stop|okay stop|stop stop)", t):
        return True                     # he's already stopped: just listen for what's next
    if not t or SLEEP_RE.fullmatch(t):
        if PENDING.get("kind") == "store":             # "no thanks" to a store request: decline it
            import storeforge
            storeforge.answer(PENDING["action"], False)
        elif PENDING.get("kind") == "predict":          # "no thanks" to a suggestion still counts as a no
            predict.answered(PENDING["key"], False)
        elif PENDING.get("kind") == "rule":
            learn.answered(PENDING["idea"], False)
        PENDING.clear()
        voice.say(personality.line("sleep"))
        return False
    if not WHO.get("owner", True):              # group chat mode: a friend is talking
        voice.say(guest_reply(t, text))
        return True
    said = group_mode_command(t)
    if said:
        voice.say(said)
        return True
    try:
        import discord_bot
        said = discord_bot.voice_command(t)
    except Exception as e:
        print(f"(Discord command failed: {e})")
        said = None
    if said:
        voice.say(said)
        return True
    if re.search(r"\b((?:stop|end|exit|leave|turn off|quit|kill)(?: the| my)? stream(?: mode)?|stream mode off|"
                 r"(?:i'?m |i am )?done streaming|(?:i'?m |i am )?(?:ending|off) (?:the |my )?stream|stop reading (?:the )?chat)\b", t):
        voice.say("Stream mode off. Good stream!" if twitch.stop() else "Stream mode wasn't on.")
        return True
    if re.search(r"\b(stream mode|(?:i'?m |i am |we'?re |going )live|start(?:ing)? (?:the |my )?stream|read (?:my |the )?(?:twitch )?chat)\b", t):
        reply = twitch.start()
        voice.say(reply or "I don't know your Twitch channel yet. Double-click Connect Twitch in my folder "
                           "and type your channel name, then say stream mode again.")
        return True
    if re.search(r"\b(be (?:the|my|a) hype ?man|hype (?:man )?mode)\b", t):
        personality.set_personality("hype")
        voice.say("Ayy, hype mode activated! Let's gooo!")
        return True
    if re.search(r"\b(butler mode|be (?:a |the )?butler|calm down|be (?:more )?(?:calm|normal|proper))\b", t):
        personality.set_personality("butler")
        voice.say("Very well. Back to my usual self.")
        return True
    m = re.search(r"\b(?:different|new|another|next|change (?:the|your)?)\s*(blinker|cough|breath|inhale)\b", t)
    if m:
        import sfx
        part = {"blinker": "both", "inhale": "breath"}.get(m.group(1), m.group(1))
        b, c = sfx.change(part)
        voice.say(f"Trying breath {b} of {len(sfx.BREATHS)} and cough {c} of {len(sfx.COUGHS)}.")
        sfx.blinker()
        voice.say("Say different cough or different breath to keep changing, or keep this one.")
        return True
    if re.search(r"\b(vitals?|oura|readiness|hrv|heart rate variability|(?:resting )?heart rate|sleep score|"
                 r"how did i sleep|how('?d| did| was) my sleep|how recovered|recovery score|body battery)\b", t):
        import oura
        display.update(status="thinking")
        voice.say(oura.analysis(original_case(text)))
        return True
    if re.search(r"\b(take|hit|rip) (a|the) blinker\b", t):
        import sfx
        voice.say(personality.line("blinker_start"))
        sfx.blinker()
        voice.say(personality.line("blinker_end"))
        return True
    if PENDING.get("kind") == "store":
        if YES_RE.search(t) and len(t.split()) <= 6 or NO_RE.fullmatch(t):
            import storeforge
            action = PENDING.pop("action")
            PENDING.clear()
            voice.say("One moment.", interruptible=False) if YES_RE.search(t) else None
            voice.say(storeforge.answer(action, bool(YES_RE.search(t) and not NO_RE.fullmatch(t))))
            offer_store_action()                 # the next one, if there's another
            return True
        PENDING.clear()                          # he moved on; it stays queued for later
    if PENDING.get("kind") == "night":
        said = answer_lesson(t)
        if said is not None:
            return said
    if PENDING.get("kind") in ("predict", "rule"):
        said = answer_suggestion(t)
        if said is not None:
            return said
    if PENDING:
        if re.fullmatch(r"(no|cancel|forget it|never mind)", t) and PENDING.get("kind") != "selfedit":
            PENDING.clear()
            voice.say(personality.line("no_problem"))
            return True
        reply = continue_pending(t)
        if reply:
            voice.say(reply)
            return True
    if re.fullmatch(r"undo (?:that|it|the|your|my)?\s*(?:last )?(?:change|edit|update|upgrade)?|"
                    r"(?:undo|revert|roll back|reverse) (?:that|your last (?:change|edit|update)|the last (?:change|edit|update))", t):
        reply, restart = selfedit.undo()
        voice.say(reply + (restart_words() if restart else ""), interruptible=False)
        if restart:
            restart_jarvis()
        return True
    if re.search(r"what (did|have) you (just )?change|what was your last (change|update|edit)", t):
        voice.say(selfedit.last_change())
        return True
    if LAST_SELF_EDIT and time.time() - LAST_SELF_EDIT.get("t", 0) < 300 and \
            re.search(r"\b(go ahead|do it|go through with it|go for it|apply it|make the change|proceed)\b", t):
        request = LAST_SELF_EDIT.pop("request")
        LAST_SELF_EDIT.clear()
        voice.say(do_self_edit(request))
        return True
    if ideas_command(t) or team_command(t) or predict_command(t):
        return True
    if hearing_command(t):
        return True
    request = self_edit_request(t)
    if request:
        PENDING.update(kind="selfedit", request=request)
        voice.say(f"Just to check, you'd like me to change my own code: {request}. Shall I go ahead?")
        return True
    if re.fullmatch(r"(?:check|look) for (?:any )?(?:new )?updates|(?:are there |is there |do you have )?any (?:new )?updates|"
                    r"(?:install|get) (?:the |any )?(?:new )?updates?|update yourself( now)?|update", t):
        display.update(status="thinking")
        voice.say("Checking for updates.", interruptible=False)
        reply, restart = updater.check(manual=True)
        if restart:
            voice.say(reply + restart_words(), interruptible=False)
            updater.pop_note()
            restart_jarvis()
            return True
        voice.say(reply)
        return True
    if re.search(r"\b(run|do|start)( a| an| your)? (self[- ]?(check|test)|systems? check|diagnostics?)|"
                 r"^(self[- ]?(check|test)|diagnostics?|systems? check)$|are you (working|running) (properly|ok(ay)?|right)", t):
        display.update(status="thinking")
        voice.say("Running a quick self-check. This takes about half a minute.", interruptible=False)
        voice.say(updater.self_check())
        return True
    if re.search(r"what('s| is| did you get) new|what (did|have) you (just )?update|what was (in )?(the|your) (last )?update", t):
        voice.say(updater.last_note())
        return True
    if re.search(r"what can you do|what are you able to do|help me out here|list your (skills|abilities)", t):
        voice.say(CAPABILITIES)
        return True
    if "what can you open" in t or "which apps" in t:
        apps = hands.allowed_apps()
        voice.say("I can open " + (", ".join(apps) if apps else "nothing at the moment") + ", and most websites.")
        return True
    if skills_command(t):
        return True
    said = quick_answer(t)                       # no need to wake Claude for these
    if said:
        voice.say(said)
        return True
    if tool_mode() == "always":                  # "use your brain for everything"
        display.update(status="thinking")
        voice.say(brain.think_stream(text, unsure=HEARD_UNSURE.is_set(), tools=True))
        return True
    for skill in (organize, computer, music):
        failed = False
        try:
            reply = skill(t)
        except Exception as e:
            print(f"(skill error in {skill.__name__}: {e})")
            learn.note(original_case(t), personality.line("error"), "error", f"{skill.__name__}: {type(e).__name__}: {e}")
            reply, failed = personality.line("error"), True
        if reply:
            voice.say(personality.flair(reply) if isinstance(reply, str) else reply)
            if not failed:
                predict.record(original_case(t), skill.__name__)      # learning his routines
            # Once music is playing, step back so it doesn't drown out the next command.
            if skill is music and reply.startswith(("Playing", "Resuming")):
                return False
            return True
    m = re.fullmatch(r"(?:open|launch|start|start up|pull up|turn on|fire up|bring up)\s+(?:up\s+)?(.+)", t)
    if m:
        voice.say(mac.open_thing(m.group(1)))
        predict.record(original_case(t), "open")
        return True
    if "weather" in t and len(t) < 40 and not re.search(r"\b(in|at|for) (?!here\b|today\b|tomorrow\b)[a-z]", t):
        voice.say(weather.report())
        predict.record(original_case(t), "weather")
        return True
    display.update(status="thinking")
    # starts talking as soon as the first sentence is ready; with his skills, any wording works
    voice.say(brain.think_stream(text, unsure=HEARD_UNSURE.is_set(), tools=brain.tools_on()))
    return True


TIME_ASK = re.compile(r"(?:so |and |um |uh )?(?:what(?:'s| is) the time|what time is it|what time(?: is it)? (?:right )?now|"
                      r"(?:do you )?(?:know|have|got) the time|time check|the time)(?: (?:right )?now| please| bro)?")
DATE_ASK = re.compile(r"(?:so |and |um |uh )?(?:what(?:'s| is) (?:the date|today's date|the date today)|what day is it|"
                      r"what day is (?:it )?today|what(?:'s| is) the day today|what's today's date|what is today's date|"
                      r"what date is it(?: today)?)(?: today)?(?: please| bro)?")
THANKS = re.compile(r"(?:ok |okay |cool |nice |great |perfect |awesome )?(?:thanks|thank you|thank you so much|thanks a lot|"
                    r"thanks so much|cheers|appreciate it|i appreciate it|ty|much appreciated)(?: bro| man| dude| buddy)?")


def quick_answer(t):
    """Simple things he can say himself, word for word, without asking Claude (saves his plan's tokens)."""
    now = datetime.datetime.now()
    if TIME_ASK.fullmatch(t):
        return f"It's {now.strftime('%I:%M %p').lstrip('0')}."
    if DATE_ASK.fullmatch(t):
        day = now.day
        suffix = "th" if 11 <= day % 100 <= 13 else {1: "st", 2: "nd", 3: "rd"}.get(day % 10, "th")
        return f"It's {now.strftime('%A, %B')} {day}{suffix}."
    if THANKS.fullmatch(t):
        return personality.line("welcome")
    return None


ACTION_WORDS = re.compile(r"(?:open|close|quit|play|pause|resume|skip|set|start|remind|add|put|schedule|book|make|"
                          r"turn|switch|run|launch|text|tell|message|send|post|cancel|stop|queue|take|write|note|"
                          r"search|google|look up|join|leave|call|wake)\b")


def chained(t):
    """True if one sentence asks for several things ("play X, remind me in 20 and tell the squad ...")."""
    parts = [p for p in re.split(r"\s*,\s*(?:and\s+|then\s+|and then\s+)?|\s+(?:and then|then|and also|also|and)\s+", t) if p]
    return sum(1 for p in parts if ACTION_WORDS.match(re.sub(r"^(?:please|can you|could you|also)\s+", "", p))) >= 2


def tool_mode():
    """"fallback" (default): quick built-in commands first, his brain and skills for everything else
    and for several things at once. "always": his brain decides everything."""
    return str(CONFIG.get("tool_brain_mode", "fallback"))


SKILLS_RE = re.compile(r"\bwhat (?:skills|add-?ons|plug-?ins|tools) (?:do you have|have you got)|\blist (?:your )?(?:skills|add-?ons|plug-?ins)\b")


def skills_command(t):
    """ "what skills do you have", "use your brain for everything", "use quick commands". True if handled."""
    if SKILLS_RE.search(t):
        import skills
        voice.say(skills.describe())
        return True
    m = re.search(r"\buse (?:your )?(brain|quick commands?|shortcuts|regular commands?|normal commands?) for everything\b|"
                  r"\b(?:go back to|use) (?:your )?(quick|regular|normal) commands\b|\bthink before (?:you act|acting)\b", t)
    if not m:
        return False
    always = "brain" in t or "think before" in t
    cfg = json.loads(Path(__file__).with_name("config.json").read_text())
    cfg["tool_brain_mode"] = "always" if always else "fallback"
    Path(__file__).with_name("config.json").write_text(json.dumps(cfg, indent=2))
    CONFIG["tool_brain_mode"] = cfg["tool_brain_mode"]
    voice.say("Got it. My brain decides everything now. It's a second or two slower, but any wording works."
              if always else "Got it. Quick commands go straight through again, and my brain handles the rest.")
    return True


HEARD_UNSURE = threading.Event()     # set while handling something he wasn't sure he heard right
HEARING_RE = re.compile(r"\b(?:your|the)(?: (?:old|original|usual|new|better))? (?:hearing|ears|speech (?:model|recognition))\b")


def hearing_command(t):
    """ "how's your hearing", "upgrade your hearing", "use your old hearing". True if handled."""
    if not HEARING_RE.search(t):
        return False
    if re.search(r"\b(old|original|usual|previous|small|faster|quicker)\b.*\bhearing\b|"
                 r"\b(downgrade|undo|revert|go back)\b", t):
        voice.say(hearing.downgrade())
    elif re.search(r"\b(upgrade|improve|better|boost|update|try)\b", t):
        voice.say("On it. I'll download a bigger speech model and test it on your voice. "
                  "It can take a few minutes, so I'll tell you how it went.", interruptible=False)

        def go():
            said = hearing.upgrade(force=True)
            hearing._notes.append(said)
        threading.Thread(target=go, daemon=True).start()
    else:
        voice.say(hearing.describe())
    return True


IDEAS_RE = re.compile(r"\b(?:any|got any|have you got any|do you have any) (?:upgrade|improvement|self[- ]improvement) ideas?\b|"
                      r"\bupgrade ideas?\b|how (?:could|can|would|should) you (?:improve|upgrade|get better|be better)\b")
STRUGGLES_RE = re.compile(r"what (?:have you been|are you|were you) struggling with|"
                          r"what (?:have you|did you) (?:struggle with|mess up|get wrong)|what couldn'?t you do")


NIGHT_WHAT = re.compile(r"what (?:did|have) you (?:learn|learned|teach yourself|taught yourself)(?: last night| overnight| "
                        r"while i (?:was )?(?:slept|sleeping|asleep))?|what did you learn|night school report")
NIGHT_TOGGLE = re.compile(r"(?:turn )?night school (on|off)|(?:turn )?(on|off) night school|(stop|start) (?:learning|"
                          r"teaching yourself) (?:at night|overnight)")

REPAIR_WHAT = re.compile(r"what (?:did you|have you) (?:fix|fixed|repair|repaired)(?: on the team| for the team)?|"
                         r"(?:last|latest) (?:repair|fix)|what broke(?: on the team)?")
REPAIR_TOGGLE = re.compile(r"(?:turn )?self[- ]?repair (on|off)|(?:turn )?(on|off) self[- ]?repair|"
                           r"(stop|start) (?:fixing|repairing) (?:the team|team errors|errors)")


def ideas_command(t):
    """Owner asks about self-improvement. Returns True if handled."""
    if REPAIR_WHAT.fullmatch(t):
        voice.say(repair.last())
        return True
    m = REPAIR_TOGGLE.fullmatch(t)
    if m:
        on = (m.group(1) or m.group(2) or m.group(3)) in ("on", "start")
        repair.set_enabled(on)
        voice.say("Self-repair's on. When my team hits an error I'll fix it and tell you." if on else
                  "Self-repair's off. I'll just tell you when my team hits an error.")
        return True
    if NIGHT_WHAT.fullmatch(t):
        lesson = nightschool.pending()
        if lesson and not PENDING:
            offer_lesson(lesson)
        else:
            voice.say(nightschool.last_night())
        return True
    m = NIGHT_TOGGLE.fullmatch(t)
    if m:
        on = (m.group(1) or m.group(2) or m.group(3)) in ("on", "start")
        nightschool.set_enabled(on)
        voice.say("Night school's on. I'll teach myself fixes overnight and ask you in the morning." if on else
                  "Night school's off. I won't change myself overnight.")
        return True
    if STRUGGLES_RE.search(t):
        voice.say(learn.struggles())
        return True
    if RULES_RE.search(t):
        voice.say(learn.forget_rules() if re.search(r"\b(forget|clear|drop|delete|reset)\b", t) else learn.rules_summary())
        return True
    if REVIEW_RE.search(t):
        voice.say(learn.review())
        idea = learn.next_offer(any_time=True)
        if not idea:
            voice.say("Let me think about what I could do better.", interruptible=False)
            display.update(status="thinking")
            idea = learn.think_about_it(review=True)
        if idea:
            offer_idea(idea)
        else:
            voice.say("I can't see anything worth changing right now.")
        return True
    if not IDEAS_RE.search(t):
        return False
    idea = learn.next_offer(any_time=True)
    if not idea:
        voice.say("Give me a minute to look over what's tripped me up lately.", interruptible=False)
        display.update(status="thinking")
        idea = learn.think_about_it()
    if not idea:
        voice.say("Nothing I'd change right now. If I can't do something you ask, I'll note it and come up with a fix.")
        return True
    offer_idea(idea)
    return True


def offer_idea(idea):
    learn.offered(idea["id"])
    if idea.get("type") == "rule":
        PENDING.clear()
        PENDING.update(kind="rule", rule=idea["rule"], idea=idea["id"])
        voice.say(f"{idea['spoken'].rstrip('.')}. Want me to make that a rule for myself?")
        return
    PENDING.update(kind="selfedit", request=idea["request"], idea=idea["id"])
    voice.say(f"{idea['spoken'].rstrip('.')}. Want me to upgrade myself to do that?")


def offer_lesson(lesson):
    """What he taught himself overnight (nightschool.py): keep it?"""
    PENDING.clear()
    PENDING.update(kind="night", lesson=lesson["id"])
    voice.say(nightschool.offer_line(lesson))


def answer_lesson(t):
    """Connor's answer to "Overnight I taught myself to ... Want to keep it?". None if he moved on."""
    yes, no = YES_RE.search(t) and len(t.split()) <= 6, NO_RE.fullmatch(t) or re.fullmatch(
        r"(?:throw it (?:out|away)|get rid of it|bin it|don'?t keep it|no keep|scrap it)", t)
    if not yes and not no:
        PENDING.clear()                          # he moved on: ask again later
        return None
    lid = PENDING.pop("lesson", None)
    PENDING.clear()
    if no:
        nightschool.discard(lid)
        voice.say("Okay, I've thrown that one out.")
        return True
    voice.say("Adding it to myself.", interruptible=False)
    reply, restart = nightschool.keep(lid)
    if restart:
        voice.say(reply + restart_words(), interruptible=False)
        restart_jarvis()
    else:
        voice.say(reply)
    return True


YES_RE = re.compile(r"\b(yes|yeah|yep|yup|sure|go ahead|do it|go for it|please|ok|okay|absolutely|bet|"
                    r"let's do it|why not|sounds good)\b")
NO_RE = re.compile(r"(no|nope|nah|not now|not today|no thanks|no thank you|don't|skip it|maybe later|later|"
                   r"never mind|cancel)( (thanks|thank you|jarvis))?")


def answer_suggestion(t):
    """Connor's answer to a routine he was offered (predict) or a rule for himself (learn).
    Returns handle()'s result, or None if he's moved on to something else."""
    kind = PENDING.get("kind")
    yes, no = YES_RE.search(t) and len(t.split()) <= 6, NO_RE.fullmatch(t)
    if not yes and not no:
        PENDING.clear()                          # he moved on: just do what he asked
        return None
    item = dict(PENDING)
    PENDING.clear()
    if kind == "predict":
        predict.answered(item["key"], bool(yes))
        if no:
            voice.say(personality.line("no_problem"))
            return True
        return handle(item["command"], _part=True)
    learn.answered(item["idea"], bool(yes))
    if yes:
        learn.add_rule(item["rule"])
        voice.say("Done. That's a rule for me from now on. Say what rules have you learned to hear them.")
    else:
        voice.say("Fair enough, I'll keep doing it my way.")
    return True


def maybe_suggest():
    """If now's when Connor usually does something, offer it. True if he offered."""
    if PENDING or not WHO.get("owner", True):
        return False
    r = predict.next_suggestion()
    if not r:
        return False
    predict.offered(r["key"])
    PENDING.update(kind="predict", key=r["key"], command=r["said"])
    voice.say(predict.suggestion_line(r))
    return True


TEAM_START = [
    re.compile(r"(?:get|put|have|send|set|ask|use) (?:the |your |my )?(?:ai )?(?:team|agents|crew|workforce|squad|specialists)"
               r"(?: to work)?(?: on| to| onto| for)? (.+)"),
    re.compile(r"(?:assemble|start|spin up|launch|call in|bring in) (?:the |a |your |my )?(?:ai )?(?:team|agents|crew|workforce)"
               r"(?: to| for| on)? (.+)"),
    re.compile(r"(?:start|kick off|begin) (?:a |an )?(?:new )?(?:team )?project (?:to |on |about |for )?(.+)"),
]
# "Build me a Shopify store ..." goes to the team (the store builder uses StoreForge)
STORE_START = re.compile(r"(?:.*\b)?(?:build|make|create|set up|put together|generate) (?:me |us )?(?:a |an |another )?"
                         r"(?:new |example |test |quick )?(?:shopify |online |e-?commerce )?store\b.*")
TEAM_STATUS = re.compile(r"\b(?:how'?s|how is|what'?s|what is|is) (?:the |my |your )?(?:ai )?(?:team|agents|crew|workforce)"
                         r"(?: doing| working on| up to| done| finished| busy)?\b|\bteam status\b|status of (?:the|my) team")
TEAM_REPORT = re.compile(r"\bread (?:me )?(?:the |my |your )?(?:last |latest |team'?s? |new )*report\b|"
                         r"what did (?:the|my|your) (?:ai )?(?:team|agents|crew) (?:find|come up with|do|say|make)")
TEAM_CANCEL = re.compile(r"\b(?:cancel|stop|call off|abort|kill) (?:the |my |your )?(?:ai )?(?:team|agents|crew|team project|project)\b")
TEAM_VIEW = re.compile(r"\b(?:show|open|pull up|bring up|display|let me see|i want to see) (?:me )?(?:the |your |my )?(?:ai )?"
                       r"(?:team|agents?|agent network|neural network|network|workforce|crew|command cent(?:er|re))"
                       r"(?: view| network| map| screen| at work| working)?\b|\bwatch (?:the |my |your )?(?:team|agents)\b")
TEAM_RESUME = re.compile(r"\b(?:restart|resume|continue|redo|retry|try again|pick up|finish|start again)\b.*"
                         r"\b(?:team|agents|crew|team'?s? project|mission|store build)\b|"
                         r"\b(?:team|agents|crew)\b.*\b(?:try again|start again|pick (?:it )?(?:back )?up|carry on)\b")
STORE_OPEN = re.compile(r"\b(?:open|show|pull up|bring up|launch|start|let me see|i want to see|can i see)\b(?: me)?(?: up)?"
                        r" (?:the |my |your )?(?:store ?forge|store forage|(?:new |latest |last )?(?:shopify )?store"
                        r"(?: project| preview| they made| the team made| you made)?|(?:store )?preview)\s*(?:app)?[.!?]?$")
TEAM_ROSTER = re.compile(r"who(?:'?s| is| are)? (?:on |in )?(?:your|the) (?:ai )?(?:team|agents|crew)|who are your agents|"
                         r"what agents do you have")


def team_command(t):
    """The AI team (workforce.py). Returns True if handled."""
    if TEAM_CANCEL.search(t):
        voice.say(workforce.cancel())
        return True
    if TEAM_RESUME.search(t) and not TEAM_STATUS.search(t) and not STORE_START.fullmatch(t) and not any(p.fullmatch(t) for p in TEAM_START):
        voice.say(workforce.resume())
        return True
    if TEAM_VIEW.search(t):
        display.open_page("/team")
        voice.say("Here's my agent network. Every node and line on it is the team's real work, live.")
        return True
    if STORE_OPEN.search(t):
        import storeforge
        if not storeforge.available():
            voice.say("I can't find StoreForge on this computer.")
            return True
        if storeforge.needs_build():
            voice.say("One sec, getting StoreForge ready. After an update that takes about a minute.",
                      interruptible=False)
        else:
            voice.say("One sec, opening StoreForge.", interruptible=False)
        voice.say(storeforge.open_app())
        return True
    if re.search(r"\b(?:store|storeforge|shopify) (?:requests?|approvals?)\b|what needs my (?:ok|okay|approval)", t):
        if not offer_store_action():
            voice.say("Nothing's waiting for your OK right now.")
        return True
    if TEAM_ROSTER.search(t):
        voice.say(workforce.roster())
        return True
    if TEAM_REPORT.search(t):
        voice.say(workforce.report())
        offer_team_change()
        return True
    if TEAM_STATUS.search(t) and not re.search(r"\b(?:on|to) (?!it\b)\w+ \w+", t.split("team", 1)[-1]):
        voice.say(workforce.status())
        return True
    if STORE_START.fullmatch(t) and ("shopify" in t or "storeforge" in t or "store forge" in t or
                                      re.search(r"\bstore (?:for|from|with|selling)\b", t)):
        voice.say(workforce.start(original_case(t)))
        return True
    for pattern in TEAM_START:
        m = pattern.fullmatch(t)
        if m and len(m.group(1).split()) >= 2:
            goal = original_case(m.group(1))
            voice.say(workforce.start(goal))
            return True
    return False


def offer_team_change():
    """If the team's last project worked out a change to Jarvis himself, ask before doing it.
    Also asks about anything the store builder queued for Connor's OK (StoreForge)."""
    if offer_store_action():
        return
    p = workforce.latest()
    if p and p.get("status") == "done" and p.get("jarvis_change") and not p.get("change_offered") and not PENDING:
        workforce._update(p["id"], change_offered=True)
        PENDING.update(kind="selfedit", request=p["jarvis_change"])
        voice.say(f"They also worked out a change to me: {p['jarvis_change']}. Shall I go ahead and make it?")


def offer_store_action():
    """One StoreForge request waiting for Connor (confirm rights, push to Shopify). True if asked."""
    if PENDING:
        return False
    try:
        import storeforge
        asks = storeforge.waiting()
    except Exception:
        return False
    if not asks:
        return False
    a = asks[0]
    PENDING.update(kind="store", action=a["id"])
    why = f" {a['why'].rstrip('.')}." if a.get("why") else ""
    voice.say(f"My store builder needs your OK to {a['text']}.{why} Should I go ahead?")
    return True


def _team_store(n):
    stores = n[2].get("stores") or []
    return stores[-1]["pid"] if stores and n[2].get("status") == "done" and CONFIG.get("open_stores", True) else None


def team_news_words(n):
    """What to say about finished team work (mentions the store they built, if any)."""
    return n[1] + (" I'm opening the store they built in StoreForge for you." if _team_store(n) else "")


def open_team_store(n):
    """If the team built a store, opens it in StoreForge so he can see it."""
    pid = _team_store(n)
    if pid:
        try:
            import storeforge
            threading.Thread(target=storeforge.open_app, args=(pid,), daemon=True).start()
        except Exception as e:
            print(f"(couldn't open StoreForge: {e})")


def tell_team_news():
    """In a conversation: say if the team has finished something."""
    n = workforce.news()
    if n:
        workforce.told(n[0])
        open_team_store(n)
        voice.say(team_news_words(n))
        offer_team_change()


ROUTINES_RE = re.compile(r"what (?:have you|did you) (?:noticed|learn(?:ed|t)?|picked up) about me|"
                         r"\b(?:my|what are my|tell me my) (?:routines?|habits?|patterns?)\b|what do i usually do")
NEEDS_RE = re.compile(r"what (?:do you think )?(?:do )?i(?:'ll| will)? (?:need|want)(?: today| next| tomorrow| now)?\b|"
                      r"any (?:suggestions|predictions|recommendations)\b|what should i (?:do|focus on|work on) "
                      r"(?:next|now|today|tomorrow)|predict (?:my|what)|what'?s coming up")
REVIEW_RE = re.compile(r"\breview yourself\b|\bself[- ]review\b|how have you been doing|how are you doing at your job|"
                       r"(?:your|give me your) report card|how did you do this week|evaluate yourself|rate yourself")
RULES_RE = re.compile(r"what rules (?:have you|did you) (?:learned|learnt|learn|made)|what are your rules|"
                      r"(?:forget|clear|drop|delete|reset) (?:all )?(?:of )?your (?:learned |own )?rules|list your rules")


def predict_command(t):
    """Predictive commands. Returns True if handled."""
    if ROUTINES_RE.search(t):
        voice.say(predict.routines_summary())
        return True
    if NEEDS_RE.search(t):
        voice.say("Let me look at what's coming up.", interruptible=False)
        display.update(status="thinking")
        when = "tomorrow" if "tomorrow" in t else "for the rest of today"
        said = predict.look_ahead(when)
        voice.say(said or "Nothing jumps out. Your day looks under control.")
        return True
    return False


def handle_and_learn(text, unsure=False):
    """handle(), and if he couldn't help Connor, remember it so he can work out an upgrade.
    Sometimes afterwards he suggests an upgrade he's worked out (learn.py).
    unsure: the speech model wasn't confident it heard this right, so check before acting."""
    owner = WHO.get("owner", True)
    if clarify.waiting(owner):                 # the answer to "Open Discord or Spotify?"
        command, said = clarify.resolve(text)
        if said:
            voice.say(said)
            return True
        if command:
            text, unsure = command, False
    question = clarify.check_heard(text, unsure, owner)
    if question:                               # "Did you say close Discord?"
        voice.say(question)
        return True
    if unsure:
        HEARD_UNSURE.set()
    try:
        return _handle_and_learn(text)
    finally:
        HEARD_UNSURE.clear()


def _handle_and_learn(text):
    before = len(voice.SAID)
    last = dict(LAST_EXCHANGE)
    if WHO.get("owner", True) and last and time.time() - last.get("t", 0) < 120 and learn.is_correction(text):
        learn.note(last["asked"], last["said"], "corrected", f"Connor then said: {text}")
    awake = handle(text)
    if not WHO.get("owner", True):
        return awake
    said = " ".join(voice.SAID[before:])
    try:
        import recall
        recall.save(text, said, "typed" if TYPING.is_set() else "voice")   # perfect memory
    except Exception as e:
        print(f"(long-term memory save failed: {e})")
    LAST_EXCHANGE.update(asked=original_case(text), said=said[:400], t=time.time())
    if learn.couldnt_help(said) and not PENDING:
        learn.note(original_case(text), said)
    if awake and not PENDING and not twitch.busy():
        lesson = nightschool.morning_offer()
        idea = None if lesson else learn.next_offer()
        if lesson:
            offer_lesson(lesson)
        elif idea:
            offer_idea(idea)
        else:
            maybe_suggest()
    return awake


LAST_EXCHANGE = {}      # what Connor last asked and what Jarvis said, to notice when he's corrected


def conversation(get_input, how="clap", spoken=False):
    twitch.set_talking(True)
    TALKING.set()
    voice.clear_barge()
    if spoken:
        voice.CONVERSING.set()          # he can be talked over while we're chatting
    try:
        _conversation(get_input, how, spoken)
    finally:
        voice.CONVERSING.clear()
        voice.clear_barge()
        TALKING.clear()
        twitch.set_talking(False)


def speak_up_loop():
    """Things he says without being asked, when nobody's talking to him: a heads-up before a calendar
    event, and when his team finishes a project (not late at night)."""
    time.sleep(60)
    while True:
        try:
            if not PAUSED.is_set() and not is_busy() and CONFIG.get("speak_up", True):
                hour = datetime.datetime.now().hour
                quiet = CONFIG.get("quiet_hours", [23, 8])
                late = (hour >= quiet[0] or hour < quiet[1]) if quiet[0] > quiet[1] else quiet[0] <= hour < quiet[1]
                h = predict.heads_up()
                if h and speak_up(h[1]):
                    predict.warned(h[0])
                fix_news = repair.pop_say()
                if fix_news and not speak_up(fix_news):
                    repair.say(fix_news, first=True)  # busy: say it in a moment
                n = None if late else workforce.news()
                if n and speak_up(team_news_words(n)):
                    workforce.told(n[0])
                    open_team_store(n)
                if repair.needs_restart() and not workforce.busy():
                    repair.restarted()
                    if speak_up("I'm restarting to load the fix I made for my team."):
                        restart_jarvis()
                    else:
                        repair._restart.set()
        except Exception as e:
            print(f"(speak-up failed: {e})")
        time.sleep(30)


def speak_up(text):
    """Say something he thought of himself. False if he's busy (he'll try again later)."""
    if not HANDLE_LOCK.acquire(blocking=False):
        return False
    try:
        if is_busy() or PAUSED.is_set():
            return False
        TYPING.set()                # keeps his own voice from waking him
        try:
            voice.say(text)
        finally:
            TYPING.clear()
            display.update(status="sleeping")
        return True
    finally:
        HANDLE_LOCK.release()


def restart_jarvis():
    """Start a fresh copy of Jarvis (to load new code). If the team is in the middle of a project, wait
    for it: restarting would kill their agents and cut the project off."""
    if not workforce.busy() and not nightschool.busy():
        selfedit.restart()
        return

    def wait():
        while workforce.busy() or nightschool.busy() or is_busy():
            time.sleep(5)
        selfedit.restart()
    print("(restart waits for the team to finish)")
    threading.Thread(target=wait, daemon=True, name="restart-later").start()


def restart_words():
    if workforce.busy():
        return " I'll restart to load it once the team finishes what they're on."
    return " Restarting now to load it."


def is_busy():
    return TALKING.is_set() or TYPING.is_set() or twitch.busy() or voice.speaking()


def typed_loop():
    """Answers what's typed into the display's "Type to Jarvis" box, just like speech."""
    while True:
        text = display.TYPED.get()
        TYPING.set()
        try:
            print(f"You (typed): {text}")
            display.log("you", text)
            with HANDLE_LOCK:
                WHO.update(owner=True, name=None)
                handle_and_learn(text)
        except Exception:
            import traceback
            traceback.print_exc()
        finally:
            TYPING.clear()
            if not TALKING.is_set():
                display.update(status="sleeping")


def _conversation(get_input, how="clap", spoken=False):
    if how == "wake":
        if sys.platform == "win32":
            import win
            win.system_sound("chime")
        else:
            subprocess.Popen(["afplay", "/System/Library/Sounds/Tink.aiff"])
        display.update(status="listening")
    elif memory.first_wake_today() and datetime.datetime.now().hour < 14:
        voice.say(briefing.full())
    else:
        voice.say(greeting())
    note = updater.pop_note()
    if note:
        voice.say(note)
    note = hearing.pop_note()
    if note:
        voice.say(note)
    with HANDLE_LOCK:
        WHO.update(owner=True, name=None)
        try:
            tell_team_news()
            if how != "wake":
                maybe_suggest()
        except Exception as e:
            print(f"(couldn't share news: {e})")
    misses, follow_up = 0, False
    while misses < 2:
        # After he answers, keep listening for a bit: no need to say "Hey Jarvis" again.
        secs = CONFIG.get("follow_up_seconds", 8) if follow_up else CONFIG.get("listen_seconds", 8)
        print("(listening for a follow-up)" if follow_up else "(listening for a command)")
        display.update(status="listening")
        text = get_input(secs)
        if not text:
            print("(heard nothing I understood)")
            misses += 1
            continue
        misses = 0
        who = dict(sys.modules["ears"].SPEAKER) if spoken and "ears" in sys.modules else {"owner": True, "name": None}
        print(f"You: {text}" if who.get("owner", True) else f"{who.get('name') or 'Guest'}: {text}")
        display.log("you", text)
        with HANDLE_LOCK:
            WHO.update(owner=who.get("owner", True), name=who.get("name"))
            heard = sys.modules["ears"].HEARD if spoken and "ears" in sys.modules else {}
            awake = handle_and_learn(text, unsure=bool(heard.get("unsure")))
        follow_up = True
        if not awake:
            display.update(status="sleeping")
            return
    if how != "wake":
        voice.say(personality.line("idle_sleep"), interruptible=False)
    display.update(status="sleeping")


def run_loop():
    import ears
    ears.preload()
    has_wake = ears.wake_model() is not None
    print("Jarvis is listening. " + ("Say \"Hey Jarvis\" or clap twice." if has_wake else "Clap twice to wake him."))
    while True:
        if PAUSED.is_set():
            display.update(status="paused")
            time.sleep(0.5)
            continue
        if TYPING.is_set():             # answering something typed: wait until he's done
            time.sleep(0.2)
            continue
        display.update(status="sleeping")
        print("(waiting to be woken)")
        if twitch.busy() or TYPING.is_set():   # reading chat or a typed reply: don't let his own voice wake him
            time.sleep(0.2)
            continue
        how = ears.wait_for_wake(CONFIG.get("clap_threshold", 0.35),
                                 paused=lambda: PAUSED.is_set() or twitch.busy() or TYPING.is_set())
        if not how:
            continue
        print(f"(awake: {how})")
        spotify.duck()
        display.update(apps=hands.allowed_apps(), status="listening")
        try:
            conversation(lambda secs=8: ears.listen(secs, carry_on=voice.take_barge()), how, spoken=True)
        finally:
            spotify.unduck()


def main():
    if "--text" not in sys.argv:
        keeper.claim_jarvis_lock()      # stops here if another Jarvis is already running
    display.start(open_browser="--no-display" not in sys.argv)
    display.update(apps=hands.allowed_apps(), status="sleeping")
    briefing.keep_display_fresh()
    try:
        import discord_bot
        discord_bot.start()
    except Exception as e:
        print(f"(Discord didn't start: {e})")
    voice.preload()
    if sys.platform == "win32":
        organizer.start_reminder_watch()
        try:
            import win
            win.clean_login_item()          # no auto-start unless "start_at_login" is true
        except Exception as e:
            print(f"(start-up check failed: {e})")
    if "--text" not in sys.argv:
        workforce.recover()                                   # a project cut off by the last restart
        updater.start(lambda: is_busy() or workforce.busy() or nightschool.busy(), restart_jarvis)
        updater.verify_on_start(restart_jarvis, is_busy)      # after an update: check the new version works
        learn.start(is_busy)
        nightschool.start(lambda: is_busy() or workforce.busy())
        import recall
        recall.start()
        hearing.start(lambda: is_busy() or workforce.busy() or nightschool.busy())
        predict.start(is_busy)
        threading.Thread(target=speak_up_loop, daemon=True, name="speak-up").start()
        threading.Thread(target=typed_loop, daemon=True, name="typed").start()
    if "--text" in sys.argv:
        def typed(secs=None):
            try:
                return input("You: ").strip()
            except EOFError:
                return "goodbye"
        conversation(typed, "wake")
        return
    try:
        if sys.platform == "win32":
            import menubar_win as menubar      # icon in the Windows taskbar tray
        else:
            import menubar
        threading.Thread(target=_guarded_loop, daemon=True).start()
        menubar.run(PAUSED)          # blocks; owns the main thread for the menu bar icon
    except ImportError:
        run_loop()


def _guarded_loop():
    try:
        run_loop()
    except Exception:
        import traceback
        traceback.print_exc()
        os._exit(1)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nGoodbye.")
    except Exception:
        import traceback
        traceback.print_exc()
        raise
