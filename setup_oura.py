"""Connect Jarvis to your Oura ring. Run: .venv/bin/python setup_oura.py"""
import http.server
import json
import secrets
import threading
import urllib.parse
import webbrowser

import oura


def save_cfg(**kw):
    cfg = oura._cfg()
    cfg.update(kw)
    oura.CONFIG.write_text(json.dumps(cfg, indent=2))


def test():
    try:
        days = oura.snapshot(2)
        print(f"\nIt works! Got Oura data for {len(days)} day(s).")
        print("Restart Jarvis and say: 'Jarvis, give me a vitals analysis.'")
    except Exception as e:
        print(f"\nConnected, but the test failed: {e}")


def with_token():
    print("\nGo to https://cloud.ouraring.com/personal-access-tokens (opening it now),")
    print("sign in, click 'Create New Personal Access Token', and copy it.")
    webbrowser.open("https://cloud.ouraring.com/personal-access-tokens")
    token = input("\nPaste the token here: ").strip()
    if not token:
        print("No token entered. Nothing changed.")
        return
    save_cfg(oura_token=token)
    test()


def with_app():
    print("\nYou'll make a free private 'app' on Oura's site so Jarvis can sign in as you.")
    print("1. Opening https://cloud.ouraring.com/oauth/applications - sign in and click 'New Application'.")
    print("2. Fill in anything for name, description, website and privacy/terms links")
    print("   (e.g. Jarvis, my assistant, https://example.com).")
    print(f"3. For Redirect URI enter exactly:  {oura.REDIRECT_URI}")
    print("4. Tick all the scopes, save, then copy the Client ID and Client Secret.")
    webbrowser.open("https://cloud.ouraring.com/oauth/applications")
    cid = input("\nClient ID: ").strip()
    secret = input("Client Secret: ").strip()
    if not cid or not secret:
        print("Missing details. Nothing changed.")
        return
    save_cfg(oura_client_id=cid, oura_client_secret=secret, oura_token="")
    state = secrets.token_urlsafe(16)
    got = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            got.update({k: v[0] for k, v in q.items()})
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<h2>Jarvis is connected to Oura. You can close this tab.</h2>")

        def log_message(self, *a):
            pass

    server = http.server.HTTPServer(("localhost", 8889), Handler)
    threading.Thread(target=server.handle_request, daemon=True).start()
    url = oura.AUTH_URL + "?" + urllib.parse.urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": oura.REDIRECT_URI,
        "scope": oura.SCOPES, "state": state})
    print("\nOpening Oura sign-in. Click Accept / Allow.")
    webbrowser.open(url)
    for _ in range(600):
        if got:
            break
        threading.Event().wait(0.5)
    server.server_close()
    if got.get("state") != state or "code" not in got:
        print(f"Sign-in didn't finish ({got.get('error', 'no response')}). Run this again to retry.")
        return
    tok = oura._post(oura.TOKEN_URL, {"grant_type": "authorization_code", "code": got["code"],
                                      "redirect_uri": oura.REDIRECT_URI, "client_id": cid,
                                      "client_secret": secret})
    oura.save_tokens(tok)
    test()


def main():
    print("== Connect Jarvis to your Oura ring ==\n")
    print("1. Personal access token (quickest, if Oura still offers it to you)")
    print("2. Oura app sign-in (works for everyone, takes about 3 minutes)")
    choice = input("\nPick 1 or 2 (Enter = 1): ").strip() or "1"
    with_app() if choice == "2" else with_token()


if __name__ == "__main__":
    main()
