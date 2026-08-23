#!/usr/bin/env python3
"""One-off helper: fetches a Twitch user access token through the authorization code flow
and writes it, together with the refresh token, into .env.

Why it exists: the chat token has to come from *our own* app (TWITCH_CLIENT_ID). On the
refresh grant Twitch demands the client secret of exactly the app that issued the token - so a
token from a foreign token generator can never be renewed (see
platforms/twitch/api.py:refresh_chat_token). If the token is created here, TWITCH_CHAT_CLIENT_ID
== TWITCH_CLIENT_ID holds, the fallback in core/config.py applies, and the token watchdog in
platforms/twitch/bot.py can do its job.

Prerequisite: in the app at https://dev.twitch.tv/console/apps, http://localhost:3000 has to be
entered as an OAuth redirect URL (Twitch matches it exactly).

Call:  python3 -m platforms.twitch.get_token   (from the project directory)
"""

import http.server
import secrets
import sys
import urllib.parse
import webbrowser

import requests
from dotenv import find_dotenv, set_key

# The scope list lives in scopes.py so that it only has to be maintained in one place - the
# bot checks against the same list at startup.
from . import scopes

REDIRECT_URI = "http://localhost:3000"
PORT = 3000

try:
    from . import config
except KeyError as missing:
    sys.exit(f"❌ .env incomplete - {missing} is missing. Fill in .env after the pattern of "
             f".env.example first (the chat token lines may stay empty, this script fills "
             f"those).")

SCOPES = scopes.REQUIRED


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Accepts exactly one redirect from Twitch and puts the result on the server instance, so
    that serve_forever() can be ended afterwards."""

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.result = query
        error = query.get("error", [None])[0]
        if error:
            body = f"<h1>Fehlgeschlagen</h1><p>{error}: {query.get('error_description', [''])[0]}</p>"
        else:
            body = "<h1>Done.</h1><p>Token fetched - you can close this window.</p>"
        encoded = f"<!doctype html><meta charset='utf-8'>{body}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass  # no request logging on stderr


def main():
    env_path = find_dotenv()
    if not env_path:
        sys.exit("❌ No .env found - please run this from the project directory.")

    client_id = config.TWITCH_CLIENT_ID
    client_secret = config.TWITCH_CLIENT_SECRET
    if not client_id or not client_secret:
        sys.exit("❌ TWITCH_CLIENT_ID / TWITCH_CLIENT_SECRET fehlen in .env.")

    state = secrets.token_urlsafe(24)
    auth_url = "https://id.twitch.tv/oauth2/authorize?" + urllib.parse.urlencode({
        "client_id": client_id,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "state": state,
        # forces the consent dialog even when the app was already authorised - otherwise a
        # changed scope list silently gets you the old scopes
        "force_verify": "true",
    })

    print(f"🔑 Requesting {len(SCOPES)} scopes for client id {client_id}.")
    print("\n🌐 Open this URL in a browser (if it does not open by itself):\n")
    print(auth_url + "\n")
    # Best-effort only, the URL is printed above regardless - nothing here needs to fail loudly.
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass  # nosec B110

    server = http.server.HTTPServer(("localhost", PORT), CallbackHandler)
    server.result = None
    server.timeout = 300
    print(f"⏳ Warte auf den Redirect auf {REDIRECT_URI} (max. 5 Minuten)…")
    server.handle_request()
    server.server_close()

    if not server.result:
        sys.exit("❌ Kein Redirect empfangen - Zeitlimit erreicht.")

    if server.result.get("error"):
        sys.exit(f"❌ Twitch hat abgelehnt: {server.result['error'][0]} - "
                 f"{server.result.get('error_description', [''])[0]}")

    # State comparison: without it a foreign tab could send a planted code to our localhost
    # listener.
    if server.result.get("state", [None])[0] != state:
        sys.exit("❌ State does not match - response discarded.")

    code = server.result.get("code", [None])[0]
    if not code:
        sys.exit("❌ No code in the response.")

    print("🔄 Tausche Code gegen Token…")
    response = requests.post("https://id.twitch.tv/oauth2/token", timeout=15, data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code",
        "redirect_uri": REDIRECT_URI,
    })
    data = response.json()
    access, refresh = data.get("access_token"), data.get("refresh_token")
    if not access or not refresh:
        sys.exit(f"❌ Token-Tausch fehlgeschlagen: {data}")

    info = requests.get("https://id.twitch.tv/oauth2/validate", timeout=15,
                        headers={"Authorization": f"OAuth {access}"}).json()

    set_key(env_path, "TWITCH_CHAT_ACCESS_TOKEN", access)
    set_key(env_path, "TWITCH_CHAT_REFRESH_TOKEN", refresh)
    # identical to TWITCH_CLIENT_ID from now on - which lets core/config.py find the matching
    # secret by itself, and TWITCH_CHAT_CLIENT_SECRET may stay empty
    set_key(env_path, "TWITCH_CHAT_CLIENT_ID", client_id)

    print("\n✅ In .env geschrieben.")
    print(f"   Account:     {info.get('login')} (id {info.get('user_id')})")
    print(f"   Client-ID:   {info.get('client_id')}")
    print(f"   Valid for:   {info.get('expires_in')} seconds "
          f"(~{(info.get('expires_in') or 0) // 3600} h) - renewed automatically from now on")
    granted = set(info.get("scopes") or [])
    missing = [s for s in SCOPES if s not in granted]
    print(f"   Scopes:      {len(granted)} granted"
          + (f", MISSING: {', '.join(missing)}" if missing else " (all requested)"))
    print("\n👉 Do not forget: copy the same three values into the .env on the server too "
          "and restart the bot there.")


if __name__ == "__main__":
    main()
