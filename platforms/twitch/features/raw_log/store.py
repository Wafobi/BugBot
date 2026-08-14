# store.py
# The raw log: every notification from the platform in its original state. Taken over from
# features/stats/store.py, table and columns unchanged.
#
# Table and column are still called eventsub_log/subscription_type: that is where the existing
# data sits, and a rename would be a migration without a return.
#
# Everything here is blocking sqlite3 and has to be called from async code via
# loop.run_in_executor(None, ...).

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
    """`db` is the feature with the STORAGE capability (see features/sql_db)."""

    def __init__(self, db):
        self._db = db

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            conn.executescript(INDEXES)

    def record(self, session_id, event_type, payload):
        """Stores EVERY platform notification in its raw state - including those with no
        handler (yet), and outside a stream too (then without a session). That way nothing the
        platform ever reported is lost: new subscriptions deliver evaluable data immediately,
        without code having to exist for them beforehand. The other features' typed tables
        remain the convenient evaluation layer."""
        with self._db.connect() as conn:
            conn.execute(
                "INSERT INTO eventsub_log (stream_session_id, subscription_type, payload) VALUES (?, ?, ?)",
                (session_id, event_type, json.dumps(payload, ensure_ascii=False)),
            )

    def recent(self, event_type=None, session_id=None, limit=100):
        """[(ts, event_type, payload_dict), ...] newest first - for looking up what the
        platform actually sent."""
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
