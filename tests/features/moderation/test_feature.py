"""ModerationFeature: escalation (delete -> timeout at timeout_threshold), the sweep that
bounds _violations (M-2), and _mask()."""

import json
from datetime import datetime, timedelta

import pytest

from core import feature as feature_api, runtime_config
from features.moderation.feature import DEFAULTS, ModerationFeature, _mask, STALE_VIOLATION_HOURS


def make_message(text, platform="twitch", user_name="troll", user_id="1",
                  is_privileged=False, is_subscriber=False):
    return feature_api.Message(
        platform=platform, user_id=user_id, user_name=user_name, text=text,
        is_privileged=is_privileged, is_subscriber=is_subscriber,
    )


@pytest.fixture
def mod(tmp_path):
    """A ModerationFeature over a temp-file config with known settings - decoupled from
    whatever the real, shipped moderation.json happens to contain."""
    f = ModerationFeature()
    path = tmp_path / "moderation.json"
    path.write_text(json.dumps({
        "settings": {
            "allowed_link_domains": ["twitch.tv"],
            "caps_min_length": 10, "caps_ratio_threshold": 0.7,
            "symbol_min_length": 8, "symbol_ratio_threshold": 0.5,
            "emote_spam_min_tokens": 6, "emote_spam_min_repeats": 6,
            "violation_window_minutes": 10,
            "timeout_threshold": 3,
            "timeout_duration_seconds": 60,
        },
        "banned_words": {"use_builtin_list": False, "extra": ["idiot"], "remove": []},
    }), encoding="utf-8")
    f.config = runtime_config.LiveConfig(path, defaults=DEFAULTS)
    return f


# --- _mask -----------------------------------------------------------------------------

def test_mask_keeps_first_letter():
    assert _mask("Idiot") == "I****"


def test_mask_single_character_has_nothing_left_to_keep():
    assert _mask("x") == "*"


def test_mask_empty_stays_empty():
    assert _mask("") == ""
    assert _mask(None) == ""


# --- review(): privileged users are exempt ------------------------------------------------

async def test_review_exempts_privileged_users(mod):
    msg = make_message("idiot", is_privileged=True)
    assert await mod.review(msg) is None


async def test_review_returns_none_for_a_clean_message(mod):
    msg = make_message("hello everyone")
    assert await mod.review(msg) is None


# --- review(): a verdict for a hit ----------------------------------------------------

async def test_review_masks_the_detail_in_the_verdict(mod):
    msg = make_message("you absolute idiot")
    verdict = await mod.review(msg)
    assert verdict is not None
    assert verdict.reason == "banned_word"
    assert verdict.detail == "i****"  # masked, not the raw word
    assert verdict.delete is True


async def test_review_subscriber_is_relaxed_but_not_exempt(mod):
    # is_subscriber=True skips the pure spam heuristics (see filters.moderate_message) but
    # a banned word still applies.
    msg = make_message("idiot", is_subscriber=True)
    verdict = await mod.review(msg)
    assert verdict is not None
    assert verdict.reason == "banned_word"


# --- escalation: no timeout below the threshold, one at/above it --------------------------

async def test_escalation_no_timeout_before_threshold(mod):
    user = make_message("idiot", user_id="42")
    v1 = await mod.review(user)
    v2 = await mod.review(user)
    assert v1.violation_count == 1
    assert v1.timeout_seconds == 0
    assert v2.violation_count == 2
    assert v2.timeout_seconds == 0


async def test_escalation_timeout_once_threshold_is_reached(mod):
    # timeout_threshold=3 in the fixture's settings.
    user = make_message("idiot", user_id="42")
    await mod.review(user)
    await mod.review(user)
    v3 = await mod.review(user)
    assert v3.violation_count == 3
    assert v3.timeout_seconds == 60


async def test_escalation_is_per_user_not_global(mod):
    a = make_message("idiot", user_id="1")
    b = make_message("idiot", user_id="2")
    await mod.review(a)
    await mod.review(a)
    v_b = await mod.review(b)
    # b's own first offence, not a's third.
    assert v_b.violation_count == 1
    assert v_b.timeout_seconds == 0


async def test_escalation_only_counts_offences_within_the_window(mod):
    user_key = "twitch:42"
    # Two offences already outside the 10-minute window, from _record_violation's own
    # trimming logic - only the fresh one (added by review() below) should count.
    old = datetime.now() - timedelta(minutes=20)
    mod._violations[user_key] = [old, old]
    verdict = await mod.review(make_message("idiot", user_id="42"))
    assert verdict.violation_count == 1


# --- sweep: bounds _violations without disturbing recent/real entries (M-2) ---------------

def test_sweep_drops_stale_empty_histories(mod):
    stale = datetime.now() - timedelta(hours=STALE_VIOLATION_HOURS + 1)
    mod._violations["twitch:raider1"] = [stale]
    mod._violations["twitch:raider2"] = [stale]
    mod._violations["twitch:real"] = [datetime.now()]

    mod._sweep()

    assert list(mod._violations.keys()) == ["twitch:real"]


def test_sweep_trims_but_keeps_a_history_with_at_least_one_recent_entry(mod):
    stale = datetime.now() - timedelta(hours=STALE_VIOLATION_HOURS + 1)
    recent = datetime.now()
    mod._violations["twitch:mixed"] = [stale, recent]

    mod._sweep()

    assert mod._violations["twitch:mixed"] == [recent]


def test_sweep_never_purges_something_a_real_review_call_would_still_count():
    # A violation_window_minutes far larger than the default, but still nowhere near
    # STALE_VIOLATION_HOURS - the sweep's own cutoff must stay well clear of any sane
    # configured window, or it could delete history a concurrent, real call still needs.
    f = ModerationFeature()
    long_window_minutes = 180  # 3h - unusually large, but far short of STALE_VIOLATION_HOURS
    within_the_long_window = datetime.now() - timedelta(hours=2)
    f._violations["twitch:x"] = [within_the_long_window]

    f._sweep()

    assert "twitch:x" in f._violations
    # And a real call in that (unusually long) window still counts it alongside the new one.
    count = f._record_violation("twitch:x", long_window_minutes)
    assert count == 2
