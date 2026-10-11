"""normalize: chat text and names made speakable."""

from platforms.twitch.features.tts import normalize, synth

REPLACEMENTS = {"_comment": "ignored", "gg": "gut gespielt", "gg wp": "gut gespielt", "gut": "SCHLECHT", "xd": "haha"}


def test_emojis_links_and_stretching_go():
    assert normalize.message("GG 😂🔥 aaaaaah!!!!! https://twitch.tv/x", REPLACEMENTS) == "gut gespielt aah! ein Link"


def test_replacements_are_single_pass_and_longest_first():
    # "gut" in a replacement's own output must not be replaced again.
    assert normalize.message("gg wp und gg", REPLACEMENTS) == "gut gespielt und gut gespielt"


def test_replacements_are_whole_words_only():
    assert normalize.message("xDD und xd", REPLACEMENTS) == "xDD und haha"


def test_comment_key_is_not_a_replacement():
    assert normalize.message("_comment", REPLACEMENTS) == "_comment"


def test_mentions_become_spoken_names():
    assert normalize.message("hi @Zuschauer_mit_Namen123", {}) == "hi Zuschauer mit Namen"


def test_names():
    assert normalize.name("Zuschauer_mit_langem_Namen123") == "Zuschauer mit langem Namen"
    assert normalize.name("WafobiTV") == "Wafobi TV"
    assert normalize.name("xX_Gamer_Xx") == "Gamer"
    assert normalize.name("12345") == "12345"  # nothing left -> original


def test_speech_settings_clamps_and_skips_junk():
    clean = synth.speech_settings({"length_scale": 9, "noise_scale": "x", "noise_w_scale": True, "_comment": "y",
                                   "sentence_silence": 0.5})
    assert clean == {"length_scale": 3.0, "sentence_silence": 0.5}


def test_dropped_word_leaves_no_space_before_punctuation():
    assert normalize.message("Game xD, cool", {"xd": ""}) == "Game, cool"
