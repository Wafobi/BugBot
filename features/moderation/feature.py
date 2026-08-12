# feature.py
# Moderation als Feature (Fähigkeit MODERATION).
#
# Das einzige Feature, das per Pull genutzt wird: die Plattform braucht ein Urteil,
# bevor sie mit der Nachricht weitermachen kann, ein "melde und vergiss" über den Bus
# reicht dafür nicht.
#
# Neu gegenüber core/moderation.py: das Feature entscheidet die *Konsequenz*, nicht nur
# den Treffer. Die Eskalationslogik (ab dem wievielten Verstoß innerhalb welchen
# Zeitfensters ein Timeout fällig wird) stand vorher wortgleich in beiden Plattformen
# und ist jetzt genau einmal hier. Die Plattform führt das Urteil nur noch aus - sie
# weiß als Einzige, wie man auf ihr löscht und stummschaltet.

from collections import defaultdict
from datetime import datetime, timedelta

from core import feature as feature_api, runtime_config

from . import filters

# Die Werte aus moderation.json - hier noch einmal, damit das Feature auch ohne die Datei
# moderiert (siehe core/runtime_config.py). Was ein Verstoß ist, steht damit an einer
# Stelle für alle Plattformen; twitch.json/discord.json können einzelne Werte in ihrem
# "moderation"-Abschnitt weiterhin überschreiben.
DEFAULTS = {
    "settings": dict(filters.DEFAULT_MODERATION_SETTINGS),
    "banned_words": {"use_builtin_list": True, "extra": [], "remove": []},
    "texts": dict(filters.VIOLATION_REASON_LABELS),
}


def _mask(found):
    """Der Fund, unlesbar gemacht: "Idiot" -> "I****".

    Nötig, weil `detail` bei einem verbotenen Wort das Wort selbst ist (filters.py) und die
    Plattformen es in ihre Verstoßmeldung schreiben. Ungekürzt hieße das: Der Bot löscht die
    Nachricht und sagt das Wort anschließend selbst - und auf ihn wendet kein Filter etwas
    an. Ein Troll bräuchte dafür nicht einmal einen Befehl, nur das Wort.

    Maskiert wird hier und nicht in den Plattformen, damit keine von ihnen den Fund je
    ungefiltert in die Hand bekommt - eine dritte Plattform erbt den Schutz dadurch, ohne
    etwas dafür zu tun.

    Der erste Buchstabe bleibt stehen, damit ein Mod die Meldung noch zuordnen kann. Bei
    einem einzelnen Zeichen bleibt nichts - sonst wäre die Maske der Fund.
    """
    found = (found or "").strip()
    if not found:
        return ""
    return found[0] + "*" * (len(found) - 1) if len(found) > 1 else "*"


class ModerationFeature(feature_api.Feature):
    name = "moderation"
    provides = frozenset({feature_api.MODERATION})

    def __init__(self):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        # user_key -> Zeitstempel der jüngsten Verstöße, für die Eskalation. Bewusst nur
        # im RAM: ein Neustart soll niemandem einen alten Verstoß nachtragen.
        self._violations = defaultdict(list)

    async def review(self, message, overrides=None):
        """Prüft eine Nachricht und gibt ein Verdict zurück - oder None, wenn nichts
        dagegen spricht.

        `overrides` ist der "moderation"-Abschnitt aus der JSON der aufrufenden
        Plattform - er liegt über den gemeinsamen Werten aus moderation.json. Die
        Plattform reicht ihn bei jeder Nachricht neu herein, damit die
        Hot-Reload-Konfiguration weiter greift und beide Plattformen unterschiedlich
        streng eingestellt bleiben können."""
        if message.is_privileged:
            # Broadcaster/Moderatoren/Admins sind ausgenommen - vorher prüfte das jede
            # Plattform vor dem Aufruf selbst, jetzt gilt die Regel an einer Stelle.
            return None

        settings = filters.build_settings(
            self.config.section("settings"), overrides, self.config.section("banned_words"),
        )
        hit = filters.moderate_message(message.text, settings, relaxed=message.is_subscriber)
        if not hit:
            return None

        reason, detail = hit
        count = self._record_violation(
            self._user_key(message), settings["violation_window_minutes"]
        )
        over_threshold = count >= settings["timeout_threshold"]
        return feature_api.Verdict(
            reason=reason,
            label=self.config.text(f"reason.{reason}"),
            detail=_mask(detail),
            delete=True,
            timeout_seconds=settings["timeout_duration_seconds"] if over_threshold else 0,
            violation_count=count,
        )

    @staticmethod
    def _user_key(message):
        """Zählt Verstöße pro User und Plattform. Die User-ID ist stabiler als der Name
        (Umbenennungen, unterschiedliche Schreibweisen), deshalb hat sie Vorrang."""
        return f"{message.platform}:{message.user_id or message.user_name.lower()}"

    def _record_violation(self, user_key, window_minutes):
        """Zählt die Verstöße dieses Users innerhalb der letzten `window_minutes` Minuten
        und gibt die aktuelle Anzahl zurück (Eskalation Löschen -> Timeout)."""
        now = datetime.now()
        history = self._violations[user_key]
        history.append(now)
        cutoff = now - timedelta(minutes=window_minutes)
        while history and history[0] < cutoff:
            history.pop(0)
        return len(history)


def create_feature():
    return ModerationFeature()
