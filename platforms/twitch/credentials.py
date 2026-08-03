"""Prüft die Twitch-Zugangsdaten - gegen Twitch selbst, nicht nur auf Vorhandensein.

Aufgerufen von check_credentials.py im Projektstamm. Der Vertrag ist eine Funktion
check(), die (Stufe, Meldung)-Paare liefert; Stufen sind "ok", "warn", "fail", "skip"
und "detail" (Fortsetzungszeile zur vorherigen Meldung).

Liest die Umgebung bewusst selbst statt config.py zu importieren: das dortige
os.environ[...] wirft, sobald eine Variable fehlt - also genau in dem Fall, den dieses
Modul diagnostizieren soll. Ein Zugangsdaten-Test, der an fehlenden Zugangsdaten
abstürzt, wäre nutzlos.

Die drei Teile hängen absichtlich nicht aneinander: ein toter User-Token soll nicht
verhindern, dass App-Zugangsdaten und Kanal geprüft werden - gerade dann will man ja
wissen, ob get_token.py überhaupt Aussicht auf Erfolg hat. Für die Kanalprüfung reicht
notfalls der App-Token.

Was hier gefragt wird, kostet nichts und ändert nichts: /oauth2/validate, ein App-Token
(der einfach verfällt), zwei lesende Helix-Aufrufe.
"""

import os

import requests

from . import scopes as twitch_scopes

VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"
TIMEOUT = 10


def _duration(seconds):
    """Restlaufzeit in etwas, das man lesen kann."""
    if seconds >= 86400:
        return f"{seconds // 86400}d {seconds % 86400 // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60}min"
    return f"{seconds // 60}min"


def _check_app(client_id, client_secret, out):
    """Sind CLIENT_ID/SECRET ein gültiges Paar? Gibt den App-Token zurück, den der
    Versuch nebenbei abwirft - mit ihm lässt sich der Kanal auch dann nachschlagen, wenn
    der User-Token hin ist. `out` sammelt die Meldungen."""
    if not (client_id and client_secret):
        return None
    try:
        response = requests.post(TOKEN_URL, timeout=TIMEOUT, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        })
    except requests.RequestException as e:
        out.append(("warn", f"App-Zugangsdaten nicht prüfbar: {e}"))
        return None

    if response.status_code == 200:
        out.append(("ok", "TWITCH_CLIENT_ID/SECRET sind ein gültiges App-Paar."))
        return response.json().get("access_token")
    try:
        detail = response.json().get("message", "")
    except ValueError:
        detail = response.text[:80]
    out.append(("fail", f"TWITCH_CLIENT_ID/SECRET werden abgelehnt "
                        f"({response.status_code}): {detail}"))
    return None


def _validate(access_token, out):
    """Der User-Token bei /oauth2/validate. None, wenn er nicht (mehr) gilt."""
    try:
        response = requests.get(
            VALIDATE_URL, headers={"Authorization": f"OAuth {access_token}"}, timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        out.append(("fail", f"Twitch nicht erreichbar: {e}"))
        return None

    if response.status_code == 401:
        out.append(("fail", "TWITCH_CHAT_ACCESS_TOKEN ist abgelaufen oder ungültig - "
                            "neu holen mit: python3 -m platforms.twitch.get_token"))
        return None
    if response.status_code != 200:
        out.append(("fail", f"/oauth2/validate antwortet {response.status_code}: "
                            f"{response.text[:120]}"))
        return None
    return response.json()


def check():
    channel = os.environ.get("TWITCH_CHANNEL", "").strip().lstrip("@").lower()
    client_id = os.environ.get("TWITCH_CLIENT_ID", "").strip()
    client_secret = os.environ.get("TWITCH_CLIENT_SECRET", "").strip()
    access_token = os.environ.get("TWITCH_CHAT_ACCESS_TOKEN", "").strip()
    refresh_token = os.environ.get("TWITCH_CHAT_REFRESH_TOKEN", "").strip()
    chat_client_id = os.environ.get("TWITCH_CHAT_CLIENT_ID", "").strip()
    chat_client_secret = os.environ.get("TWITCH_CHAT_CLIENT_SECRET", "").strip()

    if not any((channel, client_id, access_token)):
        yield "skip", "keine Twitch-Variablen gesetzt - die Plattform würde nicht laden."
        return

    missing = [
        name for name, value in (
            ("TWITCH_CHANNEL", channel),
            ("TWITCH_CLIENT_ID", client_id),
            ("TWITCH_CLIENT_SECRET", client_secret),
            ("TWITCH_CHAT_ACCESS_TOKEN", access_token),
        ) if not value
    ]
    if missing:
        yield "fail", f"fehlt in der .env: {', '.join(missing)}"

    # --- Die eigene App --------------------------------------------------------------
    messages = []
    app_token = _check_app(client_id, client_secret, messages)
    yield from messages

    # --- Der User-Token --------------------------------------------------------------
    info = None
    if access_token:
        messages = []
        info = _validate(access_token, messages)
        yield from messages

    if info is not None:
        yield from _report_token(
            info, client_id, client_secret, chat_client_id, chat_client_secret, refresh_token,
        )

    # --- Der Kanal -------------------------------------------------------------------
    yield from _report_channel(channel, info, access_token, app_token, client_id)


def _report_token(info, client_id, client_secret, chat_client_id, chat_client_secret,
                  refresh_token):
    """Alles, was am gültigen User-Token hängt: Besitzer, Laufzeit, Erneuerbarkeit,
    Scopes."""
    login = (info.get("login") or "").lower()
    token_scopes = set(info.get("scopes") or [])
    expires_in = info.get("expires_in", 0)
    token_client_id = info.get("client_id", "")

    yield "ok", f"Chat-Token gültig, gehört zu '{login}'."

    if chat_client_id and token_client_id != chat_client_id:
        yield "warn", (f"TWITCH_CHAT_CLIENT_ID ({chat_client_id}) ist nicht die App, die den "
                       f"Token ausgestellt hat ({token_client_id}) - der Refresh wird scheitern.")

    own_app = token_client_id == client_id
    effective_secret = chat_client_secret or (client_secret if own_app else "")

    if expires_in == 0:
        yield "ok", "Token läuft nicht ab (expires_in: 0) - ein Refresh ist nie nötig."
    else:
        yield ("ok" if refresh_token and effective_secret else "fail",
               f"Token läuft in {_duration(expires_in)} ab.")
        if not refresh_token:
            yield "fail", "kein TWITCH_CHAT_REFRESH_TOKEN - der Token kann nicht erneuert werden."
        elif not effective_secret:
            yield "fail", ("der Token stammt aus einer fremden App und TWITCH_CHAT_CLIENT_SECRET "
                           "ist leer - er kann nie erneuert werden. Siehe docs/twitch.md#tokens")

    if not own_app:
        yield "warn", (f"der Chat-Token stammt nicht aus deiner eigenen App "
                       f"({token_client_id} statt {client_id}).")

    required = set(twitch_scopes.REQUIRED)
    lacking = sorted(required - token_scopes)
    if lacking:
        yield "warn", f"{len(lacking)} von {len(required)} Scopes fehlen - betroffen ist:"
        for scope in lacking:
            yield "detail", f"{scope} ({twitch_scopes.CAPABILITIES.get(scope, '?')})"
        yield "detail", "neu holen mit: python3 -m platforms.twitch.get_token"
    else:
        yield "ok", f"alle {len(required)} benötigten Scopes vorhanden."

    extra = sorted(token_scopes - required)
    if extra:
        yield "warn", (f"{len(extra)} Scope(s) mehr als nötig - jeder ungenutzte ist nur "
                       f"zusätzlicher Schaden, falls der Token abhandenkommt: {', '.join(extra)}")


def _report_channel(channel, info, access_token, app_token, client_id):
    """Gibt es den Kanal, und darf der Token-Account dort moderieren? Der Kanal lässt
    sich auch mit dem App-Token nachschlagen; der Moderator-Status nicht."""
    if not channel:
        return

    if info is not None:
        headers = {"Client-Id": info.get("client_id", ""),
                   "Authorization": f"Bearer {access_token}"}
    elif app_token:
        headers = {"Client-Id": client_id, "Authorization": f"Bearer {app_token}"}
    else:
        return

    try:
        users = requests.get(f"{HELIX}/users", headers=headers,
                             params={"login": channel}, timeout=TIMEOUT)
        data = users.json().get("data", []) if users.status_code == 200 else []
    except (requests.RequestException, ValueError) as e:
        yield "warn", f"Kanal nicht prüfbar: {e}"
        return

    if not data:
        yield "fail", f"den Kanal '{channel}' gibt es auf Twitch nicht."
        return
    broadcaster_id = data[0]["id"]
    yield "ok", f"Kanal '{channel}' gefunden (id {broadcaster_id})."

    if info is None:
        yield "detail", "Moderator-Status erst prüfbar, wenn der Chat-Token wieder gilt."
        return

    login = (info.get("login") or "").lower()
    if login == channel:
        yield "ok", ("der Token gehört dem Broadcaster selbst - Moderation und "
                     "Werbepausen-Meldungen (channel:read:ads) funktionieren.")
        return

    # Ein fremder Account: ist er im Kanal überhaupt Moderator? /chat/settings verrät es,
    # ohne etwas zu ändern - es antwortet nur dann mit den Moderator-Feldern, wenn
    # moderator_id wirklich Moderator ist, und sonst mit 401.
    yield "warn", ("der Token gehört nicht dem Broadcaster - Werbepausen-Meldungen bleiben "
                   "aus, channel:read:ads gibt Twitch nur dem eigenen Account.")
    try:
        settings = requests.get(f"{HELIX}/chat/settings", headers=headers, timeout=TIMEOUT,
                                params={"broadcaster_id": broadcaster_id,
                                        "moderator_id": info.get("user_id", "")})
    except requests.RequestException as e:
        yield "warn", f"Moderator-Status nicht prüfbar: {e}"
        return

    if settings.status_code == 200:
        yield "ok", f"'{login}' ist Moderator in '{channel}' - Löschen und Timeouts gehen."
    elif settings.status_code == 401:
        yield "fail", (f"'{login}' ist in '{channel}' kein Moderator - Löschen und Timeouts "
                       f"scheitern, egal welche Scopes der Token hat.")
    else:
        yield "warn", f"Moderator-Status unklar (HTTP {settings.status_code})."
