"""Apps, websites, searches and Shortcuts on this computer."""
from skills import tool


@tool("Open an app (only ones the user allowed) or a website.", name="app or website name, or a web address")
def open_app_or_site(name: str):
    import hands
    import mac
    app = hands.find_app(name)
    if app:                                  # the brain already worked out which app: no "did you mean?"
        return hands.open_app(app)[1]
    return mac.open_thing(name)


@tool("Close an app that's open.", name="the app")
def close_app(name: str):
    import mac
    return mac.quit_app(name)


@tool("Bring an open app to the front.", name="the app")
def switch_to_app(name: str):
    import mac
    return mac.switch_to(name)


@tool("Open a Google search in the browser.", query="what to search for")
def google_search(query: str):
    import mac
    return mac.google(query)


@tool("Open a YouTube search in the browser.", query="what to look for")
def youtube_search(query: str):
    import mac
    return mac.youtube(query)


@tool("Run one of the user's Mac Shortcuts.", name="the shortcut's name")
def run_shortcut(name: str):
    import mac
    return mac.run_shortcut(name)


@tool("List the user's Mac Shortcuts.")
def list_shortcuts():
    import mac
    return mac.list_shortcuts()


@tool("The apps Jarvis is allowed to open.")
def allowed_apps():
    import hands
    apps = hands.allowed_apps()
    return "Allowed apps: " + (", ".join(apps) if apps else "none") + ". Any website is fine."
