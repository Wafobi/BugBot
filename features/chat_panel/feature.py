"""The chat panel: a browser source in OBS that mirrors the chat.

It keeps a short, in-memory history of accepted messages and sends every new one to the
browser sources hanging on the listener (features/chat_panel/server.py) - the recent
history on connect, one frame per message afterwards.

Deliberately MESSAGE_ACCEPTED and not MESSAGE: a message that moderation deletes never
reaches this feature, so it never reaches the screen either. There is still no per-message
"remove this again" frame - the same reasoning as features/chat_log recording MESSAGE and not
MESSAGE_ACCEPTED, only pointed the other way: that one wants everything for the record, this
one wants only what a viewer was ever allowed to read. The one thing that does wipe the panel
after the fact is CHAT_CLEARED (see on_chat_cleared below): a platform-wide "clear chat" is not
about any one message, so it earns the exception.

The history lives in RAM and not in STORAGE: it exists to catch up a browser source that
reloads mid-stream, not to be queried afterwards - features/chat_log already is the record.
"""

from collections import deque

from core import events
from core import feature as feature_api
from core import runtime_config

from . import config as env
from .server import ChatPanelServer

DEFAULTS = {
    # Which platforms' chat reaches the panel. Empty = all that report a message - see
    # EventBus.resolve_platforms. A capability ("chat") survives a change of service better
    # than a name.
    "platforms": [],
    # Lines starting with "!" are usually meant for the bot, not for an audience reading
    # along - on by default, same reasoning as the overlay's ticker existing separately.
    "hide_commands": True,
    # How many recent messages a freshly connecting/reloading browser source catches up on.
    "max_messages": 50,
}


class ChatPanelFeature(feature_api.Feature):
    name = "chat_panel"

    # Offers nothing, needs nothing: purely a presentation of what MESSAGE_ACCEPTED already
    # carries.
    provides = frozenset()

    def __init__(self):
        self.config = runtime_config.for_package(__file__, DEFAULTS)
        self._bus = None
        self._server = None
        self._history = deque(maxlen=self._max_messages())
        self._next_id = 1

    def _max_messages(self):
        return max(1, int(self.config.get("max_messages", 50)))

    # --- Lifecycle ------------------------------------------------------------------

    async def setup(self, bus):
        self._bus = bus
        bus.subscribe(events.MESSAGE_ACCEPTED, self.on_message)
        bus.subscribe(events.CHAT_CLEARED, self.on_chat_cleared)

        if not env.CHAT_PANEL_TOKEN:
            print("ℹ️  No CHAT_PANEL_TOKEN set - no chat panel listener.")
            return

        self._server = ChatPanelServer(
            env.CHAT_PANEL_TOKEN, env.CHAT_PANEL_BIND, env.CHAT_PANEL_PORT,
            history=lambda: list(self._history),
            on_error=lambda message: print(f"⚠️  {message}"),
        )
        await self._server.start()

    async def close(self):
        if self._server is not None:
            await self._server.close()
            self._server = None

    # --- Scope ------------------------------------------------------------------------

    def _in_scope(self, platform_name):
        if self._bus is None:
            return True   # without a bus no resolution - then better to show than to lose
        scope = self._bus.resolve_platforms(self.config.get("platforms", ()))
        return scope is None or platform_name in scope

    # --- Topics ---------------------------------------------------------------------

    async def on_message(self, message):
        if not self._in_scope(message.platform):
            return
        if self.config.get("hide_commands", True) and message.text.lstrip().startswith("!"):
            return

        maxlen = self._max_messages()
        if self._history.maxlen != maxlen:
            self._history = deque(self._history, maxlen=maxlen)

        entry = {
            "id": self._next_id,
            "platform": message.platform,
            "user_name": message.user_name,
            "text": message.text,
            "is_privileged": message.is_privileged,
            "is_subscriber": message.is_subscriber,
        }
        self._next_id += 1
        self._history.append(entry)
        if self._server is not None:
            await self._server.broadcast("message", entry)

    async def on_chat_cleared(self, platform):
        if not self._in_scope(platform):
            return
        self._history.clear()
        if self._server is not None:
            await self._server.broadcast("clear", None)


def create_feature():
    return ChatPanelFeature()
