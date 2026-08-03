# platform.py
# Discord als Platform-Implementierung (Vertrag: core/platform.py).
#
# Wie bei Twitch nur die Hülle: die Bot-Logik (Moderation, Befehle, Rollen-Reaktionen)
# bleibt in bot.py. Discord meldet bewusst kein CHAT an - es hat keinen einzelnen
# "Hauptkanal", in den freier Text gehören würde; alles Eingehende läuft über
# announce() und die dort hinterlegte Kanalzuordnung.

from core import platform as platform_api

from . import bot
from . import config


class DiscordPlatform(platform_api.Platform):
    name = "discord"
    capabilities = frozenset({
        platform_api.ANNOUNCE,   # Embeds in den zur kind passenden Kanal
        platform_api.MODERATE,   # eigene Moderation in on_message
    })

    async def start(self):
        # Blockiert für die gesamte Laufzeit. Der Kontextmanager sorgt dafür, dass die
        # Session auch dann geschlossen wird, wenn start() von außen abgebrochen wird.
        async with bot.bot:
            await bot.bot.start(config.TOKEN)

    async def close(self):
        await bot.bot.close()

    async def wait_ready(self):
        # Vor on_ready kennt der Client seine Server nicht - Ankündigungen würden dann
        # stillschweigend ins Leere laufen (siehe core/platform.py:Platform.wait_ready).
        await bot.bot.wait_until_ready()

    async def announce(self, announcement):
        return await bot.post_announcement(announcement)


def create_platform():
    return DiscordPlatform()
