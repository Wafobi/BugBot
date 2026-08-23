# platform.py
# The Platform API - the contract between bugbot.py and the individual platforms.
#
# Convention: every platform lives in its own package platforms/<name>/ and provides a
# create_platform() function in platforms/<name>/platform.py returning a subclass of
# Platform (see core/registry.py). After that bugbot.py knows neither Discord nor Twitch by
# name - it only finds, starts and stops Platform objects.
#
# Why it is built this way: previously the Twitch bot called platforms/discord/bot.py
# directly (bug reports, clips, live announcement) and the Discord bot called
# platforms/twitch/bot.py resp. api.py (live status, viewer sampling). Both directions
# could only be built with deferred imports *inside* the functions, because an import at
# module level would have been a circular import. Everything cross-platform now goes
# through core/events.py, and the two packages no longer know each other.

from abc import ABC, abstractmethod
from dataclasses import dataclass


# --- Capabilities ----------------------------------------------------------------------
# What a platform can do. core asks for this instead of recognising platforms by name - so
# a new platform need not be able to do everything, only to declare what it can.
CHAT = "chat"          # can write free text into its main channel (send_text)
ANNOUNCE = "announce"  # can post structured announcements (announce)
STREAM = "stream"      # reports stream start/end (see STREAM_ONLINE/STREAM_OFFLINE)
MODERATE = "moderate"  # moderates incoming messages itself (core.moderation)

#: All capabilities - so that "is this a capability or a platform name?" (see
#: EventBus.resolve_platforms) is answered in one place rather than three.
CAPABILITIES = frozenset({CHAT, ANNOUNCE, STREAM, MODERATE})


# --- Kinds of announcement ---------------------------------------------------------------
# The `kind` of an Announcement. Sender and receiver agree on these strings alone; how a
# platform presents them - Discord embed, Twitch chat line, or not at all - is its own
# decision. The same strings are also the topics EventBus.announce publishes under (see
# core/events.py).
STREAM_ONLINE = "stream.online"
STREAM_OFFLINE = "stream.offline"
BUG_REPORT = "bug.report"
CLIP = "clip.created"
STATUS = "status"


@dataclass(frozen=True)
class Field:
    """A named detail of an Announcement - an embed field on Discord, a "name: value"
    section on purely text-based platforms (see Announcement.as_text)."""
    name: str
    value: str
    inline: bool = False


@dataclass(frozen=True)
class Announcement:
    """A platform-neutral announcement: "this happened, post it wherever you can".

    Deliberately only as much structure as can be presented everywhere - title, text, link,
    image, a few named fields. Everything Discord-specific (embed, channel choice,
    @everyone) only comes into being in the Discord renderer, everything Twitch-specific in
    as_text.

    highlight  - the announcement is important enough for a notification (Discord turns it
                 into an @everyone). Only for real events such as a stream start.
    log        - additionally into the platform's mod log, if it keeps one.
    source     - name of the triggering platform ("twitch"), author the triggering user.
    """
    kind: str
    title: str
    text: str = ""
    url: str = ""
    image_url: str = ""
    color: int = 0x3498DB
    source: str = ""
    author: str = ""
    highlight: bool = False
    log: bool = False
    fields: tuple = ()

    def as_text(self, max_fields=None):
        """Single-line rendering for purely text-based platforms (e.g. Twitch chat).
        `max_fields` limits the detail fields - an IRC line may only be about 500
        characters long, and a stream summary would otherwise stand no chance."""
        parts = [self.title, self.text]
        parts += [f"{f.name}: {f.value}" for f in self.fields[:max_fields]]
        parts.append(self.url)
        # " ".join(part.split()) collapses embedded newlines/tabs/CR along with normal
        # whitespace - this rendering is for single-line, text-only platforms, so a line
        # break in a field is always wrong here, whatever put it there.
        return " - ".join(" ".join(part.split()) for part in parts if part and part.strip())


class Platform(ABC):
    """Base class of every platform.

    Only start() and close() are mandatory - a platform that can do nothing else is still a
    valid platform. Everything further is declared via `capabilities`; the default
    implementations below simply say "I cannot"."""

    #: short, unique name ("twitch", "discord"). Also appears as the platform column in
    #: core/stats.py and in the logs.
    name = ""

    #: frozenset of the capabilities defined above.
    capabilities = frozenset()

    def supports(self, capability):
        return capability in self.capabilities

    @abstractmethod
    async def start(self):
        """Brings the platform up. May return as soon as it is running (its own background
        tasks then keep going), or block for its entire lifetime - bugbot.py waits on both
        alike through a shared gather()."""

    @abstractmethod
    async def close(self):
        """Shuts down cleanly: cancel background tasks, close connections. Must work even
        when start() never ran, or only halfway - when another platform crashes, close() is
        called regardless."""

    async def wait_ready(self):
        """Waits until the platform can accept announcements. Default: immediately.

        Discord waits for on_ready here - before that it does not know its guilds yet and
        would silently discard every announcement. That is exactly why this exists: the
        live reconciliation at startup (bot restart mid-stream) would otherwise report into
        a bot that is not even logged in yet."""
        return

    async def send_text(self, text):
        """Writes free text into the platform's main channel. True on success.
        Default: the platform cannot (capability CHAT missing)."""
        return False

    async def announce(self, announcement):
        """Posts an announcement, provided the platform wants to present this `kind`. True
        if it actually got posted - the caller uses that to count whether the announcement
        arrived anywhere at all (see !bug).
        Default: the platform cannot (capability ANNOUNCE missing)."""
        return False

    def __repr__(self):
        return f"<{type(self).__name__} name={self.name!r}>"
