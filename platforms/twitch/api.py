# twitch_api.py
# Helix API calls for moderation actions. Twitch switched off moderation commands over IRC
# (PRIVMSG "/timeout", "/delete", ...) in February 2023 - delete and timeout have run
# exclusively through the Helix endpoints ever since.
# https://dev.twitch.tv/docs/chat/irc-migration/

import requests
from dotenv import set_key, find_dotenv
from . import config

ENV_PATH = find_dotenv()


_refresh_unavailable_logged = False


def refresh_chat_token():
    """Fetches a new Twitch chat access token via the refresh token and persists it in .env,
    because Twitch issues a new refresh token on every refresh too. Lives here (instead of in
    twitch_bot.py) so that the Helix calls below can refresh by themselves on a 401, without
    depending on the IRC-specific parts of twitch_bot.py.

    On the refresh grant Twitch demands the client_secret of the issuing app - without it the
    endpoint answers 400 "missing client secret". If it is missing (the token comes from a
    foreign app whose secret we do not have), we save ourselves the request and say so clearly
    once, rather than logging it again on every call of the token watchdog."""
    global _refresh_unavailable_logged
    if not config.TWITCH_CHAT_CLIENT_SECRET:
        if not _refresh_unavailable_logged:
            _refresh_unavailable_logged = True
            print(
                f"⚠️ Twitch chat token cannot be renewed: no client secret known for client "
                f"id {config.TWITCH_CHAT_CLIENT_ID}. Either set TWITCH_CHAT_CLIENT_SECRET in "
                f".env, or create the token afresh with your own app (TWITCH_CLIENT_ID)."
            )
        return None

    url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": config.TWITCH_CHAT_REFRESH_TOKEN,
        "client_id": config.TWITCH_CHAT_CLIENT_ID,
        "client_secret": config.TWITCH_CHAT_CLIENT_SECRET,
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        data = response.json()
        new_access = data.get("access_token")
        if not new_access:
            print(f"⚠️ Twitch-Token-Refresh fehlgeschlagen: {data}")
            return None

        config.TWITCH_CHAT_ACCESS_TOKEN = new_access
        if ENV_PATH:
            set_key(ENV_PATH, "TWITCH_CHAT_ACCESS_TOKEN", new_access)

        new_refresh = data.get("refresh_token")
        if new_refresh:
            config.TWITCH_CHAT_REFRESH_TOKEN = new_refresh
            if ENV_PATH:
                set_key(ENV_PATH, "TWITCH_CHAT_REFRESH_TOKEN", new_refresh)

        print("🔄 Twitch-Chat-Token erneuert.")
        return new_access
    except Exception as e:
        print(f"⚠️ Twitch-Token-Refresh fehlgeschlagen: {e}")
        return None


def _helix_request(method, url, chat_access_token, params=None, json_body=None):
    """Shared Helix request with automatic refresh+retry on a 401 - the basis for every call
    below, so this behaviour is not duplicated in each function. Returns None on network
    errors (callers have to check for it)."""
    def attempt(token):
        headers = {"Client-Id": config.TWITCH_CHAT_CLIENT_ID, "Authorization": f"Bearer {token}"}
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        return requests.request(method, url, headers=headers, params=params, json=json_body, timeout=10)

    try:
        response = attempt(chat_access_token)
        if response.status_code == 401:
            new_token = refresh_chat_token()
            if new_token:
                response = attempt(new_token)
        return response
    except Exception as e:
        print(f"⚠️ Twitch-Helix-Request-Fehler ({method} {url}): {e}")
        return None


def get_app_access_token():
    url = "https://id.twitch.tv/oauth2/token"
    payload = {
        "client_id": config.TWITCH_CLIENT_ID,
        "client_secret": config.TWITCH_CLIENT_SECRET,
        "grant_type": "client_credentials",
    }
    try:
        response = requests.post(url, data=payload, timeout=10)
        return response.json().get("access_token")
    except Exception:
        return None


def get_broadcaster_id(channel_login):
    """Resolves the channel's numeric user id using an app access token."""
    token = get_app_access_token()
    if not token:
        return None
    headers = {"Client-Id": config.TWITCH_CLIENT_ID, "Authorization": f"Bearer {token}"}
    try:
        response = requests.get(
            "https://api.twitch.tv/helix/users",
            headers=headers, params={"login": channel_login.lower()}, timeout=10,
        )
        data = response.json().get("data", [])
        return data[0]["id"] if data else None
    except Exception as e:
        print(f"⚠️ Could not resolve the broadcaster id: {e}")
        return None


def validate_token_info(chat_access_token):
    """Raw response from https://id.twitch.tv/oauth2/validate - among others "scopes" and
    "expires_in" (remaining lifetime in seconds, the basis for the token watchdog in
    twitch_bot.py). None on error/expired token."""
    headers = {"Authorization": f"OAuth {chat_access_token}"}
    try:
        response = requests.get("https://id.twitch.tv/oauth2/validate", headers=headers, timeout=10)
        if response.status_code == 200:
            return response.json()
        print(f"⚠️ Twitch-Token-Validierung fehlgeschlagen ({response.status_code}): {response.text}")
        return None
    except Exception as e:
        print(f"⚠️ Twitch-Token-Validierung fehlgeschlagen: {e}")
        return None


def validate_token(chat_access_token):
    """Asks Twitch which scopes the current chat token actually has (for the startup
    diagnostics in twitch_bot.py). None on error/expired token."""
    info = validate_token_info(chat_access_token)
    return info.get("scopes", []) if info is not None else None


def get_users(logins, chat_access_token):
    """Resolves up to 100 logins at once into user objects (id, login, display_name).
    `logins=None` returns the token's own owner instead."""
    params = [("login", login.lstrip("@").lower()) for login in logins] if logins else None
    response = _helix_request("GET", "https://api.twitch.tv/helix/users", chat_access_token, params=params)
    if response is not None and response.status_code == 200:
        return response.json().get("data", [])
    if response is not None:
        print(f"⚠️ Twitch-User-Lookup fehlgeschlagen ({response.status_code}): {response.text}")
    return []


def get_moderator_id(chat_access_token):
    """Resolves the user id of the bot/mod account via its own chat token."""
    users = get_users(None, chat_access_token)
    return users[0]["id"] if users else None


def delete_chat_message(broadcaster_id, moderator_id, message_id, chat_access_token):
    """Deletes a single chat message. Requires scope moderator:manage:chat_messages."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "message_id": message_id}
    response = _helix_request("DELETE", "https://api.twitch.tv/helix/moderation/chat", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Delete fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def timeout_user(broadcaster_id, moderator_id, user_id, duration, reason, chat_access_token):
    """Times a user out for `duration` seconds. Requires scope moderator:manage:banned_users."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
    body = {"data": {"user_id": user_id, "duration": duration, "reason": reason[:500]}}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/bans", chat_access_token, params=params, json_body=body)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Timeout fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def ban_user(broadcaster_id, moderator_id, user_id, reason, chat_access_token):
    """Bans a user permanently (no duration field = permanent rather than a timeout).
    Requires scope moderator:manage:banned_users."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
    body = {"data": {"user_id": user_id, "reason": reason[:500]}}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/bans", chat_access_token, params=params, json_body=body)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Ban fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def unban_user(broadcaster_id, moderator_id, user_id, chat_access_token):
    """Lifts a ban or timeout. Requires scope moderator:manage:banned_users."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "user_id": user_id}
    response = _helix_request("DELETE", "https://api.twitch.tv/helix/moderation/bans", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Unban fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def warn_user(broadcaster_id, moderator_id, user_id, reason, chat_access_token):
    """Issues a Twitch-side warning (the user sees a warning banner on their next attempt to
    chat). Requires scope moderator:manage:warnings."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
    body = {"data": {"user_id": user_id, "reason": reason[:500]}}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/warnings", chat_access_token, params=params, json_body=body)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Warnung fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def get_chatters_count(broadcaster_id, moderator_id, chat_access_token):
    """Returns the number of users currently present in chat (Helix field "total"), or None
    on error. Requires scope moderator:read:chatters."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "first": 1}
    response = _helix_request("GET", "https://api.twitch.tv/helix/chat/chatters", chat_access_token, params=params)
    if response is not None and response.status_code == 200:
        return response.json().get("total")
    if response is not None:
        print(f"⚠️ Twitch-Chatter-Abfrage fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def get_stream_info(broadcaster_id, chat_access_token):
    """Returns the current stream data (among others started_at, title, game_name), or None
    when the channel is offline. The basis for !uptime."""
    response = _helix_request(
        "GET", "https://api.twitch.tv/helix/streams", chat_access_token, params={"user_id": broadcaster_id}
    )
    if response is not None and response.status_code == 200:
        data = response.json().get("data", [])
        return data[0] if data else None
    if response is not None:
        print(f"⚠️ Twitch-Stream-Info fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def get_game_id(name, chat_access_token):
    """Resolves a category/game name to Twitch's internal game id, for !game."""
    response = _helix_request(
        "GET", "https://api.twitch.tv/helix/games", chat_access_token, params={"name": name}
    )
    if response is not None and response.status_code == 200:
        data = response.json().get("data", [])
        return data[0]["id"] if data else None
    if response is not None:
        print(f"⚠️ Twitch-Game-Lookup fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def modify_channel(broadcaster_id, chat_access_token, title=None, game_id=None):
    """Changes title and/or category. Requires scope channel:manage:broadcast."""
    body = {}
    if title is not None:
        body["title"] = title
    if game_id is not None:
        body["game_id"] = game_id
    if not body:
        return False
    response = _helix_request(
        "PATCH", "https://api.twitch.tv/helix/channels", chat_access_token,
        params={"broadcaster_id": broadcaster_id}, json_body=body,
    )
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Channel-Update fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def create_clip(broadcaster_id, chat_access_token):
    """Creates a clip of the last ~30s and returns the public URL (or None, e.g. when the
    channel is offline). Requires scope clips:edit."""
    response = _helix_request(
        "POST", "https://api.twitch.tv/helix/clips", chat_access_token, params={"broadcaster_id": broadcaster_id}
    )
    if response is not None and response.status_code == 202:
        data = response.json().get("data", [])
        if data:
            return f"https://clips.twitch.tv/{data[0]['id']}"
    if response is not None:
        print(f"⚠️ Twitch-Clip-Erstellung fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def get_subscriber_count(broadcaster_id, chat_access_token):
    """Liefert (Gesamt-Abozahl, Sub-Punkte) oder (None, None) bei Fehler.
    Erfordert Scope channel:read:subscriptions."""
    response = _helix_request(
        "GET", "https://api.twitch.tv/helix/subscriptions", chat_access_token,
        params={"broadcaster_id": broadcaster_id, "first": 1},
    )
    if response is not None and response.status_code == 200:
        data = response.json()
        return data.get("total"), data.get("points")
    if response is not None:
        print(f"⚠️ Twitch-Abo-Abfrage fehlgeschlagen ({response.status_code}): {response.text}")
    return None, None


def get_bits_leaderboard(chat_access_token):
    """Returns the top bits cheerer (all-time) as {'user_name', 'score'} or None. Implicitly
    uses the token's broadcaster id - no broadcaster_id parameter. Requires scope bits:read."""
    response = _helix_request(
        "GET", "https://api.twitch.tv/helix/bits/leaderboard", chat_access_token,
        params={"count": 1, "period": "all"},
    )
    if response is not None and response.status_code == 200:
        data = response.json().get("data", [])
        return data[0] if data else None
    if response is not None:
        print(f"⚠️ Twitch-Bits-Leaderboard-Abfrage fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def get_hype_train_status(broadcaster_id, chat_access_token):
    """Returns the most recent hype train event, or None (including when none is active).
    Requires scope channel:read:hype_train."""
    response = _helix_request(
        "GET", "https://api.twitch.tv/helix/hypetrain/events", chat_access_token,
        params={"broadcaster_id": broadcaster_id, "first": 1},
    )
    if response is not None and response.status_code == 200:
        data = response.json().get("data", [])
        return data[0] if data else None
    if response is not None:
        print(f"⚠️ Twitch-Hype-Train-Abfrage fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def get_followage(broadcaster_id, user_id, chat_access_token):
    """Returns the ISO timestamp since which `user_id` has followed the channel, or None when
    the user does not follow. Requires scope moderator:read:followers."""
    response = _helix_request(
        "GET", "https://api.twitch.tv/helix/channels/followers", chat_access_token,
        params={"broadcaster_id": broadcaster_id, "user_id": user_id},
    )
    if response is not None and response.status_code == 200:
        data = response.json().get("data", [])
        return data[0]["followed_at"] if data else None
    if response is not None:
        print(f"⚠️ Twitch-Followage-Abfrage fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def send_shoutout(broadcaster_id, moderator_id, to_broadcaster_id, chat_access_token):
    """Gibt einem anderen Kanal ein Helix-Shoutout. Erfordert Scope
    moderator:manage:shoutouts. Unterliegt Twitchs eigenem Cooldown (2min/60min)."""
    params = {
        "from_broadcaster_id": broadcaster_id,
        "to_broadcaster_id": to_broadcaster_id,
        "moderator_id": moderator_id,
    }
    response = _helix_request("POST", "https://api.twitch.tv/helix/chat/shoutouts", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Shoutout fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def start_raid(broadcaster_id, to_broadcaster_id, chat_access_token):
    """Starts a raid to another channel. Requires scope channel:manage:raids."""
    params = {"from_broadcaster_id": broadcaster_id, "to_broadcaster_id": to_broadcaster_id}
    response = _helix_request("POST", "https://api.twitch.tv/helix/raids", chat_access_token, params=params)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Raid fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def create_eventsub_subscription(sub_type, version, condition, session_id, chat_access_token):
    """Registers an arbitrary EventSub subscription for a WebSocket session. The generic
    replacement for the former handful of type-specific subscribe_*_events functions - six
    subscription types (ad break, AutoMod hold, sub, cheer, follow, hype train progress) now
    follow the same pattern. Some types (e.g. channel.ad_break.begin) require the token to
    belong to the broadcaster themselves, not merely to a mod account."""
    body = {
        "type": sub_type,
        "version": version,
        "condition": condition,
        "transport": {"method": "websocket", "session_id": session_id},
    }
    response = _helix_request(
        "POST", "https://api.twitch.tv/helix/eventsub/subscriptions", chat_access_token, json_body=body
    )
    if response is not None and response.status_code == 202:
        return True
    if response is not None:
        print(f"⚠️ EventSub-Abo ({sub_type}) fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def create_poll(broadcaster_id, title, choices, duration_seconds, chat_access_token):
    """Starts a chat poll. Requires scope channel:manage:polls."""
    body = {
        "broadcaster_id": broadcaster_id,
        "title": title[:60],
        "choices": [{"title": c[:25]} for c in choices],
        "duration": duration_seconds,
    }
    response = _helix_request("POST", "https://api.twitch.tv/helix/polls", chat_access_token, json_body=body)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Poll-Erstellung fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def create_prediction(broadcaster_id, title, outcomes, prediction_window_seconds, chat_access_token):
    """Starts a prediction. Requires scope channel:manage:predictions."""
    body = {
        "broadcaster_id": broadcaster_id,
        "title": title[:45],
        "outcomes": [{"title": o[:25]} for o in outcomes],
        "prediction_window": prediction_window_seconds,
    }
    response = _helix_request("POST", "https://api.twitch.tv/helix/predictions", chat_access_token, json_body=body)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Prediction-Erstellung fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def add_channel_vip(broadcaster_id, user_id, chat_access_token):
    """Erfordert Scope channel:manage:vips."""
    params = {"broadcaster_id": broadcaster_id, "user_id": user_id}
    response = _helix_request("POST", "https://api.twitch.tv/helix/channels/vips", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Adding a Twitch VIP failed ({response.status_code}): {response.text}")
    return False


def remove_channel_vip(broadcaster_id, user_id, chat_access_token):
    """Erfordert Scope channel:manage:vips."""
    params = {"broadcaster_id": broadcaster_id, "user_id": user_id}
    response = _helix_request("DELETE", "https://api.twitch.tv/helix/channels/vips", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-VIP-Entfernen fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def add_channel_moderator(broadcaster_id, user_id, chat_access_token):
    """Erfordert Scope channel:manage:moderators."""
    params = {"broadcaster_id": broadcaster_id, "user_id": user_id}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/moderators", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Adding a Twitch mod failed ({response.status_code}): {response.text}")
    return False


def remove_channel_moderator(broadcaster_id, user_id, chat_access_token):
    """Erfordert Scope channel:manage:moderators."""
    params = {"broadcaster_id": broadcaster_id, "user_id": user_id}
    response = _helix_request("DELETE", "https://api.twitch.tv/helix/moderation/moderators", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Mod-Entfernen fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def resolve_automod_message(moderator_id, msg_id, action, chat_access_token):
    """Releases a message held back by AutoMod (action="ALLOW") or rejects it
    (action="DENY"). Requires scope moderator:manage:automod."""
    body = {"user_id": moderator_id, "msg_id": msg_id, "action": action}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/automod/message", chat_access_token, json_body=body)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ AutoMod-Freigabe/Ablehnung fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def update_chat_settings(broadcaster_id, moderator_id, chat_access_token, settings):
    """Changes chat settings (e.g. {'slow_mode': True, 'slow_mode_wait_time': 30}).
    Requires scope moderator:manage:chat_settings."""
    response = _helix_request(
        "PATCH", "https://api.twitch.tv/helix/chat/settings", chat_access_token,
        params={"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}, json_body=settings,
    )
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Chat-Settings fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def create_custom_reward(broadcaster_id, title, cost, chat_access_token):
    """Creates a channel points reward (for !giveaway) and returns the reward object (among
    others 'id'), or None on error. Limited to one redemption per stream so that nobody can
    enter the same giveaway several times. Requires scope channel:manage:redemptions."""
    body = {
        "title": title[:45],
        "cost": cost,
        "is_max_per_user_per_stream_enabled": True,
        "max_per_user_per_stream": 1,
    }
    response = _helix_request(
        "POST", "https://api.twitch.tv/helix/channel_points/custom_rewards", chat_access_token,
        params={"broadcaster_id": broadcaster_id}, json_body=body,
    )
    if response is not None and response.status_code == 200:
        data = response.json().get("data", [])
        return data[0] if data else None
    if response is not None:
        print(f"⚠️ Twitch-Reward-Erstellung fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def delete_custom_reward(broadcaster_id, reward_id, chat_access_token):
    """Deletes a reward previously created via create_custom_reward (only rewards created by
    your own app can be removed this way). Requires scope channel:manage:redemptions."""
    response = _helix_request(
        "DELETE", "https://api.twitch.tv/helix/channel_points/custom_rewards", chat_access_token,
        params={"broadcaster_id": broadcaster_id, "id": reward_id},
    )
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Deleting a Twitch reward failed ({response.status_code}): {response.text}")
    return False


def update_redemption_status(broadcaster_id, reward_id, redemption_id, status, chat_access_token):
    """Sets a redemption to FULFILLED (winner) or CANCELED (the points are automatically
    refunded to the participant in the process). Requires scope channel:manage:redemptions."""
    response = _helix_request(
        "PATCH", "https://api.twitch.tv/helix/channel_points/custom_rewards/redemptions", chat_access_token,
        params={"broadcaster_id": broadcaster_id, "reward_id": reward_id, "id": redemption_id},
        json_body={"status": status},
    )
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Redemption-Update fehlgeschlagen ({response.status_code}): {response.text}")
    return False
