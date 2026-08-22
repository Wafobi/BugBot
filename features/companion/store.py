"""Persisted per-user state for the companion feature: custom seeds and spent bits.

Everything else about presence (features/companion/feature.py's self._companions) lives in
RAM only, like features/chat_panel's history - it says who is around right now. What lives
here does not, because both rows track something bits were actually spent on, and losing
that on a restart would mean charging for it twice:

  - companion_seeds - a custom look from !companion set. Paid once, kept forever - see
    features/companion/feature.py.
  - companion_spend - cumulative bits already spent on !companion <text> becoming visible.
    Each use costs min_bits_to_speak; what is left of someone's all-time cheer total (from
    features/stats) minus this is their spendable balance.

Same reasoning and the same shape as features/overlay/store.py's death counter.

Counterpart to features/stats/store.py and every other *.py next to a feature: SQL lives
here, when it runs lives in the feature.
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS companion_seeds (
    user_key TEXT PRIMARY KEY,
    seed     TEXT NOT NULL,
    set_at   TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS companion_spend (
    user_key TEXT PRIMARY KEY,
    spent    INTEGER NOT NULL DEFAULT 0,
    spent_at TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


class CompanionStore:
    def __init__(self, db):
        self._db = db

    def init_schema(self):
        with self._db.connect() as conn:
            conn.executescript(SCHEMA)

    # --- Custom seed (!companion set) ------------------------------------------------

    def get_seed(self, user_key):
        """The custom seed for this user, or None when they never set one - the caller then
        falls back to their name, same as everyone else's default companion."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT seed FROM companion_seeds WHERE user_key = ?", (user_key,)
            ).fetchone()
        return row[0] if row else None

    def set_seed(self, user_key, seed):
        with self._db.connect() as conn:
            conn.execute(
                """INSERT INTO companion_seeds (user_key, seed) VALUES (?, ?)
                   ON CONFLICT(user_key) DO UPDATE SET seed = excluded.seed,
                                                        set_at = datetime('now')""",
                (user_key, seed),
            )

    # --- Spent bits (!companion <text>) -----------------------------------------------

    def get_spent(self, user_key):
        """Bits this user has spent on !companion so far, all-time. 0 for someone who never
        has - there is no row to create until the first spend."""
        with self._db.connect() as conn:
            row = conn.execute(
                "SELECT spent FROM companion_spend WHERE user_key = ?", (user_key,)
            ).fetchone()
        return int(row[0]) if row else 0

    def add_spent(self, user_key, amount):
        """Books one more spend and returns the new all-time total - the same
        insert-or-add shape as features/overlay/store.py's counter, so a first-time spender
        needs no separate row creation from the caller."""
        with self._db.connect() as conn:
            conn.execute(
                """INSERT INTO companion_spend (user_key, spent, spent_at)
                   VALUES (?, ?, datetime('now'))
                   ON CONFLICT(user_key) DO UPDATE SET spent = spent + excluded.spent,
                                                        spent_at = datetime('now')""",
                (user_key, amount),
            )
            row = conn.execute(
                "SELECT spent FROM companion_spend WHERE user_key = ?", (user_key,)
            ).fetchone()
        return int(row[0]) if row else amount
