# feature.py
# Variablen als Feature (Fähigkeit VARIABLES).
#
# Füllt die {platzhalter} in den statischen Befehlen aus twitch.json/discord.json. Vorher
# stand an den Aufrufstellen ein blankes `.format(u=user_name)`: {u} war der einzig
# mögliche Platzhalter, und alles andere - ein {zeit} statt {time}, ein Tippfehler - warf
# eine KeyError, die weit oben als "Fehler bei der Verarbeitung" landete. Der Befehl blieb
# im Chat einfach still.
#
# Ein Feature und keine Methode in core/runtime_config.py, weil hier genau das steht, was
# ein Feature ausmacht: es ist auf jeder Plattform dasselbe, es bringt seine eigene
# Konfiguration mit, und es darf fehlen. Ohne dieses Feature funktionieren {u} und {user}
# weiter (die weiß die Plattform selbst), nur {time} und die selbstdefinierten Variablen
# bleiben dann als Text stehen - siehe platforms/twitch/bot.py:_render.
#
# Zwei Quellen, die zweite schlägt die erste:
#
#   "variables" feste Zeichenketten: {steam}, {socials}, was der Betreiber eben braucht.
#   "python"    ein Python-Ausdruck je Variable, ausgewertet beim Benutzen des Befehls.
#
# Auch {time} und {date} sind nichts Besonderes, sondern zwei ganz normale Ausdrücke in
# variables.json - hier steht kein einziger Variablenname mehr. Wer wissen will, was es
# gibt, liest die Datei, und wer die Uhrzeit anders formatiert haben will, ändert dort den
# Ausdruck, statt einen Formatierungs-Schlüssel zu suchen, den erst der Code kennt. Damit
# ein kaputte oder fehlende Datei nicht auch noch die Uhr abschaltet, stehen genau diese
# zwei zusätzlich in DEFAULTS unten (siehe core/runtime_config.py: Defaults sind das, was
# ein Modul zum Arbeiten braucht).
#
# Zuletzt legt die Plattform ihren Kontext darüber ({u}, {user}, {channel}) - der kommt
# aus der Nachricht und nicht aus der Datei, deshalb kann ihn keine Konfiguration
# verstellen.

import asyncio
import locale
import math
import random
import time as time_module
from datetime import date as date_type, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core import feature as feature_api, runtime_config

# Nur das Nötigste; alles Weitere steht in variables.json und kann dort geändert werden.
# {time} und {date} stehen auch dort - hier ein zweites Mal, damit ein Befehl mit der
# Uhrzeit selbst dann noch antwortet, wenn die Datei fehlt oder jemand sie kaputtschreibt.
DEFAULTS = {
    "timezone": "",
    "locale": "",
    "python": {
        "time": "now.strftime('%H:%M')",
        "date": "now.strftime('%d.%m.%Y')",
    },
    "python_timeout_seconds": 2,
    "cache_seconds": 3,
}


class VariablesFeature(feature_api.Feature):
    name = "variables"
    provides = frozenset({feature_api.VARIABLES})

    def __init__(self):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        #: name -> (Konfigurationsstand, Zeitpunkt, Wert). Siehe _cached.
        self._cache = {}
        #: Zuletzt gesetztes Locale, damit setlocale nicht bei jeder Nachricht läuft.
        self._locale = None

    # --- Zeit ---------------------------------------------------------------------------

    def _apply_locale(self):
        """Setzt LC_TIME auf das Locale aus variables.json - davon hängen die
        ausgeschriebenen Wochentage und Monate ab (strftime "%A", "%B").

        Nötig, weil das Container-Image nur C und C.utf8 kennt: ohne diesen Aufruf liefert
        now.strftime('%A') ein englisches "Sunday", bei völlig richtig gerechnetem Datum.
        Das Locale muss im Image erzeugt worden sein (siehe Dockerfile, ARG LOCALE); ist es
        das nicht, bleibt es bei Englisch, und es steht einmal im Log, warum.

        Prozessweit, wie setlocale nun einmal ist. Das ist hier richtig: ein Bot spricht
        eine Sprache, und wer sie ändert, will sie überall geändert haben."""
        name = str(self.config.get("locale", "") or "").strip()
        if name == self._locale:
            return
        self._locale = name
        if not name:
            return
        try:
            locale.setlocale(locale.LC_TIME, name)
        except locale.Error as e:
            self.config.complain(
                "locale",
                f"Locale {name!r} ist nicht verfügbar, Wochentage bleiben englisch - {e}. "
                f"Im Container wird es beim Bauen erzeugt (Dockerfile, ARG LOCALE).",
            )

    def now(self):
        """Jetzt, in der Zeitzone aus variables.json ("timezone", z.B. "Europe/Berlin").

        Die Zeitzone steht in der Konfiguration und nicht in der Container-Umgebung, weil
        sie zu dem gehört, was ein Betreiber einstellt - und weil sie so wie alles andere
        hier zur Laufzeit änderbar ist. Fehlt sie, gilt die Zeit des Prozesses; im
        Container ist das UTC, ein {time} ginge also im Sommer zwei Stunden falsch, ohne
        dass irgendwo ein Fehler auftauchte."""
        name = str(self.config.get("timezone", "") or "").strip()
        if not name:
            return datetime.now()
        try:
            return datetime.now(ZoneInfo(name))
        except (ZoneInfoNotFoundError, ValueError, OSError) as e:
            self.config.complain("timezone", f"Zeitzone {name!r} ist unbekannt, nehme die des Servers - {e}")
            return datetime.now()

    # --- Auflösen -----------------------------------------------------------------------

    async def resolve(self, template, **context):
        """Die Werte für die {platzhalter}, die in `template` wirklich vorkommen.

        Nur die vorkommenden: sonst liefe bei jedem `!discord` auch jeder Python-Ausdruck
        mit, den irgendwer für einen ganz anderen Befehl hinterlegt hat.

        `context` ist, was die Plattform beisteuert ({u}, {user}, {channel}) - es steht den
        Ausdrücken als Variable zur Verfügung und gewinnt am Ende gegen alles aus der
        Datei."""
        wanted = runtime_config.placeholders(template)
        if not wanted:
            return dict(context)
        self._apply_locale()

        values = {}
        for name in wanted:
            if name in context:
                continue
            value = await self._value(name, context, values, set())
            if value is not None:
                values[name] = value

        values.update(context)
        return values

    async def _value(self, name, context, values, resolving):
        """Der Wert einer einzelnen Variablen, mit allem, was sie ihrerseits braucht.

        Variablen dürfen Variablen benutzen - anders wäre es eine willkürliche Grenze:
        wer {steam} definiert hat, will es auch in {chef} schreiben können, statt die URL
        ein zweites Mal hinzuschreiben und beim nächsten Mal eine der beiden zu vergessen.
        Deshalb wird hier nicht stur eine Liste durchgegangen, sondern von der gefragten
        Variablen aus rückwärts aufgelöst, so tief wie nötig.

        `resolving` sind die Variablen, die auf dem Weg hierher schon angefangen wurden.
        Steht die gefragte selbst darin, hat sich jemand im Kreis definiert; das endete
        ohne diese Prüfung in einer Endlosschleife, also mit einem stehenden Bot statt
        einer Fehlermeldung."""
        if name in context:
            return context[name]
        if name in values:
            return values[name]

        static = self.config.section("variables")
        code = self.config.section("python")
        if name.startswith("_") or (name not in static and name not in code):
            return None

        if name in resolving:
            self.config.complain(
                f"cycle:{name}",
                f"Variable '{name}' benutzt sich am Ende selbst "
                f"({' -> '.join([*resolving, name])}) - der Platzhalter bleibt stehen",
            )
            return None
        resolving = resolving | {name}

        if name in static:
            value = await self._expand(name, str(static[name]), context, values, resolving)
        else:
            value = await self._evaluate(name, code[name], context, values, resolving)
        if value is not None:
            values[name] = value
        return value

    async def _expand(self, name, text, context, values, resolving):
        """Ein fester Text aus "variables", dessen eigene {platzhalter} gefüllt sind.

        Ohne diesen Schritt käme ein {steam} in einem festen Text wörtlich im Chat an -
        lautlos und mit einer Klammer mitten im Satz, denn format() ersetzt nur einmal und
        nicht, was dabei herauskommt."""
        inner = {}
        for dependency in runtime_config.placeholders(text):
            value = await self._value(dependency, context, values, resolving)
            if value is not None:
                inner[dependency] = value
        return self.config.render(text, **{**context, **inner})

    # --- Python-Ausdrücke ---------------------------------------------------------------

    async def _evaluate(self, key, code, context, values, resolving):
        """Wertet einen Ausdruck aus variables.json aus. None heißt "hat nicht geklappt";
        der Aufrufer lässt den Platzhalter dann stehen, statt Unsinn zu posten.

        Bewusst ein *Ausdruck* und keine Anweisungen: compile(..., "eval") lässt kein
        import, kein Zuweisen und kein Schreiben von Dateien durch die Hintertür zu. Es ist
        trotzdem kein Sandkasten und soll auch keiner sein - der Ausdruck läuft im
        Bot-Prozess, mit dessen Rechten, und wer die Datei schreiben darf, darf ohnehin
        schon alles. Was hier zählt, ist etwas anderes: dass ein *Versehen* - ein Tippfehler,
        eine Division durch null, etwas Langsames - nicht den Bot mitnimmt.

        Deshalb drei Vorkehrungen, jede gegen einen anderen Unfall:
          * jede Exception wird gefangen und einmal gemeldet,
          * die Auswertung läuft in einem Thread mit Zeitlimit, damit etwas Hängendes
            nicht die ganze Nachrichtenverarbeitung anhält,
          * das Ergebnis wird kurz gemerkt (cache_seconds), sonst startet jede
            Chat-Nachricht mit diesem Befehl die Auswertung neu."""
        cached = self._cached(key)
        if cached is not None:
            return cached

        timeout = _positive(self.config.get("python_timeout_seconds", 2), 2)
        try:
            compiled = compile(str(code).strip(), f"<{self.config.path.name}:{key}>", "eval")
        except SyntaxError as e:
            self.config.complain(f"python:{key}", f"Variable '{key}': {e.msg} - der Ausdruck bleibt ungenutzt")
            return None

        # Welche anderen Variablen der Ausdruck benutzt, sagt er selbst: co_names sind die
        # Namen, die er nachschlägt. Nur die werden aufgelöst - so kostet `steam + '?l=de'`
        # genau die eine Variable und nicht die ganze Datei, und ein Ausdruck, der gar
        # keine benutzt, löst gar nichts aus.
        namespace = {}
        for dependency in compiled.co_names:
            value = await self._value(dependency, context, values, resolving)
            if value is not None:
                namespace[dependency] = value

        environment = {**_SAFE_NAMES, **namespace, **context, "now": self.now()}
        try:
            value = await asyncio.wait_for(
                asyncio.get_running_loop().run_in_executor(None, lambda: eval(compiled, environment)),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            # Der Thread läuft weiter - abbrechen lässt sich ein laufender Ausdruck in
            # Python nicht. Der Bot wartet nur nicht mehr auf ihn, und das ist der Punkt.
            self.config.complain(f"python:{key}", f"Variable '{key}' braucht länger als {timeout}s - übersprungen")
            return None
        except Exception as e:
            self.config.complain(f"python:{key}", f"Variable '{key}' ist fehlgeschlagen: {e!r}")
            return None

        text = "" if value is None else str(value)
        self._remember(key, text)
        return text

    def _cached(self, key):
        """Der gemerkte Wert, solange er frisch ist. Ein Bearbeiten der Datei macht ihn
        sofort ungültig (config.version), sonst wartete man nach einer Änderung noch
        cache_seconds auf ihre Wirkung - und genau das soll hier ja nicht passieren."""
        entry = self._cache.get(key)
        if entry is None:
            return None
        version, when, value = entry
        seconds = _positive(self.config.get("cache_seconds", 3), 3)
        if version != self.config.version or (time_module.monotonic() - when) > seconds:
            return None
        return value

    def _remember(self, key, value):
        self._cache[key] = (self.config.version, time_module.monotonic(), value)


def _positive(value, default):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return number if number > 0 else default


# Was einem Ausdruck ohne Import zur Verfügung steht. Klein gehalten und auf das
# ausgerichtet, wofür Variablen in einem Chat da sind: Zeit, Datum, ein bisschen Rechnen,
# ein bisschen Zufall.
_SAFE_NAMES = {
    "datetime": datetime,
    "date": date_type,
    "timedelta": timedelta,
    "ZoneInfo": ZoneInfo,
    "math": math,
    "random": random,
}


def create_feature():
    return VariablesFeature()
