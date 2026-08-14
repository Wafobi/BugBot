# feature.py
# XP/levels as a feature of Discord (capabilities RECORDING and LEVELS).
#
# This used to sit in core/stats.py and was called by the Discord bot - switching it off
# meant commenting it out. Now it hangs on MESSAGE_ACCEPTED, brings its own commands along and
# can be left out entirely via BUGBOT_FEATURES.
#
# It lives with Discord because it is Discord's XP system: the scores belong to that server,
# and the roles hanging off them exist only there. For a while it was neutral and switchable
# across platforms via levels.json - a flexibility nobody used, and one that left open the
# question of what a shared score across two services would even mean.
#
# MESSAGE_ACCEPTED is still reported by *every* platform, which is why there is a filter here:
# a feature gets to see the whole bot's topics, even when it belongs to one platform.
#
# Handing out the role on a level-up deliberately does not live here, although both now sit
# under platforms/discord/: which role you get from level 5 on is in discord.json and is the
# bot's business. The feature only reports LEVEL_UP onto the bus - the same separation as
# before, just over shorter distances.

import asyncio
from pathlib import Path

from core import events, feature as feature_api, runtime_config

from .store import LevelsStore

# XP values, texts and command names: levels.json, re-read on change.
CONFIG = runtime_config.LiveConfig(Path(__file__).parent / "levels.json")

# Which platform is meant is written nowhere in this file: `self.owner` comes from
# core/registry.py, out of the folder the feature lives in (platforms/discord/features/...
# -> "discord"). The name used to stand here as a constant - correct as long as nobody renames
# the folder, and one of the places where a feature claims more about the world than it can
# know.


class LevelsFeature(feature_api.Feature):
    name = "levels"
    provides = frozenset({feature_api.RECORDING, feature_api.LEVELS})
    requires = frozenset({feature_api.STORAGE})

    def __init__(self):
        # The bus reaches for this too: it applies the "command_names" section when
        # collecting the commands (see core/events.py).
        self.config = CONFIG
        self.store = None
        self._bus = None

    async def setup(self, bus):
        db = bus.feature_with(feature_api.STORAGE)
        if db is None:
            raise RuntimeError("no feature with the STORAGE capability loaded")
        self.store = LevelsStore(db, self.owner)
        self._bus = bus
        await self._run(self.store.init_schema)
        bus.subscribe(events.MESSAGE_ACCEPTED, self.on_message_accepted)

    @staticmethod
    async def _run(fn, *args):
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    async def on_message_accepted(self, message):
        if not self.handles(message.platform) or not message.user_id:
            return
        level, leveled_up = await self._run(
            self.store.add_message_xp,
            message.user_id, message.user_name,
            CONFIG.get("xp_cooldown_seconds", 60),
            CONFIG.get("xp_min", 15),
            CONFIG.get("xp_max", 25),
        )
        if leveled_up and CONFIG.get("announce_level_up", True):
            # The triggering message travels along: only it knows which channel the level-up
            # should be celebrated in.
            await self._bus.publish(events.LEVEL_UP, message=message, level=level)

    # --- Commands -------------------------------------------------------------------

    def commands(self):
        return (
            feature_api.Command("!rank", self.cmd_rank, help=CONFIG.text("help.rank")),
            feature_api.Command("!top", self.cmd_top, help=CONFIG.text("help.top")),
        )

    async def cmd_rank(self, message):
        """Like all feature commands these hang in *every* platform - but what is asked for
        is always the Discord score, because there is no other. Only the look at yourself does
        not work from outside: somebody writing in Twitch chat cannot be matched to a Discord
        account from there."""
        target = message.arg_text.strip()
        if not target:
            if not self.handles(message.platform):
                return CONFIG.text("rank.elsewhere")
            xp, level = await self._run(self.store.get_level, message.user_id)
            return CONFIG.text("rank.self", user=message.user_name, level=level, xp=xp)

        # @-mentions arrive as <@123456> or @name, depending on the platform.
        cleaned = target.lstrip("<@!").rstrip(">").lstrip("@")
        if cleaned.isdigit():
            xp, level = await self._run(self.store.get_level, cleaned)
            return CONFIG.text("rank.other", user=target, level=level, xp=xp)

        found = await self._run(self.store.find_by_name, cleaned)
        if not found:
            return CONFIG.text("rank.unknown", user=cleaned)
        _, xp, level = found
        return CONFIG.text("rank.other", user=cleaned, level=level, xp=xp)

    async def cmd_top(self, message):
        top = await self._run(self.store.get_top, CONFIG.get("top_limit", 10))
        if not top:
            return CONFIG.text("top.none")
        lines = [
            CONFIG.text("top.line", rank=i, user=user_name or user_id, level=level, xp=xp)
            for i, (user_name, user_id, xp, level) in enumerate(top, start=1)
        ]
        return CONFIG.text("top.title") + "\n" + "\n".join(lines)


def create_feature():
    return LevelsFeature()
