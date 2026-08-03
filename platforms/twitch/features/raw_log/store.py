# store.py
# Das Rohprotokoll: jede Benachrichtigung der Plattform im Originalzustand. Übernommen aus
# features/stats/store.py, Tabelle und Spalten unverändert.
#
# Tabelle und Spalte heißen weiterhin eventsub_log/subscription_type: dort liegen die
# bisherigen Daten, und ein Rename wäre eine Migration ohne Gegenwert.
#
# Alles hier ist blockierendes sqlite3 und muss von async Code aus per
# loop.run_in_executor(None, ...) aufgerufen werden.

import json

SCHEMA = """
CREATE TABLE IF NOT EXISTS eventsub_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_session_id INTEGER,
    subscription_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_eventsub_log_session ON eventsub_log (stream_session_id, id);
CREATE INDEX IF NOT EXISTS idx_eventsub_log_type ON eventsub_log (subscription_type);
"""


class RawLogStore:
    """`db` ist das Feature mit der Fähigkeit STORAGE (siehe features/sql_db)."""

    def __init__(self, db):
        self._db = db

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            conn.executescript(INDEXES)

    def record(self, session_id, event_type, payload):
        """Legt JEDE Plattform-Benachrichtigung im Rohzustand ab - auch die, für die es
        (noch) keinen Handler gibt, und auch außerhalb eines Streams (dann ohne Session).
        Damit geht nichts verloren, was die Plattform je gemeldet hat: neue Abos liefern
        sofort auswertbare Daten, ohne dass vorher Code dafür existieren muss. Die
        typisierten Tabellen der anderen Features bleiben die bequeme Auswertungsebene."""
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO eventsub_log (stream_session_id, subscription_type, payload) VALUES (?, ?, ?)",
                (session_id, event_type, json.dumps(payload, ensure_ascii=False)),
            )

    def recent(self, event_type=None, session_id=None, limit=100):
        """[(ts, event_type, payload_dict), ...] neueste zuerst - zum Nachschauen, was die
        Plattform tatsächlich geschickt hat."""
        where, params = [], []
        if event_type is not None:
            where.append("subscription_type = ?")
            params.append(event_type)
        if session_id is not None:
            where.append("stream_session_id = ?")
            params.append(session_id)
        where_sql = f"WHERE {' AND '.join(where)}" if where else ""
        params.append(limit)
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT ts, subscription_type, payload FROM eventsub_log {where_sql} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return [(ts, sub_type, json.loads(payload)) for ts, sub_type, payload in rows]

    def count(self, session_id):
        if session_id is None:
            return 0
        with self._db.connect() as conn:
            return conn.execute(
                "SELECT COUNT(*) FROM eventsub_log WHERE stream_session_id = ?", (session_id,)
            ).fetchone()[0]
