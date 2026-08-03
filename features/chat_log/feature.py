# feature.py
# Der Chat-Mitschnitt als eigenes Feature (Fähigkeiten RECORDING und CHAT_LOG).
#
# Steckte vorher im Statistik-Feature, obwohl es das Gegenteil davon tut: die Statistik
# zählt, dieses hier hebt den Wortlaut auf. Als eigenes Paket lässt es sich über
# BUGBOT_FEATURES weglassen, ohne die Zählung zu verlieren - das ist der Grund für die
# Trennung, denn es ist die einzige Stelle im Bot, die Nachrichteninhalte speichert.
#
# Mitgeschrieben wird nur während eines laufenden Streams: die Session kommt vom Feature mit
# der Fähigkeit SESSIONS, außerhalb gibt es keine, und dann passiert hier nichts.

import asyncio

from core import events, feature as feature_api, runtime_config

from .store import ChatLogStore

# Die Werte aus chat_log.json - hier noch einmal, damit das Feature auch ohne die Datei
# läuft (siehe core/runtime_config.py).
#
# platforms ist leer, und das ist die Aussage: ein neutrales Feature kennt keine
# Plattformnamen. Es schreibt mit, was gemeldet wird.
#
# Wer einschränken will, schreibt in chat_log.json am besten eine *Fähigkeit* statt eines
# Dienstes: ["stream"] heißt "nur der Chat der Plattform, deren Stream hier aufgezeichnet
# wird" - die Aussage, die früher als "twitch" dastand, nur ohne die Behauptung, dass es
# immer Twitch sein wird. Ein Name geht weiterhin, wird beim Start aber gemeldet, wenn ihn
# keine geladene Plattform trägt (siehe EventBus.resolve_platforms).
DEFAULTS = {
    "platforms": [],
    "recent_limit": 200,
}


class ChatLogFeature(feature_api.Feature):
    name = "chat_log"
    provides = frozenset({feature_api.RECORDING, feature_api.CHAT_LOG})
    requires = frozenset({feature_api.STORAGE, feature_api.SESSIONS})

    def __init__(self):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        self.store = None
        self._sessions = None

    async def setup(self, bus):
        db = bus.feature_with(feature_api.STORAGE)
        if db is None:
            raise RuntimeError("kein Feature mit der Fähigkeit STORAGE geladen")
        self._sessions = bus.feature_with(feature_api.SESSIONS)
        if self._sessions is None:
            raise RuntimeError("kein Feature mit der Fähigkeit SESSIONS geladen")
        self.store = ChatLogStore(db)
        await self._run(self.store.init_schema)

        # Bewusst MESSAGE und nicht MESSAGE_ACCEPTED: gerade die später gelöschten
        # Nachrichten sind die, die man im Nachhinein noch nachlesen können will.
        bus.subscribe(events.MESSAGE, self.on_message)

    @staticmethod
    async def _run(fn, *args):
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    def _in_scope(self, platform_name):
        """Bei jeder Nachricht neu aufgelöst: die Angabe darf Fähigkeiten enthalten, und
        welche Plattform welche hat, steht erst nach dem Start fest. Das ist ein
        Mengenvergleich im RAM - dieselbe Größenordnung wie der mtime-Blick, den die
        Konfiguration ohnehin pro Nachricht macht."""
        if self.bus is None:
            return True   # ohne Bus keine Auflösung - dann lieber mitschreiben als verlieren
        scope = self.bus.resolve_platforms(self.config.get("platforms", ()))
        return scope is None or platform_name in scope

    # --- Aufzeichnung (Push) --------------------------------------------------------

    async def on_message(self, message):
        if not self._in_scope(message.platform):
            return
        session_id = self._sessions.current_session_id
        if session_id is None:
            # Offline: nichts mitschneiden. Die reinen Zähler des Statistik-Features laufen
            # weiter, nur der Wortlaut bleibt auf die Live-Zeit begrenzt.
            return
        await self._run(
            self.store.record,
            session_id, message.platform, message.user_name, message.text, message.user_id,
        )

    # --- Abfragen (Pull) ------------------------------------------------------------

    async def recent(self, session_id=None, limit=None):
        if limit is None:
            limit = self.config.get("recent_limit", 200)
        if session_id is None:
            session_id = self._sessions.current_session_id
        return await self._run(self.store.recent, session_id, limit)

    async def session_metrics(self, session_id):
        """(mitgeschriebene Nachrichten, verschiedene Chatter) - was das Statistik-Feature
        für seine Stream-Auswertung braucht."""
        return await self._run(self.store.session_metrics, session_id)

    async def total_logged(self):
        return await self._run(self.store.total_logged)


def create_feature():
    return ChatLogFeature()
