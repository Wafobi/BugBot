# store.py
# The stream sessions: when a stream ran, under which title, in which category. Taken over
# from features/stats/store.py - there the session was the silent centre everything else hung
# on (every recording stamped `self._session_id` along), without being separately switchable.
#
# Tables and columns are taken over unchanged: an existing bugbot.db finds exactly what the
# statistics feature created before.
#
# Everything here is blocking sqlite3 and has to be called from async code via
# loop.run_in_executor(None, ...).

SCHEMA = """
CREATE TABLE IF NOT EXISTS stream_sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    platform TEXT,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    title TEXT,
    game_name TEXT
);
CREATE TABLE IF NOT EXISTS stream_segments (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stream_session_id INTEGER NOT NULL,
    title TEXT,
    game_name TEXT,
    started_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_stream_segments_session ON stream_segments (stream_session_id, id);
"""

# Duration in minutes, from started_at and ended_at (resp. now, while the session runs).
# Factored out as an expression because get() and totals() both need it.
_DURATION_MINUTES = (
    "CAST((julianday(COALESCE(ended_at, datetime('now'))) - julianday(started_at)) * 1440 AS INTEGER)"
)


class SessionStore:
    """`db` is the feature with the STORAGE capability (see features/sql_db)."""

    def __init__(self, db, platform=""):
        self._db = db
        # Whose stream is recorded here. Comes from the feature (Feature.owner) and lands in
        # the column: a session without it silently claims there is only one service that can
        # stream. Old rows do not have it - there it stays NULL, and that is the honest answer
        # "not recorded back then".
        self._platform = platform
        # id of the currently running stream session, or None when offline. Other features
        # ask for it through the SESSIONS capability instead of keeping it themselves -
        # previously this state existed only in the statistics feature, which is why the chat
        # record and the raw log had to sit in there too.
        self._session_id = None

    # --- Schema ---------------------------------------------------------------------

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            # Existing databases do not know the column yet (SQLite has no "ADD COLUMN IF
            # NOT EXISTS", hence the helper from the sql_db feature). Old sessions keep NULL:
            # writing a platform in after the fact would be guessed, not recorded.
            self._db.add_column_if_missing(conn, "stream_sessions", "platform", "TEXT")
            conn.executescript(INDEXES)
        self._restore_current_session()

    def _restore_current_session(self):
        """After a restart mid-stream there is an open stream_sessions row but no process
        state yet - without this, nothing would be assigned to the session until the next
        stream start (and the chat would not be recorded at all)."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM stream_sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        self._session_id = row[0] if row is not None else None

    @property
    def current_session_id(self):
        return self._session_id

    # --- Start/Ende -----------------------------------------------------------------

    def start(self, title, game_name):
        """Returns the id of the newly created session. From here on the other features stamp
        their rows with it.

        If an open session already exists (bot restart mid-stream), its id is reused rather
        than creating a second one - otherwise the first would stay open forever and the stream
        would fall apart into two sessions."""
        if self._session_id is not None:
            return self._session_id
        with self._db.connect() as conn:
            cur = conn.execute(
                "INSERT INTO stream_sessions (platform, started_at, title, game_name) "
                "VALUES (?, datetime('now'), ?, ?)",
                (self._platform, title, game_name),
            )
            self._session_id = cur.lastrowid
        self.record_segment(title, game_name)
        return self._session_id

    def end(self):
        """Sets ended_at on the most recently started, still open session and returns its id
        (or None when no open session exists)."""
        self._session_id = None
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT id FROM stream_sessions WHERE ended_at IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
            if row is None:
                return None
            session_id = row[0]
            conn.execute("UPDATE stream_sessions SET ended_at = datetime('now') WHERE id = ?", (session_id,))
            return session_id

    def record_segment(self, title, game_name):
        """A title/category segment within a stream. Created at stream start and on every
        change afterwards - which keeps it traceable what was played when, rather than only
        recording the state at switch-on."""
        if self._session_id is None:
            return False
        with self._db.connect() as conn:
            last = conn.execute(
                "SELECT title, game_name FROM stream_segments WHERE stream_session_id = ? ORDER BY id DESC LIMIT 1",
                (self._session_id,),
            ).fetchone()
            # The platform also reports changes we do not care about (language, tags) - do
            # not create unchanged segments twice.
            if last is not None and tuple(last) == (title, game_name):
                return False
            conn.execute(
                "INSERT INTO stream_segments (stream_session_id, title, game_name) VALUES (?, ?, ?)",
                (self._session_id, title, game_name),
            )
        return True

    # --- Abfragen -------------------------------------------------------------------

    def resolve(self, session_id):
        """The running session, when the caller names no particular one."""
        return self._session_id if session_id is None else session_id

    def last_session_id(self):
        """id of the most recently started session (even if it ended long ago), or None. That
        answers "the last stream" without a second query across everything."""
        with self._db.connect() as conn:
            row = conn.execute("SELECT id FROM stream_sessions ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row is not None else None

    def get(self, session_id):
        """Master data of a session as a dict, or None. The figures for it (messages, events,
        viewers) live with the features that record them."""
        if session_id is None:
            return None
        with self._db.connect() as conn:
            row = conn.execute(
                f"""
                SELECT id, platform, started_at, ended_at, title, game_name, {_DURATION_MINUTES}
                FROM stream_sessions WHERE id = ?
                """,
                (session_id,),
            ).fetchone()
        if row is None:
            return None
        session_id, platform, started_at, ended_at, title, game_name, duration_minutes = row
        segments = self.segments(session_id)
        return {
            "session_id": session_id,
            "platform": platform,
            "started_at": started_at,
            "ended_at": ended_at,
            "title": title,
            "game_name": game_name,
            "duration_minutes": duration_minutes,
            "is_live": ended_at is None,
            "segments": segments,
            "games_played": list(dict.fromkeys(game for _, _, game in segments if game)),
        }

    def segments(self, session_id):
        """[(started_at, title, game_name), ...] chronologically - the course of a stream."""
        if session_id is None:
            return []
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT started_at, title, game_name FROM stream_segments WHERE stream_session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [tuple(row) for row in rows]

    def recent(self, limit=10):
        """[{session_id, started_at, ended_at, title, game_name}, ...] of the last streams,
        newest first - the entry point for then looking into a single stream.

        Deliberately only the master data: the message count and the viewer peak that used to
        hang in here belong to the statistics feature and are added there."""
        with self._db.connect() as conn:
            rows = conn.execute(
                """
                SELECT id, started_at, ended_at, title, game_name
                FROM stream_sessions ORDER BY id DESC LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [
            {
                "session_id": sid, "started_at": started_at, "ended_at": ended_at,
                "title": title, "game_name": game_name,
            }
            for sid, started_at, ended_at, title, game_name in rows
        ]

    def totals(self):
        """(number of sessions, total duration in minutes) across all streams - for the
        all-time output of !stats."""
        with self._db.connect() as conn:
            count, minutes = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM({_DURATION_MINUTES}), 0) FROM stream_sessions"
            ).fetchone()
        return count, int(minutes)
