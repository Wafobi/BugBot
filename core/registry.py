# registry.py
# Finds the platform and feature packages and builds them up.
#
# Two conventions, the same mechanics:
#   platforms/<name>/platform.py with create_platform() -> core.platform.Platform
#   features/<name>/feature.py   with create_feature()  -> core.feature.Feature
#
# Nothing more is needed: create a folder, write the one file, done. Neither bugbot.py nor
# core has to be touched for it.
#
# Features exist in two places, and the place says what they depend on:
#
#   features/<name>/              neutral - copes with any platform, because it only uses
#                                 topics every platform can publish (moderation, stats,
#                                 levels, chat_log).
#   platforms/<p>/features/<name>/ platform-owned - lives off events that exist only on
#                                 this one platform (stream_sessions and raw_log hang on
#                                 STREAM_*/RAW_EVENT, which only Twitch reports). These are
#                                 only loaded when their platform is loaded at all - a
#                                 Discord-only run does not drag them along.
#
# For everything afterwards the difference is gone again: a platform-owned feature lands in
# the same bus, declares its capabilities just the same, and a neutral feature may request
# them via `requires` (chat_log needs SESSIONS, which comes from Twitch). What is missing
# gets skipped cleanly - nothing is imported across package boundaries, as before.

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
    """Names of all subfolders holding the expected module file, alphabetically."""
    if not directory.is_dir():
        return []
    return sorted(
        entry.name
        for entry in directory.iterdir()
        if entry.is_dir() and (entry / f"{module_name}.py").is_file()
    )


def _select(names, env_var):
    """Filters the discovered names through an environment variable - switches individual
    ones off without deleting the folder or touching code: e.g. "Twitch only" while
    testing, or a deploy without the level system."""
    selected = os.environ.get(env_var, "").strip()
    if not selected:
        return names

    wanted = [n.strip().lower() for n in selected.split(",") if n.strip()]
    unknown = [n for n in wanted if n not in names]
    if unknown:
        print(f"⚠️ {env_var} names unknown entry/entries: {', '.join(unknown)}")
    return [n for n in names if n in wanted]


def platform_sources(root=None):
    base = Path(root) if root else ROOT
    names = _select(_module_dirs(base / PLATFORM_PACKAGE, "platform"), "BUGBOT_PLATFORMS")
    return [(PLATFORM_PACKAGE, name) for name in names]


def feature_sources(root=None):
    """[(package path, name), ...] of all features: first the neutral ones from features/,
    then the platform-owned ones of the platforms that are actually loaded.

    The names have to be unique across both sources - they are the key in the bus's feature
    directory. A duplicate is reported and skipped, rather than aborting startup later
    during registration."""
    base = Path(root) if root else ROOT
    sources = [(FEATURE_PACKAGE, name) for name in _module_dirs(base / FEATURE_PACKAGE, "feature")]
    for _, platform in platform_sources(root):
        directory = base / PLATFORM_PACKAGE / platform / FEATURE_PACKAGE
        package = f"{PLATFORM_PACKAGE}.{platform}.{FEATURE_PACKAGE}"
        sources += [(package, name) for name in _module_dirs(directory, "feature")]

    unique, seen = [], {}
    for package, name in sources:
        if name in seen:
            print(f"⚠️ Feature '{name}' exists twice ({seen[name]} and {package}) - {package} is ignored.")
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
    """The platform a feature belongs to - read off its location:
    "platforms.discord.features" -> "discord". Empty for the neutral features in features/.

    This is what lets a platform-owned feature avoid writing the name of its service
    anywhere (see core/feature.py:Feature.owner). It relies on the folder name and
    Platform.name matching - which holds anyway, because the configuration file is found
    the same way; load() says so if it ever does not."""
    parts = package.split(".")
    if len(parts) >= 3 and parts[0] == PLATFORM_PACKAGE and parts[2] == FEATURE_PACKAGE:
        return parts[1]
    return ""


def _instantiate(sources, module_name, factory_name, expected_type, label):
    """Imports the packages and calls their factory function. Whatever fails to load
    (missing token in the .env, broken import) is skipped loudly instead of taking the
    whole bot with it - the rest keeps running."""
    instances = []
    for package, name in sources:
        try:
            module = importlib.import_module(f"{package}.{name}.{module_name}")
            instance = getattr(module, factory_name)()
        except Exception as e:
            print(f"⚠️ {label} '{name}' could not be loaded, skipping it: {e!r}")
            continue
        if not isinstance(instance, expected_type):
            print(f"⚠️ {package}.{name}.{module_name}.{factory_name}() returns no {expected_type.__name__} object - skipped.")
            continue
        instances.append((package, name, instance))
    return instances


def load(names=None, bus=None):
    """Imports and instantiates the platforms and registers them on the bus. If not a
    single one comes up, that is an error: the bot would have nothing to do."""
    bus = bus if bus is not None else events.bus
    sources = platform_sources() if names is None else [(PLATFORM_PACKAGE, name) for name in names]
    loaded = _instantiate(
        sources, "platform", "create_platform", platform_api.Platform, "Platform",
    )
    platforms = []
    for _, directory, instance in loaded:
        # Folder name and Platform.name have to match: things hang on both. The
        # platform-owned features derive their `owner` from the folder (see owner_of) and
        # compare it with the names in the notifications. If the two differ, such a feature
        # filters on a name that does not exist and quietly does nothing from then on -
        # hence one line here rather than a riddle later.
        if instance.name != directory:
            print(f"⚠️ platforms/{directory} calls itself '{instance.name}' - platform-owned "
                  f"features below it will not find their platform again by that name.")
        bus.register(instance)
        platforms.append(instance)
        abilities = ", ".join(sorted(instance.capabilities)) or "no capabilities"
        print(f"🔌 Platform '{instance.name}' loaded ({abilities}).")

    if not platforms:
        raise RuntimeError("Not a single platform could be loaded - see the warnings above.")
    return platforms


def _in_dependency_order(features):
    """Sorts features so that each comes after those whose capabilities it needs per
    `requires`. One whose needs nobody covers drops out - a half-working feature (stats
    without storage) would be worse than none at all.

    `optional` counts towards the order too, but only as long as anybody offers the
    capability at all: whoever takes the stream sessions along when they exist has to be
    set up after them - otherwise their setup() looks into a directory that does not hold
    them yet. If the provider is missing entirely, it changes nothing.

    Deliberately kept simple: with the three or four features of a chat bot, a repeated
    selection round is clearer than a real topological sort, and a cycle stands out just as
    well (something is left over that never gets its turn)."""
    offered = set().union(*(f.provides for f in features)) if features else set()
    ordered, satisfied, pending = [], set(), list(features)
    while pending:
        ready = [
            f for f in pending
            if f.requires <= satisfied and (f.optional & offered) <= satisfied
        ]
        if not ready:
            # Only optional wishes left open (or a cycle among them): those cost nobody
            # their place, so carry on without them.
            ready = [f for f in pending if f.requires <= satisfied]
        if not ready:
            for f in pending:
                missing = ", ".join(sorted(f.requires - satisfied))
                print(f"⚠️ Feature '{f.name}' skipped: no source for {missing}.")
            break
        for f in ready:
            ordered.append(f)
            satisfied |= f.provides
        pending = [f for f in pending if f not in ready]
    return ordered


async def load_features(names=None, bus=None):
    """Imports the features, brings them into dependency order and sets each one up via
    setup() - all before the platforms start, so the subscriptions are in place before the
    first message comes in.

    Unlike with the platforms, "no feature at all" is not an error: a bot that only
    moderates and records nothing is a valid configuration."""
    bus = bus if bus is not None else events.bus
    sources = feature_sources()
    if names is not None:
        sources = [(package, name) for package, name in sources if name in names]
    loaded = _instantiate(sources, "feature", "create_feature", feature_api.Feature, "Feature")
    for package, _, instance in loaded:
        # Before setup(), so a feature can already use both there: who it belongs to and
        # which bus it hangs on (see core/feature.py).
        instance.owner = owner_of(package)
        instance.bus = bus
    features = [instance for _, _, instance in loaded]

    ready = []
    for instance in _in_dependency_order(features):
        try:
            await instance.setup(bus)
        except Exception as e:
            print(f"⚠️ Feature '{instance.name}' could not be set up, skipping it: {e!r}")
            continue
        bus.register_feature(instance)
        ready.append(instance)
        abilities = ", ".join(sorted(instance.provides)) or "no capabilities"
        belongs = f", belongs to '{instance.owner}'" if instance.owner else ""
        print(f"🧩 Feature '{instance.name}' loaded ({abilities}{belongs}).")
    return ready
