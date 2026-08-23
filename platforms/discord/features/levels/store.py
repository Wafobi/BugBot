# store.py
# The SQL of the levels feature. One table: discord_levels.
#
# The name is historical - the collected XP have always lived there, and there they stay. For
# a while there was a second, more "correctly" named table `levels`, copied into once at
# startup; that meant two tables with the same data, a copy routine that had to stay forever,
# and a switchover moment in which the two could drift apart. The same trade-off as with
# eventsub_log: a rename is a migration without a return.
#
# Instead the old table grows in place - the missing columns are added through the sql_db
# feature's schema helper (SQLite has no "ADD COLUMN IF NOT EXISTS"). The content stays the
# same throughout; nothing is moved.
#
# Everything here is blocking sqlite3 and has to be called from async code via
# loop.run_in_executor(None, ...).

import logging
import random
import time

log = logging.getLogger(__name__)

TABLE = "discord_levels"

# The value of the platform column comes from the feature (Feature.owner, see feature.py)
# and therefore no longer stands here. The column itself stays: that is how it is in the
# existing database, the unique index hangs on it, and it records which service the user_ids
# belong to.

# Fresh databases get the complete shape right away. Existing ones had only
# (user_id, xp, level, last_xp_ts) and are brought up to date below.
SCHEMA = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    platform TEXT NOT NULL,
    user_id TEXT NOT NULL,
    user_name TEXT,
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    last_xp_ts TEXT,
    PRIMARY KEY (platform, user_id)
);
"""

# The unique index is not merely acceleration: it is the target of the ON CONFLICT clause in
# add_message_xp. In an existing database the primary key is still user_id alone, so there
# would otherwise be no index over (platform, user_id) for the upsert to point at.
INDEXES = f"""
CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_ident ON {TABLE} (platform, user_id);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_xp ON {TABLE} (xp DESC);
"""


def xp_needed_for_next_level(level):
    """The formula MEE6 made common: how much additional XP is needed for the jump from
    `level` to `level + 1`."""
    return 5 * level * level + 50 * level + 100


def level_for_xp(xp):
    level = 0
    remaining = xp
    while remaining >= xp_needed_for_next_level(level):
        remaining -= xp_needed_for_next_level(level)
        level += 1
    return level


class LevelsStore:
    """`db` is the feature with the STORAGE capability (see features/sql_db), `platform` the
    value of the column of the same name - it comes from the feature (Feature.owner) and thus
    from the folder it lives in, rather than standing here as a string."""

    def __init__(self, db, platform):
        self._db = db
        self._platform = platform

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            # The two columns the old table lacks: without them the XP system would still
            # be nailed to one platform, and !top would have to look the display name up
            # through the platform API (which no longer works for users who left long ago).
            self._db.add_column_if_missing(conn, TABLE, "platform", "TEXT")
            self._db.add_column_if_missing(conn, TABLE, "user_name", "TEXT")
            # Everything that predates the column comes from the Discord-only era.
            conn.execute(f"UPDATE {TABLE} SET platform = 'discord' WHERE platform IS NULL")
            conn.executescript(INDEXES)
            self._fold_in_levels_table(conn)

    def _fold_in_levels_table(self, conn):
        """Fetches the holdings back out of the interim `levels` table and deletes it
        afterwards. Unlike the earlier copy routine this one is self-terminating: after one
        pass the table no longer exists, and the branch is a cheap 'does not exist' forever.

        The higher XP value wins. That makes the direction irrelevant - whichever of the two
        tables was written to in the meantime, nothing is lost.

        Everything is taken over, including rows of other platforms from the time when the XP
        system was switchable: nothing of that is read any more, but throwing it away is not a
        decision a cleanup routine should make."""
        if not self._db.table_exists(conn, "levels"):
            return
        cur = conn.execute(
            f"""
            INSERT INTO {TABLE} (platform, user_id, user_name, xp, level, last_xp_ts)
            SELECT platform, user_id, user_name, xp, level, last_xp_ts FROM levels
            -- The WHERE is mandatory: without it SQLite cannot tell the following ON
            -- CONFLICT from the ON of a join and aborts with a syntax error.
            WHERE true
            ON CONFLICT(platform, user_id) DO UPDATE SET
                user_name = COALESCE(excluded.user_name, {TABLE}.user_name),
                xp = excluded.xp,
                level = excluded.level,
                last_xp_ts = excluded.last_xp_ts
            WHERE excluded.xp > {TABLE}.xp
            """
        )
        conn.execute("DROP TABLE levels")
        log.info(f"{cur.rowcount} level entry/entries taken over from the levels table, table removed.")

    def add_message_xp(self, user_id, user_name, cooldown_seconds, xp_min, xp_max):
        """Awards (if the cooldown since the last award to this user has expired) a random
        xp_min..xp_max XP for a chat message. Returns (level, leveled_up) - level is the
        current (possibly new) level, leveled_up True if it just went up."""
        now = int(time.time())
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT xp, level, last_xp_ts FROM {TABLE} WHERE platform = ? AND user_id = ?",
                (self._platform, user_id),
            ).fetchone()
            xp, level, last_ts = row if row is not None else (0, 0, None)
            if last_ts is not None and now - int(last_ts) < cooldown_seconds:
                # Update the display name during the cooldown too: otherwise a renamed user
                # would keep their old name in !top forever.
                conn.execute(
                    f"UPDATE {TABLE} SET user_name = ? WHERE platform = ? AND user_id = ?",
                    (user_name, self._platform, user_id),
                )
                return level, False
            xp += random.randint(xp_min, xp_max)
            new_level = level_for_xp(xp)
            conn.execute(
                f"""
                INSERT INTO {TABLE} (platform, user_id, user_name, xp, level, last_xp_ts)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(platform, user_id) DO UPDATE SET
                    user_name = excluded.user_name, xp = excluded.xp,
                    level = excluded.level, last_xp_ts = excluded.last_xp_ts
                """,
                (self._platform, user_id, user_name, xp, new_level, str(now)),
            )
            return new_level, new_level > level

    def get_level(self, user_id):
        """(xp, level) for a single user, (0, 0) if they never received XP."""
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT xp, level FROM {TABLE} WHERE platform = ? AND user_id = ?", (self._platform, user_id)
            ).fetchone()
        return tuple(row) if row is not None else (0, 0)

    def find_by_name(self, user_name):
        """(user_id, xp, level) for a display name, or None. For !rank <name> - without it
        the caller would have to know the id, which nobody types in chat."""
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT user_id, xp, level FROM {TABLE} WHERE platform = ? AND lower(user_name) = ?",
                (self._platform, user_name.lower()),
            ).fetchone()
        return tuple(row) if row is not None else None

    def get_top(self, limit=10):
        """[(user_name, user_id, xp, level), ...] descending by xp."""
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT user_name, user_id, xp, level FROM {TABLE} WHERE platform = ? ORDER BY xp DESC LIMIT ?",
                (self._platform, limit),
            ).fetchall()
        return [tuple(row) for row in rows]
