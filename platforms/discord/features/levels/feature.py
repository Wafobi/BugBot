# feature.py
# XP/Level als Feature von Discord (Fähigkeiten RECORDING und LEVELS).
#
# Steckte vorher in core/stats.py und wurde vom Discord-Bot aufgerufen - abschalten ging nur
# durch Auskommentieren. Jetzt hängt es an MESSAGE_ACCEPTED, bringt seine Befehle selbst mit
# und lässt sich über BUGBOT_FEATURES ganz weglassen.
#
# Es liegt bei Discord, weil es Discords XP-System ist: die Punktestände gehören zu diesem
# Server, und die Rollen, die daran hängen, gibt es nur dort. Zwischenzeitlich war es
# neutral und über levels.json auf Plattformen umschaltbar - eine Beweglichkeit, die
# niemand benutzt hat und die die Frage offenließ, was ein gemeinsamer Punktestand über
# zwei Dienste hinweg überhaupt bedeuten soll.
#
# MESSAGE_ACCEPTED meldet weiterhin *jede* Plattform, deshalb wird hier gefiltert: ein
# Feature bekommt die Topics des ganzen Bots zu sehen, auch wenn es einer Plattform gehört.
#
# Die Rollenvergabe beim Levelaufstieg bleibt bewusst nicht hier, obwohl beides jetzt unter
# platforms/discord/ liegt: welche Rolle es ab Level 5 gibt, steht in discord.json und ist
# Sache des Bots. Das Feature meldet nur LEVEL_UP auf den Bus - dieselbe Trennung wie
# vorher, nur kürzere Wege.

import asyncio
from pathlib import Path

from core import events, feature as feature_api, runtime_config

from .store import LevelsStore

# XP-Werte, Texte und Befehlsnamen: levels.json, bei Änderung neu gelesen.
CONFIG = runtime_config.LiveConfig(Path(__file__).parent / "levels.json")

# Welche Plattform gemeint ist, steht nirgends in dieser Datei: `self.owner` kommt von
# core/registry.py aus dem Ordner, in dem das Feature liegt (platforms/discord/features/...
# -> "discord"). Vorher stand der Name hier als Konstante - richtig, solange niemand den
# Ordner umbenennt, und eine der Stellen, an denen ein Feature mehr über die Welt behauptet,
# als es wissen kann.


class LevelsFeature(feature_api.Feature):
    name = "levels"
    provides = frozenset({feature_api.RECORDING, feature_api.LEVELS})
    requires = frozenset({feature_api.STORAGE})

    def __init__(self):
        # Auch der Bus greift darauf zu: er wendet den Abschnitt "command_names" beim
        # Einsammeln der Befehle an (siehe core/events.py).
        self.config = CONFIG
        self.store = None
        self._bus = None

    async def setup(self, bus):
        db = bus.feature_with(feature_api.STORAGE)
        if db is None:
            raise RuntimeError("kein Feature mit der Fähigkeit STORAGE geladen")
        self.store = LevelsStore(db, self.owner)
        self._bus = bus
        await self._run(self.store.init_schema)
        bus.subscribe(events.MESSAGE_ACCEPTED, self.on_message_accepted)

    @staticmethod
    async def _run(fn, *args):
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    async def on_message_accepted(self, message):
        if not self.handles(message.platform) or not message.user_id:
            return
        level, leveled_up = await self._run(
            self.store.add_message_xp,
            message.user_id, message.user_name,
            CONFIG.get("xp_cooldown_seconds", 60),
            CONFIG.get("xp_min", 15),
            CONFIG.get("xp_max", 25),
        )
        if leveled_up and CONFIG.get("announce_level_up", True):
            # Die auslösende Message wandert mit: nur sie weiß, in welchem Kanal der
            # Aufstieg gefeiert werden soll.
            await self._bus.publish(events.LEVEL_UP, message=message, level=level)

    # --- Befehle --------------------------------------------------------------------

    def commands(self):
        return (
            feature_api.Command("!rank", self.cmd_rank, help=CONFIG.text("help.rank")),
            feature_api.Command("!top", self.cmd_top, help=CONFIG.text("help.top")),
        )

    async def cmd_rank(self, message):
        """Die Befehle hängen wie alle Feature-Befehle in *jeder* Plattform - gefragt wird
        aber immer der Discord-Punktestand, denn einen anderen gibt es nicht. Nur der Blick
        auf sich selbst geht von außerhalb nicht: wer im Twitch-Chat schreibt, ist von dort
        aus keinem Discord-Konto zuzuordnen."""
        target = message.arg_text.strip()
        if not target:
            if not self.handles(message.platform):
                return CONFIG.text("rank.elsewhere")
            xp, level = await self._run(self.store.get_level, message.user_id)
            return CONFIG.text("rank.self", user=message.user_name, level=level, xp=xp)

        # @-Erwähnungen kommen je nach Plattform als <@123456> oder @name herein.
        cleaned = target.lstrip("<@!").rstrip(">").lstrip("@")
        if cleaned.isdigit():
            xp, level = await self._run(self.store.get_level, cleaned)
            return CONFIG.text("rank.other", user=target, level=level, xp=xp)

        found = await self._run(self.store.find_by_name, cleaned)
        if not found:
            return CONFIG.text("rank.unknown", user=cleaned)
        _, xp, level = found
        return CONFIG.text("rank.other", user=cleaned, level=level, xp=xp)

    async def cmd_top(self, message):
        top = await self._run(self.store.get_top, CONFIG.get("top_limit", 10))
        if not top:
            return CONFIG.text("top.none")
        lines = [
            CONFIG.text("top.line", rank=i, user=user_name or user_id, level=level, xp=xp)
            for i, (user_name, user_id, xp, level) in enumerate(top, start=1)
        ]
        return CONFIG.text("top.title") + "\n" + "\n".join(lines)


def create_feature():
    return LevelsFeature()
