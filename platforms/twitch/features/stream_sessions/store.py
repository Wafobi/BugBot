# store.py
# Die Stream-Sessions: wann lief ein Stream, unter welchem Titel, in welcher Kategorie.
# Übernommen aus features/stats/store.py - dort war die Session der stille Mittelpunkt, an
# dem alles andere hing (jede Aufzeichnung stempelte `self._session_id` mit), ohne dass man
# sie einzeln an- oder abschalten konnte.
#
# Tabellen und Spalten sind unverändert übernommen: eine bestehende bugbot.db findet hier
# genau das wieder, was vorher das Statistik-Feature angelegt hat.
#
# Alles hier ist blockierendes sqlite3 und muss von async Code aus per
# loop.run_in_executor(None, ...) aufgerufen werden.

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

# Dauer in Minuten, aus started_at und ended_at (bzw. jetzt, solange die Session läuft).
# Als Ausdruck ausgelagert, weil ihn get() und totals() beide brauchen.
_DURATION_MINUTES = (
    "CAST((julianday(COALESCE(ended_at, datetime('now'))) - julianday(started_at)) * 1440 AS INTEGER)"
)


class SessionStore:
    """`db` ist das Feature mit der Fähigkeit STORAGE (siehe features/sql_db)."""

    def __init__(self, db, platform=""):
        self._db = db
        # Wessen Stream hier aufgezeichnet wird. Kommt vom Feature (Feature.owner) und
        # landet in der Spalte: eine Session ohne diese Angabe behauptet stillschweigend,
        # es gebe nur einen Dienst, der streamen kann. Alte Zeilen haben sie nicht - dort
        # bleibt sie NULL, und das ist die ehrliche Auskunft "damals nicht erfasst".
        self._platform = platform
        # id der gerade laufenden Stream-Session, oder None wenn offline. Andere Features
        # fragen das über die Fähigkeit SESSIONS ab, statt es selbst zu führen - vorher gab
        # es diesen Zustand nur im Statistik-Feature, weshalb Chat-Mitschnitt und
        # Rohprotokoll dort mit drinstecken mussten.
        self._session_id = None

    # --- Schema ---------------------------------------------------------------------

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            # Bestehende Datenbanken kennen die Spalte noch nicht (SQLite hat kein
            # "ADD COLUMN IF NOT EXISTS", daher der Helfer aus dem sql_db-Feature). Alte
            # Sessions behalten NULL: nachträglich eine Plattform hineinzuschreiben wäre
            # geraten, nicht erfasst.
            self._db.add_column_if_missing(conn, "stream_sessions", "platform", "TEXT")
            conn.executescript(INDEXES)
        self._restore_current_session()

    def _restore_current_session(self):
        """Nach einem Neustart mitten im Stream gibt es eine offene stream_sessions-Zeile,
        aber noch keinen Prozess-Zustand - ohne das hier würde bis zum nächsten Streamstart
        nichts mehr der Session zugeordnet (und der Chat gar nicht mehr mitgeschnitten)."""
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
        """Gibt die id der neu angelegten Session zurück. Ab hier stempeln die anderen
        Features ihre Zeilen damit.

        Läuft bereits eine offene Session (Bot-Neustart mitten im Stream), wird deren id
        weiterverwendet statt eine zweite anzulegen - sonst bliebe die erste für immer offen
        und der Stream zerfiele in zwei Sessions."""
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
        """Setzt ended_at auf die zuletzt gestartete, noch offene Session und gibt deren id
        zurück (oder None, falls keine offene Session existiert)."""
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
        """Ein Titel-/Kategorie-Abschnitt innerhalb eines Streams. Wird beim Streamstart und
        danach bei jeder Änderung angelegt - so bleibt nachvollziehbar, was wann gespielt
        wurde, statt nur den Zustand beim Einschalten festzuhalten."""
        if self._session_id is None:
            return False
        with self._db.connect() as conn:
            last = conn.execute(
                "SELECT title, game_name FROM stream_segments WHERE stream_session_id = ? ORDER BY id DESC LIMIT 1",
                (self._session_id,),
            ).fetchone()
            # Die Plattform meldet auch Änderungen, die uns nicht interessieren (Sprache,
            # Tags) - unveränderte Abschnitte nicht doppelt anlegen.
            if last is not None and tuple(last) == (title, game_name):
                return False
            conn.execute(
                "INSERT INTO stream_segments (stream_session_id, title, game_name) VALUES (?, ?, ?)",
                (self._session_id, title, game_name),
            )
        return True

    # --- Abfragen -------------------------------------------------------------------

    def resolve(self, session_id):
        """Die laufende Session, falls der Aufrufer keine bestimmte nennt."""
        return self._session_id if session_id is None else session_id

    def last_session_id(self):
        """id der zuletzt gestarteten Session (auch wenn sie längst beendet ist), oder None.
        Damit beantwortet sich "der letzte Stream" ohne eine zweite Abfrage über alles."""
        with self._db.connect() as conn:
            row = conn.execute("SELECT id FROM stream_sessions ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row is not None else None

    def get(self, session_id):
        """Stammdaten einer Session als Dict, oder None. Die Kennzahlen dazu (Nachrichten,
        Events, Zuschauer) liegen bei den Features, die sie aufzeichnen."""
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
        """[(started_at, title, game_name), ...] chronologisch - der Verlauf eines Streams."""
        if session_id is None:
            return []
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT started_at, title, game_name FROM stream_segments WHERE stream_session_id = ? ORDER BY id",
                (session_id,),
            ).fetchall()
        return [tuple(row) for row in rows]

    def recent(self, limit=10):
        """[{session_id, started_at, ended_at, title, game_name}, ...] der letzten Streams,
        neueste zuerst - Einstiegspunkt, um dann in einen einzelnen Stream reinzuschauen.

        Bewusst nur die Stammdaten: die Nachrichtenzahl und der Zuschauer-Peak, die hier
        früher mit drinhingen, gehören dem Statistik-Feature und werden dort ergänzt."""
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
        """(Anzahl Sessions, Gesamtdauer in Minuten) über alle Streams - für die
        All-Time-Ausgabe von !stats."""
        with self._db.connect() as conn:
            count, minutes = conn.execute(
                f"SELECT COUNT(*), COALESCE(SUM({_DURATION_MINUTES}), 0) FROM stream_sessions"
            ).fetchone()
        return count, int(minutes)
