"""Twitch-Konfiguration aus der Umgebung/.env.

Liegt hier statt in core/config.py: core ist die plattformneutrale Basis (Moderation,
Stats, runtime_config), die Discord und Twitch gemeinsam nutzen - sie soll nichts über
Twitch wissen. Wer Twitch-Werte braucht, holt sie aus diesem Modul.

Wichtig: TWITCH_CHAT_ACCESS_TOKEN und TWITCH_CHAT_REFRESH_TOKEN werden zur Laufzeit von
api.refresh_chat_token neu gesetzt. Deshalb immer als `config.TWITCH_CHAT_ACCESS_TOKEN`
lesen und nie beim Import in eine eigene Variable kopieren - sonst hält man nach dem
ersten Refresh einen toten Token in der Hand.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

from core import runtime_config

# Idempotent und unabhängig von der Import-Reihenfolge - core/config.py lädt dieselbe
# Datei, load_dotenv überschreibt bereits gesetzte Variablen aber nicht.
load_dotenv(Path(__file__).parent.parent.parent / ".env")

TWITCH_CHANNEL = os.environ["TWITCH_CHANNEL"]

# Die eigene App aus https://dev.twitch.tv/console/apps - liefert App-Access-Tokens
# (api.get_app_access_token) und ist die App, die den Chat-Token ausstellen sollte.
TWITCH_CLIENT_ID = os.environ["TWITCH_CLIENT_ID"]
TWITCH_CLIENT_SECRET = os.environ["TWITCH_CLIENT_SECRET"]

# Der User-Token, mit dem der Bot in den Chat schreibt und moderiert.
TWITCH_CHAT_ACCESS_TOKEN = os.environ["TWITCH_CHAT_ACCESS_TOKEN"]
TWITCH_CHAT_REFRESH_TOKEN = os.environ["TWITCH_CHAT_REFRESH_TOKEN"]
TWITCH_CHAT_CLIENT_ID = os.environ["TWITCH_CHAT_CLIENT_ID"]

# Der Chat-Token muss nicht von der eigenen App (TWITCH_CLIENT_ID) stammen - wer ihn über
# einen Token-Generator erzeugt, bekommt einen Token der App dieses Generators. Ein Refresh
# verlangt aber das Secret genau der App, die den Token ausgestellt hat. Stimmen die
# Client-IDs überein, ist das TWITCH_CLIENT_SECRET; sonst muss es explizit gesetzt werden.
# Leer = wir können diesen Token nicht erneuern (siehe api.refresh_chat_token).
TWITCH_CHAT_CLIENT_SECRET = os.environ.get("TWITCH_CHAT_CLIENT_SECRET") or (
    TWITCH_CLIENT_SECRET if TWITCH_CHAT_CLIENT_ID == TWITCH_CLIENT_ID else ""
)


# --- Einstellbares ------------------------------------------------------------------
# Zugangsdaten kommen aus der .env (oben), alles Einstellbare aus twitch.json: Texte,
# Zeiten, Farben, Befehlsnamen, Regeln, statische Befehle, Moderationswerte. Die Datei
# wird bei Änderung neu gelesen (siehe core/runtime_config.py) - kein Neustart nötig.
#
# Sie liegt hier und nicht in bot.py, damit auch commands.py an sie herankommt: bot.py
# importiert commands.py, andersherum wäre es ein Zirkelimport. config.py importiert
# nichts aus dem eigenen Paket und ist deshalb der Ort, an den alle dürfen.
TWITCH_CONFIG = runtime_config.LiveConfig(Path(__file__).parent / "twitch.json")


def text(key, **values):
    """Kurzform für TWITCH_CONFIG.text - steht in bot.py und commands.py zusammen an die
    hundert Mal."""
    return TWITCH_CONFIG.text(key, **values)
