# platform.py
# OBS als Platform-Implementierung (Vertrag: core/platform.py).
#
# Wie bei Twitch und Discord nur die Hülle - die Logik steht in bot.py, das Protokoll in
# link.py.
#
# Angemeldet ist allein ANNOUNCE: OBS hat keinen Chat (kein CHAT), moderiert nichts (kein
# MODERATE) und meldet bewusst keinen Stream-Beginn (kein STREAM) - die Stream-Session
# gehört Twitch, zwei Melder wären zwei Sessions für denselben Stream. Was OBS dazu weiß,
# geht als gewöhnliches PLATFORM_EVENT auf den Bus (siehe bot.py).

from core import platform as platform_api

from . import bot


class OBSPlatform(platform_api.Platform):
    name = "obs"
    capabilities = frozenset({
        platform_api.ANNOUNCE,   # Text-Quelle im Bild, siehe announce() unten
    })

    async def start(self):
        # Kehrt zurück, sobald der Port offen ist. Ob je ein Relais anruft, entscheidet
        # der Streaming-PC.
        await bot.start_obs()

    async def close(self):
        await bot.close()

    # wait_ready() bleibt absichtlich beim Default (sofort bereit): OBS läuft meist noch
    # gar nicht, wenn der Bot startet. Würde hier auf die Verbindung gewartet, hinge der
    # Live-Abgleich von Twitch an einem ausgeschalteten Rechner (siehe bot.start_obs).

    async def announce(self, announcement):
        """Blendet die Ankündigung als Text im Stream ein - aber nur die Arten, die in
        obs.json unter announce.kinds stehen. Default ist leer: was der Chat ohnehin
        sieht, muss nicht zusätzlich ins Bild."""
        return await bot.show_announcement(announcement)


def create_platform():
    return OBSPlatform()
