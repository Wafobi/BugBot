"""The TTS counts reported by platforms/twitch/features/tts (tts_queued/tts_played
PLATFORM_EVENTs): counted per stream in SQLite, and shown in the stream report only when
there were any."""

from features.sql_db.feature import SqlDbFeature
from features.stats.feature import StatsFeature
from features.stats.store import StatsStore


def test_session_metrics_count_tts_events(tmp_path):
    store = StatsStore(SqlDbFeature(tmp_path / "test.db"))
    store.init_schema()
    for _ in range(3):
        store.record_event(7, "twitch", "tts_queued", "Viewer")
    store.record_event(7, "twitch", "tts_played", "Viewer")
    store.record_event(8, "twitch", "tts_queued", "Other")  # a different stream
    metrics = store.session_metrics(7)
    assert metrics["tts_queued"] == 3
    assert metrics["tts_played"] == 1


def session_stats(**overrides):
    stats = {
        "duration_minutes": 90, "game_name": "Game", "peak_viewers": 1, "avg_viewers": 1,
        "chat_messages": 0, "unique_chatters": 0, "subs_gained": 0, "gift_subs": 0,
        "bits_cheered": 0, "follows_gained": 0, "raids_in": 0, "hypetrain_level": 0,
        "tts_queued": 0, "tts_played": 0,
    }
    stats.update(overrides)
    return stats


def test_report_shows_tts_only_when_there_was_any():
    feature = StatsFeature()
    names = [f.name for f in feature.session_fields(session_stats())]
    assert not any("TTS" in name for name in names)

    fields = feature.session_fields(session_stats(tts_queued=5, tts_played=4))
    tts = [f for f in fields if "TTS" in f.name]
    assert len(tts) == 1 and "4" in tts[0].value and "5" in tts[0].value
