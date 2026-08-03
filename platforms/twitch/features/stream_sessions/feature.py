# feature.py
# Die Stream-Session als eigenes Feature (Fähigkeiten SESSIONS und RECORDING).
#
# Vorher steckte sie mitten im Statistik-Feature: `StatsStore._session_id` war der Zustand,
# an dem sich *jede* Aufzeichnung bediente. Dadurch hingen Chat-Mitschnitt, Rohprotokoll und
# Highscores zwangsweise mit im selben Paket - man konnte nicht den Mitschnitt abschalten
# und die Zählung behalten, weil beide dieselbe private Variable lasen.
#
# Jetzt ist "läuft gerade ein Stream, und welcher?" eine Fähigkeit, die andere Features über
# `requires` anfordern. Sie stempeln ihre Zeilen mit `current_session_id`, statt eine eigene
# Buchführung zu haben - es bleibt bei einer einzigen Wahrheit darüber, was zu welchem
# Stream gehört.
#
# Zwei Dinge hängen daran, dass eine Session weiß, wem sie gehört (Feature.owner, gesetzt
# von core/registry.py aus dem Ordner):
#
#   Es zählt nur der eigene Stream. Meldet eine zweite Plattform STREAM_START, wird das
#   hier ignoriert statt eine zweite Session für denselben Abend anzulegen - vorher hätte
#   genau das passieren können, und die Regel dagegen stand nur als Kommentar in der
#   OBS-Plattform ("meldet bewusst keinen Stream").
#   Die Zeile hält fest, wessen Stream es war. Ohne diese Spalte behauptet die Tabelle
#   stillschweigend, es könne nur einen geben.

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
            raise RuntimeError("kein Feature mit der Fähigkeit STORAGE geladen")
        self.store = SessionStore(db, self.owner)
        self._bus = bus
        await self._run(self.store.init_schema)

        bus.subscribe(events.STREAM_START, self.on_stream_start)
        bus.subscribe(events.STREAM_END, self.on_stream_end)
        bus.subscribe(events.STREAM_SEGMENT, self.on_stream_segment)

    @staticmethod
    async def _run(fn, *args):
        """Alles im Store ist blockierendes sqlite3 - gehört damit in den Executor."""
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    # --- Aufzeichnung (Push) --------------------------------------------------------

    async def on_stream_start(self, platform, title, category):
        if not self.handles(platform):
            return None
        return await self._run(self.store.start, title, category)

    async def on_stream_end(self, platform):
        """Schließt die Session und gibt erst danach über SESSION_ENDED bekannt, welche id
        es war. Wer den beendeten Stream auswertet, hängt dort und nicht an STREAM_END -
        sonst hinge die Reihenfolge daran, in welcher die Features zufällig abonniert haben.

        Die erste nicht-leere Antwort der SESSION_ENDED-Abonnenten wird durchgereicht: die
        Plattform holt sich ihre Abschlussfelder weiterhin als Rückgabe von
        publish(STREAM_END) ab (siehe platforms/twitch/bot.py) und muss von der Aufteilung
        in zwei Topics nichts wissen."""
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
        """Die laufende Session, oder None wenn offline. Bewusst synchron: das ist eine
        Zahl im RAM und wird pro Chat-Nachricht abgefragt."""
        return self.store.current_session_id

    async def last_session_id(self):
        return await self._run(self.store.last_session_id)

    async def session(self, session_id=None):
        """Stammdaten einer Session (Default: der laufenden), oder None."""
        return await self._run(self.store.get, self.store.resolve(session_id))

    async def segments(self, session_id=None):
        return await self._run(self.store.segments, self.store.resolve(session_id))

    async def recent(self, limit=10):
        return await self._run(self.store.recent, limit)

    async def totals(self):
        return await self._run(self.store.totals)


def create_feature():
    return StreamSessionsFeature()
