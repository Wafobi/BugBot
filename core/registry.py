# registry.py
# Findet die Plattform- und Feature-Pakete und baut sie auf.
#
# Zwei Konventionen, dieselbe Mechanik:
#   platforms/<name>/platform.py mit create_platform() -> core.platform.Platform
#   features/<name>/feature.py   mit create_feature()  -> core.feature.Feature
#
# Mehr braucht es nicht: einen Ordner anlegen, die eine Datei schreiben, fertig. Weder
# bugbot.py noch core müssen dafür angefasst werden.
#
# Features gibt es an zwei Orten, und der Ort sagt, wovon sie abhängen:
#
#   features/<name>/              neutral - kommt mit jeder Plattform zurecht, weil es nur
#                                 Topics benutzt, die jede publizieren kann (moderation,
#                                 stats, levels, chat_log).
#   platforms/<p>/features/<name>/ plattformeigen - lebt von Ereignissen, die es nur auf
#                                 dieser einen Plattform gibt (stream_sessions und raw_log
#                                 hängen an STREAM_*/RAW_EVENT, die nur Twitch meldet).
#                                 Diese werden nur geladen, wenn ihre Plattform überhaupt
#                                 geladen wird - ein Discord-only-Lauf schleppt sie nicht
#                                 mit.
#
# Für alles danach ist der Unterschied wieder weg: ein plattformeigenes Feature landet im
# selben Bus, meldet seine Fähigkeiten genauso an, und ein neutrales Feature darf sie über
# `requires` anfordern (chat_log braucht SESSIONS, das von Twitch kommt). Was fehlt, wird
# sauber übersprungen - importiert wird über Paketgrenzen hinweg nach wie vor nichts.

import importlib
import os
from pathlib import Path

from . import events
from . import feature as feature_api
from . import platform as platform_api

PLATFORM_PACKAGE = "platforms"
FEATURE_PACKAGE = "features"
ROOT = Path(__file__).resolve().parent.parent


def _module_dirs(directory, module_name):
    """Namen aller Unterordner mit der erwarteten Moduldatei, alphabetisch."""
    if not directory.is_dir():
        return []
    return sorted(
        entry.name
        for entry in directory.iterdir()
        if entry.is_dir() and (entry / f"{module_name}.py").is_file()
    )


def _select(names, env_var):
    """Filtert die gefundenen Namen über eine Umgebungsvariable - schaltet einzelne ab,
    ohne den Ordner zu löschen oder Code anzufassen: z.B. "nur Twitch" beim Testen, oder ein
    Deploy ohne Level-System."""
    selected = os.environ.get(env_var, "").strip()
    if not selected:
        return names

    wanted = [n.strip().lower() for n in selected.split(",") if n.strip()]
    unknown = [n for n in wanted if n not in names]
    if unknown:
        print(f"⚠️ {env_var} nennt unbekannte(n) Eintrag/Einträge: {', '.join(unknown)}")
    return [n for n in names if n in wanted]


def platform_sources(root=None):
    base = Path(root) if root else ROOT
    names = _select(_module_dirs(base / PLATFORM_PACKAGE, "platform"), "BUGBOT_PLATFORMS")
    return [(PLATFORM_PACKAGE, name) for name in names]


def feature_sources(root=None):
    """[(Paketpfad, Name), ...] aller Features: erst die neutralen aus features/, dann die
    plattformeigenen der Plattformen, die tatsächlich geladen werden.

    Die Namen müssen über beide Quellen hinweg eindeutig sein - sie sind der Schlüssel im
    Feature-Verzeichnis des Bus. Ein Doppelter wird gemeldet und übersprungen, statt später
    beim Registrieren den Start abzubrechen."""
    base = Path(root) if root else ROOT
    sources = [(FEATURE_PACKAGE, name) for name in _module_dirs(base / FEATURE_PACKAGE, "feature")]
    for _, platform in platform_sources(root):
        directory = base / PLATFORM_PACKAGE / platform / FEATURE_PACKAGE
        package = f"{PLATFORM_PACKAGE}.{platform}.{FEATURE_PACKAGE}"
        sources += [(package, name) for name in _module_dirs(directory, "feature")]

    unique, seen = [], {}
    for package, name in sources:
        if name in seen:
            print(f"⚠️ Feature '{name}' gibt es doppelt ({seen[name]} und {package}) - {package} wird ignoriert.")
            continue
        seen[name] = package
        unique.append((package, name))

    wanted = _select([name for _, name in unique], "BUGBOT_FEATURES")
    return [(package, name) for package, name in unique if name in wanted]


def platform_names(root=None):
    return [name for _, name in platform_sources(root)]


def feature_names(root=None):
    return [name for _, name in feature_sources(root)]


def owner_of(package):
    """Die Plattform, der ein Feature gehört - abgelesen am Ort: "platforms.discord.features"
    -> "discord". Für die neutralen Features aus features/ leer.

    Damit muss ein plattformeigenes Feature den Namen seines Dienstes nirgends
    hinschreiben (siehe core/feature.py:Feature.owner). Voraussetzung ist, dass der
    Ordnername und Platform.name übereinstimmen - was ohnehin gilt, weil auch die
    Konfigurationsdatei so gefunden wird; load() sagt es, falls doch einmal nicht."""
    parts = package.split(".")
    if len(parts) >= 3 and parts[0] == PLATFORM_PACKAGE and parts[2] == FEATURE_PACKAGE:
        return parts[1]
    return ""


def _instantiate(sources, module_name, factory_name, expected_type, label):
    """Importiert die Pakete und ruft ihre Fabrikfunktion. Was sich nicht laden lässt
    (fehlendes Token in der .env, kaputter Import), wird laut übersprungen statt den
    ganzen Bot mitzunehmen - der Rest läuft weiter."""
    instances = []
    for package, name in sources:
        try:
            module = importlib.import_module(f"{package}.{name}.{module_name}")
            instance = getattr(module, factory_name)()
        except Exception as e:
            print(f"⚠️ {label} '{name}' konnte nicht geladen werden, wird übersprungen: {e!r}")
            continue
        if not isinstance(instance, expected_type):
            print(f"⚠️ {package}.{name}.{module_name}.{factory_name}() liefert kein {expected_type.__name__}-Objekt - übersprungen.")
            continue
        instances.append((package, name, instance))
    return instances


def load(names=None, bus=None):
    """Importiert und instanziiert die Plattformen und registriert sie am Bus. Kommt
    keine einzige zustande, ist das ein Fehler: dann hätte der Bot nichts zu tun."""
    bus = bus if bus is not None else events.bus
    sources = platform_sources() if names is None else [(PLATFORM_PACKAGE, name) for name in names]
    loaded = _instantiate(
        sources, "platform", "create_platform", platform_api.Platform, "Plattform",
    )
    platforms = []
    for _, directory, instance in loaded:
        # Ordnername und Platform.name müssen übereinstimmen: an beidem hängt etwas. Die
        # plattformeigenen Features leiten ihren `owner` aus dem Ordner ab (siehe
        # owner_of) und vergleichen ihn mit den Namen in den Meldungen. Weichen die
        # beiden ab, filtert so ein Feature auf einen Namen, den es nicht gibt, und tut
        # von da an still gar nichts - deshalb hier eine Zeile statt später ein Rätsel.
        if instance.name != directory:
            print(f"⚠️ platforms/{directory} nennt sich '{instance.name}' - plattformeigene "
                  f"Features darunter finden ihre Plattform darüber nicht wieder.")
        bus.register(instance)
        platforms.append(instance)
        abilities = ", ".join(sorted(instance.capabilities)) or "keine Fähigkeiten"
        print(f"🔌 Plattform '{instance.name}' geladen ({abilities}).")

    if not platforms:
        raise RuntimeError("Keine einzige Plattform konnte geladen werden - siehe Warnungen oben.")
    return platforms


def _in_dependency_order(features):
    """Sortiert Features so, dass jedes nach denen kommt, deren Fähigkeiten es laut
    `requires` braucht. Eines, dessen Bedarf niemand deckt, fällt raus - ein halb
    funktionierendes Feature (stats ohne Ablage) wäre schlimmer als gar keines.

    `optional` zählt für die Reihenfolge mit, aber nur solange es überhaupt jemanden gibt,
    der die Fähigkeit anbietet: wer die Stream-Sessions mitnimmt, wenn es sie gibt, muss
    nach ihnen eingerichtet werden - sonst schaut sein setup() in ein Verzeichnis, in dem
    sie noch nicht stehen. Fehlt der Anbieter ganz, ändert das gar nichts.

    Bewusst simpel gehalten: bei den drei, vier Features eines Chatbots ist eine
    wiederholte Auswahlrunde übersichtlicher als ein echter Topologie-Sortierer, und ein
    Zyklus fällt genauso auf (es bleibt etwas übrig, das nie dran kommt)."""
    offered = set().union(*(f.provides for f in features)) if features else set()
    ordered, satisfied, pending = [], set(), list(features)
    while pending:
        ready = [
            f for f in pending
            if f.requires <= satisfied and (f.optional & offered) <= satisfied
        ]
        if not ready:
            # Nur noch optionale Wünsche offen (oder ein Zyklus darin): die kosten
            # niemandem den Platz, also weiter ohne sie.
            ready = [f for f in pending if f.requires <= satisfied]
        if not ready:
            for f in pending:
                missing = ", ".join(sorted(f.requires - satisfied))
                print(f"⚠️ Feature '{f.name}' übersprungen: keine Quelle für {missing}.")
            break
        for f in ready:
            ordered.append(f)
            satisfied |= f.provides
        pending = [f for f in pending if f not in ready]
    return ordered


async def load_features(names=None, bus=None):
    """Importiert die Features, bringt sie in Abhängigkeitsreihenfolge und richtet jedes
    per setup() ein - alles vor dem Start der Plattformen, damit die Abonnements stehen,
    bevor die erste Nachricht hereinkommt.

    Anders als bei den Plattformen ist "gar kein Feature" kein Fehler: ein Bot, der nur
    moderiert und nichts mitschreibt, ist eine gültige Konfiguration."""
    bus = bus if bus is not None else events.bus
    sources = feature_sources()
    if names is not None:
        sources = [(package, name) for package, name in sources if name in names]
    loaded = _instantiate(sources, "feature", "create_feature", feature_api.Feature, "Feature")
    for package, _, instance in loaded:
        # Vor setup(), damit ein Feature beides schon dort benutzen kann: wem es gehört
        # und an welchem Bus es hängt (siehe core/feature.py).
        instance.owner = owner_of(package)
        instance.bus = bus
    features = [instance for _, _, instance in loaded]

    ready = []
    for instance in _in_dependency_order(features):
        try:
            await instance.setup(bus)
        except Exception as e:
            print(f"⚠️ Feature '{instance.name}' konnte nicht eingerichtet werden, wird übersprungen: {e!r}")
            continue
        bus.register_feature(instance)
        ready.append(instance)
        abilities = ", ".join(sorted(instance.provides)) or "keine Fähigkeiten"
        belongs = f", gehört zu '{instance.owner}'" if instance.owner else ""
        print(f"🧩 Feature '{instance.name}' geladen ({abilities}{belongs}).")
    return ready
