"""ChatPanelFeature: the Twitch-only-by-default scope, hide_commands, history bound, and
clearing on CHAT_CLEARED/STREAM_START."""

import json

import pytest

from core import events, feature as feature_api, platform as platform_api, runtime_config
from features.chat_panel.feature import ChatPanelFeature, DEFAULTS


class FakeServer:
    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, type_, data):
        self.broadcasts.append((type_, data))


class FakePlatform(platform_api.Platform):
    def __init__(self, name):
        self.name = name

    async def start(self):
        return

    async def close(self):
        return


def make_message(text, platform="twitch", user_name="someone"):
    return feature_api.Message(platform=platform, user_name=user_name, text=text)


def make_bus():
    # _in_scope() treats "no bus" as "show everything regardless of platform" (see its
    # comment) - a real bus with both platforms registered is needed so the "platforms"
    # config actually gets to filter, the same as it does in the running bot.
    bus = events.EventBus()
    bus.register(FakePlatform("twitch"))
    bus.register(FakePlatform("discord"))
    return bus


@pytest.fixture
def panel(tmp_path):
    f = ChatPanelFeature()
    path = tmp_path / "chat_panel.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    f._server = FakeServer()
    f._bus = make_bus()
    return f


# --- scope: Twitch-only by default -----------------------------------------------------

async def test_default_scope_accepts_twitch(panel):
    await panel.on_message(make_message("hi", platform="twitch"))
    assert len(panel._history) == 1


async def test_default_scope_excludes_discord(panel):
    await panel.on_message(make_message("hi", platform="discord"))
    assert len(panel._history) == 0


async def test_platforms_override_can_opt_discord_back_in(tmp_path):
    f = ChatPanelFeature()
    path = tmp_path / "chat_panel.json"
    path.write_text(json.dumps({"platforms": ["twitch", "discord"]}), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    f._server = FakeServer()
    f._bus = make_bus()

    await f.on_message(make_message("hi", platform="discord"))
    assert len(f._history) == 1


# --- hide_commands -------------------------------------------------------------------------

async def test_hide_commands_true_by_default_drops_bang_messages(panel):
    await panel.on_message(make_message("!uptime"))
    assert len(panel._history) == 0


async def test_hide_commands_false_lets_bang_messages_through(tmp_path):
    f = ChatPanelFeature()
    path = tmp_path / "chat_panel.json"
    path.write_text(json.dumps({"hide_commands": False}), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    f._server = FakeServer()

    await f.on_message(make_message("!uptime"))
    assert len(f._history) == 1


# --- history bound and broadcast --------------------------------------------------------

async def test_accepted_message_is_broadcast(panel):
    await panel.on_message(make_message("hello"))
    assert panel._server.broadcasts[0][0] == "message"
    assert panel._server.broadcasts[0][1]["text"] == "hello"


async def test_history_respects_max_messages(tmp_path):
    f = ChatPanelFeature()
    path = tmp_path / "chat_panel.json"
    path.write_text(json.dumps({"max_messages": 2}), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    f._server = FakeServer()

    for i in range(5):
        await f.on_message(make_message(f"msg{i}"))
    assert len(f._history) == 2
    assert [e["text"] for e in f._history] == ["msg3", "msg4"]


# --- clearing: CHAT_CLEARED and STREAM_START ------------------------------------------

async def test_chat_cleared_empties_history_and_broadcasts(panel):
    await panel.on_message(make_message("hello"))
    assert len(panel._history) == 1

    await panel.on_chat_cleared(platform="twitch")

    assert len(panel._history) == 0
    assert panel._server.broadcasts[-1] == ("clear", None)


async def test_chat_cleared_out_of_scope_platform_is_ignored(panel):
    await panel.on_message(make_message("hello"))
    await panel.on_chat_cleared(platform="discord")
    assert len(panel._history) == 1  # unaffected - discord is out of scope by default


async def test_stream_start_empties_history_and_broadcasts(panel):
    await panel.on_message(make_message("hello"))
    assert len(panel._history) == 1

    await panel.on_stream_start(platform="twitch", title="t", category="c")

    assert len(panel._history) == 0
    assert panel._server.broadcasts[-1] == ("clear", None)


async def test_stream_start_out_of_scope_platform_is_ignored(panel):
    await panel.on_message(make_message("hello"))
    await panel.on_stream_start(platform="discord", title="t", category="c")
    assert len(panel._history) == 1
