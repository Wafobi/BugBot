"""Stats record only while a stream runs - except cheers, which every viewer's bits balance is
computed from."""

import pytest

from core import feature as feature_api
from features.sql_db.feature import SqlDbFeature
from features.stats.feature import StatsFeature
from platforms.twitch.features.raw_log.feature import RawLogFeature


class Sessions:
    def __init__(self, current=None):
        self.current_session_id = current


class Bus:
    def __init__(self, db, sessions):
        self.features = {feature_api.STORAGE: db, feature_api.SESSIONS: sessions}

    def feature_with(self, capability):
        return self.features.get(capability)

    def subscribe(self, *args):
        pass


@pytest.fixture
async def setup(tmp_path):
    sessions = Sessions()
    db = SqlDbFeature(tmp_path / "test.db")
    stats = StatsFeature()
    await stats.setup(Bus(db, sessions))
    return stats, sessions, db


def count(db, table):
    with db.connect() as conn:
        return conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]


async def test_offline_nothing_is_recorded_but_cheers(setup):
    stats, _, db = setup
    message = feature_api.Message(platform="twitch", user_name="Viewer", text="hi")
    await stats.on_message_accepted(message)
    await stats.on_command("twitch", "!uptime", "Viewer")
    await stats.on_mod_action("twitch", "Viewer", "caps", "delete")
    await stats.on_viewers("twitch", 12)
    await stats.on_platform_event("twitch", "follow", "Viewer")
    await stats.on_platform_event("twitch", "cheer", "Viewer", 100)
    assert (count(db, "messages"), count(db, "command_usage"), count(db, "moderation_actions"),
            count(db, "viewer_samples")) == (0, 0, 0, 0)
    assert await stats.user_event_total("cheer", "Viewer") == 100  # the balance survives
    assert count(db, "events") == 1


async def test_live_everything_is_recorded(setup):
    stats, sessions, db = setup
    sessions.current_session_id = 5
    await stats.on_message_accepted(feature_api.Message(platform="twitch", user_name="Viewer", text="hi"))
    await stats.on_platform_event("twitch", "follow", "Viewer")
    assert count(db, "messages") == 1 and count(db, "events") == 1


async def test_raw_log_only_while_live(tmp_path):
    sessions = Sessions()
    db = SqlDbFeature(tmp_path / "test.db")
    raw = RawLogFeature()
    await raw.setup(Bus(db, sessions))
    await raw.on_raw_event("twitch", "channel.follow", {"user_name": "x"})
    sessions.current_session_id = 1
    await raw.on_raw_event("twitch", "channel.follow", {"user_name": "y"})
    assert count(db, "eventsub_log") == 1
