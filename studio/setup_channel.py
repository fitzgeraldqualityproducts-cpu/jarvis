"""Phase 4 helpers run from the two launchers in Jarvis's folder.

  brand_flow()    "Set Up YouTube Channel": name ideas with handle checks -> Connor picks one ->
                  logo, banner, description and brand guide -> step-by-step channel creation
  connect_flow()  "Connect YouTube Business": protects Connor's existing channel, signs in to the
                  NEW channel, locks the studio to it and checks upload and analytics permission"""
import os
import subprocess
import sys
import time
import webbrowser

from studio import branding, guard, health, ytauth

CREATE_STEPS = """
Create the channel (YouTube doesn't let apps do this, so it's done by hand, about 5 minutes):
 1. In a browser, sign in to YouTube with the NEW Google account you made for the business
    (not your usual one). Tip: use a separate Chrome profile or a private window.
 2. Click your picture (top right) > Create a channel. Use the name: {name}
 3. Handle: type {handle}. If YouTube says it's taken, try one of the others marked "not found".
 4. Customise channel > Branding: upload logo.png as the picture and banner.png as the banner
    (both are in the folder that just opened).
 5. Customise channel > Basic info: paste description.txt into the description.
Then double-click "Connect YouTube Business".
"""


def _open(path):
    if sys.platform == "darwin":
        subprocess.run(["open", str(path)])


def brand_flow():
    health.startup()
    print("Thinking up channel names and checking each handle on YouTube...\n")
    ideas = branding.names()
    for i, n in enumerate(ideas, 1):
        print(f"{i:2}. {n['name']:28} @{n['handle']:24} {n['handle_status']:10} {n['why']}")
    print('\n"not found" = no YouTube channel uses it today; YouTube Studio confirms when you claim it.')
    while True:
        pick = input("\nType the number you like (or your own name): ").strip()
        if pick.isdigit() and 1 <= int(pick) <= len(ideas):
            name, handle = ideas[int(pick) - 1]["name"], ideas[int(pick) - 1]["handle"]
            break
        if len(pick) >= 3:
            name = pick
            handle = branding.clean_handle(input("Handle to use (no @): ").strip() or pick.replace(" ", ""))
            print(f"@{handle}: {branding.check_handle(handle)}")
            break
    folder = branding.make(name, handle)
    print(f"\nMade the logo, banner, description and brand guide in {folder}")
    _open(folder)
    print(CREATE_STEPS.format(name=name, handle="@" + handle))
    webbrowser.open("https://www.youtube.com/create_channel")
    return 0


GOOGLE_STEPS = """
One-time Google setup for sign-in (free; never add billing). Use the Google Cloud project you made
for the research key ("Jarvis Business"):
 1. Turn on the YouTube Analytics API too (page opening now; press Enable).
 2. Google Auth Platform > Branding: app name "Jarvis Business", your email. Audience "External".
 3. Audience: press "Publish app" (so Google doesn't sign the channel out every 7 days). Google
    shows an "unverified app" warning at sign-in; that's expected for your own app.
 4. Clients > Create client > "Desktop app" > Create > Download JSON.
"""
PUBLISH_NOTE = """
Using the Google sign-in app you already made for the clipper. Two one-time checks in that Google
Cloud project (pick it at the top of the page; its client id starts with the same numbers):
 1. YouTube Analytics API: press Enable (page opening now; skip if it says Manage).
 2. Google Auth Platform > Audience: if it says "Testing", press "Publish app" and confirm.
    Otherwise Google blocks the new business account (Error 403: access_denied) and signs it out
    every 7 days. Google then shows an "unverified app" warning at sign-in: press Advanced >
    Go to (app name). That's expected for your own app.
"""
GOOGLE_PAGES = ["https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
                "https://console.cloud.google.com/auth/overview",
                "https://console.cloud.google.com/auth/audience",
                "https://console.cloud.google.com/auth/clients"]


def connect_flow():
    from clipper import youtube
    health.startup()
    print("Connect the YouTube BUSINESS channel to Jarvis\n")
    if not youtube.client():
        print(GOOGLE_STEPS)
        for p in GOOGLE_PAGES:
            webbrowser.open(p)
            time.sleep(1.2)
        input("Press Enter once the client JSON is downloaded...")
        import setup_youtube
        path = setup_youtube.find_download() or input("Drag the .json file here and press Enter: ").strip().strip("'\"")
        youtube.save_client(path)
        print("Saved (only on this computer).")
    else:
        print(PUBLISH_NOTE)
        for p in GOOGLE_PAGES[:1] + GOOGLE_PAGES[2:3]:
            webbrowser.open(p)
            time.sleep(1.2)
        input("Press Enter once the app is published (or the new account is a test user)...")
    if not guard.protected_channels():
        print("\nFirst, so Jarvis can NEVER post to your existing channel:")
        h = input("Type your EXISTING channel's handle (like @yourname), or press Enter if you have none: ").strip()
        if h:
            try:
                c = ytauth.protect_existing(h)
                print(f"Protected: {c['title']} ({c['id']}). The business can never upload there.")
            except Exception as e:
                print(f"Couldn't look that up ({e}). You can add it later with: .venv/bin/python -m studio protect @handle")
    if ytauth.connected() and guard.locked_channel() and "--again" not in sys.argv:
        try:
            me = ytauth.whoami()
            missing = [x for x in guard.SCOPES if x not in ytauth.granted_scopes()]
            if missing:
                print("\nThe saved sign-in is missing a permission Jarvis now uses (series playlists), so let's sign in again.")
            elif me["id"] == guard.locked_channel()["youtube_channel_id"]:
                print(f"\nAlready connected to {me['title']}. Nothing to do. (To sign in again anyway: add --again.)")
                return 0
        except Exception as e:
            print(f"\nThe saved sign-in doesn't work any more ({e}), so let's sign in again.")
    print("\nNow sign in. In Google's page, choose the NEW business account and its channel, then press Allow.")
    try:
        me = ytauth.connect()
    except guard.WrongChannel as e:
        print(f"\nStopped: {e}")
        return 1
    print(f"\nLocked to: {me['title']} ({me['handle'] or me['id']})")
    if input("Is that the NEW business channel? (y/n) ").strip().lower() != "y":
        os.remove(ytauth._path())
        from studio import db
        db.run("UPDATE channel SET youtube_channel_id=NULL, title=NULL, locked_at=NULL WHERE id=1")
        guard.audit("channel.lock", False, "Connor said it was the wrong channel; unlocked")
        print("Undone. Run this again and pick the other account.")
        return 1
    rep = ytauth.verify()
    print(f"Upload permission: {'yes' if rep.get('can_upload') else 'NO'}; analytics: "
          f"{'yes' if rep.get('can_read_analytics') else 'no'}; extra permissions: {rep.get('extra_scopes') or 'none'}")
    print("\nDone. Nothing has been uploaded. The first video will be a PRIVATE test for you to check.")
    return 0 if rep.get("ok") else 1
