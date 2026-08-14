"""Checks the Twitch credentials - against Twitch itself, not merely for their presence.

Called by check_credentials.py in the project root. The contract is a check() function
yielding (level, message) pairs; levels are "ok", "warn", "fail", "skip" and "detail"
(continuation line for the previous message).

Deliberately reads the environment itself instead of importing config.py: the os.environ[...]
in there raises as soon as a variable is missing - i.e. in exactly the case this module is
supposed to diagnose. A credentials test that crashes on missing credentials would be useless.

The three parts deliberately do not hang together: a dead user token should not prevent the
app credentials and the channel from being checked - that is precisely when you want to know
whether get_token.py stands any chance at all. For the channel check the app token will do.

What is asked here costs nothing and changes nothing: /oauth2/validate, one app token (which
simply expires), two reading Helix calls.
"""

import os

import requests

from . import scopes as twitch_scopes

VALIDATE_URL = "https://id.twitch.tv/oauth2/validate"
TOKEN_URL = "https://id.twitch.tv/oauth2/token"
HELIX = "https://api.twitch.tv/helix"
TIMEOUT = 10


def _duration(seconds):
    """Remaining lifetime in something a human can read."""
    if seconds >= 86400:
        return f"{seconds // 86400}d {seconds % 86400 // 3600}h"
    if seconds >= 3600:
        return f"{seconds // 3600}h {seconds % 3600 // 60}min"
    return f"{seconds // 60}min"


def _check_app(client_id, client_secret, out):
    """Are CLIENT_ID/SECRET a valid pair? Returns the app token the attempt yields along the
    way - with it the channel can be looked up even when the user token is dead. `out`
    collects the messages."""
    if not (client_id and client_secret):
        return None
    try:
        response = requests.post(TOKEN_URL, timeout=TIMEOUT, data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "client_credentials",
        })
    except requests.RequestException as e:
        out.append(("warn", f"app credentials not checkable: {e}"))
        return None

    if response.status_code == 200:
        out.append(("ok", "TWITCH_CLIENT_ID/SECRET are a valid app pair."))
        return response.json().get("access_token")
    try:
        detail = response.json().get("message", "")
    except ValueError:
        detail = response.text[:80]
    out.append(("fail", f"TWITCH_CLIENT_ID/SECRET werden abgelehnt "
                        f"({response.status_code}): {detail}"))
    return None


def _validate(access_token, out):
    """The user token at /oauth2/validate. None when it is not (or no longer) valid."""
    try:
        response = requests.get(
            VALIDATE_URL, headers={"Authorization": f"OAuth {access_token}"}, timeout=TIMEOUT,
        )
    except requests.RequestException as e:
        out.append(("fail", f"Twitch not reachable: {e}"))
        return None

    if response.status_code == 401:
        out.append(("fail", "TWITCH_CHAT_ACCESS_TOKEN has expired or is invalid - "
                            "fetch a new one with: python3 -m platforms.twitch.get_token"))
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
        yield "skip", "no Twitch variables set - the platform would not load."
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
        yield "fail", f"missing from the .env: {', '.join(missing)}"

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
    """Everything hanging on a valid user token: owner, lifetime, renewability, scopes."""
    login = (info.get("login") or "").lower()
    token_scopes = set(info.get("scopes") or [])
    expires_in = info.get("expires_in", 0)
    token_client_id = info.get("client_id", "")

    yield "ok", f"Chat token valid, belongs to '{login}'."

    if chat_client_id and token_client_id != chat_client_id:
        yield "warn", (f"TWITCH_CHAT_CLIENT_ID ({chat_client_id}) is not the app that issued "
                       f"the token ({token_client_id}) - the refresh will fail.")

    own_app = token_client_id == client_id
    effective_secret = chat_client_secret or (client_secret if own_app else "")

    if expires_in == 0:
        yield "ok", "Token does not expire (expires_in: 0) - a refresh is never needed."
    else:
        yield ("ok" if refresh_token and effective_secret else "fail",
               f"Token expires in {_duration(expires_in)}.")
        if not refresh_token:
            yield "fail", "no TWITCH_CHAT_REFRESH_TOKEN - the token cannot be renewed."
        elif not effective_secret:
            yield "fail", ("the token comes from a foreign app and TWITCH_CHAT_CLIENT_SECRET "
                           "is empty - it can never be renewed. See docs/twitch.md#tokens")

    if not own_app:
        yield "warn", (f"the chat token does not come from your own app "
                       f"({token_client_id} instead of {client_id}).")

    required = set(twitch_scopes.REQUIRED)
    lacking = sorted(required - token_scopes)
    if lacking:
        yield "warn", f"{len(lacking)} von {len(required)} Scopes fehlen - betroffen ist:"
        for scope in lacking:
            yield "detail", f"{scope} ({twitch_scopes.CAPABILITIES.get(scope, '?')})"
        yield "detail", "neu holen mit: python3 -m platforms.twitch.get_token"
    else:
        yield "ok", f"all {len(required)} required scopes present."

    extra = sorted(token_scopes - required)
    if extra:
        yield "warn", (f"{len(extra)} scope(s) more than needed - every unused one is only "
                       f"additional damage should the token go astray: {', '.join(extra)}")


def _report_channel(channel, info, access_token, app_token, client_id):
    """Does the channel exist, and may the token's account moderate there? The channel can be
    looked up with the app token too; the moderator status cannot."""
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
        yield "warn", f"channel not checkable: {e}"
        return

    if not data:
        yield "fail", f"the channel '{channel}' does not exist on Twitch."
        return
    broadcaster_id = data[0]["id"]
    yield "ok", f"Kanal '{channel}' gefunden (id {broadcaster_id})."

    if info is None:
        yield "detail", "moderator status only checkable once the chat token is valid again."
        return

    login = (info.get("login") or "").lower()
    if login == channel:
        yield "ok", ("the token belongs to the broadcaster themselves - moderation and "
                     "ad-break notices (channel:read:ads) work.")
        return

    # A foreign account: is it a moderator in the channel at all? /chat/settings reveals it
    # without changing anything - it answers with the moderator fields only when moderator_id
    # really is a moderator, and with 401 otherwise.
    yield "warn", ("the token does not belong to the broadcaster - ad-break notices will not "
                   "appear; Twitch grants channel:read:ads only to your own account.")
    try:
        settings = requests.get(f"{HELIX}/chat/settings", headers=headers, timeout=TIMEOUT,
                                params={"broadcaster_id": broadcaster_id,
                                        "moderator_id": info.get("user_id", "")})
    except requests.RequestException as e:
        yield "warn", f"moderator status not checkable: {e}"
        return

    if settings.status_code == 200:
        yield "ok", f"'{login}' is a moderator in '{channel}' - deleting and timeouts work."
    elif settings.status_code == 401:
        yield "fail", (f"'{login}' is not a moderator in '{channel}' - deleting and timeouts "
                       f"will fail, whatever scopes the token has.")
    else:
        yield "warn", f"Moderator-Status unklar (HTTP {settings.status_code})."
