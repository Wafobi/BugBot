# feature.py
# Statistik als Feature (Fähigkeiten RECORDING und STATS).
#
# Das Feature, für das sich der Push-Weg am deutlichsten lohnt: vorher stand in beiden
# Plattformen an ~30 Stellen ein stats.record_*-Aufruf mit voller Signatur. Jetzt melden
# die Plattformen nur noch "das ist passiert" auf den Bus, und alles Mitschreiben liegt
# in den Abonnements hier unten. Wer die Statistik abschaltet (BUGBOT_FEATURES), ändert
# an den Plattformen keine Zeile.
#
# Zählen ist alles, was hier noch passiert. Der Wortlaut der Nachrichten (features/chat_log),
# das Rohprotokoll (platforms/twitch/features/raw_log) und die Stream-Sessions selbst
# (platforms/twitch/features/stream_sessions) sind eigene Features. Deren Zahlen holt sich
# die Stream-Auswertung über deren Fähigkeiten dazu - fehlt eines, fehlt genau seine Kennzahl
# und sonst nichts.
#
# SESSIONS ist deshalb bewusst *nicht* in `requires`: das Feature kommt von Twitch, und ein
# Bot, der nur auf Discord läuft, soll trotzdem mitzählen. Ohne SESSIONS bleibt
# stream_session_id einfach NULL, !stats funktioniert unverändert, und nur die
# stream-bezogene Auswertung hat nichts zu zeigen.
#
# Die Befehle !stats, !streamstats, !highscores und !leaderboard bringt das Feature
# ebenfalls selbst mit - vorher waren !stats zweimal (einmal je Plattform) gebaut und die
# übrigen drei nur auf Twitch verfügbar.

import asyncio
from dataclasses import replace

from core import events, feature as feature_api, runtime_config

from .store import HIGHSCORE_METRICS, StatsStore

# Sämtliche Beschriftungen, Sätze und Farben dieses Features stehen in stats.json - hier
# nicht noch einmal. Die mitgelieferte Datei ist der Default-Stand (siehe
# core/runtime_config.py: die Erstfassung bleibt als Unterlage erhalten, auch wenn jemand
# später einen Schlüssel löscht).


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
            raise RuntimeError("kein Feature mit der Fähigkeit STORAGE geladen")
        self.store = StatsStore(db)
        await self._run(self.store.init_schema)

        # Alle drei optional - jedes fehlende kostet genau seinen Beitrag zur Auswertung,
        # nicht die Auswertung.
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
            # Nicht STREAM_END: die id des beendeten Streams steht erst danach fest.
            bus.subscribe(events.SESSION_ENDED, self.on_session_ended)

    @staticmethod
    async def _run(fn, *args):
        """Alles im Store ist blockierendes sqlite3 - gehört damit in den Executor,
        genau wie die Helix-Aufrufe in platforms/twitch/api.py."""
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    @property
    def _session_id(self):
        """Der laufende Stream, oder None - auch dann, wenn gar kein SESSIONS-Feature
        geladen ist."""
        return self._sessions.current_session_id if self._sessions is not None else None

    # --- Aufzeichnung (Push) --------------------------------------------------------

    async def on_message_accepted(self, message):
        """Nach der Moderation: der Nachrichtenzähler, damit ein Verstoß nicht als
        reguläre Nachricht mitzählt. (Der volle Text hängt an MESSAGE - siehe
        features/chat_log.)"""
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
        """Gleicht die Highscores ab und gibt die Kennzahlen des Streams als fertige Felder
        zurück - das SESSIONS-Feature reicht sie an die Plattform durch, die daraus ihren
        Abschlussbericht baut, ohne das Kennzahlen-Dict auseinandernehmen zu müssen.

        Die Werte kommen aus der DB statt aus einem Zähler-Dict im RAM - dadurch überlebt ein
        Bot-Neustart mitten im Stream die Highscore-Erfassung."""
        session_stats = await self.stream_stats(session_id)
        if session_stats is None:
            return ()
        for metric, key in HIGHSCORE_METRICS.items():
            await self._run(self.store.update_highscore, metric, session_stats.get(key, 0), session_id)
        return self.session_fields(session_stats)

    # --- Abfragen (Pull) ------------------------------------------------------------

    async def summary(self):
        """Die All-Time-Zahlen. Öffentlich, damit Plattformen sie in eigene Berichte
        einbauen können - der Discord-Stundenbericht mischt sie mit seinen lokalen
        Zählern."""
        summary = await self._run(self.store.get_summary)
        sessions, minutes = await self._sessions.totals() if self._sessions is not None else (0, 0)
        summary["total_stream_sessions"] = sessions
        summary["total_stream_minutes"] = minutes
        summary["total_chat_logged"] = await self._chat_log.total_logged() if self._chat_log is not None else 0
        return summary

    async def stream_stats(self, session_id=None):
        """Sämtliche Kennzahlen eines einzelnen Streams (Default: der laufende), aus den
        Beiträgen aller beteiligten Features zusammengesetzt. None, falls es die Session
        nicht gibt - oder gar keine Stream-Sessions geführt werden."""
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

    # --- Darstellung ----------------------------------------------------------------

    def _field(self, key, **values):
        """Ein Announcement-Field, dessen Beschriftung und Wert beide aus stats.json
        kommen: "<key>.name" und "<key>.value"."""
        return feature_api.Field(
            self.config.text(f"{key}.name"), self.config.text(f"{key}.value", **values),
        )

    def _breakdown(self, messages_by_platform):
        """Die Aufschlüsselung nach Plattform, z.B. " (twitch 120, discord 30)" - leer,
        solange nur eine Plattform beigetragen hat.

        Steht hier anstelle der früheren festen Twitch-Zahl: dieselbe Auskunft, aber aus
        den Daten gelesen statt aus einem Plattformnamen im Code. Welche Dienste es gibt,
        sagen die Zeilen in der Datenbank."""
        contributing = {name: count for name, count in sorted(messages_by_platform.items()) if count}
        if len(contributing) < 2:
            return ""
        entries = ", ".join(
            self.config.text("session.chat.entry", platform=name, messages=count)
            for name, count in contributing.items()
        )
        return self.config.text("session.chat.breakdown", entries=entries)

    def session_fields(self, session_stats):
        """Kennzahlen eines beendeten Streams als Announcement-Felder."""
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
        """Die All-Time-Zahlen als Felder - auch von außen nutzbar, z.B. für den
        Stundenbericht des Discord-Bots, der sie mit eigenen Werten kombiniert."""
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

    # --- Befehle --------------------------------------------------------------------

    def commands(self):
        """Die Namen hier sind die Standardnamen: umbenennen, aliasen oder abschalten
        lässt sie der Abschnitt "command_names" in stats.json, angewendet vom Bus (siehe
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
        """Kennzahlen des laufenden Streams (oder des zuletzt beendeten, wenn gerade
        offline). Alles davon kommt aus der DB und ist dort auch pro Stream auswertbar."""
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
        # Reihenfolge aus HIGHSCORE_METRICS: die Rekorde, die es gibt, in fester Folge -
        # die Beschriftung dazu kommt je Metrik aus stats.json ("highscore.<metrik>").
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
