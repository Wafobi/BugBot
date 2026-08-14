"""Checks the Discord credentials - and whether the bot finds on the server what
discord.json says.

Called by check_credentials.py in the project root. The contract is a check() function
yielding (level, message) pairs; levels are "ok", "warn", "fail", "skip" and "detail"
(continuation line for the previous message).

The name check is in here because it catches the same failure as a wrong token, only more
quietly: roles and channels are looked up *by their name*. If roles.moderator names something
that does not exist on the server, nobody there is a moderator, and the mod commands silently
do nothing.

Reads the environment itself instead of importing config.py - that one raises on a missing
token, i.e. in exactly the case this module is supposed to report.
"""

import json
import os
from pathlib import Path

import requests

API = "https://discord.com/api/v10"
TIMEOUT = 10
CONFIG_PATH = Path(__file__).parent / "discord.json"

# Application flags by which Discord reports which privileged intents are switched on in the
# developer portal. They exist twice over: the "LIMITED" variant means "switched on, but the
# app is not verified for it" - below 100 servers it works regardless. So for "is the box
# ticked?" both count.
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
    """(role names, channel names) from discord.json - i.e. what has to exist on the server.
    Empty names are deliberate: that is how you switch a function off."""
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
        yield "skip", "DISCORD_TOKEN is not set - the platform would not load."
        return

    # --- The token ------------------------------------------------------------------
    try:
        me = _get("/users/@me", token)
    except requests.RequestException as e:
        yield "fail", f"Discord not reachable: {e}"
        return

    if me.status_code == 401:
        yield "fail", "DISCORD_TOKEN is rejected - generate a new one in the developer portal."
        return
    if me.status_code != 200:
        yield "fail", f"/users/@me answers {me.status_code}: {me.text[:120]}"
        return

    user = me.json()
    yield "ok", f"Token valid, the bot is '{user.get('username')}' (id {user.get('id')})."

    # --- The privileged intents -------------------------------------------------------
    try:
        app = _get("/applications/@me", token)
        flags = app.json().get("flags", 0) if app.status_code == 200 else None
    except (requests.RequestException, ValueError):
        flags = None

    if flags is None:
        yield "warn", "Intents not checkable - /applications/@me does not answer."
    else:
        for label, mask in INTENT_FLAGS.items():
            if flags & mask:
                yield "ok", f"Intent '{label}' is switched on."
            else:
                yield "fail", (f"Intent '{label}' is OFF - switch it on in the developer "
                               f"portal under Bot, otherwise the bot sees nothing.")

    # --- The servers ------------------------------------------------------------------
    try:
        guilds_response = _get("/users/@me/guilds", token)
        guilds = guilds_response.json() if guilds_response.status_code == 200 else []
    except (requests.RequestException, ValueError) as e:
        yield "warn", f"Servers not queryable: {e}"
        return

    if not guilds:
        yield "fail", ("the bot is on no server - invite it via OAuth2 -> URL Generator "
                       "(scope 'bot').")
        return

    wanted_roles, wanted_channels = _configured_names()
    yield "ok", f"on {len(guilds)} server(s): {', '.join(g['name'] for g in guilds)}"

    for guild in guilds:
        guild_id, guild_name = guild["id"], guild["name"]
        try:
            roles = _get(f"/guilds/{guild_id}/roles", token)
            channels = _get(f"/guilds/{guild_id}/channels", token)
        except requests.RequestException as e:
            yield "warn", f"'{guild_name}': not queryable ({e})"
            continue
        if roles.status_code != 200 or channels.status_code != 200:
            yield "warn", (f"'{guild_name}': roles/channels not readable - is the bot "
                           f"missing the permission?")
            continue

        role_names = {r["name"] for r in roles.json()}
        channel_names = {c["name"] for c in channels.json()}
        missing_roles = sorted(wanted_roles - role_names)
        missing_channels = sorted(wanted_channels - channel_names)

        if not missing_roles and not missing_channels:
            yield "ok", f"'{guild_name}': every role and channel from discord.json is present."
            continue

        yield "warn", f"'{guild_name}': discord.json names things that do not exist there -"
        for name in missing_roles:
            yield "detail", f"role missing: {name}"
        for name in missing_channels:
            yield "detail", f"channel missing: {name}"
        yield "detail", "as long as that is so, the affected function silently does nothing."
