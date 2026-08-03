# platform.py
# Twitch als Platform-Implementierung (Vertrag: core/platform.py).
#
# Bewusst dünn: der eigentliche IRC-/EventSub-Code bleibt in bot.py, hier steht nur, wie
# Twitch den Vertrag erfüllt - hochfahren, herunterfahren, Text schreiben, ankündigen.

from core import platform as platform_api

from . import bot


class TwitchPlatform(platform_api.Platform):
    name = "twitch"
    capabilities = frozenset({
        platform_api.CHAT,       # send_twitch_chat schreibt in den eigenen Kanal
        platform_api.ANNOUNCE,   # optional, siehe announce() unten
        platform_api.STREAM,     # meldet stream.online/offline über den Event-Bus
        platform_api.MODERATE,   # eigene Moderation in _handle_privmsg
    })

    async def start(self):
        # Kehrt zurück, sobald die Verbindung steht - IRC-Reader, EventSub-Listener,
        # Token-Wächter und Zuschauer-Sampling laufen danach als eigene Tasks weiter.
        await bot.start_twitch_bot()

    async def close(self):
        await bot.close()

    async def send_text(self, text):
        return await bot.send_twitch_chat(text)

    async def announce(self, announcement):
        """Spiegelt Ankündigungen in den Twitch-Chat - aber nur die Arten, die in
        twitch.json unter "announce_kinds" stehen. Default ist leer, und das mit Absicht:
        !bug und !clip antworten dem Chat bereits direkt, eine zweite Meldung wäre nur
        Rauschen. Wer z.B. Bug-Reports aus Discord auch im Stream sehen will, trägt
        "announce_kinds": ["bug.report"] ein."""
        if announcement.kind not in bot.TWITCH_CONFIG.get("announce_kinds", []):
            return False
        # Eine IRC-Zeile darf nur ~500 Zeichen lang sein, daher höchstens drei Detailfelder.
        return await bot.send_twitch_chat(announcement.as_text(max_fields=3))


def create_platform():
    return TwitchPlatform()
