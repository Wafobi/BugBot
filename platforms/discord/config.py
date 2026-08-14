"""Discord configuration from the environment/.env.

Counterpart to platforms/twitch/config.py: core stays platform-neutral, and every platform
brings its own credentials.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent and independent of import order - platforms/twitch/config.py loads the same
# file, but load_dotenv does not overwrite variables that are already set.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

TOKEN = os.environ["DISCORD_TOKEN"]
