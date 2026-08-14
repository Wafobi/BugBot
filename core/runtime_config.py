# runtime_config.py
# The configuration at runtime: one JSON file per platform and per feature, next to the
# code that reads it. Each is re-read when it changes - so rules, texts, names, thresholds
# and timings can be changed while the bot is running.
#
# A LiveConfig does three things, and all three for the same reason: whatever another
# operator wants differently, they should be able to change without touching Python.
#
#   get()/section()  values and sections, with the DEFAULTS from the code underneath.
#                    The file therefore need not be complete - what is missing comes from
#                    the code, and a deleted line is not a crash but the default.
#   text()           the bot's texts. Every sentence a human gets to see has a key and
#                    lives under "texts" in the JSON. A typo by the operator must never
#                    take down the handler that is printing it - in case of doubt text()
#                    prints the default and reports once what was wrong.
#   commands()       command names: rename, alias, disable. Someone who already has a bot
#                    with !uptime in chat should not get a second one here.

import json
from pathlib import Path
from string import Formatter


def for_package(module_file, defaults=None):
    """The configuration next to the module that reads it: features/stats/stats.json for
    features/stats/feature.py.

    One package, one file, same name - so nobody has to guess which JSON belongs to which
    folder, and an additional feature simply brings its configuration along without core
    having to know about it."""
    directory = Path(module_file).resolve().parent
    return LiveConfig(directory / f"{directory.name}.json", defaults)


def deep_merge(base, override):
    """Copy of base with override laid into it. Nested dicts are merged rather than
    replaced: setting *one* threshold in the JSON does not lose the others from the
    defaults. Lists count as a single value - a list in the JSON replaces the default list
    entirely, because otherwise you could never take anything away."""
    merged = dict(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def placeholders(template):
    """The names of all {…} placeholders in a template."""
    return {name for _, name, _, _ in Formatter().parse(template) if name}


class _Unfilled(dict):
    """A dictionary for format_map that leaves an unknown placeholder standing unchanged
    instead of raising a KeyError - and remembers which ones those were, so the caller can
    report them. See LiveConfig.render."""

    def __init__(self, values):
        super().__init__(values)
        self.missing = set()

    def __missing__(self, key):
        self.missing.add(key)
        return "{" + key + "}"


class LiveConfig:
    """Loads a JSON file and checks its mtime on every access to see whether it has changed
    - no polling task needed, a stat() call is cheap enough to run per chat message. If the
    file is missing or broken, the last known good state is kept (resp. the defaults on the
    very first load) rather than crashing the bot.

    Two layers lie on top of each other, the upper beating the lower:

        `defaults`     what the code brings along. For the few values without which a
                       module cannot work - so that it runs without the file too.
        current file   what is in it right now.

    What is in the file is therefore also what applies: a deleted command is gone, a
    deleted threshold falls back to the value from the code. The whole point of this class
    hangs on exactly that - a change you cannot undo without restarting the bot is not a
    runtime change.

    There is a third layer nonetheless, but only for texts and only as a fallback text:
    `_baseline`, the content at the first successful load (i.e. the file shipped in the
    repository). It keeps text() alive when a key is missing - see there. Texts are the one
    case where that is right: a feature consisting almost entirely of sentences would
    otherwise have to carry them twice, once in Python and once in JSON. For everything
    else the same underlay would only be a way of keeping deleted things alive."""

    def __init__(self, path, defaults=None):
        self._path = Path(path)
        self._defaults = defaults or {}
        self._baseline = None
        self._mtime = None
        self._file_data = {}
        self._data = dict(self._defaults)
        self._complained = set()

        #: Counts up on every successful reload. Anyone deriving something expensive from
        #: the configuration (the command table in the bus) uses it to notice that their
        #: cached state is stale - see core/events.py.
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
            print(f"⚠️ Could not load {self._path.name}, keeping the previous state: {e}")
            return
        if self._baseline is None:
            self._baseline = data
        self._mtime = mtime
        self._file_data = data
        # Deliberately without _baseline in between: the file is the truth, otherwise
        # nothing that was once in it at startup could ever be deleted. Missing texts are
        # caught by text() itself via _baseline - the only place where that is wanted.
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
        """A subsection as a dict - always one, even when the key is missing or somebody
        has written something else into it."""
        value = self.get(key)
        return value if isinstance(value, dict) else {}

    # --- Texts -------------------------------------------------------------------------

    def text(self, key, **values):
        """The text for a key, filled with its {placeholders}.

        Robust on purpose: this call sits in every command and every announcement, and the
        template comes from outside. Neither an unknown placeholder nor half a curly brace
        nor a missing key may leave the caller with an exception - in the worst case the
        default or the raw template is what shows up. It is still reported, but only once
        per key and file state, so a typo does not flood the log."""
        template = self.section("texts").get(key)
        fallback = ((self._baseline or self._defaults).get("texts") or {}).get(key)

        if template is None:
            template = fallback
        if template is None:
            self.complain(key, f"no text stored for '{key}'")
            return key
        if not isinstance(template, str):
            self.complain(key, f"text '{key}' is not text but {type(template).__name__}")
            template = fallback if isinstance(fallback, str) else key

        try:
            return template.format(**values)
        except (KeyError, IndexError, ValueError) as e:
            unknown = ", ".join(sorted(placeholders(template) - set(values))) or "?"
            self.complain(key, f"text '{key}' uses unknown placeholders ({unknown}) - {e}")

        # The default can still get it right when only the operator's own version is broken.
        if isinstance(fallback, str) and fallback != template:
            try:
                return fallback.format(**values)
            except (KeyError, IndexError, ValueError):
                pass
        return template

    # --- Static commands -----------------------------------------------------------------

    def render(self, template, **values):
        """Fills the {placeholders} of a static command from the JSON.

        Separate from text() and deliberately from a different source: text() fills
        templates whose values the code knows - it calls them with exactly those values.
        Here the operator decides both, template *and* placeholders, and a typo should not
        cost them the answer. Previously the call sites held a bare .format(u=...): a
        {zeit} instead of {time} raised a KeyError there, which landed far above as "error
        while processing", and the command stayed silent in chat.

        Where the values come from is deliberately not stated here - features/variables
        knows that, and the platform passes them in. This class only formats.

        An unknown placeholder costs only itself: it stays standing as {name} while
        everything else in the sentence is filled. That is the difference that counts when
        the variables feature is switched off - "It is {time}, @jens" is a halfway usable
        answer, "It is {time}, @{u}" is not."""
        unfilled = _Unfilled(values)
        try:
            filled = template.format_map(unfilled)
        except (IndexError, ValueError, AttributeError, TypeError) as e:
            # Broken template rather than a missing value: half a brace, a {0}, a
            # {name.attribute} pointing at nothing. Nothing to save there, the text stays raw.
            self.complain(f"render:{template[:60]}", f"command is not a valid template - {e}")
            return template
        if unfilled.missing:
            available = ", ".join("{%s}" % name for name in sorted(values)) or "none"
            self.complain(
                f"render:{template[:60]}",
                f"command uses unknown placeholders "
                f"({', '.join('{%s}' % name for name in sorted(unfilled.missing))}) - "
                f"known are {available}",
            )
        return filled

    def color(self, key, default=0x3498DB):
        """A colour from the "colors" section as a number, the way Announcement.color
        expects it. In the JSON it reads "#2ECC71" - the way it is written everywhere else
        - but a plain number works too."""
        value = self.section("colors").get(key, default)
        if isinstance(value, int):
            return value
        try:
            return int(str(value).lstrip("#"), 16)
        except ValueError:
            self.complain(f"color:{key}", f"colour '{key}': {value!r} is not a colour like \"#2ECC71\"")
            return default

    def complain(self, key, message):
        if key in self._complained:
            return
        self._complained.add(key)
        print(f"⚠️ {self._path.name}: {message}")

    # --- Command names -------------------------------------------------------------------

    def command_names(self, section="command_names"):
        """{default name: (name, alias, ...)} from the JSON, empty when nothing was
        renamed. Three spellings are allowed, because all three read naturally:

            "!uptime": "!live"                     rename
            "!uptime": ["!live", "!howlong"]       rename + aliases
            "!uptime": false                       disable
            "!uptime": {"name": "!live", "aliases": ["!howlong"], "enabled": true}

        Names without "!" get one added - "live" and "!live" should mean the same."""
        resolved = {}
        for default_name, setting in self.section(section).items():
            # Keys with a leading underscore are explanations for the human editing the
            # file (JSON has no comments) - not a command.
            if default_name.startswith("_"):
                continue
            names = _normalize_command_setting(setting)
            if names is None:
                self.complain(f"command:{default_name}",
                               f"command '{default_name}': {setting!r} is not a valid setting")
                continue
            resolved[_with_prefix(default_name)] = names
        return resolved

    def resolve_commands(self, declared, section="command_names"):
        """Maps {default name: value} onto {actual name: value} - including aliases
        pointing at the same value. Disabled commands drop out.

        `declared` is left untouched; the order of the default names is preserved so that
        listings (!commands) do not look different after every reload."""
        overrides = self.command_names(section)
        resolved = {}
        for default_name, value in declared.items():
            names = overrides.get(_with_prefix(default_name), (_with_prefix(default_name),))
            for name in names:
                if name in resolved:
                    self.complain(f"collision:{name}",
                                   f"command name '{name}' is assigned twice - the later one is ignored")
                    continue
                resolved[name] = value
        return resolved


def _with_prefix(name):
    name = str(name).strip().lower()
    return name if name.startswith("!") else f"!{name}"


def _normalize_command_setting(setting):
    """(name, alias, ...) or () for "disabled"; None when the setting is unusable - the
    caller reports that and leaves the default name standing."""
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
