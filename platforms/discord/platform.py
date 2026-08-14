# platform.py
# Discord as a Platform implementation (contract: core/platform.py).
#
# As with Twitch, only the shell: the bot logic (moderation, commands, reaction roles) stays
# in bot.py. Discord deliberately declares no CHAT - it has no single "main channel" free
# text would belong in; everything incoming runs through announce() and the channel mapping
# stored there.

from core import platform as platform_api

from . import bot
from . import config


class DiscordPlatform(platform_api.Platform):
    name = "discord"
    capabilities = frozenset({
        platform_api.ANNOUNCE,   # embeds into the channel matching the kind
        platform_api.MODERATE,   # its own moderation in on_message
    })

    async def start(self):
        # Blocks for its entire lifetime. The context manager makes sure the session is
        # closed even when start() is cancelled from outside.
        async with bot.bot:
            await bot.bot.start(config.TOKEN)

    async def close(self):
        await bot.bot.close()

    async def wait_ready(self):
        # Before on_ready the client does not know its guilds - announcements would then run
        # silently into nothing (see core/platform.py:Platform.wait_ready).
        await bot.bot.wait_until_ready()

    async def announce(self, announcement):
        return await bot.post_announcement(announcement)


def create_platform():
    return DiscordPlatform()
