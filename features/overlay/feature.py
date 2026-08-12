"""Das Overlay-Feature: was im Bild steht, und wann es sich ändert.

Es hält einen Zustand - läuft der Stream, wie viele schauen zu, wer kam zuletzt dazu,
welches Spiel, wie oft gestorben - und schickt jede Änderung an die Browser-Quellen, die
am Lauscher hängen (features/overlay/server.py). Beim Verbinden bekommt jede erst einmal
den vollständigen Zustand, danach nur noch die Unterschiede.

Der Zustand entsteht ausschließlich aus Topics des Busses. Das Feature fragt nirgends
nach und kennt keine Plattform beim Namen: was auf Twitch ein Follow ist, kommt hier als
PLATFORM_EVENT mit event_type="follow" an, und ein zweiter Dienst mit derselben Meldung
liefe ohne Änderung mit.

Dazu bringt es den Todeszähler mit. Der steht hier, weil das Overlay der Ort ist, an dem
man ihn sieht - und weil er über einen Chat-Befehl hochgezählt wird, nicht über eine
Datei auf dem OBS-Rechner, an die ein Bot auf dem Server ohnehin nicht herankäme.
"""

import asyncio
import time

from core import events
from core import feature as feature_api
from core import runtime_config

from . import config as env
from .server import OverlayServer
from .store import OverlayStore

# Schlüssel des Todeszählers in overlay_counters. Kein Konfigurationswert: er steht in
# der Datenbank und würde beim Umbenennen einen leeren Zähler zeigen.
DEATHS = "deaths"


class OverlayFeature(feature_api.Feature):
    name = "overlay"

    # Bietet nichts an: niemand sonst soll auf dem Bildschirminhalt aufbauen.
    provides = frozenset()

    # Beides mitgenommen, wenn da - keines davon ist nötig, um zu senden. Ohne STORAGE
    # lebt der Todeszähler nur bis zum Neustart, ohne SESSIONS fehlt die Startzeit aus
    # einer schon laufenden Session (der nächste STREAM_START holt sie nach).
    optional = frozenset({feature_api.STORAGE, feature_api.SESSIONS})

    def __init__(self):
        self.config = runtime_config.for_package(__file__)
        self.store = None
        self._server = None
        self._bus = None
        self._state = {
            "live": False,
            "started_at": None,   # Unix-Sekunden; die Uptime rechnet das Overlay selbst
            "title": "",
            "game": "",
            "viewers": 0,
            "last_follower": "",
            "last_sub": "",
            "last_raid": "",
            "deaths": 0,
        }

    # --- Lebenszyklus ---------------------------------------------------------------

    async def setup(self, bus):
        self._bus = bus

        db = bus.feature_with(feature_api.STORAGE)
        if db is not None:
            self.store = OverlayStore(db)
            await asyncio.to_thread(self.store.init_schema)
            self._state["deaths"] = await asyncio.to_thread(self.store.get, DEATHS)
        else:
            print("⚠️  Overlay ohne STORAGE: der Todeszähler beginnt bei jedem Start neu.")

        bus.subscribe(events.STREAM_START, self.on_stream_start)
        bus.subscribe(events.STREAM_END, self.on_stream_end)
        bus.subscribe(events.STREAM_SEGMENT, self.on_segment)
        bus.subscribe(events.VIEWERS, self.on_viewers)
        bus.subscribe(events.PLATFORM_EVENT, self.on_platform_event)

        if not env.OVERLAY_TOKEN:
            print("ℹ️  Kein OVERLAY_TOKEN gesetzt - kein Overlay-Lauscher. "
                  "Die Zähler-Befehle laufen trotzdem.")
            return

        self._server = OverlayServer(
            env.OVERLAY_TOKEN, env.OVERLAY_BIND, env.OVERLAY_PORT,
            snapshot=self.snapshot,
            on_error=lambda message: print(f"⚠️  {message}"),
        )
        await self._server.start()

    async def close(self):
        if self._server is not None:
            await self._server.close()
            self._server = None

    # --- Zustand --------------------------------------------------------------------

    def snapshot(self):
        """Der vollständige Zustand für eine frisch verbundene Browser-Quelle.

        Die Befehlsliste steckt mit drin und wird hier frisch geholt, nicht beim Start
        gemerkt: Befehle lassen sich zur Laufzeit umbenennen, und ein Overlay, das
        danach neu lädt, soll die neuen Namen zeigen."""
        return {**self._state, "commands": self._public_commands()}

    def _public_commands(self):
        """Die Befehle, die einem normalen Zuschauer offenstehen - der Ticker im Bild.

        mod_only fällt raus: was der Zuschauer nicht benutzen darf, muss er auch nicht
        lesen. Die Namen kommen aus dem Bus und tragen damit schon die Umbenennungen aus
        den JSON-Dateien."""
        if self._bus is None:
            return []
        try:
            commands = self._bus.commands()
        except Exception as error:
            print(f"⚠️  Overlay: Befehlsliste nicht lesbar: {error}")
            return []
        return [
            {"name": command.name, "help": command.help}
            for command in commands
            if not command.mod_only
        ]

    async def _patch(self, **changes):
        """Nur das schicken, was sich wirklich geändert hat. Zuschauerzahlen kommen im
        Takt der Stichprobe herein und sind meistens dieselben - jede davon als Nachricht
        auszusenden hieße, das Overlay ohne Anlass neu zeichnen zu lassen."""
        actual = {key: value for key, value in changes.items() if self._state.get(key) != value}
        if not actual:
            return
        self._state.update(actual)
        if self._server is not None:
            await self._server.broadcast("patch", actual)

    # --- Topics ---------------------------------------------------------------------

    async def on_stream_start(self, platform=None, title="", category="", **_):
        await self._patch(live=True, started_at=time.time(), title=title or "", game=category or "")

    async def on_stream_end(self, platform=None, **_):
        await self._patch(live=False, started_at=None, viewers=0)

    async def on_segment(self, platform=None, title="", category="", **_):
        await self._patch(title=title or "", game=category or "")

    async def on_viewers(self, platform=None, count=0, **_):
        await self._patch(viewers=int(count or 0))

    async def on_platform_event(self, platform=None, event_type="", user_name="", amount=0, **_):
        """Follows, Subs, Raids. Welche Art wohin gehört, steht in overlay.json - so
        kommt ein neuer Ereignistyp ohne Codeänderung ins Bild.

        Nur der Name wird gemerkt, nichts blinkt: die Einblendungen macht die Alertbox von
        Twitch als eigene Browser-Quelle, und zwei Stellen für dieselbe Meldung wären eine
        zu viel."""
        slot = (self.config.section("event_slots") or {}).get(event_type)
        if slot:
            await self._patch(**{slot: user_name})

    # --- Befehle --------------------------------------------------------------------

    async def cmd_deaths_show(self, message):
        return self.config.text("deaths.show", count=self._state["deaths"])

    async def cmd_deaths_add(self, message):
        count = await self._store_deaths(delta=1)
        await self._patch(deaths=count)
        return self.config.text("deaths.added", count=count)

    async def cmd_deaths_set(self, message):
        raw = (message.arg_text or "").strip()
        if not raw.lstrip("-").isdigit():
            return self.config.text("deaths.usage")
        count = await self._store_deaths(value=int(raw))
        await self._patch(deaths=count)
        return self.config.text("deaths.set", count=count)

    async def _store_deaths(self, delta=None, value=None):
        """Der Zähler in einem: mit STORAGE über die Ablage, ohne sie im Zustand. Beide
        Wege geben den neuen Stand zurück, damit die Aufrufer oben nichts unterscheiden
        müssen."""
        if self.store is not None:
            if value is not None:
                return await asyncio.to_thread(self.store.set, DEATHS, value)
            return await asyncio.to_thread(self.store.add, DEATHS, delta)
        return value if value is not None else self._state["deaths"] + delta

    def commands(self):
        return (
            feature_api.Command(name="!tode", handler=self.cmd_deaths_show,
                                help=self.config.text("help.show")),
            feature_api.Command(name="!tod", handler=self.cmd_deaths_add, mod_only=True,
                                help=self.config.text("help.add")),
            feature_api.Command(name="!todsetzen", handler=self.cmd_deaths_set, mod_only=True,
                                help=self.config.text("help.set")),
        )


def create_feature():
    return OverlayFeature()
