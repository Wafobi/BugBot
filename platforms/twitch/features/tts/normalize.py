"""Chat text -> text that a TTS voice reads well.

Piper hands text to espeak-ng, which already turns it into phonemes - so this does not try to
be a phonetic layer of its own. It fixes what espeak gets wrong on *chat* text specifically
(all checked against espeak's German output, 2026-10-11):

  - Emojis are read by their Unicode name: "😂" -> "Gesicht mit Freudentränen". Dropped.
  - Links are spelled out letter by letter. Replaced by "ein Link".
  - Digits at the end of a name are read as a number: "Viewer123" -> "Viewer
    einhundertdreiundzwanzig". Dropped; "_" and CamelCase become word gaps.
  - Stretched words and punctuation ("aaaaaah", "!!!!!") come out as noise. Shortened.
  - English chat words are read the German way: "nice" -> "niike", "Game" -> "Gahme". The
    replacements list (tts.json) respells them so the German reading lands right - a starting
    set, meant to be extended.

Moderation always sees the original text, not this - this is only about how it sounds.
"""

import re
import unicodedata

_URL = re.compile(r"(https?://|www\.)\S+", re.IGNORECASE)
_MENTION = re.compile(r"@(\w+)")
# Three or more of the same letter -> two ("aaaaah" -> "aah"), of the same punctuation -> one.
_STRETCHED_LETTERS = re.compile(r"([^\W\d_])\1{2,}")
_STRETCHED_PUNCT = re.compile(r"([!?.,])\1+")
_CAMEL = re.compile(r"(?<=[a-zäöüß])(?=[A-ZÄÖÜ])")
_DECORATION = re.compile(r"^[xX]{1,3}$")


def _drop_symbols(text):
    """Emojis and other pictographs (Unicode category "So"), plus the joiners/variation
    selectors that glue them together."""
    return "".join(
        " " if unicodedata.category(c) == "So" else c
        for c in text
        if c not in "‍️︎"
    )


def name(user_name):
    """A Twitch name, made speakable: "Zuschauer_mit_Namen123" -> "Zuschauer mit Namen",
    "WafobiTV" -> "Wafobi TV", "xX_Gamer_Xx" -> "Gamer". Falls back to the original when nothing is left."""
    original = str(user_name or "").strip()
    spoken = original.lstrip("@")
    spoken = re.sub(r"\d+$", "", spoken)
    spoken = spoken.replace("_", " ")
    spoken = _CAMEL.sub(" ", spoken)
    words = spoken.split()
    # Gamer-tag decoration at either end: "xX_Gamer_Xx" -> "Gamer".
    while words and _DECORATION.match(words[0]):
        words.pop(0)
    while words and _DECORATION.match(words[-1]):
        words.pop()
    spoken = " ".join(words)
    return spoken or original


def _replace_words(text, replacements):
    """Whole-word, case-insensitive replacements in a single pass - so a replacement's own
    output is never replaced again, and the longest match wins ("gg wp" before "gg")."""
    table = {str(k).lower(): v for k, v in replacements.items()
             if k and not str(k).startswith("_") and isinstance(v, str)}
    if not table:
        return text
    words = sorted(table, key=len, reverse=True)
    pattern = re.compile(r"(?<!\w)(" + "|".join(map(re.escape, words)) + r")(?!\w)", re.IGNORECASE)
    return pattern.sub(lambda m: table[m.group(1).lower()], text)


def message(text, replacements=None):
    """A chat message, made speakable - see the module doc for what happens and why."""
    text = str(text or "")
    text = _URL.sub(" ein Link ", text)
    text = _drop_symbols(text)
    text = _MENTION.sub(lambda m: name(m.group(1)), text)
    text = _STRETCHED_LETTERS.sub(r"\1\1", text)
    text = _STRETCHED_PUNCT.sub(r"\1", text)
    text = _replace_words(text, replacements or {})
    # A dropped word ("xD" -> "") leaves its neighbours' spacing behind: "Game xD," -> "Game ,".
    text = re.sub(r"\s+([,.!?;:])", r"\1", text)
    return " ".join(text.split())
