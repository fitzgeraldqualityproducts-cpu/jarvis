"""Jarvis in Discord.

  * Text: in your server, start a message with "Jarvis", @mention him, reply to him, or DM him,
    and he answers in the chat. Your friends can ask him things too (he won't share your
    private stuff with them: no notes, memory, calendar or files).
  * Voice: say "Hey Jarvis, join Discord" (or type "jarvis join" in Discord) while you're in a
    voice channel. He joins your call and speaks there: his answers to you, and his answers to
    questions typed in the chat. "Leave Discord" sends him away. He also leaves when you do.
    (Discord doesn't let bots hear people in encrypted calls, so friends type to him.)

Set up once with the Connect Discord file in this folder (setup_discord.py).
"""
import asyncio
import glob
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"
TRIGGER = re.compile(r"^\s*(?:hey\s+|yo\s+|ok\s+)?jarvis\b[\s,:!.?-]*", re.I)

_state = {"client": None, "loop": None, "owner": None, "vc": None, "queue": None, "error": ""}
_guest_times = {}           # user id -> last time they asked
_guest_hour = []            # times of guest questions in the last hour
_mirror = threading.Event()  # mirror Jarvis's spoken answers into the call


def _cfg():
    try:
        return json.loads(CONFIG.read_text())
    except Exception:
        return {}


def token():
    return (_cfg().get("discord_token") or "").strip()


def connected():
    vc = _state["vc"]
    return bool(vc and vc.is_connected())


# ---------------- audio ----------------

def _load_opus():
    import discord
    if discord.opus.is_loaded():
        return True
    try:
        discord.opus._load_default()
    except Exception:
        pass
    if discord.opus.is_loaded():
        return True
    # Macs don't ship libopus; the "av" package includes one.
    try:
        import av
        for lib in sorted(glob.glob(os.path.join(os.path.dirname(av.__file__), "**", "libopus*"), recursive=True)):
            try:
                discord.opus.load_opus(lib)
                return True
            except Exception:
                continue
    except ImportError:
        pass
    return discord.opus.is_loaded()


def to_pcm(path):
    """Any audio file -> 48 kHz 16-bit stereo PCM bytes (what Discord wants)."""
    import av
    out = bytearray()
    with av.open(str(path)) as c:
        rs = av.AudioResampler(format="s16", layout="stereo", rate=48000)
        for frame in c.decode(audio=0):
            for f in rs.resample(frame):
                out += f.to_ndarray().tobytes()
        for f in rs.resample(None):
            out += f.to_ndarray().tobytes()
    return bytes(out)


def _queue_pcm(pcm):
    loop, q = _state["loop"], _state["queue"]
    if loop and q is not None and pcm:
        loop.call_soon_threadsafe(q.put_nowait, pcm)


def speak(text):
    """Say something in the voice call (uses Jarvis's normal voice)."""
    if not connected() or not text:
        return
    import voice
    synth = getattr(voice, "_original_synthesize", voice.synthesize)

    def go():
        try:
            for chunk in voice._sentences(voice._speakable(text)):
                path = synth(chunk)
                try:
                    _queue_pcm(to_pcm(path))
                finally:
                    try:
                        os.remove(path)
                    except OSError:
                        pass
        except Exception as e:
            print(f"(Discord voice failed: {e})")
    threading.Thread(target=go, daemon=True).start()


def _hook_voice():
    """While Jarvis is in your call, everything he says out loud also plays there."""
    import voice
    if getattr(voice, "_discord_mirror", False):
        return
    voice._discord_mirror = True
    if hasattr(voice, "add_mirror"):
        voice.add_mirror(lambda: _mirror.is_set() and connected(), lambda path: _queue_pcm(to_pcm(path)))
        return
    original = voice.synthesize          # older voice.py
    voice._original_synthesize = original

    def synthesize(text):
        path = original(text)
        if _mirror.is_set() and connected():
            try:
                _queue_pcm(to_pcm(path))
            except Exception as e:
                print(f"(couldn't send that to Discord: {e})")
        return path
    voice.synthesize = synthesize


# ---------------- answering ----------------

def _run_claude(cmd, timeout, cwd):
    import brain
    runner = getattr(brain, "run_claude", None)
    if runner:
        return runner(cmd, timeout, cwd=cwd)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=cwd, stdin=subprocess.DEVNULL)


def guest_answer(name, question, history, place):
    """Answers a friend. No access to your memory, notes, calendar or files."""
    import brain
    import personality
    claude = brain.find_claude()
    if not claude:
        return "My brain's offline right now."
    chat = "\n".join(history[-8:])
    prompt = (
        f"{personality.persona()}\n\nRight now you're in Discord ({place}), chatting by text with {name}, "
        "a friend of your owner Connor. Reply like a Discord message: short (one to three sentences), "
        "casual, no headings. You can search the web for current facts. Never reveal anything private "
        "about Connor (his schedule, notes, location, accounts, files or what he's told you); if asked, "
        "joke that it's classified. Don't claim to do things you can't, like playing music or opening apps "
        "for them.\n\n"
        f"Recent chat:\n{chat}\n\n{name}: {question}\nJarvis:")
    cmd = [claude, "-p", prompt, "--disallowedTools", "Read Glob Grep Edit Write NotebookEdit Bash Task",
           "--allowedTools", "WebSearch WebFetch"]
    try:
        with tempfile.TemporaryDirectory() as tmp:            # nothing of yours in reach
            out = _run_claude(cmd, 120, tmp)
        return (out.stdout or "").strip() or "My brain blanked on that one."
    except subprocess.TimeoutExpired:
        return "That one's taking too long, try me again."


def owner_answer(question, place):
    import brain
    return brain.think(question, context=(
        f"This message was typed by Connor in Discord ({place}), where his friends can read your reply. "
        "Answer as a short Discord message, and don't bring up his private notes unless he asks."))


def _guest_allowed(uid):
    now = time.time()
    _guest_hour[:] = [t for t in _guest_hour if now - t < 3600]
    if now - _guest_times.get(uid, 0) < 8:
        return "slow"
    if len(_guest_hour) >= int(_cfg().get("discord_guest_limit", 40)):
        return "busy"
    _guest_times[uid] = now
    _guest_hour.append(now)
    return "ok"


# ---------------- the bot ----------------

def _make_client():
    import discord
    intents = discord.Intents.default()
    intents.message_content = True
    intents.voice_states = True
    client = discord.Client(intents=intents)

    async def owner_channel():
        oid = _state["owner"]
        for g in client.guilds:
            for ch in list(g.voice_channels) + list(getattr(g, "stage_channels", [])):
                if any(m.id == oid for m in ch.members):
                    return ch
        return None

    async def join():
        ch = await owner_channel()
        if not ch:
            return "Hop in a voice channel first, then I'll join you."
        if not _load_opus():
            return "I can't speak in calls yet. Run Connect Discord in my folder again to finish setting up voice."
        try:
            vc = _state["vc"]
            if vc and vc.is_connected():
                if vc.channel.id != ch.id:
                    await vc.move_to(ch)
            else:
                _state["vc"] = await ch.connect(self_deaf=True)
        except Exception as e:
            print(f"(Discord join failed: {e})")
            return "Discord wouldn't let me in the call. Check I have permission to join that channel."
        _mirror.set()
        return f"I'm in {ch.name}."

    async def leave():
        _mirror.clear()
        vc = _state["vc"]
        _state["vc"] = None
        if vc and vc.is_connected():
            await vc.disconnect()
            return "Left the call."
        return "I wasn't in a call."

    _state["join"], _state["leave"] = join, leave

    async def player():
        q = _state["queue"]
        while True:
            pcm = await q.get()
            vc = _state["vc"]
            if not (vc and vc.is_connected()):
                continue
            done = asyncio.Event()
            loop = asyncio.get_running_loop()
            try:
                vc.play(discord.PCMAudio(io.BytesIO(pcm)), after=lambda e: loop.call_soon_threadsafe(done.set))
                await done.wait()
            except Exception as e:
                print(f"(Discord playback failed: {e})")

    @client.event
    async def on_ready():
        try:
            app = await client.application_info()
            _state["owner"] = (app.team.owner_id if app.team else app.owner.id)
        except Exception as e:
            print(f"(couldn't tell who owns the Discord bot: {e})")
        if _state.get("player") is None:
            _state["player"] = asyncio.create_task(player())
        names = ", ".join(g.name for g in client.guilds) or "no servers yet"
        print(f"(Discord: logged in as {client.user}, in {names})")

    @client.event
    async def on_voice_state_update(member, before, after):
        vc = _state["vc"]
        if member.id == _state["owner"] and vc and vc.is_connected() and (after.channel is None or
                                                                           after.channel.id != vc.channel.id):
            if after.channel is None:
                await leave()
            else:
                await join()          # follow you to the new channel

    @client.event
    async def on_message(msg):
        if msg.author.bot:
            return
        text = msg.content or ""
        is_dm = msg.guild is None
        mentioned = client.user in msg.mentions
        replied = bool(msg.reference and getattr(msg.reference.resolved, "author", None) == client.user)
        m = TRIGGER.match(text)
        if not (is_dm or mentioned or replied or m):
            return
        question = re.sub(rf"<@!?{client.user.id}>", "", text[m.end():] if m else text).strip()
        is_owner = msg.author.id == _state["owner"]
        low = question.lower().strip(" .!?")
        if is_owner and re.fullmatch(r"(join|come|hop in|get in)( (the|my|this))?( call| vc| voice| channel)?( please)?", low):
            await msg.reply(await join(), mention_author=False)
            return
        if is_owner and re.fullmatch(r"(leave|go away|get out|disconnect|dip)( (the|my|this))?( call| vc| voice)?( please)?", low):
            await msg.reply(await leave(), mention_author=False)
            return
        if not question:
            await msg.reply("Yo, what's up?", mention_author=False)
            return
        if not is_owner:
            if not _cfg().get("discord_guests", True):
                return
            ok = _guest_allowed(msg.author.id)
            if ok != "ok":
                await msg.reply("One at a time, give me a sec." if ok == "slow"
                                else "I've answered a lot this hour, I need a breather.", mention_author=False)
                return
        place = "a DM" if is_dm else f"#{msg.channel.name} in {msg.guild.name}"
        history = []
        try:
            async for h in msg.channel.history(limit=9, before=msg):
                history.append(f"{h.author.display_name}: {h.content[:300]}")
            history.reverse()
        except Exception:
            pass
        loop = asyncio.get_running_loop()
        async with msg.channel.typing():
            if is_owner:
                answer = await loop.run_in_executor(None, owner_answer, question, place)
            else:
                answer = await loop.run_in_executor(None, guest_answer, msg.author.display_name, question,
                                                    history, place)
        for i in range(0, len(answer), 1900):
            await (msg.reply(answer[i:i + 1900], mention_author=False) if i == 0 else msg.channel.send(answer[i:i + 1900]))
        if connected():
            speak(answer)

    return client


def _run():
    async def main():
        _state["loop"] = asyncio.get_running_loop()
        _state["queue"] = asyncio.Queue()
        client = _make_client()
        _state["client"] = client
        await client.start(token())
    try:
        asyncio.run(main())
    except Exception as e:
        _state["error"] = str(e)
        print(f"(Discord stopped: {e})")


def start():
    """Called when Jarvis starts. Does nothing until Connect Discord has been run."""
    if not token() or _state["client"]:
        return False
    try:
        import discord  # noqa: F401
    except ImportError:
        print("(Discord: run Connect Discord in my folder to install what I need)")
        return False
    _hook_voice()
    threading.Thread(target=_run, daemon=True).start()
    return True


def _call(name):
    loop = _state["loop"]
    if not loop or not _state.get(name):
        return None
    try:
        return asyncio.run_coroutine_threadsafe(_state[name](), loop).result(20)
    except Exception as e:
        print(f"(Discord {name} failed: {e})")
        return "Discord didn't answer me."


JOIN_RE = re.compile(r"\b(join|hop in|get in|come in(?:to)?|jump in)(?: (?:the|my|our))? (?:discord|discord call|call|vc|voice chat)\b|"
                     r"\bjoin (?:me )?(?:in|on) discord\b")
LEAVE_RE = re.compile(r"\b(leave|exit|get out of|hop off|disconnect from)(?: (?:the|my|our))? (?:discord|discord call|call|vc|voice chat)\b")


def voice_command(t):
    """Handles 'join Discord' / 'leave Discord' said out loud. Returns what to say, or None."""
    if not (JOIN_RE.search(t) or LEAVE_RE.search(t)):
        return None
    if not token():
        return "I'm not on Discord yet. Double-click Connect Discord in my folder to set me up."
    if not _state["loop"]:
        return "I couldn't log in to Discord. " + (f"It said: {_state['error'][:100]}" if _state["error"] else
                                                  "Try restarting me.")
    if LEAVE_RE.search(t):
        return _call("leave") or "I wasn't in a call."
    return _call("join") or "I'm still logging in to Discord, try again in a moment."
