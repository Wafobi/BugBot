# feature.py
# Variables as a feature (capability VARIABLES).
#
# Fills the {placeholders} in the static commands from twitch.json/discord.json. Previously
# the call sites held a bare `.format(u=user_name)`: {u} was the only possible placeholder,
# and everything else - a {zeit} instead of {time}, a typo - raised a KeyError that landed
# far above as "error while processing". The command simply stayed silent in chat.
#
# A feature and not a method in core/runtime_config.py, because what stands here is exactly
# what makes a feature: it is the same on every platform, it brings its own configuration,
# and it may be absent. Without this feature {u} and {user} keep working (the platform knows
# those itself), only {time} and the self-defined variables then stay standing as text - see
# platforms/twitch/bot.py:_render.
#
# Two sources, the second beating the first:
#
#   "variables" fixed strings: {steam}, {socials}, whatever the operator happens to need.
#   "python"    one Python expression per variable, evaluated when the command is used.
#
# {time} and {date} are nothing special either, but two perfectly ordinary expressions in
# variables.json - not a single variable name is left in here. Anyone wanting to know what
# exists reads the file, and anyone wanting the time formatted differently changes the
# expression there, instead of hunting for a formatting key that only the code knows. So
# that a broken or missing file does not switch off the clock as well, exactly those two are
# additionally in DEFAULTS below (see core/runtime_config.py: defaults are what a module
# needs in order to work).
#
# Last, the platform lays its context on top ({u}, {user}, {channel}) - that comes from the
# message and not from the file, which is why no configuration can misadjust it.

import asyncio
import locale
import math
import random
import time as time_module
from datetime import date as date_type, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from core import feature as feature_api, runtime_config

# Only the bare essentials; everything else is in variables.json and can be changed there.
# {time} and {date} are in there too - here a second time, so that a command with the time
# still answers even when the file is missing or somebody breaks it. The formats themselves
# are the operator's choice, which is why they stay as the shipped file has them.
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
        #: name -> (config version, timestamp, value). See _cached.
        self._cache = {}
        #: Locale set most recently, so setlocale does not run on every message.
        self._locale = None

    # --- Time ---------------------------------------------------------------------------

    def _apply_locale(self):
        """Sets LC_TIME to the locale from variables.json - the spelled-out weekdays and
        months depend on it (strftime "%A", "%B").

        Needed because the container image only knows C and C.utf8: without this call
        now.strftime('%A') returns an English "Sunday", on a perfectly correctly computed
        date. The locale has to have been generated in the image (see Dockerfile, ARG
        LOCALE); if it was not, English it stays, and the log says once why.

        Process-wide, as setlocale simply is. That is right here: a bot speaks one language,
        and whoever changes it wants it changed everywhere."""
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
                f"locale {name!r} is not available, weekdays stay English - {e}. "
                f"In the container it is generated at build time (Dockerfile, ARG LOCALE).",
            )

    def zone(self):
        """The timezone from variables.json ("timezone", e.g. "Europe/Berlin"), or None
        when there is none or an unknown one - the process's own time then applies.

        The timezone lives in the configuration and not in the container environment because
        it belongs to what an operator sets - and because, like everything else here, it is
        changeable at runtime. The environment is the worse place for it: if it is missing
        there, the container is on UTC and therefore two hours early in summer, without an
        error showing up anywhere.

        Belongs to the VARIABLES capability and not only to now(), because points in time
        that are not "now" should be printed in this timezone as well - the end of an ad
        break, say, which Twitch reports in UTC (platforms/twitch/bot.py). Otherwise the same
        place would have to be configured a second time, and the two could drift apart."""
        name = str(self.config.get("timezone", "") or "").strip()
        if not name:
            return None
        try:
            return ZoneInfo(name)
        except (ZoneInfoNotFoundError, ValueError, OSError) as e:
            self.config.complain("timezone", f"timezone {name!r} is unknown, using the server's - {e}")
            return None

    def now(self):
        """Now, in the timezone from variables.json - see zone().

        datetime.now(None) is the process's naive local time, i.e. exactly the case "no
        timezone entered"; which is why no case distinction is needed here."""
        return datetime.now(self.zone())

    # --- Resolving ----------------------------------------------------------------------

    async def resolve(self, template, **context):
        """The values for the {placeholders} that actually occur in `template`.

        Only the occurring ones: otherwise every `!discord` would also run every Python
        expression somebody stored for a completely different command.

        `context` is what the platform contributes ({u}, {user}, {channel}) - it is available
        to the expressions as a variable and in the end beats everything from the file."""
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
        """The value of a single variable, together with everything it needs in turn.

        Variables may use variables - anything else would be an arbitrary limit: whoever has
        defined {steam} wants to be able to write it inside {chef} too, rather than writing
        the URL a second time and forgetting one of the two next time round. So this does not
        stubbornly walk a list, but resolves backwards from the variable asked for, as deep
        as necessary.

        `resolving` are the variables already begun on the way here. If the one asked for is
        in there, somebody has defined themselves in a circle; without this check that ended
        in an endless loop, i.e. with a stalled bot instead of an error message."""
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
                f"variable '{name}' ends up using itself "
                f"({' -> '.join([*resolving, name])}) - the placeholder stays standing",
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
        """A fixed text from "variables", with its own {placeholders} filled in.

        Without this step a {steam} inside a fixed text would arrive in chat verbatim -
        silently, and with a brace in the middle of the sentence, because format() replaces
        only once and not what comes out of it."""
        inner = {}
        for dependency in runtime_config.placeholders(text):
            value = await self._value(dependency, context, values, resolving)
            if value is not None:
                inner[dependency] = value
        return self.config.render(text, **{**context, **inner})

    # --- Python expressions -------------------------------------------------------------

    async def _evaluate(self, key, code, context, values, resolving):
        """Evaluates an expression from variables.json. None means "did not work out"; the
        caller then leaves the placeholder standing rather than posting nonsense.

        Deliberately an *expression* and not statements: compile(..., "eval") allows no
        import, no assignment and no writing of files through the back door. It is
        nevertheless not a sandbox and is not meant to be one - the expression runs in the
        bot process, with its rights, and whoever may write the file may already do
        everything anyway. What counts here is something else: that an *accident* - a typo, a
        division by zero, something slow - does not take the bot with it.

        Hence three precautions, each against a different mishap:
          * every exception is caught and reported once,
          * the evaluation runs in a thread with a time limit, so that something hanging does
            not stall the entire message processing,
          * the result is remembered briefly (cache_seconds), otherwise every chat message
            with this command starts the evaluation afresh."""
        cached = self._cached(key)
        if cached is not None:
            return cached

        timeout = _positive(self.config.get("python_timeout_seconds", 2), 2)
        try:
            compiled = compile(str(code).strip(), f"<{self.config.path.name}:{key}>", "eval")
        except SyntaxError as e:
            self.config.complain(f"python:{key}", f"variable '{key}': {e.msg} - the expression stays unused")
            return None

        # Which other variables the expression uses, it says itself: co_names are the names
        # it looks up. Only those are resolved - so `steam + '?l=de'` costs exactly that one
        # variable and not the whole file, and an expression using none triggers nothing at
        # all.
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
            # The thread keeps running - a running expression cannot be cancelled in
            # Python. The bot merely stops waiting for it, and that is the point.
            self.config.complain(f"python:{key}", f"variable '{key}' takes longer than {timeout}s - skipped")
            return None
        except Exception as e:
            self.config.complain(f"python:{key}", f"variable '{key}' failed: {e!r}")
            return None

        text = "" if value is None else str(value)
        self._remember(key, text)
        return text

    def _cached(self, key):
        """The remembered value, as long as it is fresh. Editing the file invalidates it
        immediately (config.version) - otherwise you would wait another cache_seconds after a
        change for it to take effect, which is precisely what must not happen here."""
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


# What an expression has available without importing. Kept small and aimed at what
# variables in a chat are for: time, date, a little arithmetic, a little randomness.
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
