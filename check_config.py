#!/usr/bin/env python3
"""Prüft die JSON-Konfigurationen gegen den Code, der sie liest.

    python3 check_config.py

Gedacht für nach dem Ändern einer der *.json-Dateien - und für den, der den Bot zum
ersten Mal auf seinen eigenen Server anpasst. Der Bot selbst stürzt an einer kaputten
Konfiguration nicht ab (fehlende Texte fallen auf die mitgelieferte Fassung zurück,
unbekannte Platzhalter bleiben stehen), aber gemerkt hätte man es erst, wenn der Befehl
im Chat merkwürdig aussieht. Das hier sagt es vorher.

Geprüft wird:
  * lässt sich jede Datei überhaupt lesen (JSON-Syntax),
  * hat jeder text("...")-Aufruf im Code einen Schlüssel in der zugehörigen Datei,
  * passen die {platzhalter} eines Textes zu dem, was der Aufrufer mitgibt,
  * gibt es Texte, die niemand mehr benutzt,
  * nennt "command_names" nur Befehle, die es gibt, und ist danach keiner doppelt.

Die Zuordnung Code -> Datei folgt der Konvention aus core/runtime_config.py: eine JSON je
Paket, benannt wie das Paket. Ausnahme sind die plattformeigenen Features, die sich die
Datei ihrer Plattform teilen (obs_control), und die beiden Twitch-Module, die dieselbe
twitch.json lesen.
"""

import ast
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Welche Konfigurationsdatei zu welchem Modul gehört. Der erste passende Präfix gewinnt,
# deshalb stehen die spezielleren Pfade oben.
CONFIG_FOR = [
    ("platforms/discord/features/levels", "platforms/discord/features/levels/levels.json"),
    ("platforms/obs", "platforms/obs/obs.json"),
    ("platforms/twitch", "platforms/twitch/twitch.json"),
    ("platforms/discord", "platforms/discord/discord.json"),
    ("features/stats", "features/stats/stats.json"),
    ("features/moderation", "features/moderation/moderation.json"),
    ("features/chat_log", "features/chat_log/chat_log.json"),
    ("features/sql_db", "features/sql_db/sql_db.json"),
]

# Dateien, die zwar Texte ausgeben, aber nicht zum Bot gehören (laufen auf dem OBS-PC).
SKIP = {"platforms/obs/obs_bridge.py", "platforms/obs/obs_bridge_script.py"}

problems = []
notes = []


def config_path_for(path):
    relative = path.relative_to(ROOT).as_posix()
    for prefix, config in CONFIG_FOR:
        if relative.startswith(prefix):
            return ROOT / config
    return None


def load(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        problems.append(f"{path.relative_to(ROOT)}: kaputtes JSON - {e}")
    except OSError as e:
        problems.append(f"{path.relative_to(ROOT)}: nicht lesbar - {e}")
    return None


def placeholders(template):
    from string import Formatter
    try:
        return {name for _, name, _, _ in Formatter().parse(template) if name}
    except ValueError as e:
        return {f"<kaputte Vorlage: {e}>"}


def _literal_keys(node):
    """Die wörtlichen Schlüssel eines Text-Aufrufs. Meist genau einer; bei
    text("a" if x else "b") sind es zwei, und ein zusammengesetzter Schlüssel
    (f"reason.{reason}") hat keinen - der lässt sich von außen nicht prüfen."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return [node.value]
    if isinstance(node, ast.IfExp):
        return _literal_keys(node.body) + _literal_keys(node.orelse)
    return []


def text_calls(tree):
    """Alle Aufrufe, die einen Text holen: text("k", ...), CONFIG.text("k", ...),
    self.config.text("k", ...)."""
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "text" or not node.args:
            continue
        given = {kw.arg for kw in node.keywords if kw.arg}
        for key in _literal_keys(node.args[0]):
            yield key, given, node.lineno


def string_constants(tree):
    """Jede Zeichenkette im Modul. Für die Frage "benutzt das noch jemand?": Textschlüssel
    stehen nicht immer im Aufruf selbst, sondern auch mal in einer Tabelle daneben
    (requests = {"start": ("StartRecord", "rec.started")}). Als Beleg für "wird benutzt"
    reicht das - für die strengere Prüfung der Platzhalter oben nicht."""
    return {
        node.value for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
    }


def dynamic_key_shapes(tree):
    """(Präfixe, Suffixe) der zusammengesetzten Schlüssel, z.B. f"highscore.{metric}" ->
    Präfix "highscore.", f"{key}.value" -> Suffix ".value". Alles, was dazu passt, gilt
    als benutzt."""
    prefixes, suffixes = set(), set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr) or not node.values:
            continue
        first, last = node.values[0], node.values[-1]
        if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value:
            prefixes.add(first.value)
        if isinstance(last, ast.Constant) and isinstance(last.value, str) and last.value:
            suffixes.add(last.value)
    return prefixes, suffixes


def check_texts():
    used = {}
    for path in sorted(ROOT.glob("**/*.py")):
        relative = path.relative_to(ROOT).as_posix()
        if relative in SKIP or relative.startswith((".", "__")) or "/__pycache__/" in f"/{relative}":
            continue
        config_path = config_path_for(path)
        if config_path is None:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError as e:
            problems.append(f"{relative}: {e}")
            continue

        data = load(config_path) or {}
        texts = data.get("texts", {})
        seen = used.setdefault(config_path, set())
        seen |= {value for value in string_constants(tree) if value in texts}
        prefixes, suffixes = dynamic_key_shapes(tree)
        seen |= {
            key for key in texts
            if any(key.startswith(p) for p in prefixes) or any(key.endswith(x) for x in suffixes)
        }
        for key, given, line in text_calls(tree):
            seen.add(key)
            if key not in texts:
                problems.append(
                    f"{relative}:{line}: Text '{key}' fehlt in {config_path.relative_to(ROOT)}"
                )
                continue
            template = texts[key]
            if not isinstance(template, str):
                problems.append(f"{config_path.relative_to(ROOT)}: '{key}' ist kein Text")
                continue
            needed = placeholders(template)
            missing = needed - given
            if missing:
                problems.append(
                    f"{config_path.relative_to(ROOT)}: '{key}' verlangt {{{', '.join(sorted(missing))}}}, "
                    f"aber {relative}:{line} gibt das nicht mit"
                )
    return used


def check_unused(used):
    for _, config in CONFIG_FOR:
        path = ROOT / config
        data = load(path)
        if not data:
            continue
        texts = {k for k in data.get("texts", {}) if not k.startswith("_")}
        unused = texts - used.get(path, set())
        if unused:
            notes.append(f"{config}: {len(unused)} Text(e) benutzt niemand: {', '.join(sorted(unused))}")


def check_commands():
    """Umbenennungen prüfen: nennt "command_names" nur Befehle, die es gibt, und kommt
    danach kein Name doppelt vor?"""
    sys.path.insert(0, str(ROOT))
    from core import registry, runtime_config

    declared = {}
    for package, name in registry.feature_sources():
        try:
            module = __import__(f"{package}.{name}.feature", fromlist=["create_feature"])
            feature = module.create_feature()
        except Exception as e:
            notes.append(f"Feature '{name}' nicht ladbar, Befehle ungeprüft: {e!r}")
            continue
        config = getattr(feature, "config", None)
        if config is None:
            continue
        declared.setdefault(config.path, set()).update(c.name for c in feature.commands())

    seen = {}
    for path, names in declared.items():
        config = runtime_config.LiveConfig(path)
        overrides = config.command_names()
        for default_name in overrides:
            if default_name not in names:
                problems.append(
                    f"{path.relative_to(ROOT)}: command_names nennt '{default_name}', "
                    f"den es nicht gibt (vorhanden: {', '.join(sorted(names))})"
                )
        for name, command in config.resolve_commands({n: n for n in names}).items():
            if name in seen:
                problems.append(f"Befehl '{name}' ist doppelt vergeben ({seen[name]} und {path.name})")
            seen[name] = path.name


def main():
    used = check_texts()
    check_unused(used)
    check_commands()

    for note in notes:
        print(f"ℹ️ {note}")
    if problems:
        print()
        for problem in problems:
            print(f"❌ {problem}")
        print(f"\n{len(problems)} Problem(e) gefunden.")
        return 1
    print("✅ Konfiguration und Code passen zusammen.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
