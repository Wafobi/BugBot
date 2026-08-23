# events.py
# The bus: message distribution *and* directory of all platforms and features. Everything
# the parts of the bot know about each other goes through here - there are no direct
# imports between platform packages, or from a platform into a feature, any more.
#
# Three routes:
#   publish(topic, **payload)  "this happened" - subscribers update their own state or
#       record it. All recording runs through this: a platform only reports that a message
#       arrived, and does not know whether a feature is listening. publish() returns the
#       subscribers' return values - so a feature can answer too (see STREAM_END, which
#       returns the stream's figures).
#   announce(announcement)     "post this wherever you can" - goes to every platform with
#       the ANNOUNCE capability. Each decides for itself whether and how it presents the
#       `kind`. Additionally publishes under the topic announcement.kind.
#   feature(...)/command(...)  the pull directory: where a platform needs an *answer*
#       (moderation verdict) or collects the features' commands.

import asyncio
import logging
from collections import defaultdict
from dataclasses import replace

from . import platform as platform_api

log = logging.getLogger(__name__)


# --- Topics ------------------------------------------------------------------------
# The shared vocabulary between platforms (which publish) and features (which subscribe).
# Deliberately phrased platform-neutrally - "an event with a type and an amount" rather
# than "a Twitch cheer".

# Every incoming message, BEFORE moderation. For the complete record: the messages deleted
# later are precisely the ones you want to be able to read back afterwards.
#   payload: message (feature.Message)
MESSAGE = "message.received"

# Message has passed moderation. Everything that should not count an offence (message
# counters, XP) hangs here rather than on MESSAGE.
#   payload: message (feature.Message)
MESSAGE_ACCEPTED = "message.accepted"

# A command was executed.  payload: platform, command, user_name
COMMAND = "command.used"

# A moderation action was carried out (by the bot or by a human).
#   payload: platform, user_name, reason, action ("delete"/"timeout"/"ban"/"warn"/"unban")
MOD_ACTION = "moderation.action"

# A moderator/broadcaster cleared the whole chat through the platform's own UI - distinct
# from MOD_ACTION, which is about one user. Nothing else about MESSAGE_ACCEPTED changes: the
# messages already recorded stay recorded, this only tells presentation-only listeners (the
# chat panel) that the platform itself just wiped the slate.
#   payload: platform
CHAT_CLEARED = "chat.cleared"

# A typed live event: follow, sub, gift sub, cheer, raid, hype train, ...
#   payload: platform, event_type, user_name, amount (bits/count/level/0)
PLATFORM_EVENT = "platform.event"

# Raw log: a platform notification in its original state, even when there is no handler for
# it (yet).  payload: platform, event_type, payload
RAW_EVENT = "platform.raw"

# Stream state. STREAM_END returns the closing figures through the subscribers' return
# values (as a tuple of platform.Field), from which the platform builds its report.
#   STREAM_START payload: platform, title, category
#   STREAM_END   payload: platform
STREAM_START = "stream.start"
STREAM_END = "stream.end"

# The session is closed, its id is settled. Separate from STREAM_END, because the order
# would otherwise be a matter of luck: whoever evaluates the stream that just ended
# (highscores, closing report) needs the id *after* it was closed. The feature with the
# SESSIONS capability publishes this as soon as it has closed the session, and passes the
# subscribers' return values on to its own STREAM_END caller - so the platform still gets
# its closing fields as the return value of publish(STREAM_END).
#   payload: session_id
SESSION_ENDED = "session.ended"

# Title/category change within a stream.  payload: platform, title, category
STREAM_SEGMENT = "stream.segment"

# Viewer sample.  payload: platform, count
VIEWERS = "stream.viewers"

# Ad break.  payload: platform, duration_seconds
AD_BREAK = "stream.ad_break"

# A user levelled up.  payload: message (the triggering Message), level
LEVEL_UP = "level.up"


class EventBus:
    def __init__(self):
        self._platforms = {}
        self._features = {}
        self._commands = None
        self._commands_version = None
        self._handlers = defaultdict(list)

    # --- Platform registry ----------------------------------------------------------

    def register(self, platform):
        if platform.name in self._platforms:
            raise ValueError(f"platform '{platform.name}' is already registered")
        self._platforms[platform.name] = platform

    @property
    def platforms(self):
        return tuple(self._platforms.values())

    def get(self, name):
        return self._platforms.get(name)

    def with_capability(self, capability):
        return tuple(p for p in self._platforms.values() if p.supports(capability))

    def resolve_platforms(self, tokens):
        """The intended set of names from a list of capabilities and/or platform names.
        Empty list -> None, i.e. "all".

        This lets a restriction be expressed in a configuration file too, without naming a
        service: ["stream"] means "the platforms that report a stream" and is still right
        when that is a different one tomorrow. A name still works, but is reported when no
        loaded platform currently bears it - exactly the case that otherwise turns quietly
        into "never matches"."""
        if not tokens:
            return None
        known = {p.name for p in self._platforms.values()}
        resolved = set()
        for token in tokens:
            token = str(token).strip().lower()
            if not token:
                continue
            matching = self.with_capability(token)
            if matching:
                resolved |= {p.name for p in matching}
            elif token in known:
                resolved.add(token)
            elif token in platform_api.CAPABILITIES:
                # A valid capability that nobody here currently has - not an error, just
                # empty at the moment.
                continue
            else:
                log.warning(f"'{token}' is neither a loaded platform nor a capability "
                            f"({', '.join(sorted(platform_api.CAPABILITIES))}) - ignoring it.")
        return resolved

    async def wait_ready(self, timeout=None):
        """Waits until all registered platforms are ready. False on timeout - the caller
        then decides for itself whether to carry on regardless."""
        if not self._platforms:
            return True
        try:
            await asyncio.wait_for(
                asyncio.gather(*(p.wait_ready() for p in self.platforms)), timeout=timeout
            )
            return True
        except asyncio.TimeoutError:
            log.warning(f"Not all platforms were ready after {timeout}s.")
            return False

    # --- Feature registry -----------------------------------------------------------

    def register_feature(self, feature):
        if feature.name in self._features:
            raise ValueError(f"feature '{feature.name}' is already registered")
        self._features[feature.name] = feature
        # A feature should reach the directory of platforms even when it was not built via
        # core/registry.py (tests, a bot assembling its features itself). The registry sets
        # it before setup() as well - here is the latest moment at which it is certainly
        # right.
        if feature.bus is None:
            feature.bus = self
        self._commands = None

    @property
    def features(self):
        return tuple(self._features.values())

    def feature(self, name):
        return self._features.get(name)

    def features_with(self, capability):
        return tuple(f for f in self._features.values() if f.supports(capability))

    def feature_with(self, capability):
        """The first feature with this capability, or None. For the normal case where
        exactly one offers it (storage, levels) - anyone wanting to walk several (say,
        several moderation filters in a row) takes features_with."""
        found = self.features_with(capability)
        return found[0] if found else None

    def commands(self):
        """{command name: Command} of all features together. The platforms wire this into
        their own command resolution without knowing the features. On a collision the
        feature registered first wins - the conflict is reported rather than letting one of
        the two quietly disappear.

        The names are not necessarily the ones in the code: if a feature brings its own
        configuration, that file's "command_names" section may rename every command, give
        it aliases or switch it off (see core/runtime_config.py). This happens here and not
        in the features, so that none of them has to do anything for it - and the returned
        Command carries the *actual* name, so that command listings such as !commands do
        not show the names from the code.

        The result is cached: this runs per chat message. The cache expires as soon as one
        of the configurations involved has been reloaded - otherwise renaming, of all
        things, would be the one thing needing a restart."""
        version = self._command_config_version()
        if self._commands is not None and version == self._commands_version:
            return self._commands

        merged = {}
        for feature in self._features.values():
            declared = {command.name: command for command in feature.commands()}
            config = getattr(feature, "config", None)
            resolved = config.resolve_commands(declared) if config is not None else declared
            for name, command in resolved.items():
                if name in merged:
                    log.warning(f"command {name} offered twice - '{feature.name}' is ignored.")
                    continue
                merged[name] = command if command.name == name else replace(command, name=name)
        self._commands = merged
        self._commands_version = version
        return merged

    def _command_config_version(self):
        """Fingerprint across the configuration states of all features. The call costs one
        stat() per feature (see LiveConfig.reload) - the same order of magnitude as the
        hot-reload check that runs per message anyway.

        The identity of the configuration belongs in it, not just its counter: if a feature
        is handed a different LiveConfig (tests, a feature swapping its configuration), that
        one's counter starts at one again - the counter alone would then look like "nothing
        happened"."""
        return tuple(
            (feature.name, id(feature.config), feature.config.version if feature.config is not None else 0)
            for feature in self._features.values()
        )

    def command(self, name):
        return self.commands().get(name)

    # --- Pub/sub --------------------------------------------------------------------

    def subscribe(self, topic, handler):
        """Registers an async handler for a topic. The handler receives the payload from
        publish() as keyword arguments."""
        self._handlers[topic].append(handler)
        return handler

    async def publish(self, topic, **payload):
        """Calls all subscribers of the topic one after another and returns their return
        values. A failing subscriber takes down neither the publisher nor the remaining
        subscribers - the same rule as for the EventSub handlers in
        platforms/twitch/bot.py."""
        results = []
        for handler in list(self._handlers.get(topic, ())):
            try:
                results.append(await handler(**payload))
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"Error in the event handler for '{topic}': {e}")
        return results

    async def announce(self, announcement):
        """Distributes an announcement to all platforms with the ANNOUNCE capability and
        returns how many actually posted it.

        The source is not excluded: a !bug from the Discord chat is meant to land in the
        Discord bug channel. Whether a platform repeats its own announcements is decided in
        its announce()."""
        await self.publish(announcement.kind, announcement=announcement)

        delivered = 0
        for target in self.with_capability(platform_api.ANNOUNCE):
            try:
                if await target.announce(announcement):
                    delivered += 1
            except asyncio.CancelledError:
                raise
            except Exception as e:
                log.warning(f"{target.name} could not announce '{announcement.kind}': {e}")
        return delivered


# Shared instance for the running bot. Module-global like core/stats.py, rather than
# passing it through every function - the platform modules consist predominantly of module
# functions anyway. The class nevertheless remains independently instantiable (tests,
# several bots in one process).
bus = EventBus()
