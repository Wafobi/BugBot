"""Prüft die Discord-Zugangsdaten - und ob der Bot auf dem Server das vorfindet, was in
discord.json steht.

Aufgerufen von check_credentials.py im Projektstamm. Der Vertrag ist eine Funktion
check(), die (Stufe, Meldung)-Paare liefert; Stufen sind "ok", "warn", "fail", "skip"
und "detail" (Fortsetzungszeile zur vorherigen Meldung).

Die Namensprüfung ist hier mit drin, weil sie denselben Fehler abfängt wie ein falscher
Token, nur leiser: Rollen und Kanäle werden *über ihren Namen* gesucht. Steht in
roles.moderator etwas, das es auf dem Server nicht gibt, ist dort niemand Moderator, und
die Mod-Befehle tun still gar nichts.

Liest die Umgebung selbst statt config.py zu importieren - das wirft bei fehlendem Token,
also genau in dem Fall, den dieses Modul melden soll.
"""

import json
import os
from pathlib import Path

import requests

API = "https://discord.com/api/v10"
TIMEOUT = 10
CONFIG_PATH = Path(__file__).parent / "discord.json"

# Application-Flags, mit denen Discord meldet, welche privilegierten Intents im
# Entwicklerportal eingeschaltet sind. Es gibt sie doppelt: die "LIMITED"-Fassung heißt
# "eingeschaltet, aber die App ist dafür nicht geprüft" - unter 100 Servern funktioniert
# sie trotzdem. Für "ist der Haken gesetzt?" zählen deshalb beide.
INTENT_FLAGS = {
    "Server Members": (1 << 14) | (1 << 15),
    "Message Content": (1 << 18) | (1 << 19),
}


def _get(path, token, **params):
    return requests.get(
        f"{API}{path}",
        headers={"Authorization": f"Bot {token}"},
        params=params or None,
        timeout=TIMEOUT,
    )


def _configured_names():
    """(Rollennamen, Kanalnamen) aus discord.json - also das, was auf dem Server
    existieren muss. Leere Namen sind Absicht: so schaltet man eine Funktion ab."""
    try:
        data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set(), set()

    def values(section):
        return {v for k, v in section.items() if not k.startswith("_") and isinstance(v, str) and v.strip()}

    roles = values(data.get("roles", {})) | values(data.get("reaction_roles", {}))
    roles |= values(data.get("levels", {}).get("role_thresholds", {}))
    channels = values(data.get("channels", {})) | values(data.get("announce_channels", {}))
    if isinstance(data.get("clip_channel"), str) and data["clip_channel"].strip():
        channels.add(data["clip_channel"])
    return roles, channels


def check():
    token = os.environ.get("DISCORD_TOKEN", "").strip()
    if not token:
        yield "skip", "DISCORD_TOKEN ist nicht gesetzt - die Plattform würde nicht laden."
        return

    # --- Der Token ------------------------------------------------------------------
    try:
        me = _get("/users/@me", token)
    except requests.RequestException as e:
        yield "fail", f"Discord nicht erreichbar: {e}"
        return

    if me.status_code == 401:
        yield "fail", "DISCORD_TOKEN wird abgelehnt - im Entwicklerportal neu erzeugen."
        return
    if me.status_code != 200:
        yield "fail", f"/users/@me antwortet {me.status_code}: {me.text[:120]}"
        return

    user = me.json()
    yield "ok", f"Token gültig, Bot ist '{user.get('username')}' (id {user.get('id')})."

    # --- Die privilegierten Intents --------------------------------------------------
    try:
        app = _get("/applications/@me", token)
        flags = app.json().get("flags", 0) if app.status_code == 200 else None
    except (requests.RequestException, ValueError):
        flags = None

    if flags is None:
        yield "warn", "Intents nicht prüfbar - /applications/@me antwortet nicht."
    else:
        for label, mask in INTENT_FLAGS.items():
            if flags & mask:
                yield "ok", f"Intent '{label}' ist eingeschaltet."
            else:
                yield "fail", (f"Intent '{label}' ist AUS - im Entwicklerportal unter Bot "
                               f"einschalten, sonst sieht der Bot nichts.")

    # --- Die Server -------------------------------------------------------------------
    try:
        guilds_response = _get("/users/@me/guilds", token)
        guilds = guilds_response.json() if guilds_response.status_code == 200 else []
    except (requests.RequestException, ValueError) as e:
        yield "warn", f"Server nicht abfragbar: {e}"
        return

    if not guilds:
        yield "fail", ("der Bot ist auf keinem Server - über OAuth2 -> URL Generator "
                       "einladen (Scope 'bot').")
        return

    wanted_roles, wanted_channels = _configured_names()
    yield "ok", f"auf {len(guilds)} Server(n): {', '.join(g['name'] for g in guilds)}"

    for guild in guilds:
        guild_id, guild_name = guild["id"], guild["name"]
        try:
            roles = _get(f"/guilds/{guild_id}/roles", token)
            channels = _get(f"/guilds/{guild_id}/channels", token)
        except requests.RequestException as e:
            yield "warn", f"'{guild_name}': nicht abfragbar ({e})"
            continue
        if roles.status_code != 200 or channels.status_code != 200:
            yield "warn", (f"'{guild_name}': Rollen/Kanäle nicht lesbar - fehlt dem Bot "
                           f"die Berechtigung?")
            continue

        role_names = {r["name"] for r in roles.json()}
        channel_names = {c["name"] for c in channels.json()}
        missing_roles = sorted(wanted_roles - role_names)
        missing_channels = sorted(wanted_channels - channel_names)

        if not missing_roles and not missing_channels:
            yield "ok", f"'{guild_name}': alle Rollen und Kanäle aus discord.json vorhanden."
            continue

        yield "warn", f"'{guild_name}': discord.json nennt Namen, die es dort nicht gibt -"
        for name in missing_roles:
            yield "detail", f"Rolle fehlt: {name}"
        for name in missing_channels:
            yield "detail", f"Kanal fehlt: {name}"
        yield "detail", "solange das so ist, greift die betroffene Funktion still gar nicht."
