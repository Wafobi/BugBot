# feature.py
# Die Feature-API - der Vertrag für alles, was Plattformen *benutzen*, statt selbst eine
# zu sein. Gegenstück zu core/platform.py.
#
# Konvention wie bei den Plattformen: jedes Feature lebt in features/<name>/ und stellt in
# features/<name>/feature.py eine Funktion create_feature() bereit (siehe core/registry.py).
#
# Ein Feature meldet über `provides` an, was es kann, und wird auf zwei Wegen genutzt:
#
#   Push  - es abonniert im setup() die Topics aus core/events.py und schreibt mit, was
#           passiert. Die Plattform publiziert nur "das ist passiert" und weiß nicht, wer
#           zuhört. So läuft die gesamte Aufzeichnung: vorher rief jede Plattform ~30 mal
#           stats.record_* mit voller Signatur auf.
#   Pull  - die Plattform holt sich ein Feature über seine Fähigkeit und ruft dessen
#           Methoden auf. Nötig, wo sie eine *Antwort* braucht, bevor sie weitermachen
#           kann - allen voran die Moderation, die ein Urteil zur Nachricht liefert.
#
# Zusätzlich bringt ein Feature seine eigenen Befehle mit (commands()). Die Plattformen
# hängen sie in ihre Befehlsauflösung ein, ohne sie zu kennen - !rank oder !leaderboard
# funktionieren dadurch überall, ohne dass jemand Twitch- oder Discord-Code dafür
# schreibt.

from abc import ABC
from dataclasses import dataclass

# Announcement/Field sind die neutrale Darstellungssprache zwischen Plattformen und
# Features: eine Plattform rendert sie (Embed/Chatzeile), ein Feature erzeugt sie als
# Befehlsantwort oder Kennzahlenblock. Sie liegen in core/platform.py, weil dort der
# rendernde Teil des Vertrags steht - hier nur weitergereicht, damit Feature-Code nicht
# quer in die Platform-API greifen muss.
from .platform import Announcement, Field, STATUS  # noqa: F401  (bewusster Re-Export)

# Ebenso re-exportiert: die Fähigkeiten der Plattformen. Ein Feature soll sagen können,
# *welche Art* Plattform es angeht ("die, die einen Stream meldet"), ohne quer in die
# Platform-API zu greifen - und ohne einen Dienst beim Namen zu nennen.
from .platform import ANNOUNCE, CHAT, MODERATE, STREAM  # noqa: F401


# --- Fähigkeiten -------------------------------------------------------------------
STORAGE = "storage"        # persistente Ablage für andere Features (siehe features/sql_db)
RECORDING = "recording"    # schreibt mit, was auf den Plattformen passiert
MODERATION = "moderation"  # liefert ein Urteil zu einer Nachricht (review)
STATS = "stats"            # beantwortet Kennzahlen-Abfragen
LEVELS = "levels"          # XP/Level je User
SESSIONS = "sessions"      # kennt die laufende Stream-Session (siehe features/stream_sessions)
CHAT_LOG = "chat_log"      # Mitschnitt des vollen Nachrichtentextes
RAW_LOG = "raw_log"        # Rohprotokoll der Plattform-Benachrichtigungen


@dataclass
class Message:
    """Eine eingehende Nachricht, plattformneutral - das, was Features von einer
    Plattform zu sehen bekommen.

    `raw` ist das plattformspezifische Original (discord.Message bzw. der Twitch-
    Kontext). Features sollten es nur anfassen, wenn sie ohnehin plattformspezifisch
    handeln; alles Neutrale steht in den Feldern darüber.

    command/arg_text sind gefüllt, wenn die Nachricht als Befehl erkannt wurde."""
    platform: str
    user_id: str = ""
    user_name: str = ""
    text: str = ""
    is_privileged: bool = False
    is_subscriber: bool = False
    command: str = ""
    arg_text: str = ""
    raw: object = None


@dataclass(frozen=True)
class Verdict:
    """Das Urteil eines MODERATION-Features zu einer Nachricht. Die Plattform führt es
    aus - sie weiß als Einzige, wie man auf ihr löscht und stummschaltet -, entscheidet
    es aber nicht mehr selbst. Auch die Eskalation (ab dem wievielten Verstoß ein
    Timeout fällig ist) steckt hier drin und nicht mehr doppelt in beiden Plattformen."""
    reason: str                 # maschinenlesbar, z.B. "banned_word"
    label: str                  # Klartext für Chat/Log, z.B. "unerlaubtes Wort"
    detail: str = ""            # optionaler Fund, z.B. das Wort selbst
    delete: bool = True
    timeout_seconds: int = 0    # 0 = kein Timeout
    violation_count: int = 1


@dataclass(frozen=True)
class Command:
    """Ein Befehl, den ein Feature mitbringt. Der Handler bekommt eine Message (mit
    gefülltem command/arg_text) und gibt zurück, was gepostet werden soll:
    einen String, eine Announcement (für Plattformen, die reichhaltig darstellen
    können - Discord macht daraus ein Embed, Twitch eine Textzeile) oder None."""
    name: str
    handler: object
    mod_only: bool = False
    help: str = ""


class Feature(ABC):
    """Basisklasse jedes Features. Nichts davon ist Pflicht: ein Feature, das nur
    zuhört, überschreibt setup(); eines, das nur Befehle beisteuert, nur commands()."""

    #: kurzer, eindeutiger Name ("stats", "moderation", "levels")
    name = ""

    #: Name der Plattform, der dieses Feature gehört - gesetzt von core/registry.py aus dem
    #: Ordner, in dem es liegt (platforms/discord/features/levels -> "discord"). Leer bei
    #: den neutralen Features aus features/.
    #:
    #: Dafür da, dass ein plattformeigenes Feature den Namen seines Dienstes nirgends
    #: hinschreiben muss: `if message.platform != self.owner` sagt "nicht meine Plattform"
    #: und bleibt richtig, wenn der Ordner einmal anders heißt. Ein Feature *muss* nicht
    #: filtern - das Rohprotokoll etwa hebt bewusst alles auf, was hereinkommt.
    owner = ""

    #: Der Bus, an dem dieses Feature hängt. Setzt core/registry.py vor setup(), damit auch
    #: Methoden außerhalb von setup() an das Verzeichnis der Plattformen kommen.
    bus = None

    #: Fähigkeiten, die eine *Plattform* haben muss, damit ihre Meldungen dieses Feature
    #: angehen (aus core/platform.py: CHAT, ANNOUNCE, STREAM, MODERATE). Leer = alle.
    #:
    #: Das ist die Sprache, in der ein neutrales Feature über Plattformen reden darf:
    #: "die mit einem Stream" statt "Twitch". Sie überlebt einen Dienstwechsel, sie stimmt
    #: auf einer Installation, die es nie gab, und sie kann nicht still ins Leere zeigen -
    #: anders als ein Name, der auf der falschen Installation einfach nie zutrifft.
    platform_capabilities = frozenset()

    #: frozenset der oben definierten Fähigkeiten, die dieses Feature anbietet
    provides = frozenset()

    #: Fähigkeiten, die dieses Feature von *anderen* Features braucht. core/registry.py
    #: richtet die Features in Abhängigkeitsreihenfolge ein und überspringt eines, dessen
    #: Bedarf niemand deckt - ein halb funktionierendes Feature ist schlimmer als keines.
    requires = frozenset()

    #: Die eigene LiveConfig (features/<name>/<name>.json), oder None für ein Feature
    #: ohne Einstellungen. Wer eine setzt, bekommt zweierlei geschenkt: seine Texte über
    #: config.text() und die Umbenennung seiner Befehle - der Bus wendet den Abschnitt
    #: "command_names" beim Einsammeln an, das Feature muss dafür nichts tun (siehe
    #: core/events.py:EventBus.commands).
    config = None

    #: Fähigkeiten, die dieses Feature *mitnimmt, wenn es sie gibt*. Sie entscheiden nur
    #: über die Reihenfolge, nie über das Ob: ist niemand da, der sie anbietet, wird das
    #: Feature trotzdem eingerichtet und muss im setup() mit None zurechtkommen.
    #:
    #: Nötig, weil sonst genau das passiert, was `requires` verhindern soll - nur
    #: umgekehrt: ohne diese Angabe liefe setup() womöglich, bevor das optionale Feature
    #: registriert ist, und der Verzeichnis-Blick ginge still ins Leere. Beispiel: die
    #: Statistik nimmt die Stream-Sessions mit, wenn Twitch mitläuft, zählt aber auch ohne.
    optional = frozenset()

    def supports(self, capability):
        return capability in self.provides

    def platform_scope(self):
        """Die Namen der Plattformen, die dieses Feature angehen - oder None für "alle".

        Erst die eigene Plattform (bei einem plattformeigenen Feature), sonst die, die die
        geforderten Fähigkeiten mitbringen. Wird bei jedem Aufruf neu bestimmt: welche
        Plattformen es gibt, steht erst nach dem Start fest - die Features werden vorher
        eingerichtet."""
        if self.owner:
            return {self.owner}
        if not self.platform_capabilities or self.bus is None:
            return None
        return {
            platform.name
            for platform in self.bus.platforms
            if self.platform_capabilities <= platform.capabilities
        }

    def handles(self, platform_name):
        """Geht mich eine Meldung dieser Plattform etwas an?"""
        scope = self.platform_scope()
        return scope is None or platform_name in scope

    async def setup(self, bus):
        """Wird einmal beim Start aufgerufen, bevor die Plattformen hochfahren: Tabellen
        anlegen, Topics abonnieren, Zustand wiederherstellen. Der Bus ist mitgegeben,
        damit ein Feature hier auch die Features holen kann, die es laut `requires`
        braucht (siehe features/stats: Ablage über die Fähigkeit STORAGE)."""
        return

    async def close(self):
        """Aufräumen beim Herunterfahren. Muss auch dann funktionieren, wenn setup()
        nie oder nur halb durchgelaufen ist."""
        return

    def commands(self):
        """Befehle dieses Features, als Tupel von Command. Die Plattformen hängen sie
        in ihre eigene Befehlsauflösung ein."""
        return ()

    def __repr__(self):
        return f"<{type(self).__name__} name={self.name!r}>"
