"""Discord-Konfiguration aus der Umgebung/.env.

Gegenstück zu platforms/twitch/config.py: core bleibt plattformneutral, jede Plattform
bringt ihre eigenen Zugangsdaten mit.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

# Idempotent und unabhängig von der Import-Reihenfolge - platforms/twitch/config.py lädt
# dieselbe Datei, load_dotenv überschreibt bereits gesetzte Variablen aber nicht.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

TOKEN = os.environ["DISCORD_TOKEN"]
