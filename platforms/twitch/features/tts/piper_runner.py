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
     "settings": {"length_scale": ..., ...}, "english_words": ["game", ...],
     "effect": "robot" | "retro" | "funk" | null}

Effects (synth.EFFECTS) work on the finished audio with numpy, which Piper already depends
on - see EFFECTS below.
"""

import json
import re
import sys
import wave

import numpy as np
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


def _moving_average(x, width):
    width = max(1, int(width))
    return np.convolve(x, np.ones(width) / width, mode="same")


def robot(x, rate):
    """Ring modulation at a low frequency - the classic sci-fi robot - with some of the dry
    voice kept so it stays intelligible."""
    t = np.arange(len(x)) / rate
    return 0.8 * x * np.sin(2 * np.pi * 45 * t) + 0.35 * x


def retro(x, rate):
    """Speak & Spell / home computer: sample-and-hold down to ~6 kHz, 5-bit resolution."""
    step = max(1, round(rate / 6000))
    held = np.repeat(x[::step], step)[:len(x)]
    levels = 2 ** 4
    return np.round(held * levels) / levels


def funk(x, rate):
    """Radio / small speaker: roughly 300-3000 Hz, then soft clipping."""
    band = _moving_average(x, rate / 3000)
    band = band - _moving_average(band, rate / 300)
    return np.tanh(4 * band) / np.tanh(4)


EFFECTS = {"robot": robot, "retro": retro, "funk": funk}

# How an effect wants to be spoken: a robot does not do intonation.
EFFECT_SPEECH = {"robot": {"noise_scale": 0.15, "noise_w_scale": 0.2}}


def apply_effect(pcm, rate, effect):
    """int16 PCM bytes -> int16 PCM bytes with the effect applied, peak-normalized."""
    if effect not in EFFECTS:
        return pcm
    x = np.frombuffer(pcm, dtype=np.int16).astype(np.float32) / 32768.0
    y = EFFECTS[effect](x, rate)
    peak = float(np.max(np.abs(y))) if len(y) else 0.0
    if peak > 0:
        y = y * (0.9 / peak)
    return (np.clip(y, -1, 1) * 32767).astype(np.int16).tobytes()


def main():
    job = json.load(sys.stdin)
    effect = job.get("effect")
    settings = {**(job.get("settings") or {}), **EFFECT_SPEECH.get(effect, {})}
    voice = PiperVoice.load(job["model"])
    config = SynthesisConfig(
        speaker_id=job.get("speaker"),
        length_scale=settings.get("length_scale"),
        noise_scale=settings.get("noise_scale"),
        noise_w_scale=settings.get("noise_w_scale"),
    )
    text = english_to_phonemes(job["text"], job.get("english_words") or [])
    parts, rate, width, channels = [], 22050, 2, 1
    for i, chunk in enumerate(voice.synthesize(text, config)):
        if i == 0:
            rate, width, channels = chunk.sample_rate, chunk.sample_width, chunk.sample_channels
        else:
            # Same pause between sentences as the CLI's --sentence-silence (default 0.2 s).
            seconds = settings.get("sentence_silence")
            parts.append(bytes(int(rate * (0.2 if seconds is None else seconds)) * 2))
        parts.append(chunk.audio_int16_bytes)
    with wave.open(job["out"], "wb") as wav:
        wav.setframerate(rate)
        wav.setsampwidth(width)
        wav.setnchannels(channels)
        wav.writeframes(apply_effect(b"".join(parts), rate, effect))


if __name__ == "__main__":
    main()
