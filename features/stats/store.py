# store.py
# The SQL of the statistics feature: counters and figures, nothing else.
#
# What used to sit in here as well now lives with the features that own it: the stream
# sessions and their segments in platforms/twitch/features/stream_sessions, the message text
# in features/chat_log, the raw log in platforms/twitch/features/raw_log. What remains is
# the part that actually counts and calculates - and it no longer knows for itself which
# stream is running: the stream_session_id comes in from outside on every call (see
# feature.py).
#
# Everything here is blocking sqlite3 and has to be called from async code via
# loop.run_in_executor(None, ...).

# Tables whose rows are additionally assigned to the running stream session (NULL =
# happened outside a stream, or no SESSIONS feature is loaded at all). This makes
# "everything since the beginning" and "only this stream" the same query with/without
# WHERE stream_session_id - there is no second set of books kept in parallel that could
# drift apart.
SESSION_SCOPED_TABLES = ("messages", "command_usage", "moderation_actions", "events", "ad_breaks")

SCHEMA = """
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    user_name TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS command_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    command TEXT NOT NULL,
    user_name TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS moderation_actions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    user_name TEXT NOT NULL,
    reason TEXT NOT NULL,
    action TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS ad_breaks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at TEXT NOT NULL DEFAULT (datetime('now')),
    duration_seconds INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS highscores (
    metric TEXT PRIMARY KEY,
    value INTEGER NOT NULL,
    stream_session_id INTEGER,
    achieved_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT NOT NULL,
    event_type TEXT NOT NULL,
    user_name TEXT NOT NULL,
    amount INTEGER NOT NULL DEFAULT 0,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS viewer_samples (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_session_id INTEGER NOT NULL,
    viewer_count INTEGER NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_viewer_samples_session ON viewer_samples (stream_session_id, id);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages (stream_session_id);
CREATE INDEX IF NOT EXISTS idx_command_usage_session ON command_usage (stream_session_id);
CREATE INDEX IF NOT EXISTS idx_moderation_actions_session ON moderation_actions (stream_session_id);
CREATE INDEX IF NOT EXISTS idx_events_session ON events (stream_session_id);
CREATE INDEX IF NOT EXISTS idx_events_type ON events (event_type);
CREATE INDEX IF NOT EXISTS idx_ad_breaks_session ON ad_breaks (stream_session_id);
"""

# event_type -> key in the metric dicts. Cheers/gift subs from anonymous donors
# deliberately run under their own types: they count towards the sums but do not appear in
# the leaderboards (get_top_users), where "Anonymous" would otherwise sit permanently at
# the top.
_EVENT_COUNT_METRICS = {
    "follow": "follows_gained",
    "sub": "subs_gained",
    "resub": "resubs",
    "raid": "raids_in",
}
_EVENT_SUM_METRICS = {
    "cheer": "bits_cheered",
    "cheer_anon": "bits_cheered",
    "gift_sub": "gift_subs",
    "gift_sub_anon": "gift_subs",
    "raid": "raid_viewers_in",
}

# Record name in the highscores table -> key from the stream metrics.
#
# The chat record counts everything that was reported. Previously it counted only Twitch - a
# platform this feature should not know at all, and on an installation without Twitch a
# record that never moves. Anyone wanting to count only part of it restricts the recording
# instead (features/chat_log) or reads messages_by_platform.
HIGHSCORE_METRICS = {
    "peak_viewers": "peak_viewers",
    "subs_gained": "subs_gained",
    "bits_cheered": "bits_cheered",
    "follows_gained": "follows_gained",
    "hypetrain_level": "hypetrain_level",
    "chat_messages": "chat_messages",
}


class StatsStore:
    """Counters and figures. `db` is the feature with the STORAGE capability (see
    features/sql_db); `session_id` is passed through by the caller (None = no running
    session)."""

    def __init__(self, db):
        self._db = db

    # --- Schema ---------------------------------------------------------------------

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            # Add the columns first, then the indexes on them - in that order, because an
            # existing bugbot.db does not have the columns yet.
            for table in SESSION_SCOPED_TABLES:
                self._db.add_column_if_missing(conn, table, "stream_session_id", "INTEGER")
            conn.executescript(INDEXES)

    # --- Aufzeichnung ---------------------------------------------------------------

    def record_message(self, session_id, platform, user_name):
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO messages (platform, user_name, stream_session_id) VALUES (?, ?, ?)",
                (platform, user_name, session_id),
            )

    def record_command(self, session_id, platform, command, user_name):
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO command_usage (platform, command, user_name, stream_session_id) "
                "VALUES (?, ?, ?, ?)",
                (platform, command, user_name, session_id),
            )

    def record_moderation_action(self, session_id, platform, user_name, reason, action):
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO moderation_actions (platform, user_name, reason, action, stream_session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (platform, user_name, reason, action, session_id),
            )

    def record_event(self, session_id, platform, event_type, user_name, amount=0):
        """A single live event (follow/sub/gift sub/cheer/raid, ...). `amount` is the
        respective number (bits, count of gift subs, viewer count on a raid, hype train
        level, ...) or 0."""
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO events (platform, event_type, user_name, amount, stream_session_id) "
                "VALUES (?, ?, ?, ?, ?)",
                (platform, event_type, user_name, amount, session_id),
            )

    def record_ad_break(self, session_id, duration_seconds):
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO ad_breaks (duration_seconds, stream_session_id) VALUES (?, ?)",
                (duration_seconds, session_id),
            )

    def record_viewer_sample(self, session_id, viewer_count):
        """A viewer-count sample. Only within a session - peak, average and the curve come
        out of these; without a stream there is nothing to sample."""
        if session_id is None:
            return False
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO viewer_samples (stream_session_id, viewer_count) VALUES (?, ?)",
                (session_id, viewer_count),
            )
        return True

    # --- Highscores -----------------------------------------------------------------

    def update_highscore(self, metric, value, session_id=None):
        """Updates the all-time highscore for `metric`, but only if `value` beats the
        previous best (or there is none yet). Returns True when it was a new record."""
        with self._db.connect() as conn:
            row = conn.execute("SELECT value FROM highscores WHERE metric = ?", (metric,)).fetchone()
            if row is not None and row[0] >= value:
                return False
            conn.execute(
                """
                INSERT INTO highscores (metric, value, stream_session_id, achieved_at)
                VALUES (?, ?, ?, datetime('now'))
                ON CONFLICT(metric) DO UPDATE SET
                    value = excluded.value,
                    stream_session_id = excluded.stream_session_id,
                    achieved_at = excluded.achieved_at
                """,
                (metric, value, session_id),
            )
            return True

    def get_highscores(self):
        """{metric: {'value':..., 'achieved_at':...}} for every highscore reached so far."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT metric, value, achieved_at FROM highscores").fetchall()
        return {metric: {"value": value, "achieved_at": achieved_at} for metric, value, achieved_at in rows}

    # --- Abfragen -------------------------------------------------------------------

    def get_viewer_samples(self, session_id):
        """[(ts, viewer_count), ...] chronologically - a stream's viewer curve."""
        if session_id is None:
            return []
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT ts, viewer_count FROM viewer_samples WHERE stream_session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [tuple(row) for row in rows]

    def get_user_total(self, event_type, user_name):
        """Summed amount of one user's events of one type, all-time - e.g. "how many bits
        has this person cheered". Counterpart to get_top_users, for the single-row case
        (features/companion checks one chatter's total against a threshold, where a top-N
        query would be the wrong shape)."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(amount), 0) FROM events WHERE event_type = ? AND user_name = ?",
                (event_type, user_name),
            ).fetchone()
        return int(row[0]) if row else 0

    def get_top_users(self, event_type, limit=3, session_id=None):
        """[(user_name, summed amount), ...] sorted descending, for leaderboards (e.g. top
        cheerer, top gifter). With session_id only for that one stream, otherwise across all
        of them. Anonymous donors live under their own event_types (cheer_anon /
        gift_sub_anon) and therefore never show up here."""
        where_sql = "WHERE event_type = ?"
        params = [event_type]
        if session_id is not None:
            where_sql += " AND stream_session_id = ?"
            params.append(session_id)
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                f"""
                SELECT user_name, SUM(amount) AS total FROM events
                {where_sql}
                GROUP BY user_name
                ORDER BY total DESC
                LIMIT ?
                """,
                params,
            ).fetchall()
        return [(user_name, total) for user_name, total in rows]

    def _event_metrics(self, conn, session_id=None):
        """Computes the event figures (follows, subs, bits, raids, hype train) - with
        session_id for exactly one stream, without it for everything since the beginning.
        One query, one GROUP BY: counts and sums fall out together."""
        where_sql, params = ("WHERE stream_session_id = ?", (session_id,)) if session_id is not None else ("", ())
        rows = conn.execute(
            f"SELECT event_type, COUNT(*), COALESCE(SUM(amount), 0), COALESCE(MAX(amount), 0) "
            f"FROM events {where_sql} GROUP BY event_type",
            params,
        ).fetchall()

        metrics = dict.fromkeys(set(_EVENT_COUNT_METRICS.values()) | set(_EVENT_SUM_METRICS.values()), 0)
        metrics["hypetrain_level"] = 0
        for event_type, count, total, highest in rows:
            if event_type in _EVENT_COUNT_METRICS:
                metrics[_EVENT_COUNT_METRICS[event_type]] += count
            if event_type in _EVENT_SUM_METRICS:
                metrics[_EVENT_SUM_METRICS[event_type]] += total
            # Hype train: every progress notification is its own row; what matters is the
            # maximum reached, not the sum of the intermediate states.
            if event_type == "hypetrain":
                metrics["hypetrain_level"] = highest
        return metrics

    def session_metrics(self, session_id):
        """*This* feature's figures for a stream, computed from the raw data - there are no
        precomputed values that could go stale.

        Master data (title, duration), chatter count and raw-log volume are deliberately not
        in here: those belong to other features and are mixed in by feature.py."""
        if session_id is None:
            return {}
        sid = (session_id,)
        with self._db.connect() as conn:
            messages_by_platform = dict(
                conn.execute(
                    "SELECT platform, COUNT(*) FROM messages WHERE stream_session_id = ? GROUP BY platform", sid
                ).fetchall()
            )
            commands_total = conn.execute(
                "SELECT COUNT(*) FROM command_usage WHERE stream_session_id = ?", sid
            ).fetchone()[0]
            top_commands = conn.execute(
                """
                SELECT command, COUNT(*) AS n FROM command_usage WHERE stream_session_id = ?
                GROUP BY command ORDER BY n DESC LIMIT 5
                """,
                sid,
            ).fetchall()
            actions_by_type = dict(
                conn.execute(
                    "SELECT action, COUNT(*) FROM moderation_actions WHERE stream_session_id = ? GROUP BY action", sid
                ).fetchall()
            )
            ad_count, ad_seconds = conn.execute(
                "SELECT COUNT(*), COALESCE(SUM(duration_seconds), 0) FROM ad_breaks WHERE stream_session_id = ?", sid
            ).fetchone()
            peak, avg, samples = conn.execute(
                """
                SELECT COALESCE(MAX(viewer_count), 0), COALESCE(ROUND(AVG(viewer_count)), 0), COUNT(*)
                FROM viewer_samples WHERE stream_session_id = ?
                """,
                sid,
            ).fetchone()
            metrics = self._event_metrics(conn, session_id)

        metrics.update(
            {
                # Breakdown *and* sum: the keys come from the rows, not from a list in the
                # code - this feature names no platform.
                "messages_by_platform": messages_by_platform,
                "chat_messages": sum(messages_by_platform.values()),
                "commands_used": commands_total,
                "top_commands": [(command, n) for command, n in top_commands],
                "mod_actions": sum(actions_by_type.values()),
                "actions_by_type": actions_by_type,
                "ad_breaks": ad_count,
                "ad_seconds": ad_seconds,
                "peak_viewers": int(peak),
                "avg_viewers": int(avg),
                "viewer_samples": samples,
            }
        )
        return metrics

    def get_summary(self):
        """Aggregated all-time numbers for the !stats output. The stream totals (how many
        sessions, how many hours) are added by the SESSIONS feature."""
        with self._db.connect() as conn:
            total_messages = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            total_commands = conn.execute("SELECT COUNT(*) FROM command_usage").fetchone()[0]
            actions_by_type = dict(
                conn.execute("SELECT action, COUNT(*) FROM moderation_actions GROUP BY action").fetchall()
            )
            total_ad_breaks = conn.execute("SELECT COUNT(*) FROM ad_breaks").fetchone()[0]
            all_time_peak = conn.execute("SELECT COALESCE(MAX(viewer_count), 0) FROM viewer_samples").fetchone()[0]
            event_metrics = self._event_metrics(conn)

        return {
            **event_metrics,
            "total_messages": total_messages,
            "total_commands": total_commands,
            "total_mod_actions": sum(actions_by_type.values()),
            "peak_viewers": all_time_peak,
            "actions_by_type": actions_by_type,
            "total_ad_breaks": total_ad_breaks,
        }
