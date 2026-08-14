"""Everything about Twitch OAuth scopes in one place.

Deliberately separate from config.py: these are not settings you set differently per
deployment, but a property of the code itself - what REQUIRED contains follows directly from
which Helix endpoints api.py calls and which EventSub types bot.py subscribes to. config.py
reads the environment; this module describes the integration.

The module deliberately has no imports: that way both the bot and the standalone get_token.py
can pull it in without loading websockets and friends.
"""

# The scopes the chat token needs. get_token.py requests exactly this list during the OAuth
# flow, and bot.log_token_capabilities checks the token against it at startup. If a function
# is added that needs a new scope: enter it here, add a plain-text line to CAPABILITIES and
# run get_token.py once more (Twitch does not extend existing tokens after the fact).
# Deliberately kept short - the token sits on a server, and every unused scope is only
# additional damage should it go astray.
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

# Scope -> plain-text description, only for the startup diagnostics
# (log_token_capabilities). Beyond REQUIRED it also covers scopes that would be relevant for
# planned stream-control features - a token from a generator often brings many of them.
CAPABILITIES = {
    "chat:read": "Chat lesen",
    "chat:edit": "Chat schreiben (IRC PRIVMSG)",
    "user:write:chat": "Chat schreiben (Helix)",
    "moderator:manage:chat_messages": "delete messages",
    "moderator:manage:banned_users": "Timeouts/Bans",
    "moderator:manage:announcements": "Announcements posten",
    "moderator:manage:automod": "AutoMod-Warteschlange verwalten",
    "moderator:manage:automod_settings": "change AutoMod settings",
    "moderator:manage:blocked_terms": "Bannwort-Liste (Twitch-seitig) verwalten",
    "moderator:manage:chat_settings": "change chat settings (slow/follower/sub mode)",
    "moderator:manage:shield_mode": "trigger shield mode",
    "moderator:manage:shoutouts": "Shoutouts geben",
    "moderator:manage:unban_requests": "Unban-Anfragen bearbeiten",
    "moderator:manage:warnings": "Verwarnungen aussprechen",
    "channel:manage:broadcast": "change title/category",
    "channel:manage:polls": "Umfragen steuern",
    "channel:manage:predictions": "Predictions steuern",
    "channel:manage:raids": "Raids starten",
    "channel:manage:redemptions": "Channel-Point-Rewards verwalten",
    "channel:manage:schedule": "Stream-Zeitplan verwalten",
    "channel:manage:moderators": "add/remove moderators",
    "channel:manage:vips": "VIPs verwalten",
    "channel:manage:videos": "delete/edit VODs",
    "channel:manage:extensions": "Extensions verwalten",
    "channel:manage:ads": "start ad breaks",
    "channel:read:ads": "Werbeplan lesen / Ad-Break-Benachrichtigungen empfangen",
    "channel:edit:commercial": "start ad breaks (legacy)",
    "clips:edit": "Clips erstellen",
    "moderator:read:chatters": "Chatter-Anzahl abfragen",
    "channel:read:subscriptions": "Abonnentenzahl abfragen / Sub-Highscore erfassen",
    "channel:read:hype_train": "Hype-Train-Status abfragen / Hype-Train-Highscore erfassen",
    "bits:read": "Bits-Bestenliste abfragen / Bits-Highscore erfassen",
    "moderator:read:followers": "Followage abfragen / Follow-Highscore erfassen",
    "channel:read:goals": "Ziel-Events empfangen (channel.goal.end)",
    "channel:moderate": "Ban-/Unban-Events empfangen (channel.ban / channel.unban)",
}

# Scopes of no use to the bot but carrying particularly high damage potential should the
# token leak - explicitly logged as a warning at startup.
DANGEROUS_UNNEEDED = {
    "channel:read:stream_key": "RTMP-Stream-Key - erlaubt, unter deinem Namen zu streamen",
}
