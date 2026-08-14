"""Twitch configuration from the environment/.env.

Lives here instead of in core/config.py: core is the platform-neutral base (moderation,
stats, runtime_config) that Discord and Twitch share - it should know nothing about Twitch.
Whoever needs Twitch values fetches them from this module.

Important: TWITCH_CHAT_ACCESS_TOKEN and TWITCH_CHAT_REFRESH_TOKEN are re-set at runtime by
api.refresh_chat_token. So always read them as `config.TWITCH_CHAT_ACCESS_TOKEN` and never
copy them into a variable of your own at import time - otherwise you are holding a dead token
after the first refresh.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from core import runtime_config

# Idempotent and independent of import order - core/config.py loads the same file, but
# load_dotenv does not overwrite variables that are already set.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

TWITCH_CHANNEL = os.environ["TWITCH_CHANNEL"]

# Your own app from https://dev.twitch.tv/console/apps - provides app access tokens
# (api.get_app_access_token) and is the app that should issue the chat token.
TWITCH_CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
TWITCH_CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]

# The user token the bot writes into the chat and moderates with.
TWITCH_CHAT_ACCESS_TOKEN = os.environ["TWITCH_CHAT_ACCESS_TOKEN"]
TWITCH_CHAT_REFRESH_TOKEN = os.environ["TWITCH_CHAT_REFRESH_TOKEN"]
TWITCH_CHAT_CLIENT_ID = os.environ["TWITCH_CHAT_CLIENT_ID"]

# The chat token need not come from your own app (TWITCH_CLIENT_ID) - generating it through
# a token generator gets you a token of that generator's app. A refresh, however, demands the
# secret of exactly the app that issued the token. If the client ids match, that is
# TWITCH_CLIENT_SECRET; otherwise it has to be set explicitly. Empty = we cannot renew this
# token (see api.refresh_chat_token).
TWITCH_CHAT_CLIENT_SECRET = os.environ.get("TWITCH_CHAT_CLIENT_SECRET") or (
    TWITCH_CLIENT_SECRET if TWITCH_CHAT_CLIENT_ID == TWITCH_CLIENT_ID else ""
)


# --- Adjustable things ---------------------------------------------------------------
# Credentials come from the .env (above), everything adjustable from twitch.json: texts,
# timings, colours, command names, rules, static commands, moderation values. The file is
# re-read on change (see core/runtime_config.py) - no restart needed.
#
# It lives here and not in bot.py so that commands.py can reach it too: bot.py imports
# commands.py, and the other way round would be a circular import. config.py imports nothing
# from its own package and is therefore the place everyone may reach.
TWITCH_CONFIG = runtime_config.LiveConfig(Path(__file__).parent / "twitch.json")


def text(key, **values):
    """Shorthand for TWITCH_CONFIG.text - appears close to a hundred times across bot.py and
    commands.py."""
    return TWITCH_CONFIG.text(key, **values)
