# twitch_api.py
# Helix-API-Aufrufe für Moderationsaktionen. Twitch hat Moderationsbefehle über
# IRC (PRIVMSG "/timeout", "/delete", ...) im Februar 2023 abgeschaltet - Delete/
# Timeout laufen seitdem ausschließlich über die Helix-Endpunkte.
# https://dev.twitch.tv/docs/chat/irc-migration/

import requests
from dotenv import set_key, find_dotenv
from . import config

ENV_PATH = find_dotenv()


_refresh_unavailable_logged = False


def refresh_chat_token():
    """Holt per Refresh-Token einen neuen Twitch-Chat-Access-Token und persistiert ihn in .env,
    da Twitch bei jedem Refresh auch einen neuen Refresh-Token ausgibt. Lebt hier (statt in
    twitch_bot.py), damit auch die Helix-Aufrufe unten bei einem 401 selbst refreshen können,
    ohne von den IRC-spezifischen Teilen von twitch_bot.py abzuhängen.

    Twitch verlangt beim Refresh-Grant das client_secret der ausstellenden App - ohne das
    antwortet der Endpunkt mit 400 "missing client secret". Fehlt es (Token stammt aus einer
    fremden App, deren Secret wir nicht haben), sparen wir uns den Request und sagen es
    einmal deutlich, statt es bei jedem Aufruf des Token-Wächters erneut zu loggen."""
    global _refresh_unavailable_logged
    if not config.TWITCH_CHAT_CLIENT_SECRET:
        if not _refresh_unavailable_logged:
            _refresh_unavailable_logged = True
            print(
                f"⚠️ Twitch-Chat-Token kann nicht erneuert werden: kein Client-Secret zur "
                f"Client-ID {config.TWITCH_CHAT_CLIENT_ID} bekannt. Entweder "
                f"TWITCH_CHAT_CLIENT_SECRET in .env setzen oder den Token mit der eigenen App "
                f"(TWITCH_CLIENT_ID) neu erzeugen."
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
    """Gemeinsamer Helix-Request mit automatischem Refresh+Retry bei 401 - Basis für
    alle Aufrufe unten, damit dieses Verhalten nicht in jeder Funktion dupliziert wird.
    Gibt bei Netzwerkfehlern None zurück (Aufrufer müssen das prüfen)."""
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
    """Löst die numerische User-ID des Kanals über einen App-Access-Token auf."""
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
        print(f"⚠️ Konnte Broadcaster-ID nicht auflösen: {e}")
        return None


def validate_token_info(chat_access_token):
    """Rohe Antwort von https://id.twitch.tv/oauth2/validate - u.a. "scopes" und
    "expires_in" (Restlaufzeit in Sekunden, Basis für den Token-Wächter in
    twitch_bot.py). None bei Fehler/abgelaufenem Token."""
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
    """Fragt Twitch, welche Scopes der aktuelle Chat-Token tatsächlich hat
    (für die Startup-Diagnose in twitch_bot.py). None bei Fehler/abgelaufenem Token."""
    info = validate_token_info(chat_access_token)
    return info.get("scopes", []) if info is not None else None


def get_users(logins, chat_access_token):
    """Löst bis zu 100 Logins gleichzeitig zu User-Objekten (id, login, display_name) auf.
    `logins=None` liefert stattdessen den Owner des Tokens selbst zurück."""
    params = [("login", login.lstrip("@").lower()) for login in logins] if logins else None
    response = _helix_request("GET", "https://api.twitch.tv/helix/users", chat_access_token, params=params)
    if response is not None and response.status_code == 200:
        return response.json().get("data", [])
    if response is not None:
        print(f"⚠️ Twitch-User-Lookup fehlgeschlagen ({response.status_code}): {response.text}")
    return []


def get_moderator_id(chat_access_token):
    """Löst die User-ID des Bot-/Mod-Accounts über dessen eigenen Chat-Token auf."""
    users = get_users(None, chat_access_token)
    return users[0]["id"] if users else None


def delete_chat_message(broadcaster_id, moderator_id, message_id, chat_access_token):
    """Löscht eine einzelne Chat-Nachricht. Erfordert Scope moderator:manage:chat_messages."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "message_id": message_id}
    response = _helix_request("DELETE", "https://api.twitch.tv/helix/moderation/chat", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Delete fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def timeout_user(broadcaster_id, moderator_id, user_id, duration, reason, chat_access_token):
    """Timeoutet einen User für `duration` Sekunden. Erfordert Scope moderator:manage:banned_users."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
    body = {"data": {"user_id": user_id, "duration": duration, "reason": reason[:500]}}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/bans", chat_access_token, params=params, json_body=body)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Timeout fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def ban_user(broadcaster_id, moderator_id, user_id, reason, chat_access_token):
    """Bannt einen User dauerhaft (kein duration-Feld = permanent statt Timeout).
    Erfordert Scope moderator:manage:banned_users."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
    body = {"data": {"user_id": user_id, "reason": reason[:500]}}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/bans", chat_access_token, params=params, json_body=body)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Ban fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def unban_user(broadcaster_id, moderator_id, user_id, chat_access_token):
    """Hebt einen Ban oder Timeout auf. Erfordert Scope moderator:manage:banned_users."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "user_id": user_id}
    response = _helix_request("DELETE", "https://api.twitch.tv/helix/moderation/bans", chat_access_token, params=params)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Unban fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def warn_user(broadcaster_id, moderator_id, user_id, reason, chat_access_token):
    """Spricht eine Twitch-seitige Verwarnung aus (User sieht ein Warnbanner beim
    nächsten Chat-Versuch). Erfordert Scope moderator:manage:warnings."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id}
    body = {"data": {"user_id": user_id, "reason": reason[:500]}}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/warnings", chat_access_token, params=params, json_body=body)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Warnung fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def get_chatters_count(broadcaster_id, moderator_id, chat_access_token):
    """Liefert die Anzahl aktuell im Chat anwesender User (Helix-Feld "total"),
    oder None bei Fehler. Erfordert Scope moderator:read:chatters."""
    params = {"broadcaster_id": broadcaster_id, "moderator_id": moderator_id, "first": 1}
    response = _helix_request("GET", "https://api.twitch.tv/helix/chat/chatters", chat_access_token, params=params)
    if response is not None and response.status_code == 200:
        return response.json().get("total")
    if response is not None:
        print(f"⚠️ Twitch-Chatter-Abfrage fehlgeschlagen ({response.status_code}): {response.text}")
    return None


def get_stream_info(broadcaster_id, chat_access_token):
    """Liefert die aktuellen Stream-Daten (u.a. started_at, title, game_name) oder
    None, wenn der Kanal offline ist. Basis für !uptime."""
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
    """Löst einen Kategorie-/Spielnamen zur Twitch-internen Game-ID auf, für !game."""
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
    """Ändert Titel und/oder Kategorie. Erfordert Scope channel:manage:broadcast."""
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
    """Erstellt einen Clip der letzten ~30s und gibt die öffentliche URL zurück
    (oder None, z.B. wenn der Kanal offline ist). Erfordert Scope clips:edit."""
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
    """Liefert den Top-Bits-Cheerer (all-time) als {'user_name', 'score'} oder None.
    Nutzt implizit die Broadcaster-ID des Tokens - kein broadcaster_id-Parameter.
    Erfordert Scope bits:read."""
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
    """Liefert das aktuellste Hype-Train-Event oder None (auch, wenn keins aktiv ist).
    Erfordert Scope channel:read:hype_train."""
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
    """Gibt den ISO-Zeitstempel zurück, seit dem `user_id` dem Kanal folgt, oder
    None, wenn der User nicht folgt. Erfordert Scope moderator:read:followers."""
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
    """Startet einen Raid zu einem anderen Kanal. Erfordert Scope channel:manage:raids."""
    params = {"from_broadcaster_id": broadcaster_id, "to_broadcaster_id": to_broadcaster_id}
    response = _helix_request("POST", "https://api.twitch.tv/helix/raids", chat_access_token, params=params)
    if response is not None and response.status_code == 200:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Raid fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def create_eventsub_subscription(sub_type, version, condition, session_id, chat_access_token):
    """Registriert ein beliebiges EventSub-Abo für eine WebSocket-Session. Generischer
    Ersatz für die frühere Handvoll type-spezifischer subscribe_*_events-Funktionen -
    inzwischen folgen sechs Abo-Typen (Ad-Break, AutoMod-Hold, Sub, Cheer, Follow,
    Hype-Train-Progress) demselben Muster. Manche Typen (z.B. channel.ad_break.begin)
    erfordern, dass der Token dem Broadcaster selbst gehört, nicht nur einem Mod-Account."""
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
    """Startet eine Chat-Umfrage. Erfordert Scope channel:manage:polls."""
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
    """Startet eine Prediction. Erfordert Scope channel:manage:predictions."""
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
        print(f"⚠️ Twitch-VIP-Hinzufügen fehlgeschlagen ({response.status_code}): {response.text}")
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
        print(f"⚠️ Twitch-Mod-Hinzufügen fehlgeschlagen ({response.status_code}): {response.text}")
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
    """Gibt eine von AutoMod zurückgehaltene Nachricht frei (action="ALLOW") oder
    lehnt sie ab (action="DENY"). Erfordert Scope moderator:manage:automod."""
    body = {"user_id": moderator_id, "msg_id": msg_id, "action": action}
    response = _helix_request("POST", "https://api.twitch.tv/helix/moderation/automod/message", chat_access_token, json_body=body)
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ AutoMod-Freigabe/Ablehnung fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def update_chat_settings(broadcaster_id, moderator_id, chat_access_token, settings):
    """Ändert Chat-Einstellungen (z.B. {'slow_mode': True, 'slow_mode_wait_time': 30}).
    Erfordert Scope moderator:manage:chat_settings."""
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
    """Legt einen Channel-Points-Reward an (für !giveaway) und gibt das Reward-Objekt
    (u.a. 'id') zurück, oder None bei Fehler. Auf ein Redemption/Stream begrenzt, damit
    niemand mehrfach an derselben Verlosung teilnehmen kann. Erfordert Scope
    channel:manage:redemptions."""
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
    """Löscht einen zuvor per create_custom_reward angelegten Reward wieder (nur
    Rewards, die die eigene App erstellt hat, lassen sich so entfernen). Erfordert
    Scope channel:manage:redemptions."""
    response = _helix_request(
        "DELETE", "https://api.twitch.tv/helix/channel_points/custom_rewards", chat_access_token,
        params={"broadcaster_id": broadcaster_id, "id": reward_id},
    )
    if response is not None and response.status_code == 204:
        return True
    if response is not None:
        print(f"⚠️ Twitch-Reward-Löschung fehlgeschlagen ({response.status_code}): {response.text}")
    return False


def update_redemption_status(broadcaster_id, reward_id, redemption_id, status, chat_access_token):
    """Setzt eine Redemption auf FULFILLED (Gewinner) oder CANCELED (Punkte werden
    dabei automatisch an den Teilnehmer zurückerstattet). Erfordert Scope
    channel:manage:redemptions."""
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
