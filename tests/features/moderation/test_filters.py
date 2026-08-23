"""features/moderation/filters.py: the pure, stateless checks. build_settings()/
moderate_message() glue them together the same way feature.py does."""

from features.moderation import filters


def settings(**overrides):
    return filters.build_settings(overrides=overrides)


# --- banned words --------------------------------------------------------------------------

def test_banned_word_matches_on_word_boundary():
    s = settings(extra_banned_words=["opfer"])
    assert filters.check_banned_words("du opfer", s) == "opfer"
    # Substring inside an unrelated word must not fire - see the comment on
    # _compile_banned_words_pattern.
    assert filters.check_banned_words("Verkehrsopfer der Statistik", s) is None


def test_banned_word_case_insensitive():
    s = settings(extra_banned_words=["idiot"])
    assert filters.check_banned_words("IDIOT!", s) == "IDIOT"


def test_no_banned_words_configured_matches_nothing():
    # An empty pattern must not become "matches everything" - see
    # _compile_banned_words_pattern's docstring.
    s = filters.build_settings(overrides={"extra_banned_words": []},
                                word_config={"use_builtin_list": False})
    assert filters.check_banned_words("literally anything at all", s) is None


def test_bread_is_not_a_banned_word():
    # N-2 regression: "bread" was an obvious test leftover sitting at the front of
    # BASE_BANNED_WORDS - a completely ordinary word in any gaming/cooking chat.
    assert "bread" not in filters.BASE_BANNED_WORDS
    s = filters.build_settings(word_config={"use_builtin_list": True})
    assert filters.check_banned_words("I baked some bread today", s) is None


def test_banned_word_remove_overrides_builtin_list():
    s = filters.build_settings(word_config={"use_builtin_list": True, "remove": ["cunt"]})
    assert filters.check_banned_words("cunt", s) is None


# --- link spam: host-based matching (H-1 regression) --------------------------------------
# These are exactly the bypasses verified against the real code during the security review -
# before the fix, all four "should block" cases here were silently waved through because the
# allowlist was matched against the whole message as a substring, not the actual link host.

def test_link_spam_blocks_a_disallowed_domain():
    s = settings()
    assert filters.check_link_spam("check out evil-site.xyz", s) is True


def test_link_spam_mentioning_an_allowed_domain_does_not_wave_through_a_second_link():
    s = settings()
    assert filters.check_link_spam("evil-site.xyz (not twitch.tv)", s) is True


def test_link_spam_path_variant_is_not_fooled_by_the_allowed_domain_as_a_path_segment():
    s = settings()
    assert filters.check_link_spam("malware.xyz/twitch.tv", s) is True


def test_link_spam_lookalike_subdomain_is_blocked():
    # The most dangerous variant: looks like a twitch.tv link to a viewer.
    s = settings()
    assert filters.check_link_spam("twitch.tv.evil-host.com/phish", s) is True


def test_link_spam_allows_a_real_link_to_an_allowed_domain():
    s = settings()
    assert filters.check_link_spam("go watch twitch.tv/wafobitv", s) is False


def test_link_spam_allows_a_subdomain_of_an_allowed_domain():
    s = settings()
    assert filters.check_link_spam("check the clip: clips.twitch.tv/abc123", s) is False


def test_link_spam_no_link_at_all_passes():
    s = settings()
    assert filters.check_link_spam("just a normal message", s) is False


def test_link_spam_respects_configured_allowed_domains():
    s = settings(allowed_link_domains=["example.com"])
    assert filters.check_link_spam("see example.com/page", s) is False
    assert filters.check_link_spam("see twitch.tv", s) is True  # no longer allowed here


# --- excessive caps -------------------------------------------------------------------------

def test_excessive_caps_flags_shouting():
    s = settings(caps_min_length=5, caps_ratio_threshold=0.7)
    assert filters.check_excessive_caps("THIS IS SHOUTING", s) is True


def test_excessive_caps_ignores_short_messages():
    s = settings(caps_min_length=20)
    assert filters.check_excessive_caps("HI", s) is False


def test_excessive_caps_allows_normal_text():
    s = settings(caps_min_length=5, caps_ratio_threshold=0.7)
    assert filters.check_excessive_caps("This is a normal sentence.", s) is False


# --- symbol spam --------------------------------------------------------------------------

def test_symbol_spam_flags_mostly_symbols():
    s = settings(symbol_min_length=5, symbol_ratio_threshold=0.5)
    assert filters.check_symbol_spam("!!!@#$%^&*()", s) is True


def test_symbol_spam_allows_normal_punctuation():
    s = settings(symbol_min_length=5, symbol_ratio_threshold=0.5)
    assert filters.check_symbol_spam("Hello, how are you?", s) is False


# --- emote/word spam ------------------------------------------------------------------------

def test_emote_spam_flags_repeated_tokens():
    s = settings(emote_spam_min_tokens=4, emote_spam_min_repeats=4)
    assert filters.check_emote_spam("PogU PogU PogU PogU", s) is True


def test_emote_spam_allows_varied_tokens():
    s = settings(emote_spam_min_tokens=4, emote_spam_min_repeats=4)
    assert filters.check_emote_spam("this is a normal sentence with words", s) is False


# --- moderate_message: ordering and relaxed mode --------------------------------------------

def test_moderate_message_returns_none_when_nothing_matches():
    s = settings()
    assert filters.moderate_message("hello world", s) is None


def test_moderate_message_banned_word_wins_over_link_spam():
    s = filters.build_settings(word_config={"extra": ["idiot"]})
    reason, detail = filters.moderate_message("idiot evil-site.xyz", s)
    assert reason == "banned_word"
    assert detail == "idiot"


def test_moderate_message_relaxed_skips_pure_spam_heuristics_but_not_words_or_links():
    s = settings(caps_min_length=1, caps_ratio_threshold=0.1)
    # Would trip excessive_caps if not relaxed.
    assert filters.moderate_message("ALL CAPS", s, relaxed=True) is None
    # But a banned word or link still applies even for a relaxed (subscriber) sender.
    s2 = filters.build_settings(word_config={"extra": ["idiot"]})
    assert filters.moderate_message("idiot", s2, relaxed=True) == ("banned_word", "idiot")
