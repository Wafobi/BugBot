# store.py
# Das SQL des Level-Features. Eine Tabelle: discord_levels.
#
# Der Name ist historisch - dort liegen die gesammelten XP seit jeher, und dort bleiben sie.
# Es gab zwischenzeitlich eine zweite, "richtiger" benannte Tabelle `levels`, in die beim
# Start einmalig kopiert wurde; das hieß: zwei Tabellen mit denselben Daten, eine
# Kopierroutine, die für immer stehenbleiben musste, und ein Umschaltmoment, in dem beide
# auseinanderlaufen konnten. Dieselbe Abwägung wie beim eventsub_log: ein Rename ist eine
# Migration ohne Gegenwert.
#
# Stattdessen wächst die alte Tabelle an Ort und Stelle mit - die fehlenden Spalten kommen
# über den Schema-Helfer des sql_db-Features dazu (SQLite kennt kein
# "ADD COLUMN IF NOT EXISTS"). Der Inhalt bleibt dabei durchgehend derselbe, es wird nichts
# umgezogen.
#
# Alles hier ist blockierendes sqlite3 und muss von async Code aus per
# loop.run_in_executor(None, ...) aufgerufen werden.

import random
import time

TABLE = "discord_levels"

# Der Wert der platform-Spalte kommt vom Feature (Feature.owner, siehe feature.py) und
# steht deshalb nicht mehr hier. Die Spalte selbst bleibt: sie steht so in der bestehenden
# Datenbank, der eindeutige Index hängt an ihr, und sie hält fest, zu welchem Dienst die
# user_ids gehören.

# Frische Datenbanken bekommen gleich die vollständige Form. Bestehende hatten nur
# (user_id, xp, level, last_xp_ts) und werden unten nachgezogen.
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

# Der eindeutige Index ist nicht bloß Beschleunigung: er ist das Ziel der ON-CONFLICT-Klausel
# in add_message_xp. In einer bestehenden Datenbank ist der Primärschlüssel noch user_id
# allein, dort gäbe es sonst keinen Index über (platform, user_id), auf den das Upsert
# zeigen könnte.
INDEXES = f"""
CREATE UNIQUE INDEX IF NOT EXISTS idx_{TABLE}_ident ON {TABLE} (platform, user_id);
CREATE INDEX IF NOT EXISTS idx_{TABLE}_xp ON {TABLE} (xp DESC);
"""


def xp_needed_for_next_level(level):
    """MEE6-übliche Formel: wie viel zusätzliche XP für den Sprung von `level` auf
    `level + 1` nötig ist."""
    return 5 * level * level + 50 * level + 100


def level_for_xp(xp):
    level = 0
    remaining = xp
    while remaining >= xp_needed_for_next_level(level):
        remaining -= xp_needed_for_next_level(level)
        level += 1
    return level


class LevelsStore:
    """`db` ist das Feature mit der Fähigkeit STORAGE (siehe features/sql_db), `platform`
    der Wert der gleichnamigen Spalte - er kommt vom Feature (Feature.owner) und damit aus
    dem Ordner, in dem es liegt, statt hier als Zeichenkette zu stehen."""

    def __init__(self, db, platform):
        self._db = db
        self._platform = platform

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)
            # Die beiden Spalten, die der alten Tabelle fehlen: ohne sie wäre das XP-System
            # weiterhin auf eine Plattform festgenagelt, und !top müsste den Anzeigenamen
            # über die Plattform-API nachschlagen (was für längst ausgetretene User nicht
            # mehr geht).
            self._db.add_column_if_missing(conn, TABLE, "platform", "TEXT")
            self._db.add_column_if_missing(conn, TABLE, "user_name", "TEXT")
            # Alles, was vor der Spalte da war, stammt aus der Discord-only-Zeit.
            conn.execute(f"UPDATE {TABLE} SET platform = 'discord' WHERE platform IS NULL")
            conn.executescript(INDEXES)
            self._fold_in_levels_table(conn)

    def _fold_in_levels_table(self, conn):
        """Holt die Bestände aus der zwischenzeitlichen `levels`-Tabelle zurück und löscht
        sie danach. Anders als die frühere Kopierroutine ist das hier selbstbeendend: nach
        einem Durchlauf gibt es die Tabelle nicht mehr, und der Zweig ist für immer ein
        billiges 'existiert nicht'.

        Der höhere XP-Stand gewinnt. Damit ist die Richtung egal - ob zwischendurch in die
        eine oder die andere Tabelle geschrieben wurde, es geht nichts verloren.

        Übernommen wird alles, auch Zeilen anderer Plattformen aus der Zeit, in der das
        XP-System umschaltbar war: gelesen wird davon nichts mehr, aber wegwerfen ist keine
        Entscheidung, die eine Aufräum-Routine treffen sollte."""
        if not self._db.table_exists(conn, "levels"):
            return
        cur = conn.execute(
            f"""
            INSERT INTO {TABLE} (platform, user_id, user_name, xp, level, last_xp_ts)
            SELECT platform, user_id, user_name, xp, level, last_xp_ts FROM levels
            -- Das WHERE muss sein: ohne es kann SQLite das folgende ON CONFLICT nicht vom
            -- ON eines Joins unterscheiden und bricht mit einem Syntaxfehler ab.
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
        print(f"🔁 {cur.rowcount} Level-Eintrag/-Einträge aus der levels-Tabelle übernommen, Tabelle entfernt.")

    def add_message_xp(self, user_id, user_name, cooldown_seconds, xp_min, xp_max):
        """Vergibt (falls die Cooldown-Zeit seit der letzten Vergabe an diesen User
        abgelaufen ist) zufällig xp_min..xp_max XP für eine Chat-Nachricht. Gibt
        (level, leveled_up) zurück - level ist das aktuelle (ggf. neue) Level,
        leveled_up True falls es dabei gerade gestiegen ist."""
        now = int(time.time())
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT xp, level, last_xp_ts FROM {TABLE} WHERE platform = ? AND user_id = ?",
                (self._platform, user_id),
            ).fetchone()
            xp, level, last_ts = row if row is not None else (0, 0, None)
            if last_ts is not None and now - int(last_ts) < cooldown_seconds:
                # Auch im Cooldown den Anzeigenamen nachziehen: sonst behielte ein
                # umbenannter User in !top für immer seinen alten Namen.
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
        """(xp, level) für einen einzelnen User, (0, 0) falls noch nie XP erhalten."""
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT xp, level FROM {TABLE} WHERE platform = ? AND user_id = ?", (self._platform, user_id)
            ).fetchone()
        return tuple(row) if row is not None else (0, 0)

    def find_by_name(self, user_name):
        """(user_id, xp, level) für einen Anzeigenamen, oder None. Für !rank <name> -
        ohne das müsste der Aufrufer die ID kennen, die im Chat aber niemand tippt."""
        with self._db.connect() as conn:
            row = conn.execute(
                f"SELECT user_id, xp, level FROM {TABLE} WHERE platform = ? AND lower(user_name) = ?",
                (self._platform, user_name.lower()),
            ).fetchone()
        return tuple(row) if row is not None else None

    def get_top(self, limit=10):
        """[(user_name, user_id, xp, level), ...] absteigend nach xp."""
        with self._db.connect() as conn:
            rows = conn.execute(
                f"SELECT user_name, user_id, xp, level FROM {TABLE} WHERE platform = ? ORDER BY xp DESC LIMIT ?",
                (self._platform, limit),
            ).fetchall()
        return [tuple(row) for row in rows]
