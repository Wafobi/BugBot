#!/usr/bin/env python3
"""Einmal-Helfer: holt einen Twitch-User-Access-Token über den Authorization-Code-Flow
und schreibt ihn samt Refresh-Token in .env.

Warum es das gibt: der Chat-Token muss von *unserer eigenen* App (TWITCH_CLIENT_ID)
stammen. Twitch verlangt beim Refresh-Grant das Client-Secret genau der App, die den
Token ausgestellt hat - ein Token aus einem fremden Token-Generator kann deshalb nie
erneuert werden (siehe platforms/twitch/api.py:refresh_chat_token). Wird der Token hier
erzeugt, gilt TWITCH_CHAT_CLIENT_ID == TWITCH_CLIENT_ID, der Fallback in
core/config.py greift und der Token-Wächter in platforms/twitch/bot.py kann seinen Job tun.

Voraussetzung: in der App auf https://dev.twitch.tv/console/apps muss
http://localhost:3000 als OAuth Redirect URL eingetragen sein (Twitch prüft exakt).

Aufruf:  python3 -m platforms.twitch.get_token   (aus dem Projektverzeichnis)
"""

import http.server
import secrets
import sys
import urllib.parse
import webbrowser

import requests
from dotenv import find_dotenv, set_key

REDIRECT_URI = "http://localhost:3000"
PORT = 3000

# Die Scope-Liste lebt in scopes.py, damit sie nur an einer Stelle gepflegt werden
# muss - der Bot prüft beim Start gegen dieselbe Liste.
from . import scopes

try:
    from . import config
except KeyError as missing:
    sys.exit(f"❌ .env unvollständig - {missing} fehlt. Erst .env nach dem Muster von "
             f".env.example ausfüllen (die Chat-Token-Zeilen dürfen leer bleiben, die "
             f"füllt dieses Skript).")

SCOPES = scopes.REQUIRED


class CallbackHandler(http.server.BaseHTTPRequestHandler):
    """Nimmt genau einen Redirect von Twitch entgegen und legt das Ergebnis auf der
    Server-Instanz ab, damit serve_forever() danach beendet werden kann."""

    def do_GET(self):
        query = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self.server.result = query
        error = query.get("error", [None])[0]
        if error:
            body = f"<h1>Fehlgeschlagen</h1><p>{error}: {query.get('error_description', [''])[0]}</p>"
        else:
            body = "<h1>Geschafft.</h1><p>Token geholt - du kannst das Fenster schliessen.</p>"
        encoded = f"<!doctype html><meta charset='utf-8'>{body}".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, *args):
        pass  # kein Request-Logging auf stderr


def main():
    env_path = find_dotenv()
    if not env_path:
        sys.exit("❌ Keine .env gefunden - bitte im Projektverzeichnis ausführen.")

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
        # erzwingt den Zustimmungs-Dialog, auch wenn die App schon autorisiert war -
        # sonst bekommt man bei geänderter Scope-Liste stillschweigend die alten Scopes
        "force_verify": "true",
    })

    print(f"🔑 Fordere {len(SCOPES)} Scopes für Client-ID {client_id} an.")
    print("\n🌐 Öffne diese URL im Browser (falls sie nicht automatisch aufgeht):\n")
    print(auth_url + "\n")
    try:
        webbrowser.open(auth_url)
    except Exception:
        pass

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

    # State-Vergleich: ohne ihn könnte ein fremder Tab einen untergeschobenen Code
    # auf unseren localhost-Listener schicken.
    if server.result.get("state", [None])[0] != state:
        sys.exit("❌ State stimmt nicht überein - Antwort verworfen.")

    code = server.result.get("code", [None])[0]
    if not code:
        sys.exit("❌ Kein Code in der Antwort.")

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
    # ab jetzt identisch mit TWITCH_CLIENT_ID - dadurch findet core/config.py das
    # passende Secret von allein und TWITCH_CHAT_CLIENT_SECRET darf leer bleiben
    set_key(env_path, "TWITCH_CHAT_CLIENT_ID", client_id)

    print("\n✅ In .env geschrieben.")
    print(f"   Account:     {info.get('login')} (id {info.get('user_id')})")
    print(f"   Client-ID:   {info.get('client_id')}")
    print(f"   Gültig für:  {info.get('expires_in')} Sekunden "
          f"(~{(info.get('expires_in') or 0) // 3600} h) - wird ab jetzt automatisch erneuert")
    granted = set(info.get("scopes") or [])
    missing = [s for s in SCOPES if s not in granted]
    print(f"   Scopes:      {len(granted)} erteilt"
          + (f", FEHLEN: {', '.join(missing)}" if missing else " (alle angefragten)"))
    print("\n👉 Nicht vergessen: dieselben drei Werte auch in die .env auf dem Server "
          "übertragen und den Bot dort neu starten.")


if __name__ == "__main__":
    main()
