# store.py
# Das SQL des Statistik-Features: Zähler und Kennzahlen, sonst nichts.
#
# Was hier früher noch mit drinsteckte, liegt inzwischen bei den Features, denen es gehört:
# die Stream-Sessions und ihre Abschnitte bei platforms/twitch/features/stream_sessions, der
# Nachrichtentext bei features/chat_log, das Rohprotokoll bei
# platforms/twitch/features/raw_log. Übrig bleibt der Teil, der tatsächlich zählt und
# rechnet - und der weiß nicht mehr selbst, welcher Stream gerade läuft: die
# stream_session_id kommt bei jedem Aufruf von außen herein (siehe feature.py).
#
# Alles hier ist blockierendes sqlite3 und muss von async Code aus per
# loop.run_in_executor(None, ...) aufgerufen werden.

# Tabellen, deren Zeilen zusätzlich der laufenden Stream-Session zugeordnet werden
# (NULL = außerhalb eines Streams passiert, oder es ist gar kein SESSIONS-Feature geladen).
# Dadurch sind "alles seit Beginn" und "nur dieser Stream" dieselbe Abfrage mit/ohne
# WHERE stream_session_id - es gibt keine zweite, parallel gepflegte Buchführung, die
# auseinanderlaufen könnte.
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

# event_type -> Schlüssel in den Kennzahl-Dicts. Cheers/Gift-Subs von anonymen Spendern
# laufen bewusst unter eigenen Typen: sie zählen in die Summen mit rein, tauchen aber nicht
# in den Bestenlisten auf (get_top_users), wo "Anonym" sonst dauerhaft oben stünde.
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

# Rekord-Name in der highscores-Tabelle -> Schlüssel aus den Stream-Kennzahlen.
#
# Der Chat-Rekord zählt alles, was gemeldet wurde. Vorher zählte er nur Twitch - eine
# Plattform, die dieses Feature gar nicht kennen dürfte, und auf einer Installation ohne
# Twitch ein Rekord, der sich nie bewegt. Wer nur einen Teil zählen will, schränkt statt
# dessen den Mitschnitt ein (features/chat_log) oder liest messages_by_platform aus.
HIGHSCORE_METRICS = {
    "peak_viewers": "peak_viewers",
    "subs_gained": "subs_gained",
    "bits_cheered": "bits_cheered",
    "follows_gained": "follows_gained",
    "hypetrain_level": "hypetrain_level",
    "chat_messages": "chat_messages",
}


class StatsStore:
    """Zähler und Kennzahlen. `db` ist das Feature mit der Fähigkeit STORAGE (siehe
    features/sql_db); `session_id` reicht der Aufrufer durch (None = keine laufende
    Session)."""

    def __init__(self, db):
        self._db = db

    # --- Schema ---------------------------------------------------------------------

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            # Erst die Spalten nachziehen, dann die Indizes darauf - in dieser Reihenfolge,
            # weil eine bestehende bugbot.db die Spalten noch nicht hat.
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
        """Ein einzelnes Live-Event (Follow/Sub/Gift-Sub/Cheer/Raid, ...). `amount` ist der
        jeweilige Zahlenwert (Bits, Anzahl Gift-Subs, Zuschauerzahl beim Raid,
        Hype-Train-Level, ...) oder 0."""
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
        """Eine Zuschauerzahl-Stichprobe. Nur innerhalb einer Session - daraus ergeben sich
        Peak, Durchschnitt und der Verlauf; ohne Stream gibt es nichts abzutasten."""
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
        """Aktualisiert den All-Time-Highscore für `metric`, aber nur falls `value` den
        bisherigen Bestwert übertrifft (oder es noch keinen gibt). Gibt True zurück, wenn es
        ein neuer Rekord war."""
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
        """{metric: {'value':..., 'achieved_at':...}} für alle bisher erreichten Highscores."""
        with self._db.connect() as conn:
            rows = conn.execute("SELECT metric, value, achieved_at FROM highscores").fetchall()
        return {metric: {"value": value, "achieved_at": achieved_at} for metric, value, achieved_at in rows}

    # --- Abfragen -------------------------------------------------------------------

    def get_viewer_samples(self, session_id):
        """[(ts, viewer_count), ...] chronologisch - der Zuschauerverlauf eines Streams."""
        if session_id is None:
            return []
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT ts, viewer_count FROM viewer_samples WHERE stream_session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [tuple(row) for row in rows]

    def get_top_users(self, event_type, limit=3, session_id=None):
        """[(user_name, summierter_amount), ...] absteigend sortiert, für Leaderboards
        (z.B. Top-Cheerer, Top-Gifter). Mit session_id nur für diesen einen Stream, sonst
        über alle. Anonyme Spender liegen unter eigenen event_types (cheer_anon /
        gift_sub_anon) und tauchen hier deshalb nie auf."""
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
        """Rechnet die Event-Kennzahlen (Follows, Subs, Bits, Raids, Hype Train) aus - mit
        session_id für genau einen Stream, ohne für alles seit Beginn. Eine Abfrage, ein
        GROUP BY: Zählungen und Summen fallen dabei gemeinsam an."""
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
            # Hype Train: jede progress-Meldung ist eine eigene Zeile, interessant ist das
            # erreichte Maximum, nicht die Summe der Zwischenstände.
            if event_type == "hypetrain":
                metrics["hypetrain_level"] = highest
        return metrics

    def session_metrics(self, session_id):
        """Die Kennzahlen *dieses* Features für einen Stream, berechnet aus den Rohdaten - es
        gibt keine vorberechneten Werte, die veralten könnten.

        Stammdaten (Titel, Dauer), Chatter-Zahl und Rohprotokoll-Umfang stehen bewusst nicht
        drin: die gehören anderen Features und werden in feature.py dazugemischt."""
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
                # Aufschlüsselung *und* Summe: die Schlüssel kommen aus den Zeilen, nicht
                # aus einer Liste im Code - dieses Feature nennt keine Plattform beim Namen.
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
        """Aggregierte All-Time-Zahlen für die !stats-Ausgabe. Die Stream-Summen (wie viele
        Sessions, wie viele Stunden) kommen vom SESSIONS-Feature dazu."""
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
