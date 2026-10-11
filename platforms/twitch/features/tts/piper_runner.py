"""Runs one Piper synthesis - started by synth.py as its own process, never imported by the bot.

Exists instead of plain `python -m piper` for one thing the CLI cannot do: English words in a
German sentence. Chat is full of them ("nice", "Game", "Stream"), and a German voice reads
them the German way ("niike", "Gahme"). Here every word or phrase from tts.json's
"english_words" is first turned into English phonemes (espeak-ng, en-us) and handed to the
voice as a raw [[ phoneme ]] block - so it comes out as English with the voice's own accent.
Every German Piper model knows all of these phonemes (checked 2026-10-11).

Viewer text never gets to produce such a block itself: synth.py removes "[" and "]" before
the text reaches this script, and the blocks are only inserted here, afterwards.

A separate process like the CLI was, for the same reasons (see synth.py) - Piper is GPL and
only ever runs out here. Reads one JSON job from stdin:

    {"text": ..., "model": "/path/x.onnx", "speaker": 3 | null, "out": "/path/out.wav",
     "settings": {"length_scale": ..., ...}, "english_words": ["game", ...]}
"""

import json
import re
import sys
import wave

from piper import PiperVoice, SynthesisConfig
from piper.phonemize_espeak import EspeakPhonemizer


def english_to_phonemes(text, words):
    words = sorted({str(w).strip().lower() for w in words if str(w).strip()}, key=len, reverse=True)
    if not words:
        return text
    phonemizer = EspeakPhonemizer()
    cache = {}

    def phonemes(match):
        key = match.group(1).lower()
        if key not in cache:
            sentences = phonemizer.phonemize("en-us", key)
            cache[key] = " ".join("".join(s).strip() for s in sentences)
        # Punctuation right after the word goes into the block: text following a block is
        # phonemized on its own, and espeak drops punctuation at the start of a fragment -
        # "Game, good game! Freu" would otherwise run together without any pause.
        return f"[[ {cache[key]}{match.group(2)} ]]"

    pattern = re.compile(
        r"(?<!\w)(" + "|".join(map(re.escape, words)) + r")(?!\w)([,.!?;:]*)", re.IGNORECASE,
    )
    return pattern.sub(phonemes, text)


def main():
    job = json.load(sys.stdin)
    settings = job.get("settings") or {}
    voice = PiperVoice.load(job["model"])
    config = SynthesisConfig(
        speaker_id=job.get("speaker"),
        length_scale=settings.get("length_scale"),
        noise_scale=settings.get("noise_scale"),
        noise_w_scale=settings.get("noise_w_scale"),
    )
    text = english_to_phonemes(job["text"], job.get("english_words") or [])
    silence = None
    with wave.open(job["out"], "wb") as wav:
        for i, chunk in enumerate(voice.synthesize(text, config)):
            if i == 0:
                wav.setframerate(chunk.sample_rate)
                wav.setsampwidth(chunk.sample_width)
                wav.setnchannels(chunk.sample_channels)
                # Same pause between sentences as the CLI's --sentence-silence (default 0.2 s).
                seconds = settings.get("sentence_silence")
                silence = bytes(int(chunk.sample_rate * (0.2 if seconds is None else seconds)) * 2)
            elif silence:
                wav.writeframes(silence)
            wav.writeframes(chunk.audio_int16_bytes)


if __name__ == "__main__":
    main()
