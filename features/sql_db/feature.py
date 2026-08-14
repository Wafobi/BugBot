# feature.py
# Persistent storage as a feature of its own (capability STORAGE).
#
# Previously the SQLite access sat in the middle of core/stats.py: whoever wanted
# statistics got the database layout along with them, and whoever wanted to swap the
# database had to go past the statistics. Now this feature offers nothing but a connection
# and schema helpers; which tables exist is decided by each feature for itself in its
# setup().
#
# Deliberately no shared schema and no ORM layer: the features write their own SQL and only
# share the file and its handling. Swapping in different storage (Postgres, plain JSON)
# would mean building a second feature with the same capability - the features using it
# would only have to change their CREATE statements.

import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

from core import feature as feature_api, runtime_config

DEFAULT_DB_PATH = Path(__file__).resolve().parent / "bugbot.db"

DEFAULTS = {
    "db_path": "",
}


class SqlDbFeature(feature_api.Feature):
    name = "sql_db"
    provides = frozenset({feature_api.STORAGE})

    def __init__(self, path=None):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        # Order on purpose: BUGBOT_DB beats the configuration file. The file says where
        # this installation's storage lives; the environment variable is the intervention
        # from outside for exactly one run - a test that should not write into the real
        # bugbot.db, or a container with a different mount.
        self.path = Path(
            path
            or os.environ.get("BUGBOT_DB")
            or self.config.get("db_path", "")
            or DEFAULT_DB_PATH
        )

    async def setup(self, bus):
        # Only create/open, no tables: those belong to the features using them.
        with self.connect():
            pass
        print(f"🗄️ Storage: {self.path}")

    @contextmanager
    def connect(self):
        """sqlite3's built-in connection context manager does commit/rollback
        automatically, but does NOT close the connection itself - this wrapper takes care
        of that, so every call releases its file handle cleanly again.

        Every call opens its own connection rather than managing a shared one across
        threads - at the traffic of a single streamer's chat that is unproblematic and much
        simpler than pooling/WAL.

        Blocking: from async code always call it via loop.run_in_executor(None, ...)."""
        conn = sqlite3.connect(self.path)
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def add_column_if_missing(conn, table, column, decl):
        """SQLite has no 'ADD COLUMN IF NOT EXISTS' - hence asking PRAGMA table_info first.
        Needed because an already running bugbot.db does not yet have columns added later
        and would otherwise crash at startup."""
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
