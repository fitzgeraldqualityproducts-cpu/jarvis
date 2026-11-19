"""Remembering things about the user, the weather, email and his own modes."""
import sys

from skills import tool


@tool("Remember a fact about the user for good.", fact="the fact, e.g. 'my gym is Planet Fitness'")
def remember(fact: str):
    import memory
    return memory.remember(fact)


@tool("Forget a fact he was asked to remember.", what="the fact, or 'everything'")
def forget(what: str):
    import memory
    return memory.forget(what)


@tool("Everything he's been asked to remember.")
def what_you_remember():
    import memory
    return memory.recall_summary()


@tool("Search everything the user and Jarvis have ever said to each other (by meaning, so 'that pizza place' "
      "finds 'Tony's on 5th'). Use it whenever they refer to something from an earlier conversation.",
      query="what to look for", how_many="how many results (default 5)")
def search_memory(query: str, how_many: int = 5):
    import recall
    hits = recall.search(query, k=max(1, min(int(how_many), 15)))
    if not hits:
        return "Nothing in my long-term memory about that."
    return "\n".join(f"{h['when']}: user said {h['user'][:400]!r}; Jarvis said {h['jarvis'][:400]!r}" for h in hits)


@tool("Delete past conversations about something from long-term memory (only when the user asks).",
      about="what the conversations were about")
def forget_conversations(about: str):
    import recall
    n = recall.forget(about)
    return f"Deleted {n} conversation{'s' if n != 1 else ''} about that." if n else "I didn't find any to delete."


@tool("The local weather right now (for other places, search the web instead).")
def weather():
    import weather as w
    return w.report()


if sys.platform != "win32":
    @tool("Summarise today's emails from the Mail app.")
    def read_email():
        import inbox
        return inbox.summary()


@tool("Switch personality: 'hype' (hype man) or 'butler' (calm British butler).", style="hype or butler")
def set_personality(style: str):
    import personality
    personality.set_personality("butler" if "butler" in style.lower() else "hype")
    return f"Personality is now {personality.current()}."
