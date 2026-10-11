"""The TTS feature's secret and paths, from the environment/.env.

Separate from tts.json for the same reason as features/overlay/config.py: the JSON file is
what you look into when adapting things, and secrets have no business being there.

There is no port here: the browser source and the Stream Deck buttons ride on the overlay
listener (OVERLAY_PORT). TTS_TOKEN guards only the two buttons (/tts/next, /tts/skip) on it.
Without it they do not exist - the feature still loads, still queues cheers and still answers
!tts, there is just nothing that could release the queue.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent and independent of import order - the platforms load the same file, but
# load_dotenv does not overwrite variables that are already set.
load_dotenv(Path(__file__).parent.parent.parent.parent.parent / ".env")

# Secret for the Stream Deck buttons. Empty = no buttons.
TTS_TOKEN = os.environ.get("TTS_TOKEN", "")

# Where the Piper voice models (<name>.onnx + <name>.onnx.json) live. The Dockerfile
# downloads every model from voices.txt into exactly this default, so in the container
# nothing needs setting; outside of it, point this wherever `python -m piper.download_voices`
# put them.
TTS_VOICES_DIR = Path(os.environ.get("TTS_VOICES_DIR") or Path(__file__).parent / "voices")
