"""EventBus: the platform/feature directories, pub/sub, command merging and announce()."""

import asyncio

import pytest

from core import events, feature as feature_api, platform as platform_api


class FakePlatform(platform_api.Platform):
    def __init__(self, name, capabilities=frozenset()):
        self.name = name
        self.capabilities = capabilities
        self.announced = []
        self.announce_result = True

    async def start(self):
        return

    async def close(self):
        return

    async def announce(self, announcement):
        self.announced.append(announcement)
        return self.announce_result


class FakeFeature(feature_api.Feature):
    def __init__(self, name, commands=()):
        self.name = name
        self._commands = commands

    def commands(self):
        return self._commands


def make_bus():
    return events.EventBus()


# --- Platform registry -------------------------------------------------------------------

def test_register_and_lookup():
    bus = make_bus()
    twitch = FakePlatform("twitch", frozenset({platform_api.CHAT}))
    bus.register(twitch)
    assert bus.get("twitch") is twitch
    assert bus.platforms == (twitch,)
    assert bus.with_capability(platform_api.CHAT) == (twitch,)
    assert bus.with_capability(platform_api.ANNOUNCE) == ()


def test_register_duplicate_name_raises():
    bus = make_bus()
    bus.register(FakePlatform("twitch"))
    with pytest.raises(ValueError):
        bus.register(FakePlatform("twitch"))


# --- resolve_platforms ---------------------------------------------------------------------

def test_resolve_platforms_empty_means_all():
    bus = make_bus()
    bus.register(FakePlatform("twitch"))
    assert bus.resolve_platforms([]) is None
    assert bus.resolve_platforms(None) is None


def test_resolve_platforms_by_name():
    bus = make_bus()
    bus.register(FakePlatform("twitch"))
    bus.register(FakePlatform("discord"))
    assert bus.resolve_platforms(["twitch"]) == {"twitch"}


def test_resolve_platforms_by_capability():
    bus = make_bus()
    bus.register(FakePlatform("twitch", frozenset({platform_api.CHAT})))
    bus.register(FakePlatform("discord", frozenset()))
    # "chat" is a capability, not a platform name - resolves to every platform bearing it.
    assert bus.resolve_platforms(["chat"]) == {"twitch"}


def test_resolve_platforms_unknown_capability_is_empty_not_an_error(caplog):
    bus = make_bus()
    bus.register(FakePlatform("twitch"))
    # A real capability nobody currently has - not an error, just nothing yet.
    assert bus.resolve_platforms(["stream"]) == set()


def test_resolve_platforms_unknown_token_is_reported_and_ignored(caplog):
    bus = make_bus()
    bus.register(FakePlatform("twitch"))
    with caplog.at_level("WARNING"):
        result = bus.resolve_platforms(["not_a_real_platform"])
    assert result == set()
    assert "not_a_real_platform" in caplog.text


# --- Feature registry ------------------------------------------------------------------

def test_register_feature_sets_bus_when_missing():
    bus = make_bus()
    f = FakeFeature("mod")
    assert f.bus is None
    bus.register_feature(f)
    assert f.bus is bus
    assert bus.feature("mod") is f


def test_register_feature_duplicate_raises():
    bus = make_bus()
    bus.register_feature(FakeFeature("mod"))
    with pytest.raises(ValueError):
        bus.register_feature(FakeFeature("mod"))


def test_features_with_and_feature_with():
    bus = make_bus()

    class Storage(FakeFeature):
        provides = frozenset({feature_api.STORAGE})

    s = Storage("sql_db")
    bus.register_feature(s)
    bus.register_feature(FakeFeature("moderation"))
    assert bus.features_with(feature_api.STORAGE) == (s,)
    assert bus.feature_with(feature_api.STORAGE) is s
    assert bus.feature_with(feature_api.MODERATION) is None


# --- commands() ----------------------------------------------------------------------

def make_command(name):
    return feature_api.Command(name=name, handler=lambda msg: None)


def test_commands_merges_across_features():
    bus = make_bus()
    bus.register_feature(FakeFeature("a", commands=(make_command("!a"),)))
    bus.register_feature(FakeFeature("b", commands=(make_command("!b"),)))
    cmds = bus.commands()
    assert set(cmds) == {"!a", "!b"}
    assert bus.command("!a").name == "!a"
    assert bus.command("!missing") is None


def test_commands_collision_first_wins_second_is_dropped(caplog):
    bus = make_bus()
    first = make_command("!dup")
    second = make_command("!dup")
    bus.register_feature(FakeFeature("a", commands=(first,)))
    bus.register_feature(FakeFeature("b", commands=(second,)))
    with caplog.at_level("WARNING"):
        cmds = bus.commands()
    assert cmds["!dup"] is first
    assert "!dup" in caplog.text


def test_commands_cache_invalidates_when_a_feature_config_reloads(tmp_path):
    from core import runtime_config

    config_path = tmp_path / "a.json"
    config_path.write_text('{"command_names": {}}', encoding="utf-8")

    bus = make_bus()
    f = FakeFeature("a", commands=(make_command("!a"),))
    f.config = runtime_config.LiveConfig(config_path)
    bus.register_feature(f)

    first = bus.commands()
    assert "!a" in first

    import os
    config_path.write_text('{"command_names": {"!a": "!renamed"}}', encoding="utf-8")
    # A distinct mtime, forced rather than waited for - many filesystems only have 1s mtime
    # resolution, and LiveConfig.reload() only picks up a change once the mtime differs.
    newer = (config_path.stat().st_mtime or 0) + 5
    os.utime(config_path, (newer, newer))
    # No explicit f.config.reload() here on purpose: _command_config_version() (see
    # core/events.py) now calls reload() on every feature's config itself before reading its
    # .version, so bus.commands() picks up a changed file on its own - it no longer depends
    # on something else having polled this same config first.

    second = bus.commands()
    assert "!renamed" in second
    assert "!a" not in second


# --- Pub/sub ------------------------------------------------------------------------

async def test_publish_calls_all_handlers_and_collects_results():
    bus = make_bus()
    calls = []

    async def handler_a(**payload):
        calls.append(("a", payload))
        return "result_a"

    async def handler_b(**payload):
        calls.append(("b", payload))
        return "result_b"

    bus.subscribe("topic", handler_a)
    bus.subscribe("topic", handler_b)
    results = await bus.publish("topic", x=1)

    assert results == ["result_a", "result_b"]
    assert calls == [("a", {"x": 1}), ("b", {"x": 1})]


async def test_publish_isolates_a_failing_handler():
    bus = make_bus()
    calls = []

    async def broken(**_):
        raise RuntimeError("boom")

    async def fine(**_):
        calls.append("fine")
        return "ok"

    bus.subscribe("topic", broken)
    bus.subscribe("topic", fine)
    results = await bus.publish("topic")

    # The broken handler contributes nothing to the result list, but does not stop `fine`
    # from running - the same isolation the review specifically praised.
    assert results == ["ok"]
    assert calls == ["fine"]


async def test_publish_reraises_cancelled_error():
    bus = make_bus()

    async def cancels(**_):
        raise asyncio.CancelledError()

    bus.subscribe("topic", cancels)
    with pytest.raises(asyncio.CancelledError):
        await bus.publish("topic")


# --- announce() ------------------------------------------------------------------------

async def test_announce_counts_only_successful_deliveries():
    bus = make_bus()
    delivering = FakePlatform("twitch", frozenset({platform_api.ANNOUNCE}))
    declining = FakePlatform("discord", frozenset({platform_api.ANNOUNCE}))
    declining.announce_result = False
    no_capability = FakePlatform("obs", frozenset())
    for p in (delivering, declining, no_capability):
        bus.register(p)

    announcement = platform_api.Announcement(kind="bug.report", title="t")
    delivered = await bus.announce(announcement)

    assert delivered == 1
    assert delivering.announced == [announcement]
    assert declining.announced == [announcement]
    assert no_capability.announced == []


async def test_announce_isolates_a_failing_platform():
    bus = make_bus()

    class Broken(FakePlatform):
        async def announce(self, announcement):
            raise RuntimeError("boom")

    broken = Broken("twitch", frozenset({platform_api.ANNOUNCE}))
    fine = FakePlatform("discord", frozenset({platform_api.ANNOUNCE}))
    bus.register(broken)
    bus.register(fine)

    delivered = await bus.announce(platform_api.Announcement(kind="bug.report", title="t"))
    assert delivered == 1
    assert fine.announced
