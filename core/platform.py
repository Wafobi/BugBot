# platform.py
# Die Platform-API - der Vertrag zwischen bugbot.py und den einzelnen Plattformen.
#
# Konvention: jede Plattform lebt in einem eigenen Paket platforms/<name>/ und stellt in
# platforms/<name>/platform.py eine Funktion create_platform() bereit, die eine
# Unterklasse von Platform zurückgibt (siehe core/registry.py). bugbot.py kennt danach
# weder Discord noch Twitch namentlich - es findet, startet und stoppt nur noch
# Platform-Objekte.
#
# Warum das so gebaut ist: vorher rief der Twitch-Bot direkt platforms/discord/bot.py auf
# (Bug-Reports, Clips, Live-Ankündigung) und der Discord-Bot direkt platforms/twitch/bot.py
# bzw. api.py (Live-Status, Zuschauer-Sampling). Beide Richtungen ließen sich nur mit verzögerten
# Imports *innerhalb* der Funktionen bauen, weil ein Import auf Modulebene ein
# Zirkelimport gewesen wäre. Alles Plattformübergreifende läuft jetzt über
# core/events.py, und die beiden Pakete kennen einander nicht mehr.

from abc import ABC, abstractmethod
from dataclasses import dataclass


# --- Fähigkeiten -------------------------------------------------------------------
# Was eine Plattform kann. core fragt danach, statt Plattformen am Namen zu erkennen -
# eine neue Plattform muss also nicht alles können, sondern nur angeben, was sie kann.
CHAT = "chat"          # kann freien Text in ihren Hauptkanal schreiben (send_text)
ANNOUNCE = "announce"  # kann strukturierte Ankündigungen posten (announce)
STREAM = "stream"      # meldet Stream-Beginn/-Ende (siehe STREAM_ONLINE/STREAM_OFFLINE)
MODERATE = "moderate"  # moderiert eingehende Nachrichten selbst (core.moderation)

#: Alle Fähigkeiten - damit "ist das eine Fähigkeit oder ein Plattformname?" (siehe
#: EventBus.resolve_platforms) an einer Stelle beantwortet wird und nicht an dreien.
CAPABILITIES = frozenset({CHAT, ANNOUNCE, STREAM, MODERATE})


# --- Ankündigungs-Arten ------------------------------------------------------------
# Der `kind` einer Announcement. Absender und Empfänger einigen sich allein auf diese
# Strings; wie eine Plattform sie darstellt - Discord-Embed, Twitch-Chatzeile oder gar
# nicht - ist ihre eigene Entscheidung. Dieselben Strings sind auch die Topics, unter
# denen EventBus.announce publiziert (siehe core/events.py).
STREAM_ONLINE = "stream.online"
STREAM_OFFLINE = "stream.offline"
BUG_REPORT = "bug.report"
CLIP = "clip.created"
STATUS = "status"


@dataclass(frozen=True)
class Field:
    """Ein benanntes Detail einer Announcement - auf Discord ein Embed-Field, auf rein
    textbasierten Plattformen ein "Name: Wert"-Abschnitt (siehe Announcement.as_text)."""
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True)
class Announcement:
    """Eine plattformneutrale Ankündigung: "das ist passiert, postet es, wo ihr könnt".

    Bewusst nur so viel Struktur, wie sich überall darstellen lässt - Titel, Text, Link,
    Bild, ein paar benannte Felder. Alles Discord-Spezifische (Embed, Kanalwahl,
    @everyone) entsteht erst im Discord-Renderer, alles Twitch-Spezifische in as_text.

    highlight  - die Ankündigung ist wichtig genug für eine Benachrichtigung (Discord
                 macht daraus ein @everyone). Nur für echte Ereignisse wie Streamstart.
    log        - zusätzlich ins Mod-Protokoll der Plattform, sofern sie eines führt.
    source     - Name der auslösenden Plattform ("twitch"), author der auslösende User.
    """
    kind: str
    title: str
    text: str = ""
    url: str = ""
    image_url: str = ""
    color: int = 0x3498DB
    source: str = ""
    author: str = ""
    highlight: bool = False
    log: bool = False
    fields: tuple = ()

    def as_text(self, max_fields=None):
        """Einzeilige Darstellung für rein textbasierte Plattformen (z.B. Twitch-Chat).
        `max_fields` begrenzt die Detailfelder - eine IRC-Zeile darf nur ~500 Zeichen
        lang sein, ein Stream-Abschlussbericht hätte sonst keine Chance."""
        parts = [self.title, self.text]
        parts += [f"{f.name}: {f.value}" for f in self.fields[:max_fields]]
        parts.append(self.url)
        return " - ".join(part.strip() for part in parts if part and part.strip())


class Platform(ABC):
    """Basisklasse jeder Plattform.

    Pflicht sind nur start() und close() - eine Plattform, die sonst nichts kann, ist
    trotzdem eine gültige Plattform. Alles Weitere wird über `capabilities` angemeldet;
    die Default-Implementierungen unten sagen schlicht "kann ich nicht"."""

    #: kurzer, eindeutiger Name ("twitch", "discord"). Taucht so auch in core/stats.py
    #: als platform-Spalte und in den Logs auf.
    name = ""

    #: frozenset der oben definierten Fähigkeiten.
    capabilities = frozenset()

    def supports(self, capability):
        return capability in self.capabilities

    @abstractmethod
    async def start(self):
        """Fährt die Plattform hoch. Darf zurückkehren, sobald sie läuft (eigene
        Hintergrundtasks laufen dann weiter), oder für ihre gesamte Laufzeit blockieren -
        bugbot.py wartet über ein gemeinsames gather() auf beides gleichermaßen."""

    @abstractmethod
    async def close(self):
        """Fährt sauber herunter: Hintergrundtasks abbrechen, Verbindungen schließen.
        Muss auch dann funktionieren, wenn start() nie oder nur halb durchgelaufen ist -
        beim Absturz einer anderen Plattform wird close() trotzdem aufgerufen."""

    async def wait_ready(self):
        """Wartet, bis die Plattform Ankündigungen entgegennehmen kann. Default: sofort.

        Discord wartet hier auf on_ready - vorher kennt es seine Server noch nicht und
        würde jede Ankündigung stillschweigend verwerfen. Genau deshalb gibt es das:
        der Live-Abgleich beim Start (Bot-Neustart mitten im Stream) meldet sonst in
        einen Bot hinein, der noch gar nicht eingeloggt ist."""
        return

    async def send_text(self, text):
        """Schreibt freien Text in den Hauptkanal der Plattform. True bei Erfolg.
        Default: kann die Plattform nicht (Fähigkeit CHAT fehlt)."""
        return False

    async def announce(self, announcement):
        """Postet eine Ankündigung, sofern die Plattform diese `kind` darstellen will.
        True, wenn sie tatsächlich gepostet wurde - der Aufrufer zählt damit, ob die
        Ankündigung überhaupt irgendwo angekommen ist (siehe !bug).
        Default: kann die Plattform nicht (Fähigkeit ANNOUNCE fehlt)."""
        return False

    def __repr__(self):
        return f"<{type(self).__name__} name={self.name!r}>"
