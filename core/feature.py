# feature.py
# The Feature API - the contract for everything that *uses* platforms rather than being one.
# Counterpart to core/platform.py.
#
# Same convention as the platforms: every feature lives in features/<name>/ and provides a
# create_feature() function in features/<name>/feature.py (see core/registry.py).
#
# A feature declares what it can do via `provides`, and gets used in two ways:
#
#   Push  - in setup() it subscribes to the topics from core/events.py and records what
#           happens. The platform only publishes "this happened" and does not know who is
#           listening. All recording works this way: previously every platform called
#           stats.record_* about 30 times, with the full signature each time.
#   Pull  - the platform looks a feature up by its capability and calls its methods.
#           Needed wherever it needs an *answer* before it can continue - above all
#           moderation, which returns a verdict on a message.
#
# On top of that a feature brings its own commands (commands()). The platforms wire them
# into their command resolution without knowing them - which is how !rank or !leaderboard
# work everywhere without anyone writing Twitch- or Discord-specific code for them.

from abc import ABC
from dataclasses import dataclass

# Announcement/Field are the neutral presentation language between platforms and features:
# a platform renders them (embed/chat line), a feature produces them as a command reply or
# a block of figures. They live in core/platform.py because that is where the rendering
# half of the contract sits - passed through here so feature code need not reach sideways
# into the Platform API.
from .platform import Announcement, Field, STATUS  # noqa: F401  (deliberate re-export)

# Re-exported for the same reason: the platform capabilities. A feature should be able to
# say *which kind* of platform concerns it ("the one that reports a stream") without
# reaching into the Platform API - and without naming a service.
from .platform import ANNOUNCE, CHAT, MODERATE, STREAM  # noqa: F401


# --- Capabilities ----------------------------------------------------------------------
STORAGE = "storage"        # persistent storage for other features (see features/sql_db)
RECORDING = "recording"    # records what happens on the platforms
MODERATION = "moderation"  # returns a verdict on a message (review)
STATS = "stats"            # answers queries about figures
LEVELS = "levels"          # XP/level per user
SESSIONS = "sessions"      # knows the running stream session (see features/stream_sessions)
CHAT_LOG = "chat_log"      # records the full message text
RAW_LOG = "raw_log"        # raw log of the platform notifications
VARIABLES = "variables"    # fills the {placeholders} of static commands (resolve), and knows
                           # the operator's timezone (zone) - see features/variables


@dataclass
class Message:
    """An incoming message, platform-neutral - what a feature gets to see of a platform.

    `raw` is the platform-specific original (discord.Message or the Twitch context).
    Features should only touch it when they are acting platform-specifically anyway;
    everything neutral is in the fields above.

    command/arg_text are filled when the message was recognised as a command."""
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
    """The verdict of a MODERATION feature on a message. The platform carries it out - it
    alone knows how to delete and time out on itself - but no longer decides it. The
    escalation (at which offence a timeout is due) lives here too, rather than twice over
    in both platforms."""
    reason: str                 # machine-readable, e.g. "banned_word"
    label: str                  # plain text for chat/log, e.g. "disallowed word"
    detail: str = ""            # optional finding, e.g. the word itself
    delete: bool = True
    timeout_seconds: int = 0    # 0 = no timeout
    violation_count: int = 1


@dataclass(frozen=True)
class Command:
    """A command contributed by a feature. The handler receives a Message (with command/
    arg_text filled in) and returns what should be posted: a string, an Announcement (for
    platforms that can present richly - Discord turns it into an embed, Twitch into a line
    of text) or None."""
    name: str
    handler: object
    mod_only: bool = False
    help: str = ""


class Feature(ABC):
    """Base class of every feature. None of it is mandatory: a feature that only listens
    overrides setup(); one that only contributes commands, only commands()."""

    #: short, unique name ("stats", "moderation", "levels")
    name = ""

    #: name of the platform this feature belongs to - set by core/registry.py from the
    #: folder it lives in (platforms/discord/features/levels -> "discord"). Empty for the
    #: neutral features in features/.
    #:
    #: There so that a platform-owned feature need not write the name of its service
    #: anywhere: `if message.platform != self.owner` says "not my platform" and stays
    #: correct if the folder is ever renamed. A feature does *not* have to filter - the raw
    #: log, for instance, deliberately keeps everything that comes in.
    owner = ""

    #: The bus this feature is attached to. core/registry.py sets it before setup(), so
    #: that methods outside setup() can reach the directory of platforms too.
    bus = None

    #: Capabilities a *platform* must have for its notifications to concern this feature
    #: (from core/platform.py: CHAT, ANNOUNCE, STREAM, MODERATE). Empty = all of them.
    #:
    #: This is the language in which a neutral feature may talk about platforms: "the ones
    #: with a stream" instead of "Twitch". It survives a change of service, it is right on
    #: an installation that never existed, and it cannot quietly point at nothing - unlike
    #: a name, which on the wrong installation simply never matches.
    platform_capabilities = frozenset()

    #: frozenset of the capabilities defined above that this feature offers
    provides = frozenset()

    #: Capabilities this feature needs from *other* features. core/registry.py sets the
    #: features up in dependency order and skips one whose needs nobody covers - a
    #: half-working feature is worse than none.
    requires = frozenset()

    #: This feature's own LiveConfig (features/<name>/<name>.json), or None for a feature
    #: without settings. Setting one gets you two things for free: your texts via
    #: config.text() and the renaming of your commands - the bus applies the "command_names"
    #: section when collecting them, and the feature has to do nothing for it (see
    #: core/events.py:EventBus.commands).
    config = None

    #: Capabilities this feature *takes along if they exist*. They only decide the order,
    #: never the whether: if nobody offers them, the feature is set up anyway and has to
    #: cope with None in setup().
    #:
    #: Needed because otherwise exactly what `requires` prevents would happen, only the
    #: other way round: without this declaration setup() might run before the optional
    #: feature is registered, and the look into the directory would quietly find nothing.
    #: Example: stats takes the stream sessions along when Twitch is running, but counts
    #: without them too.
    optional = frozenset()

    def supports(self, capability):
        return capability in self.provides

    def platform_scope(self):
        """The names of the platforms that concern this feature - or None for "all".

        First the own platform (for a platform-owned feature), otherwise those bringing the
        required capabilities. Determined afresh on every call: which platforms exist is
        only settled after startup - the features are set up before that."""
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
        """Does a notification from this platform concern me?"""
        scope = self.platform_scope()
        return scope is None or platform_name in scope

    async def setup(self, bus):
        """Called once at startup, before the platforms come up: create tables, subscribe
        to topics, restore state. The bus is passed in so a feature can also fetch the
        features it needs per `requires` here (see features/stats: storage via the STORAGE
        capability)."""
        return

    async def close(self):
        """Clean up on shutdown. Must work even when setup() never ran, or only halfway."""
        return

    def commands(self):
        """This feature's commands, as a tuple of Command. The platforms wire them into
        their own command resolution."""
        return ()

    def __repr__(self):
        return f"<{type(self).__name__} name={self.name!r}>"
