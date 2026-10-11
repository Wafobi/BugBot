"""Text -> WAV via Piper (github.com/OHF-Voice/piper1-gpl), and the "voice" setting it reads.

Piper runs as a separate process (piper_runner.py), not as a library imported into the bot,
for two reasons: it is GPL-licensed, and a process per utterance is all it needs - there is
no long-running service to keep alive, so no second container either (see tts-plan.md).

The text goes in as JSON through stdin, never as a command-line argument, and the process is
started without a shell: whatever a viewer typed is data for Piper, at no point something a
shell or argument parser gets to interpret.

Everything here is blocking - the feature calls synthesize() via asyncio.to_thread.

A voice is written "<model>" or "<model>#<speaker>", optionally with "@<effect>": the model
is a Piper voice name (de_DE-thorsten-medium), the speaker a number for the multi-speaker
models (de_DE-mls-medium#42, de_DE-thorsten_emotional-medium#3) - without "#" speaker 0
talks - and the effect one of EFFECTS, applied to the finished audio
(de_DE-thorsten-medium@robot).
"""

import json
import re
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Used when the configured voice is unusable - and the default in tts.json.
FALLBACK_VOICE = "de_DE-thorsten-medium"

# A model name ends up in a file path - letters, digits, "_" and "-" only, so a voice
# setting can never point outside the voices directory.
_MODEL_NAME = re.compile(r"^[A-Za-z0-9_-]+$")

# Long enough for 255 characters of text on a slow server CPU, short enough that a hung
# process does not block the next button press forever.
TIMEOUT_SECONDS = 60


# Sound effects on the finished audio (piper_runner.py does the work) - Piper's own voices all
# sound like people; these make one sound like a machine.
EFFECTS = {
    "robot": "Roboter - Ringmodulation, flache Betonung",
    "retro": "8-Bit-Computer - niedrige Abtastrate, grobe Auflösung",
    "funk": "Funkgerät/Terminal-Lautsprecher - dünn, leicht übersteuert",
}


class VoiceError(ValueError):
    """The voice setting cannot be used - message says why."""


@dataclass(frozen=True)
class Voice:
    model: str
    speaker: int | None
    model_path: Path
    effect: str | None = None

    @property
    def name(self):
        name = self.model if self.speaker is None else f"{self.model}#{self.speaker}"
        return name if self.effect is None else f"{name}@{self.effect}"


def split_effect(value):
    """"...@robot" -> ("...", "robot"); without "@" -> (value, None). Raises VoiceError on an
    unknown effect."""
    base, sep, effect = str(value or "").partition("@")
    effect = effect.strip().lower()
    if not sep:
        return base, None
    if effect not in EFFECTS:
        raise VoiceError(f"unknown effect {effect!r} - known: {', '.join(EFFECTS)}")
    return base, effect


def parse(value):
    """"model" or "model#speaker" (an "@effect" is ignored here, see split_effect) ->
    (model, speaker or None). Raises VoiceError."""
    value, _ = split_effect(value)
    model, sep, speaker = str(value or "").strip().partition("#")
    model = model.strip()
    if not _MODEL_NAME.match(model):
        raise VoiceError(f"invalid model name {model!r}")
    if not sep:
        return model, None
    speaker = speaker.strip()
    if not speaker.isdigit():
        raise VoiceError(f"speaker must be a number, got {speaker!r}")
    return model, int(speaker)


def num_speakers(model_path):
    """How many speakers a model has, from the .onnx.json Piper ships next to it."""
    config = json.loads(Path(f"{model_path}.json").read_text(encoding="utf-8"))
    return int(config.get("num_speakers") or 1)


def resolve(value, voices_dir):
    """The configured voice, checked against what is actually installed. Raises VoiceError
    when the model is missing or the speaker number is out of range."""
    _, effect = split_effect(value)
    model, speaker = parse(value)
    model_path = Path(voices_dir) / f"{model}.onnx"
    if not model_path.is_file() or not Path(f"{model_path}.json").is_file():
        raise VoiceError(f"model {model!r} not installed in {voices_dir}")
    if speaker is not None:
        count = num_speakers(model_path)
        if speaker >= count:
            raise VoiceError(f"{model} has speakers 0-{count - 1}, not {speaker}")
    return Voice(model, speaker, model_path, effect)


def model_info(model_path):
    """What the admin page needs to know about an installed model: its speakers (number ->
    label; the label is the name from speaker_id_map where that is a word - thorsten_emotional:
    "amused", "drunk", ... - otherwise just the number) and its own speech defaults."""
    config = json.loads(Path(f"{model_path}.json").read_text(encoding="utf-8"))
    count = int(config.get("num_speakers") or 1)
    names = {v: k for k, v in (config.get("speaker_id_map") or {}).items()}
    inference = config.get("inference") or {}
    return {
        "speakers": {n: (names[n] if n in names and not names[n].isdigit() else str(n)) for n in range(count)},
        "defaults": {
            "length_scale": inference.get("length_scale", 1.0),
            "noise_scale": inference.get("noise_scale", 0.667),
            "noise_w_scale": inference.get("noise_w", 0.8),
            "sentence_silence": 0.2,
        },
    }


def installed(voices_dir):
    """Names of all installed models, sorted."""
    directory = Path(voices_dir)
    if not directory.is_dir():
        return []
    return sorted(p.name[:-len(".onnx")] for p in directory.glob("*.onnx") if Path(f"{p}.json").is_file())


# How it is spoken - tts.json "speech". Each is optional; missing/null keeps the model's own
# default (from its .onnx.json, e.g. thorsten-medium: length 1.0, noise 0.667, noise_w 0.8).
# Bounds keep a typo from producing minutes of audio or pure noise.
SPEECH_SETTINGS = {
    # setting:          (min,  max)
    "length_scale":     (0.3,  3.0),   # >1 slower, <1 faster
    "noise_scale":      (0.0,  2.0),   # variation in intonation
    "noise_w_scale":    (0.0,  2.0),   # variation in sound lengths
    "sentence_silence": (0.0,  3.0),   # seconds of pause after a sentence
}


def speech_settings(settings):
    """tts.json "speech" -> only the known keys with numeric values, clamped to
    SPEECH_SETTINGS' bounds. Everything else (null, "_comment", typos) is dropped, which
    leaves the model's own default in place."""
    clean = {}
    for key, (low, high) in SPEECH_SETTINGS.items():
        value = (settings or {}).get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            clean[key] = min(high, max(low, float(value)))
    return clean


RUNNER = Path(__file__).with_name("piper_runner.py")


def synthesize(text, voice, settings=None, english_words=None):
    """Speaks `text` with `voice` and returns the WAV bytes. `settings` is tts.json "speech"
    (see SPEECH_SETTINGS), `english_words` the words/phrases to pronounce in English (see
    piper_runner.py). Raises RuntimeError when Piper fails or times out."""
    # Piper reads "[[ ... ]]" as raw phonemes rather than text - viewer text must not be able
    # to reach that mode, so the brackets go before anything else. piper_runner.py inserts
    # its own blocks for english_words only after this.
    text = " ".join(str(text).replace("[", " ").replace("]", " ").split())
    words = [w for w in (english_words or [])
             if isinstance(w, str) and not w.startswith("_") and "[" not in w and "]" not in w]
    with tempfile.TemporaryDirectory(prefix="bugbot-tts-") as tmp:
        out = Path(tmp) / "speech.wav"
        job = {
            "text": text, "model": str(voice.model_path), "speaker": voice.speaker,
            "out": str(out), "settings": speech_settings(settings), "english_words": words,
            "effect": voice.effect,
        }
        try:
            result = subprocess.run(
                [sys.executable, str(RUNNER)], input=json.dumps(job).encode("utf-8"),
                capture_output=True, timeout=TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired as error:
            raise RuntimeError(f"piper timed out after {TIMEOUT_SECONDS}s") from error
        if result.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            stderr = result.stderr.decode("utf-8", "replace").strip().splitlines()
            raise RuntimeError(f"piper failed (exit {result.returncode}): {stderr[-1] if stderr else 'no output'}")
        return out.read_bytes()
