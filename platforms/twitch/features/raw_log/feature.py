# feature.py
# Das Rohprotokoll als eigenes Feature (Fähigkeiten RECORDING und RAW_LOG).
#
# Lag vorher im Statistik-Feature, hat mit Statistik aber nichts zu tun: es wertet nichts
# aus, sondern hebt ausnahmslos alles auf, was die Plattform gemeldet hat - auch das, wofür
# es noch gar keinen Handler gibt. Das ist der Grund, es einzeln an- und abschaltbar zu
# machen: es ist die Tabelle, die am schnellsten wächst, und die einzige, die man aus
# Platzgründen vielleicht nicht haben will.
#
# Anders als der Chat-Mitschnitt braucht es SESSIONS *nicht*: außerhalb eines Streams wird
# weiter protokolliert, dann eben ohne Session-Zuordnung. Genau dafür ist die Spalte
# nullable. Ist gar kein SESSIONS-Feature geladen, läuft das Rohprotokoll trotzdem - es
# verliert nur den Bezug zum Stream.

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
            raise RuntimeError("kein Feature mit der Fähigkeit STORAGE geladen")
        self.store = RawLogStore(db)
        # Optional, deshalb nicht in `requires`: fehlt es, bleibt stream_session_id NULL.
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
