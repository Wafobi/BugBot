"""Die Zähler des Overlays, dauerhaft abgelegt.

Bisher genau einer: der Todeszähler. Er steht hier und nicht in features/stats, weil er
nichts misst, was von selbst passiert - jemand zählt ihn hoch. Die Tabelle ist trotzdem
allgemein gehalten, damit der nächste Zähler (Siege, Abstürze, Kaffee) keine neue
braucht.

Gegenstück zu features/stats/store.py und mit derselben Arbeitsteilung: hier steht SQL,
im Feature steht, wann es läuft.
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
        """{Name: Stand} aller Zähler unter einem Präfix.

        substr() statt LIKE: der Präfix käme zwar nur aus dem Code, aber mit LIKE hinge an
        dieser Zeile die stille Annahme, dass darin nie ein % oder _ steht. So hängt an ihr
        gar nichts."""
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
        """Um delta erhöhen und den neuen Stand zurückgeben. Ein noch unbekannter Zähler
        beginnt bei 0, das INSERT legt ihn nebenbei an."""
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
