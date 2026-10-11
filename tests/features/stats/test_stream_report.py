"""Stream report contributions (STREAM_REPORT): stats counts every event type generically and
collects other features' fields/lines without naming any of them."""

from core import feature as feature_api
from features.sql_db.feature import SqlDbFeature
from features.stats.feature import StatsFeature
from features.stats.store import StatsStore


def test_session_metrics_count_every_event_type(tmp_path):
    store = StatsStore(SqlDbFeature(tmp_path / "test.db"))
    store.init_schema()
    for _ in range(3):
        store.record_event(7, "twitch", "something_new", "Viewer")
    store.record_event(8, "twitch", "something_new", "Other")  # a different stream
    assert store.session_metrics(7)["event_counts"] == {"something_new": 3}


class Contributor(feature_api.Feature):
    name = "contributor"
    provides = frozenset({feature_api.STREAM_REPORT})

    def stream_report(self, session_stats):
        n = session_stats["event_counts"].get("something_new", 0)
        return {"fields": (feature_api.Field("Neu", str(n)),), "lines": [f"{n} neue Dinge"]}


class Broken(feature_api.Feature):
    name = "broken"
    provides = frozenset({feature_api.STREAM_REPORT})

    def stream_report(self, session_stats):
        raise RuntimeError("boom")


class Bus:
    def __init__(self, *features):
        self._features = features

    def features_with(self, capability):
        return [f for f in self._features if f.supports(capability)]


def session_stats(**overrides):
    stats = {
        "duration_minutes": 90, "game_name": "Game", "peak_viewers": 1, "avg_viewers": 1,
        "chat_messages": 0, "unique_chatters": 0, "subs_gained": 0, "gift_subs": 0,
        "bits_cheered": 0, "follows_gained": 0, "raids_in": 0, "hypetrain_level": 0,
        "event_counts": {"something_new": 4},
    }
    stats.update(overrides)
    return stats


def test_contributions_are_collected_and_a_broken_one_is_skipped():
    feature = StatsFeature()
    feature.bus = Bus(Broken(), Contributor())
    names = [f.name for f in feature.session_fields(session_stats())]
    assert "Neu" in names
    assert feature.report_lines(session_stats()) == ["4 neue Dinge"]


def test_without_contributors_nothing_extra():
    feature = StatsFeature()
    feature.bus = Bus()
    assert feature.report_lines(session_stats()) == []
