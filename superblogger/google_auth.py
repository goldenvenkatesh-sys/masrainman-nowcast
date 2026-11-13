#!/usr/bin/env python3
"""One-time Google sign-in for Super Blogger. Run it on YOUR computer (needs Python 3).

Before running (5 minutes, free):
  1. https://console.cloud.google.com/ -> create a project "Super Blogger".
  2. APIs & Services -> Library -> enable: "Blogger API v3", "Google Search Console API",
     "Google Analytics Data API", "Google Analytics Admin API".
  3. APIs & Services -> OAuth consent screen -> External -> app name "Super Blogger",
     your email -> add yourself as a test user -> then press "PUBLISH APP"
     (otherwise Google expires the sign-in after 7 days).
  4. APIs & Services -> Credentials -> Create credentials -> OAuth client ID ->
     "Desktop app" -> Download JSON (client_secret_....json).

Then:   python google_auth.py client_secret_XXXX.json
A browser opens -> sign in with the Google account that owns masrainman.blogspot.com ->
allow. ("Google hasn't verified this app" -> Advanced -> Go to Super Blogger: it is your
own app.) The script prints three values. Add them in GitHub:
  github.com/goldenvenkatesh-sys/masrainman-nowcast -> Settings -> Secrets and variables ->
  Actions -> New repository secret:  GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET, GOOGLE_REFRESH_TOKEN
"""
import http.server
import json
import secrets
import sys
import threading
import urllib.parse
import urllib.request
import webbrowser

SCOPES = ["https://www.googleapis.com/auth/blogger",
          "https://www.googleapis.com/auth/webmasters",
          "https://www.googleapis.com/auth/analytics.readonly"]


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cfg = json.load(open(sys.argv[1]))
    c = cfg.get("installed") or cfg.get("web")
    cid, csecret = c["client_id"], c["client_secret"]
    state = secrets.token_urlsafe(16)
    got = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
            if q.get("state", [""])[0] == state:
                got["code"] = q.get("code", [""])[0]
                got["error"] = q.get("error", [""])[0]
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write("<h2>Super Blogger: done. You can close this tab.</h2>".encode())

        def log_message(self, *a):
            pass

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    redirect = f"http://127.0.0.1:{srv.server_port}/"
    url = "https://accounts.google.com/o/oauth2/v2/auth?" + urllib.parse.urlencode({
        "client_id": cid, "redirect_uri": redirect, "response_type": "code", "scope": " ".join(SCOPES),
        "access_type": "offline", "prompt": "consent", "state": state})
    threading.Thread(target=srv.handle_request, daemon=True).start()
    print("Opening the Google sign-in page... if it doesn't open, paste this into your browser:\n" + url + "\n")
    webbrowser.open(url)
    while "code" not in got and "error" not in got:
        threading.Event().wait(0.5)
    if not got.get("code"):
        sys.exit(f"Sign-in failed: {got.get('error')}")
    data = urllib.parse.urlencode({"code": got["code"], "client_id": cid, "client_secret": csecret,
                                   "redirect_uri": redirect, "grant_type": "authorization_code"}).encode()
    tok = json.load(urllib.request.urlopen("https://oauth2.googleapis.com/token", data=data))
    if "refresh_token" not in tok:
        sys.exit(f"No refresh token returned: {tok}")
    print("Add these three GitHub secrets (Settings -> Secrets and variables -> Actions):\n")
    print("GOOGLE_CLIENT_ID     =", cid)
    print("GOOGLE_CLIENT_SECRET =", csecret)
    print("GOOGLE_REFRESH_TOKEN =", tok["refresh_token"])
    print("\nKeep them private. Super Blogger picks them up on its next run.")


if __name__ == "__main__":
    main()
