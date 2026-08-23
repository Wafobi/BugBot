"""The overlay feature: what is on screen, and when it changes.

It holds a state - is the stream running, how many are watching, who joined last, which
game, how many deaths - and sends every change to the browser sources hanging on the
listener (features/overlay/server.py). On connect each first gets the complete state, only
the differences afterwards.

The state arises exclusively from bus topics. The feature asks nowhere for anything and
knows no platform by name: what is a follow on Twitch arrives here as a PLATFORM_EVENT with
event_type="follow", and a second service with the same notification would run along
unchanged.

It also brings the death counter along. That lives here because the overlay is where you see
it - and because it is counted up via a chat command, not via a file on the OBS machine that
a bot on the server could not reach anyway.
"""

import asyncio
import logging
import time

from core import events
from core import feature as feature_api
from core import runtime_config

from . import config as env
from .server import OverlayServer
from .store import OverlayStore

log = logging.getLogger(__name__)

# Key stem of the death counter in overlay_counters. Not a configuration value: it lives in
# the database and renaming it would show an empty counter.
#
# Counting happens per game: "deaths:Elden Ring". The stem on its own stays the pot for
# everything happening outside a known game - offline, or when the platform reports no
# category. That is why the old key from the time before per-game counts keeps exactly its
# previous meaning, and there is nothing to migrate.
DEATHS = "deaths"


class OverlayFeature(feature_api.Feature):
    name = "overlay"

    # Offers nothing: nobody else should build on what is on screen.
    provides = frozenset()

    # Both taken along if present - neither is needed in order to send. Without STORAGE the
    # death counter lives only until the restart; without SESSIONS the start time of an
    # already running session is missing (the next STREAM_START makes up for it).
    optional = frozenset({feature_api.STORAGE, feature_api.SESSIONS})

    def __init__(self):
        self.config = runtime_config.for_package(__file__)
        self.store = None
        self._server = None
        self._bus = None
        # Substitute storage without STORAGE: {key: value}, only until the restart.
        self._memory = {}
        self._state = {
            "live": False,
            "started_at": None,   # unix seconds; the overlay computes the uptime itself
            "title": "",
            "game": "",
            "viewers": 0,
            "last_follower": "",
            "last_sub": "",
            "last_raid": "",
            "deaths": 0,
        }

    # --- Lifecycle ------------------------------------------------------------------

    async def setup(self, bus):
        self._bus = bus

        db = bus.feature_with(feature_api.STORAGE)
        if db is not None:
            self.store = OverlayStore(db)
            await asyncio.to_thread(self.store.init_schema)
            # At startup no game is known yet - the first STREAM_START makes up for that.
            self._state["deaths"] = await self._read_deaths(self._deaths_key())
        else:
            log.warning("Overlay without STORAGE: the death counter starts over on every start.")

        bus.subscribe(events.STREAM_START, self.on_stream_start)
        bus.subscribe(events.STREAM_END, self.on_stream_end)
        bus.subscribe(events.STREAM_SEGMENT, self.on_segment)
        bus.subscribe(events.VIEWERS, self.on_viewers)
        bus.subscribe(events.PLATFORM_EVENT, self.on_platform_event)

        if not env.OVERLAY_TOKEN:
            log.info("No OVERLAY_TOKEN set - no overlay listener. "
                     "The counter commands run regardless.")
            return

        self._server = OverlayServer(
            env.OVERLAY_TOKEN, env.OVERLAY_BIND, env.OVERLAY_PORT,
            snapshot=self.snapshot,
            on_error=lambda message: log.warning(message),
        )
        await self._server.start()

    async def close(self):
        if self._server is not None:
            await self._server.close()
            self._server = None

    # --- State ----------------------------------------------------------------------

    def snapshot(self):
        """The complete state for a freshly connected browser source.

        The command list is part of it and is fetched fresh here rather than remembered at
        startup: commands can be renamed at runtime, and an overlay reloading afterwards
        should show the new names."""
        return {**self._state, "commands": self._public_commands()}

    def _public_commands(self):
        """The commands open to an ordinary viewer - the ticker on screen.

        mod_only drops out: what the viewer may not use, they need not read either. The names
        come from the bus and therefore already carry the renames from the JSON files - hence
        .values() and not the keys: bus.commands() returns {name: Command}, and the Command
        in it carries the actual name.

        All inside one try: if the list fails, that should cost the ticker and not the whole
        state. Previously the loop sat outside, and an exception in it took the state frame
        with it - the page then connected and never got any data."""
        if self._bus is None:
            return []
        try:
            return [
                {"name": command.name, "help": command.help}
                for command in self._bus.commands().values()
                if not command.mod_only
            ]
        except Exception as error:
            log.warning(f"Overlay: Befehlsliste nicht lesbar: {error}")
            return []

    async def _patch(self, **changes):
        """Send only what actually changed. Viewer counts arrive at the sampling tick and
        are mostly the same - sending each of them as a message would mean redrawing the
        overlay for no reason."""
        actual = {key: value for key, value in changes.items() if self._state.get(key) != value}
        if not actual:
            return
        self._state.update(actual)
        if self._server is not None:
            await self._server.broadcast("patch", actual)

    # --- Topics ---------------------------------------------------------------------

    async def on_stream_start(self, platform=None, title="", category="", **_):
        await self._patch(live=True, started_at=time.time(), title=title or "", game=category or "")
        await self._refresh_deaths()

    async def on_stream_end(self, platform=None, **_):
        await self._patch(live=False, started_at=None, viewers=0)

    async def on_segment(self, platform=None, title="", category="", **_):
        """Title or category change. On a change of game a different counter belongs on
        screen - otherwise the previous game's would stay standing."""
        before = self._state["game"]
        await self._patch(title=title or "", game=category or "")
        if self._state["game"] != before:
            await self._refresh_deaths()

    async def on_viewers(self, platform=None, count=0, **_):
        await self._patch(viewers=int(count or 0))

    async def on_platform_event(self, platform=None, event_type="", user_name="", amount=0, **_):
        """Follows, subs, raids. Which kind belongs where is in overlay.json - so a new
        event type gets on screen without a code change.

        Only the name is remembered, nothing flashes: the on-screen alerts are done by
        Twitch's own alertbox as a separate browser source, and two places for the same
        notice would be one too many."""
        slot = (self.config.section("event_slots") or {}).get(event_type)
        if slot:
            await self._patch(**{slot: user_name})

    # --- Commands -------------------------------------------------------------------

    async def cmd_deaths_show(self, message):
        """Without an argument the running title, with one any other - so "how often did I
        die in X" can be asked without currently playing X.

        The argument is *looked up*, not adopted, and the answer uses the stored name.
        Otherwise the bot would speak arbitrary chat text - and what it says bypasses
        moderation, because it is not the user writing but the bot. An unknown game therefore
        gets an answer without any input in it."""
        asked = (message.arg_text or "").strip()
        if asked:
            game = await self._find_game(asked)
            if game is None:
                return self.config.text("deaths.unknown_game")
        else:
            game = self._game()
        count = await self._read_deaths(self._deaths_key(game))
        return self._say("deaths.show", game, count=count)

    async def cmd_deaths_add(self, message):
        game = self._game()
        count = await self._add_deaths(self._deaths_key(game), 1)
        await self._patch(deaths=count)
        return self._say("deaths.added", game, count=count)

    async def cmd_deaths_set(self, message):
        raw = (message.arg_text or "").strip()
        if not raw.lstrip("-").isdigit():
            return self.config.text("deaths.usage")
        game = self._game()
        count = await self._set_deaths(self._deaths_key(game), int(raw))
        await self._patch(deaths=count)
        return self._say("deaths.set", game, count=count)

    def _say(self, key, game, **values):
        """The same notice in two versions: with a game name and without. A single text with
        {game} would not do - without a known game a filler word would stand there, and the
        sentence would read crooked."""
        if game:
            return self.config.text(key, game=game, **values)
        return self.config.text(f"{key}_no_game", **values)

    # --- The counter -----------------------------------------------------------------

    def _game(self):
        return self._state["game"].strip()

    def _deaths_key(self, game=None):
        """"deaths:Elden Ring" per game, "deaths" for everything without a known category."""
        game = (self._game() if game is None else game).strip()
        return f"{DEATHS}:{game}" if game else DEATHS

    async def _find_game(self, asked):
        """The stored name for a query, or None.

        Case-insensitive, so that "!tode elden ring" hits - but what comes back is always the
        name from storage, never the typed one. That is the entire protection: what the bot
        says then comes from the category the platform reported."""
        prefix = f"{DEATHS}:"
        if self.store is None:
            known = [k[len(prefix):] for k in self._memory if k.startswith(prefix)]
        else:
            known = [k[len(prefix):] for k in (await asyncio.to_thread(self.store.under, prefix))]

        asked = asked.casefold()
        for game in known:
            if game.casefold() == asked:
                return game
        return None

    async def _refresh_deaths(self):
        """Bring the count of the currently running game on screen."""
        await self._patch(deaths=await self._read_deaths(self._deaths_key()))

    # Three thin wrappers around storage, so the callers above need not know whether a
    # STORAGE feature exists. Without one the count lives only until the restart.
    async def _read_deaths(self, key):
        if self.store is None:
            return self._memory.get(key, 0)
        return await asyncio.to_thread(self.store.get, key)

    async def _add_deaths(self, key, delta):
        if self.store is None:
            self._memory[key] = self._memory.get(key, 0) + delta
            return self._memory[key]
        return await asyncio.to_thread(self.store.add, key, delta)

    async def _set_deaths(self, key, value):
        if self.store is None:
            self._memory[key] = value
            return value
        return await asyncio.to_thread(self.store.set, key, value)

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
