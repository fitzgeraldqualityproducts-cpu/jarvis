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
