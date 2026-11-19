"""Discord and Twitch."""
from skills import tool


@tool("Post a message in the user's Discord server, e.g. to tell the squad something.",
      message="the message, written as the user would say it", channel="channel name if they said one")
def discord_message(message: str, channel: str = ""):
    import discord_bot
    return discord_bot.send_text(message, channel or None)


@tool("Join or leave the user's Discord voice call.", action="join or leave")
def discord_call(action: str = "join"):
    import discord_bot
    return discord_bot.voice_command("leave discord" if "leave" in action.lower() else "join discord") or \
        "I'm not on Discord yet."


@tool("Turn Twitch stream mode on (reads chat aloud) or off.", on="true to start, false to stop")
def stream_mode(on: bool = True):
    import twitch
    if not on:
        return "Stream mode off." if twitch.stop() else "Stream mode wasn't on."
    return twitch.start() or "I don't know the Twitch channel yet: double-click Connect Twitch in my folder."
