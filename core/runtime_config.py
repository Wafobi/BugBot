# runtime_config.py
# Die Konfiguration zur Laufzeit: eine JSON-Datei je Plattform und je Feature, neben dem
# Code, der sie liest. Jede wird bei Änderung neu eingelesen - Regeln, Texte, Namen,
# Schwellenwerte und Zeiten lassen sich also im laufenden Betrieb ändern.
#
# Drei Dinge macht eine LiveConfig, und alle drei haben denselben Grund: was ein anderer
# Betreiber anders haben will, soll er ändern können, ohne Python anzufassen.
#
#   get()/section()  Werte und Abschnitte, mit den DEFAULTS aus dem Code als Unterlage.
#                    Die Datei muss also nicht vollständig sein - was fehlt, kommt aus dem
#                    Code, und eine gelöschte Zeile ist kein Absturz, sondern der Default.
#   text()           die Texte des Bots. Jeder Satz, den ein Mensch zu sehen bekommt, hat
#                    einen Schlüssel und steht unter "texts" in der JSON. Ein Tippfehler
#                    des Betreibers darf dabei nie den Handler mitreißen, der ihn gerade
#                    ausgibt - text() gibt im Zweifel den Default aus und meldet einmal,
#                    was nicht stimmte.
#   commands()       Befehlsnamen: umbenennen, Aliase geben, abschalten. Wer schon einen
#                    Bot mit !uptime im Chat hat, soll den hier nicht doppelt bekommen.

import json
from pathlib import Path
from string import Formatter


def for_package(module_file, defaults=None):
    """Die Konfiguration neben dem Modul, das sie liest: features/stats/stats.json für
    features/stats/feature.py.

    Ein Paket, eine Datei, gleicher Name - so muss niemand raten, welche JSON zu welchem
    Ordner gehört, und ein zusätzliches Feature bringt seine Konfiguration einfach mit,
    ohne dass core davon wissen muss."""
    directory = Path(module_file).resolve().parent
    return LiveConfig(directory / f"{directory.name}.json", defaults)


def deep_merge(base, override):
    """Kopie von base, in die override hineingelegt wird. Verschachtelte Dicts werden
    zusammengeführt statt ersetzt: wer in der JSON *einen* Schwellenwert setzt, verliert
    nicht die übrigen aus den Defaults. Listen gelten als ein Wert - eine Liste in der
    JSON ersetzt die Default-Liste vollständig, denn sonst könnte man nie etwas
    wegnehmen."""
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def placeholders(template):
    """Die Namen aller {…}-Platzhalter einer Vorlage."""
    return {name for _, name, _, _ in Formatter().parse(template) if name}


class LiveConfig:
    """Lädt eine JSON-Datei und prüft bei jedem Zugriff per mtime, ob sie sich geändert
    hat - kein Polling-Task nötig, ein stat()-Aufruf ist billig genug, um ihn pro
    Chat-Nachricht mitlaufen zu lassen. Fehlt die Datei oder ist sie kaputt, bleibt der
    zuletzt bekannte gute Stand (bzw. die Defaults beim allerersten Laden) erhalten,
    statt den Bot abstürzen zu lassen.

    Zwei Lagen liegen übereinander, die obere schlägt die untere:

        `defaults`     was der Code mitbringt. Für die wenigen Werte, ohne die ein Modul
                       nicht arbeiten kann - damit es auch ohne die Datei läuft.
        aktuelle Datei was jetzt darin steht.

    Was in der Datei steht, ist damit auch das, was gilt: ein gelöschter Befehl ist weg,
    ein gelöschter Schwellenwert fällt auf den Wert aus dem Code zurück. Genau daran
    hängt der Sinn der ganzen Klasse - eine Änderung, die man nicht rückgängig machen
    kann, ohne den Bot neu zu starten, ist keine Laufzeit-Änderung.

    Eine dritte Lage gibt es trotzdem, aber nur für Texte und nur als Rückfalltext:
    `_baseline`, der Inhalt beim ersten erfolgreichen Laden (also die mitgelieferte Datei
    aus dem Repository). Sie hält text() am Leben, wenn ein Schlüssel fehlt - siehe dort.
    Texte sind der eine Fall, in dem das richtig ist: ein Feature, das fast nur aus Sätzen
    besteht, müsste sie sonst zweimal führen, einmal in Python und einmal in JSON. Für
    alles andere wäre dieselbe Unterlage nur ein Weg, Gelöschtes am Leben zu halten."""

    def __init__(self, path, defaults=None):
        self._path = Path(path)
        self._defaults = defaults or {}
        self._baseline = None
        self._mtime = None
        self._file_data = {}
        self._data = dict(self._defaults)
        self._complained = set()

        #: Zählt bei jedem erfolgreichen Neuladen hoch. Wer aus der Konfiguration etwas
        #: Teures ableitet (die Befehlstabelle im Bus), erkennt daran, dass sein
        #: Zwischenstand veraltet ist - siehe core/events.py.
        self.version = 0

        self.reload()

    @property
    def path(self):
        return self._path

    def reload(self):
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            return
        if mtime == self._mtime:
            return
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️ Konnte {self._path.name} nicht laden, behalte vorherigen Stand: {e}")
            return
        if self._baseline is None:
            self._baseline = data
        self._mtime = mtime
        self._file_data = data
        # Bewusst ohne _baseline dazwischen: die Datei ist die Wahrheit, sonst ließe sich
        # nichts löschen, was beim Start einmal dringestanden hat. Fehlende Texte fängt
        # text() eigens über _baseline ab - der einzige Ort, an dem das gewollt ist.
        self._data = deep_merge(self._defaults, data)
        self._complained.clear()
        self.version += 1

    @property
    def data(self):
        self.reload()
        return self._data

    def get(self, key, default=None):
        self.reload()
        value = self._data.get(key, default)
        return default if value is None else value

    def section(self, key):
        """Ein Unterabschnitt als Dict - immer eines, auch wenn der Schlüssel fehlt oder
        jemand etwas anderes hineingeschrieben hat."""
        value = self.get(key)
        return value if isinstance(value, dict) else {}

    # --- Texte -----------------------------------------------------------------------

    def text(self, key, **values):
        """Der Text zu einem Schlüssel, mit {platzhaltern} gefüllt.

        Robust mit Absicht: dieser Aufruf steckt in jedem Befehl und jeder Ankündigung,
        und die Vorlage kommt von außen. Weder ein unbekannter Platzhalter noch eine
        halbe geschweifte Klammer noch ein fehlender Schlüssel darf den Aufrufer mit
        einer Exception zurücklassen - im schlimmsten Fall steht eben der Default oder
        die rohe Vorlage da. Gemeldet wird trotzdem, aber nur einmal je Schlüssel und
        Dateistand, damit ein Tippfehler nicht das Log flutet."""
        template = self.section("texts").get(key)
        fallback = ((self._baseline or self._defaults).get("texts") or {}).get(key)

        if template is None:
            template = fallback
        if template is None:
            self._complain(key, f"kein Text für '{key}' hinterlegt")
            return key
        if not isinstance(template, str):
            self._complain(key, f"Text '{key}' ist kein Text, sondern {type(template).__name__}")
            template = fallback if isinstance(fallback, str) else key

        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError) as e:
            unknown = ", ".join(sorted(placeholders(template) - set(values))) or "?"
            self._complain(key, f"Text '{key}' benutzt unbekannte Platzhalter ({unknown}) - {e}")

        # Der Default kann es noch richtig machen, wenn nur die eigene Fassung kaputt ist.
        if isinstance(fallback, str) and fallback != template:
            try:
                return fallback.format(**values)
            except (KeyError, IndexError, ValueError):
                pass
        return template

    def color(self, key, default=0x3498DB):
        """Eine Farbe aus dem Abschnitt "colors" als Zahl, wie Announcement.color sie
        erwartet. In der JSON steht sie als "#2ECC71" - so, wie sie überall sonst
        geschrieben wird -, eine reine Zahl geht aber auch."""
        value = self.section("colors").get(key, default)
        if isinstance(value, int):
            return value
        try:
            return int(str(value).lstrip("#"), 16)
        except ValueError:
            self._complain(f"color:{key}", f"Farbe '{key}': {value!r} ist keine Farbe wie \"#2ECC71\"")
            return default

    def _complain(self, key, message):
        if key in self._complained:
            return
        self._complained.add(key)
        print(f"⚠️ {self._path.name}: {message}")

    # --- Befehlsnamen ------------------------------------------------------------------

    def command_names(self, section="command_names"):
        """{Standardname: (Name, Alias, ...)} aus der JSON, leer wenn nichts umbenannt
        wurde. Erlaubt drei Schreibweisen, weil sich alle drei natürlich lesen:

            "!uptime": "!live"                     umbenennen
            "!uptime": ["!live", "!wielange"]      umbenennen + Aliase
            "!uptime": false                       abschalten
            "!uptime": {"name": "!live", "aliases": ["!wielange"], "enabled": true}

        Namen ohne "!" werden ergänzt - "live" und "!live" sollen dasselbe meinen."""
        resolved = {}
        for default_name, setting in self.section(section).items():
            # Schlüssel mit Unterstrich sind Erklärungen für den Menschen, der die Datei
            # bearbeitet (JSON kennt keine Kommentare) - kein Befehl.
            if default_name.startswith("_"):
                continue
            names = _normalize_command_setting(setting)
            if names is None:
                self._complain(f"command:{default_name}",
                               f"Befehl '{default_name}': {setting!r} ist keine gültige Angabe")
                continue
            resolved[_with_prefix(default_name)] = names
        return resolved

    def resolve_commands(self, declared, section="command_names"):
        """Bildet {Standardname: Wert} auf {tatsächlicher Name: Wert} ab - inklusive
        Aliasen, die auf denselben Wert zeigen. Abgeschaltete Befehle fallen heraus.

        `declared` bleibt unangetastet; die Reihenfolge der Standardnamen bleibt erhalten,
        damit Auflistungen (!commands) nicht bei jedem Neuladen anders aussehen."""
        overrides = self.command_names(section)
        resolved = {}
        for default_name, value in declared.items():
            names = overrides.get(_with_prefix(default_name), (_with_prefix(default_name),))
            for name in names:
                if name in resolved:
                    self._complain(f"collision:{name}",
                                   f"Befehlsname '{name}' ist doppelt vergeben - der spätere wird ignoriert")
                    continue
                resolved[name] = value
        return resolved


def _with_prefix(name):
    name = str(name).strip().lower()
    return name if name.startswith("!") else f"!{name}"


def _normalize_command_setting(setting):
    """(Name, Alias, ...) oder () für "abgeschaltet"; None, wenn die Angabe unbrauchbar
    ist - der Aufrufer meldet das und lässt den Standardnamen stehen."""
    if setting is False:
        return ()
    if isinstance(setting, str):
        return (_with_prefix(setting),) if setting.strip() else None
    if isinstance(setting, (list, tuple)):
        names = tuple(_with_prefix(n) for n in setting if str(n).strip())
        return names or None
    if isinstance(setting, dict):
        if setting.get("enabled") is False:
            return ()
        names = [setting["name"]] if setting.get("name") else []
        names += list(setting.get("aliases") or [])
        return tuple(_with_prefix(n) for n in names if str(n).strip()) or None
    return None
