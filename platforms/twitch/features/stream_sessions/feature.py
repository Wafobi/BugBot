# feature.py
# The stream session as a feature of its own (capabilities SESSIONS and RECORDING).
#
# It used to sit in the middle of the statistics feature: `StatsStore._session_id` was the
# state *every* recording helped itself to. That forced the chat record, the raw log and the
# highscores into the same package - you could not switch the record off and keep the
# counting, because both read the same private variable.
#
# Now "is a stream running, and which one?" is a capability other features request via
# `requires`. They stamp their rows with `current_session_id` instead of keeping books of
# their own - there remains a single truth about what belongs to which stream.
#
# Two things hang on a session knowing who it belongs to (Feature.owner, set by
# core/registry.py from the folder):
#
#   Only our own stream counts. If a second platform reports STREAM_START, it is ignored here
#   rather than creating a second session for the same evening - previously exactly that could
#   have happened, and the rule against it stood only as a comment in the OBS platform
#   ("deliberately reports no stream").
#   The row records whose stream it was. Without that column the table silently claims there
#   can only ever be one.

import asyncio

from core import events, feature as feature_api

from .store import SessionStore


class StreamSessionsFeature(feature_api.Feature):
    name = "stream_sessions"
    provides = frozenset({feature_api.SESSIONS, feature_api.RECORDING})
    requires = frozenset({feature_api.STORAGE})

    def __init__(self):
        self.store = None
        self._bus = None

    async def setup(self, bus):
        db = bus.feature_with(feature_api.STORAGE)
        if db is None:
            raise RuntimeError("no feature with the STORAGE capability loaded")
        self.store = SessionStore(db, self.owner)
        self._bus = bus
        await self._run(self.store.init_schema)

        bus.subscribe(events.STREAM_START, self.on_stream_start)
        bus.subscribe(events.STREAM_END, self.on_stream_end)
        bus.subscribe(events.STREAM_SEGMENT, self.on_stream_segment)

    @staticmethod
    async def _run(fn, *args):
        """Everything in the store is blocking sqlite3 - so it belongs in the executor."""
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    # --- Aufzeichnung (Push) --------------------------------------------------------

    async def on_stream_start(self, platform, title, category):
        if not self.handles(platform):
            return None
        return await self._run(self.store.start, title, category)

    async def on_stream_end(self, platform):
        """Closes the session and only then announces which id it was, via SESSION_ENDED.
        Whoever evaluates the ended stream hangs on that and not on STREAM_END - otherwise the
        order would depend on which features happened to subscribe first.

        The first non-empty answer from the SESSION_ENDED subscribers is passed through: the
        platform still collects its closing fields as the return value of publish(STREAM_END)
        (see platforms/twitch/bot.py) and need know nothing of the split into two topics."""
        if not self.handles(platform):
            return None
        session_id = await self._run(self.store.end)
        results = await self._bus.publish(events.SESSION_ENDED, session_id=session_id)
        return next((result for result in results if result), None)

    async def on_stream_segment(self, platform, title, category):
        if not self.handles(platform):
            return None
        return await self._run(self.store.record_segment, title, category)

    # --- Abfragen (Pull) ------------------------------------------------------------
    # Das, was Features mit `requires = {SESSIONS}` benutzen.

    @property
    def current_session_id(self):
        """The running session, or None when offline. Deliberately synchronous: this is a
        number in RAM and is asked for on every chat message."""
        return self.store.current_session_id

    async def last_session_id(self):
        return await self._run(self.store.last_session_id)

    async def session(self, session_id=None):
        """Master data of a session (default: the running one), or None."""
        return await self._run(self.store.get, self.store.resolve(session_id))

    async def segments(self, session_id=None):
        return await self._run(self.store.segments, self.store.resolve(session_id))

    async def recent(self, limit=10):
        return await self._run(self.store.recent, limit)

    async def totals(self):
        return await self._run(self.store.totals)


def create_feature():
    return StreamSessionsFeature()
