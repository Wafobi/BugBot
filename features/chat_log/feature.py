# feature.py
# The chat record as a feature of its own (capabilities RECORDING and CHAT_LOG).
#
# This used to sit inside the statistics feature, although it does the opposite: statistics
# count, this one keeps the wording. As its own package it can be left out via
# BUGBOT_FEATURES without losing the counting - which is the reason for the split, because
# it is the only place in the bot that stores message content.
#
# Recording only happens during a running stream: the session comes from the feature with
# the SESSIONS capability, outside of one there is none, and then nothing happens here.

import asyncio

from core import events, feature as feature_api, runtime_config

from .store import ChatLogStore

# The values from chat_log.json - repeated here so the feature runs without the file too
# (see core/runtime_config.py).
#
# platforms is empty, and that is the statement: a neutral feature knows no platform names.
# It records whatever gets reported.
#
# Anyone wanting to restrict it should write a *capability* into chat_log.json rather than a
# service: ["stream"] means "only the chat of the platform whose stream is being recorded
# here" - the statement that used to read "twitch", just without the claim that it will
# always be Twitch. A name still works, but is reported at startup when no loaded platform
# bears it (see EventBus.resolve_platforms).
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
            raise RuntimeError("no feature with the STORAGE capability loaded")
        self._sessions = bus.feature_with(feature_api.SESSIONS)
        if self._sessions is None:
            raise RuntimeError("no feature with the SESSIONS capability loaded")
        self.store = ChatLogStore(db)
        await self._run(self.store.init_schema)

        # Deliberately MESSAGE and not MESSAGE_ACCEPTED: the messages deleted later are
        # precisely the ones you want to be able to read back afterwards.
        bus.subscribe(events.MESSAGE, self.on_message)

    @staticmethod
    async def _run(fn, *args):
        return await asyncio.get_event_loop().run_in_executor(None, fn, *args)

    def _in_scope(self, platform_name):
        """Resolved afresh on every message: the setting may contain capabilities, and
        which platform has which is only settled after startup. This is a set comparison in
        RAM - the same order of magnitude as the mtime check the configuration does per
        message anyway."""
        if self.bus is None:
            return True   # without a bus no resolution - then better to record than to lose
        scope = self.bus.resolve_platforms(self.config.get("platforms", ()))
        return scope is None or platform_name in scope

    # --- Recording (push) -----------------------------------------------------------

    async def on_message(self, message):
        if not self._in_scope(message.platform):
            return
        session_id = self._sessions.current_session_id
        if session_id is None:
            # Offline: record nothing. The plain counters of the statistics feature keep
            # running, only the wording stays limited to live time.
            return
        await self._run(
            self.store.record,
            session_id, message.platform, message.user_name, message.text, message.user_id,
        )

    # --- Queries (pull) -------------------------------------------------------------

    async def recent(self, session_id=None, limit=None):
        if limit is None:
            limit = self.config.get("recent_limit", 200)
        if session_id is None:
            session_id = self._sessions.current_session_id
        return await self._run(self.store.recent, session_id, limit)

    async def session_metrics(self, session_id):
        """(messages recorded, distinct chatters) - what the statistics feature needs for
        its stream evaluation."""
        return await self._run(self.store.session_metrics, session_id)

    async def total_logged(self):
        return await self._run(self.store.total_logged)


def create_feature():
    return ChatLogFeature()
