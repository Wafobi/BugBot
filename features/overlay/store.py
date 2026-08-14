"""The overlay's counters, stored persistently.

So far exactly one: the death counter. It lives here and not in features/stats because it
measures nothing that happens by itself - somebody counts it up. The table is kept general
nonetheless, so that the next counter (wins, crashes, coffees) needs no new one.

Counterpart to features/stats/store.py and with the same division of labour: SQL lives
here, when it runs lives in the feature.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS overlay_counters (
    name       TEXT PRIMARY KEY,
    value      INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class OverlayStore:
    def __init__(self, db):
        self._db = db

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)

    def under(self, prefix):
        """{name: value} of all counters under a prefix.

        substr() rather than LIKE: the prefix would only ever come from the code, but with
        LIKE this line would carry the silent assumption that it never contains a % or _.
        This way it carries nothing at all."""
        with self._db.connect() as conn:
            rows = conn.execute(
                "SELECT name, value FROM overlay_counters WHERE substr(name, 1, ?) = ?"
                " ORDER BY name",
                (len(prefix), prefix),
            ).fetchall()
        return {row[0]: int(row[1]) for row in rows}

    def get(self, name):
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT value FROM overlay_counters WHERE name = ?", (name,)
            ).fetchone()
        return int(row[0]) if row else 0

    def add(self, name, delta=1):
        """Increase by delta and return the new value. A counter not yet known starts at 0,
        and the INSERT creates it along the way."""
        with self._db.connect() as conn:
            conn.execute(
                """INSERT INTO overlay_counters (name, value) VALUES (?, ?)
                   ON CONFLICT(name) DO UPDATE SET value = value + excluded.value,
                                                   updated_at = datetime('now')""",
                (name, delta),
            )
            row = conn.execute(
                "SELECT value FROM overlay_counters WHERE name = ?", (name,)
            ).fetchone()
        return int(row[0]) if row else delta

    def set(self, name, value):
        with self._db.connect() as conn:
            conn.execute(
                """INSERT INTO overlay_counters (name, value) VALUES (?, ?)
                   ON CONFLICT(name) DO UPDATE SET value = excluded.value,
                                                   updated_at = datetime('now')""",
                (name, value),
            )
        return value
