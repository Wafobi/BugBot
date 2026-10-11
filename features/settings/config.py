"""The settings website's secret, from the environment/.env - see features/overlay/config.py
for why secrets live here and not in a JSON file.

Without SETTINGS_TOKEN there is no settings website: the page can change every setting of the
bot, so it never opens without a secret of its own - neither OVERLAY_TOKEN (sits in the scene
collection) nor TTS_TOKEN (sits in the Stream Deck buttons) opens it.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).parent.parent.parent / ".env")

SETTINGS_TOKEN = os.environ.get("SETTINGS_TOKEN", "")
