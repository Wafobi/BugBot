"""Alles über Twitch-OAuth-Scopes an einer Stelle.

Bewusst getrennt von config.py: das sind keine Einstellungen, die man pro Deployment
anders setzt, sondern eine Eigenschaft des Codes selbst - welche Scopes REQUIRED enthält,
ergibt sich direkt daraus, welche Helix-Endpunkte api.py aufruft und welche EventSub-Typen
bot.py abonniert. config.py liest Umgebung, dieses Modul beschreibt die Integration.

Das Modul hat absichtlich keine Importe: so kann sowohl der Bot als auch das
eigenständige get_token.py es ziehen, ohne websockets & Co. zu laden.
"""

# Die Scopes, die der Chat-Token braucht. get_token.py fordert genau diese Liste
# beim OAuth-Flow an, und bot.log_token_capabilities prüft den Token beim Start dagegen.
# Kommt eine Funktion dazu, die einen neuen Scope braucht: hier eintragen, in
# CAPABILITIES eine Klartext-Zeile ergänzen und get_token.py einmal neu laufen
# lassen (Twitch erweitert bestehende Tokens nicht nachträglich).
# Bewusst knapp gehalten - der Token liegt auf einem Server, jeder ungenutzte Scope ist
# nur zusätzlicher Schaden, falls er abhandenkommt.
REQUIRED = [
    # --- Chat (IRC) ---
    "chat:read",                        # bot.py: IRC-Verbindung
    "chat:edit",                        # IRC PRIVMSG
    # --- Moderation ---
    "moderator:manage:chat_messages",   # api.delete_chat_message
    "moderator:manage:banned_users",    # api.timeout_user / ban_user / unban_user
    "moderator:manage:warnings",        # api.warn_user
    "moderator:manage:automod",         # api.resolve_automod_message
    "moderator:manage:chat_settings",   # api.update_chat_settings
    "moderator:manage:shoutouts",       # api.send_shoutout
    # --- Kanal-Verwaltung ---
    "channel:manage:broadcast",         # api.modify_channel (Titel/Kategorie)
    "channel:manage:raids",             # api.start_raid
    "channel:manage:polls",             # api.create_poll + EventSub channel.poll.end
    "channel:manage:predictions",       # api.create_prediction + channel.prediction.end
    "channel:manage:vips",              # api.add_channel_vip / remove_channel_vip
    "channel:manage:moderators",        # api.add_channel_moderator / remove_...
    "channel:manage:redemptions",       # api.create/delete_custom_reward,
                                        # update_redemption_status + Redemption-EventSub
    "clips:edit",                       # api.create_clip
    # --- Lesend / EventSub ---
    "moderator:read:chatters",          # api.get_chatters_count
    "moderator:read:followers",         # api.get_followage + EventSub channel.follow
    "channel:read:subscriptions",       # api.get_subscriber_count + channel.subscribe*
    "bits:read",                        # api.get_bits_leaderboard + channel.cheer
    "channel:read:hype_train",          # api.get_hype_train_status + hype_train-Events
    "channel:read:ads",                 # EventSub channel.ad_break.begin
    "channel:read:goals",               # EventSub channel.goal.end
    "channel:moderate",                 # EventSub channel.ban / channel.unban
]

# Scope -> Klartext-Beschreibung, nur für die Startup-Diagnose (log_token_capabilities).
# Deckt über REQUIRED hinaus auch Scopes ab, die für geplante Stream-Steuerungs-Features
# relevant wären - ein Token aus einem Generator bringt oft viele davon mit.
CAPABILITIES = {
    "chat:read": "Chat lesen",
    "chat:edit": "Chat schreiben (IRC PRIVMSG)",
    "user:write:chat": "Chat schreiben (Helix)",
    "moderator:manage:chat_messages": "Nachrichten löschen",
    "moderator:manage:banned_users": "Timeouts/Bans",
    "moderator:manage:announcements": "Announcements posten",
    "moderator:manage:automod": "AutoMod-Warteschlange verwalten",
    "moderator:manage:automod_settings": "AutoMod-Einstellungen ändern",
    "moderator:manage:blocked_terms": "Bannwort-Liste (Twitch-seitig) verwalten",
    "moderator:manage:chat_settings": "Chat-Einstellungen (Slow/Follower/Sub-Mode) ändern",
    "moderator:manage:shield_mode": "Shield Mode auslösen",
    "moderator:manage:shoutouts": "Shoutouts geben",
    "moderator:manage:unban_requests": "Unban-Anfragen bearbeiten",
    "moderator:manage:warnings": "Verwarnungen aussprechen",
    "channel:manage:broadcast": "Titel/Kategorie ändern",
    "channel:manage:polls": "Umfragen steuern",
    "channel:manage:predictions": "Predictions steuern",
    "channel:manage:raids": "Raids starten",
    "channel:manage:redemptions": "Channel-Point-Rewards verwalten",
    "channel:manage:schedule": "Stream-Zeitplan verwalten",
    "channel:manage:moderators": "Moderatoren hinzufügen/entfernen",
    "channel:manage:vips": "VIPs verwalten",
    "channel:manage:videos": "VODs löschen/bearbeiten",
    "channel:manage:extensions": "Extensions verwalten",
    "channel:manage:ads": "Werbeblöcke starten",
    "channel:read:ads": "Werbeplan lesen / Ad-Break-Benachrichtigungen empfangen",
    "channel:edit:commercial": "Werbeblöcke starten (legacy)",
    "clips:edit": "Clips erstellen",
    "moderator:read:chatters": "Chatter-Anzahl abfragen",
    "channel:read:subscriptions": "Abonnentenzahl abfragen / Sub-Highscore erfassen",
    "channel:read:hype_train": "Hype-Train-Status abfragen / Hype-Train-Highscore erfassen",
    "bits:read": "Bits-Bestenliste abfragen / Bits-Highscore erfassen",
    "moderator:read:followers": "Followage abfragen / Follow-Highscore erfassen",
    "channel:read:goals": "Ziel-Events empfangen (channel.goal.end)",
    "channel:moderate": "Ban-/Unban-Events empfangen (channel.ban / channel.unban)",
}

# Scopes, die keinen Nutzen für den Bot haben, aber besonders hohes Schadenspotenzial
# bergen, falls der Token geleakt wird - werden beim Start explizit als Warnung geloggt.
DANGEROUS_UNNEEDED = {
    "channel:read:stream_key": "RTMP-Stream-Key - erlaubt, unter deinem Namen zu streamen",
}
