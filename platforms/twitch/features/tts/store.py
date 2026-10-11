"""Every TTS message, persisted: who, from what, what was said - and when it was queued and
when it was actually played, as a position in the stream (stream_offset_seconds), so the
moment of the answer can be found again in the VOD.

The table doubles as the queue's backing store: an entry is written when it is queued and
updated when it is played, stopped or given up on - so a restart or an update of the bot in
the middle of a stream no longer loses what was waiting (see TtsFeature.setup, restore()).
Every stream starts with a clean queue, though: what is still waiting when a new one starts
is expired, not carried over (expire_waiting()).

Statuses:
  queued   waiting for /tts/next
  played   released and sent to the browser source
  stopped  released, then cut off with /tts/skip
  expired  was still waiting when a new stream started (or, after a restart, belonged to
           another stream than the running one) - never read out

Counterpart to features/companion/store.py and every other *.py next to a feature: SQL lives
here, when it runs lives in the feature. Blocking sqlite3 - call via asyncio.to_thread.
Times are UTC, like everywhere in bugbot.db (datetime('now')).
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS tts_messages (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_session_id     INTEGER,
    source                TEXT NOT NULL,
    user_key              TEXT,
    user_name             TEXT,
    bits                  INTEGER NOT NULL DEFAULT 0,
    message               TEXT,
    spoken                TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'queued',
    voice                 TEXT,
    queued_at             TEXT NOT NULL DEFAULT (datetime('now')),
    played_at             TEXT,
    played_session_id     INTEGER,
    stream_offset_seconds INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tts_messages_status ON tts_messages (status, id);
CREATE INDEX IF NOT EXISTS idx_tts_messages_played_session ON tts_messages (played_session_id, id);
"""

_COLUMNS = (
    "id", "stream_session_id", "source", "user_key", "user_name", "bits", "message", "spoken",
    "status", "voice", "queued_at", "played_at", "played_session_id", "stream_offset_seconds",
)


class TtsStore:
    def __init__(self, db):
        self._db = db

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)

    def add(self, session_id, source, user_key, user_name, bits, message, spoken):
        """A newly queued message; returns its id."""
        with self._db.connect() as conn:
            cur = conn.execute(
                """INSERT INTO tts_messages (stream_session_id, source, user_key, user_name, bits, message, spoken)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (session_id, source, user_key, user_name, bits, message, spoken),
            )
            return cur.lastrowid

    def mark_played(self, entry_id, voice, session_id, offset_seconds):
        with self._db.connect() as conn:
            conn.execute(
                """UPDATE tts_messages SET status = 'played', voice = ?, played_at = datetime('now'),
                       played_session_id = ?, stream_offset_seconds = ?
                   WHERE id = ?""",
                (voice, session_id, offset_seconds, entry_id),
            )

    def mark_stopped(self, entry_id):
        with self._db.connect() as conn:
            conn.execute("UPDATE tts_messages SET status = 'stopped' WHERE id = ? AND status = 'played'", (entry_id,))

    def expire_waiting(self):
        """A new stream: whatever still waits is expired - every stream gets a clean queue.
        Returns how many."""
        with self._db.connect() as conn:
            return conn.execute("UPDATE tts_messages SET status = 'expired' WHERE status = 'queued'").rowcount

    def restore(self, session_id):
        """After a restart: what was still waiting *in this stream* (session_id; None = queued
        while offline, restored only while still offline), oldest first. Waiting entries of
        any other stream are expired instead - they belong to a queue that is over."""
        with self._db.connect() as conn:
            conn.execute(
                "UPDATE tts_messages SET status = 'expired' WHERE status = 'queued' AND stream_session_id IS NOT ?",
                (session_id,),
            )
            rows = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM tts_messages WHERE status = 'queued' ORDER BY id"
            ).fetchall()
        return [dict(zip(_COLUMNS, row)) for row in rows]

    def history(self, session_id=None, limit=500):
        """Messages of one stream (by the stream they were queued in), oldest first - or,
        without session_id, the most recent ones regardless of stream."""
        with self._db.connect() as conn:
            if session_id is None:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM tts_messages ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
                rows.reverse()
            else:
                rows = conn.execute(
                    f"SELECT {', '.join(_COLUMNS)} FROM tts_messages "
                    "WHERE stream_session_id = ? OR played_session_id = ? ORDER BY id LIMIT ?",
                    (session_id, session_id, limit),
                ).fetchall()
        return [dict(zip(_COLUMNS, row)) for row in rows]

    def sessions(self, limit=30):
        """Streams that had TTS, newest first: (session id, messages, played, first queued_at)."""
        with self._db.connect() as conn:
            rows = conn.execute(
                """SELECT stream_session_id, COUNT(*), SUM(status IN ('played', 'stopped')), MIN(queued_at)
                   FROM tts_messages WHERE stream_session_id IS NOT NULL
                   GROUP BY stream_session_id ORDER BY stream_session_id DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [{"session_id": s, "messages": n, "played": p or 0, "first_at": t} for s, n, p, t in rows]
