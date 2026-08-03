# feature.py
# Die persistente Ablage als eigenes Feature (Fähigkeit STORAGE).
#
# Vorher lag der SQLite-Zugriff mitten in core/stats.py: wer Statistiken wollte, bekam
# das Datenbank-Layout mit, und wer die Datenbank tauschen wollte, musste an den
# Statistiken vorbei. Jetzt bietet dieses Feature nur Verbindung und Schema-Helfer an;
# welche Tabellen es gibt, entscheidet jedes Feature für sich in seinem setup().
#
# Bewusst kein gemeinsames Schema und keine ORM-Schicht: die Features schreiben ihr SQL
# selbst, teilen sich nur die Datei und die Handhabung. Ein Austausch gegen eine andere
# Ablage (Postgres, reines JSON) hieße, ein zweites Feature mit derselben Fähigkeit zu
# bauen - die Features, die es nutzen, müssten dafür nur ihre CREATE-Statements ändern.

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from core import feature as feature_api, runtime_config

DEFAULT_DB_PATH = Path(__file__).resolve().parent.parent.parent / "bugbot.db"

DEFAULTS = {
    "db_path": "",
}


class SqlDbFeature(feature_api.Feature):
    name = "sql_db"
    provides = frozenset({feature_api.STORAGE})

    def __init__(self, path=None):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        # Reihenfolge mit Absicht: BUGBOT_DB schlägt die Konfigurationsdatei. Die Datei
        # sagt, wo die Ablage dieser Installation liegt; die Umgebungsvariable ist der
        # Eingriff von außen für genau einen Lauf - ein Test, der nicht in die echte
        # bugbot.db schreiben soll, oder ein Container mit anderem Mount.
        self.path = Path(
            path
            or os.environ.get("BUGBOT_DB")
            or self.config.get("db_path", "")
            or DEFAULT_DB_PATH
        )

    async def setup(self, bus):
        # Nur anlegen/öffnen, keine Tabellen: die gehören den Features, die sie nutzen.
        with self.connect():
            pass
        print(f"🗄️ Ablage: {self.path}")

    @contextmanager
    def connect(self):
        """sqlite3s eingebauter Connection-Contextmanager committet/rollbackt zwar
        automatisch, schließt die Connection selbst aber NICHT - das übernimmt dieser
        Wrapper, damit jeder Aufruf sein File-Handle sauber wieder freigibt.

        Jeder Aufruf öffnet seine eigene Connection statt eine geteilte über Threads
        hinweg zu verwalten - beim Traffic eines einzelnen Streamer-Chats ist das
        unproblematisch und deutlich einfacher als Pooling/WAL.

        Blockierend: aus async Code immer per loop.run_in_executor(None, ...) aufrufen."""
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def add_column_if_missing(conn, table, column, decl):
        """SQLite kennt kein 'ADD COLUMN IF NOT EXISTS' - deshalb erst PRAGMA table_info
        fragen. Nötig, weil eine bereits laufende bugbot.db nachträglich hinzugekommene
        Spalten noch nicht hat und sonst beim Start crashen würde."""
        existing = {row[1] for row in conn.execute(f"PRAGMA table_info({table})")}
        if column not in existing:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")

    @staticmethod
    def table_exists(conn, table):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?", (table,)
        ).fetchone()
        return row is not None


def create_feature():
    return SqlDbFeature()
