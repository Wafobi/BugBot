# platform.py
# Twitch as a Platform implementation (contract: core/platform.py).
#
# Deliberately thin: the actual IRC/EventSub code stays in bot.py; what stands here is only
# how Twitch fulfils the contract - start up, shut down, write text, announce.

from core import platform as platform_api

from . import bot


class TwitchPlatform(platform_api.Platform):
    name = "twitch"
    capabilities = frozenset({
        platform_api.CHAT,       # send_twitch_chat writes into our own channel
        platform_api.ANNOUNCE,   # optional, see announce() below
        platform_api.STREAM,     # reports stream.online/offline over the event bus
        platform_api.MODERATE,   # its own moderation in _handle_privmsg
    })

    async def start(self):
        # Returns as soon as the connection stands - IRC reader, EventSub listener, token
        # watchdog and viewer sampling keep running as tasks of their own afterwards.
        await bot.start_twitch_bot()

    async def close(self):
        await bot.close()

    async def send_text(self, text):
        return await bot.send_twitch_chat(text)

    async def announce(self, announcement):
        """Mirrors announcements into the Twitch chat - but only the kinds listed in
        twitch.json under "announce_kinds". The default is empty, and deliberately so: !bug
        and !clip already answer the chat directly, and a second notice would be noise alone.
        Anyone wanting to see, say, bug reports from Discord on stream too enters
        "announce_kinds": ["bug.report"]."""
        if announcement.kind not in bot.TWITCH_CONFIG.get("announce_kinds", []):
            return False
        # An IRC line may only be about 500 characters long, hence at most three detail fields.
        return await bot.send_twitch_chat(announcement.as_text(max_fields=3))


def create_platform():
    return TwitchPlatform()
