# feature.py
# Statistics as a feature (capabilities RECORDING and STATS).
#
# The feature for which the push route pays off most clearly: previously both platforms
# held a stats.record_* call with the full signature in about 30 places. Now the platforms
# only report "this happened" onto the bus, and all the recording lives in the subscriptions
# further down. Switching the statistics off (BUGBOT_FEATURES) changes not one line in the
# platforms.
#
# Counting is all that still happens here. The wording of the messages (features/chat_log),
# the raw log (platforms/twitch/features/raw_log) and the stream sessions themselves
# (platforms/twitch/features/stream_sessions) are features of their own. The stream
# evaluation fetches their numbers via their capabilities - if one is missing, exactly its
# figure is missing and nothing else.
#
# SESSIONS is therefore deliberately *not* in `requires`: that feature comes from Twitch, and
# a bot running only on Discord should still count. Without SESSIONS the stream_session_id
# simply stays NULL, !stats works unchanged, and only the stream-related evaluation has
# nothing to show.
#
# The commands !stats, !streamstats, !highscores and !leaderboard are likewise brought along
# by the feature itself - previously !stats was built twice (once per platform) and the other
# three were available on Twitch only.

import asyncio
from dataclasses import replace

from core import events, feature as feature_api, runtime_config

from .store import HIGHSCORE_METRICS, StatsStore

# All labels, sentences and colours of this feature live in stats.json - not a second time
# here. The shipped file is the default state: a deleted text falls back to its version (see
# core/runtime_config.py, text()), everything else to the default at the call site.


class StatsFeature(feature_api.Feature):
    name = "stats"
    provides = frozenset({feature_api.RECORDING, feature_api.STATS})
    requires = frozenset({feature_api.STORAGE})
    optional = frozenset({feature_api.SESSIONS, feature_api.CHAT_LOG, feature_api.RAW_LOG})

    def __init__(self):
        self.config = runtime_config.for_package(__file__)
        self.store = None
        self._sessions = None
        self._chat_log = None
        self._raw_log = None

    async def setup(self, bus):
        db = bus.feature_with(feature_api.STORAGE)
        if db is None:
            raise RuntimeError("no feature with the STORAGE capability loaded")
        self.store = StatsStore(db)
        await self._run(self.store.init_schema)

        # All three optional - each missing one costs exactly its contribution to the
        # evaluation, not the evaluation.
        self._sessions = bus.feature_with(feature_api.SESSIONS)
        self._chat_log = bus.feature_with(feature_api.CHAT_LOG)
        self._raw_log = bus.feature_with(feature_api.RAW_LOG)

        bus.subscribe(events.MESSAGE_ACCEPTED, self.on_message_accepted)
        bus.subscribe(events.COMMAND, self.on_command)
        bus.subscribe(events.MOD_ACTION, self.on_mod_action)
        bus.subscribe(events.PLATFORM_EVENT, self.on_platform_event)
        bus.subscribe(events.VIEWERS, self.on_viewers)
        bus.subscribe(events.AD_BREAK, self.on_ad_break)
        if self._sessions is not None:
            # Not STREAM_END: the id of the ended stream is only settled afterwards.
            bus.subscribe(events.SESSION_ENDED, self.on_session_ended)

    @staticmethod
    async def _run(fn, *args):
        """Everything in the store is blocking sqlite3 - so it belongs in the executor,
        exactly like the Helix calls in platforms/twitch/api.py."""
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    @property
    def _session_id(self):
        """The running stream, or None - including when no SESSIONS feature is loaded at
        all."""
        return self._sessions.current_session_id if self._sessions is not None else None

    # --- Recording (push) -----------------------------------------------------------

    async def on_message_accepted(self, message):
        """After moderation: the message counter, so that an offence does not count as a
        regular message. (The full text hangs on MESSAGE - see features/chat_log.)"""
        await self._run(self.store.record_message, self._session_id, message.platform, message.user_name)

    async def on_command(self, platform, command, user_name):
        await self._run(self.store.record_command, self._session_id, platform, command, user_name)

    async def on_mod_action(self, platform, user_name, reason, action):
        await self._run(
            self.store.record_moderation_action, self._session_id, platform, user_name, reason, action
        )

    async def on_platform_event(self, platform, event_type, user_name, amount=0):
        await self._run(self.store.record_event, self._session_id, platform, event_type, user_name, amount)

    async def on_viewers(self, platform, count):
        await self._run(self.store.record_viewer_sample, self._session_id, count)

    async def on_ad_break(self, platform, duration_seconds):
        await self._run(self.store.record_ad_break, self._session_id, duration_seconds)

    async def on_session_ended(self, session_id):
        """Reconciles the highscores and returns the stream's figures as finished fields -
        the SESSIONS feature passes them on to the platform, which builds its closing report
        from them without having to take the metrics dict apart.

        The values come from the DB rather than from a counter dict in RAM - which is how a
        bot restart mid-stream survives the highscore capture."""
        session_stats = await self.stream_stats(session_id)
        if session_stats is None:
            return ()
        for metric, key in HIGHSCORE_METRICS.items():
            await self._run(self.store.update_highscore, metric, session_stats.get(key, 0), session_id)
        return self.session_fields(session_stats)

    # --- Queries (pull) -------------------------------------------------------------

    async def summary(self):
        """The all-time numbers. Public so that platforms can build them into their own
        reports - the Discord hourly report mixes them with its local counters."""
        summary = await self._run(self.store.get_summary)
        sessions, minutes = await self._sessions.totals() if self._sessions is not None else (0, 0)
        summary["total_stream_sessions"] = sessions
        summary["total_stream_minutes"] = minutes
        summary["total_chat_logged"] = await self._chat_log.total_logged() if self._chat_log is not None else 0
        return summary

    async def user_event_total(self, event_type, user_name):
        """One user's summed amount for one event type, all-time - e.g. bits cheered. Public
        so other features can gate something on it (features/companion: only show a
        chatter's text once they have cheered enough bits) without reaching past this
        feature's STATS capability into the store themselves."""
        return await self._run(self.store.get_user_total, event_type, user_name)

    async def stream_stats(self, session_id=None):
        """All figures of a single stream (default: the running one), assembled from the
        contributions of every feature involved. None if the session does not exist - or if
        no stream sessions are kept at all."""
        if self._sessions is None:
            return None
        session = await self._sessions.session(session_id)
        if session is None:
            return None
        session_id = session["session_id"]

        metrics = await self._run(self.store.session_metrics, session_id)
        chat_logged, unique_chatters = (
            await self._chat_log.session_metrics(session_id) if self._chat_log is not None else (0, 0)
        )
        return {
            **session,
            **metrics,
            "chat_logged": chat_logged,
            "unique_chatters": unique_chatters,
            "eventsub_notifications": await self._raw_log.count(session_id) if self._raw_log is not None else 0,
        }

    # --- Presentation ---------------------------------------------------------------

    def _field(self, key, **values):
        """An Announcement field whose label and value both come from stats.json:
        "<key>.name" and "<key>.value"."""
        return feature_api.Field(
            self.config.text(f"{key}.name"), self.config.text(f"{key}.value", **values),
        )

    def _breakdown(self, messages_by_platform):
        """The breakdown by platform, e.g. " (twitch 120, discord 30)" - empty as long as
        only one platform contributed.

        Stands here in place of the former fixed Twitch number: the same information, but
        read from the data instead of from a platform name in the code. Which services exist
        is what the rows in the database say."""
        contributing = {name: count for name, count in sorted(messages_by_platform.items()) if count}
        if len(contributing) < 2:
            return ""
        entries = ", ".join(
            self.config.text("session.chat.entry", platform=name, messages=count)
            for name, count in contributing.items()
        )
        return self.config.text("session.chat.breakdown", entries=entries)

    def session_fields(self, session_stats):
        """Figures of an ended stream as Announcement fields."""
        if not session_stats:
            return ()
        hours, minutes = divmod(session_stats["duration_minutes"], 60)
        fields = [
            feature_api.Field(
                self.config.text("session.category.name"),
                session_stats["game_name"] or "-",
                inline=True,
            ),
            replace(self._field("session.duration", hours=hours, minutes=minutes), inline=True),
            replace(self._field(
                "session.viewers",
                peak=session_stats["peak_viewers"], average=session_stats["avg_viewers"],
            ), inline=True),
            self._field(
                "session.chat",
                messages=session_stats["chat_messages"],
                chatters=session_stats["unique_chatters"],
                breakdown=self._breakdown(session_stats.get("messages_by_platform", {})),
            ),
            self._field(
                "session.support",
                subs=session_stats["subs_gained"], gift_subs=session_stats["gift_subs"],
                bits=session_stats["bits_cheered"], follows=session_stats["follows_gained"],
            ),
        ]
        if session_stats["raids_in"] or session_stats["hypetrain_level"]:
            fields.append(self._field(
                "session.extras",
                raids=session_stats["raids_in"], raid_viewers=session_stats["raid_viewers_in"],
                hypetrain_level=session_stats["hypetrain_level"],
            ))
        return tuple(fields)

    def summary_fields(self, summary):
        """The all-time numbers as fields - usable from outside too, e.g. for the Discord
        bot's hourly report, which combines them with its own values."""
        return (
            self._field(
                "summary.messages",
                messages=summary["total_messages"], commands=summary["total_commands"],
            ),
            self._field("summary.mod_actions", mod_actions=summary["total_mod_actions"]),
            replace(self._field("summary.ad_breaks", ad_breaks=summary["total_ad_breaks"]), inline=True),
            replace(self._field(
                "summary.sessions",
                sessions=summary["total_stream_sessions"],
                hours=summary["total_stream_minutes"] // 60,
            ), inline=True),
            self._field(
                "summary.live",
                peak_viewers=summary["peak_viewers"], subs=summary["subs_gained"],
                gift_subs=summary["gift_subs"], bits=summary["bits_cheered"],
                follows=summary["follows_gained"], raids=summary["raids_in"],
            ),
        )

    # --- Commands -------------------------------------------------------------------

    def commands(self):
        """The names here are the default names: the "command_names" section in stats.json
        lets them be renamed, aliased or switched off, applied by the bus (see
        core/events.py)."""
        return (
            feature_api.Command("!stats", self.cmd_stats, mod_only=True,
                                help=self.config.text("help.stats")),
            feature_api.Command("!streamstats", self.cmd_streamstats,
                                help=self.config.text("help.streamstats")),
            feature_api.Command("!highscores", self.cmd_highscores,
                                help=self.config.text("help.highscores")),
            feature_api.Command("!leaderboard", self.cmd_leaderboard,
                                help=self.config.text("help.leaderboard")),
        )

    async def cmd_stats(self, message):
        return feature_api.Announcement(
            kind=feature_api.STATUS,
            title=self.config.text("summary.title"),
            color=self.config.color("summary", 0x2ECC71),
            fields=self.summary_fields(await self.summary()),
        )

    async def cmd_streamstats(self, message):
        """Figures of the running stream (or of the last ended one when currently offline).
        All of it comes from the DB and can be evaluated per stream there too."""
        if self._sessions is None:
            return self.config.text("session.disabled")
        session_stats = await self.stream_stats()
        if session_stats is None:
            session_stats = await self.stream_stats(await self._sessions.last_session_id())
        if session_stats is None:
            return self.config.text("session.none")
        scope = self.config.text(
            "session.scope.live" if session_stats["is_live"] else "session.scope.last"
        )
        return feature_api.Announcement(
            kind=feature_api.STATUS,
            title=self.config.text("session.title", scope=scope),
            text=session_stats["title"] or "",
            color=self.config.color("stream", 0x9146FF),
            fields=self.session_fields(session_stats),
        )

    async def cmd_highscores(self, message):
        highscores = await self._run(self.store.get_highscores)
        if not highscores:
            return self.config.text("highscores.none")
        # Order from HIGHSCORE_METRICS: the records that exist, in a fixed sequence - the
        # label for each comes per metric from stats.json ("highscore.<metric>").
        entries = [
            self.config.text(
                "highscores.line",
                label=self.config.text(f"highscore.{metric}"),
                value=highscores[metric]["value"],
            )
            for metric in HIGHSCORE_METRICS
            if metric in highscores
        ]
        return self.config.text("highscores.result", entries=", ".join(entries))

    async def cmd_leaderboard(self, message):
        limit = self.config.get("leaderboard_limit", 3)
        top_cheerers = await self._run(self.store.get_top_users, "cheer", limit)
        top_gifters = await self._run(self.store.get_top_users, "gift_sub", limit)
        if not top_cheerers and not top_gifters:
            return self.config.text("leaderboard.none")

        def entries(rows):
            return ", ".join(
                self.config.text("leaderboard.entry", name=name, total=total) for name, total in rows
            )

        parts = []
        if top_cheerers:
            parts.append(self.config.text("leaderboard.cheerers", entries=entries(top_cheerers)))
        if top_gifters:
            parts.append(self.config.text("leaderboard.gifters", entries=entries(top_gifters)))
        return self.config.text("leaderboard.separator").join(parts)


def create_feature():
    return StatsFeature()
