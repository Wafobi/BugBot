"""OverlayFeature's chat half: the Twitch-only-by-default scope, hide_commands, history
bound, and clearing on CHAT_CLEARED/STREAM_START. Formerly tests/features/chat_panel, folded
in along with the feature itself - see features/overlay/feature.py."""

import json
import time

import pytest

from core import events, feature as feature_api, platform as platform_api, runtime_config
from features.overlay.feature import DEFAULTS, OverlayFeature


class FakeServer:
    def __init__(self):
        self.broadcasts = []

    async def broadcast(self, type_, data):
        self.broadcasts.append((type_, data))


class FakeStats(feature_api.Feature):
    """Stands in for the STATS feature - stream_stats()/session_events() return whatever
    the test hands it, so on_platform_event's recap refresh (features/overlay/feature.py)
    can be exercised without a real database."""

    name = "stats"
    provides = frozenset({feature_api.STATS})

    def __init__(self, stats, events):
        self._stats = stats
        self._events = events

    async def stream_stats(self, session_id=None):
        return self._stats

    async def session_events(self, session_id):
        return self._events


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
    # _chat_in_scope() treats "no bus" as "show everything regardless of platform" (see its
    # comment) - a real bus with both platforms registered is needed so the "chat.platforms"
    # config actually gets to filter, the same as it does in the running bot.
    bus = events.EventBus()
    bus.register(FakePlatform("twitch"))
    bus.register(FakePlatform("discord"))
    return bus


@pytest.fixture
def overlay(tmp_path):
    f = OverlayFeature()
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps({}), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    f._server = FakeServer()
    f._bus = make_bus()
    return f


# --- scope: Twitch-only by default -----------------------------------------------------

async def test_default_scope_accepts_twitch(overlay):
    await overlay.on_message(make_message("hi", platform="twitch"))
    assert len(overlay._chat_history) == 1


async def test_default_scope_excludes_discord(overlay):
    await overlay.on_message(make_message("hi", platform="discord"))
    assert len(overlay._chat_history) == 0


async def test_platforms_override_can_opt_discord_back_in(tmp_path):
    f = OverlayFeature()
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps({"chat": {"platforms": ["twitch", "discord"]}}), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    f._server = FakeServer()
    f._bus = make_bus()

    await f.on_message(make_message("hi", platform="discord"))
    assert len(f._chat_history) == 1


# --- hide_commands -------------------------------------------------------------------------

async def test_hide_commands_true_by_default_drops_bang_messages(overlay):
    await overlay.on_message(make_message("!uptime"))
    assert len(overlay._chat_history) == 0


async def test_hide_commands_false_lets_bang_messages_through(tmp_path):
    f = OverlayFeature()
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps({"chat": {"hide_commands": False}}), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    f._server = FakeServer()

    await f.on_message(make_message("!uptime"))
    assert len(f._chat_history) == 1


# --- history bound and broadcast --------------------------------------------------------

async def test_accepted_message_is_broadcast(overlay):
    await overlay.on_message(make_message("hello"))
    assert overlay._server.broadcasts[0][0] == "message"
    assert overlay._server.broadcasts[0][1]["text"] == "hello"


async def test_history_respects_max_messages(tmp_path):
    f = OverlayFeature()
    path = tmp_path / "overlay.json"
    path.write_text(json.dumps({"chat": {"max_messages": 2}}), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    f._server = FakeServer()

    for i in range(5):
        await f.on_message(make_message(f"msg{i}"))
    assert len(f._chat_history) == 2
    assert [e["text"] for e in f._chat_history] == ["msg3", "msg4"]


# --- clearing: CHAT_CLEARED and STREAM_START ------------------------------------------

async def test_chat_cleared_empties_history_and_broadcasts(overlay):
    await overlay.on_message(make_message("hello"))
    assert len(overlay._chat_history) == 1

    await overlay.on_chat_cleared(platform="twitch")

    assert len(overlay._chat_history) == 0
    assert overlay._server.broadcasts[-1] == ("clear", None)


async def test_chat_cleared_out_of_scope_platform_is_ignored(overlay):
    await overlay.on_message(make_message("hello"))
    await overlay.on_chat_cleared(platform="discord")
    assert len(overlay._chat_history) == 1  # unaffected - discord is out of scope by default


async def test_stream_start_empties_history_and_broadcasts(overlay):
    await overlay.on_message(make_message("hello"))
    assert len(overlay._chat_history) == 1

    await overlay.on_stream_start(platform="twitch", title="t", category="c")

    assert len(overlay._chat_history) == 0
    assert overlay._server.broadcasts[-1] == ("clear", None)


async def test_stream_start_out_of_scope_platform_is_ignored(overlay):
    await overlay.on_message(make_message("hello"))
    await overlay.on_stream_start(platform="discord", title="t", category="c")
    assert len(overlay._chat_history) == 1


# --- ad_break: one timestamp plus a length, for the countdown bar to compute itself ------

async def test_ad_break_patches_started_at_and_seconds(overlay):
    before = time.time()
    await overlay.on_ad_break(platform="twitch", duration_seconds=90)
    type_, data = overlay._server.broadcasts[-1]
    assert type_ == "patch"
    assert data["ad_break_seconds"] == 90
    assert before <= data["ad_break_started_at"] <= time.time()


# --- stream_recap: re-read from STATS on snapshot() - i.e. when a browser source connects
# - not on every follow/sub/raid/cheer (those only ever update STATS, which subscribes to
# PLATFORM_EVENT on its own) and not on STREAM_END/SESSION_ENDED (those fire after the
# stream has already stopped) -----------------------------------------------------------

async def test_snapshot_refreshes_recap_from_stats_while_live(overlay):
    overlay._state["live"] = True
    overlay._stats = FakeStats(
        stats={
            "session_id": 1, "follows_gained": 3, "subs_gained": 1, "resubs": 0,
            "gift_subs": 0, "bits_cheered": 50, "raids_in": 0, "raid_viewers_in": 0,
            "chat_messages": 12,
        },
        events=[{"type": "follow", "user_name": "Kevin", "amount": 0}],
    )
    snapshot = await overlay.snapshot()
    assert snapshot["stream_recap"]["follows"] == 3
    assert snapshot["stream_recap"]["bits"] == 50
    assert snapshot["stream_recap"]["events"] == [{"type": "follow", "user_name": "Kevin", "amount": 0}]


async def test_snapshot_does_not_refresh_recap_while_offline(overlay):
    # live defaults to False - a database read for a source that isn't even connecting to
    # show a recap (bars.html, chat.html, ...) would be one on every single connection.
    overlay._stats = FakeStats(stats={"session_id": 1, "follows_gained": 3}, events=[])
    await overlay.snapshot()
    assert overlay._state["stream_recap"] is None


async def test_platform_event_does_not_touch_recap(overlay):
    # The one thing on_platform_event does for a recap-worthy event is nothing: STATS
    # already recorded it on its own. Regression guard for exactly the design this replaced
    # (a refresh from inside on_platform_event, which meant every single connection saw
    # stale numbers unless a follow/sub/raid/cheer happened to have just come in).
    overlay._state["live"] = True
    overlay._stats = FakeStats(stats={"session_id": 1, "follows_gained": 3}, events=[])
    await overlay.on_platform_event(platform="twitch", event_type="follow", user_name="Kevin")
    assert overlay._state["stream_recap"] is None


async def test_snapshot_recap_refresh_without_stats_is_a_no_op(overlay):
    # overlay fixture never sets _stats - stays None, same as a bot without STATS loaded.
    overlay._state["live"] = True
    await overlay.snapshot()
    assert overlay._state["stream_recap"] is None


async def test_snapshot_recap_refresh_without_a_running_session_is_a_no_op(overlay):
    overlay._state["live"] = True
    overlay._stats = FakeStats(stats=None, events=[])
    await overlay.snapshot()
    assert overlay._state["stream_recap"] is None
