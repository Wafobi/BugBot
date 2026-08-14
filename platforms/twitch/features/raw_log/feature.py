# feature.py
# The raw log as a feature of its own (capabilities RECORDING and RAW_LOG).
#
# It used to sit in the statistics feature but has nothing to do with statistics: it evaluates
# nothing and keeps everything without exception that the platform reported - including what
# has no handler at all yet. That is the reason to make it separately switchable: it is the
# table that grows fastest, and the only one you might not want for space reasons.
#
# Unlike the chat record it does *not* need SESSIONS: outside a stream logging continues, just
# without a session assignment. That is exactly what the column is nullable for. If no SESSIONS
# feature is loaded at all, the raw log still runs - it merely loses the link to the stream.

import asyncio

from core import events, feature as feature_api

from .store import RawLogStore


class RawLogFeature(feature_api.Feature):
    name = "raw_log"
    provides = frozenset({feature_api.RECORDING, feature_api.RAW_LOG})
    requires = frozenset({feature_api.STORAGE})
    optional = frozenset({feature_api.SESSIONS})

    def __init__(self):
        self.store = None
        self._sessions = None

    async def setup(self, bus):
        db = bus.feature_with(feature_api.STORAGE)
        if db is None:
            raise RuntimeError("no feature with the STORAGE capability loaded")
        self.store = RawLogStore(db)
        # Optional, hence not in `requires`: if it is missing, stream_session_id stays NULL.
        self._sessions = bus.feature_with(feature_api.SESSIONS)
        await self._run(self.store.init_schema)

        bus.subscribe(events.RAW_EVENT, self.on_raw_event)

    @staticmethod
    async def _run(fn, *args):
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    @property
    def _session_id(self):
        return self._sessions.current_session_id if self._sessions is not None else None

    # --- Aufzeichnung (Push) --------------------------------------------------------

    async def on_raw_event(self, platform, event_type, payload):
        await self._run(self.store.record, self._session_id, event_type, payload)

    # --- Abfragen (Pull) ------------------------------------------------------------

    async def recent(self, event_type=None, session_id=None, limit=100):
        return await self._run(self.store.recent, event_type, session_id, limit)

    async def count(self, session_id):
        return await self._run(self.store.count, session_id)


def create_feature():
    return RawLogFeature()
