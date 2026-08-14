# store.py
# The chat record: the only place in the bot that keeps the *content* of messages. Taken
# over from features/stats/store.py - table and index unchanged, so an existing bugbot.db
# finds exactly what the statistics feature created before.
#
# Everything here is blocking sqlite3 and has to be called from async code via
# loop.run_in_executor(None, ...).

SCHEMA = """
CREATE TABLE IF NOT EXISTS chat_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_session_id INTEGER NOT NULL,
    platform TEXT NOT NULL,
    user_id TEXT,
    user_name TEXT NOT NULL,
    message TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_chat_log_session ON chat_log (stream_session_id, id);
"""


class ChatLogStore:
    """`db` is the feature with the STORAGE capability (see features/sql_db)."""

    def __init__(self, db):
        self._db = db

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            conn.executescript(INDEXES)

    def record(self, session_id, platform, user_name, message, user_id=None):
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO chat_log (stream_session_id, platform, user_id, user_name, message) "
                "VALUES (?, ?, ?, ?, ?)",
                (session_id, platform, user_id, user_name, message),
            )

    def recent(self, session_id, limit=200):
        """[(platform, user_name, message, ts), ...] of a session's most recent messages,
        in chronological order."""
        if session_id is None:
            return []
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT platform, user_name, message, ts FROM chat_log
                WHERE stream_session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [tuple(row) for row in reversed(rows)]

    def session_metrics(self, session_id):
        """(messages recorded, distinct chatters) of a session. The statistics feature
        fetches this from here rather than reaching into chat_log itself - the table belongs
        to this feature, and without it the figure is simply missing."""
        if session_id is None:
            return 0, 0
        with self._db.connect() as conn:
            logged, chatters = conn.execute(
                """
                SELECT COUNT(*), COUNT(DISTINCT COALESCE(user_id, user_name))
                FROM chat_log WHERE stream_session_id = ?
                """,
                (session_id,),
            ).fetchone()
        return logged, chatters

    def total_logged(self):
        with self._db.connect() as conn:
            return conn.execute("SELECT COUNT(*) FROM chat_log").fetchone()[0]
