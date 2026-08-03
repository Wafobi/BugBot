# store.py
# Der Chat-Mitschnitt: die einzige Stelle im Bot, die den *Inhalt* von Nachrichten
# aufbewahrt. Übernommen aus features/stats/store.py - Tabelle und Index unverändert, eine
# bestehende bugbot.db findet hier genau das wieder, was vorher das Statistik-Feature
# angelegt hat.
#
# Alles hier ist blockierendes sqlite3 und muss von async Code aus per
# loop.run_in_executor(None, ...) aufgerufen werden.

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
    """`db` ist das Feature mit der Fähigkeit STORAGE (siehe features/sql_db)."""

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
        """[(platform, user_name, message, ts), ...] der jüngsten Nachrichten einer Session,
        chronologisch aufsteigend."""
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
        """(mitgeschriebene Nachrichten, verschiedene Chatter) einer Session. Das
        Statistik-Feature holt sich das hier, statt selbst in chat_log zu greifen - die
        Tabelle gehört diesem Feature, und ohne es fehlt die Kennzahl schlicht."""
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
