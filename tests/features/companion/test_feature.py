"""CompanionFeature: the bounded caches (M-2) and the bits-gate/seed logic they sit under."""

from features.companion.feature import CompanionFeature, MAX_CACHED_KEYS


class FakeStore:
    """Stands in for CompanionStore - in-memory, so these tests exercise the cache/store
    interaction (lazy-load-once-then-cache) without touching SQLite."""

    def __init__(self):
        self.seeds = {}
        self.spent = {}

    def get_seed(self, key):
        return self.seeds.get(key)

    def set_seed(self, key, seed):
        self.seeds[key] = seed

    def get_spent(self, key):
        return self.spent.get(key, 0)

    def add_spent(self, key, amount):
        self.spent[key] = self.spent.get(key, 0) + amount
        return self.spent[key]


def make_feature(with_store=True):
    f = CompanionFeature()
    f._store = FakeStore() if with_store else None
    return f


# --- _remember_bounded: caps and evicts only when a store can reload the value (M-2) -----

def test_remember_bounded_without_store_never_evicts():
    f = make_feature(with_store=False)
    for i in range(MAX_CACHED_KEYS + 50):
        f._remember_bounded(f._spend_cache, f"k{i}", i)
    # Without STORAGE the cache *is* the only copy of the data - evicting would silently
    # reset a chatter's spend count, a worse bug than unbounded growth.
    assert len(f._spend_cache) == MAX_CACHED_KEYS + 50


def test_remember_bounded_with_store_caps_and_evicts_oldest_first():
    f = make_feature(with_store=True)
    for i in range(MAX_CACHED_KEYS + 50):
        f._remember_bounded(f._spend_cache, f"k{i}", i)
    assert len(f._spend_cache) == MAX_CACHED_KEYS
    assert "k0" not in f._spend_cache
    assert "k49" not in f._spend_cache
    assert f"k{MAX_CACHED_KEYS + 49}" in f._spend_cache


def test_remember_bounded_update_in_place_preserves_insertion_order():
    f = make_feature(with_store=True)
    f._remember_bounded(f._spend_cache, "a", 1)
    f._remember_bounded(f._spend_cache, "b", 2)
    f._remember_bounded(f._spend_cache, "a", 999)  # update, not a fresh insert
    assert list(f._spend_cache.keys()) == ["a", "b"]
    assert f._spend_cache["a"] == 999


# --- _seed_for / _spent_for: lazy-load-once-then-cache from the store ---------------------

async def test_seed_for_loads_from_store_once_then_caches():
    f = make_feature()
    f._store.set_seed("twitch:1", "custom-seed")
    assert await f._seed_for("twitch:1", "default_name") == "custom-seed"
    # Change the store directly - the cached value must still answer, not a fresh lookup.
    f._store.set_seed("twitch:1", "different")
    assert await f._seed_for("twitch:1", "default_name") == "custom-seed"


async def test_seed_for_falls_back_to_default_name_without_a_custom_seed():
    f = make_feature()
    assert await f._seed_for("twitch:1", "wafobitv") == "wafobitv"


async def test_seed_for_without_store_falls_back_to_default_every_time():
    f = make_feature(with_store=False)
    assert await f._seed_for("twitch:1", "wafobitv") == "wafobitv"


async def test_spent_for_and_spend_accumulate_through_the_store():
    f = make_feature()
    assert await f._spent_for("twitch:1") == 0
    await f._spend("twitch:1", 100)
    assert await f._spent_for("twitch:1") == 100
    await f._spend("twitch:1", 50)
    assert await f._spent_for("twitch:1") == 150
    assert f._store.spent["twitch:1"] == 150


async def test_spend_without_store_still_accumulates_in_ram_only():
    f = make_feature(with_store=False)
    await f._spend("twitch:1", 100)
    assert await f._spent_for("twitch:1") == 100


# --- _is_excluded / _key -----------------------------------------------------------------

def make_message(user_name, platform="twitch", user_id=""):
    from core import feature as feature_api
    return feature_api.Message(platform=platform, user_id=user_id, user_name=user_name)


def test_is_excluded_matches_case_insensitively(tmp_path):
    f = CompanionFeature()
    f.config.get = lambda key, default=None: ["WafobiTV"] if key == "exclude_users" else default
    assert f._is_excluded(make_message("wafobitv")) is True
    assert f._is_excluded(make_message("someone_else")) is False


def test_key_prefers_user_id_over_name():
    assert CompanionFeature._key(make_message("Name", platform="twitch", user_id="123")) == "twitch:123"


def test_key_falls_back_to_lowercased_name_without_an_id():
    assert CompanionFeature._key(make_message("SomeName", platform="discord", user_id="")) == "discord:somename"
