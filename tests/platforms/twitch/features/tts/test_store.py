"""TTS record (store.py) with a real SQLite file, and how the feature uses it: every message
kept, played time as a position in the stream, a clean queue per stream, and the queue of the
running stream surviving a restart."""

import http
from datetime import datetime, timedelta, UTC

import pytest

from features.sql_db.feature import SqlDbFeature
from platforms.twitch.features.tts import synth
from platforms.twitch.features.tts.feature import TtsFeature
from platforms.twitch.features.tts.store import TtsStore


class FakeSessions:
    def __init__(self, current=None, started_minutes_ago=90):
        self.current_session_id = current
        self.started = datetime.now(UTC) - timedelta(minutes=started_minutes_ago)

    async def session(self, session_id):
        return {"session_id": session_id, "title": f"Stream {session_id}",
                "started_at": self.started.strftime("%Y-%m-%d %H:%M:%S")}


class FakeOverlay:
    def __init__(self):
        self.sent = []

    def listeners(self, role):
        return 1

    async def push(self, type_, data, role):
        self.sent.append((type_, data))


class Bus:
    def __init__(self, db, sessions):
        self.features = {"storage": db, "sessions": sessions}

    def feature_with(self, capability):
        return self.features.get(capability)

    def subscribe(self, *args):
        pass

    async def publish(self, *args, **kwargs):
        return []


@pytest.fixture
def db(tmp_path):
    return SqlDbFeature(tmp_path / "test.db")


async def make_feature(db, sessions):
    f = TtsFeature()
    f.owner = "twitch"
    await f.setup(Bus(db, sessions))
    return f


@pytest.fixture
def fake_synth(monkeypatch):
    monkeypatch.setattr(TtsFeature, "_voice", lambda self, entry=None: synth.Voice("de_DE-x", None, None, "robot"))
    monkeypatch.setattr(synth, "synthesize", lambda *args: b"RIFF")


def test_store_records_and_lists(db):
    store = TtsStore(db)
    store.init_schema()
    first = store.add(7, "cheer", "twitch:1", "Viewer", 100, "Hallo", "Viewer hat 100 Bits dagelassen: Hallo")
    store.add(7, "command", "twitch:2", "Other", 0, "Moin", "Other sagt: Moin")
    store.mark_played(first, "de_DE-x", 7, 3723)
    history = store.history(7)
    assert [(m["user_name"], m["status"], m["stream_offset_seconds"]) for m in history] == [
        ("Viewer", "played", 3723), ("Other", "queued", None),
    ]
    assert store.sessions() == [{"session_id": 7, "messages": 2, "played": 1, "first_at": history[0]["queued_at"]}]


async def test_played_message_gets_its_position_in_the_stream(db, fake_synth):
    sessions = FakeSessions(current=1, started_minutes_ago=90)
    f = await make_feature(db, sessions)
    f._overlay = FakeOverlay()
    await f.on_raw_event("twitch", "channel.cheer", {"bits": 100, "message": "Cheer100 Hallo", "user_id": "1", "user_name": "Viewer"})
    assert (await f.release_next())[0] == http.HTTPStatus.OK
    row = f._store.history(1)[0]
    assert row["status"] == "played" and row["source"] == "cheer" and row["bits"] == 100
    assert row["message"] == "Hallo" and row["voice"] == "de_DE-x@robot"
    assert 90 * 60 - 5 <= row["stream_offset_seconds"] <= 90 * 60 + 5


async def test_every_stream_starts_with_a_clean_queue(db, fake_synth):
    sessions = FakeSessions(current=1)
    f = await make_feature(db, sessions)
    await f._enqueue("alt", "twitch:1", "Viewer", source="command")
    sessions.current_session_id = 2  # the next stream
    assert await f.queue_length() == 0
    assert f._store.history(1)[0]["status"] == "expired"
    await f._enqueue("neu", "twitch:1", "Viewer", source="command")
    assert await f.queue_length() == 1


async def test_restart_mid_stream_keeps_the_queue(db):
    sessions = FakeSessions(current=3)
    f = await make_feature(db, sessions)
    await f._enqueue("eins", "twitch:1", "Viewer", source="command")
    await f._enqueue("zwei", "twitch:2", "Other", source="command")
    restarted = await make_feature(db, sessions)
    assert [e["text"] for e in restarted._queue] == ["eins", "zwei"]
    # ...but a restart into the *next* stream does not carry it over.
    sessions.current_session_id = 4
    again = await make_feature(db, sessions)
    assert not again._queue
    assert {m["status"] for m in again._store.history(3)} == {"expired"}


async def test_stop_marks_the_playing_message(db, monkeypatch):
    sessions = FakeSessions(current=1)
    f = await make_feature(db, sessions)
    f._overlay = FakeOverlay()
    monkeypatch.setattr(TtsFeature, "_voice", lambda self, entry=None: synth.Voice("de_DE-x", None, None))
    import io
    import wave
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(1000)
        w.writeframes(b"\0\0" * 5000)
    monkeypatch.setattr(synth, "synthesize", lambda *args: buffer.getvalue())
    await f._enqueue("eins", "twitch:1", "Viewer", source="command")
    await f.release_next()
    await f.skip_next()
    assert f._store.history(1)[0]["status"] == "stopped"


async def test_offline_tts_takes_nothing(db):
    sessions = FakeSessions(current=None)
    f = await make_feature(db, sessions)
    await f.on_raw_event("twitch", "channel.cheer", {"bits": 100, "message": "Cheer100 Hallo", "user_id": "1", "user_name": "Viewer"})
    from core import feature as feature_api
    reply = await f.cmd_tts(feature_api.Message(platform="twitch", user_id="1", user_name="Viewer",
                                                arg_text="Hallo", is_privileged=True))
    assert "nur während des Streams" in reply
    assert not f._queue and f._store.history() == []


async def test_offline_tester_may_queue_but_nothing_is_recorded(db, fake_synth):
    from core import feature as feature_api
    sessions = FakeSessions(current=None)
    f = await make_feature(db, sessions)
    f.config.get = lambda key, default=None: ["WafobiTV"] if key == "offline_testers" else \
        __import__("platforms.twitch.features.tts.feature", fromlist=["DEFAULTS"]).DEFAULTS.get(key, default)
    f._overlay = FakeOverlay()
    assert await f.cmd_tts(feature_api.Message(platform="twitch", user_id="9", user_name="wafobitv",
                                               arg_text="Test", is_privileged=True)) is None
    assert len(f._queue) == 1 and f._queue[0]["id"] is None
    assert (await f.release_next())[0] == http.HTTPStatus.OK
    assert f._store.history() == []
    # a mod who is not on the list still gets the offline answer
    reply = await f.cmd_tts(feature_api.Message(platform="twitch", user_id="8", user_name="SomeMod",
                                                arg_text="Test", is_privileged=True))
    assert "nur während des Streams" in reply
