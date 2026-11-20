"""Connects the Shorts clipper to your YouTube channel. Run: .venv/bin/python setup_youtube.py
(or double-click Connect YouTube)."""
import glob
import os
import sys
import time
import webbrowser

from clipper import settings, youtube

STEPS = """
One-time Google setup (free, about 10 minutes). Google needs this so Jarvis can upload to YOUR channel.

 1. Make a project: on the page that just opened, name it "Jarvis Clipper" and press Create.
 2. Turn on two APIs: press Enable on both pages that open next
    (YouTube Data API v3 and YouTube Analytics API). Pick the Jarvis Clipper project if asked.
 3. Branding / consent screen: press Get started, app name "Jarvis Clipper", your email,
    Audience "External", then finish. Under Audience, add your own Google email as a test user,
    then press "Publish app" (so Google doesn't sign Jarvis out every week; you can ignore the
    "unverified app" warning for yourself).
 4. Clients: Create client -> Application type "Desktop app" -> Create -> Download JSON.

Then come back here.
"""
PAGES = ["https://console.cloud.google.com/projectcreate",
         "https://console.cloud.google.com/apis/library/youtube.googleapis.com",
         "https://console.cloud.google.com/apis/library/youtubeanalytics.googleapis.com",
         "https://console.cloud.google.com/auth/overview",
         "https://console.cloud.google.com/auth/clients"]


def find_download():
    folder = os.path.expanduser("~/Downloads")
    files = sorted(glob.glob(os.path.join(folder, "client_secret*.json")), key=os.path.getmtime, reverse=True)
    return files[0] if files else None


def main():
    print("Connect YouTube to Jarvis's Shorts clipper\n")
    if not youtube.client():
        print(STEPS)
        if input("Open the Google pages now? (Y/n) ").strip().lower() != "n":
            for p in PAGES:
                webbrowser.open(p)
                time.sleep(1.5)
        input("\nPress Enter once you've downloaded the client JSON file...")
        path = find_download()
        if path:
            print(f"Found {os.path.basename(path)} in Downloads.")
        else:
            path = input("Drag the downloaded .json file into this window and press Enter: ").strip().strip("'\"")
        try:
            youtube.save_client(path)
        except Exception as e:
            print(f"That didn't work: {e}")
            return 1
        print("Saved (only on this computer).")
    print("\nNow sign in: your browser opens Google's page. Pick your YouTube channel and press Allow.")
    try:
        title = youtube.sign_in("main")
    except Exception as e:
        print(f"\nSign-in didn't work: {e}")
        return 1
    print(f"\nConnected to: {title}")
    if input("Upload approved clips to YouTube as PRIVATE videos? (Y/n) ").strip().lower() != "n":
        settings.update_channel("main", permissions={"upload_private": True})
        print("Private uploads are on. Clips still wait for your OK first.")
    print("Public publishing stays OFF. Turn it on in the clipper dashboard's Settings, or say "
          "'Jarvis, turn on public publishing'. Note: " + youtube.lock_note())
    return 0


if __name__ == "__main__":
    sys.exit(main())
