# feature.py
# Moderation as a feature (capability MODERATION).
#
# The only feature used by pull: the platform needs a verdict before it can carry on with
# the message, and a "report and forget" over the bus is not enough for that.
#
# New compared to core/moderation.py: the feature decides the *consequence*, not just the
# hit. The escalation logic (at which offence within which time window a timeout is due)
# used to stand word for word in both platforms and is now here exactly once. The platform
# only carries the verdict out - it alone knows how to delete and time out on itself.

from collections import defaultdict
from datetime import datetime, timedelta

from core import feature as feature_api, runtime_config

from . import filters

# The values from moderation.json - repeated here so the feature moderates without the file
# too (see core/runtime_config.py). What counts as an offence therefore lives in one place
# for all platforms; twitch.json/discord.json can still override individual values in their
# own "moderation" section.
DEFAULTS = {
    "settings": dict(filters.DEFAULT_MODERATION_SETTINGS),
    "banned_words": {"use_builtin_list": True, "extra": [], "remove": []},
    "texts": dict(filters.VIOLATION_REASON_LABELS),
}


def _mask(found):
    """The finding, made unreadable: "Idiot" -> "I****".

    Needed because `detail` for a banned word is the word itself (filters.py) and the
    platforms write it into their offence notice. Unabridged that would mean: the bot
    deletes the message and then says the word itself - and no filter applies anything to
    it. A troll would not even need a command for that, only the word.

    Masking happens here and not in the platforms, so that none of them ever gets the
    finding unfiltered - a third platform inherits the protection without doing anything
    for it.

    The first letter stays so a mod can still place the notice. With a single character
    nothing stays - otherwise the mask would be the finding.
    """
    found = (found or "").strip()
    if not found:
        return ""
    return found[0] + "*" * (len(found) - 1) if len(found) > 1 else "*"


class ModerationFeature(feature_api.Feature):
    name = "moderation"
    provides = frozenset({feature_api.MODERATION})

    def __init__(self):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        # user_key -> timestamps of the most recent offences, for the escalation.
        # Deliberately in RAM only: a restart should hold no old offence against anyone.
        self._violations = defaultdict(list)

    async def review(self, message, overrides=None):
        """Checks a message and returns a Verdict - or None when nothing speaks against it.

        `overrides` is the "moderation" section from the calling platform's JSON - it lies
        on top of the shared values from moderation.json. The platform passes it in afresh
        with every message, so that the hot-reload configuration keeps working and both
        platforms can stay set to different levels of strictness."""
        if message.is_privileged:
            # Broadcaster/moderators/admins are exempt - previously every platform checked
            # that itself before the call, now the rule applies in one place.
            return None

        settings = filters.build_settings(
            self.config.section("settings"), overrides, self.config.section("banned_words"),
        )
        hit = filters.moderate_message(message.text, settings, relaxed=message.is_subscriber)
        if not hit:
            return None

        reason, detail = hit
        count = self._record_violation(
            self._user_key(message), settings["violation_window_minutes"]
        )
        over_threshold = count >= settings["timeout_threshold"]
        return feature_api.Verdict(
            reason=reason,
            label=self.config.text(f"reason.{reason}"),
            detail=_mask(detail),
            delete=True,
            timeout_seconds=settings["timeout_duration_seconds"] if over_threshold else 0,
            violation_count=count,
        )

    @staticmethod
    def _user_key(message):
        """Counts offences per user and platform. The user id is more stable than the name
        (renames, differing spellings), which is why it takes precedence."""
        return f"{message.platform}:{message.user_id or message.user_name.lower()}"

    def _record_violation(self, user_key, window_minutes):
        """Counts this user's offences within the last `window_minutes` minutes and returns
        the current number (escalation delete -> timeout)."""
        now = datetime.now()
        history = self._violations[user_key]
        history.append(now)
        cutoff = now - timedelta(minutes=window_minutes)
        while history and history[0] < cutoff:
            history.pop(0)
        return len(history)


def create_feature():
    return ModerationFeature()
